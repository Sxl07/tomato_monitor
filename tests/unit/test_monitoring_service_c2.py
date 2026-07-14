"""Unit tests for MonitoringService C2 — finalize_capture + analysis flow.

Validates:
- finalize_capture() signals finalize_event, NOT abort_event
- finalize_capture() clears pause_event and thermal_pause_event
- Transitions to analyzing when snapshots >= 1
- Transitions to completed when snapshots == 0 (empty metrics via create_pending)
- Transitions to error when worker absent from registry
- Transitions to error on thread.join timeout
- Transitions to error when worker.error_reason is set
- Concurrent finalization raises FinalizationInProgressError
- Result status is never "aborted"
- registry.release_finalization called on success
- Zero snapshots: create_pending_for_finalization (not create)
- thread.join(timeout=10.0) verified
- _run_analysis configuration uses analysis_* fields
- _run_analysis success/error flows
- _run_worker uses remove_runtime (not remove)
- Capture writer error handling (recoverable)
- Metrics computation (unique_tomatoes → total_detections)

Spec 009, Phase C2.
No dependency on cv2, numpy, CaptureWorker, or any heavy vision module.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Optional
from unittest.mock import MagicMock, patch, call

import pytest

from src.application.services.monitoring_service import (
    MonitoringService,
    FinalizationInProgressError,
    MonitoringNotFoundError,
)


# ---------------------------------------------------------------------------
# Lightweight FakeCaptureRuntime (no cv2, no numpy, no CaptureWorker)
# ---------------------------------------------------------------------------


@dataclass
class FakeCaptureMetrics:
    """Lightweight stand-in for CaptureMetrics."""

    duration_seconds: float = 12.5
    total_snapshots: int = 5
    total_loop_cycles: int = 100
    effective_fps: float = 8.0
    capture_reasons: dict = field(
        default_factory=lambda: {"first_frame": 1, "scene_change": 3, "timeout": 1}
    )
    avg_iteration_ms: float = 125.0
    avg_scene_gate_ms: float = 15.0
    avg_save_ms: float = 40.0
    peak_temperature_c: float = 55.0
    thermal_events: int = 0
    exit_reason: str = "finalize"


class FakeCaptureRuntime:
    """Lightweight runtime that mimics CaptureWorker's event interface."""

    def __init__(self):
        self.finalize_event = threading.Event()
        self.abort_event = threading.Event()
        self.pause_event = threading.Event()
        self.thermal_pause_event = threading.Event()
        self.capture_metrics = FakeCaptureMetrics()
        self._error_reason: Optional[str] = None

    @property
    def error_reason(self) -> Optional[str]:
        return self._error_reason

    def release_resources(self):
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_service_and_worker(registry=None):
    """Build a MonitoringService and a FakeCaptureRuntime with all mocks."""
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

    worker = FakeCaptureRuntime()

    # Mock monitoring in running state
    mock_monitoring = MagicMock()
    mock_monitoring.status = "running"
    mock_monitoring.id = 1
    monitoring_repo.get_by_id.return_value = mock_monitoring
    monitoring_repo.update_status.return_value = mock_monitoring
    monitoring_repo.update_counters.return_value = mock_monitoring

    # Thread that's already dead (join returns immediately)
    mock_thread = MagicMock()
    mock_thread.is_alive.return_value = False

    registry.get_worker.return_value = worker
    registry.get_thread.return_value = mock_thread

    return (
        service, worker, monitoring_repo, snapshot_repo,
        inspection_result_repo, metrics_repo, registry, mock_thread,
    )


# ---------------------------------------------------------------------------
# A. Zero snapshots
# ---------------------------------------------------------------------------


class TestFinalizeZeroSnapshots:
    """Zero snapshots → completed via create_pending_for_finalization."""

    def test_update_counters_called_with_zeros(self):
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, _) = _build_service_and_worker()
        snapshot_repo.get_by_monitoring.return_value = []

        with patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter"
        ):
            service.finalize_capture(1)

        monitoring_repo.update_counters.assert_called_once_with(
            1, total_snapshots=0, total_detections=0
        )

    def test_create_pending_for_finalization_called(self):
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, _) = _build_service_and_worker()
        snapshot_repo.get_by_monitoring.return_value = []

        with patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter"
        ):
            service.finalize_capture(1)

        metrics_repo.create_pending_for_finalization.assert_called_once()
        args = metrics_repo.create_pending_for_finalization.call_args[0]
        assert args[0] == 1
        assert args[1].total_tomatoes == 0

    def test_create_not_called(self):
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, _) = _build_service_and_worker()
        snapshot_repo.get_by_monitoring.return_value = []

        with patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter"
        ):
            service.finalize_capture(1)

        metrics_repo.create.assert_not_called()

    def test_create_pending_before_completed(self):
        """create_pending_for_finalization occurs before update_status(completed)."""
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, _) = _build_service_and_worker()
        snapshot_repo.get_by_monitoring.return_value = []

        call_order = []
        metrics_repo.create_pending_for_finalization.side_effect = (
            lambda *a, **k: call_order.append("create_pending")
        )
        monitoring_repo.update_status.side_effect = (
            lambda mid, status: (
                call_order.append(f"update_status:{status}"),
                MagicMock(status=status),
            )[1]
        )

        with patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter"
        ):
            service.finalize_capture(1)

        pending_idx = call_order.index("create_pending")
        completed_idx = call_order.index("update_status:completed")
        assert pending_idx < completed_idx

    def test_no_analysis_thread_started(self):
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, _) = _build_service_and_worker()
        snapshot_repo.get_by_monitoring.return_value = []

        with patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter"
        ), patch(
            "src.application.services.monitoring_service.threading.Thread"
        ) as mock_thread_cls:
            service.finalize_capture(1)

        mock_thread_cls.assert_not_called()


