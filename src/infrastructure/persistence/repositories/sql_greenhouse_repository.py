"""Concrete SQLAlchemy implementation of GreenhouseRepository.

Persists Greenhouse entities to the SQLite database, converting between
domain dataclasses and ORM models internally.
"""

from typing import Optional

from sqlalchemy.orm import Session

from src.domain.entities.greenhouse import Greenhouse
from src.domain.exceptions import RecoveredEntityAlreadyExistsError
from src.domain.repositories.greenhouse_repository import GreenhouseRepository
from src.infrastructure.persistence.models.greenhouse_model import (
    GreenhouseModel,
    utcnow,
)
from src.infrastructure.persistence.models.user_model import UserModel


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

    def get_all_by_owner(self, owner_user_id: int) -> list[Greenhouse]:
        """Return only the greenhouses owned by the given user (Spec 022)."""
        models = (
            self._session.query(GreenhouseModel)
            .filter(GreenhouseModel.owner_user_id == owner_user_id)
            .all()
        )
        return [self._to_entity(m) for m in models]

    def get_by_id_for_owner(
        self, id: int, owner_user_id: int
    ) -> Optional[Greenhouse]:
        """Return the greenhouse only if it belongs to the given owner."""
        model = self._session.get(GreenhouseModel, id)
        if model is None or model.owner_user_id != owner_user_id:
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
            owner_user_id=model.owner_user_id,
            name=model.name,
            location=model.location,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: Greenhouse) -> GreenhouseModel:
        """Convert a domain entity to an ORM model instance for persistence."""
        return GreenhouseModel(
            owner_user_id=entity.owner_user_id,
            name=entity.name,
            location=entity.location,
        )

    def find_owner_remote_id(self, greenhouse_id: int) -> Optional[str]:
        """Resolve a greenhouse's owner to the owner User's remote_user_id.

        Ownership correlation (Spec 022, Task 3.4):
        ``greenhouse.owner_user_id -> users.id -> users.remote_user_id``. The
        returned value is the Supabase Auth user id (``auth.uid()``).

        Returns None when the greenhouse does not exist, has no local owner
        (``owner_user_id IS NULL``), or the owner User has no ``remote_user_id``.
        """
        model = self._session.get(GreenhouseModel, greenhouse_id)
        if model is None or model.owner_user_id is None:
            return None
        owner = self._session.get(UserModel, model.owner_user_id)
        if owner is None:
            return None
        return owner.remote_user_id

    # --- Recovery primitives (Spec 022, block D1) ---

    def find_by_remote_id(self, remote_id: str) -> Optional[Greenhouse]:
        """Return the greenhouse mapped to the given remote_id, or None."""
        model = (
            self._session.query(GreenhouseModel)
            .filter(GreenhouseModel.remote_id == remote_id)
            .first()
        )
        if model is None:
            return None
        return self._to_entity(model)

    def find_by_owner_and_name(
        self, owner_user_id: int, name: str
    ) -> Optional[Greenhouse]:
        """Return the greenhouse matching (owner_user_id, name), or None."""
        model = (
            self._session.query(GreenhouseModel)
            .filter(
                GreenhouseModel.owner_user_id == owner_user_id,
                GreenhouseModel.name == name,
            )
            .first()
        )
        if model is None:
            return None
        return self._to_entity(model)

    def insert_preserving_remote_id(
        self, entity: Greenhouse, remote_id: str
    ) -> Greenhouse:
        """Insert a recovered greenhouse, mapping remote_id -> local remote_id.

        Import-missing-only: guards against duplicate remote_id, assigns a new
        autoincrement id, and marks the row synced. Mirrors create()'s
        commit-based transactional pattern.
        """
        if self._find_model_by_remote_id(remote_id) is not None:
            raise RecoveredEntityAlreadyExistsError("Greenhouse", remote_id)

        model = GreenhouseModel(
            owner_user_id=entity.owner_user_id,
            name=entity.name,
            location=entity.location,
            remote_id=remote_id,
            remote_sync_status="synced",
            remote_sync_error=None,
            last_synced_at=utcnow(),
        )
        self._session.add(model)
        self._session.flush()
        self._session.commit()
        self._session.refresh(model)
        return self._to_entity(model)

    def _find_model_by_remote_id(self, remote_id: str) -> Optional[GreenhouseModel]:
        """Return the ORM model with the given remote_id, or None."""
        return (
            self._session.query(GreenhouseModel)
            .filter(GreenhouseModel.remote_id == remote_id)
            .first()
        )
