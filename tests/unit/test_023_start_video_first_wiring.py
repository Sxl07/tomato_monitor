"""Spec 023 wiring test for MonitoringService._start_video_first.

Verifies that _start_video_first:
    - builds a RecordingSampler with recording_target_fps,
    - injects configured_recording_fps=recording_target_fps,
    - injects configured_camera_stream_fps=camera_stream_fps,
    - opens the VideoRecorder with fps=recording_target_fps (NOT camera_stream_fps).

No camera / cv2 / picamera2. The worker thread is neutralized (never started)
by patching Thread so we only inspect construction.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.application.services.monitoring_service import MonitoringService


def _profile():
    return SimpleNamespace(
        name="edge",
        video_first_enabled=True,
        recording_target_fps=5.0,
        camera_stream_fps=20.0,
        camera_width=960,
        camera_height=720,
        video_codec_candidates=("mp4v", "avc1"),
        thermal_poll_interval_seconds=5.0,
        thermal_warning_temp=72.0,
        thermal_critical_temp=78.0,
        thermal_resume_temp=65.0,
    )


def _service():
    registry = MagicMock()
    return MonitoringService(
        monitoring_repo=MagicMock(),
        snapshot_repo=MagicMock(),
        inspection_result_repo=MagicMock(),
        metrics_repo=MagicMock(),
        module_repo=MagicMock(),
        runtime_registry=registry,
    ), registry


def test_start_video_first_wires_sampler_and_cadences():
    service, registry = _service()
    monitoring = SimpleNamespace(id=7)
    frame_source = MagicMock()

    captured = {}

    def _fake_worker(**kwargs):
        captured["worker"] = kwargs
        return MagicMock()

    def _fake_recorder(**kwargs):
        captured["recorder"] = kwargs
        return MagicMock()

    def _fake_sampler(**kwargs):
        captured["sampler"] = kwargs
        return MagicMock()

    with patch(
        "src.infrastructure.config.settings.ACTIVE_PROFILE", _profile()
    ), patch(
        "src.application.services.video_recording_worker.VideoRecordingWorker",
        side_effect=_fake_worker,
    ), patch(
        "src.infrastructure.camera.video_recorder.VideoRecorder",
        side_effect=_fake_recorder,
    ), patch(
        "src.application.services.recording_sampler.RecordingSampler",
        side_effect=_fake_sampler,
    ), patch(
        "src.infrastructure.monitoring.thermal_monitor.ThermalMonitor"
    ), patch("threading.Thread") as MockThread:
        MockThread.return_value = MagicMock()
        service._start_video_first(monitoring, frame_source, MagicMock(), None)

    # RecordingSampler built with recording_target_fps.
    assert captured["sampler"]["recording_fps"] == 5.0
    # VideoRecorder opened with recording_target_fps (NOT camera_stream_fps).
    assert captured["recorder"]["fps"] == 5.0
    # Worker receives both cadences and the sampler.
    wk = captured["worker"]
    assert wk["configured_recording_fps"] == 5.0
    assert wk["configured_camera_stream_fps"] == 20.0
    assert "recording_sampler" in wk
