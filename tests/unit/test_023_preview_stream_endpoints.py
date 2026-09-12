"""MJPEG preview stream endpoint tests (Spec 023, Task 7.4 + 11.1).

TestClient + dependency overrides / fakes. No hardware, no real DB. Covers:
    - /api/camera/preview-stream: 200 multipart, first-frame reuse, unsubscribe
      once, 503 before headers (subscribe None / no first frame), stop wakeup.
    - /api/monitoring/{id}/preview-stream: 200 multipart with two frames, worker
      disappears -> stream ends (A.3), 503 no worker, never opens camera,
      ownership 404/401.
    - /api/camera/preview-diagnostics: auth + JSON shape.
"""

from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.routes import monitoring_api
from app import dependencies as deps
from src.application.services.live_preview_manager import STREAM_STOPPED


# --------------------------------------------------------------------------- #
# Fakes for the pre-monitoring preview manager
# --------------------------------------------------------------------------- #

class _FakePreviewManager:
    def __init__(self, *, subscribe_result=("tok", 7), wait_sequence=None,
                 diagnostics=None):
        self._subscribe_result = subscribe_result
        # wait_sequence: list of items returned by successive wait_for_preview.
        self._wait_sequence = list(wait_sequence or [])
        self._wait_i = 0
        self.unsubscribed = []
        self._diagnostics = diagnostics or {}

    def subscribe(self):
        return self._subscribe_result

    def wait_for_preview(self, generation, after_sequence, timeout):
        if self._wait_i < len(self._wait_sequence):
            item = self._wait_sequence[self._wait_i]
            self._wait_i += 1
            return item
        return STREAM_STOPPED

    def unsubscribe(self, token):
        self.unsubscribed.append(token)

    def diagnostics(self):
        return self._diagnostics


def _build_camera_app(manager, *, user=SimpleNamespace(id=1)):
    app = FastAPI()
    app.include_router(monitoring_api.router)
    app.dependency_overrides[deps.get_live_preview_manager] = lambda: manager
    app.dependency_overrides[deps.require_current_user_api] = lambda: user
    # A camera_service that explodes if used (preview must never open a camera).
    app.state.camera_service = SimpleNamespace(
        check_availability=lambda: SimpleNamespace(
            status=SimpleNamespace(value="available"), reason=None
        ),
    )
    return app


class TestCameraPreviewStream:
    def test_200_multipart_first_frame_reused_and_unsubscribe_once(self):
        manager = _FakePreviewManager(
            subscribe_result=("tok", 7),
            wait_sequence=[(1, b"jpeg1"), (2, b"jpeg2"), STREAM_STOPPED],
        )
        app = _build_camera_app(manager)
        client = TestClient(app)
        resp = client.get("/api/camera/preview-stream")
        assert resp.status_code == 200
        assert "multipart/x-mixed-replace" in resp.headers["content-type"]
        body = resp.content
        assert b"--frame" in body
        assert b"jpeg1" in body  # first frame from the preflight is reused
        assert b"jpeg2" in body
        # unsubscribe called exactly once with the token.
        assert manager.unsubscribed == ["tok"]

    def test_503_before_headers_when_subscribe_none(self):
        manager = _FakePreviewManager(subscribe_result=None)
        app = _build_camera_app(manager)
        client = TestClient(app)
        resp = client.get("/api/camera/preview-stream")
        assert resp.status_code == 503
        assert "application/json" in resp.headers["content-type"]
        assert manager.unsubscribed == []  # never subscribed

    def test_503_before_headers_when_no_first_frame(self):
        # subscribe ok, but the first wait returns None (timeout) -> 503.
        manager = _FakePreviewManager(
            subscribe_result=("tok", 7), wait_sequence=[None],
        )
        app = _build_camera_app(manager)
        client = TestClient(app)
        resp = client.get("/api/camera/preview-stream")
        assert resp.status_code == 503
        assert manager.unsubscribed == ["tok"]  # unsubscribed on failure

    def test_stream_ends_on_stream_stopped(self):
        # First frame ok, then STREAM_STOPPED -> generator ends promptly.
        manager = _FakePreviewManager(
            subscribe_result=("tok", 7),
            wait_sequence=[(1, b"only"), STREAM_STOPPED],
        )
        app = _build_camera_app(manager)
        client = TestClient(app)
        resp = client.get("/api/camera/preview-stream")
        assert resp.status_code == 200
        assert b"only" in resp.content
        assert manager.unsubscribed == ["tok"]


# --------------------------------------------------------------------------- #
# Monitoring preview stream
# --------------------------------------------------------------------------- #

class _FakeWorker:
    def __init__(self, snapshots):
        # snapshots: list of (sequence, frame) yielded on successive calls;
        # once exhausted, returns the last one (so sequence stops advancing).
        self._snaps = list(snapshots)
        self._i = 0

    def get_preview_frame_snapshot(self):
        if self._i < len(self._snaps):
            snap = self._snaps[self._i]
            self._i += 1
            return snap
        return self._snaps[-1] if self._snaps else None


class _FakeRegistry:
    def __init__(self, workers_sequence):
        # workers_sequence: list of workers returned by successive get_worker()
        # calls; once exhausted returns the last element.
        self._seq = list(workers_sequence)
        self._i = 0

    def get_worker(self, monitoring_id):
        if self._i < len(self._seq):
            w = self._seq[self._i]
            self._i += 1
            return w
        return self._seq[-1] if self._seq else None


def _frame(v):
    return np.full((2, 2, 3), v, dtype=np.uint8)


