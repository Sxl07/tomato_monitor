"""Concrete repository implementation for ActivityLog entities using SQLAlchemy."""

from typing import Optional

from sqlalchemy.orm import Session

from src.domain.entities.activity_log import ActivityLog
from src.domain.exceptions import RecoveredEntityAlreadyExistsError
from src.domain.repositories.activity_log_repository import ActivityLogRepository
from src.infrastructure.persistence.models.activity_log_model import (
    ActivityLogModel,
    utcnow,
)


class SqlActivityLogRepository(ActivityLogRepository):
    """SQLAlchemy-based implementation of ActivityLogRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, activity_log: ActivityLog) -> ActivityLog:
        """Persist a new activity log entry. Return it with assigned id."""
        model = self._to_model(activity_log)
        self._session.add(model)
        self._session.flush()
        self._session.commit()
        self._session.refresh(model)
        return self._to_entity(model)

    def get_by_id(self, id: int) -> Optional[ActivityLog]:
        """Return the activity log with the given id, or None if not found."""
        model = (
            self._session.query(ActivityLogModel)
            .filter(ActivityLogModel.id == id)
            .first()
        )
        if model is None:
            return None
        return self._to_entity(model)

    def list_by_module(self, module_id: int) -> list[ActivityLog]:
        """Return all activity logs for the given module, newest first."""
        models = (
            self._session.query(ActivityLogModel)
            .filter(ActivityLogModel.module_id == module_id)
            .order_by(ActivityLogModel.occurred_at.desc())
            .all()
        )
        return [self._to_entity(m) for m in models]

    def list_recent(self, limit: int = 10) -> list[ActivityLog]:
        """Return the most recent activity logs across all modules."""
        models = (
            self._session.query(ActivityLogModel)
            .order_by(ActivityLogModel.occurred_at.desc())
            .limit(limit)
            .all()
        )
        return [self._to_entity(m) for m in models]

    def list_by_user(self, user_id: int) -> list[ActivityLog]:
        """Return all activity logs for the given user, newest first."""
        models = (
            self._session.query(ActivityLogModel)
            .filter(ActivityLogModel.user_id == user_id)
            .order_by(ActivityLogModel.occurred_at.desc())
            .all()
        )
        return [self._to_entity(m) for m in models]

    def list_all(self) -> list[ActivityLog]:
        """Return all activity logs, ordered by occurred_at descending."""
        models = (
            self._session.query(ActivityLogModel)
            .order_by(ActivityLogModel.occurred_at.desc())
            .all()
        )
        return [self._to_entity(m) for m in models]

    def update_sync_status(self, ids: list[int], status: str) -> None:
        """Update sync_status for the given activity log ids."""
        if not ids:
            return
        self._session.query(ActivityLogModel).filter(
            ActivityLogModel.id.in_(ids)
        ).update({"sync_status": status}, synchronize_session="fetch")
        self._session.flush()
        self._session.commit()

    def find_by_remote_id(self, remote_id: str) -> Optional[ActivityLog]:
        """Return the activity log mapped to the given remote_id, or None."""
        model = (
            self._session.query(ActivityLogModel)
            .filter(ActivityLogModel.remote_id == remote_id)
            .first()
        )
        if model is None:
            return None
        return self._to_entity(model)

    def insert_preserving_remote_id(
        self, entity: ActivityLog, remote_id: str
    ) -> ActivityLog:
        """Insert a recovered activity log, mapping remote_id -> local remote_id.

        Import-missing-only: guards against duplicate remote_id, assigns a new
        autoincrement id, and marks the row synced. Mirrors create()'s
        flush+commit+refresh transactional pattern.
        """
        existing = (
            self._session.query(ActivityLogModel)
            .filter(ActivityLogModel.remote_id == remote_id)
            .first()
        )
        if existing is not None:
            raise RecoveredEntityAlreadyExistsError("ActivityLog", remote_id)

        model = self._to_model(entity)
        if entity.created_at is not None:
            model.created_at = entity.created_at
        model.remote_id = remote_id
        model.remote_sync_status = "synced"
        model.remote_sync_error = None
        model.last_synced_at = utcnow()
        self._session.add(model)
        self._session.flush()
        self._session.commit()
        self._session.refresh(model)
        return self._to_entity(model)

    def _to_entity(self, model: ActivityLogModel) -> ActivityLog:
        return ActivityLog(
            id=model.id,
            module_id=model.module_id,
            activity_type_id=model.activity_type_id,
            user_id=model.user_id,
            product_name=model.product_name,
            quantity=model.quantity,
            unit=model.unit,
            notes=model.notes,
            occurred_at=model.occurred_at,
            created_at=model.created_at,
            sync_status=model.sync_status,
        )

    def _to_model(self, entity: ActivityLog) -> ActivityLogModel:
        model = ActivityLogModel(
            module_id=entity.module_id,
            activity_type_id=entity.activity_type_id,
            user_id=entity.user_id,
            product_name=entity.product_name,
            quantity=entity.quantity,
            unit=entity.unit,
            notes=entity.notes,
            sync_status=entity.sync_status,
        )
        if entity.occurred_at is not None:
            model.occurred_at = entity.occurred_at
        return model
