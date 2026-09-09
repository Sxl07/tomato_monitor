"""RemoteSyncService: orchestrates entity synchronization to Supabase.

Runs a durable-deletion phase (FASE 0) at the start of each sync, before the
upsert phases, to propagate locally-completed deletions from the
Deletion_Outbox (anti-resurrection ordering). It then processes entities in
strict hierarchical order (parents before children), reserves UUIDs before
remote writes for crash recovery, and continues past individual failures
without aborting the entire sync.

Ownership:
- The sync_api route owns try_acquire()/release() on SyncRuntimeState.
- This service only calls runtime_state.update_progress().

This module belongs to the application layer and depends only on
abstract ports and stdlib. No httpx, SQLAlchemy, FastAPI, or
infrastructure imports.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.application.interfaces.deletion_outbox_port import (
    DeletionOutboxEntry,
    DeletionOutboxPort,
)
from src.application.interfaces.remote_data_port import RemoteDataPort
from src.application.interfaces.remote_storage_port import RemoteStoragePort
from src.application.interfaces.sync_state_port import SyncStatePort
from src.application.services.sync_runtime_state import SyncRuntimeState


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class SyncResult:
    """Summary of a sync execution.

    The deletion counters (deletions_synced / deletions_failed) summarize the
    FASE 0 durable-deletion propagation phase that runs before the upsert
    phases. They are additive and do not alter the existing contract fields.
    """

    success: bool
    entities_synced: int
    entities_failed: int
    images_uploaded: int
    images_failed: int
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    deletions_synced: int = 0
    deletions_failed: int = 0


# ---------------------------------------------------------------------------
# Sync phases in strict order
# ---------------------------------------------------------------------------

_PHASES = [
    ("greenhouse", "greenhouses"),
    ("module", "modules"),
    ("monitoring", "monitorings"),
    ("monitoring_metrics", "monitoring_metrics"),
    ("snapshot", "snapshots"),
    ("inspection_result", "inspection_results"),
    ("activity_log", "activity_logs"),
]

# Parent dependency map: entity_type → (parent_entity_type, local_fk_field)
_PARENT_DEPS: dict[str, tuple[str, str]] = {
    "module": ("greenhouse", "greenhouse_id"),
    "monitoring": ("module", "module_id"),
    "monitoring_metrics": ("monitoring", "monitoring_id"),
    "snapshot": ("monitoring", "monitoring_id"),
    "inspection_result": ("snapshot", "snapshot_id"),
    "activity_log": ("module", "module_id"),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_value(value):
    """Convert a value to JSON-safe form (datetimes → ISO strings)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            # Naive → interpret as UTC
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()
    return value


def _serialize_dict(data: dict) -> dict:
    """Serialize all values in a dict for JSON transport."""
    return {k: _serialize_value(v) for k, v in data.items()}


def _build_remote_storage_path(
    monitoring_remote_uuid: str, frame_index: int, snapshot_type: str
) -> str:
    """Build deterministic remote path for snapshot images."""
    return f"monitorings/{monitoring_remote_uuid}/{snapshot_type}/snapshot_{frame_index:06d}.jpg"


def _remote_error_detail(
    error_type: Optional[str], error_message: Optional[str]
) -> str:
    """Build a safe error detail string from remote error fields."""
    if error_type and error_message:
        return f"{error_type}: {error_message}"
    if error_type:
        return error_type
    if error_message:
        return error_message
    return "UNKNOWN: unknown"


# ---------------------------------------------------------------------------
# Fields to NEVER send to remote
# ---------------------------------------------------------------------------