def _build_monitoring_app(registry, *, owner_ok=True, user=SimpleNamespace(id=1)):
    app = FastAPI()
    app.include_router(monitoring_api.router)
    app.state.monitoring_runtime_registry = registry
    # Camera service must never be used by the stream.
    app.state.camera_service = SimpleNamespace()

    if owner_ok:
        app.dependency_overrides[deps.require_monitoring_owner] = (
            lambda: SimpleNamespace(id=1)
        )
    return app


@pytest.fixture(autouse=True)
def _stub_encoder(monkeypatch):
    monkeypatch.setattr(
        "src.application.services.camera_service.CameraService.encode_frame_jpeg",
        staticmethod(lambda frame: b"J" + bytes([int(frame[0, 0, 0]) & 0xFF])),
    )


class TestMonitoringPreviewStream:
    def test_200_multipart_two_frames_then_worker_gone(self):
        worker = _FakeWorker([(1, _frame(10)), (2, _frame(20))])
        # Preflight get_worker -> worker; loop: worker, worker, then None (gone).
        registry = _FakeRegistry([worker, worker, worker, None])
        app = _build_monitoring_app(registry)
        client = TestClient(app)
        resp = client.get("/api/monitoring/5/preview-stream")
        assert resp.status_code == 200
        assert "multipart/x-mixed-replace" in resp.headers["content-type"]
        body = resp.content
        assert b"--frame" in body
        assert b"J" + bytes([10]) in body
        assert b"J" + bytes([20]) in body

    def test_worker_replaced_ends_stream(self):
        # A.3: during the loop the registry returns a DIFFERENT worker object ->
        # the stream must end (not serve stale frames from the old worker).
        worker_a = _FakeWorker([(1, _frame(1)), (2, _frame(2))])
        worker_b = _FakeWorker([(1, _frame(9))])
        registry = _FakeRegistry([worker_a, worker_b])  # preflight A, then B
        app = _build_monitoring_app(registry)
        client = TestClient(app)
        resp = client.get("/api/monitoring/5/preview-stream")
        assert resp.status_code == 200
        # First (preflight) frame from A is emitted; then loop sees B != A -> ends.
        assert b"J" + bytes([1]) in resp.content
        assert b"J" + bytes([9]) not in resp.content

    def test_503_when_no_worker(self):
        registry = _FakeRegistry([None])
        app = _build_monitoring_app(registry)
        client = TestClient(app)
        resp = client.get("/api/monitoring/5/preview-stream")
        assert resp.status_code == 503

    def test_never_opens_camera(self, monkeypatch):
        # Any attempt to open a camera must blow up the test.
        monkeypatch.setattr(
            "src.application.services.camera_service.CameraService.capture_preview_frame",
            lambda self: (_ for _ in ()).throw(AssertionError("no camera")),
            raising=False,
        )
        worker = _FakeWorker([(1, _frame(3))])
        registry = _FakeRegistry([worker, None])
        app = _build_monitoring_app(registry)
        client = TestClient(app)
        resp = client.get("/api/monitoring/5/preview-stream")
        assert resp.status_code == 200


class TestMonitoringPreviewStreamOwnership:
    def test_foreign_or_missing_404(self):
        registry = _FakeRegistry([_FakeWorker([(1, _frame(1))])])
        app = _build_monitoring_app(registry, owner_ok=False)

        def _deny():
            raise HTTPException(status_code=404, detail="No encontrado")

        app.dependency_overrides[deps.require_monitoring_owner] = _deny
        client = TestClient(app)
        resp = client.get("/api/monitoring/5/preview-stream")
        assert resp.status_code == 404

    def test_unauthenticated_401(self):
        registry = _FakeRegistry([_FakeWorker([(1, _frame(1))])])
        app = _build_monitoring_app(registry, owner_ok=False)

        def _unauth():
            raise HTTPException(status_code=401, detail="Not authenticated")

        # require_monitoring_owner depends on auth; simulate the 401 it raises.
        app.dependency_overrides[deps.require_monitoring_owner] = _unauth
        client = TestClient(app)
        resp = client.get("/api/monitoring/5/preview-stream")
        assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# preview-diagnostics
# --------------------------------------------------------------------------- #

class TestPreviewDiagnostics:
    def test_returns_json_shape(self):
        manager = _FakePreviewManager(diagnostics={
            "camera_frames_produced": 400,
            "preview_frames_encoded": 398,
            "active_subscribers": 1,
            "camera_capture_elapsed_seconds": 20.0,
            "effective_camera_stream_fps": 19.95,
            "capture_state": "running",
            "subscriptions_suspended": False,
            "generation": 3,
        })
        app = _build_camera_app(manager)
        client = TestClient(app)
        resp = client.get("/api/camera/preview-diagnostics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["camera_frames_produced"] == 400
        assert data["preview_frames_encoded"] == 398
        assert data["active_subscribers"] == 1
        assert data["effective_camera_stream_fps"] == 19.95
        assert data["state"]["capture_state"] == "running"
        assert data["state"]["subscriptions_suspended"] is False

    def test_requires_auth(self):
        manager = _FakePreviewManager(diagnostics={})
        app = _build_camera_app(manager)

        def _unauth():
            raise HTTPException(status_code=401, detail="Not authenticated")

        app.dependency_overrides[deps.require_current_user_api] = _unauth
        client = TestClient(app)
        resp = client.get("/api/camera/preview-diagnostics")
        assert resp.status_code == 401
