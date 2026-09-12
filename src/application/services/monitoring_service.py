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


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (Spec 020 traceability).

    Used for durable capture_completed_at / deferred_analysis_started_at marks in
    the per-monitoring metrics file. Kept module-level and dependency-free.
    """
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()

# Statuses considered "active" (non-terminal) for the one-session-per-module rule.
_ACTIVE_STATUSES = {
    MonitoringState.INITIALIZING.value,
    MonitoringState.RUNNING.value,
    MonitoringState.PAUSED.value,
    MonitoringState.FINISHING.value,
    # Spec 020: ready_for_analysis is an active, non-terminal state (video-first
    # capture finished, video validated, camera released, analysis not started).
    MonitoringState.READY_FOR_ANALYSIS.value,
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


class DiskSpaceLowError(DomainError):
    """Insufficient free disk space to start a video-first recording."""

    def __init__(self, free_mb: int) -> None:
        self.free_mb = free_mb
        super().__init__(f"Espacio en disco bajo ({free_mb} MB).")


class DeviceBusyError(DomainError):
    """A hardware/compute phase (capture or analysis) is active on the device.

    Spec 020 device-global guard: only ONE active capture/analysis phase may run
    on the single Raspberry Pi at a time. Sessions merely waiting in
    ready_for_analysis (no worker/thread) do NOT trigger this.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)


class PowerSourceNotConfirmedError(DomainError):
    """Deferred analysis rejected: the operator did not confirm the power source."""

    def __init__(self) -> None:
        super().__init__(
            "Se requiere confirmar la fuente de energía para continuar."
        )


class AnalysisPreflightFailedError(DomainError):
    """Deferred analysis rejected by a preflight check (actionable Spanish message)."""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


class AnalysisAlreadyRunningError(DomainError):
    """Deferred analysis rejected: an analysis is already in progress."""

    def __init__(self, monitoring_id: int) -> None:
        self.monitoring_id = monitoring_id
        super().__init__("El análisis de este monitoreo ya está en curso.")


class NotReadyForAnalysisError(DomainError):
    """Deferred analysis rejected: monitoring is not in ready_for_analysis."""

    def __init__(self, monitoring_id: int, status: str) -> None:
        self.monitoring_id = monitoring_id
        self.status = status
        super().__init__(
            "El monitoreo no está listo para análisis."
        )


