"""Repository interface: ModuleRepository.

Defines the abstract contract for module persistence operations.
Implementations reside in the infrastructure layer.
"""

from abc import ABC, abstractmethod
from typing import Optional

from src.domain.entities.module import Module


class ModuleRepository(ABC):
    """Abstract repository for Module entities.

    Contract:
        - get_by_id returns None if no module exists with the given id.
        - delete cascades to all child entities in the hierarchy:
          Module → Monitoring → Snapshot → InspectionResult,
          and Monitoring → MonitoringMetrics.
    """

    @abstractmethod
    def create(self, greenhouse_id: int, module: Module) -> Module:
        """Persist a new module under the given greenhouse. Return it with assigned id."""
        ...

    @abstractmethod
    def get_by_greenhouse(self, greenhouse_id: int) -> list[Module]:
        """Return all modules for the given greenhouse. Returns empty list if none exist."""
        ...

    @abstractmethod
    def get_by_id(self, id: int) -> Optional[Module]:
        """Return the module with the given id, or None if not found."""
        ...

    @abstractmethod
    def update(self, id: int, fields: dict) -> Module:
        """Update module fields (name, crop_type, width_m, length_m). Return updated entity.

        Args:
            id: The module identifier.
            fields: Dictionary with keys to update. Valid keys:
                name, crop_type, width_m, length_m.
        """
        ...

    @abstractmethod
    def delete(self, id: int) -> None:
        """Delete the module and cascade-delete all descendant entities.

        Cascade path: Module → Monitoring → Snapshot → InspectionResult,
        and Monitoring → MonitoringMetrics.
        """
        ...

    # --- Recovery primitives (Spec 022, block D1) ---

    @abstractmethod
    def find_by_remote_id(self, remote_id: str) -> Optional[Module]:
        """Return the module mapped to the given remote_id, or None.

        The remote UUID is stored in the local ``remote_id`` column; the local
        primary key remains an integer autoincrement value.
        """
        ...

    @abstractmethod
    def insert_preserving_remote_id(
        self, greenhouse_id: int, entity: Module, remote_id: str
    ) -> Module:
        """Insert a recovered module under the LOCAL parent greenhouse id.

        Import-missing-only: builds a NEW local row with a fresh autoincrement
        id under ``greenhouse_id`` (the local parent id), sets remote sync
        metadata to a synced state, and never modifies an existing local row.

        Raises:
            RecoveredEntityAlreadyExistsError: if a local module already exists
                with the given remote_id.
        """
        ...
