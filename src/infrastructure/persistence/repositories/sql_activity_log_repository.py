"""Concrete repository implementation for ActivityLog entities using SQLAlchemy."""

from typing import Optional

from sqlalchemy.orm import Session

from src.domain.entities.activity_log import ActivityLog
from src.domain.repositories.activity_log_repository import ActivityLogRepository
from src.infrastructure.persistence.models.activity_log_model import ActivityLogModel


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
