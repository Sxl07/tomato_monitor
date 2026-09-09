"""SyncStateRepository: infrastructure adapter for remote sync state management.

Implements SyncStatePort by reading/writing remote sync metadata columns
on existing ORM models via SQLAlchemy sessions.

Each mutation commits immediately to ensure durable checkpoints before
remote operations. Sessions are short-lived (created and closed per operation).

This module is purely local persistence — no network calls, no Supabase,
no httpx.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy.orm import Session, joinedload

from src.application.interfaces.sync_state_port import StoragePaths
from src.infrastructure.persistence.models.activity_log_model import ActivityLogModel
from src.infrastructure.persistence.models.greenhouse_model import GreenhouseModel
from src.infrastructure.persistence.models.inspection_result_model import (
    InspectionResultModel,
)
from src.infrastructure.persistence.models.module_model import ModuleModel
from src.infrastructure.persistence.models.monitoring_metrics_model import (
    MonitoringMetricsModel,
)
from src.infrastructure.persistence.models.monitoring_model import MonitoringModel
from src.infrastructure.persistence.models.snapshot_model import SnapshotModel

# Static mapping of entity_type strings to ORM model classes.
_ENTITY_MODELS: dict[str, type] = {
    "greenhouse": GreenhouseModel,
    "module": ModuleModel,
    "monitoring": MonitoringModel,
    "snapshot": SnapshotModel,
    "monitoring_metrics": MonitoringMetricsModel,
    "inspection_result": InspectionResultModel,
    "activity_log": ActivityLogModel,
}

# Retryable sync statuses (persisted "syncing" is stale after crash/restart).
_RETRYABLE_STATUSES = ("pending", "error", "syncing")


def _utcnow() -> datetime:
    """Return current UTC time as timezone-naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _resolve_model_class(entity_type: str) -> type:
    """Resolve entity_type string to an ORM model class.

    Raises:
        ValueError: If entity_type is not supported.
    """
    model_class = _ENTITY_MODELS.get(entity_type)
    if model_class is None:
        supported = ", ".join(sorted(_ENTITY_MODELS.keys()))
        raise ValueError(
            f"Unsupported entity_type '{entity_type}'. "
            f"Supported: {supported}"
        )
    return model_class


def _model_to_dict(model) -> dict:
    """Convert an ORM model instance to a clean dict of column values.

    Excludes SQLAlchemy internal state and relationships.
    """
    return {
        column.name: getattr(model, column.name)
        for column in model.__table__.columns
    }


