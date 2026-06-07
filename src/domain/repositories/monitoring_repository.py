"""Repository interface: MonitoringRepository.

Defines the abstract contract for monitoring session persistence operations.
Implementations reside in the infrastructure layer.
"""

from abc import ABC, abstractmethod
from typing import Optional

from src.domain.entities.monitoring import Monitoring


class MonitoringRepository(ABC):
    """Abstract repository for Monitoring entities.

    Contract:
        - get_by_id returns None if no monitoring exists with the given id.
        - delete cascades to all child entities in the hierarchy:
          Monitoring → Snapshot → InspectionResult, and
          Monitoring → MonitoringMetrics.
    """

    @abstractmethod
    def create(self, module_id: int, monitoring: Monitoring) -> Monitoring:
        """Persist a new monitoring session under the given module.

        Return it with assigned id, status='initializing', and started_at set.
        """
        ...

    @abstractmethod
    def get_by_module(self, module_id: int) -> list[Monitoring]:
        """Return all monitorings for the given module. Returns empty list if none exist."""
        ...

    @abstractmethod
    def get_by_id(self, id: int) -> Optional[Monitoring]:
        """Return the monitoring with the given id, or None if not found."""
        ...

    @abstractmethod
    def update_status(self, id: int, status: str) -> Monitoring:
        """Update the monitoring session status. Return the updated entity."""
        ...

    @abstractmethod
    def update_counters(
        self, id: int, total_snapshots: int, total_detections: int
    ) -> Monitoring:
        """Update snapshot and detection counters. Return the updated entity."""
        ...

    @abstractmethod
    def delete(self, id: int) -> None:
        """Delete the monitoring and cascade-delete all descendant entities.

        Cascade path: Monitoring → Snapshot → InspectionResult, and
        Monitoring → MonitoringMetrics.
        """
        ...
