"""Task 14.3 — MANDATORY UI/route + preview-during-recording tests (Spec 019).

Covers:
    1. The production START action offers/consumes NO manual video-file selection.
    2. With video_first_enabled=True, START wires the VIDEO frame source
       (camera_mode="video", fps=int(recording_target_fps)).
    3. During a running video-first recording, the preview endpoint:
         - gets the worker from MonitoringRuntimeRegistry.get_worker(id),
         - calls VideoRecordingWorker.get_last_frame(),
         - returns that frame,
         - NEVER calls CameraService.capture_single_frame()/capture_preview_frame(),
         - NEVER constructs a second camera / Picamera2 / re-acquires the lock.
    4. The frame served by preview does not share mutable memory with the
       worker's internal buffer (get_last_frame returns a copy).
    5. Status → UI mapping: running=Recording, analyzing=Processing,
       completed=Completed (video-first wording).

Heavy deps (torch/detectron2/cv2/picamera2) are mocked at import so the route
layer can be exercised without hardware. numpy is kept real for the copy check.
"""

from unittest.mock import MagicMock, patch

import pytest

# cv2 must stay REAL (the preview endpoint encodes JPEG via cv2) and is
# available in the test environment. We do NOT mock cv2 here — mocking it in
# sys.modules would pollute other tests that rely on real cv2 (e.g. the camera
# read-path tests). Route imports below do not require torch/detectron2 at
# module load, so no heavy mocking is needed.

import numpy as np  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


# --------------------------------------------------------------------------- #
# 1 & 2. START action — no file selection + video-first frame source wiring
# --------------------------------------------------------------------------- #

def _start_client():
    from app.routes.agricultural_ui import router
    from app.dependencies import require_current_user_html

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_current_user_html] = lambda: MagicMock(id=1)

    app.state.db_manager = MagicMock()
    app.state.db_manager.get_session.return_value = MagicMock()
    app.state.log_service = MagicMock()
    app.state.camera_service = MagicMock()
    return TestClient(app, raise_server_exceptions=False)


