"""Tests for _reconcile_orphaned_sessions in MonitoringService.

Validates orphan detection for all active statuses including 'analyzing'.
Uses mocks — no DB, no camera, no real threads.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.application.services.monitoring_service import MonitoringService, _ACTIVE_STATUSES
from src.domain.value_objects.monitoring_status import MonitoringState, MonitoringStatus


def _build_service(registry=None):
    monitoring_repo = MagicMock()
    if registry is None:
        registry = MagicMock()
    service = MonitoringService(
        monitoring_repo=monitoring_repo,
        snapshot_repo=MagicMock(),
        inspection_result_repo=MagicMock(),
        metrics_repo=MagicMock(),
        module_repo=MagicMock(),
        runtime_registry=registry,
    )
    return service, monitoring_repo, registry


class TestOrphanAnalyzingNoThread:
    """analyzing without live thread → error + registry.remove."""

    def test_analyzing_no_thread_marked_error(self):
        service, monitoring_repo, registry = _build_service()
        mon = MagicMock(id=1, status="analyzing")
        monitoring_repo.get_by_module.return_value = [mon]
        registry.get_thread.return_value = None

        service._reconcile_orphaned_sessions(module_id=10)

        monitoring_repo.update_status.assert_called_once_with(1, "error")
        registry.remove.assert_called_once_with(1)

    def test_analyzing_dead_thread_marked_error(self):
        service, monitoring_repo, registry = _build_service()
        mon = MagicMock(id=2, status="analyzing")
        monitoring_repo.get_by_module.return_value = [mon]
        dead_thread = MagicMock()
        dead_thread.is_alive.return_value = False
        registry.get_thread.return_value = dead_thread

        service._reconcile_orphaned_sessions(module_id=10)

        monitoring_repo.update_status.assert_called_once_with(2, "error")
        registry.remove.assert_called_once_with(2)


class TestAnalyzingWithLiveThread:
    """analyzing with live thread → no changes."""

    def test_analyzing_live_thread_not_touched(self):
        service, monitoring_repo, registry = _build_service()
        mon = MagicMock(id=3, status="analyzing")
        monitoring_repo.get_by_module.return_value = [mon]
        live_thread = MagicMock()
        live_thread.is_alive.return_value = True
        registry.get_thread.return_value = live_thread

        service._reconcile_orphaned_sessions(module_id=10)

        monitoring_repo.update_status.assert_not_called()
        registry.remove.assert_not_called()


class TestRunningOrphan:
    """running without live thread → error (existing behavior preserved)."""

    def test_running_no_thread_marked_error(self):
        service, monitoring_repo, registry = _build_service()
        mon = MagicMock(id=4, status="running")
        monitoring_repo.get_by_module.return_value = [mon]
        registry.get_thread.return_value = None

        service._reconcile_orphaned_sessions(module_id=10)

        monitoring_repo.update_status.assert_called_once_with(4, "error")
        registry.remove.assert_called_once_with(4)


class TestTerminalStatesUntouched:
    """completed, aborted, error → not modified."""

    @pytest.mark.parametrize("status", ["completed", "aborted", "error"])
    def test_terminal_not_modified(self, status):
        service, monitoring_repo, registry = _build_service()
        mon = MagicMock(id=5, status=status)
        monitoring_repo.get_by_module.return_value = [mon]

        service._reconcile_orphaned_sessions(module_id=10)

        monitoring_repo.update_status.assert_not_called()
        registry.remove.assert_not_called()


class TestUpdateStatusFailure:
    """update_status failure → no propagation, registry.remove still called."""

    def test_update_status_fails_no_propagation(self):
        service, monitoring_repo, registry = _build_service()
        mon = MagicMock(id=6, status="analyzing")
        monitoring_repo.get_by_module.return_value = [mon]
        registry.get_thread.return_value = None
        monitoring_repo.update_status.side_effect = RuntimeError("DB locked")

        # Must not raise
        service._reconcile_orphaned_sessions(module_id=10)

        registry.remove.assert_called_once_with(6)


class TestNeverUsesAborted:
    """Reconciliation never transitions to aborted."""

    def test_never_aborted(self):
        service, monitoring_repo, registry = _build_service()
        mon = MagicMock(id=7, status="running")
        monitoring_repo.get_by_module.return_value = [mon]
        registry.get_thread.return_value = None

        service._reconcile_orphaned_sessions(module_id=10)

        for call in monitoring_repo.update_status.call_args_list:
            assert call[0][1] != "aborted"


class TestActiveStatusesContainsAnalyzing:
    """_ACTIVE_STATUSES must include 'analyzing'."""

    def test_analyzing_in_active(self):
        assert "analyzing" in _ACTIVE_STATUSES


class TestStateMachineAnalyzing:
    """State machine allows analyzing → error and analyzing → completed."""

    def test_analyzing_to_error_allowed(self):
        status = MonitoringStatus(MonitoringState.ANALYZING)
        result = status.transition_to(MonitoringState.ERROR)
        assert result._state == MonitoringState.ERROR

    def test_analyzing_to_completed_allowed(self):
        status = MonitoringStatus(MonitoringState.ANALYZING)
        result = status.transition_to(MonitoringState.COMPLETED)
        assert result._state == MonitoringState.COMPLETED

    def test_analyzing_to_aborted_not_allowed(self):
        from src.domain.exceptions import InvalidTransitionError
        status = MonitoringStatus(MonitoringState.ANALYZING)
        with pytest.raises(InvalidTransitionError):
            status.transition_to(MonitoringState.ABORTED)
