"""Concrete repository implementation for Module entities using SQLAlchemy."""

from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.domain.entities.module import Module
from src.domain.exceptions import DuplicateModuleError
from src.domain.repositories.module_repository import ModuleRepository
from src.infrastructure.persistence.models.module_model import ModuleModel


class SqlModuleRepository(ModuleRepository):
    """SQLAlchemy-based implementation of ModuleRepository.

    Manages Module persistence against a SQLite database via SQLAlchemy Session.
    Handles entity ↔ model conversion internally to preserve layer independence.
    """

    def __init__(self, session: Session) -> None:
        """Initialize with an active SQLAlchemy session.

        Args:
            session: SQLAlchemy Session instance for database operations.
        """
        self._session = session

    def create(self, greenhouse_id: int, module: Module) -> Module:
        """Persist a new module under the given greenhouse.

        Catches IntegrityError for duplicate (greenhouse_id, name) and raises
        DuplicateModuleError from the domain layer.

        Args:
            greenhouse_id: The parent greenhouse identifier.
            module: Module domain entity with data to persist.

        Returns:
            The created Module with assigned id and timestamps.

        Raises:
            DuplicateModuleError: If a module with the same name already exists
                in the specified greenhouse.
        """
        model = self._to_model(greenhouse_id, module)
        self._session.add(model)
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            raise DuplicateModuleError(greenhouse_id, module.name)
        self._session.commit()
        self._session.refresh(model)
        return self._to_entity(model)

    def get_by_greenhouse(self, greenhouse_id: int) -> list[Module]:
        """Return all modules for the given greenhouse ordered by created_at descending.

        Args:
            greenhouse_id: The greenhouse identifier to filter by.

        Returns:
            List of Module entities, newest first. Empty list if none exist.
        """
        models = (
            self._session.query(ModuleModel)
            .filter(ModuleModel.greenhouse_id == greenhouse_id)
            .order_by(ModuleModel.created_at.desc())
            .all()
        )
        return [self._to_entity(m) for m in models]

    def get_by_id(self, id: int) -> Optional[Module]:
        """Return the module with the given id, or None if not found.

        Args:
            id: The module identifier.

        Returns:
            Module entity or None if no record exists with the given id.
        """
        model = self._session.query(ModuleModel).filter(ModuleModel.id == id).first()
        if model is None:
            return None
        return self._to_entity(model)

    def update(self, id: int, fields: dict) -> Module:
        """Update module fields and return the updated entity.

        Valid keys: name, crop_type, width_m, length_m.

        Args:
            id: The module identifier.
            fields: Dictionary with keys to update.

        Returns:
            The updated Module entity.

        Raises:
            DuplicateModuleError: If updating the name causes a duplicate
                (greenhouse_id, name) conflict.
        """
        model = self._session.query(ModuleModel).filter(ModuleModel.id == id).one()

        allowed_fields = {"name", "crop_type", "width_m", "length_m", "monitoring_frequency_days"}
        for key, value in fields.items():
            if key in allowed_fields:
                setattr(model, key, value)

        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            raise DuplicateModuleError(model.greenhouse_id, fields.get("name", model.name))
        self._session.commit()
        self._session.refresh(model)
        return self._to_entity(model)

    def delete(self, id: int) -> None:
        """Delete the module and cascade-delete all descendant entities.

        Cascade path: Module → Monitoring → Snapshot → InspectionResult,
        and Monitoring → MonitoringMetrics.

        Args:
            id: The module identifier.
        """
        model = self._session.query(ModuleModel).filter(ModuleModel.id == id).first()
        if model is not None:
            self._session.delete(model)
            self._session.commit()

    def _to_entity(self, model: ModuleModel) -> Module:
        """Convert a SQLAlchemy ModuleModel to a domain Module entity.

        Args:
            model: The ORM model instance.

        Returns:
            Domain Module dataclass.
        """
        return Module(
            id=model.id,
            greenhouse_id=model.greenhouse_id,
            name=model.name,
            crop_type=model.crop_type,
            width_m=model.width_m,
            length_m=model.length_m,
            monitoring_frequency_days=model.monitoring_frequency_days,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, greenhouse_id: int, entity: Module) -> ModuleModel:
        """Convert a domain Module entity to a SQLAlchemy ModuleModel for creation.

        Args:
            greenhouse_id: The parent greenhouse identifier.
            entity: The domain Module entity.

        Returns:
            ORM ModuleModel instance ready for persistence.
        """
        return ModuleModel(
            greenhouse_id=greenhouse_id,
            name=entity.name,
            crop_type=entity.crop_type,
            width_m=entity.width_m,
            length_m=entity.length_m,
            monitoring_frequency_days=entity.monitoring_frequency_days,
        )
