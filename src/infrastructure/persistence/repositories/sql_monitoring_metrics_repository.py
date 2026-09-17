"""Concrete SQLAlchemy implementation of MonitoringMetricsRepository.

Persists MonitoringMetrics entities to the SQLite database, converting between
domain dataclasses and ORM models internally.
"""

from typing import Optional

from sqlalchemy.orm import Session

from src.domain.entities.monitoring_metrics import MonitoringMetrics
from src.domain.exceptions import (
    MetricsNotAllowedError,
    ParentNotFoundError,
    RecoveredEntityAlreadyExistsError,
)
from src.domain.repositories.monitoring_metrics_repository import (
    MonitoringMetricsRepository,
)
from src.infrastructure.persistence.models.monitoring_metrics_model import (
    MonitoringMetricsModel,
    utcnow,
)
from src.infrastructure.persistence.models.monitoring_model import MonitoringModel

# Terminal statuses that allow metrics creation
_ALLOWED_STATUSES = {"completed", "aborted"}

# Statuses that allow pending metrics creation (during finalization)
_PENDING_ALLOWED_STATUSES = {"running", "analyzing"}


class SqlMonitoringMetricsRepository(MonitoringMetricsRepository):
    """SQLAlchemy-backed repository for MonitoringMetrics entities.

    All operations use the injected Session instance. The UNIQUE constraint
    on monitoring_id ensures at most one metrics record per monitoring.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, monitoring_id: int, metrics: MonitoringMetrics
    ) -> MonitoringMetrics:
        """Persist monitoring metrics for the given monitoring.

        Guards:
            - Raises ParentNotFoundError if monitoring_id does not exist.
            - Raises MetricsNotAllowedError if monitoring status is not in
              {completed, aborted}.
            - The UNIQUE constraint on monitoring_id will raise IntegrityError
              if metrics already exist for the monitoring.
        """
        monitoring = self._session.get(MonitoringModel, monitoring_id)
        if monitoring is None:
            raise ParentNotFoundError("Monitoring", monitoring_id)

        if monitoring.status not in _ALLOWED_STATUSES:
            raise MetricsNotAllowedError(monitoring_id, monitoring.status)

        model = self._to_model(monitoring_id, metrics)
        self._session.add(model)
        self._session.flush()
        return self._to_entity(model)

    def create_pending_for_finalization(
        self, monitoring_id: int, metrics: MonitoringMetrics
    ) -> MonitoringMetrics:
        """Persist pending metrics. Flushes without commit.

        Guards:
            - Raises ParentNotFoundError if monitoring_id does not exist.
            - Raises MetricsNotAllowedError if monitoring status is not in
              {running, analyzing}.
        """
        monitoring = self._session.get(MonitoringModel, monitoring_id)
        if monitoring is None:
            raise ParentNotFoundError("Monitoring", monitoring_id)
        if monitoring.status not in _PENDING_ALLOWED_STATUSES:
            raise MetricsNotAllowedError(monitoring_id, monitoring.status)

        model = self._to_model(monitoring_id, metrics)
        self._session.add(model)
        self._session.flush()
        return self._to_entity(model)

    def get_by_monitoring(self, monitoring_id: int) -> Optional[MonitoringMetrics]:
        """Return the metrics for the given monitoring, or None if not found."""
        model = (
            self._session.query(MonitoringMetricsModel)
            .filter(MonitoringMetricsModel.monitoring_id == monitoring_id)
            .first()
        )
        if model is None:
            return None
        return self._to_entity(model)

    def get_by_monitoring_ids(
        self, ids: list[int]
    ) -> dict[int, MonitoringMetrics]:
        """Bulk-fetch metrics for several monitorings in a single query.

        Uses ``monitoring_id IN (:ids)``. Empty ``ids`` -> {}. Monitoring ids
        without metrics are absent from the returned dict.
        """
        if not ids:
            return {}
        models = (
            self._session.query(MonitoringMetricsModel)
            .filter(MonitoringMetricsModel.monitoring_id.in_(ids))
            .all()
        )
        return {m.monitoring_id: self._to_entity(m) for m in models}

    def find_by_remote_id(self, remote_id: str) -> Optional[MonitoringMetrics]:
        """Return the metrics mapped to the given remote_id, or None."""
        model = (
            self._session.query(MonitoringMetricsModel)
            .filter(MonitoringMetricsModel.remote_id == remote_id)
            .first()
        )
        if model is None:
            return None
        return self._to_entity(model)

    def insert_preserving_remote_id(
        self, monitoring_id: int, entity: MonitoringMetrics, remote_id: str
    ) -> MonitoringMetrics:
        """Insert recovered metrics under the LOCAL parent monitoring id.

        Import-missing-only: guards against duplicate remote_id, assigns a new
        autoincrement id, and marks the row synced. Does NOT validate the
        monitoring status. Unlike create() (flush-only), this commits so the
        recovered row is a durable local checkpoint that survives a later
        remote failure; rolls back and re-raises on error.
        """
        existing = (
            self._session.query(MonitoringMetricsModel)
            .filter(MonitoringMetricsModel.remote_id == remote_id)
            .first()
        )
        if existing is not None:
            raise RecoveredEntityAlreadyExistsError("MonitoringMetrics", remote_id)

        model = self._to_model(monitoring_id, entity)
        if entity.computed_at is not None:
            model.computed_at = entity.computed_at
        model.remote_id = remote_id
        model.remote_sync_status = "synced"
        model.remote_sync_error = None
        model.last_synced_at = utcnow()
        # Recovery is a durable LOCAL checkpoint: commit so the recovered row
        # survives even if a later remote request fails. Distinct transactional
        # context from create() (which only flushes); create() is unchanged.
        try:
            self._session.add(model)
            self._session.flush()
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        self._session.refresh(model)
        return self._to_entity(model)

    def _to_model(self, monitoring_id: int, metrics: MonitoringMetrics) -> MonitoringMetricsModel:
        """Convert domain entity to ORM model."""
        return MonitoringMetricsModel(
            monitoring_id=monitoring_id,
            total_tomatoes=metrics.total_tomatoes,
            healthy_count=metrics.healthy_count,
            unhealthy_count=metrics.unhealthy_count,
            pct_healthy=metrics.pct_healthy,
            pct_unhealthy=metrics.pct_unhealthy,
            pct_green=metrics.pct_green,
            pct_breaker=metrics.pct_breaker,
            pct_turning=metrics.pct_turning,
            pct_pink=metrics.pct_pink,
            pct_light_red=metrics.pct_light_red,
            pct_red=metrics.pct_red,
            snapshots_with_detections=metrics.snapshots_with_detections,
        )

    def _to_entity(self, model: MonitoringMetricsModel) -> MonitoringMetrics:
        """Convert an ORM model instance to a domain entity."""
        return MonitoringMetrics(
            id=model.id,
            monitoring_id=model.monitoring_id,
            total_tomatoes=model.total_tomatoes,
            healthy_count=model.healthy_count,
            unhealthy_count=model.unhealthy_count,
            pct_healthy=model.pct_healthy,
            pct_unhealthy=model.pct_unhealthy,
            pct_green=model.pct_green,
            pct_breaker=model.pct_breaker,
            pct_turning=model.pct_turning,
            pct_pink=model.pct_pink,
            pct_light_red=model.pct_light_red,
            pct_red=model.pct_red,
            snapshots_with_detections=model.snapshots_with_detections,
            computed_at=model.computed_at,
        )
