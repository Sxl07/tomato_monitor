"""Unit tests for MonitoringService C1 integration — CaptureWorker connected.

Validates that:
- start_session() no longer requires inference_runner
- start_session() instantiates CaptureWorker, not MonitoringWorker
- _run_worker() transitions initializing → running and executes CaptureWorker.run()
- abort_session() works with CaptureWorker.abort_event
- abort_session() releases camera by waiting for worker thread
- monitoring_start() route no longer loads inference models
- total_detections stays 0 during capture

Spec 009, Phase C1.
"""

from __future__ import annotations

import ast
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Test: start_session signature no longer requires inference_runner
# ---------------------------------------------------------------------------


class TestStartSessionSignature:
    """Verify start_session() no longer takes inference_runner."""

    def test_no_inference_runner_parameter(self):
        """start_session() must not have inference_runner in its signature."""
        from src.application.services.monitoring_service import MonitoringService
        import inspect

        sig = inspect.signature(MonitoringService.start_session)
        param_names = list(sig.parameters.keys())

        assert "inference_runner" not in param_names, (
            "start_session() still has inference_runner parameter"
        )

    def test_no_snapshot_inference_runner_import(self):
        """monitoring_service.py must not import SnapshotInferenceRunner."""
        source_path = Path("src/application/services/monitoring_service.py")
        source_code = source_path.read_text(encoding="utf-8")

        assert "SnapshotInferenceRunner" not in source_code, (
            "monitoring_service.py still imports SnapshotInferenceRunner"
        )

    def test_no_monitoring_worker_import(self):
        """monitoring_service.py must import CaptureWorker, not MonitoringWorker."""
        source_path = Path("src/application/services/monitoring_service.py")
        source_code = source_path.read_text(encoding="utf-8")

        # Should import CaptureWorker
        assert "from src.application.services.capture_worker import CaptureWorker" in source_code

        # Should NOT import MonitoringWorker at module level
        tree = ast.parse(source_code)
        top_level_imports = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "monitoring_worker" in node.module:
                    top_level_imports.append(node.module)

        assert len(top_level_imports) == 0, (
            f"monitoring_service.py still imports from monitoring_worker at top level: {top_level_imports}"
        )


# ---------------------------------------------------------------------------
# Test: start_session creates CaptureWorker
# ---------------------------------------------------------------------------


class TestStartSessionCreatesCaptureWorker:
    """Verify start_session creates a CaptureWorker instance."""

    def test_start_session_creates_capture_worker(self):
        """start_session() must create a CaptureWorker and register it."""
        from src.application.services.monitoring_service import MonitoringService

        # Build mocks
        monitoring_repo = MagicMock()
        snapshot_repo = MagicMock()
        inspection_result_repo = MagicMock()
        metrics_repo = MagicMock()
        module_repo = MagicMock()
        registry = MagicMock()
        frame_source = MagicMock()
        db_session = MagicMock()

        # Setup module exists
        module_repo.get_by_id.return_value = MagicMock(id=1)
        # No existing active sessions
        monitoring_repo.get_by_module.return_value = []
        # Create returns a monitoring with id
        mock_monitoring = MagicMock()
        mock_monitoring.id = 99
        monitoring_repo.create.return_value = mock_monitoring
        # Registry
        registry.has_live_worker_for_module.return_value = False
        registry.cleanup_dead = MagicMock()

        service = MonitoringService(
            monitoring_repo=monitoring_repo,
            snapshot_repo=snapshot_repo,
            inspection_result_repo=inspection_result_repo,
            metrics_repo=metrics_repo,
            module_repo=module_repo,
            runtime_registry=registry,
        )

        # Patch the lazy imports at their source modules
        mock_profile = MagicMock()
        mock_profile.capture_loop_fps = 5.0
        mock_profile.min_seconds_between_snapshots = 1.0
        mock_profile.max_seconds_without_snapshot = 3.0
        mock_profile.gate_resolution = (240, 240)
        mock_profile.scene_gate_orb_threshold = 35
        mock_profile.scene_gate_hsv_threshold = 0.38
        mock_profile.thermal_poll_interval_seconds = 5.0
        mock_profile.thermal_warning_temp = 72.0
        mock_profile.thermal_critical_temp = 78.0
        mock_profile.thermal_resume_temp = 65.0

        with patch.dict(
            "sys.modules",
            {
                "src.infrastructure.camera.raspberry_camera_frame_source": MagicMock(
                    is_camera_locked=MagicMock(return_value=False)
                ),
            },
        ):
            result = service.start_session(
                module_id=1,
                width_m=5.0,
                length_m=2.0,
                notes=None,
                frame_source=frame_source,
                db_session=db_session,
            )

        # Verify registry.register was called with a CaptureWorker
        assert registry.register.called
        call_args = registry.register.call_args
        registered_worker = call_args[0][1]  # positional arg: (monitoring_id, worker, thread)

        from src.application.services.capture_worker import CaptureWorker
        assert isinstance(registered_worker, CaptureWorker), (
            f"Expected CaptureWorker but got {type(registered_worker).__name__}"
        )

        # Verify registry.register was called with a CaptureWorker
        assert registry.register.called
        call_args = registry.register.call_args
        registered_worker = call_args[0][1]  # positional arg: (monitoring_id, worker, thread)

        from src.application.services.capture_worker import CaptureWorker
        assert isinstance(registered_worker, CaptureWorker), (
            f"Expected CaptureWorker but got {type(registered_worker).__name__}"
        )


