"""Task 10 tests: video-first integration in MonitoringService.

Covers:
    - start_session routes to VideoRecordingWorker when video_first_enabled.
    - disk-space guard blocks start with an actionable message.
    - finalize_capture (video-first): finalize worker -> validate temp ->
      atomic promote -> persist video_path -> running→analyzing -> launch analysis.
    - invalid temp -> error, temp kept, no video_path, NO analysis launched.
    - _run_video_analysis success -> MonitoringMetrics persisted + completed.
    - video integrity: a fatal analysis failure never deletes/corrupts the video.

Fakes only — no cv2, no real camera, no real video, no detectron2.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.application.services.monitoring_service import (
    MonitoringService,
    DiskSpaceLowError,
)
from src.domain.value_objects.monitoring_status import MonitoringState


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

@dataclass
class FakeRecordingMetrics:
    recording_duration_seconds: float = 10.0
    frames_written: int = 50
    configured_recording_fps: float = 5.0
    container_fps: float = 5.0
    effective_recording_fps: float = 5.0
    deviation_between_configured_and_effective_fps: float = 0.0
    recorded_width: int = 640
    recorded_height: int = 480
    codec_used: str = "mp4v"
    peak_temperature_c: float = 60.0
    exit_reason: str = "finalize"


class FakeVideoRecordingWorker:
    """Mimics VideoRecordingWorker's public interface used by MonitoringService."""

    def __init__(self, *, error_reason=None):
        self.finalize_event = threading.Event()
        self.abort_event = threading.Event()
        self.pause_event = threading.Event()
        self.thermal_pause_event = threading.Event()
        self.complete_event = self.finalize_event
        self._error_reason = error_reason
        self.recording_metrics = FakeRecordingMetrics()

    @property
    def error_reason(self):
        return self._error_reason

    def get_last_frame(self):
        return None

    def release_resources(self):
        pass


def _video_profile(**overrides):
    params = dict(
        name="edge",
        video_first_enabled=True,
        recording_target_fps=5.0,
        video_codec_candidates=("mp4v", "avc1"),
        sparse_min_frames_between_detections=3,
        sparse_max_frames_without_detection=8,
        sparse_use_scene_gate=True,
        sparse_enable_flow_propagation=True,
        save_annotated_video=False,
        thermal_poll_interval_seconds=5.0,
        thermal_warning_temp=72.0,
        thermal_critical_temp=78.0,
        thermal_resume_temp=65.0,
        analysis_thermal_pause_threshold=78.0,
        analysis_thermal_resume_threshold=72.0,
    )
    params.update(overrides)
    return SimpleNamespace(**params)


def _build_service(registry=None):
    monitoring_repo = MagicMock()
    snapshot_repo = MagicMock()
    inspection_result_repo = MagicMock()
    metrics_repo = MagicMock()
    module_repo = MagicMock()
    if registry is None:
        registry = MagicMock()
        registry.claim_finalization.return_value = True
    service = MonitoringService(
        monitoring_repo=monitoring_repo,
        snapshot_repo=snapshot_repo,
        inspection_result_repo=inspection_result_repo,
        metrics_repo=metrics_repo,
        module_repo=module_repo,
        runtime_registry=registry,
    )
    return service, monitoring_repo, registry


def _running_monitoring():
    m = MagicMock()
    m.status = "running"
    m.id = 1
    return m


# --------------------------------------------------------------------------- #
# start_session routing + disk guard
# --------------------------------------------------------------------------- #

