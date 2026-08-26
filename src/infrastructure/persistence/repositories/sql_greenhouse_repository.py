"""Concrete SQLAlchemy implementation of GreenhouseRepository.

Persists Greenhouse entities to the SQLite database, converting between
domain dataclasses and ORM models internally.
"""

from typing import Optional

from sqlalchemy.orm import Session

from src.domain.entities.greenhouse import Greenhouse
from src.domain.repositories.greenhouse_repository import GreenhouseRepository
from src.infrastructure.persistence.models.greenhouse_model import GreenhouseModel


class SqlGreenhouseRepository(GreenhouseRepository):
    """SQLAlchemy-backed repository for Greenhouse entities.

    All operations use the injected Session instance. Cascade deletes are
    handled by the ORM relationship configuration on GreenhouseModel.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, greenhouse: Greenhouse) -> Greenhouse:
        """Persist a new greenhouse and return it with the assigned id."""
        model = self._to_model(greenhouse)
        self._session.add(model)
        self._session.flush()
        self._session.commit()
        self._session.refresh(model)
        return self._to_entity(model)

    def get_all(self) -> list[Greenhouse]:
        """Return all greenhouses. Returns an empty list if none exist."""
        models = self._session.query(GreenhouseModel).all()
        return [self._to_entity(m) for m in models]

    def get_by_id(self, id: int) -> Optional[Greenhouse]:
        """Return the greenhouse with the given id, or None if not found."""
        model = self._session.get(GreenhouseModel, id)
        if model is None:
            return None
        return self._to_entity(model)

    def update(self, id: int, name: str, location: Optional[str]) -> Greenhouse:
        """Update a greenhouse's name and location. Return the updated entity."""
        model = self._session.get(GreenhouseModel, id)
        if model is None:
            raise ValueError(f"Greenhouse with id={id} not found")

        # Detect real domain change for dirty tracking
        domain_changed = (model.name != name or model.location != location)

        model.name = name
        model.location = location

        # Dirty tracking: if synced and domain changed, mark pending for re-sync
        if domain_changed and getattr(model, "remote_sync_status", None) == "synced":
            model.remote_sync_status = "pending"
            model.remote_sync_error = None

        self._session.flush()
        self._session.commit()
        self._session.refresh(model)
        return self._to_entity(model)

    def delete(self, id: int) -> None:
        """Delete the greenhouse and cascade-delete all descendant entities.

        Cascade is handled by the ORM relationship configuration
        (cascade='all, delete-orphan') on GreenhouseModel.modules.
        """
        model = self._session.get(GreenhouseModel, id)
        if model is not None:
            self._session.delete(model)
            self._session.flush()
            self._session.commit()

    def _to_entity(self, model: GreenhouseModel) -> Greenhouse:
        """Convert an ORM model instance to a domain entity."""
        return Greenhouse(
            id=model.id,
            name=model.name,
            location=model.location,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: Greenhouse) -> GreenhouseModel:
        """Convert a domain entity to an ORM model instance for persistence."""
        return GreenhouseModel(
            name=entity.name,
            location=entity.location,
        )
