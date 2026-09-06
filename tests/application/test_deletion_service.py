"""Tests for DeletionService — Spec 021, Task 4.5.

Directed unit tests (MANDATORY, Req 14.1 + 14.8) for the deletion service's
authorization, no-interference check and two-transaction delete flow. These are
pure unit tests with lightweight in-memory fakes — NO SQLAlchemy, NO network,
NO hardware.

Coverage:
    - Authorization by FSM state (Req 14.1, 1.2, 1.4): allowed for
      {ready_for_analysis, completed, aborted, error}; rejected for
      {initializing, running, paused, finishing, analyzing}; not-found for a
      missing monitoring id.
    - No-interference (Req 14.8, 3.1): a target with a live thread / registered
      worker / finalization claim / analysis claim is rejected (WORKER_ACTIVE)
      without mutating state; a DIFFERENT monitoring's worker does not reject the
      target; a missing registry is treated as no active worker.
    - Two-transaction flow (Task 4.4, Req 4.7): guard-reject creates no outbox
      entry and no cascade; happy path enqueues then cascades (deleted_at passed
      to TX2) with no network calls; TX1 failure aborts without cascade; TX2
      failure marks the entry failed, keeps the hierarchy intact and never
      propagates remotely; idempotent re-invoke reuses the same outbox entry.

Requirements: 14.1, 14.8, 1.2, 1.4, 3.1, 4.7.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pytest

from src.application.interfaces.deletion_outbox_port import (
    DeletionOutboxEntry,
    DeletionOutboxEntryInput,
)
from src.application.interfaces.local_cascade_port import LocalCascadeResult
from src.application.interfaces.sync_state_port import StoragePaths
from src.application.services.deletion_service import (
    AuthorizationOutcome,
    ContainerDescendantNotDeletableError,
    ContainerDescendantWorkerActiveError,
    DeletionOutboxRegistrationError,
    DeletionResult,
    DeletionService,
    DescendantValidationOutcome,
    LocalCascadeFailedError,
    MonitoringNotFoundError,
    MonitoringStateNotDeletableError,
    MonitoringWorkerActiveError,
)
from src.domain.value_objects.monitoring_status import MonitoringState


# ---------------------------------------------------------------------------
# Lightweight fakes (no SQLAlchemy, no network, no hardware)
# ---------------------------------------------------------------------------


class FakeMonitoring:
    """Minimal monitoring-like object exposing the attributes the service reads."""

    def __init__(self, id: int, status: str, module_id: int = 1) -> None:
        self.id = id
        self.status = status
        self.module_id = module_id


class FakeMonitoringRepository:
    """In-memory MonitoringRepository fake.

    Records mutating calls so tests can assert the guard never mutates state.
    """

    def __init__(self, monitorings: Optional[dict[int, FakeMonitoring]] = None) -> None:
        self._by_id: dict[int, FakeMonitoring] = monitorings or {}
        self._by_module: dict[int, list[FakeMonitoring]] = {}
        for mon in self._by_id.values():
            self._by_module.setdefault(mon.module_id, []).append(mon)
        self.mutations: list[tuple] = []

    def get_by_id(self, id: int) -> Optional[FakeMonitoring]:
        return self._by_id.get(id)

    def get_by_module(self, module_id: int) -> list[FakeMonitoring]:
        return list(self._by_module.get(module_id, []))

    # Any write would be a bug for the guard/reject paths — record it.
    def update_status(self, *args, **kwargs) -> None:  # pragma: no cover - guard
        self.mutations.append(("update_status", args, kwargs))

    def delete(self, *args, **kwargs) -> None:  # pragma: no cover - guard
        self.mutations.append(("delete", args, kwargs))


class FakeRuntimeRegistry:
    """Structural MonitoringRuntimeRegistry fake.

    Configurable per-monitoring signals for the no-interference check. Records
    every read so tests can confirm the service never mutates the registry.
    """

    def __init__(self) -> None:
        self._workers: dict[int, object] = {}
        self._threads: dict[int, object] = {}
        self._finalization: set[int] = set()
        self._analysis: set[int] = set()
        self.reads: list[tuple] = []

    def set_worker(self, mid: int, worker: object) -> None:
        self._workers[mid] = worker

    def set_thread(self, mid: int, thread: object) -> None:
        self._threads[mid] = thread

    def set_finalization(self, mid: int) -> None:
        self._finalization.add(mid)

    def set_analysis(self, mid: int) -> None:
        self._analysis.add(mid)

    def get_worker(self, monitoring_id: int) -> object | None:
        self.reads.append(("get_worker", monitoring_id))
        return self._workers.get(monitoring_id)

    def get_thread(self, monitoring_id: int):
        self.reads.append(("get_thread", monitoring_id))
        return self._threads.get(monitoring_id)

    def is_finalization_claimed(self, monitoring_id: int) -> bool:
        self.reads.append(("is_finalization_claimed", monitoring_id))
        return monitoring_id in self._finalization

    def is_analysis_claimed(self, monitoring_id: int) -> bool:
        self.reads.append(("is_analysis_claimed", monitoring_id))
        return monitoring_id in self._analysis


class FakeAliveThread:
    """Thread-like object exposing is_alive()."""

    def __init__(self, alive: bool = True) -> None:
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


class FakeSyncState:
    """Minimal SyncStatePort fake: remote id lookup + snapshot storage paths."""

    def __init__(
        self,
        remote_ids: Optional[dict[tuple[str, int], str]] = None,
        storage_paths: Optional[dict[int, StoragePaths]] = None,
    ) -> None:
        self._remote_ids = remote_ids or {}
        self._storage_paths = storage_paths or {}

    def get_remote_id(self, entity_type: str, local_id: int) -> Optional[str]:
        return self._remote_ids.get((entity_type, local_id))

    def get_storage_paths(self, snapshot_id: int) -> StoragePaths:
        return self._storage_paths.get(snapshot_id, StoragePaths())


class FakeSnapshotRepository:
    """Minimal SnapshotRepository fake: snapshots keyed by monitoring id."""

    def __init__(self, by_monitoring: Optional[dict[int, list]] = None) -> None:
        self._by_monitoring = by_monitoring or {}

    def get_by_monitoring(self, monitoring_id: int) -> list:
        return list(self._by_monitoring.get(monitoring_id, []))


class FakeModuleRepository:
    """Minimal ModuleRepository fake used only for greenhouse traversal."""

    def __init__(self, by_greenhouse: Optional[dict[int, list]] = None) -> None:
        self._by_greenhouse = by_greenhouse or {}

    def get_by_greenhouse(self, greenhouse_id: int) -> list:
        return list(self._by_greenhouse.get(greenhouse_id, []))


class RecordingDeletionOutbox:
    """DeletionOutboxPort fake that records enqueue/mark calls.

    Idempotent by (entity_type, entity_local_id): re-enqueuing returns the same
    persisted entry (mirrors the real contract). Tracks whether any network-like
    call was made (there are none — this port is local-only).
    """

    def __init__(self, enqueue_should_fail: bool = False) -> None:
        self._enqueue_should_fail = enqueue_should_fail
        self._entries: dict[tuple[str, int], DeletionOutboxEntry] = {}
        self._next_id = 1
        self.enqueue_calls: list[DeletionOutboxEntryInput] = []
        self.mark_failed_calls: list[int] = []
        self.mark_completed_calls: list[tuple[int, datetime]] = []

    def enqueue(self, entry: DeletionOutboxEntryInput) -> DeletionOutboxEntry:
        self.enqueue_calls.append(entry)
        if self._enqueue_should_fail:
            raise RuntimeError("simulated TX1 enqueue failure")
        key = (entry.entity_type, entry.entity_local_id)
        existing = self._entries.get(key)
        if existing is not None:
            return existing
        persisted = DeletionOutboxEntry(
            id=self._next_id,
            entity_type=entry.entity_type,
            entity_local_id=entry.entity_local_id,
            remote_table=entry.remote_table,
            remote_id=entry.remote_id,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            status="pending",
            local_delete_status="prepared",
            cleanup_status="pending",
            deleted_at=None,
            last_error=None,
            retry_count=0,
        )
        self._entries[key] = persisted
        self._next_id += 1
        return persisted

    def mark_local_completed(self, outbox_id: int, deleted_at: datetime) -> None:
        self.mark_completed_calls.append((outbox_id, deleted_at))

    def mark_local_failed(self, outbox_id: int) -> None:
        self.mark_failed_calls.append(outbox_id)
        for entry in self._entries.values():
            if entry.id == outbox_id:
                entry.local_delete_status = "failed"

    # The remaining port methods are unused by the delete flow under test.


class RecordingLocalCascade:
    """LocalCascadePort fake recording the cascade calls and the deleted_at.

    Configurable to succeed or fail; on success it marks the associated outbox
    entry completed (as the real TX2 does) so idempotency reuse can be observed.
    """

    def __init__(self, outbox: RecordingDeletionOutbox, succeed: bool = True) -> None:
        self._outbox = outbox
        self._succeed = succeed
        self.calls: list[dict] = []

    def execute_cascade(
        self,
        entity_type: str,
        entity_local_id: int,
        outbox_id: int,
        deleted_at: datetime,
    ) -> LocalCascadeResult:
        self.calls.append(
            {
                "entity_type": entity_type,
                "entity_local_id": entity_local_id,
                "outbox_id": outbox_id,
                "deleted_at": deleted_at,
            }
        )
        if not self._succeed:
            return LocalCascadeResult(
                success=False, error_message="simulated TX2 cascade failure"
            )
        # Mirror the real TX2: mark the entry completed within the cascade txn.
        self._outbox.mark_local_completed(outbox_id, deleted_at)
        return LocalCascadeResult(success=True)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _guard_service(
    monitorings: dict[int, FakeMonitoring],
    registry: Optional[FakeRuntimeRegistry] = None,
) -> tuple[DeletionService, FakeMonitoringRepository]:
    """Build a DeletionService with only the guard collaborators wired."""
    repo = FakeMonitoringRepository(monitorings)
    service = DeletionService(
        monitoring_repository=repo,
        runtime_registry=registry,
    )
    return service, repo


def _full_service(
    monitoring: FakeMonitoring,
    *,
    enqueue_should_fail: bool = False,
    cascade_succeeds: bool = True,
    registry: Optional[FakeRuntimeRegistry] = None,
    snapshots: Optional[dict[int, list]] = None,
    remote_ids: Optional[dict[tuple[str, int], str]] = None,
    storage_paths: Optional[dict[int, StoragePaths]] = None,
) -> tuple[DeletionService, FakeMonitoringRepository, RecordingDeletionOutbox, RecordingLocalCascade]:
    """Build a DeletionService with the full two-transaction collaborators."""
    repo = FakeMonitoringRepository({monitoring.id: monitoring})
    outbox = RecordingDeletionOutbox(enqueue_should_fail=enqueue_should_fail)
    cascade = RecordingLocalCascade(outbox, succeed=cascade_succeeds)
    service = DeletionService(
        monitoring_repository=repo,
        runtime_registry=registry,
        sync_state=FakeSyncState(remote_ids=remote_ids, storage_paths=storage_paths),
        snapshot_repository=FakeSnapshotRepository(snapshots),
        module_repository=FakeModuleRepository(),
        deletion_outbox=outbox,
        local_cascade=cascade,
    )
    return service, repo, outbox, cascade


# ===========================================================================
# Authorization by FSM state (Req 14.1, 1.2, 1.4)
# ===========================================================================

DELETABLE = [
    MonitoringState.READY_FOR_ANALYSIS,
    MonitoringState.COMPLETED,
    MonitoringState.ABORTED,
    MonitoringState.ERROR,
]

PROHIBITED = [
    MonitoringState.INITIALIZING,
    MonitoringState.RUNNING,
    MonitoringState.PAUSED,
    MonitoringState.FINISHING,
    MonitoringState.ANALYZING,
]


@pytest.mark.parametrize("state", DELETABLE)
def test_authorize_allows_deletable_states(state: MonitoringState) -> None:
    service, _ = _guard_service({7: FakeMonitoring(7, state.value)})

    result = service.authorize_monitoring_deletion(7)

    assert result.outcome is AuthorizationOutcome.ALLOWED
    assert result.allowed is True
    assert result.current_state is state


@pytest.mark.parametrize("state", PROHIBITED)
def test_authorize_rejects_prohibited_states(state: MonitoringState) -> None:
    service, repo = _guard_service({7: FakeMonitoring(7, state.value)})

    result = service.authorize_monitoring_deletion(7)

    assert result.outcome is AuthorizationOutcome.STATE_PROHIBITED
    assert result.allowed is False
    # The guard never mutates the monitoring.
    assert repo.mutations == []


def test_authorize_not_found_for_missing_id() -> None:
    service, _ = _guard_service({})

    result = service.authorize_monitoring_deletion(404)

    assert result.outcome is AuthorizationOutcome.NOT_FOUND
    assert result.allowed is False


def test_authorize_unknown_status_treated_as_prohibited() -> None:
    service, _ = _guard_service({7: FakeMonitoring(7, "bogus_state")})

    result = service.authorize_monitoring_deletion(7)

    assert result.outcome is AuthorizationOutcome.STATE_PROHIBITED


@pytest.mark.parametrize("state", PROHIBITED)
def test_or_raise_raises_state_not_deletable(state: MonitoringState) -> None:
    service, _ = _guard_service({7: FakeMonitoring(7, state.value)})

    with pytest.raises(MonitoringStateNotDeletableError) as exc:
        service.authorize_monitoring_deletion_or_raise(7)

    assert exc.value.current_state == state.value


def test_or_raise_raises_not_found() -> None:
    service, _ = _guard_service({})

    with pytest.raises(MonitoringNotFoundError):
        service.authorize_monitoring_deletion_or_raise(404)


@pytest.mark.parametrize("state", DELETABLE)
def test_or_raise_returns_state_when_allowed(state: MonitoringState) -> None:
    service, _ = _guard_service({7: FakeMonitoring(7, state.value)})

    assert service.authorize_monitoring_deletion_or_raise(7) is state


# ===========================================================================
# No-interference check (Req 14.8, 3.1, 3.2)
# ===========================================================================


def test_worker_active_via_live_thread_rejects_target() -> None:
    registry = FakeRuntimeRegistry()
    registry.set_thread(7, FakeAliveThread(alive=True))
    service, repo = _guard_service(
        {7: FakeMonitoring(7, MonitoringState.COMPLETED.value)}, registry
    )

    result = service.guard_monitoring_deletion(7)

    assert result.outcome is AuthorizationOutcome.WORKER_ACTIVE
    assert result.allowed is False
    assert repo.mutations == []  # no state/data mutation


def test_worker_active_via_registered_worker_rejects_target() -> None:
    registry = FakeRuntimeRegistry()
    registry.set_worker(7, object())
    service, _ = _guard_service(
        {7: FakeMonitoring(7, MonitoringState.COMPLETED.value)}, registry
    )

    assert service.has_active_worker(7) is True
    assert (
        service.guard_monitoring_deletion(7).outcome
        is AuthorizationOutcome.WORKER_ACTIVE
    )


def test_worker_active_via_finalization_claim_rejects_target() -> None:
    registry = FakeRuntimeRegistry()
    registry.set_finalization(7)
    service, _ = _guard_service(
        {7: FakeMonitoring(7, MonitoringState.ABORTED.value)}, registry
    )

    assert service.has_active_worker(7) is True


def test_worker_active_via_analysis_claim_rejects_target() -> None:
    registry = FakeRuntimeRegistry()
    registry.set_analysis(7)
    service, _ = _guard_service(
        {7: FakeMonitoring(7, MonitoringState.ERROR.value)}, registry
    )

    assert service.has_active_worker(7) is True


def test_different_monitoring_worker_does_not_reject_target() -> None:
    registry = FakeRuntimeRegistry()
    # Worker/thread/claims belong to a DIFFERENT monitoring (id 99).
    registry.set_worker(99, object())
    registry.set_thread(99, FakeAliveThread(alive=True))
    registry.set_finalization(99)
    registry.set_analysis(99)
    service, _ = _guard_service(
        {7: FakeMonitoring(7, MonitoringState.COMPLETED.value)}, registry
    )

    assert service.has_active_worker(7) is False
    result = service.guard_monitoring_deletion(7)
    assert result.outcome is AuthorizationOutcome.ALLOWED


def test_no_registry_treated_as_no_active_worker() -> None:
    service, _ = _guard_service(
        {7: FakeMonitoring(7, MonitoringState.COMPLETED.value)}, registry=None
    )

    assert service.has_active_worker(7) is False
    assert (
        service.guard_monitoring_deletion(7).outcome is AuthorizationOutcome.ALLOWED
    )


def test_dead_thread_not_treated_as_active() -> None:
    registry = FakeRuntimeRegistry()
    registry.set_thread(7, FakeAliveThread(alive=False))
    service, _ = _guard_service(
        {7: FakeMonitoring(7, MonitoringState.COMPLETED.value)}, registry
    )

    assert service.has_active_worker(7) is False


def test_or_raise_raises_worker_active() -> None:
    registry = FakeRuntimeRegistry()
    registry.set_worker(7, object())
    service, _ = _guard_service(
        {7: FakeMonitoring(7, MonitoringState.COMPLETED.value)}, registry
    )

    with pytest.raises(MonitoringWorkerActiveError):
        service.authorize_monitoring_deletion_or_raise(7)


# ===========================================================================
# Two-transaction flow (Task 4.4, Req 4.7)
# ===========================================================================


def test_guard_reject_by_state_creates_no_outbox_and_no_cascade() -> None:
    mon = FakeMonitoring(7, MonitoringState.RUNNING.value)
    service, repo, outbox, cascade = _full_service(mon)

    with pytest.raises(MonitoringStateNotDeletableError):
        service.delete_monitoring(7)

    assert outbox.enqueue_calls == []
    assert cascade.calls == []
    assert repo.mutations == []


def test_guard_reject_by_worker_creates_no_outbox_and_no_cascade() -> None:
    mon = FakeMonitoring(7, MonitoringState.COMPLETED.value)
    registry = FakeRuntimeRegistry()
    registry.set_worker(7, object())
    service, repo, outbox, cascade = _full_service(mon, registry=registry)

    with pytest.raises(MonitoringWorkerActiveError):
        service.delete_monitoring(7)

    assert outbox.enqueue_calls == []
    assert cascade.calls == []


def test_guard_reject_not_found_creates_no_outbox_and_no_cascade() -> None:
    # Build a service whose repo has NO monitoring with the requested id.
    repo = FakeMonitoringRepository({})
    outbox = RecordingDeletionOutbox()
    cascade = RecordingLocalCascade(outbox)
    service = DeletionService(
        monitoring_repository=repo,
        sync_state=FakeSyncState(),
        snapshot_repository=FakeSnapshotRepository(),
        module_repository=FakeModuleRepository(),
        deletion_outbox=outbox,
        local_cascade=cascade,
    )

    with pytest.raises(MonitoringNotFoundError):
        service.delete_monitoring(404)

    assert outbox.enqueue_calls == []
    assert cascade.calls == []


def test_happy_path_enqueues_then_cascades_with_deleted_at() -> None:
    mon = FakeMonitoring(7, MonitoringState.COMPLETED.value)
    service, _, outbox, cascade = _full_service(
        mon, remote_ids={("monitoring", 7): "remote-uuid-7"}
    )

    result = service.delete_monitoring(7)

    # TX1 ran once, TX2 ran once, in that order.
    assert len(outbox.enqueue_calls) == 1
    assert len(cascade.calls) == 1

    # TX2 received the deleted_at captured by the service, and completed.
    tx2 = cascade.calls[0]
    assert tx2["entity_type"] == "monitoring"
    assert tx2["entity_local_id"] == 7
    assert tx2["outbox_id"] == result.outbox_id
    assert tx2["deleted_at"] == result.deleted_at

    # The entry was marked completed (no failure), deleted_at is UTC.
    assert outbox.mark_failed_calls == []
    assert outbox.mark_completed_calls == [(result.outbox_id, result.deleted_at)]
    assert result.deleted_at.tzinfo is timezone.utc

    # DeletionResult carries outbox_id + deleted_at and is a fresh (non-reused) entry.
    assert isinstance(result, DeletionResult)
    assert result.entity_type == "monitoring"
    assert result.entity_local_id == 7
    assert result.outbox_id == 1
    assert result.reused_outbox_entry is False


def test_tx1_enqueue_failure_aborts_without_cascade() -> None:
    mon = FakeMonitoring(7, MonitoringState.COMPLETED.value)
    service, _, outbox, cascade = _full_service(mon, enqueue_should_fail=True)

    with pytest.raises(DeletionOutboxRegistrationError) as exc:
        service.delete_monitoring(7)

    assert exc.value.entity_type == "monitoring"
    assert exc.value.entity_local_id == 7
    # Enqueue was attempted but the cascade never ran.
    assert len(outbox.enqueue_calls) == 1
    assert cascade.calls == []


def test_tx2_cascade_failure_marks_failed_and_does_not_propagate() -> None:
    mon = FakeMonitoring(7, MonitoringState.COMPLETED.value)
    service, _, outbox, cascade = _full_service(mon, cascade_succeeds=False)

    with pytest.raises(LocalCascadeFailedError) as exc:
        service.delete_monitoring(7)

    assert exc.value.entity_type == "monitoring"
    assert exc.value.entity_local_id == 7
    assert exc.value.error_message == "simulated TX2 cascade failure"

    # Entry was enqueued, cascade attempted, then marked failed (non-completed).
    assert len(outbox.enqueue_calls) == 1
    assert len(cascade.calls) == 1
    assert outbox.mark_failed_calls == [1]
    assert outbox.mark_completed_calls == []
    # Non-completed entries are never eligible for remote propagation.
    entry = outbox._entries[("monitoring", 7)]
    assert entry.local_delete_status == "failed"


def test_idempotent_reinvoke_reuses_same_outbox_entry() -> None:
    mon = FakeMonitoring(7, MonitoringState.COMPLETED.value)
    repo = FakeMonitoringRepository({7: mon})
    outbox = RecordingDeletionOutbox()

    # First attempt: cascade FAILS, leaving a durable 'failed' entry (id 1).
    failing_cascade = RecordingLocalCascade(outbox, succeed=False)
    service_fail = DeletionService(
        monitoring_repository=repo,
        sync_state=FakeSyncState(),
        snapshot_repository=FakeSnapshotRepository(),
        module_repository=FakeModuleRepository(),
        deletion_outbox=outbox,
        local_cascade=failing_cascade,
    )
    with pytest.raises(LocalCascadeFailedError):
        service_fail.delete_monitoring(7)

    # Second attempt (retry): cascade SUCCEEDS; enqueue must reuse the entry.
    ok_cascade = RecordingLocalCascade(outbox, succeed=True)
    service_ok = DeletionService(
        monitoring_repository=repo,
        sync_state=FakeSyncState(),
        snapshot_repository=FakeSnapshotRepository(),
        module_repository=FakeModuleRepository(),
        deletion_outbox=outbox,
        local_cascade=ok_cascade,
    )
    result = service_ok.delete_monitoring(7)

    # Two enqueue calls total, but only ONE durable entry (no duplicate).
    assert len(outbox.enqueue_calls) == 2
    assert len(outbox._entries) == 1
    assert result.outbox_id == 1
    assert result.reused_outbox_entry is True


# ===========================================================================
# Task 5.3 -- module/greenhouse cascade, descendant validation, path capture
#   Directed unit tests (MANDATORY, Req 14.3 + 14.9). Pure fakes, no
#   SQLAlchemy / network / hardware. Reuses the fakes defined above.
#   Coverage: 14.3, 14.9, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8.
# ===========================================================================


class FakeSnapshot:
    """Minimal snapshot-like object exposing only the id the service reads."""

    def __init__(self, id: int) -> None:
        self.id = id


class FakeModule:
    """Minimal module-like object exposing the id the greenhouse traversal reads."""

    def __init__(self, id: int) -> None:
        self.id = id


def _container_service(
    *,
    monitorings: dict[int, FakeMonitoring],
    by_greenhouse: Optional[dict[int, list]] = None,
    snapshots: Optional[dict[int, list]] = None,
    remote_ids: Optional[dict[tuple[str, int], str]] = None,
    storage_paths: Optional[dict[int, StoragePaths]] = None,
    registry: Optional[FakeRuntimeRegistry] = None,
    enqueue_should_fail: bool = False,
    cascade_succeeds: bool = True,
) -> tuple[
    DeletionService,
    FakeMonitoringRepository,
    RecordingDeletionOutbox,
    RecordingLocalCascade,
]:
    """Build a DeletionService with the full container (module/greenhouse) wiring.

    ``monitorings`` maps monitoring id -> FakeMonitoring; the monitoring's
    ``module_id`` drives the module -> monitoring traversal. ``by_greenhouse``
    maps greenhouse id -> list[FakeModule] for the greenhouse traversal.
    """
    repo = FakeMonitoringRepository(monitorings)
    outbox = RecordingDeletionOutbox(enqueue_should_fail=enqueue_should_fail)
    cascade = RecordingLocalCascade(outbox, succeed=cascade_succeeds)
    service = DeletionService(
        monitoring_repository=repo,
        runtime_registry=registry,
        sync_state=FakeSyncState(remote_ids=remote_ids, storage_paths=storage_paths),
        snapshot_repository=FakeSnapshotRepository(snapshots),
        module_repository=FakeModuleRepository(by_greenhouse),
        deletion_outbox=outbox,
        local_cascade=cascade,
    )
    return service, repo, outbox, cascade


# ---------------------------------------------------------------------------
# Module happy path (Req 14.3, 6.3, 6.4)
# ---------------------------------------------------------------------------


def test_module_happy_path_single_root_entry_and_descendant_capture() -> None:
    # Module 1 has two deletable, worker-free monitorings (7, 8).
    mons = {
        7: FakeMonitoring(7, MonitoringState.COMPLETED.value, module_id=1),
        8: FakeMonitoring(8, MonitoringState.ABORTED.value, module_id=1),
    }
    # Each monitoring has snapshots; some snapshot paths are null (must be skipped).
    snapshots = {
        7: [FakeSnapshot(70), FakeSnapshot(71)],
        8: [FakeSnapshot(80)],
    }
    storage_paths = {
        70: StoragePaths(raw_storage_path="m/70/raw.jpg", annotated_storage_path="m/70/ann.jpg"),
        71: StoragePaths(raw_storage_path="m/71/raw.jpg", annotated_storage_path=None),
        80: StoragePaths(raw_storage_path=None, annotated_storage_path="m/80/ann.jpg"),
    }
    service, repo, outbox, cascade = _container_service(
        monitorings=mons,
        snapshots=snapshots,
        remote_ids={("module", 1): "remote-module-1"},
        storage_paths=storage_paths,
    )

    result = service.delete_module(1)

    # Exactly ONE root outbox entry enqueued, targeting the modules table.
    assert len(outbox.enqueue_calls) == 1
    payload = outbox.enqueue_calls[0]
    assert payload.entity_type == "module"
    assert payload.entity_local_id == 1
    assert payload.remote_table == "modules"
    assert payload.remote_id == "remote-module-1"

    # One local-artifact row per descendant monitoring, relative to OUTPUTS_DIR
    # (no 'outputs/' prefix).
    artifact_paths = [a.relative_path for a in payload.local_artifacts]
    assert artifact_paths == ["monitorings/7", "monitorings/8"]
    assert all(not p.startswith("outputs/") for p in artifact_paths)

    # Aggregated non-null descendant snapshot storage paths only.
    collected = [sp.storage_path for sp in payload.storage_paths]
    assert collected == ["m/70/raw.jpg", "m/70/ann.jpg", "m/71/raw.jpg", "m/80/ann.jpg"]

    # Cascade ran once with the module root and completed with deleted_at.
    assert len(cascade.calls) == 1
    tx2 = cascade.calls[0]
    assert tx2["entity_type"] == "module"
    assert tx2["entity_local_id"] == 1
    assert tx2["outbox_id"] == result.outbox_id
    assert tx2["deleted_at"] == result.deleted_at
    assert outbox.mark_failed_calls == []
    assert outbox.mark_completed_calls == [(result.outbox_id, result.deleted_at)]

    # DeletionResult is correct.
    assert isinstance(result, DeletionResult)
    assert result.entity_type == "module"
    assert result.entity_local_id == 1
    assert result.reused_outbox_entry is False
    assert result.deleted_at.tzinfo is timezone.utc
    # The guard is read-only.
    assert repo.mutations == []


# ---------------------------------------------------------------------------
# Greenhouse happy path (Req 14.3, 6.3, 6.4) -- descendants across modules
# ---------------------------------------------------------------------------


def test_greenhouse_happy_path_single_root_entry_across_multiple_modules() -> None:
    # Greenhouse 5 -> modules 1 and 2; each module has monitorings.
    mons = {
        7: FakeMonitoring(7, MonitoringState.COMPLETED.value, module_id=1),
        8: FakeMonitoring(8, MonitoringState.READY_FOR_ANALYSIS.value, module_id=2),
        9: FakeMonitoring(9, MonitoringState.ERROR.value, module_id=2),
    }
    snapshots = {
        7: [FakeSnapshot(70)],
        8: [FakeSnapshot(80)],
        9: [],
    }
    storage_paths = {
        70: StoragePaths(raw_storage_path="g/70/raw.jpg", annotated_storage_path="g/70/ann.jpg"),
        80: StoragePaths(raw_storage_path="g/80/raw.jpg", annotated_storage_path=None),
    }
    service, _, outbox, cascade = _container_service(
        monitorings=mons,
        by_greenhouse={5: [FakeModule(1), FakeModule(2)]},
        snapshots=snapshots,
        remote_ids={("greenhouse", 5): "remote-gh-5"},
        storage_paths=storage_paths,
    )

    result = service.delete_greenhouse(5)

    assert len(outbox.enqueue_calls) == 1
    payload = outbox.enqueue_calls[0]
    assert payload.entity_type == "greenhouse"
    assert payload.entity_local_id == 5
    assert payload.remote_table == "greenhouses"
    assert payload.remote_id == "remote-gh-5"

    # One local-artifact per descendant monitoring, across BOTH modules.
    artifact_paths = [a.relative_path for a in payload.local_artifacts]
    assert artifact_paths == ["monitorings/7", "monitorings/8", "monitorings/9"]

    # Aggregated non-null storage paths across all descendant snapshots.
    collected = [sp.storage_path for sp in payload.storage_paths]
    assert collected == ["g/70/raw.jpg", "g/70/ann.jpg", "g/80/raw.jpg"]

    assert len(cascade.calls) == 1
    assert cascade.calls[0]["entity_type"] == "greenhouse"
    assert cascade.calls[0]["entity_local_id"] == 5
    assert result.entity_type == "greenhouse"
    assert result.entity_local_id == 5
    assert outbox.mark_completed_calls == [(result.outbox_id, result.deleted_at)]


# ---------------------------------------------------------------------------
# Descendant validation reject (Req 14.9, 6.5, 6.6, 6.7, 6.8)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prohibited_state", PROHIBITED)
def test_module_reject_when_descendant_state_prohibited(
    prohibited_state: MonitoringState,
) -> None:
    mons = {
        7: FakeMonitoring(7, MonitoringState.COMPLETED.value, module_id=1),
        8: FakeMonitoring(8, prohibited_state.value, module_id=1),
    }
    service, repo, outbox, cascade = _container_service(monitorings=mons)

    with pytest.raises(ContainerDescendantNotDeletableError) as exc:
        service.delete_module(1)

    assert exc.value.entity_type == "module"
    assert exc.value.entity_local_id == 1
    assert exc.value.monitoring_id == 8
    # NO outbox entry, NO cascade, hierarchy intact.
    assert outbox.enqueue_calls == []
    assert cascade.calls == []
    assert repo.mutations == []


def test_module_reject_when_descendant_worker_active() -> None:
    mons = {
        7: FakeMonitoring(7, MonitoringState.COMPLETED.value, module_id=1),
        8: FakeMonitoring(8, MonitoringState.COMPLETED.value, module_id=1),
    }
    registry = FakeRuntimeRegistry()
    registry.set_worker(8, object())
    service, _, outbox, cascade = _container_service(
        monitorings=mons, registry=registry
    )

    with pytest.raises(ContainerDescendantWorkerActiveError) as exc:
        service.delete_module(1)

    assert exc.value.entity_type == "module"
    assert exc.value.entity_local_id == 1
    assert exc.value.monitoring_id == 8
    assert outbox.enqueue_calls == []
    assert cascade.calls == []


@pytest.mark.parametrize("prohibited_state", PROHIBITED)
def test_greenhouse_reject_when_descendant_state_prohibited(
    prohibited_state: MonitoringState,
) -> None:
    mons = {
        7: FakeMonitoring(7, MonitoringState.COMPLETED.value, module_id=1),
        9: FakeMonitoring(9, prohibited_state.value, module_id=2),
    }
    service, _, outbox, cascade = _container_service(
        monitorings=mons,
        by_greenhouse={5: [FakeModule(1), FakeModule(2)]},
    )

    with pytest.raises(ContainerDescendantNotDeletableError) as exc:
        service.delete_greenhouse(5)

    assert exc.value.entity_type == "greenhouse"
    assert exc.value.entity_local_id == 5
    assert exc.value.monitoring_id == 9
    assert outbox.enqueue_calls == []
    assert cascade.calls == []


def test_greenhouse_reject_when_descendant_worker_active() -> None:
    mons = {
        7: FakeMonitoring(7, MonitoringState.COMPLETED.value, module_id=1),
        9: FakeMonitoring(9, MonitoringState.ABORTED.value, module_id=2),
    }
    registry = FakeRuntimeRegistry()
    registry.set_thread(9, FakeAliveThread(alive=True))
    service, _, outbox, cascade = _container_service(
        monitorings=mons,
        by_greenhouse={5: [FakeModule(1), FakeModule(2)]},
        registry=registry,
    )

    with pytest.raises(ContainerDescendantWorkerActiveError) as exc:
        service.delete_greenhouse(5)

    assert exc.value.entity_type == "greenhouse"
    assert exc.value.entity_local_id == 5
    assert exc.value.monitoring_id == 9
    assert outbox.enqueue_calls == []
    assert cascade.calls == []


def test_validate_descendants_allows_when_all_deletable_and_worker_free() -> None:
    mons = {
        7: FakeMonitoring(7, MonitoringState.COMPLETED.value, module_id=1),
        8: FakeMonitoring(8, MonitoringState.ERROR.value, module_id=1),
    }
    service, _, _, _ = _container_service(monitorings=mons)

    module_result = service.validate_descendants("module", 1)
    assert module_result.outcome is DescendantValidationOutcome.ALLOWED
    assert module_result.allowed is True
    assert set(module_result.checked_monitoring_ids) == {7, 8}

    # Same for a greenhouse root spanning the module.
    service_gh, _, _, _ = _container_service(
        monitorings=mons, by_greenhouse={5: [FakeModule(1)]}
    )
    gh_result = service_gh.validate_descendants("greenhouse", 5)
    assert gh_result.outcome is DescendantValidationOutcome.ALLOWED
    assert set(gh_result.checked_monitoring_ids) == {7, 8}


def test_validate_descendants_or_raise_raises_for_both_reasons() -> None:
    # Prohibited state.
    mons_state = {8: FakeMonitoring(8, MonitoringState.RUNNING.value, module_id=1)}
    service_state, _, _, _ = _container_service(monitorings=mons_state)
    with pytest.raises(ContainerDescendantNotDeletableError):
        service_state.validate_descendants_or_raise("module", 1)

    # Active worker.
    mons_worker = {8: FakeMonitoring(8, MonitoringState.COMPLETED.value, module_id=1)}
    registry = FakeRuntimeRegistry()
    registry.set_analysis(8)
    service_worker, _, _, _ = _container_service(
        monitorings=mons_worker, registry=registry
    )
    with pytest.raises(ContainerDescendantWorkerActiveError):
        service_worker.validate_descendants_or_raise("module", 1)


# ---------------------------------------------------------------------------
# TX1 / TX2 failure paths for containers (Req 6.4)
# ---------------------------------------------------------------------------


def test_module_tx1_enqueue_failure_aborts_without_cascade() -> None:
    mons = {7: FakeMonitoring(7, MonitoringState.COMPLETED.value, module_id=1)}
    service, _, outbox, cascade = _container_service(
        monitorings=mons, enqueue_should_fail=True
    )

    with pytest.raises(DeletionOutboxRegistrationError) as exc:
        service.delete_module(1)

    assert exc.value.entity_type == "module"
    assert exc.value.entity_local_id == 1
    assert len(outbox.enqueue_calls) == 1
    assert cascade.calls == []


def test_greenhouse_tx2_cascade_failure_marks_failed_and_does_not_propagate() -> None:
    mons = {7: FakeMonitoring(7, MonitoringState.COMPLETED.value, module_id=1)}
    service, _, outbox, cascade = _container_service(
        monitorings=mons,
        by_greenhouse={5: [FakeModule(1)]},
        cascade_succeeds=False,
    )

    with pytest.raises(LocalCascadeFailedError) as exc:
        service.delete_greenhouse(5)

    assert exc.value.entity_type == "greenhouse"
    assert exc.value.entity_local_id == 5
    assert exc.value.error_message == "simulated TX2 cascade failure"
    assert len(outbox.enqueue_calls) == 1
    assert len(cascade.calls) == 1
    assert outbox.mark_failed_calls == [1]
    assert outbox.mark_completed_calls == []
    entry = outbox._entries[("greenhouse", 5)]
    assert entry.local_delete_status == "failed"


# ---------------------------------------------------------------------------
# Storage / local-artifact capture correctness (Req 6.3)
# ---------------------------------------------------------------------------


def test_module_capture_includes_only_non_null_paths_one_artifact_per_monitoring() -> None:
    mons = {
        7: FakeMonitoring(7, MonitoringState.COMPLETED.value, module_id=1),
        8: FakeMonitoring(8, MonitoringState.COMPLETED.value, module_id=1),
    }
    snapshots = {
        7: [FakeSnapshot(70), FakeSnapshot(71)],
        8: [FakeSnapshot(80)],
    }
    # 70: both null (nothing captured). 71: only raw. 80: only annotated.
    storage_paths = {
        70: StoragePaths(raw_storage_path=None, annotated_storage_path=None),
        71: StoragePaths(raw_storage_path="only/raw.jpg", annotated_storage_path=None),
        80: StoragePaths(raw_storage_path=None, annotated_storage_path="only/ann.jpg"),
    }
    service, _, outbox, _ = _container_service(
        monitorings=mons, snapshots=snapshots, storage_paths=storage_paths
    )

    service.delete_module(1)

    payload = outbox.enqueue_calls[0]
    collected = [sp.storage_path for sp in payload.storage_paths]
    assert collected == ["only/raw.jpg", "only/ann.jpg"]
    # One artifact per descendant monitoring regardless of snapshot path nullity.
    assert [a.relative_path for a in payload.local_artifacts] == [
        "monitorings/7",
        "monitorings/8",
    ]


def test_empty_module_enqueues_root_entry_with_no_artifacts_or_paths() -> None:
    # Module 1 has no monitorings at all.
    service, _, outbox, cascade = _container_service(
        monitorings={},
        remote_ids={("module", 1): "remote-module-1"},
    )

    result = service.delete_module(1)

    assert len(outbox.enqueue_calls) == 1
    payload = outbox.enqueue_calls[0]
    assert payload.entity_type == "module"
    assert payload.remote_table == "modules"
    assert payload.remote_id == "remote-module-1"
    assert list(payload.local_artifacts) == []
    assert list(payload.storage_paths) == []
    # Cascade still runs and completes for the empty container.
    assert len(cascade.calls) == 1
    assert result.entity_local_id == 1
    assert outbox.mark_completed_calls == [(result.outbox_id, result.deleted_at)]


def test_empty_greenhouse_enqueues_root_entry_with_no_artifacts_or_paths() -> None:
    # Greenhouse 5 has no modules (and thus no monitorings).
    service, _, outbox, cascade = _container_service(
        monitorings={},
        by_greenhouse={5: []},
        remote_ids={("greenhouse", 5): "remote-gh-5"},
    )

    result = service.delete_greenhouse(5)

    assert len(outbox.enqueue_calls) == 1
    payload = outbox.enqueue_calls[0]
    assert payload.entity_type == "greenhouse"
    assert payload.remote_table == "greenhouses"
    assert list(payload.local_artifacts) == []
    assert list(payload.storage_paths) == []
    assert len(cascade.calls) == 1
    assert result.entity_local_id == 5
