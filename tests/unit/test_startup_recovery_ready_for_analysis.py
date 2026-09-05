"""Spec 020, Task 7.2 — startup recovery exception for ready_for_analysis.

A monitoring parked in ready_for_analysis legitimately has NO live runtime
thread after a reboot (capture done, video validated, camera released, analysis
not yet started). It must survive reconciliation:
    - reconcile_orphaned_sessions_on_startup() must NOT transition it to error;
    - _reconcile_orphaned_sessions(module) must NOT transition it to error;
    - analyzing WITHOUT a runtime still -> error (unchanged);
    - other active orphans (initializing/running/paused/finishing) -> error;
    - video_path is never cleared.

Uses mocks — no DB, no camera, no real threads.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.application.services.monitoring_service import MonitoringService
from src.domain.value_objects.monitoring_status import MonitoringState


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


READY = MonitoringState.READY_FOR_ANALYSIS.value


class TestStartupReconciliationExemptsReadyForAnalysis:
    def test_ready_for_analysis_not_marked_error_on_startup(self):
        service, repo, registry = _build_service()
        mon = MagicMock(id=1, status=READY)
        repo.get_active.return_value = [mon]
        registry.get_thread.return_value = None  # empty registry after reboot

        service.reconcile_orphaned_sessions_on_startup()

        # Must NOT transition to error (or anything).
        repo.update_status.assert_not_called()
        registry.remove.assert_not_called()

    def test_ready_for_analysis_video_path_preserved_on_startup(self):
        service, repo, registry = _build_service()
        mon = MagicMock(id=1, status=READY)
        repo.get_active.return_value = [mon]
        registry.get_thread.return_value = None

        service.reconcile_orphaned_sessions_on_startup()

        repo.update_video_path.assert_not_called()

    def test_analyzing_still_marked_error_on_startup(self):
        service, repo, registry = _build_service()
        mon = MagicMock(id=2, status=MonitoringState.ANALYZING.value)
        repo.get_active.return_value = [mon]
        registry.get_thread.return_value = None

        service.reconcile_orphaned_sessions_on_startup()

        repo.update_status.assert_called_once_with(2, "error")

    def test_mixed_batch_only_ready_for_analysis_survives(self):
        service, repo, registry = _build_service()
        ready = MagicMock(id=1, status=READY)
        analyzing = MagicMock(id=2, status=MonitoringState.ANALYZING.value)
        running = MagicMock(id=3, status=MonitoringState.RUNNING.value)
        repo.get_active.return_value = [ready, analyzing, running]
        registry.get_thread.return_value = None

        service.reconcile_orphaned_sessions_on_startup()

        changed = {c[0][0] for c in repo.update_status.call_args_list}
        assert 1 not in changed          # ready_for_analysis preserved
        assert changed == {2, 3}         # only analyzing + running -> error
        for c in repo.update_status.call_args_list:
            assert c[0][1] == "error"


class TestPerModuleReconciliationExemptsReadyForAnalysis:
    def test_ready_for_analysis_survives_per_module_reconcile(self):
        service, repo, registry = _build_service()
        ready = MagicMock(id=1, status=READY)
        repo.get_by_module.return_value = [ready]
        registry.get_thread.return_value = None

        service._reconcile_orphaned_sessions(module_id=10)

        repo.update_status.assert_not_called()
        registry.remove.assert_not_called()

    def test_analyzing_orphan_marked_error_per_module(self):
        service, repo, registry = _build_service()
        analyzing = MagicMock(id=2, status=MonitoringState.ANALYZING.value)
        repo.get_by_module.return_value = [analyzing]
        registry.get_thread.return_value = None

        service._reconcile_orphaned_sessions(module_id=10)

        repo.update_status.assert_called_once_with(2, "error")
