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

    # --- Recovery primitives (Spec 022, block D1) ---

    @abstractmethod
    def find_by_remote_id(self, remote_id: str) -> Optional[Snapshot]:
        """Return the snapshot mapped to the given remote_id, or None.

        The remote UUID is stored in the local ``remote_id`` column; the local
        primary key remains an integer autoincrement value.
        """
        ...

    @abstractmethod
    def insert_preserving_remote_id(
        self,
        monitoring_id: int,
        entity: Snapshot,
        remote_id: str,
        raw_storage_path: Optional[str] = None,
        annotated_storage_path: Optional[str] = None,
    ) -> Snapshot:
        """Insert a recovered snapshot under the LOCAL parent monitoring id.

        Import-missing-only: builds a NEW local row with a fresh autoincrement
        id under ``monitoring_id`` (the local parent id), sets remote sync
        metadata to a synced state, records the remote storage paths as given
        (without downloading anything), and never modifies an existing local
        row.

        Raises:
            RecoveredEntityAlreadyExistsError: if a local snapshot already
                exists with the given remote_id.
        """
        ...
