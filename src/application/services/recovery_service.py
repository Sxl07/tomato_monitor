"""Application service: RecoveryService (Spec 022, block D2).

Hierarchical metadata recovery from a remote store (e.g. Supabase rows) into
the local SQLite database. This service reconstructs the local hierarchy
(greenhouses -> modules -> monitorings -> monitoring_metrics -> snapshots ->
inspection_results -> activity_logs) from RLS-visible remote rows, preserving
remote UUIDs in the local ``remote_id`` columns while assigning fresh local
integer primary keys.

Scope (metadata + snapshot images):
    - Metadata recovery (D2) plus physical snapshot image download (D3.2). Raw
      and annotated snapshot images are fetched from remote Storage AFTER the
      snapshot metadata phase closes (never while a local unit-of-work is open)
      and written atomically under OUTPUTS_DIR via a RecoveryFilePort. Videos
      are out of scope.
    - Anti-resurrection (Spec 022, D3.1): before recovering or reusing a remote
      entity the service consults a RecoveryTombstonePort. A blocking tombstone
      (pending | syncing | error) on the exact remote identity prevents the row
      from being reused/inserted/mapped, and its descendants are blocked via a
      per-run blocked-lineage marker. A failed tombstone lookup fails safe (the
      row is skipped and its subtree blocked) without aborting the whole run.
    - NO API/UI/auth/wiring. Authentication is assumed already resolved.
    - Import-missing-only: this service NEVER updates, merges, reparents, or
      duplicates an existing local row. It only inserts missing rows or reuses
      already-present ones.

Session discipline (Spec 022, D2.1):
    - The remote read of a whole table happens with NO local unit-of-work open.
    - Each remote row is processed inside its OWN unit-of-work (fresh session),
      always closed before the next row and before the next phase's remote read.
      A failed row therefore cannot contaminate the session used by the next
      row, and no SQLite transaction is ever held open across a remote request.

Dependencies are abstract ports plus stdlib only. This module does not import
FastAPI, httpx, SQLAlchemy, Supabase, or any filesystem primitives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from src.application.interfaces.recovery_file_port import RecoveryFilePort
from src.application.interfaces.recovery_tombstone_port import (
    RecoveryTombstonePort,
    TombstoneCheckResult,
)
from src.application.interfaces.recovery_unit_of_work_port import (
    RecoveryUnitOfWorkFactory,
)
from src.application.interfaces.remote_download_port import RemoteDownloadPort
from src.application.interfaces.remote_read_port import RemoteReadPort
from src.application.interfaces.sync_state_port import SyncStatePort
from src.domain.entities.activity_log import ActivityLog
from src.domain.entities.greenhouse import Greenhouse
from src.domain.entities.inspection_result import DetectionInspectionResult
from src.domain.entities.module import Module
from src.domain.entities.monitoring import Monitoring
from src.domain.entities.monitoring_metrics import MonitoringMetrics
from src.domain.entities.snapshot import Snapshot
from src.domain.exceptions import RecoveredEntityAlreadyExistsError


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class RecoveryIssue:
    """A single non-fatal issue encountered while recovering a row.

    Attributes:
        entity_type: The remote table / entity type the issue relates to.
        remote_id: The remote UUID of the offending row, when available.
        code: A short machine-readable classification code.
        message: A short, safe human-readable description (no secrets/JWT).
    """

    entity_type: str
    remote_id: Optional[str]
    code: str
    message: str


@dataclass
class RecoveryResult:
    """Aggregated outcome of a recovery run.

    ``success`` reflects whether all phases could be READ from the remote store.
    Skipped rows, conflicts, and per-row errors do NOT flip ``success`` to
    False; only a failed remote read of a phase does.
    """

    success: bool
    entities_recovered: int = 0
    entities_reused: int = 0
    entities_skipped: int = 0
    conflicts: int = 0
    errors: list[RecoveryIssue] = field(default_factory=list)
    # Snapshot image download counters (Spec 022, D3.2). Invariant:
    # images_downloaded + images_skipped + images_failed == number of non-null,
    # validly-declared Storage object paths expected across recovered/reused
    # snapshots (each of raw_storage_path / annotated_storage_path present
    # contributes exactly one expected object).
    images_downloaded: int = 0
    images_skipped: int = 0
    images_failed: int = 0


@dataclass
class _SnapshotDownloadJob:
    """In-memory description of one snapshot image to download (D3.2).

    Produced while the snapshot metadata phase runs (inside each per-row UoW)
    but consumed only AFTER all snapshot units-of-work have closed, so no HTTP
    ever happens while a local transaction is open. Holds plain values only:
    no ORM entities and no sessions.
    """

    snapshot_remote_id: str
    monitoring_remote_id: str
    local_monitoring_id: int
    frame_index: int
    snapshot_type: str  # "raw" | "annotated"
    remote_path: str
    local_relative_path: str


# ---------------------------------------------------------------------------
# Recovery service
# ---------------------------------------------------------------------------

# Entity-type keys used in the remote_to_local parent map. Only parents that
# have children need an entry.
_GREENHOUSE = "greenhouse"
_MODULE = "module"
_MONITORING = "monitoring"
_SNAPSHOT = "snapshot"

# Remote table names, in strict phase order.
_TABLE_GREENHOUSES = "greenhouses"
_TABLE_MODULES = "modules"
_TABLE_MONITORINGS = "monitorings"
_TABLE_MONITORING_METRICS = "monitoring_metrics"
_TABLE_SNAPSHOTS = "snapshots"
_TABLE_INSPECTION_RESULTS = "inspection_results"
_TABLE_ACTIVITY_LOGS = "activity_logs"

# Explicit map from a recovery-phase remote table to the deletion_outbox
# entity_type that could DIRECTLY tombstone it (Spec 022, D3.1). Only these
# three tables have a direct tombstone type; there is NO auto-pluralization.
# Tables NOT listed here (monitoring_metrics, snapshots, inspection_results,
# activity_logs) have NO direct tombstone type — they are blocked only via
# blocked lineage (an ancestor being tombstoned). Do NOT invent tombstone
# types for them.
_TABLE_TO_TOMBSTONE_ENTITY_TYPE = {
    _TABLE_GREENHOUSES: "greenhouse",
    _TABLE_MODULES: "module",
    _TABLE_MONITORINGS: "monitoring",
}


class RecoveryService:
    """Recovers the local hierarchy from remote metadata rows.

    Reads one full RLS-visible table per phase (with no local unit-of-work
    open), persists each row durably inside its own per-row unit-of-work, then
    moves to the next phase. If a phase's remote read fails, remaining phases
    are skipped, rows already inserted in prior phases remain, and the run is
    reported as ``success=False``.
    """

    def __init__(
        self,
        remote_read: RemoteReadPort,
        uow_factory: RecoveryUnitOfWorkFactory,
        sync_state: SyncStatePort,
        tombstone_guard: RecoveryTombstonePort,
        remote_download: RemoteDownloadPort,
        recovery_files: RecoveryFilePort,
    ) -> None:
        self._remote_read = remote_read
        self._uow_factory = uow_factory
        self._sync_state = sync_state
        self._tombstone_guard = tombstone_guard
        self._remote_download = remote_download
        self._recovery_files = recovery_files

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def execute_recovery(
        self,
        access_token: str,
        remote_user_id: str,
        local_user_id: int,
    ) -> RecoveryResult:
        """Recover the full hierarchy for a user from the remote store.

        Args:
            access_token: Ephemeral JWT (already resolved; never re-authenticated).
            remote_user_id: Remote owner UUID (auth.uid()).
            local_user_id: Local users.id for the authenticated operator.

        Returns:
            RecoveryResult summarizing recovered/reused/skipped/conflict counts.
        """
        result = RecoveryResult(success=True)
        # (entity_type, remote_uuid) -> local int id. Only parents are tracked.
        remote_to_local: dict[tuple[str, str], int] = {}
        # Snapshot image download jobs collected during the snapshot metadata
        # phase and processed AFTER that phase's UoWs all close (no HTTP inside
        # a local unit-of-work).
        download_jobs: list[_SnapshotDownloadJob] = []
        # Per-run blocked lineage: (parent_map_key, remote_uuid) of any entity
        # that was tombstoned, failed its tombstone check, or descends from such
        # an entity. Keyed with the SAME parent map keys used in remote_to_local
        # (_GREENHOUSE, _MODULE, _MONITORING, _SNAPSHOT). Descendants consult it
        # to skip with PARENT_TOMBSTONED and keep propagating the marker.
        blocked_lineage: set[tuple[str, str]] = set()
        # Per-run cache of tombstone checks, keyed by (outbox_entity_type,
        # remote_id). Lives only for this run so a remote identity is queried at
        # most once.
        tombstone_cache: dict[tuple[str, str], TombstoneCheckResult] = {}

        phases = (
            (_TABLE_GREENHOUSES, self._recover_greenhouse_row),
            (_TABLE_MODULES, self._recover_module_row),
            (_TABLE_MONITORINGS, self._recover_monitoring_row),
            (_TABLE_MONITORING_METRICS, self._recover_metrics_row),
            (_TABLE_SNAPSHOTS, self._recover_snapshot_row),
            (_TABLE_INSPECTION_RESULTS, self._recover_inspection_row),
            (_TABLE_ACTIVITY_LOGS, self._recover_activity_log_row),
        )

        for table, handler in phases:
            # Remote read of the whole table happens with NO local UoW open.
            query = self._remote_read.fetch_by_owner(
                access_token, table, remote_user_id
            )
            if not query.success:
                error_type = query.error_type or "UNKNOWN"
                result.success = False
                result.errors.append(
                    RecoveryIssue(
                        entity_type=table,
                        remote_id=None,
                        code=f"REMOTE_READ_{error_type}",
                        message=f"Remote read of '{table}' failed ({error_type}).",
                    )
                )
                # Stop remaining phases; prior inserts remain (no rollback).
                return result

            # One unit-of-work PER ROW: a fresh session is opened and closed
            # around processing a single row, before the next row / phase.
            for row in query.rows:
                with self._uow_factory() as uow:
                    handler(
                        row,
                        result,
                        remote_to_local,
                        remote_user_id,
                        local_user_id,
                        uow,
                        blocked_lineage,
                        tombstone_cache,
                        download_jobs,
                    )

            # After ALL snapshot metadata UoWs are closed and durable, download
            # the collected images. No local unit-of-work is open here, and this
            # runs before the next phase's remote read (inspection_results).
            if table == _TABLE_SNAPSHOTS:
                self._process_download_jobs(access_token, download_jobs, result)

        return result

    # ------------------------------------------------------------------
    # Snapshot image download (Spec 022, D3.2)
    # ------------------------------------------------------------------

    def _process_download_jobs(
        self,
        access_token: str,
        jobs: list["_SnapshotDownloadJob"],
        result: RecoveryResult,
    ) -> None:
        """Download and atomically write each collected snapshot image.

        Called with NO local unit-of-work open. Per job: skip if already local,
        re-validate the remote path is exactly the deterministic path, download,
        then atomically write. Individual failures never abort the run, roll
        back metadata, or delete previously written files.
        """
        for job in jobs:
            # A) Idempotence: already present locally -> no HTTP.
            if self._recovery_files.exists(job.local_relative_path):
                result.images_skipped += 1
                continue

            # B) Defense-in-depth: the stored remote path must EXACTLY equal the
            # deterministic path for this monitoring + type + frame_index.
            expected = _expected_remote_path(
                job.monitoring_remote_id, job.snapshot_type, job.frame_index
            )
            if job.remote_path != expected:
                result.images_failed += 1
                result.errors.append(
                    RecoveryIssue(
                        entity_type=_TABLE_SNAPSHOTS,
                        remote_id=job.snapshot_remote_id,
                        code="IMAGE_REMOTE_PATH_INVALID",
                        message=(
                            f"Stored {job.snapshot_type} path does not match the "
                            "expected deterministic path; not downloaded."
                        ),
                    )
                )
                continue

            # C) Download the object.
            download = self._remote_download.download_object(
                access_token, job.remote_path
            )

            if download.success:
                if not isinstance(download.content, (bytes, bytearray)):
                    result.images_failed += 1
                    result.errors.append(
                        RecoveryIssue(
                            entity_type=_TABLE_SNAPSHOTS,
                            remote_id=job.snapshot_remote_id,
                            code="IMAGE_WRITE_FAILED",
                            message="Download reported success without bytes.",
                        )
                    )
                    continue
                written = self._recovery_files.write_atomic(
                    job.local_relative_path, bytes(download.content)
                )
                if written.success:
                    if written.already_exists:
                        result.images_skipped += 1
                    else:
                        result.images_downloaded += 1
                else:
                    result.images_failed += 1
                    result.errors.append(
                        RecoveryIssue(
                            entity_type=_TABLE_SNAPSHOTS,
                            remote_id=job.snapshot_remote_id,
                            code="IMAGE_WRITE_FAILED",
                            message="Failed to write recovered image locally.",
                        )
                    )
                continue

            # D) NOT_FOUND is a skip (object absent), not a hard failure.
            error_type = download.error_type or "UNKNOWN"
            if error_type == "NOT_FOUND":
                result.images_skipped += 1
                result.errors.append(
                    RecoveryIssue(
                        entity_type=_TABLE_SNAPSHOTS,
                        remote_id=job.snapshot_remote_id,
                        code="IMAGE_NOT_FOUND",
                        message="Remote object not found; skipped.",
                    )
                )
                continue

            # All other download errors: count as failed, keep going.
            result.images_failed += 1
            result.errors.append(
                RecoveryIssue(
                    entity_type=_TABLE_SNAPSHOTS,
                    remote_id=job.snapshot_remote_id,
                    code=f"IMAGE_DOWNLOAD_{error_type}",
                    message=f"Image download failed ({error_type}).",
                )
            )

    # ------------------------------------------------------------------
    # Anti-resurrection helper
    # ------------------------------------------------------------------

    def _check_tombstone(
        self,
        entity_type: str,
        remote_id: str,
        cache: dict[tuple[str, str], TombstoneCheckResult],
    ) -> TombstoneCheckResult:
        """Consult the per-run cache, else the tombstone guard, then cache it.

        A single remote identity is looked up at most once per run.
        """
        key = (entity_type, remote_id)
        cached = cache.get(key)
        if cached is not None:
            return cached
        checked = self._tombstone_guard.check(entity_type, remote_id)
        cache[key] = checked
        return checked

    # ------------------------------------------------------------------
    # Phase handlers (one row at a time, fault-isolated, per-row UoW)
    # ------------------------------------------------------------------

    def _recover_greenhouse_row(
        self, row, result, remote_to_local, remote_user_id, local_user_id, uow,
        blocked_lineage, tombstone_cache, download_jobs
    ) -> None:
        remote_id = row.get("id")
        try:
            if not _is_valid_uuid(remote_id):
                self._skip(result, _TABLE_GREENHOUSES, remote_id, "INVALID_REMOTE_ID",
                           "Missing or invalid remote id.")
                return

            # Anti-resurrection: a blocking (or unresolvable) tombstone on this
            # greenhouse prevents recovery/reuse and blocks the whole subtree.
            if self._tombstone_blocks(
                _TABLE_GREENHOUSES, remote_id, result, blocked_lineage,
                tombstone_cache, own_map_key=_GREENHOUSE,
            ):
                return

            # Defense-in-depth: greenhouses carry an explicit owner column.
            if row.get("owner_user_id") != remote_user_id:
                self._conflict(result, _TABLE_GREENHOUSES, remote_id, "OWNER_MISMATCH",
                               "Remote owner does not match the requesting user.")
                return

            existing = uow.greenhouse_repo.find_by_remote_id(remote_id)
            if existing is not None:
                if existing.owner_user_id == local_user_id:
                    result.entities_reused += 1
                    remote_to_local[(_GREENHOUSE, remote_id)] = existing.id
                else:
                    self._conflict(result, _TABLE_GREENHOUSES, remote_id,
                                   "LOCAL_OWNER_MISMATCH",
                                   "Existing local greenhouse belongs to another user.")
                return

            name = row.get("name")
            natural = uow.greenhouse_repo.find_by_owner_and_name(local_user_id, name)
            if natural is not None:
                self._conflict(result, _TABLE_GREENHOUSES, remote_id,
                               "NATURAL_KEY_CONFLICT",
                               "A local greenhouse with the same name already exists.")
                return

            created_at = self._parse_ts(row.get("created_at"))
            updated_at = self._parse_ts(row.get("updated_at"))
            if self._ts_invalid(row.get("created_at"), created_at) or self._ts_invalid(
                row.get("updated_at"), updated_at
            ):
                self._skip(result, _TABLE_GREENHOUSES, remote_id, "INVALID_TIMESTAMP",
                           "Unparseable timestamp on greenhouse row.")
                return

            entity = Greenhouse(
                owner_user_id=local_user_id,
                name=name,
                location=row.get("location"),
                created_at=created_at,
                updated_at=updated_at,
            )
            created = uow.greenhouse_repo.insert_preserving_remote_id(entity, remote_id)
            result.entities_recovered += 1
            remote_to_local[(_GREENHOUSE, remote_id)] = created.id
        except RecoveredEntityAlreadyExistsError:
            # Race re-query using this row's uow: reuse if consistent.
            existing = uow.greenhouse_repo.find_by_remote_id(remote_id)
            if existing is not None and existing.owner_user_id == local_user_id:
                result.entities_reused += 1
                remote_to_local[(_GREENHOUSE, remote_id)] = existing.id
            else:
                self._conflict(result, _TABLE_GREENHOUSES, remote_id,
                               "LOCAL_OWNER_MISMATCH", "Race on existing greenhouse row.")
        except Exception as exc:  # noqa: BLE001 - per-row fault isolation
            self._row_error(result, _TABLE_GREENHOUSES, remote_id, exc)

    def _recover_module_row(
        self, row, result, remote_to_local, remote_user_id, local_user_id, uow,
        blocked_lineage, tombstone_cache, download_jobs
    ) -> None:
        remote_id = row.get("id")
        try:
            if not _is_valid_uuid(remote_id):
                self._skip(result, _TABLE_MODULES, remote_id, "INVALID_REMOTE_ID",
                           "Missing or invalid remote id.")
                return

            # Anti-resurrection: direct tombstone on this module (module is both
            # a child of greenhouse and a parent of monitorings/activity_logs).
            if self._tombstone_blocks(
                _TABLE_MODULES, remote_id, result, blocked_lineage,
                tombstone_cache, own_map_key=_MODULE,
            ):
                return

            remote_parent = row.get("greenhouse_id")
            if not _is_valid_uuid(remote_parent):
                self._skip(result, _TABLE_MODULES, remote_id, "INVALID_REMOTE_ID",
                           "Missing or invalid remote greenhouse_id.")
                return

            # Blocked-lineage: parent greenhouse tombstoned/failed -> block this
            # module and keep propagating the marker down to its own subtree.
            if self._lineage_blocks(
                _TABLE_MODULES, remote_id, (_GREENHOUSE, remote_parent),
                result, blocked_lineage, own_map_key=_MODULE,
            ):
                return

            local_greenhouse_id = remote_to_local.get((_GREENHOUSE, remote_parent))
            if local_greenhouse_id is None:
                self._skip(result, _TABLE_MODULES, remote_id, "PARENT_UNRESOLVED",
                           "Parent greenhouse was not recovered.")
                return

            existing = uow.module_repo.find_by_remote_id(remote_id)
            if existing is not None:
                if existing.greenhouse_id == local_greenhouse_id:
                    result.entities_reused += 1
                    remote_to_local[(_MODULE, remote_id)] = existing.id
                else:
                    self._conflict(result, _TABLE_MODULES, remote_id, "PARENT_MISMATCH",
                                   "Existing local module has a different parent.")
                return

            name = row.get("name")
            for sibling in uow.module_repo.get_by_greenhouse(local_greenhouse_id):
                if sibling.name == name:
                    self._conflict(result, _TABLE_MODULES, remote_id,
                                   "NATURAL_KEY_CONFLICT",
                                   "A local module with the same name already exists.")
                    return

            created_at = self._parse_ts(row.get("created_at"))
            updated_at = self._parse_ts(row.get("updated_at"))
            if self._ts_invalid(row.get("created_at"), created_at) or self._ts_invalid(
                row.get("updated_at"), updated_at
            ):
                self._skip(result, _TABLE_MODULES, remote_id, "INVALID_TIMESTAMP",
                           "Unparseable timestamp on module row.")
                return

            entity = Module(
                greenhouse_id=local_greenhouse_id,
                name=name,
                crop_type=row.get("crop_type") or "Tomate Cherry",
                width_m=row.get("width_m"),
                length_m=row.get("length_m"),
                monitoring_frequency_days=row.get("monitoring_frequency_days"),
                created_at=created_at,
                updated_at=updated_at,
            )
            created = uow.module_repo.insert_preserving_remote_id(
                local_greenhouse_id, entity, remote_id
            )
            result.entities_recovered += 1
            remote_to_local[(_MODULE, remote_id)] = created.id
        except RecoveredEntityAlreadyExistsError:
            self._handle_race_child(
                uow.module_repo, result, remote_to_local, _MODULE, _TABLE_MODULES,
                remote_id, "greenhouse_id", local_greenhouse_id,
            )
        except Exception as exc:  # noqa: BLE001
            self._row_error(result, _TABLE_MODULES, remote_id, exc)

    def _recover_monitoring_row(
        self, row, result, remote_to_local, remote_user_id, local_user_id, uow,
        blocked_lineage, tombstone_cache, download_jobs
    ) -> None:
        remote_id = row.get("id")
        try:
            if not _is_valid_uuid(remote_id):
                self._skip(result, _TABLE_MONITORINGS, remote_id, "INVALID_REMOTE_ID",
                           "Missing or invalid remote id.")
                return

            # Anti-resurrection: direct tombstone on this monitoring (child of
            # module, parent of metrics/snapshots).
            if self._tombstone_blocks(
                _TABLE_MONITORINGS, remote_id, result, blocked_lineage,
                tombstone_cache, own_map_key=_MONITORING,
            ):
                return

            remote_parent = row.get("module_id")
            if not _is_valid_uuid(remote_parent):
                self._skip(result, _TABLE_MONITORINGS, remote_id, "INVALID_REMOTE_ID",
                           "Missing or invalid remote module_id.")
                return

            # Blocked-lineage: parent module tombstoned/failed -> block this
            # monitoring and keep propagating to its metrics/snapshot subtree.
            if self._lineage_blocks(
                _TABLE_MONITORINGS, remote_id, (_MODULE, remote_parent),
                result, blocked_lineage, own_map_key=_MONITORING,
            ):
                return

            local_module_id = remote_to_local.get((_MODULE, remote_parent))
            if local_module_id is None:
                self._skip(result, _TABLE_MONITORINGS, remote_id, "PARENT_UNRESOLVED",
                           "Parent module was not recovered.")
                return

            # REUSE before interpreting creator / timestamps / business fields.
            existing = uow.monitoring_repo.find_by_remote_id(remote_id)
            if existing is not None:
                if existing.module_id == local_module_id:
                    result.entities_reused += 1
                    remote_to_local[(_MONITORING, remote_id)] = existing.id
                else:
                    self._conflict(result, _TABLE_MONITORINGS, remote_id,
                                   "PARENT_MISMATCH",
                                   "Existing local monitoring has a different parent.")
                return

            # Only when missing: resolve the local creator (nullable).
            remote_creator = row.get("created_by_user_id")
            local_creator: Optional[int]
            if remote_creator is None:
                local_creator = None
            else:
                local_creator = self._sync_state.get_local_user_id_by_remote_id(
                    remote_creator
                )
                if local_creator is None:
                    self._skip(result, _TABLE_MONITORINGS, remote_id,
                               "USER_MAPPING_NOT_FOUND",
                               "Remote creator has no local user mapping.")
                    return

            started_at = self._parse_ts(row.get("started_at"))
            completed_at = self._parse_ts(row.get("completed_at"))
            if self._ts_invalid(row.get("started_at"), started_at) or self._ts_invalid(
                row.get("completed_at"), completed_at
            ):
                self._skip(result, _TABLE_MONITORINGS, remote_id, "INVALID_TIMESTAMP",
                           "Unparseable timestamp on monitoring row.")
                return

            entity = Monitoring(
                module_id=local_module_id,
                status=row.get("status") or "initializing",
                started_at=started_at,
                completed_at=completed_at,
                width_m=row.get("width_m"),
                length_m=row.get("length_m"),
                notes=row.get("notes"),
                total_snapshots=row.get("total_snapshots") or 0,
                total_detections=row.get("total_detections") or 0,
                created_by_user_id=local_creator,
                sync_status="synced",
                video_path=None,
            )
            created = uow.monitoring_repo.insert_preserving_remote_id(
                local_module_id, entity, remote_id
            )
            result.entities_recovered += 1
            remote_to_local[(_MONITORING, remote_id)] = created.id
        except RecoveredEntityAlreadyExistsError:
            self._handle_race_child(
                uow.monitoring_repo, result, remote_to_local, _MONITORING,
                _TABLE_MONITORINGS, remote_id, "module_id", local_module_id,
            )
        except Exception as exc:  # noqa: BLE001
            self._row_error(result, _TABLE_MONITORINGS, remote_id, exc)

    def _recover_metrics_row(
        self, row, result, remote_to_local, remote_user_id, local_user_id, uow,
        blocked_lineage, tombstone_cache, download_jobs
    ) -> None:
        remote_id = row.get("id")
        try:
            if not _is_valid_uuid(remote_id):
                self._skip(result, _TABLE_MONITORING_METRICS, remote_id,
                           "INVALID_REMOTE_ID", "Missing or invalid remote id.")
                return

            # No direct tombstone type for monitoring_metrics: it can only be
            # blocked via its ancestor monitoring's lineage (checked below).
            remote_parent = row.get("monitoring_id")
            if not _is_valid_uuid(remote_parent):
                self._skip(result, _TABLE_MONITORING_METRICS, remote_id,
                           "INVALID_REMOTE_ID", "Missing or invalid remote monitoring_id.")
                return

            # Blocked-lineage: parent monitoring tombstoned/failed. Metrics is a
            # leaf, so no own lineage key is registered.
            if self._lineage_blocks(
                _TABLE_MONITORING_METRICS, remote_id, (_MONITORING, remote_parent),
                result, blocked_lineage, own_map_key=None,
            ):
                return

            local_monitoring_id = remote_to_local.get((_MONITORING, remote_parent))
            if local_monitoring_id is None:
                self._skip(result, _TABLE_MONITORING_METRICS, remote_id,
                           "PARENT_UNRESOLVED", "Parent monitoring was not recovered.")
                return

            existing = uow.monitoring_metrics_repo.find_by_remote_id(remote_id)
            if existing is not None:
                if existing.monitoring_id == local_monitoring_id:
                    result.entities_reused += 1  # leaf: no map needed
                else:
                    self._conflict(result, _TABLE_MONITORING_METRICS, remote_id,
                                   "PARENT_MISMATCH",
                                   "Existing local metrics have a different parent.")
                return

            # 1:1 natural conflict: monitoring already has metrics.
            if uow.monitoring_metrics_repo.get_by_monitoring(local_monitoring_id):
                self._conflict(result, _TABLE_MONITORING_METRICS, remote_id,
                               "NATURAL_KEY_CONFLICT",
                               "Local monitoring already has metrics.")
                return

            computed_at = self._parse_ts(row.get("computed_at"))
            if self._ts_invalid(row.get("computed_at"), computed_at):
                self._skip(result, _TABLE_MONITORING_METRICS, remote_id,
                           "INVALID_TIMESTAMP", "Unparseable timestamp on metrics row.")
                return

            entity = MonitoringMetrics(
                monitoring_id=local_monitoring_id,
                total_tomatoes=row.get("total_tomatoes") or 0,
                healthy_count=row.get("healthy_count") or 0,
                unhealthy_count=row.get("unhealthy_count") or 0,
                pct_healthy=row.get("pct_healthy") or 0.0,
                pct_unhealthy=row.get("pct_unhealthy") or 0.0,
                snapshots_with_detections=row.get("snapshots_with_detections") or 0,
                pct_green=row.get("pct_green") or 0.0,
                pct_breaker=row.get("pct_breaker") or 0.0,
                pct_turning=row.get("pct_turning") or 0.0,
                pct_pink=row.get("pct_pink") or 0.0,
                pct_light_red=row.get("pct_light_red") or 0.0,
                pct_red=row.get("pct_red") or 0.0,
                computed_at=computed_at,
            )
            uow.monitoring_metrics_repo.insert_preserving_remote_id(
                local_monitoring_id, entity, remote_id
            )
            result.entities_recovered += 1
        except RecoveredEntityAlreadyExistsError:
            existing = uow.monitoring_metrics_repo.find_by_remote_id(remote_id)
            local_parent = remote_to_local.get((_MONITORING, row.get("monitoring_id")))
            if existing is not None and existing.monitoring_id == local_parent:
                result.entities_reused += 1
            else:
                self._conflict(result, _TABLE_MONITORING_METRICS, remote_id,
                               "PARENT_MISMATCH", "Race on existing metrics row.")
        except Exception as exc:  # noqa: BLE001
            self._row_error(result, _TABLE_MONITORING_METRICS, remote_id, exc)

    def _recover_snapshot_row(
        self, row, result, remote_to_local, remote_user_id, local_user_id, uow,
        blocked_lineage, tombstone_cache, download_jobs
    ) -> None:
        remote_id = row.get("id")
        try:
            if not _is_valid_uuid(remote_id):
                self._skip(result, _TABLE_SNAPSHOTS, remote_id, "INVALID_REMOTE_ID",
                           "Missing or invalid remote id.")
                return

            # No direct tombstone type for snapshots (they are deleted only via
            # cascade); a snapshot is blocked only via its monitoring's lineage.
            remote_parent = row.get("monitoring_id")
            if not _is_valid_uuid(remote_parent):
                self._skip(result, _TABLE_SNAPSHOTS, remote_id, "INVALID_REMOTE_ID",
                           "Missing or invalid remote monitoring_id.")
                return

            # Blocked-lineage: parent monitoring tombstoned/failed. Snapshot is
            # itself a parent of inspection_results, so register its own marker.
            if self._lineage_blocks(
                _TABLE_SNAPSHOTS, remote_id, (_MONITORING, remote_parent),
                result, blocked_lineage, own_map_key=_SNAPSHOT,
            ):
                return

            local_monitoring_id = remote_to_local.get((_MONITORING, remote_parent))
            if local_monitoring_id is None:
                self._skip(result, _TABLE_SNAPSHOTS, remote_id, "PARENT_UNRESOLVED",
                           "Parent monitoring was not recovered.")
                return

            # REUSE-before-interpret: check for an existing local snapshot BEFORE
            # interpreting any cloud business field (frame_index, image_path).
            # Import-missing-only: a reused row is never re-interpreted.
            existing = uow.snapshot_repo.find_by_remote_id(remote_id)
            if existing is not None:
                if existing.monitoring_id == local_monitoring_id:
                    result.entities_reused += 1
                    remote_to_local[(_SNAPSHOT, remote_id)] = existing.id
                    # REUSED consistently: still eligible for image recovery so
                    # missing physical files can be restored. Metadata is NOT
                    # modified; the physical jobs use the LOCAL frame_index, so
                    # a corrupted cloud frame_index cannot affect a valid row.
                    self._enqueue_snapshot_downloads(
                        download_jobs, row, remote_id, remote_parent,
                        local_monitoring_id, existing.frame_index,
                    )
                else:
                    self._conflict(result, _TABLE_SNAPSHOTS, remote_id, "PARENT_MISMATCH",
                                   "Existing local snapshot has a different parent.")
                return

            # Snapshot is MISSING: only now interpret the cloud frame_index.
            # PostgREST delivers this as an integer; require a non-negative int
            # (bool is not a valid int here) and never coerce strings or default
            # to 0 when the value is absent.
            raw_frame_index = row.get("frame_index")
            if (
                not isinstance(raw_frame_index, int)
                or isinstance(raw_frame_index, bool)
                or raw_frame_index < 0
            ):
                self._skip(result, _TABLE_SNAPSHOTS, remote_id, "INVALID_FRAME_INDEX",
                           "Missing or invalid frame_index on snapshot row.")
                return
            frame_index = raw_frame_index

            # D3.2: the destination image_path is authoritative for THIS device.
            # The remote local_image_path may carry the originating device's old
            # integer monitoring id, so it is informative only and NOT used here.
            # Rebuild the canonical local raw path from the LOCAL monitoring id.
            image_path = _local_raw_relative_path(local_monitoring_id, frame_index)
            image_path = f"outputs/{image_path}"
            captured_at = self._parse_ts(row.get("captured_at"))
            if self._ts_invalid(row.get("captured_at"), captured_at):
                self._skip(result, _TABLE_SNAPSHOTS, remote_id, "INVALID_TIMESTAMP",
                           "Unparseable timestamp on snapshot row.")
                return

            entity = Snapshot(
                monitoring_id=local_monitoring_id,
                image_path=image_path,
                frame_index=frame_index,
                captured_at=captured_at,
                change_score=row.get("change_score"),
                has_detections=bool(row.get("has_detections") or False),
            )
            created = uow.snapshot_repo.insert_preserving_remote_id(
                local_monitoring_id,
                entity,
                remote_id=remote_id,
                raw_storage_path=row.get("raw_storage_path"),
                annotated_storage_path=row.get("annotated_storage_path"),
            )
            result.entities_recovered += 1
            remote_to_local[(_SNAPSHOT, remote_id)] = created.id
            # RECOVERED: enqueue image downloads (processed after this phase).
            self._enqueue_snapshot_downloads(
                download_jobs, row, remote_id, remote_parent,
                local_monitoring_id, frame_index,
            )
        except RecoveredEntityAlreadyExistsError:
            self._handle_race_child(
                uow.snapshot_repo, result, remote_to_local, _SNAPSHOT, _TABLE_SNAPSHOTS,
                remote_id, "monitoring_id", local_monitoring_id,
            )
        except Exception as exc:  # noqa: BLE001
            self._row_error(result, _TABLE_SNAPSHOTS, remote_id, exc)

    def _recover_inspection_row(
        self, row, result, remote_to_local, remote_user_id, local_user_id, uow,
        blocked_lineage, tombstone_cache, download_jobs
    ) -> None:
        remote_id = row.get("id")
        try:
            if not _is_valid_uuid(remote_id):
                self._skip(result, _TABLE_INSPECTION_RESULTS, remote_id,
                           "INVALID_REMOTE_ID", "Missing or invalid remote id.")
                return

            # No direct tombstone type for inspection_results (cascade-only);
            # blocked only via its ancestor snapshot's lineage.
            remote_parent = row.get("snapshot_id")
            if not _is_valid_uuid(remote_parent):
                self._skip(result, _TABLE_INSPECTION_RESULTS, remote_id,
                           "INVALID_REMOTE_ID", "Missing or invalid remote snapshot_id.")
                return

            # Blocked-lineage: parent snapshot tombstoned/failed. Leaf: no own
            # lineage key.
            if self._lineage_blocks(
                _TABLE_INSPECTION_RESULTS, remote_id, (_SNAPSHOT, remote_parent),
                result, blocked_lineage, own_map_key=None,
            ):
                return

            local_snapshot_id = remote_to_local.get((_SNAPSHOT, remote_parent))
            if local_snapshot_id is None:
                self._skip(result, _TABLE_INSPECTION_RESULTS, remote_id,
                           "PARENT_UNRESOLVED", "Parent snapshot was not recovered.")
                return

            existing = uow.inspection_result_repo.find_by_remote_id(remote_id)
            if existing is not None:
                if existing.snapshot_id == local_snapshot_id:
                    result.entities_reused += 1  # leaf
                else:
                    self._conflict(result, _TABLE_INSPECTION_RESULTS, remote_id,
                                   "PARENT_MISMATCH",
                                   "Existing local result has a different parent.")
                return

            created_at = self._parse_ts(row.get("created_at"))
            if self._ts_invalid(row.get("created_at"), created_at):
                self._skip(result, _TABLE_INSPECTION_RESULTS, remote_id,
                           "INVALID_TIMESTAMP", "Unparseable timestamp on result row.")
                return

            entity = DetectionInspectionResult(
                snapshot_id=local_snapshot_id,
                detection_index=int(row.get("detection_index") or 0),
                bbox_x1=int(row.get("bbox_x1") or 0),
                bbox_y1=int(row.get("bbox_y1") or 0),
                bbox_x2=int(row.get("bbox_x2") or 0),
                bbox_y2=int(row.get("bbox_y2") or 0),
                detection_score=row.get("detection_score") or 0.0,
                health_label=row.get("health_label") or "healthy",
                health_confidence=row.get("health_confidence") or 0.0,
                maturity_stage=row.get("maturity_stage"),
                maturity_percent=row.get("maturity_percent"),
                created_at=created_at,
            )
            uow.inspection_result_repo.insert_preserving_remote_id(
                local_snapshot_id, entity, remote_id
            )
            result.entities_recovered += 1
        except RecoveredEntityAlreadyExistsError:
            existing = uow.inspection_result_repo.find_by_remote_id(remote_id)
            local_parent = remote_to_local.get((_SNAPSHOT, row.get("snapshot_id")))
            if existing is not None and existing.snapshot_id == local_parent:
                result.entities_reused += 1
            else:
                self._conflict(result, _TABLE_INSPECTION_RESULTS, remote_id,
                               "PARENT_MISMATCH", "Race on existing result row.")
        except Exception as exc:  # noqa: BLE001
            self._row_error(result, _TABLE_INSPECTION_RESULTS, remote_id, exc)

    def _recover_activity_log_row(
        self, row, result, remote_to_local, remote_user_id, local_user_id, uow,
        blocked_lineage, tombstone_cache, download_jobs
    ) -> None:
        remote_id = row.get("id")
        try:
            if not _is_valid_uuid(remote_id):
                self._skip(result, _TABLE_ACTIVITY_LOGS, remote_id, "INVALID_REMOTE_ID",
                           "Missing or invalid remote id.")
                return

            # No direct tombstone type for activity_logs; blockable only via its
            # parent module's lineage (a tombstoned monitoring does NOT block an
            # activity_log, since its parent is the module, not the monitoring).
            remote_parent = row.get("module_id")
            if not _is_valid_uuid(remote_parent):
                self._skip(result, _TABLE_ACTIVITY_LOGS, remote_id, "INVALID_REMOTE_ID",
                           "Missing or invalid remote module_id.")
                return

            # Blocked-lineage: parent module tombstoned/failed. Leaf: no own key.
            if self._lineage_blocks(
                _TABLE_ACTIVITY_LOGS, remote_id, (_MODULE, remote_parent),
                result, blocked_lineage, own_map_key=None,
            ):
                return

            local_module_id = remote_to_local.get((_MODULE, remote_parent))
            if local_module_id is None:
                self._skip(result, _TABLE_ACTIVITY_LOGS, remote_id, "PARENT_UNRESOLVED",
                           "Parent module was not recovered.")
                return

            # REUSE before interpreting user / activity_type / timestamps.
            existing = uow.activity_log_repo.find_by_remote_id(remote_id)
            if existing is not None:
                if existing.module_id == local_module_id:
                    result.entities_reused += 1  # leaf
                else:
                    self._conflict(result, _TABLE_ACTIVITY_LOGS, remote_id,
                                   "PARENT_MISMATCH",
                                   "Existing local activity has a different parent.")
                return

            # Only when missing: resolve user mapping and activity type.
            remote_user = row.get("user_id")
            if not _is_valid_uuid(remote_user):
                self._skip(result, _TABLE_ACTIVITY_LOGS, remote_id, "INVALID_REMOTE_ID",
                           "Missing or invalid remote user_id.")
                return
            local_user = self._sync_state.get_local_user_id_by_remote_id(remote_user)
            if local_user is None:
                self._skip(result, _TABLE_ACTIVITY_LOGS, remote_id,
                           "USER_MAPPING_NOT_FOUND", "Remote user has no local mapping.")
                return

            activity_type = uow.activity_type_repo.get_by_code(
                row.get("activity_type_code")
            )
            if activity_type is None:
                self._skip(result, _TABLE_ACTIVITY_LOGS, remote_id,
                           "ACTIVITY_TYPE_NOT_FOUND",
                           "Unknown activity type code; not seeded locally.")
                return

            occurred_at = self._parse_ts(row.get("occurred_at"))
            created_at = self._parse_ts(row.get("created_at"))
            if self._ts_invalid(row.get("occurred_at"), occurred_at) or self._ts_invalid(
                row.get("created_at"), created_at
            ):
                self._skip(result, _TABLE_ACTIVITY_LOGS, remote_id, "INVALID_TIMESTAMP",
                           "Unparseable timestamp on activity row.")
                return

            entity = ActivityLog(
                module_id=local_module_id,
                activity_type_id=activity_type.id,
                user_id=local_user,
                product_name=row.get("product_name"),
                quantity=row.get("quantity"),
                unit=row.get("unit"),
                notes=row.get("notes"),
                occurred_at=occurred_at,
                created_at=created_at,
                sync_status="synced",
            )
            uow.activity_log_repo.insert_preserving_remote_id(entity, remote_id)
            result.entities_recovered += 1
        except RecoveredEntityAlreadyExistsError:
            existing = uow.activity_log_repo.find_by_remote_id(remote_id)
            local_parent = remote_to_local.get((_MODULE, row.get("module_id")))
            if existing is not None and existing.module_id == local_parent:
                result.entities_reused += 1
            else:
                self._conflict(result, _TABLE_ACTIVITY_LOGS, remote_id,
                               "PARENT_MISMATCH", "Race on existing activity row.")
        except Exception as exc:  # noqa: BLE001
            self._row_error(result, _TABLE_ACTIVITY_LOGS, remote_id, exc)

    # ------------------------------------------------------------------
    # Snapshot download job collection (Spec 022, D3.2)
    # ------------------------------------------------------------------

    def _enqueue_snapshot_downloads(
        self, download_jobs, row, snapshot_remote_id, monitoring_remote_id,
        local_monitoring_id, frame_index,
    ) -> None:
        """Append a download job per NON-NULL declared Storage path.

        Only ``raw_storage_path`` and ``annotated_storage_path`` that are
        present (non-empty strings) contribute expected objects. The remote path
        is validated later (right before HTTP) against the deterministic path;
        here we only record the stored value plus the canonical local
        destination for this device.
        """
        for snapshot_type, remote_key, local_builder in (
            ("raw", "raw_storage_path", _local_raw_relative_path),
            ("annotated", "annotated_storage_path", _local_annotated_relative_path),
        ):
            remote_path = row.get(remote_key)
            if not isinstance(remote_path, str) or not remote_path.strip():
                continue
            download_jobs.append(
                _SnapshotDownloadJob(
                    snapshot_remote_id=snapshot_remote_id,
                    monitoring_remote_id=monitoring_remote_id,
                    local_monitoring_id=local_monitoring_id,
                    frame_index=frame_index,
                    snapshot_type=snapshot_type,
                    remote_path=remote_path,
                    local_relative_path=local_builder(
                        local_monitoring_id, frame_index
                    ),
                )
            )

    # ------------------------------------------------------------------
    # Anti-resurrection guard helpers (Spec 022, D3.1)
    # ------------------------------------------------------------------

    def _tombstone_blocks(
        self, table, remote_id, result, blocked_lineage, tombstone_cache,
        own_map_key,
    ) -> bool:
        """Direct tombstone check for a table with a direct tombstone type.

        Returns True (caller must RETURN) when a blocking tombstone exists or
        the lookup fails. In both cases the row is neither reused, inserted, nor
        mapped, and — when this entity is itself a lineage parent — its own
        (own_map_key, remote_id) marker is added to ``blocked_lineage`` so its
        descendants are blocked too. Returns False when no tombstone blocks.
        """
        outbox_type = _TABLE_TO_TOMBSTONE_ENTITY_TYPE.get(table)
        if outbox_type is None:
            # No direct tombstone type for this table.
            return False

        checked = self._check_tombstone(outbox_type, remote_id, tombstone_cache)
        if not checked.success:
            self._skip(result, table, remote_id, "TOMBSTONE_CHECK_FAILED",
                       "Tombstone lookup failed; skipping to avoid resurrection.")
            self._mark_lineage(blocked_lineage, own_map_key, remote_id)
            return True
        if checked.blocked:
            self._skip(result, table, remote_id, "TOMBSTONE_BLOCKED",
                       f"Blocking tombstone present (status={checked.status}).")
            self._mark_lineage(blocked_lineage, own_map_key, remote_id)
            return True
        return False

    def _lineage_blocks(
        self, table, remote_id, parent_key, result, blocked_lineage, own_map_key,
    ) -> bool:
        """Blocked-lineage check for a child row.

        ``parent_key`` is (parent_map_key, remote_parent_uuid). Returns True
        (caller must RETURN) when the parent is in ``blocked_lineage``: the row
        is skipped with PARENT_TOMBSTONED, never reused/inserted/mapped, and —
        when this child is itself a lineage parent — its own marker is added so
        the subtree keeps propagating. Returns False otherwise.
        """
        if parent_key in blocked_lineage:
            self._skip(result, table, remote_id, "PARENT_TOMBSTONED",
                       "An ancestor is tombstoned; skipping descendant.")
            self._mark_lineage(blocked_lineage, own_map_key, remote_id)
            return True
        return False

    @staticmethod
    def _mark_lineage(blocked_lineage, own_map_key, remote_id) -> None:
        """Register (own_map_key, remote_id) so descendants stay blocked."""
        if own_map_key is not None:
            blocked_lineage.add((own_map_key, remote_id))

    # ------------------------------------------------------------------
    # Race handling helpers
    # ------------------------------------------------------------------

    def _handle_race_child(
        self,
        repo,
        result,
        remote_to_local,
        map_key: str,
        table: str,
        remote_id: str,
        parent_attr: str,
        local_parent_id,
    ) -> None:
        """Re-query on a duplicate child insert; reuse + map if parent matches."""
        existing = repo.find_by_remote_id(remote_id)
        if existing is not None and getattr(existing, parent_attr) == local_parent_id:
            result.entities_reused += 1
            remote_to_local[(map_key, remote_id)] = existing.id
        else:
            self._conflict(result, table, remote_id, "PARENT_MISMATCH",
                           "Race on existing child row.")

    # ------------------------------------------------------------------
    # Result mutation helpers
    # ------------------------------------------------------------------

    def _skip(self, result, entity_type, remote_id, code, message) -> None:
        result.entities_skipped += 1
        result.errors.append(RecoveryIssue(entity_type, remote_id, code, message))

    def _conflict(self, result, entity_type, remote_id, code, message) -> None:
        result.conflicts += 1
        result.errors.append(RecoveryIssue(entity_type, remote_id, code, message))

    def _row_error(self, result, entity_type, remote_id, exc: Exception) -> None:
        # Keep the message short and safe: no secrets, no JWT, no full payload.
        safe = type(exc).__name__
        result.entities_skipped += 1
        result.errors.append(
            RecoveryIssue(
                entity_type=entity_type,
                remote_id=remote_id if isinstance(remote_id, str) else None,
                code="INSERT_ERROR",
                message=f"Row processing failed ({safe}).",
            )
        )

    # ------------------------------------------------------------------
    # Timestamp parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _ts_invalid(raw_value, parsed) -> bool:
        """Return True when a non-None raw timestamp failed to parse.

        Used to distinguish an absent timestamp (fine) from a present-but-
        unparseable one (row error).
        """
        return raw_value is not None and parsed is None

    def _parse_ts(self, value) -> Optional[datetime]:
        """Parse a remote timestamp into a UTC, timezone-naive datetime.

        Accepts None (returns None), an existing datetime (normalized to UTC
        naive), or an ISO 8601 string including a trailing "Z" or an offset.
        Returns None when a string value cannot be parsed; callers use
        ``_ts_invalid`` to detect that case and treat the row as an error.
        """
        if value is None:
            return None
        if isinstance(value, datetime):
            return _to_utc_naive(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                return None
            return _to_utc_naive(parsed)
        return None


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _to_utc_naive(value: datetime) -> datetime:
    """Convert a datetime to UTC and drop tzinfo (project convention)."""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _expected_remote_path(
    monitoring_remote_id: str, snapshot_type: str, frame_index: int
) -> str:
    """Deterministic remote Storage object path (Spec 022, D3.2 contract).

    RAW:       monitorings/{uuid}/raw/snapshot_{index:06d}.jpg
    ANNOTATED: monitorings/{uuid}/annotated/snapshot_{index:06d}.jpg
    """
    return (
        f"monitorings/{monitoring_remote_id}/{snapshot_type}/"
        f"snapshot_{frame_index:06d}.jpg"
    )


def _local_raw_relative_path(local_monitoring_id: int, frame_index: int) -> str:
    """Canonical local raw path, relative to OUTPUTS_DIR (no leading outputs/)."""
    return (
        f"monitorings/{local_monitoring_id}/snapshots/raw/"
        f"snapshot_{frame_index:06d}.jpg"
    )


def _local_annotated_relative_path(
    local_monitoring_id: int, frame_index: int
) -> str:
    """Canonical local annotated path, relative to OUTPUTS_DIR.

    Note the intentional folder-name difference from Storage: the remote folder
    is ``annotated/`` while the local folder is ``annotated_snapshots/``.
    """
    return (
        f"monitorings/{local_monitoring_id}/annotated_snapshots/"
        f"snapshot_{frame_index:06d}.jpg"
    )


def _is_valid_uuid(value) -> bool:
    """Strict UUID validation using the stdlib parser.

    Rejects non-strings, values with surrounding whitespace, and any string the
    stdlib UUID parser does not accept (e.g. 36 arbitrary chars, "not-a-uuid",
    empty, or 36-digit numeric strings). Accepts only canonical UUID strings:
    the input must round-trip to ``str(UUID(value)) == value.lower()``.
    """
    if not isinstance(value, str):
        return False
    if value != value.strip():
        return False
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return str(parsed) == value.lower()