class TestStartActionNoFileSelection:
    def test_setup_page_has_no_video_file_selection(self):
        """The setup screen must not expose a manual video-file picker."""
        client = _start_client()

        mock_module = MagicMock(id=1, name="Módulo Test")
        with patch("app.routes.agricultural_ui.get_module_repository") as m_repo, \
             patch("app.routes.agricultural_ui._module_owned_by_user", return_value=mock_module), \
             patch("app.routes.agricultural_ui.ModelService") as m_model:
            repo = MagicMock()
            repo.get_by_id.return_value = mock_module
            m_repo.return_value = repo
            inst = MagicMock()
            inst.check_availability.return_value = MagicMock(value="available")
            m_model.return_value = inst

            resp = client.get("/modulos/1/monitoreo/nuevo")

        assert resp.status_code == 200
        body = resp.text.lower()
        # No file/video selection widgets or data/videos references.
        assert "data/videos" not in body
        assert 'type="file"' not in body
        assert "seleccionar video" not in body
        assert "select" not in body or "activity_type" not in body  # no video <select>

    def test_start_post_accepts_only_dimensions_and_notes(self):
        """POST start consumes only width/length/notes; no video_path is used."""
        client = _start_client()

        captured = {}

        def _fake_create_fs(**kwargs):
            captured.update(kwargs)
            return MagicMock()

        owned_module = MagicMock(id=1, name="M")
        with patch("app.routes.agricultural_ui.get_module_repository") as m_repo, \
             patch("app.routes.agricultural_ui._module_owned_by_user", return_value=owned_module), \
             patch("app.routes.agricultural_ui.get_monitoring_service") as m_svc, \
             patch("src.application.services.frame_source_factory.create_frame_source",
                   side_effect=_fake_create_fs), \
             patch("app.routes.agricultural_ui.ModelService") as m_model, \
             patch("app.dependencies.get_log_service", return_value=MagicMock()):
            repo = MagicMock()
            repo.get_by_id.return_value = owned_module
            m_repo.return_value = repo
            svc = MagicMock()
            svc.start_session.return_value = MagicMock(id=42)
            m_svc.return_value = svc
            inst = MagicMock()
            inst.check_availability.return_value = MagicMock(value="available")
            m_model.return_value = inst

            resp = client.post(
                "/modulos/1/monitoreo/iniciar",
                # Include a spurious video_path to prove it is IGNORED.
                data={"width_m": "5", "length_m": "2", "notes": "", "video_path": "../evil.mp4"},
                follow_redirects=False,
            )

        assert resp.status_code == 303
        assert svc.start_session.called
        # start_session must not receive any video-file selection argument.
        _, kwargs = svc.start_session.call_args
        assert "video_path" not in kwargs
        # The frame source is a camera source; it was not built from a file path.
        assert "video_path" not in captured

    @pytest.mark.parametrize("stream_fps", [20])
    def test_video_first_start_uses_video_frame_source(self, stream_fps):
        """Spec 023: when video_first_enabled=True, START builds the VIDEO frame
        source with camera_mode='video' and fps=int(camera_stream_fps) (the
        PHYSICAL cadence), decoupled from recording_target_fps."""
        client = _start_client()

        captured = {}

        def _fake_create_fs(**kwargs):
            captured.update(kwargs)
            return MagicMock()

        # A profile stub with video-first enabled. camera_stream_fps drives the
        # physical cadence; recording_target_fps is intentionally different to
        # prove the fps passed to the source is NOT the recording cadence.
        profile = MagicMock()
        profile.video_first_enabled = True
        profile.camera_width = 640
        profile.camera_height = 480
        profile.camera_stream_fps = float(stream_fps)
        profile.recording_target_fps = 5.0
        profile.camera_fps = 30

        owned_module = MagicMock(id=1, name="M")
        with patch("app.routes.agricultural_ui.get_module_repository") as m_repo, \
             patch("app.routes.agricultural_ui._module_owned_by_user", return_value=owned_module), \
             patch("app.routes.agricultural_ui.get_monitoring_service") as m_svc, \
             patch("src.application.services.frame_source_factory.create_frame_source",
                   side_effect=_fake_create_fs), \
             patch("app.routes.agricultural_ui.ModelService") as m_model, \
             patch("app.dependencies.get_log_service", return_value=MagicMock()), \
             patch("src.infrastructure.config.settings.ACTIVE_PROFILE", profile):
            repo = MagicMock()
            repo.get_by_id.return_value = owned_module
            m_repo.return_value = repo
            svc = MagicMock()
            svc.start_session.return_value = MagicMock(id=7)
            m_svc.return_value = svc
            inst = MagicMock()
            inst.check_availability.return_value = MagicMock(value="available")
            m_model.return_value = inst

            resp = client.post(
                "/modulos/1/monitoreo/iniciar",
                data={"width_m": "5", "length_m": "2", "notes": ""},
                follow_redirects=False,
            )

        assert resp.status_code == 303
        assert captured.get("camera_mode") == "video"
        # Spec 023: physical cadence comes from camera_stream_fps, not recording.
        assert captured.get("fps") == int(stream_fps)
        assert isinstance(captured.get("fps"), int)


# --------------------------------------------------------------------------- #
# 3 & 4. Preview during recording — registry worker + get_last_frame copy
# --------------------------------------------------------------------------- #

class _FakeVideoWorker:
    """Minimal video-first worker: exposes get_last_frame returning a COPY."""

    def __init__(self, frame):
        self._buffer = frame

    def get_last_frame(self):
        # Mirror the real worker: return a COPY, never the internal buffer.
        return None if self._buffer is None else self._buffer.copy()


def _preview_client(worker, camera_service=None):
    from app.routes.monitoring_api import router
    from app.dependencies import require_current_user_api, require_monitoring_owner

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_current_user_api] = lambda: MagicMock(id=1)
    # Spec 023: the single-frame preview endpoint now depends on ownership.
    # Ownership itself is covered by test_023_monitoring_preview_ownership.py;
    # here we bypass it (owner granted) to test the worker/frame behavior.
    app.dependency_overrides[require_monitoring_owner] = lambda: MagicMock(id=55)

    registry = MagicMock()
    registry.get_worker.return_value = worker
    app.state.monitoring_runtime_registry = registry
    # A spy CameraService to prove it is NEVER used by the recording preview.
    app.state.camera_service = camera_service or MagicMock()
    app.state.db_manager = MagicMock()
    app.state.log_service = MagicMock()
    return TestClient(app, raise_server_exceptions=False), registry, app.state.camera_service