# ---------------------------------------------------------------------------
# B. _run_analysis successful
# ---------------------------------------------------------------------------


class TestRunAnalysisSuccess:
    """_run_analysis happy path: session, repos, metrics, completed."""

    def test_run_analysis_success_flow(self):
        registry = MagicMock()
        service = MonitoringService(
            monitoring_repo=MagicMock(),
            snapshot_repo=MagicMock(),
            inspection_result_repo=MagicMock(),
            metrics_repo=MagicMock(),
            module_repo=MagicMock(),
            runtime_registry=registry,
        )

        mock_session = MagicMock()
        mock_db_manager = MagicMock()
        mock_db_manager.get_session.return_value = mock_session

        mock_monitoring_repo = MagicMock()
        mock_snapshot_repo = MagicMock()
        mock_inspection_repo = MagicMock()
        mock_metrics_repo = MagicMock()

        # Fake analysis result
        mock_result = MagicMock()
        mock_result.total_snapshots = 10
        mock_result.unique_tomatoes = 5
        mock_result.healthy_count = 4
        mock_result.unhealthy_count = 1
        mock_result.maturity_counts = {"green": 1, "breaker": 1, "turning": 1, "pink": 1, "light_red": 0, "red": 1}
        mock_result.snapshots_with_detections = 8

        mock_analysis_service = MagicMock()
        mock_analysis_service.run.return_value = mock_result
        mock_analysis_service.progress = MagicMock(status="completed")
        mock_analysis_service.error_reason = None

        mock_profile = SimpleNamespace(
            thermal_poll_interval_seconds=5.0,
            analysis_thermal_pause_threshold=72.0,
            analysis_thermal_resume_threshold=65.0,
            analysis_skip_maturity=False,
            name="edge",
        )

        with patch(
            "src.infrastructure.persistence.database.DatabaseManager",
            return_value=mock_db_manager,
        ), patch(
            "src.infrastructure.persistence.repositories.SqlMonitoringRepository",
            return_value=mock_monitoring_repo,
        ), patch(
            "src.infrastructure.persistence.repositories.SqlSnapshotRepository",
            return_value=mock_snapshot_repo,
        ), patch(
            "src.infrastructure.persistence.repositories.SqlInspectionResultRepository",
            return_value=mock_inspection_repo,
        ), patch(
            "src.infrastructure.persistence.repositories.SqlMonitoringMetricsRepository",
            return_value=mock_metrics_repo,
        ), patch(
            "src.infrastructure.config.settings.ACTIVE_PROFILE", mock_profile,
        ), patch(
            "src.infrastructure.monitoring.thermal_monitor.ThermalMonitor",
        ) as MockThermal, patch(
            "src.application.services.snapshot_analysis_service.SnapshotAnalysisService",
            return_value=mock_analysis_service,
        ):
            service._run_analysis(42)

        # Verify thread-local session used
        mock_db_manager.get_session.assert_called_once()

        # Verify registry.set_worker
        registry.set_worker.assert_called_once_with(42, mock_analysis_service)

        # Verify counters use unique_tomatoes
        mock_monitoring_repo.update_counters.assert_called_once_with(
            42, total_snapshots=10, total_detections=5,
        )

        # Verify create_pending_for_finalization
        mock_metrics_repo.create_pending_for_finalization.assert_called_once()
        m_args = mock_metrics_repo.create_pending_for_finalization.call_args[0]
        assert m_args[0] == 42

        # Verify analyzing → completed
        mock_monitoring_repo.update_status.assert_called_once_with(
            42, "completed"
        )

        # Verify session closed and registry removed
        mock_session.close.assert_called_once()
        registry.remove.assert_called_once_with(42)


# ---------------------------------------------------------------------------
# C. _run_analysis fatal (analysis_service.run() raises)
# ---------------------------------------------------------------------------


class TestRunAnalysisFatal:
    """_run_analysis crash: rollback, error, no metrics, close, cleanup."""

    def test_run_analysis_crash_flow(self):
        registry = MagicMock()
        service = MonitoringService(
            monitoring_repo=MagicMock(),
            snapshot_repo=MagicMock(),
            inspection_result_repo=MagicMock(),
            metrics_repo=MagicMock(),
            module_repo=MagicMock(),
            runtime_registry=registry,
        )

        mock_session = MagicMock()
        mock_db_manager = MagicMock()
        mock_db_manager.get_session.return_value = mock_session

        mock_monitoring_repo = MagicMock()
        mock_metrics_repo = MagicMock()

        mock_analysis_service = MagicMock()
        mock_analysis_service.run.side_effect = RuntimeError("Model crash")

        mock_profile = SimpleNamespace(
            thermal_poll_interval_seconds=5.0,
            analysis_thermal_pause_threshold=72.0,
            analysis_thermal_resume_threshold=65.0,
            analysis_skip_maturity=False,
            name="edge",
        )

        with patch(
            "src.infrastructure.persistence.database.DatabaseManager",
            return_value=mock_db_manager,
        ), patch(
            "src.infrastructure.persistence.repositories.SqlMonitoringRepository",
            return_value=mock_monitoring_repo,
        ), patch(
            "src.infrastructure.persistence.repositories.SqlSnapshotRepository",
        ), patch(
            "src.infrastructure.persistence.repositories.SqlInspectionResultRepository",
        ), patch(
            "src.infrastructure.persistence.repositories.SqlMonitoringMetricsRepository",
            return_value=mock_metrics_repo,
        ), patch(
            "src.infrastructure.config.settings.ACTIVE_PROFILE", mock_profile,
        ), patch(
            "src.infrastructure.monitoring.thermal_monitor.ThermalMonitor",
        ), patch(
            "src.application.services.snapshot_analysis_service.SnapshotAnalysisService",
            return_value=mock_analysis_service,
        ):
            service._run_analysis(42)

        # rollback called
        mock_session.rollback.assert_called()
        # transition to error attempted
        mock_monitoring_repo.update_status.assert_called_with(42, "error")
        # no metrics created
        mock_metrics_repo.create_pending_for_finalization.assert_not_called()
        mock_metrics_repo.create.assert_not_called()
        # session closed
        mock_session.close.assert_called_once()
        # registry cleaned
        registry.remove.assert_called_once_with(42)


