"""Concrete repository implementation for ActivityType entities using SQLAlchemy."""

from typing import Optional

from sqlalchemy.orm import Session

from src.domain.entities.activity_type import ActivityType
from src.domain.repositories.activity_type_repository import ActivityTypeRepository
from src.infrastructure.persistence.models.activity_type_model import ActivityTypeModel


class SqlActivityTypeRepository(ActivityTypeRepository):
    """SQLAlchemy-based implementation of ActivityTypeRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, activity_type: ActivityType) -> ActivityType:
        """Persist a new activity type. Return it with assigned id."""
        model = self._to_model(activity_type)
        self._session.add(model)
        self._session.flush()
        self._session.commit()
        self._session.refresh(model)
        return self._to_entity(model)

    def get_by_id(self, id: int) -> Optional[ActivityType]:
        """Return the activity type with the given id, or None if not found."""
        model = (
            self._session.query(ActivityTypeModel)
            .filter(ActivityTypeModel.id == id)
            .first()
        )
        if model is None:
            return None
        return self._to_entity(model)

    def get_by_code(self, code: str) -> Optional[ActivityType]:
        """Return the activity type with the given code, or None if not found."""
        model = (
            self._session.query(ActivityTypeModel)
            .filter(ActivityTypeModel.code == code)
            .first()
        )
        if model is None:
            return None
        return self._to_entity(model)

    def list_active(self) -> list[ActivityType]:
        """Return all active activity types."""
        models = (
            self._session.query(ActivityTypeModel)
            .filter(ActivityTypeModel.is_active == True)  # noqa: E712
            .order_by(ActivityTypeModel.name)
            .all()
        )
        return [self._to_entity(m) for m in models]

    def list_all(self) -> list[ActivityType]:
        """Return all activity types regardless of active status."""
        models = (
            self._session.query(ActivityTypeModel)
            .order_by(ActivityTypeModel.name)
            .all()
        )
        return [self._to_entity(m) for m in models]

    def _to_entity(self, model: ActivityTypeModel) -> ActivityType:
        return ActivityType(
            id=model.id,
            code=model.code,
            name=model.name,
            category=model.category,
            requires_product=model.requires_product,
            allows_quantity=model.allows_quantity,
            default_unit=model.default_unit,
            is_active=model.is_active,
        )

    def _to_model(self, entity: ActivityType) -> ActivityTypeModel:
        return ActivityTypeModel(
            code=entity.code,
            name=entity.name,
            category=entity.category,
            requires_product=entity.requires_product,
            allows_quantity=entity.allows_quantity,
            default_unit=entity.default_unit,
            is_active=entity.is_active,
        )
