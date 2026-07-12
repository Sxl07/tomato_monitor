"""Repository interface: SnapshotRepository.

Defines the abstract contract for snapshot persistence operations.
Implementations reside in the infrastructure layer.
"""

from abc import ABC, abstractmethod
from typing import Optional

from src.domain.entities.snapshot import Snapshot


class SnapshotRepository(ABC):
    """Abstract repository for Snapshot entities.

    Contract:
        - get_by_id returns None if no snapshot exists with the given id.
        - Snapshots are deleted only via cascade from their parent Monitoring.
    """

    @abstractmethod
    def create(self, monitoring_id: int, snapshot: Snapshot) -> Snapshot:
        """Persist a new snapshot under the given monitoring. Return it with assigned id."""
        ...

    @abstractmethod
    def get_by_monitoring(self, monitoring_id: int) -> list[Snapshot]:
        """Return all snapshots for the given monitoring. Returns empty list if none exist."""
        ...

    @abstractmethod
    def get_by_id(self, id: int) -> Optional[Snapshot]:
        """Return the snapshot with the given id, or None if not found."""
        ...

    @abstractmethod
    def update_has_detections(self, id: int, has_detections: bool) -> Snapshot:
        """Update the has_detections field on a snapshot. Return updated entity."""
        ...