_EXCLUDED_FIELDS = frozenset({
    "remote_id",
    "remote_sync_status",
    "last_synced_at",
    "remote_sync_error",
    "sync_status",
})


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class RemoteSyncService:
    """Orchestrates entity synchronization from local SQLite to remote Supabase.

    Processes entities in strict parent-child order. Each entity gets a
    UUID reserved locally before remote write (crash-recovery safe).
    Individual failures do not abort the sync.
    """

    def __init__(
        self,
        remote_data: RemoteDataPort,
        remote_storage: RemoteStoragePort,
        sync_state: SyncStatePort,
        runtime_state: SyncRuntimeState,
        deletion_outbox: Optional[DeletionOutboxPort] = None,
    ) -> None:
        self._remote_data = remote_data
        self._remote_storage = remote_storage
        self._sync_state = sync_state
        self._runtime_state = runtime_state
        # Optional to keep existing constructor call sites/tests working.
        # When None, FASE 0 (durable-deletion propagation) is a no-op.
        # A later wiring task injects the real DeletionOutboxRepository.
        self._deletion_outbox = deletion_outbox

    def execute_sync(
        self,
        access_token: str,
        user_remote_id: str,
    ) -> SyncResult:
        """Execute full synchronization of pending entities.

        Args:
            access_token: Ephemeral JWT already obtained by caller.
            user_remote_id: Remote UUID of the authenticated user.

        Returns:
            SyncResult with counters and error details.
        """
        start = time.monotonic()

        # Defensive identity validation
        if not isinstance(user_remote_id, str) or not user_remote_id.strip():
            return SyncResult(
                success=False,
                entities_synced=0,
                entities_failed=0,
                images_uploaded=0,
                images_failed=0,
                errors=["Remote user identity is required for synchronization."],
                duration_seconds=time.monotonic() - start,
            )

        # Resolve the session's LOCAL user id ONCE (Spec 022). Fail closed: if
        # the remote identity maps to no local user, abort BEFORE FASE 0 and the
        # upsert phases without touching any row.
        session_local_user_id = self._sync_state.get_local_user_id_by_remote_id(
            user_remote_id
        )
        if session_local_user_id is None:
            return SyncResult(
                success=False,
                entities_synced=0,
                entities_failed=0,
                images_uploaded=0,
                images_failed=0,
                errors=[
                    "Could not resolve the local user for the remote identity; "
                    "synchronization aborted."
                ],
                duration_seconds=time.monotonic() - start,
            )

        entities_synced = 0
        entities_failed = 0
        images_uploaded = 0
        images_failed = 0
        errors: list[str] = []

        # FASE 0 — Durable deletions (BEFORE the upsert phases).
        # Propagating deletions first guarantees anti-resurrection: since the
        # Local_Cascade already removed the local records, the later upsert
        # phases never find the deleted entities and cannot re-upload them.
        # Scoped to the session's local user: only that user's outbox entries
        # are propagated; other users' and legacy NULL-owner entries are
        # untouched (no status change, no retry increment, no remote DELETE).
        deletions_synced, deletions_failed, del_errors = self._propagate_deletions(
            access_token, session_local_user_id
        )
        errors.extend(del_errors)

        for entity_type, remote_table in _PHASES:
            if entity_type == "snapshot":
                s, f, iu, im, errs = self._sync_snapshots(
                    access_token, user_remote_id, remote_table
                )
                entities_synced += s
                entities_failed += f
                images_uploaded += iu
                images_failed += im
                errors.extend(errs)
            else:
                s, f, errs = self._sync_entity_phase(
                    entity_type, remote_table, access_token, user_remote_id
                )
                entities_synced += s
                entities_failed += f
                errors.extend(errs)

        duration = time.monotonic() - start
        success = (
            entities_failed == 0
            and images_failed == 0
            and deletions_failed == 0
        )

        return SyncResult(
            success=success,
            entities_synced=entities_synced,
            entities_failed=entities_failed,
            images_uploaded=images_uploaded,
            images_failed=images_failed,
            errors=errors,
            duration_seconds=duration,
            deletions_synced=deletions_synced,
            deletions_failed=deletions_failed,
        )

    # ------------------------------------------------------------------
    # FASE 0 — Durable deletion propagation (before upsert phases)
    # ------------------------------------------------------------------

    def _propagate_deletions(
        self,
        access_token: str,
        owner_user_id: int,
    ) -> tuple[int, int, list[str]]:
        """Propagate durable local deletions to the remote backend (FASE 0).

        Runs BEFORE the upsert phases. Processes Deletion_Outbox entries that
        represent locally-completed deletions, emitting an idempotent root
        DELETE per entry (remote ON DELETE CASCADE removes descendants).

        Selection and gating are delegated to the port: only entries with
        local_delete_status='completed' whose remote status is
        pending/error/(persisted) syncing are returned, ordered by
        created_at ASC. Entries in 'prepared'/'failed' are NEVER returned and
        therefore NEVER propagated (Req 4.10, 9.3).

        Unidirectional local -> remote (Req 9.2): no remote -> local
        propagation is introduced. Token/lock ownership stays in sync_api.

        Returns:
            (deletions_synced, deletions_failed, errors)
        """
        synced = 0
        failed = 0
        errors: list[str] = []

        # No outbox wired (default None) -> FASE 0 is a no-op.
        if self._deletion_outbox is None:
            return synced, failed, errors

        entries = self._deletion_outbox.get_pending_for_propagation(owner_user_id)
        total = len(entries)
        processed = 0

        # Per-entry processing is wrapped so a failure on ONE entry never aborts
        # the whole FASE 0 (Req 9.5): a failed or unexpectedly-raising entry is
        # recorded (error + retry increment) and the loop proceeds to the
        # remaining entries. No entry is ever removed from the outbox (Req 9.4).
        for entry in entries:
            try:
                ok, err_detail = self._propagate_deletion_entry(entry, access_token)
                if ok:
                    synced += 1
                else:
                    # Failure detail already recorded (error + retry) inside the
                    # entry helper; only accumulate the summary error here.
                    failed += 1
                    if err_detail:
                        errors.append(
                            f"deletion outbox_id={entry.id} "
                            f"({entry.remote_table}): {err_detail}"
                        )
            except Exception as exc:
                # Unexpected exception on this entry: record it as error + retry
                # so retry tracking is consistent with the data/storage failure
                # paths, then continue with the remaining entries (Req 9.5).
                failed += 1
                err_msg = (
                    f"deletion outbox_id={getattr(entry, 'id', None)}: "
                    f"{type(exc).__name__}"
                )
                errors.append(err_msg)
                entry_id = getattr(entry, "id", None)
                if entry_id is not None:
                    try:
                        self._deletion_outbox.mark_error(entry_id, err_msg)
                        self._deletion_outbox.increment_retry_count(entry_id)
                    except Exception:
                        pass

            processed += 1
            self._runtime_state.update_progress("deletions", processed, total)

        return synced, failed, errors

    def _propagate_deletion_entry(
        self,
        entry: DeletionOutboxEntry,
        access_token: str,
    ) -> tuple[bool, Optional[str]]:
        """Propagate a single durable-deletion outbox entry.

        Emits the idempotent root data DELETE (already-absent = success, relying
        on remote ON DELETE CASCADE). An entry without a remote_id was never
        synced, so no remote row exists and the data DELETE is skipped.

        On data-delete success (or no remote_id), finalization is delegated to
        _propagate_storage_and_finalize, which removes the entry's Storage
        objects and marks the entry 'synced' only when the data DELETE and ALL
        storage paths are 'removed' (no orphaned objects). If any storage path
        cannot be removed, that helper marks the entry 'error' (retryable) and
        the entry is NOT marked 'synced'.

        On a retryable data-delete failure (connectivity / remote-unavailable /
        RLS), marks the entry 'error' (which records last_error with a UTC
        timestamp) AND increments retry_count, then returns WITHOUT removing the
        outbox entry (Req 9.4) and WITHOUT re-uploading the entity (Req 10.3).
        The entry stays retryable and is retried on the next manual sync because
        get_pending_for_propagation returns pending/error/(recovered) syncing
        entries (Req 11.1, 11.2). It is never marked 'synced' while incomplete.

        Anti-resurrection (Req 10.3): FASE 0 runs before the upsert phases and
        the Local_Cascade already removed the local rows, so a failed deletion
        cannot fall through to an upsert of the same entity (the upsert phases
        never find it). No re-creation / re-upload path is introduced here, and
        no remote -> local propagation exists.

        Returns:
            (success, error_detail) where error_detail is set only on failure.
        """
        self._deletion_outbox.mark_syncing(entry.id)

        if entry.remote_id:
            result = self._remote_data.delete_by_id(
                access_token, entry.remote_table, entry.remote_id
            )
            if not result.success:
                detail = _remote_error_detail(
                    result.error_type, result.error_message
                )
                # Record the failure: mark_error persists last_error with a UTC
                # timestamp; increment_retry_count tracks the failed attempt.
                # The entry is NEVER removed from the outbox (Req 9.4) and the
                # entity is NEVER re-uploaded (Req 10.3) -- it stays retryable
                # for the next sync (Req 11.1, 11.2, 11.3).
                self._deletion_outbox.mark_error(entry.id, detail)
                self._deletion_outbox.increment_retry_count(entry.id)
                return False, detail
        # If remote_id is None the entity was never synced remotely: there is
        # no remote row to remove, so the data DELETE is skipped and we proceed
        # straight to finalization.

        return self._propagate_storage_and_finalize(entry, access_token)

    def _propagate_storage_and_finalize(
        self,
        entry: DeletionOutboxEntry,
        access_token: str,
    ) -> tuple[bool, Optional[str]]:
        """Remove the entry's Storage objects, then finalize (FASE 0).

        Runs AFTER a successful data DELETE. Removes every remote Storage object
        associated with the entry so no Orphaned_Storage_Object remains, and
        only then marks the entry 'synced' (Req 8.4/8.5).

        Processing (Req 8.1-8.5):
          - Reads the entry's Storage-path rows. Both 'pending' AND 'error' rows
            are processed; an 'error' row from a previous run is retried here.
            'removed' is the only terminal state and is left untouched.
          - For each non-removed row, calls remove_object. Success or
            already-absent (idempotent) -> mark the row 'removed'. A retryable
            (non-not-found) failure -> mark the row 'error' and remember that
            not all rows are removed.
          - If ALL Storage-path rows are 'removed' (nothing left non-removed),
            mark the entry 'synced' (no orphans). Otherwise mark the entry
            'error' so it stays retryable and is NOT marked 'synced'.

        An entry with no Storage paths (never-synced or no snapshots) has nothing
        to clean and is marked 'synced'.

        Returns:
            (success, error_detail) where error_detail is set only on failure.
        """
        rows = self._deletion_outbox.get_storage_paths_for_entry(entry.id)

        all_removed = True
        last_detail: Optional[str] = None

        for row in rows:
            if row.status == "removed":
                # Terminal state: nothing to do, already clean.
                continue

            # Process both 'pending' and 'error' rows (an 'error' path is retried).
            result = self._remote_storage.remove_object(
                access_token, row.storage_path
            )
            if result.success:
                # Deleted now or already absent (idempotent) -> terminal 'removed'.
                self._deletion_outbox.mark_storage_path_status(row.id, "removed")
            else:
                # Retryable failure (non-not-found): keep the row retryable and
                # remember that the entry still has an orphaned object.
                all_removed = False
                last_detail = _remote_error_detail(
                    result.error_type, result.error_message
                )
                self._deletion_outbox.mark_storage_path_status(row.id, "error")

        if all_removed:
            # Data DELETE done and every Storage path removed -> no orphans.
            self._deletion_outbox.mark_synced(entry.id)
            return True, None

        # A Storage-path failure keeps the entry retryable: record the error
        # (mark_error persists last_error with a UTC timestamp) and increment
        # retry_count, consistently with the data-delete failure path. The entry
        # is NOT marked 'synced' and is NEVER removed from the outbox (Req 9.4);
        # it is retried on the next sync (Req 11.1, 11.2, 11.3).
        detail = f"storage cleanup incomplete: {last_detail or 'UNKNOWN'}"
        self._deletion_outbox.mark_error(entry.id, detail)
        self._deletion_outbox.increment_retry_count(entry.id)
        return False, detail

    # ------------------------------------------------------------------
    # Generic entity phase
    # ------------------------------------------------------------------

    def _sync_entity_phase(
        self,
        entity_type: str,
        remote_table: str,
        access_token: str,
        user_remote_id: str,
    ) -> tuple[int, int, list[str]]:
        """Process a single entity phase. Returns (synced, failed, errors)."""
        pending = self._sync_state.get_pending_entities(entity_type)
        total = len(pending)
        processed = 0
        synced = 0
        failed = 0
        phase_errors: list[str] = []

        # Compute pending parent IDs for dependency check
        parent_pending_ids: Optional[set] = None
        parent_type: Optional[str] = None
        parent_fk: Optional[str] = None

        if entity_type in _PARENT_DEPS:
            parent_type, parent_fk = _PARENT_DEPS[entity_type]
            parent_pending = self._sync_state.get_pending_entities(parent_type)
            parent_pending_ids = {row["id"] for row in parent_pending}

        for entity in pending:
            try:
                status, err_detail = self._process_entity(
                    entity_type, remote_table, entity, access_token,
                    user_remote_id, parent_type, parent_fk, parent_pending_ids,
                )
                if status == "synced":
                    synced += 1
                elif status == "failed":
                    failed += 1
                    phase_errors.append(
                        f"{entity_type} id={entity.get('id')}: {err_detail}"
                    )
                # "skipped" → neither synced nor failed
            except Exception as exc:
                failed += 1
                if isinstance(exc, ValueError):
                    err_msg = f"{entity_type} id={entity.get('id')}: {str(exc)}"
                else:
                    err_msg = f"{entity_type} id={entity.get('id')}: {type(exc).__name__}"
                phase_errors.append(err_msg)
                try:
                    self._sync_state.mark_error(
                        entity_type, entity["id"], err_msg
                    )
                except Exception:
                    pass

            processed += 1
            self._runtime_state.update_progress(remote_table, processed, total)

        return synced, failed, phase_errors

    def _process_entity(
        self,
        entity_type: str,
        remote_table: str,
        entity: dict,
        access_token: str,
        user_remote_id: str,
        parent_type: Optional[str],
        parent_fk: Optional[str],
        parent_pending_ids: Optional[set],
    ) -> tuple[str, Optional[str]]:
        """Process a single entity. Returns (status, error_detail).

        status is one of 'synced', 'failed', or 'skipped'.
        error_detail is populated only when status == 'failed'.
        """
        local_id = entity["id"]

        # User-scope guard (Spec 022): a sync processes ONLY the current user's
        # hierarchy. Resolve the entity's effective owner via its root
        # Greenhouse; if it belongs to a DIFFERENT user, skip silently
        # (no-touch): no UUID reserve, no mark_syncing, no error, no remote
        # write. A NULL/unresolved owner (effective is None) is NOT skipped here
        # so the greenhouse OWNER_MISSING/OWNER_NOT_SYNCED reporting still
        # applies to the user's own non-syncable rows. Likewise, if the
        # session's local user id cannot be resolved, we cannot positively
        # assert a DIFFERENT owner, so we do not skip on that basis.
        session_local_user_id = self._sync_state.get_local_user_id_by_remote_id(
            user_remote_id
        )
        effective_owner = self._sync_state.get_effective_owner_local_user_id(
            entity_type, local_id
        )
        if (
            effective_owner is not None
            and session_local_user_id is not None
            and effective_owner != session_local_user_id
        ):
            return "skipped", None

        # Check parent dependency
        parent_remote_id: Optional[str] = None
        if parent_type is not None and parent_fk is not None and parent_pending_ids is not None:
            parent_local_id = entity.get(parent_fk)
            if parent_local_id is None:
                return "skipped", None
            parent_remote_id = self._sync_state.get_remote_id(parent_type, parent_local_id)
            if parent_remote_id is None or parent_local_id in parent_pending_ids:
                return "skipped", None

        # Greenhouse ownership gate (Spec 022, Tasks 6.1/6.2): resolve the
        # owner's remote identity BEFORE any state mutation or remote write.
        # The remote payload must carry the owner's remote_user_id (UUID =
        # auth.uid()), never the local integer id. A greenhouse without a local
        # owner, or whose owner has no remote_user_id, cannot be synced: it is
        # reported via the existing error mechanism and skipped, letting the
        # existing per-entity fault isolation continue with other entities.
        owner_remote_uuid: Optional[str] = None
        if entity_type == "greenhouse":
            owner_local_id = entity.get("owner_user_id")
            if owner_local_id is None:
                err = "OWNER_MISSING: greenhouse has no local owner (owner_user_id is NULL)"
                self._sync_state.mark_error(entity_type, local_id, err)
                return "failed", err
            owner_remote_uuid = self._sync_state.get_user_remote_id(owner_local_id)
            if owner_remote_uuid is None:
                err = "OWNER_NOT_SYNCED: owner user has no remote_user_id"
                self._sync_state.mark_error(entity_type, local_id, err)
                return "failed", err

        # Get or reserve UUID
        remote_id = self._sync_state.get_remote_id(entity_type, local_id)
        if remote_id is None:
            remote_id = str(uuid.uuid4())
        self._sync_state.reserve_remote_id(entity_type, local_id, remote_id)
        self._sync_state.mark_syncing(entity_type, local_id)

        # Build payload
        payload = self._build_payload(
            entity_type, entity, remote_id, user_remote_id, parent_remote_id
        )
        if entity_type == "greenhouse":
            # Owner mapped local owner_user_id -> users.remote_user_id (UUID).
            payload["owner_user_id"] = owner_remote_uuid

        # Remote upsert
        result = self._remote_data.upsert(access_token, remote_table, payload)
        if not result.success:
            error_msg = f"{result.error_type}: {result.error_message or 'unknown'}"
            self._sync_state.mark_error(entity_type, local_id, error_msg)
            return "failed", error_msg

        self._sync_state.mark_synced(entity_type, local_id, remote_id)
        return "synced", None

    # ------------------------------------------------------------------
    # Snapshot phase (includes Storage)
    # ------------------------------------------------------------------

    def _sync_snapshots(
        self,
        access_token: str,
        user_remote_id: str,
        remote_table: str,
    ) -> tuple[int, int, int, int, list[str]]:
        """Process snapshots with Storage uploads.

        Returns: (synced, failed, images_uploaded, images_failed, errors)
        """
        pending = self._sync_state.get_pending_entities("snapshot")
        total = len(pending)
        processed = 0
        synced = 0
        failed = 0
        img_uploaded = 0
        img_failed = 0
        phase_errors: list[str] = []

        # Parent check: monitoring must be synced
        mon_pending = self._sync_state.get_pending_entities("monitoring")
        mon_pending_ids = {row["id"] for row in mon_pending}

        for snap in pending:
            try:
                result = self._process_snapshot(
                    snap, access_token, remote_table, mon_pending_ids, user_remote_id
                )
                # Always accumulate image counters regardless of entity status
                img_uploaded += result[1]
                img_failed += result[2]
                if result[0] == "synced":
                    synced += 1
                elif result[0] == "failed":
                    failed += 1
                    if result[3]:
                        phase_errors.append(result[3])
                # "skipped" → neither synced nor failed
            except Exception as exc:
                failed += 1
                err_msg = f"snapshot id={snap.get('id')}: {type(exc).__name__}"
                phase_errors.append(err_msg)
                try:
                    self._sync_state.mark_error("snapshot", snap["id"], err_msg)
                except Exception:
                    pass

            processed += 1
            self._runtime_state.update_progress(remote_table, processed, total)

        return synced, failed, img_uploaded, img_failed, phase_errors

    def _process_snapshot(
        self,
        snap: dict,
        access_token: str,
        remote_table: str,
        mon_pending_ids: set,
        user_remote_id: str,
    ) -> tuple[str, int, int, str]:
        """Process one snapshot. Returns (status, imgs_up, imgs_fail, error_msg)."""
        local_id = snap["id"]
        monitoring_local_id = snap.get("monitoring_id")

        # User-scope guard (Spec 022): skip snapshots that belong to another
        # user's hierarchy BEFORE reserving a UUID or uploading any file. Skip
        # only when a DIFFERENT local owner can be positively determined; an
        # unresolved effective owner or session user id does not trigger a skip.
        session_local_user_id = self._sync_state.get_local_user_id_by_remote_id(
            user_remote_id
        )
        effective_owner = self._sync_state.get_effective_owner_local_user_id(
            "snapshot", local_id
        )
        if (
            effective_owner is not None
            and session_local_user_id is not None
            and effective_owner != session_local_user_id
        ):
            return ("skipped", 0, 0, "")

        # Parent check
        if monitoring_local_id is None:
            return ("skipped", 0, 0, "")
        mon_remote_id = self._sync_state.get_remote_id("monitoring", monitoring_local_id)
        if mon_remote_id is None or monitoring_local_id in mon_pending_ids:
            return ("skipped", 0, 0, "")

        # Reserve UUID
        snapshot_remote_id = self._sync_state.get_remote_id("snapshot", local_id)
        if snapshot_remote_id is None:
            snapshot_remote_id = str(uuid.uuid4())
        self._sync_state.reserve_remote_id("snapshot", local_id, snapshot_remote_id)
        self._sync_state.mark_syncing("snapshot", local_id)

        frame_index = snap.get("frame_index", 0)
        raw_local_path = Path(snap.get("image_path", ""))

        # Check raw exists
        if not raw_local_path.is_file():
            err = f"snapshot id={local_id}: raw file not found"
            self._sync_state.mark_error("snapshot", local_id, err)
            return ("failed", 0, 1, err)

        # Upload raw
        raw_remote_path = _build_remote_storage_path(mon_remote_id, frame_index, "raw")
        raw_result = self._remote_storage.upload_file(
            access_token, str(raw_local_path), raw_remote_path
        )
        if not raw_result.success:
            err = f"snapshot id={local_id}: raw upload {_remote_error_detail(raw_result.error_type, raw_result.error_message)}"
            self._sync_state.mark_error("snapshot", local_id, err)
            return ("failed", 0, 1, err)

        imgs_uploaded = 1

        # Annotated (optional)
        annotated_remote_path: Optional[str] = None
        annotated_local = self._derive_annotated_path(raw_local_path, frame_index)
        if annotated_local is not None and annotated_local.is_file():
            ann_remote = _build_remote_storage_path(mon_remote_id, frame_index, "annotated")
            ann_result = self._remote_storage.upload_file(
                access_token, str(annotated_local), ann_remote
            )
            if not ann_result.success:
                err = f"snapshot id={local_id}: annotated upload {_remote_error_detail(ann_result.error_type, ann_result.error_message)}"
                self._sync_state.mark_error("snapshot", local_id, err)
                return ("failed", imgs_uploaded, 1, err)
            annotated_remote_path = ann_remote
            imgs_uploaded += 1

        # Metadata upsert
        payload = self._build_snapshot_payload(
            snap, snapshot_remote_id, mon_remote_id, raw_remote_path, annotated_remote_path
        )
        data_result = self._remote_data.upsert(access_token, remote_table, payload)
        if not data_result.success:
            err = f"snapshot id={local_id}: metadata {_remote_error_detail(data_result.error_type, data_result.error_message)}"
            self._sync_state.mark_error("snapshot", local_id, err)
            return ("failed", imgs_uploaded, 0, err)

        # Local checkpoints
        self._sync_state.set_storage_paths(local_id, raw_remote_path, annotated_remote_path)
        self._sync_state.mark_synced("snapshot", local_id, snapshot_remote_id)
        return ("synced", imgs_uploaded, 0, "")

    def _derive_annotated_path(
        self, raw_path: Path, frame_index: int
    ) -> Optional[Path]:
        """Derive annotated snapshot path from raw path using project convention.

        Convention: .../monitorings/<id>/annotated_snapshots/snapshot_NNNNNN.jpg
        Raw is at: .../monitorings/<id>/snapshots/raw/snapshot_NNNNNN.jpg

        Only derives if path structure matches expected hierarchy.
        """
        filename = f"snapshot_{frame_index:06d}.jpg"
        # Validate expected structure: parent must be "raw", grandparent must be "snapshots"
        if raw_path.parent.name != "raw":
            return None
        if raw_path.parent.parent.name != "snapshots":
            return None
        monitoring_dir = raw_path.parent.parent.parent
        return monitoring_dir / "annotated_snapshots" / filename

    # ------------------------------------------------------------------
    # Payload builders
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        entity_type: str,
        entity: dict,
        remote_id: str,
        user_remote_id: str,
        parent_remote_id: Optional[str],
    ) -> dict:
        """Build the remote payload for a generic entity."""
        if entity_type == "greenhouse":
            return self._build_greenhouse_payload(entity, remote_id)
        elif entity_type == "module":
            return self._build_module_payload(entity, remote_id, parent_remote_id)
        elif entity_type == "monitoring":
            return self._build_monitoring_payload(entity, remote_id, parent_remote_id, user_remote_id)
        elif entity_type == "monitoring_metrics":
            return self._build_metrics_payload(entity, remote_id, parent_remote_id)
        elif entity_type == "inspection_result":
            return self._build_inspection_result_payload(entity, remote_id, parent_remote_id)
        elif entity_type == "activity_log":
            return self._build_activity_log_payload(entity, remote_id, parent_remote_id, user_remote_id)
        else:
            return {"id": remote_id}

    def _build_greenhouse_payload(self, entity: dict, remote_id: str) -> dict:
        payload: dict = {"id": remote_id}
        for key in ("name", "location", "created_at", "updated_at"):
            if key in entity:
                payload[key] = _serialize_value(entity[key])
        return payload

    def _build_module_payload(
        self, entity: dict, remote_id: str, greenhouse_remote_id: Optional[str]
    ) -> dict:
        payload: dict = {
            "id": remote_id,
            "greenhouse_id": greenhouse_remote_id,
        }
        for key in ("name", "crop_type", "width_m", "length_m",
                    "monitoring_frequency_days", "created_at", "updated_at"):
            if key in entity:
                payload[key] = _serialize_value(entity[key])
        return payload

    def _build_monitoring_payload(
        self, entity: dict, remote_id: str,
        module_remote_id: Optional[str], user_remote_id: str,
    ) -> dict:
        payload: dict = {
            "id": remote_id,
            "module_id": module_remote_id,
        }
        for key in ("status", "started_at", "completed_at", "width_m", "length_m",
                    "notes", "total_snapshots", "total_detections"):
            if key in entity:
                payload[key] = _serialize_value(entity[key])

        # created_by_user_id: map local int to user_remote_id
        if entity.get("created_by_user_id") is not None:
            payload["created_by_user_id"] = user_remote_id
        else:
            payload["created_by_user_id"] = None

        return payload

    def _build_metrics_payload(
        self, entity: dict, remote_id: str, monitoring_remote_id: Optional[str]
    ) -> dict:
        payload: dict = {
            "id": remote_id,
            "monitoring_id": monitoring_remote_id,
        }
        for key in ("total_tomatoes", "healthy_count", "unhealthy_count",
                    "pct_healthy", "pct_unhealthy", "pct_green", "pct_breaker",
                    "pct_turning", "pct_pink", "pct_light_red", "pct_red",
                    "snapshots_with_detections", "computed_at"):
            if key in entity:
                payload[key] = _serialize_value(entity[key])
        return payload

    def _build_snapshot_payload(
        self,
        snap: dict,
        remote_id: str,
        monitoring_remote_id: str,
        raw_remote_path: str,
        annotated_remote_path: Optional[str],
    ) -> dict:
        payload: dict = {
            "id": remote_id,
            "monitoring_id": monitoring_remote_id,
            "local_image_path": snap.get("image_path"),
            "raw_storage_path": raw_remote_path,
            "annotated_storage_path": annotated_remote_path,
        }
        for key in ("captured_at", "frame_index", "change_score", "has_detections"):
            if key in snap:
                payload[key] = _serialize_value(snap[key])
        return payload

    def _build_inspection_result_payload(
        self, entity: dict, remote_id: str, snapshot_remote_id: Optional[str]
    ) -> dict:
        payload: dict = {
            "id": remote_id,
            "snapshot_id": snapshot_remote_id,
        }
        for key in ("detection_index", "bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2",
                    "detection_score", "health_label", "health_confidence",
                    "maturity_stage", "maturity_percent", "created_at"):
            if key in entity:
                payload[key] = _serialize_value(entity[key])
        return payload

    def _build_activity_log_payload(
        self, entity: dict, remote_id: str,
        module_remote_id: Optional[str], user_remote_id: str,
    ) -> dict:
        activity_type_code = entity.get("activity_type_code")
        if not activity_type_code:
            raise ValueError("Missing activity_type_code for activity_log sync")

        payload: dict = {
            "id": remote_id,
            "module_id": module_remote_id,
            "user_id": user_remote_id,
            "activity_type_code": activity_type_code,
        }
        for key in ("product_name", "quantity", "unit", "notes",
                    "occurred_at", "created_at"):
            if key in entity:
                payload[key] = _serialize_value(entity[key])
        return payload