# ---------------------------------------------------------------------------
# D. Constructor failure (repos/ThermalMonitor/SnapshotAnalysisService raises)
# ---------------------------------------------------------------------------


class TestRunAnalysisConstructorFailure:
    """Import or construction failure inside _run_analysis."""

    def test_repo_construction_failure_does_not_escape(self):
        """If SqlMonitoringRepository raises, exception stays in thread."""
        registry = MagicMock()
        service = MonitoringService(
            monitoring_repo=MagicMock(),
            snapshot_repo=MagicMock(),
            inspection_result_repo=MagicMock(),
            metrics_repo=MagicMock(),
            module_repo=MagicMock(),
            runtime_registry=registry,
        )

        mock_session = MagicMock()
        mock_db_manager = MagicMock()
        mock_db_manager.get_session.return_value = mock_session

        mock_profile = SimpleNamespace(
            thermal_poll_interval_seconds=5.0,
            analysis_thermal_pause_threshold=72.0,
            analysis_thermal_resume_threshold=65.0,
            analysis_skip_maturity=False,
            name="edge",
        )

        with patch(
            "src.infrastructure.persistence.database.DatabaseManager",
            return_value=mock_db_manager,
        ), patch(
            "src.infrastructure.persistence.repositories.SqlMonitoringRepository",
            side_effect=RuntimeError("Connection broken"),
        ), patch(
            "src.infrastructure.config.settings.ACTIVE_PROFILE", mock_profile,
        ):
            # Must not raise
            service._run_analysis(42)

        # rollback when session exists
        mock_session.rollback.assert_called()
        # close called
        mock_session.close.assert_called_once()
        # registry cleaned
        registry.remove.assert_called_once_with(42)

    def test_thermal_monitor_failure_does_not_escape(self):
        """If ThermalMonitor raises, exception stays in thread."""
        registry = MagicMock()
        service = MonitoringService(
            monitoring_repo=MagicMock(),
            snapshot_repo=MagicMock(),
            inspection_result_repo=MagicMock(),
            metrics_repo=MagicMock(),
            module_repo=MagicMock(),
            runtime_registry=registry,
        )

        mock_session = MagicMock()
        mock_db_manager = MagicMock()
        mock_db_manager.get_session.return_value = mock_session
        mock_monitoring_repo = MagicMock()

        mock_profile = SimpleNamespace(
            thermal_poll_interval_seconds=5.0,
            analysis_thermal_pause_threshold=72.0,
            analysis_thermal_resume_threshold=65.0,
            analysis_skip_maturity=False,
            name="edge",
        )

        with patch(
            "src.infrastructure.persistence.database.DatabaseManager",
            return_value=mock_db_manager,
        ), patch(
            "src.infrastructure.persistence.repositories.SqlMonitoringRepository",
            return_value=mock_monitoring_repo,
        ), patch(
            "src.infrastructure.persistence.repositories.SqlSnapshotRepository",
        ), patch(
            "src.infrastructure.persistence.repositories.SqlInspectionResultRepository",
        ), patch(
            "src.infrastructure.persistence.repositories.SqlMonitoringMetricsRepository",
        ), patch(
            "src.infrastructure.config.settings.ACTIVE_PROFILE", mock_profile,
        ), patch(
            "src.infrastructure.monitoring.thermal_monitor.ThermalMonitor",
            side_effect=RuntimeError("Thermal init failed"),
        ):
            service._run_analysis(42)

        mock_session.close.assert_called_once()
        registry.remove.assert_called_once_with(42)

    def test_db_manager_failure_does_not_escape(self):
        """If DatabaseManager raises, exception stays in thread, no session used."""
        registry = MagicMock()
        service = MonitoringService(
            monitoring_repo=MagicMock(),
            snapshot_repo=MagicMock(),
            inspection_result_repo=MagicMock(),
            metrics_repo=MagicMock(),
            module_repo=MagicMock(),
            runtime_registry=registry,
        )

        with patch(
            "src.infrastructure.persistence.database.DatabaseManager",
            side_effect=RuntimeError("Cannot create DB manager"),
        ):
            service._run_analysis(42)

        registry.remove.assert_called_once_with(42)


# ---------------------------------------------------------------------------
# E. Configuration — uses analysis_* fields exclusively
# ---------------------------------------------------------------------------