class TestStartSessionVideoFirst:
    def test_routes_to_video_recording_worker(self):
        service, monitoring_repo, registry = _build_service()
        service._module_repo.get_by_id.return_value = MagicMock(id=1)
        monitoring_repo.get_by_module.return_value = []
        created = MagicMock(); created.id = 77
        monitoring_repo.create.return_value = created
        registry.has_live_worker_for_module.return_value = False

        started = {}

        def fake_start_video_first(monitoring, frame_source, db_session, log_service):
            started["called"] = True
            started["monitoring"] = monitoring

        with patch(
            "src.infrastructure.config.settings.ACTIVE_PROFILE", _video_profile()
        ), patch.object(service, "_check_disk_space_or_raise"), patch.object(
            service, "_start_video_first", side_effect=fake_start_video_first
        ), patch.object(service, "_start_capture_first") as cap_first, patch.dict(
            "sys.modules",
            {
                "src.infrastructure.camera.raspberry_camera_frame_source": MagicMock(
                    is_camera_locked=MagicMock(return_value=False)
                ),
            },
        ):
            service.start_session(1, 5.0, 2.0, None, MagicMock(), MagicMock())

        assert started.get("called") is True
        cap_first.assert_not_called()

    def test_camera_unavailable_recording_run_transitions_error(self):
        # Worker exits with error_reason set and exit_reason "frame_source_exhausted"
        # (camera unavailable / stopped responding). The run wrapper MUST mark the
        # session error (not leave it running) and surface the actionable message.
        service, monitoring_repo, registry = _build_service()

        worker = FakeVideoRecordingWorker(error_reason="Frame source no disponible")
        worker.recording_metrics.exit_reason = "frame_source_exhausted"

        # A log service to capture the actionable operator message.
        logged = []

        class _Log:
            def add_entry(self, **kw):
                logged.append(kw)

        worker._log_service = _Log()
        worker._monitoring_id = 1

        # worker.run is a no-op here (already "ran"); the wrapper reads error_reason.
        worker.run = MagicMock()

        mock_repo = MagicMock()

        with patch(
            "src.infrastructure.persistence.database.DatabaseManager"
        ) as MockDbm, patch(
            "src.infrastructure.persistence.repositories.SqlMonitoringRepository",
            return_value=mock_repo,
        ), patch(
            "src.application.services.log_service.LogLevel", lambda v: v
        ):
            MockDbm.return_value.get_session.return_value = MagicMock()
            service._run_video_recording_worker(1, worker)

        # Session transitioned to error (never left running).
        mock_repo.update_status.assert_any_call(1, MonitoringState.ERROR.value)
        # Actionable camera-unavailable message emitted.
        assert any(
            e.get("message") == service.CAMERA_UNAVAILABLE_MESSAGE for e in logged
        )
        assert service.CAMERA_UNAVAILABLE_MESSAGE == "La cámara no está disponible. Verifica la conexión."

    def test_route_monitoring_start_uses_video_mode_and_recording_fps(self):
        # Gap 3: the production route (video_first_enabled) must call
        # create_frame_source with camera_mode="video" and
        # fps=ACTIVE_PROFILE.recording_target_fps. Verify by inspecting the
        # monitoring_start source (deterministic, no HTTP/auth/DB needed).
        import ast
        from pathlib import Path

        source = Path("app/routes/agricultural_ui.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "monitoring_start":
                fn = node
                break
        assert fn is not None, "monitoring_start route not found"

        # Find create_frame_source(...) calls guarded by the video-first branch.
        video_calls = []
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "create_frame_source"
            ):
                kwargs = {kw.arg: kw.value for kw in node.keywords}
                video_calls.append(kwargs)

        # At least one create_frame_source call must pass camera_mode="video".
        video_mode_calls = [
            kw for kw in video_calls
            if isinstance(kw.get("camera_mode"), ast.Constant)
            and kw["camera_mode"].value == "video"
        ]
        assert video_mode_calls, (
            "monitoring_start must call create_frame_source(camera_mode='video') "
            "in the video-first branch"
        )
        # And its fps must derive from ACTIVE_PROFILE.recording_target_fps.
        fps_src = video_mode_calls[0].get("fps")
        assert fps_src is not None
        fps_text = ast.dump(fps_src)
        assert "recording_target_fps" in fps_text, (
            "video-first create_frame_source fps must use recording_target_fps"
        )

    def test_frame_source_factory_passes_video_mode_and_recording_fps(self):
        # Gap 2: create_frame_source must build a RaspberryCameraFrameSource with
        # camera_mode="video" and fps=recording_target_fps in video-first mode.
        from src.application.services import frame_source_factory

        captured = {}

        class _FakeRaspberrySource:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        fake_module = SimpleNamespace(
            RaspberryCameraFrameSource=_FakeRaspberrySource,
            PICAMERA2_AVAILABLE=True,
        )

        with patch.dict(
            "sys.modules",
            {"src.infrastructure.camera.raspberry_camera_frame_source": fake_module},
        ):
            src = frame_source_factory.create_frame_source(
                width=640, height=480, fps=5, camera_mode="video"
            )

        assert isinstance(src, _FakeRaspberrySource)
        assert captured["camera_mode"] == "video"
        assert captured["fps"] == 5
        assert captured["width"] == 640
        assert captured["height"] == 480

    def test_disk_space_low_blocks_start(self):
        service, monitoring_repo, registry = _build_service()
        service._module_repo.get_by_id.return_value = MagicMock(id=1)
        monitoring_repo.get_by_module.return_value = []
        registry.has_live_worker_for_module.return_value = False

        with patch(
            "src.infrastructure.config.settings.ACTIVE_PROFILE", _video_profile()
        ), patch(
            "shutil.disk_usage", return_value=SimpleNamespace(total=1, used=1, free=10 * 1024 * 1024)
        ), patch.dict(
            "sys.modules",
            {
                "src.infrastructure.camera.raspberry_camera_frame_source": MagicMock(
                    is_camera_locked=MagicMock(return_value=False)
                ),
            },
        ):
            with pytest.raises(DiskSpaceLowError) as exc_info:
                service.start_session(1, 5.0, 2.0, None, MagicMock(), MagicMock())

        assert "Espacio en disco bajo" in str(exc_info.value)
        # Session must NOT have been created when disk is low.
        monitoring_repo.create.assert_not_called()


