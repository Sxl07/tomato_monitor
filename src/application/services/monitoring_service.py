"""MonitoringService: orchestrates the monitoring session lifecycle.

Creates, controls, and finalizes monitoring sessions. Spawns a
CaptureWorker in a daemon thread for the fast capture loop and
launches a deferred SnapshotAnalysisService thread for inference.

Enforces:
- One active session per module (non-terminal statuses).
- Valid state machine transitions via MonitoringStatus value object.
- Metrics computation on terminal states (completed, aborted).
- aborted is reserved for explicit operator cancellation; system failures → error.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from sqlalchemy.orm import Session

from src.application.services.log_service import LogService
from src.application.services.capture_worker import CaptureWorker
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

logger = logging.getLogger(__name__)

# Statuses considered "active" (non-terminal) for the one-session-per-module rule.
_ACTIVE_STATUSES = {
    MonitoringState.INITIALIZING.value,
    MonitoringState.RUNNING.value,
    MonitoringState.PAUSED.value,
    MonitoringState.FINISHING.value,
    MonitoringState.ANALYZING.value,
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


class CameraStillBusyError(DomainError):
    """Camera is still held by a previous monitoring worker.

    Raised when attempting to start a new session while a previous
    worker thread is still alive or the global camera lock is held.
    """

    def __init__(self, previous_monitoring_id: Optional[int]) -> None:
        self.previous_monitoring_id = previous_monitoring_id
        super().__init__(
            "El monitoreo anterior todavía está liberando la cámara. "
            "Espera unos segundos e intenta de nuevo."
        )


class FinalizationInProgressError(DomainError):
    """Capture is already being finalized by another request."""

    def __init__(self, monitoring_id: int) -> None:
        self.monitoring_id = monitoring_id
        super().__init__(
            f"La captura del monitoreo {monitoring_id} ya está siendo finalizada. "
            f"Espera a que termine."
        )


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
        runtime_registry=None,
    ) -> None:
        self._monitoring_repo = monitoring_repo
        self._snapshot_repo = snapshot_repo
        self._inspection_result_repo = inspection_result_repo
        self._metrics_repo = metrics_repo
        self._module_repo = module_repo

        # Shared runtime registry — persists across requests.
        # If not provided, fall back to a local instance (for tests/backwards compat).
        if runtime_registry is not None:
            self._registry = runtime_registry
        else:
            from src.application.services.monitoring_runtime_registry import (
                MonitoringRuntimeRegistry,
            )
            self._registry = MonitoringRuntimeRegistry()

    def start_session(
        self,
        module_id: int,
        width_m: float,
        length_m: float,
        notes: Optional[str],
        frame_source: FrameSource,
        db_session: Session,
        log_service: Optional[LogService] = None,
    ) -> Monitoring:
        """Create a monitoring session and spawn the background capture worker.

        Args:
            module_id: The module to monitor (must exist).
            width_m: Module width in meters (confirmed by farmer).
            length_m: Module length in meters (confirmed by farmer).
            notes: Optional farmer notes for this session.
            frame_source: Camera or video frame source.
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

        # Reconcile orphaned sessions before enforcing the one-active rule.
        self._reconcile_orphaned_sessions(module_id)

        # Enforce one active session per module.
        existing = self._monitoring_repo.get_by_module(module_id)
        for m in existing:
            if m.status in _ACTIVE_STATUSES:
                raise ActiveSessionError(module_id, m.id)

        # Check if a previous worker thread is still alive (e.g., abort timed out
        # and session was marked ERROR but thread hasn't died yet).
        self._registry.cleanup_dead()
        if self._registry.has_live_worker_for_module(module_id, self._monitoring_repo):
            raise CameraStillBusyError(None)

        # Also check the global camera lock directly as a safety net.
        try:
            from src.infrastructure.camera.raspberry_camera_frame_source import (
                is_camera_locked,
            )
            if is_camera_locked():
                raise CameraStillBusyError(None)
        except ImportError:
            pass  # Non-RPi environment, no lock to check

        # Create monitoring entity with initializing status.
        monitoring = Monitoring(
            module_id=module_id,
            width_m=width_m,
            length_m=length_m,
            notes=notes,
        )
        monitoring = self._monitoring_repo.create(module_id, monitoring)

        # Commit the monitoring record so the background thread (which creates
        # its own DB session) can see it when it tries to update status.
        try:
            db_session.commit()
        except Exception:
            pass  # If session auto-commits or is already flushed, this is fine

        # Spawn the background worker in a daemon thread.
        from src.infrastructure.config.settings import ACTIVE_PROFILE
        from src.infrastructure.monitoring.thermal_monitor import ThermalMonitor

        worker = CaptureWorker(
            monitoring_id=monitoring.id,
            frame_source=frame_source,
            snapshot_repo=self._snapshot_repo,
            monitoring_repo=self._monitoring_repo,
            db_session=db_session,
            log_service=log_service,
            capture_loop_fps=ACTIVE_PROFILE.capture_loop_fps,
            min_seconds_between_snapshots=ACTIVE_PROFILE.min_seconds_between_snapshots,
            max_seconds_without_snapshot=ACTIVE_PROFILE.max_seconds_without_snapshot,
            gate_resolution=ACTIVE_PROFILE.gate_resolution,
            scene_gate_orb_threshold=ACTIVE_PROFILE.scene_gate_orb_threshold,
            scene_gate_hsv_threshold=ACTIVE_PROFILE.scene_gate_hsv_threshold,
        )

        # Create ThermalMonitor — uses worker.thermal_pause_event so that
        # thermal pauses don't interfere with manual pause_event.
        thermal_monitor = ThermalMonitor(
            pause_event=worker.thermal_pause_event,
            poll_interval_seconds=ACTIVE_PROFILE.thermal_poll_interval_seconds,
            warning_temp=ACTIVE_PROFILE.thermal_warning_temp,
            critical_temp=ACTIVE_PROFILE.thermal_critical_temp,
            resume_temp=ACTIVE_PROFILE.thermal_resume_temp,
        )
        worker._thermal_monitor = thermal_monitor

        thread = threading.Thread(
            target=self._run_worker,
            args=(monitoring.id, worker),
            daemon=True,
            name=f"capture-worker-{monitoring.id}",
        )
        self._registry.register(monitoring.id, worker, thread)
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
        worker = self._registry.get_worker(monitoring_id)
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
        worker = self._registry.get_worker(monitoring_id)
        if worker is not None:
            worker.pause_event.clear()

        # Persist the status change.
        return self._monitoring_repo.update_status(
            monitoring_id, MonitoringState.RUNNING.value
        )

    def abort_session(self, monitoring_id: int) -> Monitoring:
        """Abort the session, wait for the worker to stop and release camera.

        IMPORTANT: This method waits for the background thread to fully
        terminate (camera released) before returning. If the thread does NOT
        terminate within the timeout, the session is marked as 'error' instead
        of 'aborted', and references are NOT cleaned — preventing a new
        monitoring from starting while the camera may still be held.

        Args:
            monitoring_id: The monitoring session to abort.

        Returns:
            Updated Monitoring entity with status 'aborted' or 'error'.

        Raises:
            MonitoringNotFoundError: If monitoring_id does not exist.
            InvalidTransitionError: If current status does not allow aborting.
        """
        monitoring = self._get_monitoring_or_raise(monitoring_id)

        # Validate state transition.
        status = MonitoringStatus(MonitoringState(monitoring.status))
        status.transition_to(MonitoringState.ABORTED)

        # Signal the worker to stop.
        worker = self._registry.get_worker(monitoring_id)
        thread = self._registry.get_thread(monitoring_id)
        if worker is not None:
            logger.info(f"Signaling abort for monitoring {monitoring_id}...")
            worker.abort_event.set()
            worker.pause_event.clear()

        # Wait for the worker thread to finish (camera release happens in finally).
        thread_terminated = True
        if thread is not None and thread.is_alive():
            logger.info(
                f"Waiting for worker thread {monitoring_id} to terminate "
                f"(camera release)..."
            )
            thread.join(timeout=15.0)
            if thread.is_alive():
                thread_terminated = False
                logger.error(
                    f"Worker thread for monitoring {monitoring_id} did not stop "
                    f"within 15s. Camera may still be held. Marking as ERROR."
                )

        if thread_terminated:
            # Worker terminated (or was never registered) — check camera lock.
            if worker is None and thread is None:
                # No worker in registry — check if camera is still locked
                # (could be held by an orphaned thread we don't know about).
                try:
                    from src.infrastructure.camera.raspberry_camera_frame_source import (
                        is_camera_locked,
                    )
                    if is_camera_locked():
                        logger.warning(
                            f"Abort for monitoring {monitoring_id}: no worker in registry "
                            f"but camera lock is held. Marking as error."
                        )
                        monitoring = self._monitoring_repo.update_status(
                            monitoring_id, MonitoringState.ERROR.value
                        )
                        return monitoring
                except ImportError:
                    pass

            logger.info(f"Worker thread {monitoring_id} terminated. Camera released.")
            monitoring = self._monitoring_repo.update_status(
                monitoring_id, MonitoringState.ABORTED.value
            )
            self._compute_metrics(monitoring_id)
            self._registry.remove(monitoring_id)
        else:
            # Worker did NOT terminate — DO NOT clean references.
            # Mark session as error to prevent new monitorings from starting
            # (orphan reconciliation will eventually clean this up).
            monitoring = self._monitoring_repo.update_status(
                monitoring_id, MonitoringState.ERROR.value
            )
            # Don't remove from registry — the thread is still alive
            # and may still be holding the camera lock.

        return monitoring

    def finalize_capture(self, monitoring_id: int) -> Monitoring:
        """Signal end of capture, release camera, start deferred analysis.

        Only operates from status='running'. Never marks aborted.
        System failures → error.

        Returns:
            Monitoring in status 'analyzing' (has snapshots) or 'completed' (zero snapshots).

        Raises:
            MonitoringNotFoundError, InvalidTransitionError, FinalizationInProgressError.
        """
        monitoring = self._get_monitoring_or_raise(monitoring_id)

        # Validate transition (don't persist yet)
        status = MonitoringStatus(MonitoringState(monitoring.status))
        status.transition_to(MonitoringState.ANALYZING)

        # Claim exclusive finalization
        if not self._registry.claim_finalization(monitoring_id):
            raise FinalizationInProgressError(monitoring_id)

        try:
            return self._finalize_capture_inner(monitoring_id)
        except Exception as e:
            # After claim acquired, no exception should propagate to caller.
            logger.error(
                f"Unexpected error in finalize_capture for {monitoring_id}: {e}"
            )
            self._rollback_request_session_best_effort()
            self._mark_monitoring_error_best_effort(monitoring_id)
            self._registry.remove(monitoring_id)
            self._registry.release_finalization(monitoring_id)
            monitoring = self._monitoring_repo.get_by_id(monitoring_id)
            return monitoring

    def _mark_monitoring_error_best_effort(self, monitoring_id: int) -> None:
        """Best-effort transition to error. Never raises."""
        try:
            self._monitoring_repo.update_status(
                monitoring_id, MonitoringState.ERROR.value
            )
        except Exception:
            pass

    def _rollback_request_session_best_effort(self) -> None:
        """Best-effort rollback of request-scoped sessions. Never raises."""
        seen: set[int] = set()
        for repo in (
            self._monitoring_repo,
            self._snapshot_repo,
            self._inspection_result_repo,
            self._metrics_repo,
        ):
            session = getattr(repo, "_session", None)
            if session is not None and id(session) not in seen:
                seen.add(id(session))
                try:
                    session.rollback()
                except Exception:
                    pass

    def _finalize_capture_inner(self, monitoring_id: int) -> Monitoring:
        """Inner finalization logic. Caller handles claim release on exception."""
        import dataclasses

        worker = self._registry.get_worker(monitoring_id)
        thread = self._registry.get_thread(monitoring_id)

        # No worker/thread → error
        if worker is None or thread is None:
            monitoring = self._monitoring_repo.update_status(
                monitoring_id, MonitoringState.ERROR.value
            )
            self._registry.release_finalization(monitoring_id)
            return monitoring

        # Signal worker to stop (NOT abort)
        worker.finalize_event.set()
        worker.pause_event.clear()
        if hasattr(worker, "thermal_pause_event"):
            worker.thermal_pause_event.clear()

        # Wait for capture thread
        thread.join(timeout=10.0)

        if thread.is_alive():
            monitoring = self._monitoring_repo.update_status(
                monitoring_id, MonitoringState.ERROR.value
            )
            self._registry.release_finalization(monitoring_id)
            return monitoring

        # Check worker error
        if worker.error_reason:
            monitoring = self._monitoring_repo.update_status(
                monitoring_id, MonitoringState.ERROR.value
            )
            self._registry.remove(monitoring_id)
            self._registry.release_finalization(monitoring_id)
            return monitoring

        # Check camera lock
        try:
            from src.infrastructure.camera.raspberry_camera_frame_source import is_camera_locked
            if is_camera_locked():
                monitoring = self._monitoring_repo.update_status(
                    monitoring_id, MonitoringState.ERROR.value
                )
                self._registry.remove(monitoring_id)
                self._registry.release_finalization(monitoring_id)
                return monitoring
        except ImportError:
            pass

        # Get confirmed snapshots (protected — failure here must not propagate)
        snapshots = self._snapshot_repo.get_by_monitoring(monitoring_id)

        # Write capture metrics (recoverable — errors do not block flow)
        try:
            from src.infrastructure.persistence.local.snapshot_analysis_report_writer import (
                SnapshotAnalysisReportWriter,
            )
            from src.infrastructure.config.settings import ACTIVE_PROFILE

            capture_data = dataclasses.asdict(worker.capture_metrics)
            capture_data["total_snapshots"] = len(snapshots)

            writer = SnapshotAnalysisReportWriter()
            write_result = writer.write_capture_metrics(
                monitoring_id=monitoring_id,
                capture_metrics=capture_data,
                profile_name=ACTIVE_PROFILE.name,
            )
            for error in write_result.errors:
                logger.warning(
                    f"Capture metrics write error for {monitoring_id}: {error}"
                )
        except Exception as e:
            logger.warning(f"Failed to write capture metrics for {monitoring_id}: {e}")

        # Zero snapshots → completed directly
        if len(snapshots) == 0:
            try:
                self._monitoring_repo.update_counters(
                    monitoring_id, total_snapshots=0, total_detections=0
                )
                empty_metrics = self._build_empty_metrics(monitoring_id)
                self._metrics_repo.create_pending_for_finalization(
                    monitoring_id, empty_metrics
                )
                self._monitoring_repo.update_status(
                    monitoring_id, MonitoringState.COMPLETED.value
                )
            except Exception as e:
                logger.error(
                    f"Failed to complete zero-snapshot monitoring {monitoring_id}: {e}"
                )
                self._rollback_request_session_best_effort()
                self._mark_monitoring_error_best_effort(monitoring_id)
                self._registry.remove(monitoring_id)
                self._registry.release_finalization(monitoring_id)
                monitoring = self._monitoring_repo.get_by_id(monitoring_id)
                return monitoring

            self._registry.remove(monitoring_id)
            self._registry.release_finalization(monitoring_id)
            monitoring = self._monitoring_repo.get_by_id(monitoring_id)
            return monitoring

        # Has snapshots → analyzing + start analysis thread
        monitoring = self._monitoring_repo.update_status(
            monitoring_id, MonitoringState.ANALYZING.value
        )

        analysis_thread = threading.Thread(
            target=self._run_analysis,
            args=(monitoring_id,),
            daemon=True,
            name=f"analysis-worker-{monitoring_id}",
        )
        self._registry.register(monitoring_id, None, analysis_thread)
        analysis_thread.start()

        self._registry.release_finalization(monitoring_id)
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
        worker = self._registry.get_worker(monitoring_id)
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

        # Clean up worker and thread references.
        self._registry.remove(monitoring_id)

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

    def _build_metrics_from_analysis_result(self, monitoring_id: int, result) -> MonitoringMetrics:
        """Build MonitoringMetrics from an AnalysisResult."""
        total = result.unique_tomatoes
        pct_healthy = (result.healthy_count / total * 100.0) if total > 0 else 0.0
        pct_unhealthy = (result.unhealthy_count / total * 100.0) if total > 0 else 0.0

        mc = result.maturity_counts
        total_maturity = sum(mc.values())
        if total_maturity > 0:
            pct_green = mc.get("green", 0) / total_maturity * 100.0
            pct_breaker = mc.get("breaker", 0) / total_maturity * 100.0
            pct_turning = mc.get("turning", 0) / total_maturity * 100.0
            pct_pink = mc.get("pink", 0) / total_maturity * 100.0
            pct_light_red = mc.get("light_red", 0) / total_maturity * 100.0
            pct_red = mc.get("red", 0) / total_maturity * 100.0
        else:
            pct_green = pct_breaker = pct_turning = pct_pink = pct_light_red = pct_red = 0.0

        return MonitoringMetrics(
            monitoring_id=monitoring_id,
            total_tomatoes=total,
            healthy_count=result.healthy_count,
            unhealthy_count=result.unhealthy_count,
            pct_healthy=pct_healthy,
            pct_unhealthy=pct_unhealthy,
            pct_green=pct_green,
            pct_breaker=pct_breaker,
            pct_turning=pct_turning,
            pct_pink=pct_pink,
            pct_light_red=pct_light_red,
            pct_red=pct_red,
            snapshots_with_detections=result.snapshots_with_detections,
        )

    def _build_empty_metrics(self, monitoring_id: int) -> MonitoringMetrics:
        """Build empty MonitoringMetrics for zero-snapshot completion."""
        return MonitoringMetrics(
            monitoring_id=monitoring_id,
            total_tomatoes=0,
            healthy_count=0,
            unhealthy_count=0,
            pct_healthy=0.0,
            pct_unhealthy=0.0,
            pct_green=0.0,
            pct_breaker=0.0,
            pct_turning=0.0,
            pct_pink=0.0,
            pct_light_red=0.0,
            pct_red=0.0,
            snapshots_with_detections=0,
        )

    def _reconcile_orphaned_sessions(self, module_id: int) -> None:
        """Detect and mark orphaned sessions for a module.

        A session is "orphaned" if it has an active status in the database
        but no corresponding live runtime thread (capture worker or analysis
        service) in memory. This can happen when the server restarts, the
        worker crashes silently, or the daemon thread dies without updating
        the DB.

        Covers all active statuses including 'analyzing' — an analysis thread
        that has died without transitioning to completed/error is treated as
        orphaned. Snapshots and partial metrics are preserved.

        Orphaned sessions are transitioned to 'error' status so they no
        longer block new monitorings for the module.
        """
        existing = self._monitoring_repo.get_by_module(module_id)
        for m in existing:
            if m.status not in _ACTIVE_STATUSES:
                continue

            # Check if we have a live worker for this session in the shared registry.
            thread = self._registry.get_thread(m.id)

            worker_alive = thread is not None and thread.is_alive()

            if not worker_alive:
                # This session is orphaned — no live worker backing it.
                logger.warning(
                    f"Orphaned session detected: monitoring_id={m.id}, "
                    f"status={m.status}, module_id={module_id}. "
                    f"Transitioning to 'error'."
                )
                try:
                    self._monitoring_repo.update_status(
                        m.id, MonitoringState.ERROR.value
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to mark orphaned session {m.id} as error: {e}"
                    )
                # Clean up stale references from registry.
                self._registry.remove(m.id)

    def _get_monitoring_or_raise(self, monitoring_id: int) -> Monitoring:
        """Fetch monitoring by id or raise MonitoringNotFoundError."""
        monitoring = self._monitoring_repo.get_by_id(monitoring_id)
        if monitoring is None:
            raise MonitoringNotFoundError(monitoring_id)
        return monitoring

    def _run_worker(self, monitoring_id: int, worker: CaptureWorker) -> None:
        """Wrapper that runs the capture worker and handles post-run transitions.

        IMPORTANT: Creates a FRESH database session for this background thread.
        The request-scoped session from the HTTP handler is NOT valid here
        (it gets closed after the response is sent, causing
        "identity map is no longer valid" errors).
        """
        from src.infrastructure.persistence.database import DatabaseManager
        from src.infrastructure.persistence.repositories import (
            SqlMonitoringRepository,
            SqlSnapshotRepository,
        )

        # Create a fresh DB session for this background thread.
        try:
            db_manager = DatabaseManager()
            thread_session = db_manager.get_session()
        except Exception as e:
            logger.error(
                f"Failed to create thread-local DB session for monitoring {monitoring_id}: {e}"
            )
            worker.release_resources()
            self._registry.remove_runtime(monitoring_id)
            return

        # Rebuild repositories with the thread-local session.
        thread_monitoring_repo = SqlMonitoringRepository(session=thread_session)
        thread_snapshot_repo = SqlSnapshotRepository(session=thread_session)

        # Patch the worker to use thread-local repositories and session.
        worker._monitoring_repo = thread_monitoring_repo
        worker._snapshot_repo = thread_snapshot_repo
        worker._db_session = thread_session

        # Transition from initializing → running before the loop begins.
        try:
            thread_monitoring_repo.update_status(
                monitoring_id, MonitoringState.RUNNING.value
            )
            thread_session.commit()
        except Exception as e:
            logger.error(
                f"Failed to transition monitoring {monitoring_id} to running: {e}"
            )
            thread_session.close()
            worker.release_resources()
            self._registry.remove_runtime(monitoring_id)
            return

        # Run the capture loop (blocking in this thread).
        worker.run()

        # After worker exits, check if it stopped due to an error.
        if worker.error_reason:
            try:
                thread_monitoring_repo.update_status(
                    monitoring_id, MonitoringState.ERROR.value
                )
                thread_session.commit()
            except Exception as e:
                logger.error(
                    f"Failed to transition monitoring {monitoring_id} to error: {e}"
                )
            logger.error(
                f"Monitoring {monitoring_id} ended with error: {worker.error_reason}"
            )

        # Close the thread-local session.
        try:
            thread_session.close()
        except Exception:
            pass

        # Clean up from the shared registry (preserve finalization claims).
        self._registry.remove_runtime(monitoring_id)

    def _run_analysis(self, monitoring_id: int) -> None:
        """Run SnapshotAnalysisService in a background thread.

        Creates fresh DB session, runs analysis, persists metrics, transitions state.
        All operations are wrapped in try/except/finally — no exception escapes.
        """
        thread_session = None
        try:
            from src.infrastructure.persistence.database import DatabaseManager
            db_manager = DatabaseManager()
            thread_session = db_manager.get_session()
        except Exception as e:
            logger.error(
                f"Failed to create DB session for analysis {monitoring_id}: {e}"
            )
            self._registry.remove(monitoring_id)
            return

        try:
            from src.infrastructure.persistence.repositories import (
                SqlMonitoringRepository, SqlSnapshotRepository,
                SqlInspectionResultRepository, SqlMonitoringMetricsRepository,
            )
            from src.infrastructure.config.settings import ACTIVE_PROFILE
            from src.infrastructure.monitoring.thermal_monitor import ThermalMonitor
            from src.application.services.snapshot_analysis_service import SnapshotAnalysisService

            thread_monitoring_repo = SqlMonitoringRepository(session=thread_session)
            thread_snapshot_repo = SqlSnapshotRepository(session=thread_session)
            thread_inspection_repo = SqlInspectionResultRepository(session=thread_session)
            thread_metrics_repo = SqlMonitoringMetricsRepository(session=thread_session)

            # Thermal monitor for analysis — uses analysis-specific thresholds
            thermal_pause_event = threading.Event()
            thermal_monitor = ThermalMonitor(
                pause_event=thermal_pause_event,
                poll_interval_seconds=ACTIVE_PROFILE.thermal_poll_interval_seconds,
                warning_temp=ACTIVE_PROFILE.analysis_thermal_pause_threshold,
                critical_temp=ACTIVE_PROFILE.analysis_thermal_pause_threshold,
                resume_temp=ACTIVE_PROFILE.analysis_thermal_resume_threshold,
            )

            analysis_service = SnapshotAnalysisService(
                monitoring_id=monitoring_id,
                snapshot_repo=thread_snapshot_repo,
                inspection_result_repo=thread_inspection_repo,
                db_session=thread_session,
                thermal_monitor=thermal_monitor,
                analysis_skip_maturity=ACTIVE_PROFILE.analysis_skip_maturity,
                profile_name=ACTIVE_PROFILE.name,
            )

            self._registry.set_worker(monitoring_id, analysis_service)

            result = analysis_service.run()

            # Determine success: progress == "completed" and no fatal error_reason
            if (
                analysis_service.progress.status == "completed"
                and analysis_service.error_reason is None
            ):
                # Success — persist metrics and transition
                thread_monitoring_repo.update_counters(
                    monitoring_id,
                    total_snapshots=result.total_snapshots,
                    total_detections=result.unique_tomatoes,
                )
                metrics = self._build_metrics_from_analysis_result(
                    monitoring_id, result
                )
                thread_metrics_repo.create_pending_for_finalization(
                    monitoring_id, metrics
                )
                thread_monitoring_repo.update_status(
                    monitoring_id, MonitoringState.COMPLETED.value
                )
            else:
                # Analysis reported error
                try:
                    thread_session.rollback()
                except Exception:
                    pass
                try:
                    thread_monitoring_repo.update_status(
                        monitoring_id, MonitoringState.ERROR.value
                    )
                    thread_session.commit()
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"Analysis {monitoring_id} crashed: {e}")
            try:
                if thread_session:
                    thread_session.rollback()
            except Exception:
                pass
            try:
                from src.infrastructure.persistence.repositories import (
                    SqlMonitoringRepository,
                )
                fallback_repo = SqlMonitoringRepository(session=thread_session)
                fallback_repo.update_status(
                    monitoring_id, MonitoringState.ERROR.value
                )
                thread_session.commit()
            except Exception:
                pass
        finally:
            try:
                if thread_session:
                    thread_session.close()
            except Exception:
                pass
            self._registry.remove(monitoring_id)