class SyncStateRepository:
    """Infrastructure adapter implementing SyncStatePort via SQLAlchemy.

    Uses a session_factory callable to create short-lived sessions.
    Each mutation commits immediately for durable checkpoints.
    """

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        """Initialize with a callable that produces new SQLAlchemy Sessions.

        Args:
            session_factory: Callable returning a fresh Session instance.
                             Compatible with DatabaseManager.get_session.
        """
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_pending_entities(self, entity_type: str) -> list[dict]:
        """Return entities with retryable sync status (pending, error, syncing).

        Results are ordered by id ASC for determinism.
        For activity_log, enriches each dict with activity_type_code from
        the joined ActivityType relationship.
        """
        model_class = _resolve_model_class(entity_type)
        session = self._session_factory()
        try:
            query = (
                session.query(model_class)
                .filter(model_class.remote_sync_status.in_(_RETRYABLE_STATUSES))
                .order_by(model_class.id.asc())
            )

            # Eagerly load activity_type for code resolution
            if entity_type == "activity_log":
                query = query.options(joinedload(ActivityLogModel.activity_type))

            models = query.all()

            if entity_type == "activity_log":
                results = []
                for m in models:
                    row = _model_to_dict(m)
                    row["activity_type_code"] = (
                        m.activity_type.code
                        if m.activity_type is not None
                        else None
                    )
                    results.append(row)
                return results

            return [_model_to_dict(m) for m in models]
        finally:
            session.close()

    def get_remote_id(self, entity_type: str, local_id: int) -> Optional[str]:
        """Return the remote UUID for a local entity, or None."""
        model_class = _resolve_model_class(entity_type)
        session = self._session_factory()
        try:
            model = session.get(model_class, local_id)
            if model is None:
                return None
            return model.remote_id
        finally:
            session.close()

    def get_user_remote_id(self, local_user_id: int) -> Optional[str]:
        """Return the remote_user_id (UUID) for a local user, or None.

        Resolves ``users.id -> users.remote_user_id`` (the Supabase Auth user
        id / ``auth.uid()``). Returns None if the user is missing or has no
        remote identity assigned.
        """
        from src.infrastructure.persistence.models.user_model import UserModel

        session = self._session_factory()
        try:
            model = session.get(UserModel, local_user_id)
            if model is None:
                return None
            return model.remote_user_id
        finally:
            session.close()

    def get_effective_owner_local_user_id(
        self, entity_type: str, local_id: int
    ) -> Optional[int]:
        """Resolve the effective owner's LOCAL user id via the Greenhouse root.

        Walks the FK chain up to the owning greenhouse and returns its
        ``owner_user_id`` (local ``users.id``). Returns None when any link is
        missing or the greenhouse has no local owner (legacy NULL). Does NOT
        depend on ``users.remote_user_id``.
        """
        session = self._session_factory()
        try:
            greenhouse = self._resolve_owning_greenhouse(session, entity_type, local_id)
            if greenhouse is None:
                return None
            return greenhouse.owner_user_id
        finally:
            session.close()

    def get_local_user_id_by_remote_id(self, user_remote_id: str) -> Optional[int]:
        """Return the local users.id whose remote_user_id matches, or None."""
        from src.infrastructure.persistence.models.user_model import UserModel

        if not user_remote_id:
            return None
        session = self._session_factory()
        try:
            model = (
                session.query(UserModel)
                .filter(UserModel.remote_user_id == user_remote_id)
                .first()
            )
            return None if model is None else model.id
        finally:
            session.close()

    def _resolve_owning_greenhouse(
        self, session: Session, entity_type: str, local_id: int
    ) -> Optional[GreenhouseModel]:
        """Return the root GreenhouseModel that owns the given entity, or None."""
        if entity_type == "greenhouse":
            return session.get(GreenhouseModel, local_id)

        if entity_type == "module":
            module = session.get(ModuleModel, local_id)
            if module is None:
                return None
            return session.get(GreenhouseModel, module.greenhouse_id)

        if entity_type == "monitoring":
            monitoring = session.get(MonitoringModel, local_id)
            if monitoring is None:
                return None
            return self._resolve_owning_greenhouse(session, "module", monitoring.module_id)

        if entity_type == "monitoring_metrics":
            metrics = session.get(MonitoringMetricsModel, local_id)
            if metrics is None:
                return None
            return self._resolve_owning_greenhouse(
                session, "monitoring", metrics.monitoring_id
            )

        if entity_type == "snapshot":
            snapshot = session.get(SnapshotModel, local_id)
            if snapshot is None:
                return None
            return self._resolve_owning_greenhouse(
                session, "monitoring", snapshot.monitoring_id
            )

        if entity_type == "inspection_result":
            result = session.get(InspectionResultModel, local_id)
            if result is None:
                return None
            return self._resolve_owning_greenhouse(
                session, "snapshot", result.snapshot_id
            )

        if entity_type == "activity_log":
            log = session.get(ActivityLogModel, local_id)
            if log is None:
                return None
            return self._resolve_owning_greenhouse(session, "module", log.module_id)

        return None

    def get_storage_paths(self, snapshot_id: int) -> StoragePaths:
        """Return remote storage paths for a snapshot.

        Raises:
            ValueError: If snapshot does not exist.
        """
        session = self._session_factory()
        try:
            model = session.get(SnapshotModel, snapshot_id)
            if model is None:
                raise ValueError(f"Snapshot with id={snapshot_id} not found")
            return StoragePaths(
                raw_storage_path=model.raw_storage_path,
                annotated_storage_path=model.annotated_storage_path,
            )
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Mutations (each commits immediately for durable checkpoints)
    # ------------------------------------------------------------------

    def reserve_remote_id(
        self, entity_type: str, local_id: int, remote_id: str
    ) -> None:
        """Store a pre-generated remote UUID. Idempotent for same UUID.

        Raises:
            ValueError: If entity not found or if a different UUID is already reserved.
        """
        model_class = _resolve_model_class(entity_type)
        session = self._session_factory()
        try:
            model = session.get(model_class, local_id)
            if model is None:
                raise ValueError(
                    f"{entity_type} with id={local_id} not found"
                )

            if model.remote_id is not None and model.remote_id != remote_id:
                raise ValueError(
                    f"{entity_type} id={local_id} already has remote_id="
                    f"'{model.remote_id}'; cannot replace with '{remote_id}'"
                )

            model.remote_id = remote_id
            model.remote_sync_status = "pending"
            model.remote_sync_error = None
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def mark_syncing(self, entity_type: str, local_id: int) -> None:
        """Mark entity as currently being synchronized.

        Raises:
            ValueError: If entity not found.
        """
        model_class = _resolve_model_class(entity_type)
        session = self._session_factory()
        try:
            model = session.get(model_class, local_id)
            if model is None:
                raise ValueError(
                    f"{entity_type} with id={local_id} not found"
                )

            model.remote_sync_status = "syncing"
            model.remote_sync_error = None
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def mark_synced(
        self, entity_type: str, local_id: int, remote_id: str
    ) -> None:
        """Mark entity as successfully synchronized.

        Raises:
            ValueError: If entity not found or remote_id conflict.
        """
        model_class = _resolve_model_class(entity_type)
        session = self._session_factory()
        try:
            model = session.get(model_class, local_id)
            if model is None:
                raise ValueError(
                    f"{entity_type} with id={local_id} not found"
                )

            # Protect identity stability
            if model.remote_id is None:
                model.remote_id = remote_id
            elif model.remote_id != remote_id:
                raise ValueError(
                    f"{entity_type} id={local_id} already has remote_id="
                    f"'{model.remote_id}'; cannot confirm with '{remote_id}'"
                )

            model.remote_sync_status = "synced"
            model.last_synced_at = _utcnow()
            model.remote_sync_error = None
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def mark_error(
        self, entity_type: str, local_id: int, error_msg: str
    ) -> None:
        """Mark entity sync as failed. Preserves remote_id.

        Raises:
            ValueError: If entity not found.
        """
        model_class = _resolve_model_class(entity_type)
        session = self._session_factory()
        try:
            model = session.get(model_class, local_id)
            if model is None:
                raise ValueError(
                    f"{entity_type} with id={local_id} not found"
                )

            model.remote_sync_status = "error"
            model.remote_sync_error = error_msg
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def set_storage_paths(
        self,
        snapshot_id: int,
        raw_path: Optional[str],
        annotated_path: Optional[str],
    ) -> None:
        """Store remote storage paths for a snapshot.

        Raises:
            ValueError: If snapshot not found.
        """
        session = self._session_factory()
        try:
            model = session.get(SnapshotModel, snapshot_id)
            if model is None:
                raise ValueError(f"Snapshot with id={snapshot_id} not found")

            model.raw_storage_path = raw_path
            model.annotated_storage_path = annotated_path
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_sync_status_counts(self) -> "SyncStatusCounts":
        """Return aggregated sync status counts across all syncable entity types.

        Counts:
        - pending_count: entities with remote_sync_status in (pending, syncing)
        - synced_count: entities with remote_sync_status == 'synced'
        - error_count: entities with remote_sync_status == 'error'
        - last_sync_at: most recent last_synced_at across all entities

        Entities that have never been marked (NULL remote_sync_status) are
        counted as pending since they haven't been synced yet.
        """
        from src.application.interfaces.sync_state_port import SyncStatusCounts
        from sqlalchemy import func

        session = self._session_factory()
        try:
            pending_total = 0
            synced_total = 0
            error_total = 0
            last_sync_at = None

            for model_class in _ENTITY_MODELS.values():
                # Count by status
                rows = (
                    session.query(
                        model_class.remote_sync_status,
                        func.count(model_class.id),
                    )
                    .group_by(model_class.remote_sync_status)
                    .all()
                )

                for status, count in rows:
                    if status in ("pending", "syncing", None):
                        pending_total += count
                    elif status == "synced":
                        synced_total += count
                    elif status == "error":
                        error_total += count
                        # errors are also retryable/pending
                        pending_total += count

                # Find latest last_synced_at
                max_synced = (
                    session.query(func.max(model_class.last_synced_at))
                    .scalar()
                )
                if max_synced is not None:
                    if last_sync_at is None or max_synced > last_sync_at:
                        last_sync_at = max_synced

            return SyncStatusCounts(
                pending_count=pending_total,
                synced_count=synced_total,
                error_count=error_total,
                last_sync_at=last_sync_at,
            )
        finally:
            session.close()
