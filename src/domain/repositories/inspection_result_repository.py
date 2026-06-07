"""Repository interface: InspectionResultRepository.

Defines the abstract contract for inspection result persistence operations.
Implementations reside in the infrastructure layer.
"""

from abc import ABC, abstractmethod

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
