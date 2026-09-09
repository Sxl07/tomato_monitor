"""Repository interface: GreenhouseRepository.

Defines the abstract contract for greenhouse persistence operations.
Implementations reside in the infrastructure layer.
"""

from abc import ABC, abstractmethod
from typing import Optional

from src.domain.entities.greenhouse import Greenhouse


class GreenhouseRepository(ABC):
    """Abstract repository for Greenhouse entities.

    Contract:
        - get_by_id returns None if no greenhouse exists with the given id.
        - delete cascades to all child entities in the hierarchy:
          Greenhouse → Module → Monitoring → Snapshot → InspectionResult,
          and Monitoring → MonitoringMetrics.
    """

    @abstractmethod
    def create(self, greenhouse: Greenhouse) -> Greenhouse:
        """Persist a new greenhouse and return it with the assigned id."""
        ...

    @abstractmethod
    def get_all(self) -> list[Greenhouse]:
        """Return all greenhouses. Returns an empty list if none exist."""
        ...

    @abstractmethod
    def get_by_id(self, id: int) -> Optional[Greenhouse]:
        """Return the greenhouse with the given id, or None if not found."""
        ...

    @abstractmethod
    def get_all_by_owner(self, owner_user_id: int) -> list[Greenhouse]:
        """Return only the greenhouses owned by the given user (Spec 022).

        Local multiuser isolation: authenticated flows must list only the
        current user's own greenhouses. Returns an empty list if none.
        """
        ...

    @abstractmethod
    def get_by_id_for_owner(
        self, id: int, owner_user_id: int
    ) -> Optional[Greenhouse]:
        """Return the greenhouse only if it belongs to the given owner.

        Returns None when the greenhouse does not exist OR is owned by a
        different user. Used to prevent access to another user's hierarchy.
        """
        ...

    @abstractmethod
    def update(self, id: int, name: str, location: Optional[str]) -> Greenhouse:
        """Update a greenhouse's name and location. Return the updated entity."""
        ...

    @abstractmethod
    def delete(self, id: int) -> None:
        """Delete the greenhouse and cascade-delete all descendant entities.

        Cascade path: Greenhouse → Module → Monitoring → Snapshot →
        InspectionResult, and Monitoring → MonitoringMetrics.
        """
        ...

    @abstractmethod
    def find_owner_remote_id(self, greenhouse_id: int) -> Optional[str]:
        """Resolve a greenhouse's owner to the owner User's remote_user_id.

        Correlation: ``greenhouse.owner_user_id -> users.id ->
        users.remote_user_id`` (the Supabase Auth user id / ``auth.uid()``).
        Returns None if the greenhouse is missing, has no local owner, or the
        owner has no ``remote_user_id``.
        """
        ...
