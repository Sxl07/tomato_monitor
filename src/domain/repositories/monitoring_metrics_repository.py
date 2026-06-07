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
    def get_by_monitoring(self, monitoring_id: int) -> Optional[MonitoringMetrics]:
        """Return the metrics for the given monitoring, or None if not found."""
        ...
