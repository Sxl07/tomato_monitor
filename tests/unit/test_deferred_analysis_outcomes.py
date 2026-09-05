"""Spec 020, Task 11.2 — deferred analysis terminal outcomes.

Verifies that _run_video_analysis (reused unchanged by the manual deferred start)
releases the per-monitoring analysis claim AND the device-global analysis slot on
EVERY outcome (completed / error / crash), so the device is not left blocked, and
that the video is never touched by a failure. The vision pipeline is stubbed.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.application.services.monitoring_runtime_registry import (
    MonitoringRuntimeRegistry,
)
from src.application.services.monitoring_service import MonitoringService
from src.domain.value_objects.monitoring_status import MonitoringState


def _profile():
    return SimpleNamespace(
        name="edge",
        sparse_min_frames_between_detections=3,
        sparse_max_frames_without_detection=8,
        sparse_use_scene_gate=True,
        sparse_enable_flow_propagation=True,
        save_annotated_video=False,
        thermal_poll_interval_seconds=5.0,
        analysis_thermal_pause_threshold=78.0,
        analysis_thermal_resume_threshold=72.0,
    )


@dataclass
class FakeAnalysisResult:
    status: str = "completed"
    detector_scheduled_frames: int = 4
    unique_tomatoes: int = 3
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


def _build():
    registry = MonitoringRuntimeRegistry()
    service = MonitoringService(
        monitoring_repo=MagicMock(),
        snapshot_repo=MagicMock(),
        inspection_result_repo=MagicMock(),
        metrics_repo=MagicMock(),
        module_repo=MagicMock(),
        runtime_registry=registry,
    )
    return service, registry


def _run(service, registry, *, result=None, error_reason=None, crash=False):
    """Invoke _run_video_analysis inline with the pipeline stubbed."""
    # Simulate that the manual start already took both claims.
    registry.claim_global_analysis(1)
    registry.claim_analysis(1)
    registry.register(1, None, MagicMock())

    fake_analysis = MagicMock()
    if crash:
        fake_analysis.run.side_effect = RuntimeError("fatal crash")
    else:
        fake_analysis.run.return_value = result
        fake_analysis.error_reason = error_reason

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
        "src.infrastructure.config.settings.ACTIVE_PROFILE", _profile()
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


class TestClaimReleasedOnEveryOutcome:
    def test_completed_releases_claims(self):
        service, registry = _build()
        _run(service, registry, result=FakeAnalysisResult(status="completed"))
        assert registry.is_analysis_claimed(1) is False
        assert registry.is_global_analysis_active() is False

    def test_error_releases_claims(self):
        service, registry = _build()
        _run(
            service, registry,
            result=FakeAnalysisResult(status="error"), error_reason="boom",
        )
        assert registry.is_analysis_claimed(1) is False
        assert registry.is_global_analysis_active() is False

    def test_crash_releases_claims(self):
        service, registry = _build()
        _run(service, registry, crash=True)
        assert registry.is_analysis_claimed(1) is False
        assert registry.is_global_analysis_active() is False

    def test_device_free_for_next_operation_after_completion(self):
        service, registry = _build()
        _run(service, registry, result=FakeAnalysisResult(status="completed"))
        # A new heavy analysis can take the device-global slot again.
        assert registry.claim_global_analysis(2) is True


class TestVideoIntegrityOnFailure:
    def test_fatal_failure_never_touches_video(self, tmp_path):
        video = tmp_path / "monitoring.mp4"
        video.write_bytes(b"\x00\x01video")
        original = video.read_bytes()

        service, registry = _build()
        registry.claim_global_analysis(1)
        registry.claim_analysis(1)
        registry.register(1, None, MagicMock())

        fake_analysis = MagicMock()
        fake_analysis.run.side_effect = RuntimeError("fatal crash")

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
            "src.infrastructure.config.settings.ACTIVE_PROFILE", _profile()
        ), patch(
            "src.infrastructure.monitoring.thermal_monitor.ThermalMonitor"
        ), patch(
            "src.infrastructure.camera.opencv_video_reader.OpenCvVideoReader"
        ), patch(
            "src.application.services.video_analysis_service.VideoAnalysisService",
            return_value=fake_analysis,
        ):
            MockDbm.return_value.get_session.return_value = MagicMock()
            service._run_video_analysis(1, str(video))

        assert video.exists()
        assert video.read_bytes() == original
        assert registry.is_global_analysis_active() is False