# ---------------------------------------------------------------------------
# Test: abort_session works with CaptureWorker
# ---------------------------------------------------------------------------


class TestAbortWithCaptureWorker:
    """Verify abort_session correctly signals CaptureWorker.abort_event."""

    def test_abort_sets_abort_event(self):
        """abort_session() must set worker.abort_event."""
        from src.application.services.monitoring_service import MonitoringService
        from src.application.services.capture_worker import CaptureWorker

        monitoring_repo = MagicMock()
        snapshot_repo = MagicMock()
        inspection_result_repo = MagicMock()
        metrics_repo = MagicMock()
        module_repo = MagicMock()
        registry = MagicMock()

        # Mock monitoring in running state
        mock_monitoring = MagicMock()
        mock_monitoring.status = "running"
        monitoring_repo.get_by_id.return_value = mock_monitoring
        monitoring_repo.update_status.return_value = mock_monitoring

        # Create a real CaptureWorker to check abort_event
        worker = CaptureWorker(
            monitoring_id=1,
            frame_source=MagicMock(),
            snapshot_repo=MagicMock(),
            monitoring_repo=MagicMock(),
            db_session=MagicMock(),
        )

        # Register it
        registry.get_worker.return_value = worker
        # Thread is not alive (already stopped)
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False
        registry.get_thread.return_value = mock_thread

        service = MonitoringService(
            monitoring_repo=monitoring_repo,
            snapshot_repo=snapshot_repo,
            inspection_result_repo=inspection_result_repo,
            metrics_repo=metrics_repo,
            module_repo=module_repo,
            runtime_registry=registry,
        )

        service.abort_session(1)

        assert worker.abort_event.is_set(), (
            "abort_session() did not set CaptureWorker.abort_event"
        )


# ---------------------------------------------------------------------------
# Test: monitoring_start route no longer calls _build_inference_runner
# ---------------------------------------------------------------------------


class TestRouteNoInference:
    """Verify agricultural_ui monitoring_start doesn't load models."""

    def test_monitoring_start_does_not_call_build_inference_runner(self):
        """The monitoring_start route must NOT call _build_inference_runner."""
        source_path = Path("app/routes/agricultural_ui.py")
        source_code = source_path.read_text(encoding="utf-8")

        # Find the monitoring_start function
        tree = ast.parse(source_code)
        found_fn = False

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "monitoring_start":
                found_fn = True
                # Check that _build_inference_runner is NOT called inside this function
                fn_source = ast.get_source_segment(source_code, node)
                assert "_build_inference_runner()" not in fn_source, (
                    "monitoring_start() still calls _build_inference_runner()"
                )
                # Also check no inference_runner in start_session call
                assert "inference_runner=" not in fn_source, (
                    "monitoring_start() still passes inference_runner to start_session()"
                )
                break

        assert found_fn, "Could not find monitoring_start function"

    def test_no_detectron2_import_at_start(self):
        """monitoring_start must not import detectron2 or ResNet during start."""
        source_path = Path("app/routes/agricultural_ui.py")
        source_code = source_path.read_text(encoding="utf-8")

        # Find monitoring_start function body
        tree = ast.parse(source_code)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "monitoring_start":
                fn_source = ast.get_source_segment(source_code, node)
                # These imports should NOT be in the function body
                assert "build_tomato_detector" not in fn_source
                assert "build_health_model_resnet" not in fn_source
                assert "SnapshotInferenceRunner" not in fn_source
                break


