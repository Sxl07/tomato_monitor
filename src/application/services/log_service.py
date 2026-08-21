"""In-memory activity log service for monitoring sessions.

Provides structured, thread-safe log storage scoped per monitoring session.
Designed for the Activity Log Panel UI — entries are NOT persisted to SQLite.
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Optional


class LogLevel(str, Enum):
    """Severity levels for activity log entries."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class LogEntry:
    """Immutable log entry for the activity panel.

    Frozen to guarantee thread-safe sharing between the worker thread
    and HTTP handler thread without defensive copies.
    """

    timestamp: datetime
    level: LogLevel
    source: str
    message: str


class LogService:
    """In-memory, thread-safe log storage scoped per monitoring session.

    Stores up to MAX_ENTRIES per session using a bounded deque.
    Oldest entries are discarded automatically when the limit is exceeded (FIFO eviction).
    """

    MAX_ENTRIES: int = 200

    def __init__(self) -> None:
        self._lock = Lock()
        self._sessions: dict[int, deque[LogEntry]] = {}

    def add_entry(
        self,
        monitoring_id: int,
        level: LogLevel,
        source: str,
        message: str,
    ) -> LogEntry:
        """Create and store a log entry for the given session.

        Args:
            monitoring_id: The monitoring session identifier.
            level: Severity level of the entry.
            source: Component that emitted the entry (e.g., "worker", "camera").
            message: Human-readable message in Spanish for the UI.

        Returns:
            The created LogEntry instance.
        """
        entry = LogEntry(
            timestamp=datetime.now(timezone.utc),
            level=level,
            source=source,
            message=message,
        )
        with self._lock:
            if monitoring_id not in self._sessions:
                self._sessions[monitoring_id] = deque(maxlen=self.MAX_ENTRIES)
            self._sessions[monitoring_id].append(entry)
        return entry

    def get_entries(
        self,
        monitoring_id: int,
        since: Optional[datetime] = None,
    ) -> list[LogEntry]:
        """Return entries for a session, optionally filtered by timestamp.

        Args:
            monitoring_id: The monitoring session identifier.
            since: If provided, only entries with timestamp strictly greater
                than this value are returned.

        Returns:
            List of LogEntry objects ordered by timestamp ascending.
        """
        with self._lock:
            session_deque = self._sessions.get(monitoring_id)
            if session_deque is None:
                return []
            entries = list(session_deque)

        if since is not None:
            # Normalize: if since is naive, assume UTC
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            entries = [e for e in entries if e.timestamp > since]

        return entries

    def clear_session(self, monitoring_id: int) -> None:
        """Remove all entries for a session.

        Called when the Farmer navigates away from the Execution Screen.

        Args:
            monitoring_id: The monitoring session identifier to clear.
        """
        with self._lock:
            self._sessions.pop(monitoring_id, None)
