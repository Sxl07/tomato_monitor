"""Shared runtime registry for active monitoring workers and threads.

This module provides a thread-safe singleton that tracks which worker
instances (CaptureWorker during capture, SnapshotAnalysisService during
analysis) and their threads are currently alive. It is stored on app.state
and shared across all request-scoped MonitoringService instances.

This solves the core bug: MonitoringService is created per-request, but worker
state must persist across requests to correctly handle abort, orphan detection,
and camera lock ownership.

Additionally provides finalization claims so that only one thread may finalize
a given monitoring session at a time (prevents race between capture completion
callback and explicit user finalization).
"""

import threading
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class MonitoringRuntimeRegistry:
    """Thread-safe registry of active monitoring workers and their threads.

    One global instance lives on app.state. All MonitoringService instances
    read and write to this shared registry.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._workers: dict[int, object | None] = {}
        self._threads: dict[int, threading.Thread] = {}
        self._finalization_claims: set[int] = set()
        # Spec 020: analysis claims are SEPARATE from finalization claims. They
        # guarantee exactly one deferred-analysis start per monitoring under
        # concurrent requests.
        self._analysis_claims: set[int] = set()
        # Spec 020 device-global coordination (single RPi, one camera):
        #   - _active_captures: monitoring_ids with an ACTIVE capture/recording
        #     session. Enables has_active_capture() as a GLOBAL, module-independent
        #     source of truth (has_live_worker_for_module is per-module and NOT
        #     sufficient for device-global coordination).
        #   - _global_analysis_owner: at most ONE heavy analysis may run on the
        #     whole device at a time. Holds the monitoring_id that owns the
        #     device-global analysis slot, or None.
        self._active_captures: set[int] = set()
        # Capture reservations: held atomically from BEFORE a capture worker
        # thread starts until it is explicitly released. They contend with the
        # heavy-analysis slot in a SINGLE device-global decision, closing the
        # race where a simultaneous capture-start and analysis-start could both
        # pass their separate checks.
        self._capture_reservations: set[int] = set()
        self._global_analysis_owner: Optional[int] = None

    def register(
        self, monitoring_id: int, worker: object | None, thread: threading.Thread
    ) -> None:
        """Register a worker and its thread for a monitoring session."""
        with self._lock:
            self._workers[monitoring_id] = worker
            self._threads[monitoring_id] = thread
            logger.info(
                f"Registry: registered worker for monitoring {monitoring_id}."
            )

    def get_worker(self, monitoring_id: int) -> object | None:
        """Get the worker for a monitoring session, or None."""
        with self._lock:
            return self._workers.get(monitoring_id)

    def get_thread(self, monitoring_id: int) -> Optional[threading.Thread]:
        """Get the thread for a monitoring session, or None."""
        with self._lock:
            return self._threads.get(monitoring_id)

    def set_worker(self, monitoring_id: int, worker: object | None) -> None:
        """Update the worker/runtime for a session without replacing the thread."""
        with self._lock:
            self._workers[monitoring_id] = worker

    def remove_runtime(self, monitoring_id: int) -> None:
        """Remove worker and thread but preserve finalization claim."""
        with self._lock:
            self._workers.pop(monitoring_id, None)
            self._threads.pop(monitoring_id, None)

    def claim_finalization(self, monitoring_id: int) -> bool:
        """Attempt to claim exclusive finalization. Returns True if claimed, False if already claimed."""
        with self._lock:
            if monitoring_id in self._finalization_claims:
                return False
            self._finalization_claims.add(monitoring_id)
            return True

    def release_finalization(self, monitoring_id: int) -> None:
        """Release a finalization claim."""
        with self._lock:
            self._finalization_claims.discard(monitoring_id)

    def is_finalization_claimed(self, monitoring_id: int) -> bool:
        """Check if finalization is currently claimed."""
        with self._lock:
            return monitoring_id in self._finalization_claims

    def claim_analysis(self, monitoring_id: int) -> bool:
        """Attempt to claim exclusive deferred-analysis start (Spec 020).

        Returns True if claimed, False if an analysis is already claimed for this
        monitoring. Distinct from finalization claims. Atomic under the RLock.
        """
        with self._lock:
            if monitoring_id in self._analysis_claims:
                return False
            self._analysis_claims.add(monitoring_id)
            return True

    def release_analysis(self, monitoring_id: int) -> None:
        """Release an analysis claim (idempotent)."""
        with self._lock:
            self._analysis_claims.discard(monitoring_id)

    def is_analysis_claimed(self, monitoring_id: int) -> bool:
        """Check if a deferred-analysis start is currently claimed."""
        with self._lock:
            return monitoring_id in self._analysis_claims

    # ------------------------------------------------------------------ #
    # Spec 020: device-global capture/analysis coordination
    # ------------------------------------------------------------------ #

    def _has_capture_phase_locked(self) -> bool:
        """True if any capture reservation OR live active capture exists.

        Caller MUST hold self._lock. Prunes stale active-capture marks (thread
        died without clearing). Reservations are NEVER pruned by liveness — a
        reservation is held from BEFORE the worker thread starts until it is
        explicitly released, closing the start_session/finalize race window.
        """
        if self._capture_reservations:
            return True
        stale = [
            mid
            for mid in self._active_captures
            if mid not in self._threads or not self._threads[mid].is_alive()
        ]
        for mid in stale:
            self._active_captures.discard(mid)
        return len(self._active_captures) > 0

    def reserve_capture(self, monitoring_id: int) -> bool:
        """Atomically reserve the device for a capture phase (Spec 020).

        A single device-global decision, contending with the heavy-analysis slot:
        the reservation is granted ONLY if no heavy analysis owns the device-global
        slot. Once reserved, claim_global_analysis() will fail until the capture
        reservation (and any active capture) is released. Returns False if a heavy
        analysis is active. Atomic under the RLock. The reservation is NOT pruned
        by thread liveness (it is held before the worker thread starts).
        """
        with self._lock:
            if self._global_analysis_owner is not None:
                return False
            self._capture_reservations.add(monitoring_id)
            return True

    def release_capture_reservation(self, monitoring_id: int) -> None:
        """Release a capture reservation (idempotent). Call on start failure."""
        with self._lock:
            self._capture_reservations.discard(monitoring_id)

    def mark_capture_active(self, monitoring_id: int) -> None:
        """Mark a monitoring as having an ACTIVE capture/recording session.

        Called when a capture-first / video-first worker starts. Enables the
        GLOBAL, module-independent has_active_capture() query used to block a
        deferred analysis while any capture is running on the device.
        """
        with self._lock:
            self._active_captures.add(monitoring_id)

    def clear_capture_active(self, monitoring_id: int) -> None:
        """Clear the active-capture mark AND any capture reservation (idempotent)."""
        with self._lock:
            self._active_captures.discard(monitoring_id)
            self._capture_reservations.discard(monitoring_id)

    def has_active_capture(self) -> bool:
        """Return True if ANY capture phase is active/reserved on the device.

        GLOBAL and independent of module. Includes both pending reservations
        (before the worker thread starts) and live active captures; stale active
        marks (thread died without clearing) are pruned. This is the authoritative
        source for device-global capture detection; is_camera_locked() is only a
        hardware safety net, not the sole source.
        """
        with self._lock:
            return self._has_capture_phase_locked()

    def claim_global_analysis(self, monitoring_id: int) -> bool:
        """Claim the device-global heavy-analysis slot (at most one at a time).

        ATOMIC device-global decision contending with capture: the slot is granted
        ONLY if no other heavy analysis owns it AND no capture phase is active or
        reserved. Returns True if granted, False otherwise. Atomic under the RLock
        so a simultaneous capture-start and analysis-start can never both win.
        """
        with self._lock:
            if self._global_analysis_owner is not None:
                return False
            if self._has_capture_phase_locked():
                return False
            self._global_analysis_owner = monitoring_id
            return True

    def release_global_analysis(self, monitoring_id: int) -> None:
        """Release the device-global analysis slot if owned by this monitoring."""
        with self._lock:
            if self._global_analysis_owner == monitoring_id:
                self._global_analysis_owner = None

    def is_global_analysis_active(self) -> bool:
        """Return True if a heavy analysis currently owns the device-global slot."""
        with self._lock:
            return self._global_analysis_owner is not None

    def remove(self, monitoring_id: int) -> None:
        """Remove worker, thread, finalization AND analysis claims for a session."""
        with self._lock:
            self._workers.pop(monitoring_id, None)
            self._threads.pop(monitoring_id, None)
            self._finalization_claims.discard(monitoring_id)
            self._analysis_claims.discard(monitoring_id)
            self._active_captures.discard(monitoring_id)
            self._capture_reservations.discard(monitoring_id)
            if self._global_analysis_owner == monitoring_id:
                self._global_analysis_owner = None
            logger.info(
                f"Registry: removed worker for monitoring {monitoring_id}."
            )

    def has_live_worker_for_module(self, module_id: int, monitoring_repo) -> bool:
        """Check if any live worker thread exists for sessions of a given module.

        Uses the monitoring_repo to map monitoring_ids to module_ids.
        Returns True if at least one registered thread is alive for the module.
        """
        with self._lock:
            for mid, thread in list(self._threads.items()):
                if not thread.is_alive():
                    continue
                # Check if this monitoring belongs to the given module
                monitoring = monitoring_repo.get_by_id(mid)
                if monitoring is not None and monitoring.module_id == module_id:
                    return True
            return False

    def has_any_live_thread(self) -> bool:
        """Check if any registered worker thread is still alive."""
        with self._lock:
            for thread in self._threads.values():
                if thread.is_alive():
                    return True
            return False

    def cleanup_dead(self) -> None:
        """Remove entries for threads that are no longer alive."""
        with self._lock:
            dead_ids = [
                mid for mid, thread in self._threads.items()
                if not thread.is_alive()
            ]
            for mid in dead_ids:
                self._workers.pop(mid, None)
                self._threads.pop(mid, None)
                logger.debug(f"Registry: cleaned up dead worker for monitoring {mid}.")
