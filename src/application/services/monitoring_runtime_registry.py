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

    def remove(self, monitoring_id: int) -> None:
        """Remove worker, thread, and finalization claim for a monitoring session."""
        with self._lock:
            self._workers.pop(monitoring_id, None)
            self._threads.pop(monitoring_id, None)
            self._finalization_claims.discard(monitoring_id)
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
