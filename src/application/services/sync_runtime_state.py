"""SyncRuntimeState: in-memory thread-safe state for manual sync coordination.

Provides:
1. Mutual exclusion: prevents two simultaneous manual sync operations.
2. Runtime progress: exposes current phase/progress to UI/API.

This class is deliberately ephemeral — state is lost on process restart.
Persisted sync status (pending/syncing/synced/error) lives in SQLite via
SyncStateRepository. A persisted "syncing" after restart is treated as
stale/retryable by SyncStateRepository.get_pending_entities().

Ownership model:
- The future sync_api route owns try_acquire() / release().
- RemoteSyncService only calls update_progress().
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SyncRuntimeState:
    """Thread-safe in-memory state for manual sync coordination.

    All public methods are protected by a threading.Lock to ensure
    safe concurrent access from FastAPI threadpool workers.
    """

    is_syncing: bool = False
    is_recovering: bool = False
    phase: str = ""
    processed: int = 0
    total: int = 0
    errors: list[str] = field(default_factory=list)
    last_result: Optional[dict] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def try_acquire(self) -> bool:
        """Attempt to start a new sync operation.

        Returns True if acquired (device was idle), False if a sync OR a
        recovery is already active (at most one manual cloud operation may run
        at a time). Resets progress counters for the new operation on success.
        Does NOT modify state of an active operation on failure.
        """
        with self._lock:
            if self.is_syncing or self.is_recovering:
                return False
            self.is_syncing = True
            self.phase = ""
            self.processed = 0
            self.total = 0
            self.errors = []
            return True

    def release(self, result: Optional[dict] = None) -> None:
        """Mark sync operation as completed.

        Stores the result summary and clears the syncing flag.
        Safe to call with result=None (e.g., on exception before
        RemoteSyncService produces a result).

        Does NOT reset phase/processed/total so that the last progress
        snapshot remains observable until the next try_acquire().
        """
        with self._lock:
            self.is_syncing = False
            self.last_result = result

    def update_progress(self, phase: str, processed: int, total: int) -> None:
        """Update runtime progress for the active sync operation.

        Called by RemoteSyncService during sync execution.
        Does not modify is_syncing, last_result, or errors.
        """
        with self._lock:
            self.phase = phase
            self.processed = processed
            self.total = total

    def get_status(self) -> dict:
        """Return a snapshot of the current runtime state.

        Returns a new dict with a copy of the errors list to prevent
        external mutation of internal state.

        NOTE: This snapshot deliberately does NOT expose recovery state — the
        GET /api/sync/status contract is unchanged. Recovery state is used only
        internally for mutual exclusion via get_active_operation().
        """
        with self._lock:
            return {
                "is_syncing": self.is_syncing,
                "phase": self.phase,
                "processed": self.processed,
                "total": self.total,
                "errors": list(self.errors),
                "last_result": self.last_result,
            }

    # ------------------------------------------------------------------
    # Recovery coordination (Spec 022, block E)
    #
    # Recovery reuses this single shared object so that at most ONE manual
    # cloud operation (sync OR recovery) runs at a time. Recovery does NOT use
    # the sync progress fields (phase/processed/total/errors/last_result).
    # ------------------------------------------------------------------

    def try_acquire_recovery(self) -> bool:
        """Attempt to start a manual recovery operation.

        Returns True if acquired (device was idle), False if a sync OR a
        recovery is already active. On success only the recovery flag is set;
        the sync progress fields are left untouched.
        """
        with self._lock:
            if self.is_syncing or self.is_recovering:
                return False
            self.is_recovering = True
            return True

    def release_recovery(self) -> None:
        """Mark the recovery operation as completed. Idempotent.

        Clears only the recovery flag; never touches sync state or progress.
        """
        with self._lock:
            self.is_recovering = False

    def get_active_operation(self) -> Optional[str]:
        """Return the currently active manual cloud operation under the lock.

        Returns "sync", "recovery", or None. If both flags were somehow set,
        "sync" takes precedence in reporting (should not happen given the
        mutual-exclusion guards).
        """
        with self._lock:
            if self.is_syncing:
                return "sync"
            if self.is_recovering:
                return "recovery"
            return None
