"""Repository interface: InspectionResultRepository.

Defines the abstract contract for inspection result persistence operations.
Implementations reside in the infrastructure layer.
"""

from abc import ABC, abstractmethod
from typing import Optional

from src.domain.entities.inspection_result import DetectionInspectionResult


class InspectionResultRepository(ABC):
    """Abstract repository for DetectionInspectionResult entities.

    Contract:
        - Inspection results are deleted only via cascade from their parent
          Snapshot (which cascades from Monitoring → Module → Greenhouse).
        - get_by_snapshot and get_by_monitoring return empty lists if no
          results exist for the given identifier.
    """

    @abstractmethod
    def create(
        self, snapshot_id: int, result: DetectionInspectionResult
    ) -> DetectionInspectionResult:
        """Persist a new inspection result under the given snapshot.

        Return it with the assigned id.
        """
        ...

    @abstractmethod
    def get_by_snapshot(self, snapshot_id: int) -> list[DetectionInspectionResult]:
        """Return all inspection results for the given snapshot.

        Returns an empty list if none exist.
        """
        ...

    @abstractmethod
    def get_by_monitoring(self, monitoring_id: int) -> list[DetectionInspectionResult]:
        """Return all inspection results across all snapshots of the given monitoring.

        This aggregates results from all snapshots belonging to the monitoring.
        Returns an empty list if none exist.
        """
        ...

    # --- Recovery primitives (Spec 022, block D1) ---

    @abstractmethod
    def find_by_remote_id(
        self, remote_id: str
    ) -> Optional[DetectionInspectionResult]:
        """Return the inspection result mapped to the given remote_id, or None.

        The remote UUID is stored in the local ``remote_id`` column; the local
        primary key remains an integer autoincrement value.
        """
        ...

    @abstractmethod
    def insert_preserving_remote_id(
        self, snapshot_id: int, entity: DetectionInspectionResult, remote_id: str
    ) -> DetectionInspectionResult:
        """Insert a recovered inspection result under the LOCAL parent snapshot id.

        Import-missing-only: builds a NEW local row with a fresh autoincrement
        id under ``snapshot_id`` (the local parent id), sets remote sync
        metadata to a synced state, and never modifies an existing local row.

        Raises:
            RecoveredEntityAlreadyExistsError: if a local inspection result
                already exists with the given remote_id.
        """
        ...