# --------------------------------------------------------------------------- #
# finalize_capture (video-first) happy path
# --------------------------------------------------------------------------- #

def _finalize_setup(worker, registry, monitoring_repo):
    m = _running_monitoring()
    monitoring_repo.get_by_id.return_value = m
    monitoring_repo.update_status.return_value = m
    monitoring_repo.update_video_path.return_value = m
    thread = MagicMock()
    thread.is_alive.return_value = False
    registry.get_worker.return_value = worker
    registry.get_thread.return_value = thread
    registry.claim_finalization.return_value = True


class TestFinalizeVideoFirst:
    def test_happy_path_validates_promotes_persists_and_launches_analysis(self):
        service, monitoring_repo, registry = _build_service()
        worker = FakeVideoRecordingWorker()
        _finalize_setup(worker, registry, monitoring_repo)

        launched = {}

        def fake_thread(*args, **kwargs):
            launched["target"] = kwargs.get("target")
            launched["args"] = kwargs.get("args")
            t = MagicMock()
            t.start = MagicMock()
            return t

        with patch(
            "src.infrastructure.config.settings.ACTIVE_PROFILE", _video_profile()
        ), patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.camera.video_recorder.VideoRecorder"
        ) as MockRecorder, patch("os.replace") as mock_replace, patch(
            "os.makedirs"
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter"
        ), patch("threading.Thread", side_effect=fake_thread):
            MockRecorder.return_value.validate.return_value = True
            result = service.finalize_capture(1)

        # Atomic promotion happened.
        assert mock_replace.called
        # video_path persisted (relative) AFTER validation/promotion.
        monitoring_repo.update_video_path.assert_called_once()
        args = monitoring_repo.update_video_path.call_args[0]
        assert args[0] == 1
        assert args[1] == "outputs/monitorings/1/video/monitoring.mp4"
        # Transitioned to analyzing.
        monitoring_repo.update_status.assert_any_call(1, MonitoringState.ANALYZING.value)
        # Analysis launched on the FINAL video path.
        assert launched["target"] == service._run_video_analysis
        assert launched["args"] == (1, "outputs/monitorings/1/video/monitoring.mp4")

    def test_invalid_temp_goes_to_error_no_promotion_no_analysis(self):
        service, monitoring_repo, registry = _build_service()
        worker = FakeVideoRecordingWorker()
        _finalize_setup(worker, registry, monitoring_repo)

        with patch(
            "src.infrastructure.config.settings.ACTIVE_PROFILE", _video_profile()
        ), patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.camera.video_recorder.VideoRecorder"
        ) as MockRecorder, patch("os.replace") as mock_replace, patch(
            "threading.Thread"
        ) as MockThread:
            MockRecorder.return_value.validate.return_value = False
            service.finalize_capture(1)

        # No promotion, no video_path persisted, error state, no analysis thread.
        mock_replace.assert_not_called()
        monitoring_repo.update_video_path.assert_not_called()
        monitoring_repo.update_status.assert_any_call(1, MonitoringState.ERROR.value)
        MockThread.assert_not_called()


# --------------------------------------------------------------------------- #
# _run_video_analysis success + video integrity
# --------------------------------------------------------------------------- #

@dataclass
class FakeAnalysisResult:
    status: str = "completed"
    detector_scheduled_frames: int = 4
    unique_tomatoes: int = 3
    total_detection_rows: int = 5
    healthy_count: int = 2
    unhealthy_count: int = 1
    snapshots_with_detections: int = 2
    maturity_counts: dict = None

    def __post_init__(self):
        if self.maturity_counts is None:
            self.maturity_counts = {
                "green": 1, "breaker": 0, "turning": 0,
                "pink": 0, "light_red": 0, "red": 2,
            }