class TestRunAnalysisConfiguration:
    """_run_analysis uses the correct analysis-specific profile fields."""

    def test_uses_analysis_skip_maturity(self):
        """analysis_skip_maturity comes from ACTIVE_PROFILE.analysis_skip_maturity."""
        registry = MagicMock()
        service = MonitoringService(
            monitoring_repo=MagicMock(),
            snapshot_repo=MagicMock(),
            inspection_result_repo=MagicMock(),
            metrics_repo=MagicMock(),
            module_repo=MagicMock(),
            runtime_registry=registry,
        )

        mock_session = MagicMock()
        mock_db_manager = MagicMock()
        mock_db_manager.get_session.return_value = mock_session

        # Deliberately different values between capture and analysis
        mock_profile = SimpleNamespace(
            thermal_poll_interval_seconds=3.0,
            # Capture thermal (should NOT be used)
            thermal_critical_temp=999.0,
            thermal_resume_temp=888.0,
            # Analysis specific
            analysis_thermal_pause_threshold=72.0,
            analysis_thermal_resume_threshold=65.0,
            analysis_skip_maturity=True,
            # capture field that should NOT be used
            skip_maturity=False,
            name="test_profile",
        )

        captured_thermal_args = {}
        captured_analysis_args = {}

        class FakeThermalMonitor:
            def __init__(self, pause_event, **kwargs):
                captured_thermal_args.update(kwargs)

        class FakeAnalysisService:
            def __init__(self, **kwargs):
                captured_analysis_args.update(kwargs)
                self.progress = MagicMock(status="completed")
                self.error_reason = None

            def run(self):
                result = MagicMock()
                result.total_snapshots = 0
                result.unique_tomatoes = 0
                result.healthy_count = 0
                result.unhealthy_count = 0
                result.maturity_counts = {}
                result.snapshots_with_detections = 0
                return result

        with patch(
            "src.infrastructure.persistence.database.DatabaseManager",
            return_value=mock_db_manager,
        ), patch(
            "src.infrastructure.persistence.repositories.SqlMonitoringRepository",
        ), patch(
            "src.infrastructure.persistence.repositories.SqlSnapshotRepository",
        ), patch(
            "src.infrastructure.persistence.repositories.SqlInspectionResultRepository",
        ), patch(
            "src.infrastructure.persistence.repositories.SqlMonitoringMetricsRepository",
        ), patch(
            "src.infrastructure.config.settings.ACTIVE_PROFILE", mock_profile,
        ), patch(
            "src.infrastructure.monitoring.thermal_monitor.ThermalMonitor",
            FakeThermalMonitor,
        ), patch(
            "src.application.services.snapshot_analysis_service.SnapshotAnalysisService",
            FakeAnalysisService,
        ):
            service._run_analysis(42)

        # Verify ThermalMonitor gets analysis thresholds
        assert captured_thermal_args["warning_temp"] == 72.0
        assert captured_thermal_args["critical_temp"] == 72.0
        assert captured_thermal_args["resume_temp"] == 65.0
        assert captured_thermal_args["poll_interval_seconds"] == 3.0

        # Verify SnapshotAnalysisService gets analysis_skip_maturity
        assert captured_analysis_args["analysis_skip_maturity"] is True


# ---------------------------------------------------------------------------
# F. Recoverable errors — result.errors not empty but completed
# ---------------------------------------------------------------------------


class TestRunAnalysisRecoverableErrors:
    """Recoverable errors don't prevent completed status."""

    def test_errors_in_result_but_completed(self):
        registry = MagicMock()
        service = MonitoringService(
            monitoring_repo=MagicMock(),
            snapshot_repo=MagicMock(),
            inspection_result_repo=MagicMock(),
            metrics_repo=MagicMock(),
            module_repo=MagicMock(),
            runtime_registry=registry,
        )

        mock_session = MagicMock()
        mock_db_manager = MagicMock()
        mock_db_manager.get_session.return_value = mock_session

        mock_monitoring_repo = MagicMock()
        mock_metrics_repo = MagicMock()

        # Result has errors but progress is completed, error_reason is None
        mock_result = MagicMock()
        mock_result.total_snapshots = 5
        mock_result.unique_tomatoes = 3
        mock_result.healthy_count = 2
        mock_result.unhealthy_count = 1
        mock_result.maturity_counts = {}
        mock_result.snapshots_with_detections = 4
        mock_result.errors = ["Snapshot 3: read failed", "Annotation failed"]

        mock_analysis_service = MagicMock()
        mock_analysis_service.run.return_value = mock_result
        mock_analysis_service.progress = MagicMock(status="completed")
        mock_analysis_service.error_reason = None

        mock_profile = SimpleNamespace(
            thermal_poll_interval_seconds=5.0,
            analysis_thermal_pause_threshold=72.0,
            analysis_thermal_resume_threshold=65.0,
            analysis_skip_maturity=False,
            name="edge",
        )

        with patch(
            "src.infrastructure.persistence.database.DatabaseManager",
            return_value=mock_db_manager,
        ), patch(
            "src.infrastructure.persistence.repositories.SqlMonitoringRepository",
            return_value=mock_monitoring_repo,
        ), patch(
            "src.infrastructure.persistence.repositories.SqlSnapshotRepository",
        ), patch(
            "src.infrastructure.persistence.repositories.SqlInspectionResultRepository",
        ), patch(
            "src.infrastructure.persistence.repositories.SqlMonitoringMetricsRepository",
            return_value=mock_metrics_repo,
        ), patch(
            "src.infrastructure.config.settings.ACTIVE_PROFILE", mock_profile,
        ), patch(
            "src.infrastructure.monitoring.thermal_monitor.ThermalMonitor",
        ), patch(
            "src.application.services.snapshot_analysis_service.SnapshotAnalysisService",
            return_value=mock_analysis_service,
        ):
            service._run_analysis(42)

        # Monitoring ends completed (not error)
        mock_monitoring_repo.update_status.assert_called_once_with(42, "completed")
        mock_metrics_repo.create_pending_for_finalization.assert_called_once()


# ---------------------------------------------------------------------------
# G. Capture writer errors (recoverable)
# ---------------------------------------------------------------------------


