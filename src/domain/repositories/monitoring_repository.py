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
    def get_active(self) -> list[Monitoring]:
        """Return all monitorings currently in a non-terminal (active) status.

        Active statuses are initializing, running, paused, finishing, analyzing.
        Used by startup reconciliation to detect sessions orphaned by a process
        restart. Returns an empty list if none exist.
        """
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
    def update_video_path(
        self,
        id: int,
        video_path: Optional[str],
    ) -> Monitoring:
        """Persist the monitoring video path and return the updated entity.

        The path is stored as-is (a relative path from the project root, or
        None). None clears the stored path. No filesystem resolution or
        traversal validation is performed here.
        """
        ...

    @abstractmethod
    def delete(self, id: int) -> None:
        """Delete the monitoring and cascade-delete all descendant entities.

        Cascade path: Monitoring → Snapshot → InspectionResult, and
        Monitoring → MonitoringMetrics.
        """
        ...

    @abstractmethod
    def has_synced_descendants(self, id: int) -> bool:
        """Strict sync guard for destructive reprocess.

        Return True if the Monitoring itself, ANY related Snapshot, ANY related
        DetectionInspectionResult, or the related MonitoringMetrics has
        remote_sync_status == "synced" (a single synced entity is enough).
        """
        ...

    @abstractmethod
    def clear_analysis_results(self, id: int) -> None:
        """Delete a monitoring's analysis results for reprocess.

        Removes all Snapshots (cascading to InspectionResults) and the
        MonitoringMetrics for the given monitoring. Does NOT delete the
        Monitoring itself and NEVER touches the recorded video.
        """
        ...

    @abstractmethod
    def reset_for_reprocess(self, id: int) -> Monitoring:
        """Reset a terminal monitoring to 'analyzing' for a controlled reprocess.

        This is an explicit, audited analysis RESET — NOT a normal business
        transition. It does not add a completed→analyzing edge to the FSM and
        does not validate the transition through MonitoringStatus.
        """
        ...

    @abstractmethod
    def list_all(self) -> list[Monitoring]:
        """Return all monitorings, ordered by started_at descending."""
        ...

    @abstractmethod
    def update_sync_status(self, ids: list[int], status: str) -> None:
        """Update sync_status for the given monitoring ids."""
        ...
