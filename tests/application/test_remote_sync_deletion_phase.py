"""Tests for RemoteSyncService FASE 0 — durable-deletion propagation.

Spec 021 — Monitoring Data Lifecycle and Remote Deletion Consistency.
Task 8.4 (MANDATORY directed tests).

Validates the deletion phase of RemoteSyncService in isolation from the
upsert phases, using in-memory fakes (no SQLAlchemy, network, or hardware):

- Anti-resurrection ordering (Req 14.7): FASE 0 runs and marks entries synced
  BEFORE the upsert phases; a deleted entity is NEVER re-uploaded (upsert count
  stays 0 with empty sync state); progress label 'deletions' is emitted before
  any upsert label.
- No-orphan Storage cleanup (Req 14.6): an entry is only marked 'synced' when
  the data DELETE and ALL its Storage paths reach 'removed'; a failing Storage
  path keeps the entry retryable (mark_error), the path stays retryable, and
  already-absent is treated as removed.
- Offline retry (Req 14.5): a CONNECTIVITY data-delete failure marks the entry
  'error' + increments retry_count, does NOT mark it synced/removed, sets
  SyncResult.deletions_failed>=1 and success False; a subsequent run reprocesses
  and can succeed.
- Continuation on failure (Req 9.5): with 3 entries, a middle failure leaves the
  first and third synced and the loop continues.
- Entry with remote_id=None and no Storage paths: synced without calling
  delete_by_id.
- deletion_outbox=None: FASE 0 is a no-op and execute_sync still returns a
  SyncResult (existing behavior).

Requirements: 14.5, 14.6, 14.7, 10.1, 10.2, 9.4, 9.5, 11.1, 11.2, 11.3, 8.4
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import List, Optional

import pytest

from src.application.interfaces.deletion_outbox_port import (
    DeletionOutboxEntry,
    OutboxStoragePathRow,
)
from src.application.interfaces.remote_data_port import (
    RemoteDeleteResult,
    RemoteUpsertResult,
)
from src.application.interfaces.remote_storage_port import (
    RemoteStorageDeleteResult,
    RemoteUploadResult,
)
from src.application.interfaces.sync_state_port import (
    StoragePaths,
    SyncStatusCounts,
)
from src.application.services.remote_sync_service import (
    RemoteSyncService,
    SyncResult,
)
from src.application.services.sync_runtime_state import SyncRuntimeState


_USER_REMOTE_ID = "99999999-8888-4777-8666-555555555555"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


# Local user id that _USER_REMOTE_ID resolves to (session owner).
_SESSION_LOCAL_USER_ID = 7


def _make_entry(
    entry_id: int,
    *,
    remote_id: Optional[str],
    remote_table: str = "monitorings",
    created_at: Optional[datetime] = None,
    entity_type: str = "monitoring",
    entity_local_id: Optional[int] = None,
    owner_user_id: Optional[int] = _SESSION_LOCAL_USER_ID,
) -> DeletionOutboxEntry:
    """Build a locally-completed DeletionOutboxEntry ready for propagation.

    Defaults ``owner_user_id`` to the session's local user id so entries are
    owned by the syncing user (Spec 022 FASE 0 scoping).
    """
    return DeletionOutboxEntry(
        id=entry_id,
        entity_type=entity_type,
        entity_local_id=entity_local_id if entity_local_id is not None else entry_id,
        remote_table=remote_table,
        remote_id=remote_id,
        created_at=created_at or datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        status="pending",
        local_delete_status="completed",
        cleanup_status="pending",
        deleted_at=datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        last_error=None,
        retry_count=0,
        owner_user_id=owner_user_id,
    )


class FakeDeletionOutbox:
    """In-memory DeletionOutboxPort focused on FASE 0 propagation.

    Holds completed entries and per-entry Storage-path rows. Records
    mark_syncing/synced/error, mark_storage_path_status, and
    increment_retry_count. get_pending_for_propagation returns the entries
    that are still retryable (status pending/error/syncing) ordered by
    created_at ASC.
    """

    def __init__(self) -> None:
        self._entries: List[DeletionOutboxEntry] = []
        # outbox_id -> list[OutboxStoragePathRow]
        self._paths: dict[int, List[OutboxStoragePathRow]] = {}
        self.calls: list[tuple] = []

    # --- seeding helpers -------------------------------------------------

    def add_entry(self, entry: DeletionOutboxEntry) -> None:
        self._entries.append(entry)
        self._paths.setdefault(entry.id, [])

    def add_storage_path(
        self, outbox_id: int, path_id: int, storage_path: str, status: str = "pending"
    ) -> None:
        self._paths.setdefault(outbox_id, []).append(
            OutboxStoragePathRow(id=path_id, storage_path=storage_path, status=status)
        )

    def _find(self, outbox_id: int) -> Optional[DeletionOutboxEntry]:
        for entry in self._entries:
            if entry.id == outbox_id:
                return entry
        return None

    def _find_path(self, storage_path_id: int) -> Optional[OutboxStoragePathRow]:
        for rows in self._paths.values():
            for row in rows:
                if row.id == storage_path_id:
                    return row
        return None

    # --- port methods used by FASE 0 ------------------------------------

    def get_pending_for_propagation(
        self, owner_user_id: int
    ) -> List[DeletionOutboxEntry]:
        retriable = [
            e
            for e in self._entries
            if e.owner_user_id == owner_user_id
            and e.local_delete_status == "completed"
            and e.status in ("pending", "error", "syncing")
        ]
        return sorted(retriable, key=lambda e: e.created_at)

    def get_storage_paths_for_entry(self, outbox_id: int) -> List[OutboxStoragePathRow]:
        return sorted(self._paths.get(outbox_id, []), key=lambda r: r.id)

    def mark_syncing(self, outbox_id: int) -> None:
        self.calls.append(("mark_syncing", outbox_id))
        entry = self._find(outbox_id)
        if entry is not None:
            entry.status = "syncing"

    def mark_synced(self, outbox_id: int) -> None:
        self.calls.append(("mark_synced", outbox_id))
        entry = self._find(outbox_id)
        if entry is not None:
            entry.status = "synced"

    def mark_error(self, outbox_id: int, error_message: str) -> None:
        self.calls.append(("mark_error", outbox_id, error_message))
        entry = self._find(outbox_id)
        if entry is not None:
            entry.status = "error"
            entry.last_error = error_message

    def mark_storage_path_status(self, storage_path_id: int, status: str) -> None:
        self.calls.append(("mark_storage_path_status", storage_path_id, status))
        row = self._find_path(storage_path_id)
        if row is not None:
            row.status = status

    def increment_retry_count(self, outbox_id: int) -> None:
        self.calls.append(("increment_retry_count", outbox_id))
        entry = self._find(outbox_id)
        if entry is not None:
            entry.retry_count += 1

    # --- unused-by-FASE-0 port methods (minimal no-ops) -----------------

    def enqueue(self, entry):  # pragma: no cover - not exercised in FASE 0
        raise NotImplementedError

    def mark_local_prepared(self, outbox_id: int) -> None:  # pragma: no cover
        pass

    def mark_local_completed(self, outbox_id: int, deleted_at: datetime) -> None:  # pragma: no cover
        pass

    def mark_local_failed(self, outbox_id: int) -> None:  # pragma: no cover
        pass

    def mark_local_artifact_status(self, artifact_id, status, last_error=None):  # pragma: no cover
        pass

    def record_last_error(self, outbox_id, error_message, occurred_at):  # pragma: no cover
        pass


class FakeRemoteData:
    """RemoteDataPort fake with configurable delete results and upsert tracking.

    delete_by_id returns a configurable RemoteDeleteResult per remote_id (or a
    default). upsert records all calls so tests can assert a deleted entity is
    NEVER re-uploaded. upsert returns success by default (harmless when the
    sync state has no pending entities).
    """

    def __init__(
        self,
        delete_results: Optional[dict[str, RemoteDeleteResult]] = None,
        default_delete: Optional[RemoteDeleteResult] = None,
    ) -> None:
        self.delete_calls: list[tuple] = []
        self.upsert_calls: list[dict] = []
        self._delete_results = delete_results or {}
        self._default_delete = default_delete or RemoteDeleteResult(success=True)

    def upsert(self, access_token: str, table: str, data: dict) -> RemoteUpsertResult:
        self.upsert_calls.append({"table": table, "data": copy.deepcopy(data)})
        return RemoteUpsertResult(success=True, remote_id=data.get("id"))

    def delete_by_id(
        self, access_token: str, table: str, remote_id: str
    ) -> RemoteDeleteResult:
        self.delete_calls.append((table, remote_id))
        return self._delete_results.get(remote_id, self._default_delete)


class FakeRemoteStorage:
    """RemoteStoragePort fake with configurable remove_object results."""

    def __init__(
        self,
        remove_results: Optional[dict[str, RemoteStorageDeleteResult]] = None,
        default_remove: Optional[RemoteStorageDeleteResult] = None,
    ) -> None:
        self.remove_calls: list[str] = []
        self.upload_calls: list[dict] = []
        self._remove_results = remove_results or {}
        self._default_remove = default_remove or RemoteStorageDeleteResult(success=True)

    def upload_file(
        self, access_token: str, local_file_path: str, remote_path: str
    ) -> RemoteUploadResult:  # pragma: no cover - not exercised in FASE 0
        self.upload_calls.append({"local": local_file_path, "remote": remote_path})
        return RemoteUploadResult(success=True, object_path=remote_path)

    def remove_object(
        self, access_token: str, path: str
    ) -> RemoteStorageDeleteResult:
        self.remove_calls.append(path)
        return self._remove_results.get(path, self._default_remove)


class FakeSyncState:
    """SyncStatePort fake: no pending entities, so upsert phases are no-ops."""

    def get_pending_entities(self, entity_type: str) -> list[dict]:
        return []

    def get_local_user_id_by_remote_id(self, user_remote_id: str) -> Optional[int]:
        # The session's remote identity resolves to the session local user id.
        return _SESSION_LOCAL_USER_ID if user_remote_id == _USER_REMOTE_ID else None

    def get_remote_id(self, entity_type: str, local_id: int) -> Optional[str]:  # pragma: no cover
        return None

    def reserve_remote_id(self, entity_type, local_id, remote_id) -> None:  # pragma: no cover
        pass

    def mark_syncing(self, entity_type, local_id) -> None:  # pragma: no cover
        pass

    def mark_synced(self, entity_type, local_id, remote_id) -> None:  # pragma: no cover
        pass

    def mark_error(self, entity_type, local_id, error_msg) -> None:  # pragma: no cover
        pass

    def get_storage_paths(self, snapshot_id: int) -> StoragePaths:  # pragma: no cover
        return StoragePaths()

    def set_storage_paths(self, snapshot_id, raw_path, annotated_path) -> None:  # pragma: no cover
        pass

    def get_sync_status_counts(self) -> SyncStatusCounts:  # pragma: no cover
        return SyncStatusCounts()


class FakeRuntimeState:
    """Records update_progress labels/order without any locking."""

    def __init__(self) -> None:
        self.progress: list[tuple] = []

    def update_progress(self, phase: str, processed: int, total: int) -> None:
        self.progress.append((phase, processed, total))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_service(outbox, data=None, storage=None, runtime=None):
    return RemoteSyncService(
        remote_data=data or FakeRemoteData(),
        remote_storage=storage or FakeRemoteStorage(),
        sync_state=FakeSyncState(),
        runtime_state=runtime or SyncRuntimeState(),
        deletion_outbox=outbox,
    )


# ===========================================================================
# Anti-resurrection ordering (Req 14.7, 10.1, 10.2)
# ===========================================================================


class TestAntiResurrectionOrdering:
    def test_deletion_runs_and_marks_synced_and_no_reupload(self):
        outbox = FakeDeletionOutbox()
        outbox.add_entry(_make_entry(1, remote_id="mon-uuid-1"))

        data = FakeRemoteData(default_delete=RemoteDeleteResult(success=True))
        runtime = FakeRuntimeState()
        svc = _build_service(outbox, data=data, runtime=runtime)

        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        # Deletion phase ran and the entry was marked synced.
        assert ("mark_synced", 1) in outbox.calls
        assert result.deletions_synced == 1
        assert result.deletions_failed == 0
        # No re-upload of the deleted entity (empty sync state -> zero upserts).
        assert data.upsert_calls == []
        # Data DELETE was emitted for the root.
        assert data.delete_calls == [("monitorings", "mon-uuid-1")]

    def test_deletions_label_emitted_before_upsert_labels(self):
        outbox = FakeDeletionOutbox()
        outbox.add_entry(_make_entry(1, remote_id="mon-uuid-1"))
        runtime = FakeRuntimeState()
        svc = _build_service(outbox, runtime=runtime)

        svc.execute_sync("jwt", _USER_REMOTE_ID)

        labels = [p[0] for p in runtime.progress]
        assert "deletions" in labels
        deletions_index = labels.index("deletions")
        upsert_labels = {
            "greenhouses", "modules", "monitorings", "monitoring_metrics",
            "snapshots", "inspection_results", "activity_logs",
        }
        # Any upsert label present must come after the deletions label.
        for i, label in enumerate(labels):
            if label in upsert_labels:
                assert i > deletions_index

    def test_syncing_marked_before_synced(self):
        outbox = FakeDeletionOutbox()
        outbox.add_entry(_make_entry(1, remote_id="mon-uuid-1"))
        svc = _build_service(outbox)

        svc.execute_sync("jwt", _USER_REMOTE_ID)

        order = [c for c in outbox.calls if c[0] in ("mark_syncing", "mark_synced")]
        assert order == [("mark_syncing", 1), ("mark_synced", 1)]


# ===========================================================================
# No-orphan Storage cleanup (Req 14.6, 8.4, 8.5)
# ===========================================================================


class TestNoOrphanStorage:
    def test_two_paths_removed_then_entry_synced(self):
        outbox = FakeDeletionOutbox()
        outbox.add_entry(_make_entry(1, remote_id="mon-uuid-1"))
        outbox.add_storage_path(1, 10, "monitorings/x/raw/s_000001.jpg")
        outbox.add_storage_path(1, 11, "monitorings/x/annotated/s_000001.jpg")

        storage = FakeRemoteStorage(default_remove=RemoteStorageDeleteResult(success=True))
        svc = _build_service(outbox, storage=storage)

        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        # Both paths marked removed, entry synced.
        assert ("mark_storage_path_status", 10, "removed") in outbox.calls
        assert ("mark_storage_path_status", 11, "removed") in outbox.calls
        assert ("mark_synced", 1) in outbox.calls
        assert outbox._find(1).status == "synced"
        assert result.deletions_synced == 1
        assert result.deletions_failed == 0

    def test_one_path_fails_entry_not_synced_and_path_retryable(self):
        outbox = FakeDeletionOutbox()
        outbox.add_entry(_make_entry(1, remote_id="mon-uuid-1"))
        outbox.add_storage_path(1, 10, "ok/path.jpg")
        outbox.add_storage_path(1, 11, "bad/path.jpg")

        storage = FakeRemoteStorage(
            remove_results={
                "ok/path.jpg": RemoteStorageDeleteResult(success=True),
                "bad/path.jpg": RemoteStorageDeleteResult(
                    success=False, error_type="CONNECTIVITY", error_message="down"
                ),
            }
        )
        svc = _build_service(outbox, storage=storage)

        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        # Successful path marked removed; failing path marked error (retryable).
        assert ("mark_storage_path_status", 10, "removed") in outbox.calls
        assert ("mark_storage_path_status", 11, "error") in outbox.calls
        # Entry NOT synced; marked error + retry incremented.
        assert ("mark_synced", 1) not in outbox.calls
        assert outbox._find(1).status == "error"
        assert ("increment_retry_count", 1) in outbox.calls
        assert result.deletions_synced == 0
        assert result.deletions_failed == 1
        assert result.success is False

    def test_already_absent_storage_treated_as_removed(self):
        outbox = FakeDeletionOutbox()
        outbox.add_entry(_make_entry(1, remote_id="mon-uuid-1"))
        outbox.add_storage_path(1, 10, "gone/path.jpg")

        storage = FakeRemoteStorage(
            remove_results={
                "gone/path.jpg": RemoteStorageDeleteResult(
                    success=True, already_absent=True
                ),
            }
        )
        svc = _build_service(outbox, storage=storage)

        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        assert ("mark_storage_path_status", 10, "removed") in outbox.calls
        assert ("mark_synced", 1) in outbox.calls
        assert result.deletions_synced == 1


# ===========================================================================
# Offline retry with last_error (Req 14.5, 9.4, 11.1, 11.2, 11.3, 10.3)
# ===========================================================================


class TestOfflineRetry:
    def test_connectivity_failure_marks_error_and_retry(self):
        outbox = FakeDeletionOutbox()
        outbox.add_entry(_make_entry(1, remote_id="mon-uuid-1"))
        # A storage path that would be cleaned only if data DELETE succeeded.
        outbox.add_storage_path(1, 10, "monitorings/x/raw/s.jpg")

        data = FakeRemoteData(
            default_delete=RemoteDeleteResult(
                success=False, error_type="CONNECTIVITY", error_message="offline"
            )
        )
        storage = FakeRemoteStorage()
        svc = _build_service(outbox, data=data, storage=storage)

        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        entry = outbox._find(1)
        assert entry.status == "error"
        assert entry.retry_count == 1
        assert entry.last_error is not None
        # Not synced, no storage removal attempted (DELETE failed first).
        assert ("mark_synced", 1) not in outbox.calls
        assert storage.remove_calls == []
        assert result.deletions_failed >= 1
        assert result.success is False

    def test_subsequent_run_reprocesses_and_succeeds(self):
        outbox = FakeDeletionOutbox()
        outbox.add_entry(_make_entry(1, remote_id="mon-uuid-1"))

        # First run: connectivity failure.
        failing_data = FakeRemoteData(
            default_delete=RemoteDeleteResult(
                success=False, error_type="CONNECTIVITY", error_message="offline"
            )
        )
        svc1 = _build_service(outbox, data=failing_data)
        result1 = svc1.execute_sync("jwt", _USER_REMOTE_ID)
        assert result1.deletions_failed == 1
        assert outbox._find(1).status == "error"

        # Entry still returned for propagation (error is retryable).
        assert [e.id for e in outbox.get_pending_for_propagation(_SESSION_LOCAL_USER_ID)] == [1]

        # Second run: connectivity restored -> succeeds.
        ok_data = FakeRemoteData(default_delete=RemoteDeleteResult(success=True))
        svc2 = _build_service(outbox, data=ok_data)
        result2 = svc2.execute_sync("jwt", _USER_REMOTE_ID)

        assert result2.deletions_synced == 1
        assert result2.deletions_failed == 0
        assert outbox._find(1).status == "synced"


# ===========================================================================
# Continuation on failure (Req 9.5)
# ===========================================================================


class TestContinuationOnFailure:
    def test_middle_entry_fails_others_synced(self):
        outbox = FakeDeletionOutbox()
        base = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        outbox.add_entry(
            _make_entry(1, remote_id="uuid-1", created_at=base.replace(second=1))
        )
        outbox.add_entry(
            _make_entry(2, remote_id="uuid-2", created_at=base.replace(second=2))
        )
        outbox.add_entry(
            _make_entry(3, remote_id="uuid-3", created_at=base.replace(second=3))
        )

        data = FakeRemoteData(
            delete_results={
                "uuid-1": RemoteDeleteResult(success=True),
                "uuid-2": RemoteDeleteResult(
                    success=False, error_type="CONNECTIVITY", error_message="down"
                ),
                "uuid-3": RemoteDeleteResult(success=True),
            }
        )
        svc = _build_service(outbox, data=data)

        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        assert outbox._find(1).status == "synced"
        assert outbox._find(2).status == "error"
        assert outbox._find(3).status == "synced"
        assert result.deletions_synced == 2
        assert result.deletions_failed == 1
        # All three roots were attempted (loop continued past the failure).
        assert data.delete_calls == [
            ("monitorings", "uuid-1"),
            ("monitorings", "uuid-2"),
            ("monitorings", "uuid-3"),
        ]


# ===========================================================================
# Entry without remote_id and without storage paths (Req 9.1, 8.4)
# ===========================================================================


class TestNoRemoteIdNoPaths:
    def test_synced_without_calling_delete_by_id(self):
        outbox = FakeDeletionOutbox()
        outbox.add_entry(_make_entry(1, remote_id=None))

        data = FakeRemoteData()
        svc = _build_service(outbox, data=data)

        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        # No data DELETE emitted (never synced remotely).
        assert data.delete_calls == []
        assert ("mark_synced", 1) in outbox.calls
        assert result.deletions_synced == 1
        assert result.deletions_failed == 0


# ===========================================================================
# No outbox wired -> FASE 0 no-op (existing behavior)
# ===========================================================================


class TestNoOutboxNoOp:
    def test_execute_sync_returns_result_without_outbox(self):
        svc = RemoteSyncService(
            remote_data=FakeRemoteData(),
            remote_storage=FakeRemoteStorage(),
            sync_state=FakeSyncState(),
            runtime_state=SyncRuntimeState(),
            deletion_outbox=None,
        )

        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        assert isinstance(result, SyncResult)
        assert result.deletions_synced == 0
        assert result.deletions_failed == 0
        assert result.success is True


# ===========================================================================
# Spec 022 — FASE 0 user-scoped propagation (A + B + legacy NULL)
# ===========================================================================


class TestFase0UserScoped:
    """B's sync propagates only B's deletions; A and legacy NULL are untouched."""

    def test_only_session_user_entries_processed(self):
        _A_LOCAL = 1  # different user
        _LEGACY = None

        outbox = FakeDeletionOutbox()
        # A's deletion (owner = A, not the session user).
        outbox.add_entry(_make_entry(
            1, remote_id="mon-A", owner_user_id=_A_LOCAL
        ))
        # B's deletion (owner = session user _SESSION_LOCAL_USER_ID).
        outbox.add_entry(_make_entry(
            2, remote_id="mon-B", owner_user_id=_SESSION_LOCAL_USER_ID
        ))
        # Legacy deletion with NULL owner.
        outbox.add_entry(_make_entry(
            3, remote_id="mon-legacy", owner_user_id=_LEGACY
        ))

        data = FakeRemoteData(default_delete=RemoteDeleteResult(success=True))
        svc = _build_service(outbox, data=data)

        result = svc.execute_sync("jwt", _USER_REMOTE_ID)  # session = B

        # Only B reached delete_by_id.
        assert data.delete_calls == [("monitorings", "mon-B")]
        assert result.deletions_synced == 1
        assert result.deletions_failed == 0

        # B was marked synced.
        b = outbox._find(2)
        assert b.status == "synced"

        # A and legacy are completely intact: no status change, no retry.
        a = outbox._find(1)
        legacy = outbox._find(3)
        assert a.status == "pending"
        assert a.retry_count == 0
        assert legacy.status == "pending"
        assert legacy.retry_count == 0

        # No mark_* calls touched entries 1 or 3.
        touched_ids = {c[1] for c in outbox.calls if len(c) >= 2 and isinstance(c[1], int)}
        assert 1 not in touched_ids
        assert 3 not in touched_ids

    def test_unresolvable_session_user_aborts_before_fase0(self):
        """If the remote identity maps to no local user, sync aborts untouched."""
        outbox = FakeDeletionOutbox()
        outbox.add_entry(_make_entry(
            2, remote_id="mon-B", owner_user_id=_SESSION_LOCAL_USER_ID
        ))
        data = FakeRemoteData(default_delete=RemoteDeleteResult(success=True))
        svc = _build_service(outbox, data=data)

        result = svc.execute_sync("jwt", "unknown-remote-id")

        assert result.success is False
        # Nothing propagated; no delete calls; entry untouched.
        assert data.delete_calls == []
        assert outbox.calls == []
        assert outbox._find(2).status == "pending"