def _patch_analysis_thread_deps(monkeypatch):
    """Patch DB/repos/thermal/reader for _run_video_analysis to run inline."""


class TestRunVideoAnalysis:
    def _run(self, analysis_result, analysis_error=None, tmp_video=None):
        service, monitoring_repo, registry = _build_service()

        fake_analysis = MagicMock()
        fake_analysis.run.return_value = analysis_result
        fake_analysis.error_reason = analysis_error

        mock_mon_repo = MagicMock()
        mock_metrics_repo = MagicMock()

        with patch(
            "src.infrastructure.persistence.database.DatabaseManager"
        ) as MockDbm, patch(
            "src.infrastructure.persistence.repositories.SqlMonitoringRepository",
            return_value=mock_mon_repo,
        ), patch(
            "src.infrastructure.persistence.repositories.SqlSnapshotRepository"
        ), patch(
            "src.infrastructure.persistence.repositories.SqlInspectionResultRepository"
        ), patch(
            "src.infrastructure.persistence.repositories.SqlMonitoringMetricsRepository",
            return_value=mock_metrics_repo,
        ), patch(
            "src.infrastructure.config.settings.ACTIVE_PROFILE", _video_profile()
        ), patch(
            "src.infrastructure.monitoring.thermal_monitor.ThermalMonitor"
        ), patch(
            "src.infrastructure.camera.opencv_video_reader.OpenCvVideoReader"
        ), patch(
            "src.application.services.video_analysis_service.VideoAnalysisService",
            return_value=fake_analysis,
        ):
            MockDbm.return_value.get_session.return_value = MagicMock()
            service._run_video_analysis(1, "outputs/monitorings/1/video/monitoring.mp4")

        return mock_mon_repo, mock_metrics_repo

    def test_success_persists_metrics_and_completes(self):
        mon_repo, metrics_repo = self._run(FakeAnalysisResult(status="completed"))
        # Metrics persisted via create_pending_for_finalization.
        assert metrics_repo.create_pending_for_finalization.called
        metrics = metrics_repo.create_pending_for_finalization.call_args[0][1]
        assert metrics.total_tomatoes == 3
        assert metrics.healthy_count == 2
        assert metrics.unhealthy_count == 1
        assert metrics.snapshots_with_detections == 2
        # Transitioned to completed.
        mon_repo.update_status.assert_any_call(1, MonitoringState.COMPLETED.value)

    def test_analysis_error_transitions_error(self):
        mon_repo, metrics_repo = self._run(
            FakeAnalysisResult(status="error"), analysis_error="boom"
        )
        metrics_repo.create_pending_for_finalization.assert_not_called()
        mon_repo.update_status.assert_any_call(1, MonitoringState.ERROR.value)

    def test_video_integrity_analysis_failure_never_touches_video(self, tmp_path):
        # A real video file exists; a fatal analysis failure must not delete or
        # modify it (MonitoringService never writes/deletes the video on analysis).
        video = tmp_path / "monitoring.mp4"
        video.write_bytes(b"\x00\x01\x02\x03video-bytes")
        original = video.read_bytes()

        service, monitoring_repo, registry = _build_service()
        fake_analysis = MagicMock()
        fake_analysis.run.side_effect = RuntimeError("fatal analysis crash")

        with patch(
            "src.infrastructure.persistence.database.DatabaseManager"
        ) as MockDbm, patch(
            "src.infrastructure.persistence.repositories.SqlMonitoringRepository"
        ), patch(
            "src.infrastructure.persistence.repositories.SqlSnapshotRepository"
        ), patch(
            "src.infrastructure.persistence.repositories.SqlInspectionResultRepository"
        ), patch(
            "src.infrastructure.persistence.repositories.SqlMonitoringMetricsRepository"
        ), patch(
            "src.infrastructure.config.settings.ACTIVE_PROFILE", _video_profile()
        ), patch(
            "src.infrastructure.monitoring.thermal_monitor.ThermalMonitor"
        ), patch(
            "src.infrastructure.camera.opencv_video_reader.OpenCvVideoReader"
        ), patch(
            "src.application.services.video_analysis_service.VideoAnalysisService",
            return_value=fake_analysis,
        ):
            MockDbm.return_value.get_session.return_value = MagicMock()
            # Must not raise out of the background-run method.
            service._run_video_analysis(1, str(video))

        # The video file is untouched after a fatal analysis failure.
        assert video.exists()
        assert video.read_bytes() == original
