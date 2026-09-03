"""Tests for reconcile_orphaned_sessions_on_startup in MonitoringService.

HOTFIX post-Spec019: a monitoring left in a non-terminal status (notably
'analyzing') after a process restart had no worker in the fresh in-memory
registry and could never progress — it stayed active forever, blocking the
module and leaving the UI polling 0/0. Startup reconciliation now transitions
such sessions to 'error' while PRESERVING the recorded video (reprocessable)
and WITHOUT auto-reprocessing.

Uses mocks — no DB, no camera, no real threads.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.application.services.monitoring_service import MonitoringService


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


class TestAnalyzingOrphanAtStartup:
    """analyzing session with empty registry → error, video preserved."""

    def test_analyzing_marked_error(self):
        service, repo, registry = _build_service()
        mon = MagicMock(id=1, status="analyzing")
        repo.get_active.return_value = [mon]
        registry.get_thread.return_value = None  # empty registry after restart

        service.reconcile_orphaned_sessions_on_startup()

        repo.update_status.assert_called_once_with(1, "error")
        registry.remove.assert_called_once_with(1)

    def test_video_path_never_cleared(self):
        """Reconciliation must NOT call update_video_path (video preserved)."""
        service, repo, registry = _build_service()
        mon = MagicMock(id=1, status="analyzing")
        repo.get_active.return_value = [mon]
        registry.get_thread.return_value = None

        service.reconcile_orphaned_sessions_on_startup()

        repo.update_video_path.assert_not_called()

    def test_never_auto_reprocesses(self):
        """Startup reconciliation must not launch any reprocess/analysis."""
        service, repo, registry = _build_service()
        mon = MagicMock(id=1, status="analyzing")
        repo.get_active.return_value = [mon]
        registry.get_thread.return_value = None

        # No thread should be registered/started by reconciliation.
        service.reconcile_orphaned_sessions_on_startup()

        registry.set_worker.assert_not_called()
        registry.set_thread.assert_not_called()


class TestMultipleActiveStatuses:
    """All active statuses left orphaned → error."""

    @pytest.mark.parametrize(
        "status", ["initializing", "running", "paused", "finishing", "analyzing"]
    )
    def test_active_status_marked_error(self, status):
        service, repo, registry = _build_service()
        mon = MagicMock(id=7, status=status)
        repo.get_active.return_value = [mon]
        registry.get_thread.return_value = None

        service.reconcile_orphaned_sessions_on_startup()

        repo.update_status.assert_called_once_with(7, "error")


class TestNeverUsesAborted:
    """Reconciliation must never transition to 'aborted'."""

    def test_never_aborted(self):
        service, repo, registry = _build_service()
        mon = MagicMock(id=2, status="analyzing")
        repo.get_active.return_value = [mon]
        registry.get_thread.return_value = None

        service.reconcile_orphaned_sessions_on_startup()

        for call in repo.update_status.call_args_list:
            assert call[0][1] != "aborted"


class TestLiveThreadNotTouched:
    """Defensive: a session with a live thread is left alone."""

    def test_live_thread_not_reconciled(self):
        service, repo, registry = _build_service()
        mon = MagicMock(id=3, status="analyzing")
        repo.get_active.return_value = [mon]
        live_thread = MagicMock()
        live_thread.is_alive.return_value = True
        registry.get_thread.return_value = live_thread

        service.reconcile_orphaned_sessions_on_startup()

        repo.update_status.assert_not_called()


class TestResilience:
    """Failures must not propagate or stop reconciliation of other sessions."""

    def test_get_active_failure_no_raise(self):
        service, repo, registry = _build_service()
        repo.get_active.side_effect = RuntimeError("DB down")

        # Must not raise.
        service.reconcile_orphaned_sessions_on_startup()

        repo.update_status.assert_not_called()

    def test_update_status_failure_continues(self):
        service, repo, registry = _build_service()
        mon_a = MagicMock(id=10, status="analyzing")
        mon_b = MagicMock(id=11, status="running")
        repo.get_active.return_value = [mon_a, mon_b]
        registry.get_thread.return_value = None
        repo.update_status.side_effect = [RuntimeError("locked"), None]

        # Must not raise; second session still processed.
        service.reconcile_orphaned_sessions_on_startup()

        assert repo.update_status.call_count == 2

    def test_no_active_sessions_noop(self):
        service, repo, registry = _build_service()
        repo.get_active.return_value = []

        service.reconcile_orphaned_sessions_on_startup()

        repo.update_status.assert_not_called()
        registry.remove.assert_not_called()
