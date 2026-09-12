"""Ownership tests for GET /api/monitoring/{id}/preview (Spec 023, Task 6).

TestClient + dependency overrides / fakes. No real DB. Verifies:
    - owner -> 200 image/jpeg (worker frame, camera never opened);
    - foreign user -> 404;
    - missing monitoring -> 404 (identical response to foreign);
    - unauthenticated -> 401;
    - owner but no worker -> 503 (distinct from 404 ownership failure).
"""

from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.routes import monitoring_api
from app import dependencies as deps


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class _FakeMonitoringRepo:
    def __init__(self, monitorings):
        self._m = monitorings  # dict id -> SimpleNamespace(module_id=...)

    def get_by_id(self, mid):
        return self._m.get(mid)


class _FakeModuleRepo:
    def __init__(self, modules):
        self._mods = modules  # dict id -> SimpleNamespace(greenhouse_id=...)

    def get_by_id(self, mid):
        return self._mods.get(mid)


class _FakeGreenhouseRepo:
    """get_by_id_for_owner returns the greenhouse only if owner matches."""

    def __init__(self, greenhouses):
        self._gh = greenhouses  # dict id -> owner_user_id

    def get_by_id_for_owner(self, gid, owner_user_id):
        owner = self._gh.get(gid)
        if owner is None or owner != owner_user_id:
            return None
        return SimpleNamespace(id=gid, owner_user_id=owner)


class _FakeWorker:
    def __init__(self, frame):
        self._frame = frame

    def get_last_frame(self):
        return None if self._frame is None else self._frame.copy()


class _FakeRegistry:
    def __init__(self, worker):
        self._worker = worker

    def get_worker(self, monitoring_id):
        return self._worker


def _build_app(*, user, monitorings, modules, greenhouses, worker):
    app = FastAPI()
    app.include_router(monitoring_api.router)

    # camera_service is only referenced by other endpoints; provide a stub so
    # state access never fails, but it must NEVER be used by preview.
    app.state.camera_service = SimpleNamespace(
        capture_preview_frame=lambda: (_ for _ in ()).throw(
            AssertionError("camera must not be opened")
        ),
        capture_single_frame=lambda: (_ for _ in ()).throw(
            AssertionError("camera must not be opened")
        ),
    )
    app.state.monitoring_runtime_registry = _FakeRegistry(worker)

    # Override the request-scoped repository dependencies with fakes.
    app.dependency_overrides[deps.get_monitoring_repository] = (
        lambda: _FakeMonitoringRepo(monitorings)
    )
    app.dependency_overrides[deps.get_module_repository] = (
        lambda: _FakeModuleRepo(modules)
    )
    app.dependency_overrides[deps.get_greenhouse_repository] = (
        lambda: _FakeGreenhouseRepo(greenhouses)
    )
    app.dependency_overrides[deps.require_current_user_api] = lambda: user
    return app


@pytest.fixture(autouse=True)
def _stub_encoder(monkeypatch):
    monkeypatch.setattr(
        "src.application.services.camera_service.CameraService.encode_frame_jpeg",
        staticmethod(lambda frame: b"\xff\xd8\xff\xe0jpeg"),
    )


def _frame():
    return np.zeros((4, 4, 3), dtype=np.uint8)


# Shared hierarchy: monitoring 1 -> module 10 -> greenhouse 100 owned by user A(id=1).
USER_A = SimpleNamespace(id=1, is_active=True)
USER_B = SimpleNamespace(id=2, is_active=True)
MONITORINGS = {1: SimpleNamespace(id=1, module_id=10)}
MODULES = {10: SimpleNamespace(id=10, greenhouse_id=100)}
GREENHOUSES = {100: 1}  # greenhouse 100 owned by user 1 (A)


class TestPreviewOwnership:
    def test_owner_gets_200_jpeg(self):
        app = _build_app(
            user=USER_A, monitorings=MONITORINGS, modules=MODULES,
            greenhouses=GREENHOUSES, worker=_FakeWorker(_frame()),
        )
        client = TestClient(app)
        resp = client.get("/api/monitoring/1/preview")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"

    def test_foreign_user_gets_404(self):
        app = _build_app(
            user=USER_B, monitorings=MONITORINGS, modules=MODULES,
            greenhouses=GREENHOUSES, worker=_FakeWorker(_frame()),
        )
        client = TestClient(app)
        resp = client.get("/api/monitoring/1/preview")
        assert resp.status_code == 404

    def test_missing_monitoring_gets_404_identical_to_foreign(self):
        # Missing monitoring for owner A.
        app_missing = _build_app(
            user=USER_A, monitorings={}, modules=MODULES,
            greenhouses=GREENHOUSES, worker=_FakeWorker(_frame()),
        )
        missing = TestClient(app_missing).get("/api/monitoring/999/preview")

        # Foreign monitoring for user B.
        app_foreign = _build_app(
            user=USER_B, monitorings=MONITORINGS, modules=MODULES,
            greenhouses=GREENHOUSES, worker=_FakeWorker(_frame()),
        )
        foreign = TestClient(app_foreign).get("/api/monitoring/1/preview")

        assert missing.status_code == 404
        assert foreign.status_code == 404
        # Indistinguishable response bodies.
        assert missing.json() == foreign.json()

    def test_unauthenticated_gets_401(self):
        app = _build_app(
            user=USER_A, monitorings=MONITORINGS, modules=MODULES,
            greenhouses=GREENHOUSES, worker=_FakeWorker(_frame()),
        )

        def _unauth():
            raise HTTPException(status_code=401, detail="Not authenticated")

        app.dependency_overrides[deps.require_current_user_api] = _unauth
        client = TestClient(app)
        resp = client.get("/api/monitoring/1/preview")
        assert resp.status_code == 401

    def test_owner_but_no_worker_gets_503(self):
        # Ownership valid, but no active worker -> 503 (distinct from 404).
        app = _build_app(
            user=USER_A, monitorings=MONITORINGS, modules=MODULES,
            greenhouses=GREENHOUSES, worker=None,
        )
        client = TestClient(app)
        resp = client.get("/api/monitoring/1/preview")
        assert resp.status_code == 503
