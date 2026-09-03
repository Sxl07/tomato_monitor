"""Concrete SQLAlchemy implementation of MonitoringRepository.

Persists Monitoring entities to the SQLite database, converting between
domain dataclasses and ORM models internally. Uses the MonitoringStatus
value object to validate state transitions.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from src.domain.entities.monitoring import Monitoring
from src.domain.repositories.monitoring_repository import MonitoringRepository
from src.domain.value_objects.monitoring_status import MonitoringState, MonitoringStatus
from src.infrastructure.persistence.models.monitoring_model import MonitoringModel


def _utcnow() -> datetime:
    """Return current UTC time as timezone-naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SqlMonitoringRepository(MonitoringRepository):
    """SQLAlchemy-backed repository for Monitoring entities.

    All operations use the injected Session instance. Cascade deletes are
    handled by the ORM relationship configuration on MonitoringModel.
    State transition validation uses MonitoringStatus from the domain layer.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, module_id: int, monitoring: Monitoring) -> Monitoring:
        """Persist a new monitoring session under the given module.

        Assigns status='initializing' and started_at=UTC now regardless of
        input values, ensuring consistent initial state.
        """
        model = MonitoringModel(
            module_id=module_id,
            status="initializing",
            started_at=_utcnow(),
            width_m=monitoring.width_m,
            length_m=monitoring.length_m,
            notes=monitoring.notes,
            total_snapshots=0,
            total_detections=0,
            created_by_user_id=monitoring.created_by_user_id,
            sync_status=monitoring.sync_status,
            video_path=monitoring.video_path,
        )
        self._session.add(model)
        self._session.flush()
        self._session.commit()
        return self._to_entity(model)

    def get_by_module(self, module_id: int) -> list[Monitoring]:
        """Return all monitorings for the given module, ordered by started_at descending."""
        models = (
            self._session.query(MonitoringModel)
            .filter(MonitoringModel.module_id == module_id)
            .order_by(MonitoringModel.started_at.desc())
            .all()
        )
        return [self._to_entity(m) for m in models]

    def get_active(self) -> list[Monitoring]:
        """Return all monitorings in a non-terminal (active) status.

        Ordered by started_at descending. Used by startup reconciliation.
        """
        active_statuses = [
            MonitoringState.INITIALIZING.value,
            MonitoringState.RUNNING.value,
            MonitoringState.PAUSED.value,
            MonitoringState.FINISHING.value,
            MonitoringState.ANALYZING.value,
        ]
        models = (
            self._session.query(MonitoringModel)
            .filter(MonitoringModel.status.in_(active_statuses))
            .order_by(MonitoringModel.started_at.desc())
            .all()
        )
        return [self._to_entity(m) for m in models]

    def get_by_id(self, id: int) -> Optional[Monitoring]:
        """Return the monitoring with the given id, or None if not found."""
        model = self._session.get(MonitoringModel, id)
        if model is None:
            return None
        return self._to_entity(model)

    def update_status(self, id: int, status: str) -> Monitoring:
        """Update the monitoring session status with state machine validation.

        Steps:
        1. Load the monitoring from DB.
        2. Create MonitoringStatus from the current state.
        3. Call transition_to with the target state — raises InvalidTransitionError
           if the transition is not allowed.
        4. If target is 'completed' or 'aborted', set completed_at = UTC now.
        5. Update the model and flush.
        """
        model = self._session.get(MonitoringModel, id)
        if model is None:
            raise ValueError(f"Monitoring with id={id} not found")

        # Validate transition using the domain state machine
        current_status = MonitoringStatus(MonitoringState(model.status))
        target_state = MonitoringState(status)
        current_status.transition_to(target_state)

        # Apply the transition
        model.status = status

        # Set completed_at on terminal transitions (completed/aborted)
        if target_state in (MonitoringState.COMPLETED, MonitoringState.ABORTED):
            model.completed_at = _utcnow()

        self._session.flush()
        self._session.commit()
        return self._to_entity(model)

    def update_counters(
        self, id: int, total_snapshots: int, total_detections: int
    ) -> Monitoring:
        """Update snapshot and detection counters. Return the updated entity."""
        model = self._session.get(MonitoringModel, id)
        if model is None:
            raise ValueError(f"Monitoring with id={id} not found")

        model.total_snapshots = total_snapshots
        model.total_detections = total_detections
        self._session.flush()
        self._session.commit()
        return self._to_entity(model)

    def update_video_path(self, id: int, video_path: Optional[str]) -> Monitoring:
        """Persist the monitoring video path (relative) and return the entity.

        Stores the value as-is; None clears the path. No filesystem resolution
        or traversal validation happens here.
        """
        model = self._session.get(MonitoringModel, id)
        if model is None:
            raise ValueError(f"Monitoring with id={id} not found")

        model.video_path = video_path
        self._session.flush()
        self._session.commit()
        return self._to_entity(model)

    def delete(self, id: int) -> None:
        """Delete the monitoring and cascade-delete all descendant entities.

        Cascade is handled by the ORM relationship configuration
        (cascade='all, delete-orphan') on MonitoringModel.snapshots and .metrics.
        """
        model = self._session.get(MonitoringModel, id)
        if model is not None:
            self._session.delete(model)
            self._session.flush()

    def has_synced_descendants(self, id: int) -> bool:
        """Strict sync guard: True if the monitoring or ANY descendant is synced."""
        from src.infrastructure.persistence.models.snapshot_model import SnapshotModel
        from src.infrastructure.persistence.models.inspection_result_model import (
            InspectionResultModel,
        )
        from src.infrastructure.persistence.models.monitoring_metrics_model import (
            MonitoringMetricsModel,
        )

        _SYNCED = "synced"

        # Monitoring itself.
        model = self._session.get(MonitoringModel, id)
        if model is not None and getattr(model, "remote_sync_status", None) == _SYNCED:
            return True

        # Any snapshot.
        snapshot_synced = (
            self._session.query(SnapshotModel.id)
            .filter(
                SnapshotModel.monitoring_id == id,
                SnapshotModel.remote_sync_status == _SYNCED,
            )
            .first()
        )
        if snapshot_synced is not None:
            return True

        # Any inspection result (joined via its snapshot's monitoring).
        result_synced = (
            self._session.query(InspectionResultModel.id)
            .join(SnapshotModel, InspectionResultModel.snapshot_id == SnapshotModel.id)
            .filter(
                SnapshotModel.monitoring_id == id,
                InspectionResultModel.remote_sync_status == _SYNCED,
            )
            .first()
        )
        if result_synced is not None:
            return True

        # The monitoring metrics.
        metrics_synced = (
            self._session.query(MonitoringMetricsModel.id)
            .filter(
                MonitoringMetricsModel.monitoring_id == id,
                MonitoringMetricsModel.remote_sync_status == _SYNCED,
            )
            .first()
        )
        return metrics_synced is not None

    def clear_analysis_results(self, id: int) -> None:
        """Delete snapshots (cascade to results) and metrics; keep monitoring+video."""
        from src.infrastructure.persistence.models.snapshot_model import SnapshotModel
        from src.infrastructure.persistence.models.monitoring_metrics_model import (
            MonitoringMetricsModel,
        )

        # Delete snapshots via ORM so InspectionResult cascade (delete-orphan) runs.
        snapshots = (
            self._session.query(SnapshotModel)
            .filter(SnapshotModel.monitoring_id == id)
            .all()
        )
        for snap in snapshots:
            self._session.delete(snap)

        metrics = (
            self._session.query(MonitoringMetricsModel)
            .filter(MonitoringMetricsModel.monitoring_id == id)
            .all()
        )
        for m in metrics:
            self._session.delete(m)

        self._session.flush()
        self._session.commit()

    def reset_for_reprocess(self, id: int) -> Monitoring:
        """Reset a terminal monitoring to 'analyzing' (audited reset, not a FSM edge).

        Deliberately bypasses MonitoringStatus.transition_to: reprocess is an
        explicit analysis restart, not a normal business transition, so no
        completed→analyzing edge is added to the FSM.
        """
        model = self._session.get(MonitoringModel, id)
        if model is None:
            raise ValueError(f"Monitoring with id={id} not found")

        model.status = MonitoringState.ANALYZING.value
        model.completed_at = None
        self._session.flush()
        self._session.commit()
        return self._to_entity(model)

    def list_all(self) -> list[Monitoring]:
        """Return all monitorings, ordered by started_at descending."""
        models = (
            self._session.query(MonitoringModel)
            .order_by(MonitoringModel.started_at.desc())
            .all()
        )
        return [self._to_entity(m) for m in models]

    def update_sync_status(self, ids: list[int], status: str) -> None:
        """Update sync_status for the given monitoring ids."""
        if not ids:
            return
        self._session.query(MonitoringModel).filter(
            MonitoringModel.id.in_(ids)
        ).update({"sync_status": status}, synchronize_session="fetch")
        self._session.flush()
        self._session.commit()

    def _to_entity(self, model: MonitoringModel) -> Monitoring:
        """Convert an ORM model instance to a domain entity."""
        return Monitoring(
            id=model.id,
            module_id=model.module_id,
            status=model.status,
            started_at=model.started_at,
            completed_at=model.completed_at,
            width_m=model.width_m,
            length_m=model.length_m,
            notes=model.notes,
            total_snapshots=model.total_snapshots,
            total_detections=model.total_detections,
            created_by_user_id=model.created_by_user_id,
            sync_status=model.sync_status,
            video_path=model.video_path,
        )