# ---------------------------------------------------------------------------
# Test: total_detections stays 0
# ---------------------------------------------------------------------------


class TestDetectionsZero:
    """Verify total_detections is never incremented during capture."""

    def test_capture_worker_never_sets_detections(self):
        """CaptureWorker only updates total_snapshots, never total_detections > 0."""
        from src.application.services.capture_worker import CaptureWorker
        from tests.unit.test_capture_worker import (
            FakeFrameSource,
            FakeSnapshotRepo,
            FakeMonitoringRepo,
            FakeDbSession,
        )

        frame_source = FakeFrameSource(num_frames=10)
        snapshot_repo = FakeSnapshotRepo()
        monitoring_repo = FakeMonitoringRepo()
        db_session = FakeDbSession()

        worker = CaptureWorker(
            monitoring_id=1,
            frame_source=frame_source,
            snapshot_repo=snapshot_repo,
            monitoring_repo=monitoring_repo,
            db_session=db_session,
            capture_loop_fps=100.0,
            min_seconds_between_snapshots=0.0,
            max_seconds_without_snapshot=999.0,
            gate_resolution=(64, 64),
        )

        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(True, {}),
        ), patch("cv2.imwrite", return_value=True):
            def stop():
                time.sleep(0.05)
                worker.finalize_event.set()

            t = threading.Thread(target=stop)
            t.start()
            worker.run()
            t.join()

        # Every counter update must have total_detections=0
        for update in monitoring_repo.counter_updates:
            assert update["total_detections"] == 0, (
                f"total_detections was set to {update['total_detections']} during capture"
            )


# ---------------------------------------------------------------------------
# Test: analyzing is in _ACTIVE_STATUSES
# ---------------------------------------------------------------------------


class TestActiveStatuses:
    """Verify 'analyzing' is included in _ACTIVE_STATUSES."""

    def test_analyzing_in_active_statuses(self):
        """_ACTIVE_STATUSES must include 'analyzing' for orphan detection."""
        from src.application.services.monitoring_service import _ACTIVE_STATUSES

        assert "analyzing" in _ACTIVE_STATUSES


# ---------------------------------------------------------------------------
# Test: _run_worker early failures release resources
# ---------------------------------------------------------------------------


class TestRunWorkerEarlyFailures:
    """Verify _run_worker releases resources on early failures."""

    def test_db_session_creation_failure_releases_resources(self):
        """If DatabaseManager fails, worker.release_resources() is called."""
        from src.application.services.monitoring_service import MonitoringService
        from src.application.services.capture_worker import CaptureWorker

        worker = CaptureWorker(
            monitoring_id=1,
            frame_source=MagicMock(),
            snapshot_repo=MagicMock(),
            monitoring_repo=MagicMock(),
            db_session=MagicMock(),
        )
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
        registry.remove.assert_called_once_with(1)

    def test_transition_failure_releases_resources(self):
        """If initializing→running transition fails, release_resources called."""
        from src.application.services.monitoring_service import MonitoringService
        from src.application.services.capture_worker import CaptureWorker

        worker = CaptureWorker(
            monitoring_id=1,
            frame_source=MagicMock(),
            snapshot_repo=MagicMock(),
            monitoring_repo=MagicMock(),
            db_session=MagicMock(),
        )
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

        # Make DB session work but transition fail
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
            return_value=MagicMock(),
        ):
            service._run_worker(1, worker)

        worker.release_resources.assert_called_once()
        mock_session.close.assert_called_once()
        registry.remove.assert_called_once_with(1)