class TestCaptureWriterErrors:
    """write_capture_metrics errors are recoverable — flow continues."""

    def test_write_capture_metrics_returns_errors(self):
        """Errors in write_result.errors are logged, flow continues."""
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, _) = _build_service_and_worker()
        snapshot_repo.get_by_monitoring.return_value = []

        mock_write_result = MagicMock()
        mock_write_result.errors = ["disk full", "permission denied"]
        mock_write_result.paths = {}

        mock_writer = MagicMock()
        mock_writer.write_capture_metrics.return_value = mock_write_result

        with patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter",
            return_value=mock_writer,
        ), patch(
            "src.infrastructure.config.settings.ACTIVE_PROFILE",
            SimpleNamespace(name="edge"),
        ):
            result = service.finalize_capture(1)

        # Flow continued to completion
        metrics_repo.create_pending_for_finalization.assert_called_once()
        # Did not transition to error
        status_calls = [c[0][1] for c in monitoring_repo.update_status.call_args_list]
        assert "error" not in status_calls

    def test_write_capture_metrics_raises_exception(self):
        """Exception from writer is caught, flow continues."""
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, _) = _build_service_and_worker()
        snapshot_repo.get_by_monitoring.return_value = []

        mock_writer = MagicMock()
        mock_writer.write_capture_metrics.side_effect = OSError("disk exploded")

        with patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter",
            return_value=mock_writer,
        ), patch(
            "src.infrastructure.config.settings.ACTIVE_PROFILE",
            SimpleNamespace(name="edge"),
        ):
            result = service.finalize_capture(1)

        # Flow continued to completion
        metrics_repo.create_pending_for_finalization.assert_called_once()


# ---------------------------------------------------------------------------
# H. _run_worker uses remove_runtime (not remove) in all paths
# ---------------------------------------------------------------------------


class TestRunWorkerUsesRemoveRuntime:
    """_run_worker exit paths use remove_runtime to preserve finalization claims."""

    def test_db_session_creation_failure_uses_remove_runtime(self):
        from src.application.services.monitoring_service import MonitoringService

        worker = FakeCaptureRuntime()
        worker.release_resources = MagicMock()

        registry = MagicMock()
        service = MonitoringService(
            monitoring_repo=MagicMock(),
            snapshot_repo=MagicMock(),
            inspection_result_repo=MagicMock(),
            metrics_repo=MagicMock(),
            module_repo=MagicMock(),
            runtime_registry=registry,
        )

        with patch(
            "src.infrastructure.persistence.database.DatabaseManager",
            side_effect=RuntimeError("Connection refused"),
        ):
            service._run_worker(1, worker)

        worker.release_resources.assert_called_once()
        registry.remove_runtime.assert_called_once_with(1)
        registry.remove.assert_not_called()

    def test_transition_failure_uses_remove_runtime(self):
        from src.application.services.monitoring_service import MonitoringService

        worker = FakeCaptureRuntime()
        worker.release_resources = MagicMock()

        registry = MagicMock()
        service = MonitoringService(
            monitoring_repo=MagicMock(),
            snapshot_repo=MagicMock(),
            inspection_result_repo=MagicMock(),
            metrics_repo=MagicMock(),
            module_repo=MagicMock(),
            runtime_registry=registry,
        )

        mock_session = MagicMock()
        mock_db_manager = MagicMock()
        mock_db_manager.get_session.return_value = mock_session

        mock_monitoring_repo = MagicMock()
        mock_monitoring_repo.update_status.side_effect = RuntimeError("DB locked")

        with patch(
            "src.infrastructure.persistence.database.DatabaseManager",
            return_value=mock_db_manager,
        ), patch(
            "src.infrastructure.persistence.repositories.SqlMonitoringRepository",
            return_value=mock_monitoring_repo,
        ), patch(
            "src.infrastructure.persistence.repositories.SqlSnapshotRepository",
        ):
            service._run_worker(1, worker)

        worker.release_resources.assert_called_once()
        mock_session.close.assert_called_once()
        registry.remove_runtime.assert_called_once_with(1)
        registry.remove.assert_not_called()

    def test_normal_exit_uses_remove_runtime(self):
        from src.application.services.monitoring_service import MonitoringService

        worker = FakeCaptureRuntime()
        worker.run = MagicMock()  # Simulates worker.run() completing

        registry = MagicMock()
        service = MonitoringService(
            monitoring_repo=MagicMock(),
            snapshot_repo=MagicMock(),
            inspection_result_repo=MagicMock(),
            metrics_repo=MagicMock(),
            module_repo=MagicMock(),
            runtime_registry=registry,
        )

        mock_session = MagicMock()
        mock_db_manager = MagicMock()
        mock_db_manager.get_session.return_value = mock_session

        mock_monitoring_repo = MagicMock()

        with patch(
            "src.infrastructure.persistence.database.DatabaseManager",
            return_value=mock_db_manager,
        ), patch(
            "src.infrastructure.persistence.repositories.SqlMonitoringRepository",
            return_value=mock_monitoring_repo,
        ), patch(
            "src.infrastructure.persistence.repositories.SqlSnapshotRepository",
        ):
            service._run_worker(1, worker)

        registry.remove_runtime.assert_called_once_with(1)
        registry.remove.assert_not_called()


# ---------------------------------------------------------------------------
# I. Metrics computation (unique_tomatoes, percentages, division by zero)
# ---------------------------------------------------------------------------


