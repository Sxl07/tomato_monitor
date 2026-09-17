"""Repository interface: MonitoringMetricsRepository.

Defines the abstract contract for monitoring metrics persistence operations.
Implementations reside in the infrastructure layer.
"""

from abc import ABC, abstractmethod
from typing import Optional

from src.domain.entities.monitoring_metrics import MonitoringMetrics


class MonitoringMetricsRepository(ABC):
    """Abstract repository for MonitoringMetrics entities.

    Contract:
        - get_by_monitoring returns None if no metrics exist for the given
          monitoring id.
        - Each monitoring has at most one MonitoringMetrics record (1:1 relation).
        - Metrics are deleted only via cascade from their parent Monitoring.
    """

    @abstractmethod
    def create(
        self, monitoring_id: int, metrics: MonitoringMetrics
    ) -> MonitoringMetrics:
        """Persist monitoring metrics for the given monitoring.

        Return the created entity with assigned id.
        """
        ...

    @abstractmethod
    def create_pending_for_finalization(
        self, monitoring_id: int, metrics: MonitoringMetrics
    ) -> MonitoringMetrics:
        """Persist metrics for a session being finalized (running or analyzing).

        Flushes but does NOT commit. The subsequent status transition
        to 'completed' in the same session will commit both.
        """
        ...

    @abstractmethod
    def get_by_monitoring(self, monitoring_id: int) -> Optional[MonitoringMetrics]:
        """Return the metrics for the given monitoring, or None if not found."""
        ...

    @abstractmethod
    def get_by_monitoring_ids(
        self, ids: list[int]
    ) -> dict[int, MonitoringMetrics]:
        """Bulk-fetch metrics for several monitorings in a single query.

        Args:
            ids: List of monitoring ids. An empty list returns {}.

        Returns:
            Mapping monitoring_id -> MonitoringMetrics. Monitoring ids without
            metrics are simply absent from the dict (never None values).
        """
        ...

    # --- Recovery primitives (Spec 022, block D1) ---

    @abstractmethod
    def find_by_remote_id(self, remote_id: str) -> Optional[MonitoringMetrics]:
        """Return the metrics mapped to the given remote_id, or None.

        The remote UUID is stored in the local ``remote_id`` column; the local
        primary key remains an integer autoincrement value.
        """
        ...

    @abstractmethod
    def insert_preserving_remote_id(
        self, monitoring_id: int, entity: MonitoringMetrics, remote_id: str
    ) -> MonitoringMetrics:
        """Insert recovered metrics under the LOCAL parent monitoring id.

        Import-missing-only: builds a NEW local row with a fresh autoincrement
        id under ``monitoring_id`` (the local parent id), sets remote sync
        metadata to a synced state, and never modifies an existing local row.
        Unlike create(), this does NOT validate the monitoring status (recovery
        rebuilds terminal data as-is).

        Raises:
            RecoveredEntityAlreadyExistsError: if local metrics already exist
                with the given remote_id.
        """
        ...
