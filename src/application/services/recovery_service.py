"""Application service: RecoveryService (Spec 022, block D2).

Hierarchical metadata recovery from a remote store (e.g. Supabase rows) into
the local SQLite database. This service reconstructs the local hierarchy
(greenhouses -> modules -> monitorings -> monitoring_metrics -> snapshots ->
inspection_results -> activity_logs) from RLS-visible remote rows, preserving
remote UUIDs in the local ``remote_id`` columns while assigning fresh local
integer primary keys.

Scope (metadata only):
    - NO image/video download, NO filesystem access.
    - NO anti-resurrection / deletion_outbox handling.
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

from src.application.interfaces.recovery_unit_of_work_port import (
    RecoveryUnitOfWorkFactory,
)
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
    ) -> None:
        self._remote_read = remote_read
        self._uow_factory = uow_factory
        self._sync_state = sync_state

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
                    )

        return result

    # ------------------------------------------------------------------
    # Phase handlers (one row at a time, fault-isolated, per-row UoW)
    # ------------------------------------------------------------------

    def _recover_greenhouse_row(
        self, row, result, remote_to_local, remote_user_id, local_user_id, uow
    ) -> None:
        remote_id = row.get("id")
        try:
            if not _is_valid_uuid(remote_id):
                self._skip(result, _TABLE_GREENHOUSES, remote_id, "INVALID_REMOTE_ID",
                           "Missing or invalid remote id.")
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
        self, row, result, remote_to_local, remote_user_id, local_user_id, uow
    ) -> None:
        remote_id = row.get("id")
        try:
            if not _is_valid_uuid(remote_id):
                self._skip(result, _TABLE_MODULES, remote_id, "INVALID_REMOTE_ID",
                           "Missing or invalid remote id.")
                return

            remote_parent = row.get("greenhouse_id")
            if not _is_valid_uuid(remote_parent):
                self._skip(result, _TABLE_MODULES, remote_id, "INVALID_REMOTE_ID",
                           "Missing or invalid remote greenhouse_id.")
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
        self, row, result, remote_to_local, remote_user_id, local_user_id, uow
    ) -> None:
        remote_id = row.get("id")
        try:
            if not _is_valid_uuid(remote_id):
                self._skip(result, _TABLE_MONITORINGS, remote_id, "INVALID_REMOTE_ID",
                           "Missing or invalid remote id.")
                return

            remote_parent = row.get("module_id")
            if not _is_valid_uuid(remote_parent):
                self._skip(result, _TABLE_MONITORINGS, remote_id, "INVALID_REMOTE_ID",
                           "Missing or invalid remote module_id.")
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
        self, row, result, remote_to_local, remote_user_id, local_user_id, uow
    ) -> None:
        remote_id = row.get("id")
        try:
            if not _is_valid_uuid(remote_id):
                self._skip(result, _TABLE_MONITORING_METRICS, remote_id,
                           "INVALID_REMOTE_ID", "Missing or invalid remote id.")
                return

            remote_parent = row.get("monitoring_id")
            if not _is_valid_uuid(remote_parent):
                self._skip(result, _TABLE_MONITORING_METRICS, remote_id,
                           "INVALID_REMOTE_ID", "Missing or invalid remote monitoring_id.")
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
        self, row, result, remote_to_local, remote_user_id, local_user_id, uow
    ) -> None:
        remote_id = row.get("id")
        try:
            if not _is_valid_uuid(remote_id):
                self._skip(result, _TABLE_SNAPSHOTS, remote_id, "INVALID_REMOTE_ID",
                           "Missing or invalid remote id.")
                return

            remote_parent = row.get("monitoring_id")
            if not _is_valid_uuid(remote_parent):
                self._skip(result, _TABLE_SNAPSHOTS, remote_id, "INVALID_REMOTE_ID",
                           "Missing or invalid remote monitoring_id.")
                return

            local_monitoring_id = remote_to_local.get((_MONITORING, remote_parent))
            if local_monitoring_id is None:
                self._skip(result, _TABLE_SNAPSHOTS, remote_id, "PARENT_UNRESOLVED",
                           "Parent monitoring was not recovered.")
                return

            existing = uow.snapshot_repo.find_by_remote_id(remote_id)
            if existing is not None:
                if existing.monitoring_id == local_monitoring_id:
                    result.entities_reused += 1
                    remote_to_local[(_SNAPSHOT, remote_id)] = existing.id
                else:
                    self._conflict(result, _TABLE_SNAPSHOTS, remote_id, "PARENT_MISMATCH",
                                   "Existing local snapshot has a different parent.")
                return

            # NOTE: the remote key is local_image_path (NOT image_path). Download
            # is out of scope (D3); do not invent a path.
            image_path = row.get("local_image_path")
            if not isinstance(image_path, str) or not image_path.strip():
                self._skip(result, _TABLE_SNAPSHOTS, remote_id, "INVALID_SNAPSHOT_PATH",
                           "Missing or invalid local_image_path.")
                return

            captured_at = self._parse_ts(row.get("captured_at"))
            if self._ts_invalid(row.get("captured_at"), captured_at):
                self._skip(result, _TABLE_SNAPSHOTS, remote_id, "INVALID_TIMESTAMP",
                           "Unparseable timestamp on snapshot row.")
                return

            entity = Snapshot(
                monitoring_id=local_monitoring_id,
                image_path=image_path,
                frame_index=int(row.get("frame_index") or 0),
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
        except RecoveredEntityAlreadyExistsError:
            self._handle_race_child(
                uow.snapshot_repo, result, remote_to_local, _SNAPSHOT, _TABLE_SNAPSHOTS,
                remote_id, "monitoring_id", local_monitoring_id,
            )
        except Exception as exc:  # noqa: BLE001
            self._row_error(result, _TABLE_SNAPSHOTS, remote_id, exc)

    def _recover_inspection_row(
        self, row, result, remote_to_local, remote_user_id, local_user_id, uow
    ) -> None:
        remote_id = row.get("id")
        try:
            if not _is_valid_uuid(remote_id):
                self._skip(result, _TABLE_INSPECTION_RESULTS, remote_id,
                           "INVALID_REMOTE_ID", "Missing or invalid remote id.")
                return

            remote_parent = row.get("snapshot_id")
            if not _is_valid_uuid(remote_parent):
                self._skip(result, _TABLE_INSPECTION_RESULTS, remote_id,
                           "INVALID_REMOTE_ID", "Missing or invalid remote snapshot_id.")
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
        self, row, result, remote_to_local, remote_user_id, local_user_id, uow
    ) -> None:
        remote_id = row.get("id")
        try:
            if not _is_valid_uuid(remote_id):
                self._skip(result, _TABLE_ACTIVITY_LOGS, remote_id, "INVALID_REMOTE_ID",
                           "Missing or invalid remote id.")
                return

            remote_parent = row.get("module_id")
            if not _is_valid_uuid(remote_parent):
                self._skip(result, _TABLE_ACTIVITY_LOGS, remote_id, "INVALID_REMOTE_ID",
                           "Missing or invalid remote module_id.")
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