class TestMetricsComputation:
    """_build_metrics_from_analysis_result correctness."""

    def test_unique_tomatoes_feeds_total_detections_and_total_tomatoes(self):
        service = MonitoringService(
            monitoring_repo=MagicMock(),
            snapshot_repo=MagicMock(),
            inspection_result_repo=MagicMock(),
            metrics_repo=MagicMock(),
            module_repo=MagicMock(),
        )

        result = MagicMock()
        result.unique_tomatoes = 15
        result.healthy_count = 10
        result.unhealthy_count = 5
        result.maturity_counts = {"green": 3, "breaker": 2, "turning": 4, "pink": 2, "light_red": 2, "red": 2}
        result.snapshots_with_detections = 8

        metrics = service._build_metrics_from_analysis_result(42, result)

        assert metrics.total_tomatoes == 15
        assert metrics.monitoring_id == 42

    def test_health_percentages_correct(self):
        service = MonitoringService(
            monitoring_repo=MagicMock(),
            snapshot_repo=MagicMock(),
            inspection_result_repo=MagicMock(),
            metrics_repo=MagicMock(),
            module_repo=MagicMock(),
        )

        result = MagicMock()
        result.unique_tomatoes = 10
        result.healthy_count = 7
        result.unhealthy_count = 3
        result.maturity_counts = {"green": 5, "red": 5}
        result.snapshots_with_detections = 5

        metrics = service._build_metrics_from_analysis_result(1, result)

        assert metrics.pct_healthy == 70.0
        assert metrics.pct_unhealthy == 30.0

    def test_maturity_percentages_correct(self):
        service = MonitoringService(
            monitoring_repo=MagicMock(),
            snapshot_repo=MagicMock(),
            inspection_result_repo=MagicMock(),
            metrics_repo=MagicMock(),
            module_repo=MagicMock(),
        )

        result = MagicMock()
        result.unique_tomatoes = 10
        result.healthy_count = 10
        result.unhealthy_count = 0
        result.maturity_counts = {"green": 2, "breaker": 2, "turning": 2, "pink": 2, "light_red": 1, "red": 1}
        result.snapshots_with_detections = 5

        metrics = service._build_metrics_from_analysis_result(1, result)

        assert metrics.pct_green == 20.0
        assert metrics.pct_breaker == 20.0
        assert metrics.pct_turning == 20.0
        assert metrics.pct_pink == 20.0
        assert metrics.pct_light_red == 10.0
        assert metrics.pct_red == 10.0

    def test_division_by_zero_returns_zero(self):
        service = MonitoringService(
            monitoring_repo=MagicMock(),
            snapshot_repo=MagicMock(),
            inspection_result_repo=MagicMock(),
            metrics_repo=MagicMock(),
            module_repo=MagicMock(),
        )

        result = MagicMock()
        result.unique_tomatoes = 0
        result.healthy_count = 0
        result.unhealthy_count = 0
        result.maturity_counts = {}
        result.snapshots_with_detections = 0

        metrics = service._build_metrics_from_analysis_result(1, result)

        assert metrics.pct_healthy == 0.0
        assert metrics.pct_unhealthy == 0.0
        assert metrics.pct_green == 0.0
        assert metrics.pct_red == 0.0

    def test_total_detection_rows_not_used_as_tomato_count(self):
        """total_detection_rows is NOT the same as unique_tomatoes."""
        service = MonitoringService(
            monitoring_repo=MagicMock(),
            snapshot_repo=MagicMock(),
            inspection_result_repo=MagicMock(),
            metrics_repo=MagicMock(),
            module_repo=MagicMock(),
        )

        result = MagicMock()
        result.unique_tomatoes = 5  # This is the correct count
        result.total_detection_rows = 50  # This is raw detection count
        result.healthy_count = 3
        result.unhealthy_count = 2
        result.maturity_counts = {}
        result.snapshots_with_detections = 4

        metrics = service._build_metrics_from_analysis_result(1, result)

        # total_tomatoes must use unique_tomatoes, not total_detection_rows
        assert metrics.total_tomatoes == 5


# ---------------------------------------------------------------------------
# Original finalize_capture event tests (using FakeCaptureRuntime)
# ---------------------------------------------------------------------------


class TestFinalizeCaptureSetsFinalizeEvent:
    """finalize_event must be set; abort_event must NOT be set."""

    def test_finalize_capture_sets_finalize_event(self):
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, _) = _build_service_and_worker()
        snapshot_repo.get_by_monitoring.return_value = [MagicMock()]

        assert not worker.finalize_event.is_set()
        assert not worker.abort_event.is_set()

        with patch(
            "src.application.services.monitoring_service.threading.Thread",
            return_value=MagicMock(),
        ), patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter"
        ):
            service.finalize_capture(1)

        assert worker.finalize_event.is_set()
        assert not worker.abort_event.is_set()


class TestFinalizeCaptureClsPauseEvents:
    """pause_event and thermal_pause_event must be cleared."""

    def test_finalize_capture_clears_pause_events(self):
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, _) = _build_service_and_worker()
        snapshot_repo.get_by_monitoring.return_value = []

        worker.pause_event.set()
        worker.thermal_pause_event.set()

        with patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter"
        ):
            service.finalize_capture(1)

        assert not worker.pause_event.is_set()
        assert not worker.thermal_pause_event.is_set()


class TestFinalizeWithSnapshotsTransitionsToAnalyzing:
    """When snapshots exist, status goes to analyzing."""

    def test_finalize_capture_with_snapshots_transitions_to_analyzing(self):
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, _) = _build_service_and_worker()
        snapshot_repo.get_by_monitoring.return_value = [MagicMock(), MagicMock()]

        analyzing_monitoring = MagicMock()
        analyzing_monitoring.status = "analyzing"
        monitoring_repo.update_status.return_value = analyzing_monitoring

        with patch(
            "src.application.services.monitoring_service.threading.Thread",
            return_value=MagicMock(),
        ), patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter"
        ):
            result = service.finalize_capture(1)

        monitoring_repo.update_status.assert_any_call(1, "analyzing")
        assert result.status == "analyzing"