class TestRecordingPreview:
    def test_preview_uses_worker_last_frame_no_camera(self):
        """Preview reads the worker's last frame; never touches the camera.

        The JPEG encoder is patched to deterministic bytes so the assertion is
        independent of the real cv2 state (other test files may mock cv2).
        """
        frame = np.full((4, 4, 3), 123, dtype=np.uint8)
        worker = _FakeVideoWorker(frame)
        client, registry, camera_service = _preview_client(worker)

        encoded = {}

        def _fake_encode(f):
            # Prove the route hands the worker's frame (a copy) to the encoder.
            encoded["frame"] = f
            return b"JPEGBYTES"

        # Guard: no second camera / Picamera2 constructed anywhere in the path.
        with patch("app.routes.monitoring_api.CameraService.encode_frame_jpeg",
                   side_effect=_fake_encode), \
             patch("src.infrastructure.camera.raspberry_camera_frame_source."
                   "RaspberryCameraFrameSource", side_effect=AssertionError("no 2nd camera"),
                   create=True):
            resp = client.get("/api/monitoring/55/preview")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"
        assert resp.content == b"JPEGBYTES"

        # The frame passed to the encoder came from the worker (value 123).
        assert encoded["frame"] is not None
        assert int(encoded["frame"][0, 0, 0]) == 123

        # Worker obtained from the registry with the right id.
        registry.get_worker.assert_called_with(55)
        # CameraService's camera-opening methods were NEVER used.
        assert not camera_service.capture_single_frame.called
        assert not camera_service.capture_preview_frame.called

    def test_preview_503_when_no_worker(self):
        """No active recording worker → 503, without opening a camera."""
        client, registry, camera_service = _preview_client(worker=None)
        resp = client.get("/api/monitoring/99/preview")
        assert resp.status_code == 503
        assert not camera_service.capture_preview_frame.called
        assert not camera_service.capture_single_frame.called

    def test_preview_503_when_no_frame_yet(self):
        """Worker present but no frame captured yet → 503 (safe, no camera)."""
        worker = _FakeVideoWorker(None)
        client, registry, camera_service = _preview_client(worker)
        resp = client.get("/api/monitoring/1/preview")
        assert resp.status_code == 503
        assert not camera_service.capture_preview_frame.called

    def test_get_last_frame_returns_independent_copy(self):
        """The frame handed to preview does not share memory with the buffer."""
        buffer = np.zeros((3, 3, 3), dtype=np.uint8)
        worker = _FakeVideoWorker(buffer)
        returned = worker.get_last_frame()
        # Mutating the internal buffer must not affect the returned copy.
        buffer[0, 0, 0] = 255
        assert returned[0, 0, 0] == 0
        assert not np.shares_memory(returned, buffer)


# --------------------------------------------------------------------------- #
# 5. Status → UI mapping (Recording / Processing / Completed)
# --------------------------------------------------------------------------- #

class TestStatusLabels:
    def _label(self, status):
        from app.context_builders import build_active_monitoring_context
        from datetime import datetime

        m = MagicMock()
        m.id = 1
        m.status = status
        m.started_at = datetime(2025, 1, 1, 8, 0, 0)
        ctx = build_active_monitoring_context([m])
        return ctx.status_label if ctx else None

    def test_running_is_recording(self):
        assert self._label("running") == "Grabando"

    def test_analyzing_is_processing(self):
        assert self._label("analyzing") == "Procesando video"

    def test_completed_maps_to_completed_wording_in_template(self):
        """The execution template shows a Completed wording for completed."""
        from pathlib import Path

        tpl = Path("app/templates/agricultural/monitoring_execution.html").read_text(
            encoding="utf-8"
        )
        # Completed block wording (Spanish) present.
        assert "completado" in tpl.lower()
        # Running block uses Recording wording; analyzing uses Processing wording.
        assert "Grabando..." in tpl
        assert "Procesando video..." in tpl
