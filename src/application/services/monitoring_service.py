"""MonitoringService: orchestrates the monitoring session lifecycle.

Creates, controls, and finalizes monitoring sessions. Spawns a
MonitoringWorker in a daemon thread for the capture loop and
communicates with it via threading.Event signals.

Enforces:
- One active session per module (non-terminal statuses).
- Valid state machine transitions via MonitoringStatus value object.
- Metrics computation on terminal states (completed, aborted).
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from sqlalchemy.orm import Session

from src.application.services.log_service import LogService
from src.application.services.monitoring_worker import MonitoringWorker
from src.domain.entities.monitoring import Monitoring
from src.domain.entities.monitoring_metrics import MonitoringMetrics
from src.domain.exceptions import (
    DomainError,
    InvalidTransitionError,
    ParentNotFoundError,
)
from src.domain.interfaces.frame_source import FrameSource
from src.domain.repositories.inspection_result_repository import (
    InspectionResultRepository,
)
from src.domain.repositories.module_repository import ModuleRepository
from src.domain.repositories.monitoring_metrics_repository import (
    MonitoringMetricsRepository,
)
from src.domain.repositories.monitoring_repository import MonitoringRepository
from src.domain.repositories.snapshot_repository import SnapshotRepository
from src.domain.value_objects.monitoring_status import MonitoringState, MonitoringStatus
from src.infrastructure.vision.snapshot_inference_runner import SnapshotInferenceRunner

logger = logging.getLogger(__name__)

# Statuses considered "active" (non-terminal) for the one-session-per-module rule.
_ACTIVE_STATUSES = {
    MonitoringState.INITIALIZING.value,
    MonitoringState.RUNNING.value,
    MonitoringState.PAUSED.value,
    MonitoringState.FINISHING.value,
}


class ActiveSessionError(DomainError):
    """Module already has an active monitoring session."""

    def __init__(self, module_id: int, existing_monitoring_id: int) -> None:
        self.module_id = module_id
        self.existing_monitoring_id = existing_monitoring_id
        super().__init__(
            f"Module {module_id} already has an active monitoring session "
            f"(monitoring_id={existing_monitoring_id}). "
            f"Only one active session per module is allowed."
        )


class MonitoringNotFoundError(DomainError):
    """Monitoring session not found."""

    def __init__(self, monitoring_id: int) -> None:
        self.monitoring_id = monitoring_id
        super().__init__(f"Monitoring session with id {monitoring_id} not found.")


class MonitoringService:
    """Orchestrates monitoring session lifecycle.

    Responsibilities:
    - Create and start monitoring sessions.
    - Signal pause/resume/abort/complete to the background worker.
    - Compute and persist aggregated metrics.
    - Enforce one-active-session-per-module invariant.
    """

    def __init__(
        self,
        monitoring_repo: MonitoringRepository,
        snapshot_repo: SnapshotRepository,
        inspection_result_repo: InspectionResultRepository,
        metrics_repo: MonitoringMetricsRepository,
        module_repo: ModuleRepository,
    ) -> None:
        self._monitoring_repo = monitoring_repo
        self._snapshot_repo = snapshot_repo
        self._inspection_result_repo = inspection_result_repo
        self._metrics_repo = metrics_repo
        self._module_repo = module_repo

        # Track active workers keyed by monitoring_id.
        self._active_workers: dict[int, MonitoringWorker] = {}

    def start_session(
        self,
        module_id: int,
        width_m: float,
        length_m: float,
        notes: Optional[str],
        frame_source: FrameSource,
        inference_runner: SnapshotInferenceRunner,
        db_session: Session,
        log_service: Optional[LogService] = None,
    ) -> Monitoring:
        """Create a monitoring session and spawn the background worker.

        Args:
            module_id: The module to monitor (must exist).
            width_m: Module width in meters (confirmed by farmer).
            length_m: Module length in meters (confirmed by farmer).
            notes: Optional farmer notes for this session.
            frame_source: Camera or video frame source.
            inference_runner: Pre-loaded inference pipeline.
            db_session: SQLAlchemy session for the worker to commit results.
            log_service: Optional LogService for emitting activity log entries.

        Returns:
            The created Monitoring entity with status 'initializing'.

        Raises:
            ParentNotFoundError: If module_id does not exist.
            ActiveSessionError: If the module already has a non-terminal session.
        """
        # Validate module exists.
        module = self._module_repo.get_by_id(module_id)
        if module is None:
            raise ParentNotFoundError("Module", module_id)

        # Enforce one active session per module.
        existing = self._monitoring_repo.get_by_module(module_id)
        for m in existing:
            if m.status in _ACTIVE_STATUSES:
                raise ActiveSessionError(module_id, m.id)

        # Create monitoring entity with initializing status.
        monitoring = Monitoring(
            module_id=module_id,
            width_m=width_m,
            length_m=length_m,
            notes=notes,
        )
        monitoring = self._monitoring_repo.create(module_id, monitoring)

        # Spawn the background worker in a daemon thread.
        worker = MonitoringWorker(
            monitoring_id=monitoring.id,
            frame_source=frame_source,
            inference_runner=inference_runner,
            snapshot_repo=self._snapshot_repo,
            inspection_result_repo=self._inspection_result_repo,
            monitoring_repo=self._monitoring_repo,
            db_session=db_session,
            log_service=log_service,
        )
        self._active_workers[monitoring.id] = worker

        thread = threading.Thread(
            target=self._run_worker,
            args=(monitoring.id, worker),
            daemon=True,
            name=f"monitoring-worker-{monitoring.id}",
        )
        thread.start()

        return monitoring

    def pause_session(self, monitoring_id: int) -> Monitoring:
        """Signal the worker to pause and update session status.

        Args:
            monitoring_id: The monitoring session to pause.

        Returns:
            Updated Monitoring entity with status 'paused'.

        Raises:
            MonitoringNotFoundError: If monitoring_id does not exist.
            InvalidTransitionError: If current status does not allow pausing.
        """
        monitoring = self._get_monitoring_or_raise(monitoring_id)

        # Validate state transition via value object.
        status = MonitoringStatus(MonitoringState(monitoring.status))
        status.transition_to(MonitoringState.PAUSED)

        # Signal the worker to pause.
        worker = self._active_workers.get(monitoring_id)
        if worker is not None:
            worker.pause_event.set()

        # Persist the status change.
        return self._monitoring_repo.update_status(
            monitoring_id, MonitoringState.PAUSED.value
        )

    def resume_session(self, monitoring_id: int) -> Monitoring:
        """Signal the worker to resume and update session status.

        Args:
            monitoring_id: The monitoring session to resume.

        Returns:
            Updated Monitoring entity with status 'running'.

        Raises:
            MonitoringNotFoundError: If monitoring_id does not exist.
            InvalidTransitionError: If current status does not allow resuming.
        """
        monitoring = self._get_monitoring_or_raise(monitoring_id)

        # Validate state transition.
        status = MonitoringStatus(MonitoringState(monitoring.status))
        status.transition_to(MonitoringState.RUNNING)

        # Clear the pause signal so the worker resumes.
        worker = self._active_workers.get(monitoring_id)
        if worker is not None:
            worker.pause_event.clear()

        # Persist the status change.
        return self._monitoring_repo.update_status(
            monitoring_id, MonitoringState.RUNNING.value
        )

    def abort_session(self, monitoring_id: int) -> Monitoring:
        """Abort the session, compute partial metrics, and signal the worker to stop.

        Args:
            monitoring_id: The monitoring session to abort.

        Returns:
            Updated Monitoring entity with status 'aborted'.

        Raises:
            MonitoringNotFoundError: If monitoring_id does not exist.
            InvalidTransitionError: If current status does not allow aborting.
        """
        monitoring = self._get_monitoring_or_raise(monitoring_id)

        # Validate state transition.
        status = MonitoringStatus(MonitoringState(monitoring.status))
        status.transition_to(MonitoringState.ABORTED)

        # Signal the worker to stop.
        worker = self._active_workers.get(monitoring_id)
        if worker is not None:
            worker.abort_event.set()
            # Also clear pause in case it was paused.
            worker.pause_event.clear()

        # Persist the status change.
        monitoring = self._monitoring_repo.update_status(
            monitoring_id, MonitoringState.ABORTED.value
        )

        # Compute partial metrics.
        self._compute_metrics(monitoring_id)

        # Clean up worker reference.
        self._active_workers.pop(monitoring_id, None)

        return monitoring

    def complete_session(self, monitoring_id: int) -> Monitoring:
        """Signal traversal complete, compute metrics, finalize session.

        Args:
            monitoring_id: The monitoring session to complete.

        Returns:
            Updated Monitoring entity with status 'completed'.

        Raises:
            MonitoringNotFoundError: If monitoring_id does not exist.
            InvalidTransitionError: If current status does not allow completing.
        """
        monitoring = self._get_monitoring_or_raise(monitoring_id)

        # Validate running → finishing transition.
        status = MonitoringStatus(MonitoringState(monitoring.status))
        status.transition_to(MonitoringState.FINISHING)

        # Signal the worker to finish.
        worker = self._active_workers.get(monitoring_id)
        if worker is not None:
            worker.complete_event.set()
            # Also clear pause in case it was paused.
            worker.pause_event.clear()

        # Transition to finishing.
        self._monitoring_repo.update_status(
            monitoring_id, MonitoringState.FINISHING.value
        )

        # Compute full metrics.
        self._compute_metrics(monitoring_id)

        # Transition finishing → completed.
        monitoring = self._monitoring_repo.update_status(
            monitoring_id, MonitoringState.COMPLETED.value
        )

        # Clean up worker reference.
        self._active_workers.pop(monitoring_id, None)

        return monitoring

    def get_status(self, monitoring_id: int) -> Monitoring:
        """Return the current monitoring session state and counters.

        Args:
            monitoring_id: The monitoring session to query.

        Returns:
            The Monitoring entity with current status and counters.

        Raises:
            MonitoringNotFoundError: If monitoring_id does not exist.
        """
        return self._get_monitoring_or_raise(monitoring_id)

    def _compute_metrics(self, monitoring_id: int) -> MonitoringMetrics:
        """Aggregate inspection results into MonitoringMetrics and persist.

        Calculates total_tomatoes, healthy/unhealthy counts, percentages
        by maturity stage, and snapshots_with_detections.

        Args:
            monitoring_id: The monitoring session to compute metrics for.

        Returns:
            The persisted MonitoringMetrics entity.
        """
        results = self._inspection_result_repo.get_by_monitoring(monitoring_id)
        snapshots = self._snapshot_repo.get_by_monitoring(monitoring_id)

        total_tomatoes = len(results)
        healthy_count = sum(1 for r in results if r.health_label == "healthy")
        unhealthy_count = sum(1 for r in results if r.health_label == "unhealthy")

        # Percentages (avoid division by zero).
        if total_tomatoes > 0:
            pct_healthy = (healthy_count / total_tomatoes) * 100.0
            pct_unhealthy = (unhealthy_count / total_tomatoes) * 100.0
        else:
            pct_healthy = 0.0
            pct_unhealthy = 0.0

        # Maturity stage percentages (among all detections with maturity data).
        maturity_stages = {
            "green": 0,
            "breaker": 0,
            "turning": 0,
            "pink": 0,
            "light_red": 0,
            "red": 0,
        }
        for r in results:
            if r.maturity_stage and r.maturity_stage in maturity_stages:
                maturity_stages[r.maturity_stage] += 1

        total_with_maturity = sum(maturity_stages.values())
        if total_with_maturity > 0:
            pct_green = (maturity_stages["green"] / total_with_maturity) * 100.0
            pct_breaker = (maturity_stages["breaker"] / total_with_maturity) * 100.0
            pct_turning = (maturity_stages["turning"] / total_with_maturity) * 100.0
            pct_pink = (maturity_stages["pink"] / total_with_maturity) * 100.0
            pct_light_red = (
                maturity_stages["light_red"] / total_with_maturity
            ) * 100.0
            pct_red = (maturity_stages["red"] / total_with_maturity) * 100.0
        else:
            pct_green = 0.0
            pct_breaker = 0.0
            pct_turning = 0.0
            pct_pink = 0.0
            pct_light_red = 0.0
            pct_red = 0.0

        snapshots_with_detections = sum(1 for s in snapshots if s.has_detections)

        metrics = MonitoringMetrics(
            monitoring_id=monitoring_id,
            total_tomatoes=total_tomatoes,
            healthy_count=healthy_count,
            unhealthy_count=unhealthy_count,
            pct_healthy=pct_healthy,
            pct_unhealthy=pct_unhealthy,
            pct_green=pct_green,
            pct_breaker=pct_breaker,
            pct_turning=pct_turning,
            pct_pink=pct_pink,
            pct_light_red=pct_light_red,
            pct_red=pct_red,
            snapshots_with_detections=snapshots_with_detections,
        )

        return self._metrics_repo.create(monitoring_id, metrics)

    def _get_monitoring_or_raise(self, monitoring_id: int) -> Monitoring:
        """Fetch monitoring by id or raise MonitoringNotFoundError."""
        monitoring = self._monitoring_repo.get_by_id(monitoring_id)
        if monitoring is None:
            raise MonitoringNotFoundError(monitoring_id)
        return monitoring

    def _run_worker(self, monitoring_id: int, worker: MonitoringWorker) -> None:
        """Wrapper that runs the worker and handles post-run transitions.

        Transitions to 'running' before the loop starts, and handles
        error state if the worker exits with an error reason.
        """
        # Transition from initializing → running before the loop begins.
        try:
            self._monitoring_repo.update_status(
                monitoring_id, MonitoringState.RUNNING.value
            )
        except Exception as e:
            logger.error(
                f"Failed to transition monitoring {monitoring_id} to running: {e}"
            )
            return

        # Run the capture loop (blocking in this thread).
        worker.run()

        # After worker exits, check if it stopped due to an error.
        if worker.error_reason:
            try:
                self._monitoring_repo.update_status(
                    monitoring_id, MonitoringState.ERROR.value
                )
            except Exception as e:
                logger.error(
                    f"Failed to transition monitoring {monitoring_id} to error: {e}"
                )
            logger.error(
                f"Monitoring {monitoring_id} ended with error: {worker.error_reason}"
            )

        # Clean up worker reference.
        self._active_workers.pop(monitoring_id, None)