class TestFinalizeWorkerAbsentTransitionsToError:
    """No worker in registry → error status."""

    def test_finalize_capture_worker_absent_transitions_to_error(self):
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, _) = _build_service_and_worker()
        registry.get_worker.return_value = None

        error_monitoring = MagicMock()
        error_monitoring.status = "error"
        monitoring_repo.update_status.return_value = error_monitoring

        result = service.finalize_capture(1)
        monitoring_repo.update_status.assert_any_call(1, "error")
        assert result.status == "error"

    def test_finalize_capture_thread_absent_transitions_to_error(self):
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, _) = _build_service_and_worker()
        registry.get_thread.return_value = None

        error_monitoring = MagicMock()
        error_monitoring.status = "error"
        monitoring_repo.update_status.return_value = error_monitoring

        result = service.finalize_capture(1)
        monitoring_repo.update_status.assert_any_call(1, "error")
        assert result.status == "error"


class TestFinalizeThreadTimeoutTransitionsToError:
    """Thread still alive after join(timeout=10.0) → error."""

    def test_finalize_capture_thread_timeout_transitions_to_error(self):
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, mock_thread) = _build_service_and_worker()
        mock_thread.is_alive.return_value = True
        mock_thread.join = MagicMock()

        error_monitoring = MagicMock()
        error_monitoring.status = "error"
        monitoring_repo.update_status.return_value = error_monitoring

        result = service.finalize_capture(1)
        monitoring_repo.update_status.assert_any_call(1, "error")
        assert result.status == "error"


class TestFinalizeWorkerErrorReasonTransitionsToError:
    """Worker with error_reason set → error status."""

    def test_finalize_capture_worker_error_reason_transitions_to_error(self):
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, _) = _build_service_and_worker()
        worker._error_reason = "Frame source no disponible"

        error_monitoring = MagicMock()
        error_monitoring.status = "error"
        monitoring_repo.update_status.return_value = error_monitoring

        result = service.finalize_capture(1)
        monitoring_repo.update_status.assert_any_call(1, "error")
        assert result.status == "error"


class TestFinalizeConcurrentRaisesError:
    """Second concurrent finalize_capture raises FinalizationInProgressError."""

    def test_finalize_capture_concurrent_raises_finalization_error(self):
        registry = MagicMock()
        registry.claim_finalization.return_value = False

        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, _, _) = _build_service_and_worker(registry=registry)

        with pytest.raises(FinalizationInProgressError):
            service.finalize_capture(1)


class TestFinalizeNeverMarksAborted:
    """finalize_capture never transitions to aborted status."""

    def test_finalize_capture_never_marks_aborted(self):
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, _) = _build_service_and_worker()
        snapshot_repo.get_by_monitoring.return_value = []

        with patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter"
        ):
            service.finalize_capture(1)

        for call_args in monitoring_repo.update_status.call_args_list:
            args, kwargs = call_args
            status_arg = args[1] if len(args) > 1 else kwargs.get("status")
            assert status_arg != "aborted"

    def test_finalize_capture_error_paths_never_mark_aborted(self):
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, mock_thread) = _build_service_and_worker()
        mock_thread.is_alive.return_value = True

        service.finalize_capture(1)

        for call_args in monitoring_repo.update_status.call_args_list:
            args, kwargs = call_args
            status_arg = args[1] if len(args) > 1 else kwargs.get("status")
            assert status_arg != "aborted"


class TestFinalizeClaimReleasedOnSuccess:
    """release_finalization is called on successful paths."""

    def test_finalize_capture_claim_released_zero_snapshots(self):
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, _) = _build_service_and_worker()
        snapshot_repo.get_by_monitoring.return_value = []

        with patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter"
        ):
            service.finalize_capture(1)

        registry.release_finalization.assert_called_with(1)

    def test_finalize_capture_claim_released_with_snapshots(self):
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, _) = _build_service_and_worker()
        snapshot_repo.get_by_monitoring.return_value = [MagicMock()]

        with patch(
            "src.application.services.monitoring_service.threading.Thread",
            return_value=MagicMock(),
        ), patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter"
        ):
            service.finalize_capture(1)

        registry.release_finalization.assert_called_with(1)


class TestFinalizeJoinCalledWithTimeout:
    """thread.join(timeout=10.0) is verified."""

    def test_finalize_capture_join_called_with_timeout_10(self):
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, mock_thread) = _build_service_and_worker()
        snapshot_repo.get_by_monitoring.return_value = []

        with patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter"
        ):
            service.finalize_capture(1)

        mock_thread.join.assert_called_once_with(timeout=10.0)


# ---------------------------------------------------------------------------
# Finalize_capture propagation hardening tests
# ---------------------------------------------------------------------------