class ReprocessPreconditionError(DomainError):
    """Reprocess rejected because a precondition is not met."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)


class ReprocessBlockedBySyncError(DomainError):
    """Reprocess rejected because synced data would be orphaned remotely."""

    def __init__(self) -> None:
        super().__init__(
            "No se puede reprocesar: este monitoreo ya fue sincronizado. "
            "Reprocesar borraría datos que existen en el servidor y quedarían huérfanos."
        )


class ReprocessInProgressError(DomainError):
    """A reprocess is already running for this monitoring."""

    def __init__(self, monitoring_id: int) -> None:
        self.monitoring_id = monitoring_id
        super().__init__(
            f"El reprocesamiento del monitoreo {monitoring_id} ya está en curso."
        )


class ReprocessDeviceBusyError(DomainError):
    """Reprocess rejected: a capture/analysis phase is active on the device.

    Spec 020 device-global guard for reprocess. Raised BEFORE any destructive
    clear/reset, so no results are cleared and no state is changed when busy.
    """

    def __init__(self, monitoring_id: int) -> None:
        self.monitoring_id = monitoring_id
        super().__init__(
            "El dispositivo está ocupado (captura o análisis en curso). "
            "Espera a que termine para reprocesar."
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
        width_m: "float | None",
        length_m: "float | None",
        notes: Optional[str],
        frame_source: FrameSource,
        db_session: Session,
        log_service: Optional[LogService] = None,
    ) -> Monitoring:
        """Create a monitoring session and spawn the background capture worker.

        Args:
            module_id: The module to monitor (must exist).
            width_m: Module width in meters (confirmed by farmer), or None if not provided.
            length_m: Module length in meters, or None if not provided.
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

        # Spec 020 device-global guard: a heavy deferred analysis and a capture
        # must not run at the same time on the single Raspberry Pi. Reserve the
        # capture phase ATOMICALLY via the single coordinator (registry). The
        # reservation contends with claim_global_analysis in one atomic decision,
        # so a simultaneous capture-start and analysis-start can never both win.
        # This is the ONLY new constraint on the capture-first flow; an
        # already-admitted session is otherwise unchanged (Requirements 15.1, 21.3).
        # A bare MagicMock registry (legacy unit tests) returns a truthy Mock, so
        # we only reject on an explicit ``is False`` result from the real registry.
        if self._registry.reserve_capture(module_id) is False:
            raise DeviceBusyError(
                "Hay un análisis en curso en el dispositivo. "
                "Espera a que termine para iniciar una nueva captura."
            )

        try:
            # Video-first: block start if disk space is insufficient (before
            # creating the session), with an actionable message.
            from src.infrastructure.config.settings import ACTIVE_PROFILE
            if getattr(ACTIVE_PROFILE, "video_first_enabled", False):
                self._check_disk_space_or_raise()

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

            # The PROVISIONAL module_id reservation is held for the ENTIRE start
            # (no release→reserve gap). The worker start below registers the
            # monitoring, marks its capture active (keyed by monitoring id) and
            # starts the thread. Because the provisional reservation is never
            # released until AFTER the worker is running, there is no window in
            # which claim_global_analysis could win — has_active_capture stays
            # True continuously (provisional reservation → then live active mark).
            if getattr(ACTIVE_PROFILE, "video_first_enabled", False):
                self._start_video_first(
                    monitoring, frame_source, db_session, log_service
                )
            else:
                self._start_capture_first(
                    monitoring, frame_source, db_session, log_service
                )

            # Worker started OK and mark_capture_active(monitoring.id) is now in
            # effect. Release the provisional module_id reservation; the device
            # remains covered by the worker's own active-capture mark.
            self._registry.release_capture_reservation(module_id)
        except Exception:
            # Any failure before/at worker launch: release the provisional
            # reservation AND (defensively) any per-monitoring capture phase so
            # the device-global capture slot is freed.
            try:
                self._registry.release_capture_reservation(module_id)
            except Exception:
                pass
            try:
                mid = getattr(locals().get("monitoring", None), "id", None)
                if mid is not None:
                    self._registry.clear_capture_active(mid)
            except Exception:
                pass
            raise

        return monitoring

    def _start_capture_first(self, monitoring, frame_source, db_session, log_service):
        """Spawn the capture-first CaptureWorker (preserved, unchanged)."""
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
        # Spec 020: mark this as an ACTIVE capture for device-global coordination
        # (has_active_capture()); cleared when the worker thread finishes.
        self._registry.mark_capture_active(monitoring.id)
        thread.start()

    def _start_video_first(self, monitoring, frame_source, db_session, log_service):
        """Spawn the video-first VideoRecordingWorker in a daemon thread.

        Single camera owner: the worker owns the frame source; analysis starts
        only after finalize releases the camera. The recorder writes to the temp
        path monitoring.recording.mp4; atomic promotion happens in finalize.
        """
        from src.infrastructure.config.settings import ACTIVE_PROFILE, BASE_DIR
        from src.application.services.video_recording_worker import VideoRecordingWorker
        from src.application.services.recording_sampler import RecordingSampler
        from src.infrastructure.camera.video_recorder import VideoRecorder
        from src.infrastructure.monitoring.thermal_monitor import ThermalMonitor

        # The recorder writes under BASE_DIR at the RELATIVE temp path; passing
        # allowed_base=BASE_DIR makes VideoRecorder sanitize the path (reject
        # traversal/absolute) BEFORE opening the writer and resolve it to the
        # correct absolute location. The DB video_path remains RELATIVE.
        temp_rel = self._recording_temp_path(monitoring.id)

        recorder = VideoRecorder(
            output_path=temp_rel,
            fps=float(ACTIVE_PROFILE.recording_target_fps),
            codec_candidates=tuple(ACTIVE_PROFILE.video_codec_candidates),
            allowed_base=BASE_DIR,
        )

        # Spec 023: the RecordingSampler enforces the recording cadence
        # (recording_target_fps) by temporal selection, while the camera/preview
        # run at camera_stream_fps. The VideoRecorder container fps stays
        # recording_target_fps (NOT camera_stream_fps).
        recording_sampler = RecordingSampler(
            recording_fps=float(ACTIVE_PROFILE.recording_target_fps)
        )

        worker = VideoRecordingWorker(
            monitoring_id=monitoring.id,
            frame_source=frame_source,
            video_recorder=recorder,
            monitoring_repo=self._monitoring_repo,
            db_session=db_session,
            configured_recording_fps=float(ACTIVE_PROFILE.recording_target_fps),
            configured_camera_stream_fps=float(ACTIVE_PROFILE.camera_stream_fps),
            recording_sampler=recording_sampler,
            log_service=log_service,
        )

        thermal_monitor = ThermalMonitor(
            pause_event=worker.thermal_pause_event,
            poll_interval_seconds=ACTIVE_PROFILE.thermal_poll_interval_seconds,
            warning_temp=ACTIVE_PROFILE.thermal_warning_temp,
            critical_temp=ACTIVE_PROFILE.thermal_critical_temp,
            resume_temp=ACTIVE_PROFILE.thermal_resume_temp,
        )
        worker._thermal_monitor = thermal_monitor

        thread = threading.Thread(
            target=self._run_video_recording_worker,
            args=(monitoring.id, worker),
            daemon=True,
            name=f"video-recording-worker-{monitoring.id}",
        )
        self._registry.register(monitoring.id, worker, thread)
        # Spec 020: mark this as an ACTIVE capture for device-global coordination
        # (has_active_capture()); cleared when the recording thread finishes.
        self._registry.mark_capture_active(monitoring.id)
        thread.start()

    #: Minimum free disk space (MB) required to start a video-first recording.
    MIN_FREE_DISK_MB = 200

    def _check_disk_space_or_raise(self) -> None:
        """Raise DiskSpaceLowError if free space under OUTPUTS_DIR is too low.

        Best-effort: if disk usage cannot be measured, do not block the start.
        """
        import shutil
        from src.infrastructure.config.settings import OUTPUTS_DIR

        try:
            OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
            free_bytes = shutil.disk_usage(str(OUTPUTS_DIR)).free
        except Exception:
            return  # Cannot measure — do not block.
        free_mb = int(free_bytes / (1024 * 1024))
        if free_mb < self.MIN_FREE_DISK_MB:
            raise DiskSpaceLowError(free_mb)

    @staticmethod
    def _recording_temp_path(monitoring_id: int) -> str:
        """Relative temp recording path (from project root)."""
        return f"outputs/monitorings/{monitoring_id}/video/monitoring.recording.mp4"

    @staticmethod
    def _recording_final_path(monitoring_id: int) -> str:
        """Relative final validated video path (from project root)."""
        return f"outputs/monitorings/{monitoring_id}/video/monitoring.mp4"

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

        # Spec 020 idempotency: finalizing a monitoring that already reached
        # ready_for_analysis is a safe no-op (video already promoted, camera
        # released, no analysis launched). Return the current entity unchanged.
        if monitoring.status == MonitoringState.READY_FOR_ANALYSIS.value:
            return monitoring

        # Validate transition (don't persist yet). ANALYZING is a valid target
        # from running for both flows; the video-first branch will instead
        # transition to ready_for_analysis inside _finalize_video_first.
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

    def start_deferred_analysis(
        self,
        monitoring_id: int,
        power_source_confirmed: bool,
        db_session: Session,
    ) -> Monitoring:
        """Manually start the deferred analysis of a ready_for_analysis monitoring.

        Spec 020 flow:
          1. Monitoring must exist and be in ``ready_for_analysis`` (else reject,
             state unchanged).
          2. ``power_source_confirmed`` must be truthy (per-attempt confirmation;
             absent/false is handled here, NOT via a framework 422).
          3. Run AnalysisPreflight (video present/safe/readable, no concurrent
             analysis, no module conflict, no device capture/analysis active,
             temperature ok, no current undervoltage).
          4. Acquire the device-global analysis slot AND the per-monitoring
             analysis claim atomically. If either is taken -> already running.
          5. ONLY after the claims are held: write durable traceability
             (manual_deferred_analysis=true, deferred_analysis_started_at,
             preflight temperature), transition ready_for_analysis -> analyzing,
             register + launch the analysis thread (reusing _run_video_analysis
             unchanged). If the thread fails to launch -> release both claims and
             roll back to ready_for_analysis (NO false start metadata persisted).

        Raises:
            MonitoringNotFoundError, NotReadyForAnalysisError,
            PowerSourceNotConfirmedError, AnalysisPreflightFailedError,
            AnalysisAlreadyRunningError, DeviceBusyError.
        """
        from src.infrastructure.config.settings import ACTIVE_PROFILE, BASE_DIR
        from src.application.services.analysis_preflight import AnalysisPreflight

        monitoring = self._get_monitoring_or_raise(monitoring_id)

        # 1) state gate (reject without modifying anything)
        if monitoring.status != MonitoringState.READY_FOR_ANALYSIS.value:
            raise NotReadyForAnalysisError(monitoring_id, monitoring.status)

        # 2) per-attempt power confirmation (application-level; never a 422)
        if not power_source_confirmed:
            raise PowerSourceNotConfirmedError()

        # 3) preflight (reuses ACTIVE_PROFILE analysis pause threshold; no new value)
        preflight = AnalysisPreflight(
            base_dir=BASE_DIR,
            monitoring_repo=self._monitoring_repo,
            runtime_registry=self._registry,
            thermal_pause_threshold_c=ACTIVE_PROFILE.analysis_thermal_pause_threshold,
        )
        result = preflight.run(monitoring)
        if not result.ok:
            raise AnalysisPreflightFailedError(result.reason_code, str(result.message))

        # 4) atomic exclusion: device-global slot + per-monitoring analysis claim.
        if not self._registry.claim_global_analysis(monitoring_id):
            raise AnalysisAlreadyRunningError(monitoring_id)
        if not self._registry.claim_analysis(monitoring_id):
            # Another start for THIS monitoring already holds the per-monitoring
            # claim; release the global slot we just took and reject.
            self._registry.release_global_analysis(monitoring_id)
            raise AnalysisAlreadyRunningError(monitoring_id)

        try:
            # 5) ready_for_analysis -> analyzing
            self._monitoring_repo.update_status(
                monitoring_id, MonitoringState.ANALYZING.value
            )
            try:
                db_session.commit()
            except Exception:
                pass

            # Register + launch the analysis thread (reuse _run_video_analysis).
            analysis_thread = threading.Thread(
                target=self._run_video_analysis,
                args=(monitoring_id, monitoring.video_path),
                daemon=True,
                name=f"video-analysis-worker-{monitoring_id}",
            )
            self._registry.register(monitoring_id, None, analysis_thread)
            analysis_thread.start()

            # Durable traceability written ONLY after the analysis thread has
            # actually started (claims held). Writing it here — never before a
            # possible launch failure — guarantees a rolled-back attempt leaves NO
            # metadata falsely asserting the analysis began.
            self._write_deferred_analysis_metadata(
                monitoring_id, preflight_temperature_c=result.temperature_c
            )
        except Exception as e:
            # Launch failed BEFORE analysis started -> release claims and roll
            # back to ready_for_analysis. Do NOT leave false start metadata's
            # effect on state (video preserved; monitoring stays reprocessable).
            logger.error(
                f"Failed to launch deferred analysis for {monitoring_id}: {e}"
            )
            self._registry.remove_runtime(monitoring_id)
            self._registry.release_analysis(monitoring_id)
            self._registry.release_global_analysis(monitoring_id)
            self._rollback_request_session_best_effort()
            try:
                self._monitoring_repo.update_status(
                    monitoring_id, MonitoringState.READY_FOR_ANALYSIS.value
                )
                db_session.commit()
            except Exception:
                pass
            monitoring = self._monitoring_repo.get_by_id(monitoring_id)
            return monitoring

        return self._monitoring_repo.get_by_id(monitoring_id)

    def _write_deferred_analysis_metadata(
        self, monitoring_id: int, preflight_temperature_c: Optional[float]
    ) -> None:
        """Persist durable manual-deferred-analysis traceability (Spec 020).

        Writes a non-destructive 'deferred_analysis' section into the per-monitoring
        pipeline_metrics.json (merging, never truncating existing sections). Called
        ONLY after the analysis claims are held (never at finalize, never on a
        failed launch). Best-effort: errors are logged, not raised.
        """
        try:
            from src.infrastructure.persistence.local.snapshot_analysis_report_writer import (
                SnapshotAnalysisReportWriter,
            )
            from src.infrastructure.config.settings import ACTIVE_PROFILE, OUTPUTS_DIR

            writer = SnapshotAnalysisReportWriter(
                base_outputs_dir=OUTPUTS_DIR / "monitorings"
            )
            section = {
                "manual_deferred_analysis": True,
                "deferred_analysis_started_at": _utcnow_iso(),
                "preflight_temperature_c": preflight_temperature_c,
            }
            writer.write_deferred_analysis_metadata(
                monitoring_id=monitoring_id,
                deferred_metadata=section,
                profile_name=ACTIVE_PROFILE.name,
            )
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(
                f"Failed to write deferred-analysis metadata for {monitoring_id}: {e}"
            )

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

        # Video-first path: validate + atomically promote the recorded video,
        # persist video_path, then launch deferred analysis on the final video.
        if self._is_video_recording_worker(worker):
            return self._finalize_video_first(monitoring_id, worker)

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

    #: Operator-facing message when the camera is unavailable at start (tasks.md 10.1).
    CAMERA_UNAVAILABLE_MESSAGE = "La cámara no está disponible. Verifica la conexión."

    def _emit_camera_unavailable_log(self, worker) -> None:
        """Emit the actionable camera-unavailable message via the worker log service."""
        log_service = getattr(worker, "_log_service", None)
        if log_service is None:
            return
        try:
            from src.application.services.log_service import LogLevel

            log_service.add_entry(
                monitoring_id=getattr(worker, "_monitoring_id", None),
                level=LogLevel("error"),
                source="monitoring_service",
                message=self.CAMERA_UNAVAILABLE_MESSAGE,
            )
        except Exception:
            pass

    @staticmethod
    def _is_video_recording_worker(worker) -> bool:
        """True if the worker is a VideoRecordingWorker (video-first flow)."""
        return hasattr(worker, "recording_metrics") and hasattr(worker, "get_last_frame")

    def _finalize_video_first(self, monitoring_id: int, worker) -> Monitoring:
        """Validate + atomically promote the recorded video, then STOP (Spec 020).

        Order: worker already finalized/released camera -> validate temp ->
        os.replace to monitoring.mp4 -> persist video_path -> write recording
        metrics + capture_completed_at -> running -> ready_for_analysis.

        Spec 020: finalize NO LONGER auto-launches analysis. The monitoring is
        left in ready_for_analysis with the camera released and ZERO analysis
        threads; the operator starts analysis explicitly later via
        start_deferred_analysis. Invalid/unreadable temp -> keep temp,
        transition to error.
        """
        import dataclasses
        import os

        from src.infrastructure.config.settings import BASE_DIR
        from src.infrastructure.camera.video_recorder import VideoRecorder

        temp_rel = self._recording_temp_path(monitoring_id)
        final_rel = self._recording_final_path(monitoring_id)
        temp_abs = str(BASE_DIR / temp_rel)
        final_abs = str(BASE_DIR / final_rel)

        # Validate the temporary recording (exists, size>0, opens, reads >=1 frame).
        # Sanitize the relative temp path against BASE_DIR before touching the file.
        try:
            valid = VideoRecorder(
                output_path=temp_rel,
                fps=1.0,
                allowed_base=BASE_DIR,
            ).validate()
        except Exception as e:
            logger.warning(f"Video validation raised for {monitoring_id}: {e}")
            valid = False

        if not valid:
            # Keep the temp/failed file for diagnosis; do NOT promote; go to error.
            logger.error(
                f"Video-first finalize {monitoring_id}: temp video invalid/unreadable "
                f"({temp_rel}); keeping temp, transitioning to error."
            )
            monitoring = self._monitoring_repo.update_status(
                monitoring_id, MonitoringState.ERROR.value
            )
            self._registry.remove(monitoring_id)
            self._registry.release_finalization(monitoring_id)
            return monitoring

        # Atomic promotion temp -> final on the same filesystem.
        try:
            os.makedirs(os.path.dirname(final_abs), exist_ok=True)
            os.replace(temp_abs, final_abs)
        except Exception as e:
            logger.error(
                f"Video-first finalize {monitoring_id}: atomic rename failed: {e}"
            )
            monitoring = self._monitoring_repo.update_status(
                monitoring_id, MonitoringState.ERROR.value
            )
            self._registry.remove(monitoring_id)
            self._registry.release_finalization(monitoring_id)
            return monitoring

        # Persist the relative video_path ONLY after a valid promotion.
        try:
            self._monitoring_repo.update_video_path(monitoring_id, final_rel)
        except Exception as e:
            logger.error(
                f"Video-first finalize {monitoring_id}: failed to persist video_path: {e}"
            )
            self._rollback_request_session_best_effort()
            monitoring = self._monitoring_repo.update_status(
                monitoring_id, MonitoringState.ERROR.value
            )
            self._registry.remove(monitoring_id)
            self._registry.release_finalization(monitoring_id)
            return monitoring

        # Write recording metrics + capture_completed_at (recoverable -- errors do
        # not block flow). Spec 020: at finalize we persist ONLY capture_completed_at.
        # manual_deferred_analysis / deferred_analysis_started_at are written ONLY
        # on a successful manual analysis start (never here).
        try:
            from src.infrastructure.persistence.local.snapshot_analysis_report_writer import (
                SnapshotAnalysisReportWriter,
            )
            from src.infrastructure.config.settings import ACTIVE_PROFILE, OUTPUTS_DIR

            recording_data = dataclasses.asdict(worker.recording_metrics)
            recording_data["capture_completed_at"] = _utcnow_iso()
            writer = SnapshotAnalysisReportWriter(
                base_outputs_dir=OUTPUTS_DIR / "monitorings"
            )
            write_result = writer.write_capture_metrics(
                monitoring_id=monitoring_id,
                capture_metrics=recording_data,
                profile_name=ACTIVE_PROFILE.name,
            )
            for error in write_result.errors:
                logger.warning(
                    f"Recording metrics write error for {monitoring_id}: {error}"
                )
        except Exception as e:
            logger.warning(f"Failed to write recording metrics for {monitoring_id}: {e}")

        # Spec 020: running -> ready_for_analysis and STOP. Do NOT launch analysis,
        # do NOT register any analysis thread. Camera is already released by the
        # VideoRecordingWorker. The operator starts analysis explicitly later.
        monitoring = self._monitoring_repo.update_status(
            monitoring_id, MonitoringState.READY_FOR_ANALYSIS.value
        )

        # Spec 020: capture phase is over — clear the device-global capture mark
        # so other modules can start recording while this one waits for analysis.
        self._registry.clear_capture_active(monitoring_id)
        # Drop the recording worker runtime (no analysis runtime is created).
        self._registry.remove_runtime(monitoring_id)
        self._registry.release_finalization(monitoring_id)
        return monitoring

    def _run_video_analysis(
        self, monitoring_id: int, video_rel_path: str, config=None
    ) -> None:
        """Run VideoAnalysisService on the final video in a background thread.

        Fresh DB session; on success persists MonitoringMetrics via
        _build_metrics_from_analysis_result and transitions analyzing → completed.
        A later analysis failure NEVER modifies/deletes the validated video.
        """
        thread_session = None
        try:
            from src.infrastructure.persistence.database import DatabaseManager
            db_manager = DatabaseManager()
            thread_session = db_manager.get_session()
        except Exception as e:
            logger.error(
                f"Failed to create DB session for video analysis {monitoring_id}: {e}"
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
            from src.infrastructure.camera.opencv_video_reader import OpenCvVideoReader
            from src.application.services.video_analysis_service import (
                VideoAnalysisService, VideoAnalysisConfig,
            )

            thread_monitoring_repo = SqlMonitoringRepository(session=thread_session)
            thread_snapshot_repo = SqlSnapshotRepository(session=thread_session)
            thread_inspection_repo = SqlInspectionResultRepository(session=thread_session)
            thread_metrics_repo = SqlMonitoringMetricsRepository(session=thread_session)

            thermal_pause_event = threading.Event()
            thermal_monitor = ThermalMonitor(
                pause_event=thermal_pause_event,
                poll_interval_seconds=ACTIVE_PROFILE.thermal_poll_interval_seconds,
                warning_temp=ACTIVE_PROFILE.analysis_thermal_pause_threshold,
                critical_temp=ACTIVE_PROFILE.analysis_thermal_pause_threshold,
                resume_temp=ACTIVE_PROFILE.analysis_thermal_resume_threshold,
            )

            # Reprocess supplies an explicit config; the normal flow uses the
            # active profile's sparse defaults.
            if config is None:
                config = VideoAnalysisConfig(
                    min_frames_between_detections=ACTIVE_PROFILE.sparse_min_frames_between_detections,
                    max_frames_without_detection=ACTIVE_PROFILE.sparse_max_frames_without_detection,
                    use_scene_gate=ACTIVE_PROFILE.sparse_use_scene_gate,
                    enable_flow_propagation=ACTIVE_PROFILE.sparse_enable_flow_propagation,
                    save_annotated_video=ACTIVE_PROFILE.save_annotated_video,
                )

            analysis_service = VideoAnalysisService(
                monitoring_id=monitoring_id,
                video_path=video_rel_path,
                snapshot_repo=thread_snapshot_repo,
                inspection_result_repo=thread_inspection_repo,
                monitoring_repo=thread_monitoring_repo,
                db_session=thread_session,
                config=config,
                video_reader=OpenCvVideoReader(video_rel_path),
                thermal_monitor=thermal_monitor,
                profile_name=ACTIVE_PROFILE.name,
            )

            self._registry.set_worker(monitoring_id, analysis_service)

            result = analysis_service.run()

            if result.status == "completed" and analysis_service.error_reason is None:
                thread_monitoring_repo.update_counters(
                    monitoring_id,
                    total_snapshots=result.detector_scheduled_frames,
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
            logger.error(f"Video analysis {monitoring_id} crashed: {e}")
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

    def reprocess_monitoring(self, monitoring_id: int, config=None) -> Monitoring:
        """Reprocess a terminal monitoring's existing video with a new config.

        Reuses the existing monitoring.mp4 (no camera, no re-recording, video
        NEVER modified/deleted). Preconditions: terminal (completed/error),
        video_path non-null, file present on disk, no other active session and no
        reprocess already running. STRICT sync guard: blocked if the Monitoring
        or ANY Snapshot / DetectionInspectionResult / MonitoringMetrics is
        remote_sync_status == "synced" (nothing is altered when blocked).

        Args:
            monitoring_id: Terminal monitoring to reprocess.
            config: Optional VideoAnalysisConfig; defaults to the active profile.

        Raises:
            MonitoringNotFoundError, ReprocessPreconditionError,
            ActiveSessionError, ReprocessInProgressError,
            ReprocessBlockedBySyncError.
        """
        import os
        from src.infrastructure.config.settings import BASE_DIR
        from src.infrastructure.security.path_sanitizer import (
            PathTraversalError,
            validate_safe_path,
        )

        monitoring = self._get_monitoring_or_raise(monitoring_id)

        # Precondition: terminal state completed/error.
        terminal = {
            MonitoringState.COMPLETED.value,
            MonitoringState.ERROR.value,
        }
        if monitoring.status not in terminal:
            raise ReprocessPreconditionError(
                "Solo se puede reprocesar un monitoreo terminado (completed/error)."
            )

        # Precondition: video_path present.
        if not monitoring.video_path:
            raise ReprocessPreconditionError(
                "El monitoreo no tiene un video asociado para reprocesar."
            )

        # Sanitize the persisted video_path BEFORE any file access or result
        # clearing. A malicious/absolute/traversal path is rejected here, so no
        # local data is touched. Reuses the shared path_sanitizer.
        try:
            video_abs = str(validate_safe_path(monitoring.video_path, BASE_DIR))
        except PathTraversalError as exc:
            raise ReprocessPreconditionError(
                f"La ruta del video del monitoreo no es válida: {exc.reason}"
            ) from exc

        # Precondition: file exists on disk (after sanitizing).
        if not os.path.exists(video_abs):
            raise ReprocessPreconditionError(
                "El archivo de video del monitoreo no existe en disco."
            )

        # Precondition: no other active session for the module, no reprocess in progress.
        for m in self._monitoring_repo.get_by_module(monitoring.module_id):
            if m.id != monitoring_id and m.status in _ACTIVE_STATUSES:
                raise ActiveSessionError(monitoring.module_id, m.id)

        # STRICT sync guard — BEFORE touching any data.
        if self._monitoring_repo.has_synced_descendants(monitoring_id):
            raise ReprocessBlockedBySyncError()

        # Exclusive claim to prevent concurrent reprocess.
        if not self._registry.claim_finalization(monitoring_id):
            raise ReprocessInProgressError(monitoring_id)

        # Spec 020: reprocess is a heavy analysis on the device. Acquire the
        # device-global analysis slot BEFORE any destructive clear/reset. If the
        # device is busy (a capture is active/reserved, or another analysis owns
        # the slot), REJECT here — do NOT clear results, do NOT change state, do
        # NOT launch a thread. A bare MagicMock registry returns a truthy Mock, so
        # we only reject on an explicit ``is False`` result.
        if self._registry.claim_global_analysis(monitoring_id) is False:
            self._registry.release_finalization(monitoring_id)
            raise ReprocessDeviceBusyError(monitoring_id)

        try:
            # Clear previous derived results (NOT the video). Guard already passed.
            self._monitoring_repo.clear_analysis_results(monitoring_id)
            self._clear_derived_artifacts(monitoring_id)

            # Controlled reset to analyzing (audited, not a FSM edge).
            self._monitoring_repo.reset_for_reprocess(monitoring_id)

            # Launch analysis on the SAME video with the given config. The global
            # slot is released by _run_video_analysis's finally (registry.remove).
            analysis_thread = threading.Thread(
                target=self._run_video_analysis,
                args=(monitoring_id, monitoring.video_path, config),
                daemon=True,
                name=f"video-reprocess-worker-{monitoring_id}",
            )
            self._registry.register(monitoring_id, None, analysis_thread)
            analysis_thread.start()
        except Exception:
            # Launch failed before analysis ran: release the device-global slot
            # so the device is not left blocked.
            try:
                self._registry.release_global_analysis(monitoring_id)
            except Exception:
                pass
            raise
        finally:
            self._registry.release_finalization(monitoring_id)

        return self._monitoring_repo.get_by_id(monitoring_id)

    def _clear_derived_artifacts(self, monitoring_id: int) -> None:
        """Remove derived artifacts (snapshots/crops/reports) but KEEP the video.

        Best-effort: never raises. The monitoring.mp4 under video/ is preserved.
        """
        import shutil
        from src.infrastructure.config.settings import OUTPUTS_DIR

        base = OUTPUTS_DIR / "monitorings" / str(monitoring_id)
        for sub in ("snapshots", "annotated_snapshots", "crops", "reports"):
            target = base / sub
            try:
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
            except Exception:
                pass

    def recover_abrupt_recordings(self) -> None:
        """Recovery after abrupt termination (Task 11.1).

        For each monitoring, if a leftover monitoring.recording.mp4 exists (the
        worker's finally did not promote it), keep it, validate it safely, and
        promote it to monitoring.mp4 ONLY if it validates (atomic rename + persist
        relative video_path). Invalid temps are kept for diagnosis and NEVER
        auto-marked as a valid monitoring.mp4. Never raises.
        """
        import os
        from src.infrastructure.config.settings import BASE_DIR, OUTPUTS_DIR
        from src.infrastructure.camera.video_recorder import VideoRecorder
        from src.infrastructure.security.path_sanitizer import (
            PathTraversalError,
            validate_safe_path,
        )

        monitorings_dir = OUTPUTS_DIR / "monitorings"
        try:
            if not monitorings_dir.exists():
                return
            entries = list(monitorings_dir.iterdir())
        except Exception:
            return

        for entry in entries:
            try:
                if not entry.is_dir() or not entry.name.isdigit():
                    continue
                monitoring_id = int(entry.name)
                temp_rel = self._recording_temp_path(monitoring_id)
                final_rel = self._recording_final_path(monitoring_id)

                # Confine both paths to BASE_DIR before any file access. These are
                # system-generated from an integer id, so this is defense-in-depth:
                # a manipulated path never escapes outputs/monitorings.
                try:
                    temp_abs = str(validate_safe_path(temp_rel, BASE_DIR))
                    final_abs = str(validate_safe_path(final_rel, BASE_DIR))
                except PathTraversalError:
                    logger.warning(
                        f"Recovery: rejected non-confined path for monitoring "
                        f"{monitoring_id}; skipping."
                    )
                    continue

                if not os.path.exists(temp_abs):
                    continue
                # A final already exists — keep the temp for diagnosis, do nothing.
                if os.path.exists(final_abs):
                    continue

                # Validate the leftover temp safely (path sanitized).
                try:
                    valid = VideoRecorder(
                        output_path=temp_rel, fps=1.0, allowed_base=BASE_DIR
                    ).validate()
                except Exception:
                    valid = False

                if not valid:
                    # Keep for diagnosis; never auto-promote.
                    logger.warning(
                        f"Recovery: leftover temp for monitoring {monitoring_id} is "
                        f"invalid; keeping for diagnosis (not promoted)."
                    )
                    continue

                # Promote atomically and persist relative video_path.
                try:
                    os.makedirs(os.path.dirname(final_abs), exist_ok=True)
                    os.replace(temp_abs, final_abs)
                    self._monitoring_repo.update_video_path(monitoring_id, final_rel)
                    logger.info(
                        f"Recovery: promoted leftover recording for monitoring "
                        f"{monitoring_id} to {final_rel}."
                    )
                except Exception as e:
                    logger.error(
                        f"Recovery: failed to promote temp for monitoring "
                        f"{monitoring_id}: {e}"
                    )
            except Exception:
                # Never let one bad entry break recovery.
                continue

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
        longer block new monitorings for the module. This ONLY updates status:
        it never clears video_path nor deletes the recorded video, so a
        video-first session remains reprocessable after reconciliation
        (Task 11.5).
        """
        existing = self._monitoring_repo.get_by_module(module_id)
        for m in existing:
            if m.status not in _ACTIVE_STATUSES:
                continue

            # Spec 020: ready_for_analysis is the ONE active state that may
            # legitimately exist WITHOUT a live runtime thread (capture finished,
            # video validated, camera released, analysis not yet started). It
            # survives reboot and must NOT be reconciled to error.
            if m.status == MonitoringState.READY_FOR_ANALYSIS.value:
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

    def reconcile_orphaned_sessions_on_startup(self) -> None:
        """Reconcile sessions left active after a process restart (startup only).

        On restart the in-memory runtime registry is empty, so ANY monitoring
        still in a non-terminal status (initializing / running / paused /
        finishing / analyzing) has no worker backing it and can never progress
        on its own. Without this, such a session — notably one stuck in
        'analyzing' — would remain active indefinitely, blocking the module and
        leaving the UI polling 0/0 forever.

        Each orphaned session is transitioned to 'error'. This ONLY updates the
        status: it never clears video_path nor deletes monitoring.mp4, so a
        video-first session remains reprocessable afterwards. It does NOT
        auto-reprocess anything at startup. Best-effort — never raises.
        """
        try:
            active = self._monitoring_repo.get_active()
        except Exception as e:
            logger.error(f"Startup reconciliation: failed to list active sessions: {e}")
            return

        for m in active:
            # Spec 020: ready_for_analysis legitimately has no live thread after
            # reboot (capture done, video validated, camera released, analysis not
            # started). Keep it — it is recoverable and the operator starts the
            # analysis explicitly later. It is NOT an orphan.
            if m.status == MonitoringState.READY_FOR_ANALYSIS.value:
                continue

            # Defensive: a live worker could exist if reconciliation is ever
            # invoked outside the empty-registry startup path.
            thread = self._registry.get_thread(m.id)
            if thread is not None and thread.is_alive():
                continue

            logger.warning(
                f"Startup reconciliation: orphaned session monitoring_id={m.id}, "
                f"status={m.status}. Transitioning to 'error' "
                f"(video preserved, reprocessable)."
            )
            try:
                self._monitoring_repo.update_status(
                    m.id, MonitoringState.ERROR.value
                )
            except Exception as e:
                logger.error(
                    f"Startup reconciliation: failed to mark session {m.id} "
                    f"as error: {e}"
                )
            # Drop any stale registry reference (no-op if absent).
            try:
                self._registry.remove(m.id)
            except Exception:
                pass

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

        # Spec 020: this capture is no longer active for device-global coordination.
        self._registry.clear_capture_active(monitoring_id)
        # Clean up from the shared registry (preserve finalization claims).
        self._registry.remove_runtime(monitoring_id)

    def _run_video_recording_worker(self, monitoring_id: int, worker) -> None:
        """Run the VideoRecordingWorker in a background thread (video-first).

        Creates a fresh DB session, transitions initializing → running, runs the
        recording loop (which owns the camera and releases it in its finally),
        and marks error if the worker exited due to an error.
        """
        from src.infrastructure.persistence.database import DatabaseManager
        from src.infrastructure.persistence.repositories import SqlMonitoringRepository

        try:
            db_manager = DatabaseManager()
            thread_session = db_manager.get_session()
        except Exception as e:
            logger.error(
                f"Failed to create thread-local DB session for recording {monitoring_id}: {e}"
            )
            worker.release_resources()
            self._registry.remove_runtime(monitoring_id)
            return

        thread_monitoring_repo = SqlMonitoringRepository(session=thread_session)
        worker._monitoring_repo = thread_monitoring_repo
        worker._db_session = thread_session

        try:
            thread_monitoring_repo.update_status(
                monitoring_id, MonitoringState.RUNNING.value
            )
            thread_session.commit()
        except Exception as e:
            logger.error(
                f"Failed to transition recording {monitoring_id} to running: {e}"
            )
            thread_session.close()
            worker.release_resources()
            self._registry.remove_runtime(monitoring_id)
            return

        # Run the recording loop (blocking; releases camera in its finally).
        worker.run()

        # If the worker exited due to an error, mark the session error so the
        # module unblocks and finalize does not run on a dead session. This
        # covers BOTH exit_reason=="error" AND exit_reason=="frame_source_exhausted"
        # (camera unavailable / stopped responding) — any run with error_reason set
        # must not remain in `running`.
        if worker.error_reason:
            try:
                thread_monitoring_repo.update_status(
                    monitoring_id, MonitoringState.ERROR.value
                )
                thread_session.commit()
            except Exception as e:
                logger.error(
                    f"Failed to transition recording {monitoring_id} to error: {e}"
                )
            logger.error(
                f"Recording {monitoring_id} ended with error "
                f"(exit_reason={worker.recording_metrics.exit_reason}): "
                f"{worker.error_reason}"
            )
            # Surface the actionable operator-facing message (tasks.md 10.1) when
            # the failure is a camera-unavailability exit.
            if worker.recording_metrics.exit_reason == "frame_source_exhausted":
                self._emit_camera_unavailable_log(worker)

        try:
            thread_session.close()
        except Exception:
            pass

        # Spec 020: this capture is no longer active for device-global coordination.
        # (finalize's success path already cleared it; this covers error/exhausted
        # exits where finalize does not run.)
        self._registry.clear_capture_active(monitoring_id)
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
