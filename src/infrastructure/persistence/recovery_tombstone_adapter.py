"""SqlRecoveryTombstoneAdapter: anti-resurrection guard over deletion_outbox.

Implements RecoveryTombstonePort by querying the durable local
``deletion_outbox`` for a BLOCKING tombstone matching a remote identity
(entity_type + remote_id) whose remote status is not yet ``synced``.

Each check opens a fresh, short-lived local Session (via the injected
session_factory), never keeps a Session open between checks, never commits, and
always closes the Session. It does not mutate outbox state or Spec 021 deletion
semantics.
"""

from __future__ import annotations

from typing import Callable

from sqlalchemy.orm import Session

from src.application.interfaces.recovery_tombstone_port import TombstoneCheckResult
from src.infrastructure.persistence.deletion_outbox_repository import (
    DeletionOutboxRepository,
)


class SqlRecoveryTombstoneAdapter:
    """SQLAlchemy-backed RecoveryTombstonePort implementation.

    Reuses DeletionOutboxRepository.find_blocking_by_remote_identity to perform
    the exact (entity_type, remote_id) lookup restricted to blocking statuses
    (pending | syncing | error).
    """

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        """Initialize with a callable producing new Sessions.

        Args:
            session_factory: Callable returning a fresh Session (compatible with
                DatabaseManager.get_session).
        """
        self._session_factory = session_factory

    def check(self, entity_type: str, remote_id: str) -> TombstoneCheckResult:
        """Return whether a blocking tombstone exists for the remote identity.

        On any unexpected error the result is ``success=False`` so the caller
        fails safe (skips the row) rather than risking resurrection.
        """
        try:
            # The repository manages its own short-lived session per call; it
            # never keeps a session open, never commits, and closes it.
            repo = DeletionOutboxRepository(self._session_factory)
            entry = repo.find_blocking_by_remote_identity(entity_type, remote_id)
        except Exception as exc:  # noqa: BLE001 - classified as a safe failure
            return TombstoneCheckResult(
                success=False,
                blocked=False,
                error_message=f"Tombstone lookup failed ({type(exc).__name__}).",
            )

        if entry is None:
            return TombstoneCheckResult(success=True, blocked=False)
        return TombstoneCheckResult(
            success=True, blocked=True, status=entry.status
        )