class TestFinalizeCaptureNoPropagation:
    """finalize_capture never propagates internal failures after claim."""

    def _setup_with_session(self):
        """Build service with a shared mock session on repos for rollback verification."""
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, _) = _build_service_and_worker()
        mock_session = MagicMock()
        monitoring_repo._session = mock_session
        snapshot_repo._session = mock_session
        metrics_repo._session = mock_session
        return service, worker, monitoring_repo, snapshot_repo, metrics_repo, registry, mock_session

    def test_snapshot_repo_raises_does_not_propagate(self):
        """If snapshot_repo.get_by_monitoring raises, finalize returns error."""
        (service, worker, monitoring_repo, snapshot_repo,
         metrics_repo, registry, mock_session) = self._setup_with_session()
        snapshot_repo.get_by_monitoring.side_effect = RuntimeError("DB timeout")

        error_mon = MagicMock(status="error")
        running_mon = MagicMock(status="running", id=1)
        monitoring_repo.get_by_id.side_effect = [running_mon, error_mon]

        with patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ):
            result = service.finalize_capture(1)

        assert result.status == "error"
        mock_session.rollback.assert_called()
        registry.release_finalization.assert_called_with(1)
        for c in monitoring_repo.update_status.call_args_list:
            assert c[0][1] != "aborted"

    def test_update_status_analyzing_raises_does_not_propagate(self):
        """If update_status(analyzing) raises, finalize returns error."""
        (service, worker, monitoring_repo, snapshot_repo,
         metrics_repo, registry, mock_session) = self._setup_with_session()
        snapshot_repo.get_by_monitoring.return_value = [MagicMock()]

        error_mon = MagicMock(status="error")

        def side_effect(mid, status):
            if status == "analyzing":
                raise RuntimeError("DB connection lost")
            return error_mon

        monitoring_repo.update_status.side_effect = side_effect
        running_mon = MagicMock(status="running", id=1)
        monitoring_repo.get_by_id.side_effect = [running_mon, error_mon]

        with patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter"
        ):
            result = service.finalize_capture(1)

        assert result.status == "error"
        mock_session.rollback.assert_called()
        registry.release_finalization.assert_called_with(1)
        # completed never attempted
        status_calls = [c[0][1] for c in monitoring_repo.update_status.call_args_list
                        if not isinstance(c[0][1], Exception)]
        assert "completed" not in status_calls

    def test_registry_register_raises_does_not_propagate(self):
        """If registry.register raises, finalize returns error."""
        (service, worker, monitoring_repo, snapshot_repo,
         metrics_repo, registry, mock_session) = self._setup_with_session()
        snapshot_repo.get_by_monitoring.return_value = [MagicMock()]
        registry.register.side_effect = RuntimeError("Registry full")

        error_mon = MagicMock(status="error")
        running_mon = MagicMock(status="running", id=1)
        monitoring_repo.get_by_id.side_effect = [running_mon, error_mon]

        with patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter"
        ):
            result = service.finalize_capture(1)

        assert result.status == "error"
        mock_session.rollback.assert_called()
        registry.release_finalization.assert_called_with(1)

    def test_thread_start_raises_does_not_propagate(self):
        """If analysis_thread.start() raises, finalize returns error."""
        (service, worker, monitoring_repo, snapshot_repo,
         metrics_repo, registry, mock_session) = self._setup_with_session()
        snapshot_repo.get_by_monitoring.return_value = [MagicMock()]

        mock_thread = MagicMock()
        mock_thread.start.side_effect = RuntimeError("Cannot start thread")

        error_mon = MagicMock(status="error")
        running_mon = MagicMock(status="running", id=1)
        monitoring_repo.get_by_id.side_effect = [running_mon, error_mon]

        with patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter"
        ), patch(
            "src.application.services.monitoring_service.threading.Thread",
            return_value=mock_thread,
        ):
            result = service.finalize_capture(1)

        assert result.status == "error"
        mock_session.rollback.assert_called()
        registry.release_finalization.assert_called_with(1)
        for c in monitoring_repo.update_status.call_args_list:
            assert c[0][1] != "aborted"


# ---------------------------------------------------------------------------
# Zero snapshots: create_pending_for_finalization failure
# ---------------------------------------------------------------------------


class TestZeroSnapshotsPendingFailure:
    """create_pending_for_finalization failure in zero-snapshot path."""

    def test_create_pending_raises_triggers_error_flow(self):
        """If create_pending raises, rollback attempted + error transition."""
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, _) = _build_service_and_worker()
        snapshot_repo.get_by_monitoring.return_value = []
        metrics_repo.create_pending_for_finalization.side_effect = RuntimeError(
            "Integrity error"
        )

        mock_session = MagicMock()
        monitoring_repo._session = mock_session
        snapshot_repo._session = mock_session
        metrics_repo._session = mock_session

        error_mon = MagicMock(status="error")
        running_mon = MagicMock(status="running", id=1)
        monitoring_repo.get_by_id.side_effect = [running_mon, error_mon]

        with patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter"
        ):
            result = service.finalize_capture(1)

        assert result.status == "error"
        # rollback attempted
        mock_session.rollback.assert_called()
        # completed never attempted after create_pending failure
        status_calls = [c[0][1] for c in monitoring_repo.update_status.call_args_list]
        assert "completed" not in status_calls
        # error attempted
        assert "error" in status_calls
        # registry cleaned
        registry.remove.assert_called_with(1)
        # claim released
        registry.release_finalization.assert_called_with(1)
        # registry cleaned
        registry.remove.assert_called_with(1)
        # claim released
        registry.release_finalization.assert_called_with(1)

    def test_create_pending_raises_no_analysis_thread(self):
        """If create_pending raises in zero-snapshot path, no analysis thread started."""
        (service, worker, monitoring_repo, snapshot_repo,
         _, metrics_repo, registry, _) = _build_service_and_worker()
        snapshot_repo.get_by_monitoring.return_value = []
        metrics_repo.create_pending_for_finalization.side_effect = RuntimeError("fail")

        error_mon = MagicMock(status="error")
        running_mon = MagicMock(status="running", id=1)
        monitoring_repo.get_by_id.side_effect = [running_mon, error_mon]

        with patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            return_value=False,
        ), patch(
            "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter"
        ), patch(
            "src.application.services.monitoring_service.threading.Thread"
        ) as thread_cls:
            service.finalize_capture(1)

        thread_cls.assert_not_called()
