"""Port: LocalCascadePort.

Defines the abstract contract for executing the local cascade delete
(Local_Cascade) of a root entity. Concrete implementations (e.g., a
SQLAlchemy repository relying on local ON DELETE CASCADE) reside in the
infrastructure layer.

The Local_Cascade deletes only local database records (SQLite). It never
removes physical artifacts under ``outputs/``; deferred physical cleanup is a
later phase driven by the Retention_Window.

The cascade is executed within a single transaction (TX2) that ALSO sets the
outbox entry's local_delete_status='completed' and deleted_at. This coupling
guarantees durability: on success the hierarchy is gone and the entry is
marked completed atomically; on failure the transaction rolls back, leaving
the hierarchy intact and the entry NOT marked completed.

The port depends only on stdlib/typing/dataclasses. It performs no network work.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol


@dataclass
class LocalCascadeResult:
    """Result of a Local_Cascade delete executed within TX2.

    Attributes:
        success: Whether the cascade committed successfully. When True, zero
            dependent records remain and the outbox entry was marked
            local_delete_status='completed' with deleted_at set (same TX2).
        error_message: Failure description on rollback. When success is False,
            the hierarchy is left intact and the entry is NOT marked completed.
    """

    success: bool
    error_message: Optional[str] = None


class LocalCascadePort(Protocol):
    """Abstract port for the transactional local cascade delete (TX2).

    Contract:
        - execute_cascade deletes the root entity and its dependent hierarchy
          (greenhouse -> module -> monitoring -> snapshot -> result -> metrics;
          module -> activity log) relying on local ON DELETE CASCADE.
        - The cascade AND the outbox transition to local_delete_status='completed'
          (with deleted_at) happen in the SAME transaction (TX2).
        - On success, zero dependent records remain for the root entity.
        - On failure, the transaction rolls back: the hierarchy stays intact and
          the entry is NOT marked completed.
        - Only local DB records are touched; ``outputs/`` is never removed here.
    """

    def execute_cascade(
        self,
        entity_type: str,
        entity_local_id: int,
        outbox_id: int,
        deleted_at: datetime,
    ) -> LocalCascadeResult:
        """Delete the root entity's hierarchy and mark the entry completed (TX2).

        Executes the local cascade delete for the given root entity and, within
        the same transaction, sets the outbox entry's local_delete_status to
        ``completed`` and its deleted_at. If any step fails, the whole
        transaction is rolled back, leaving the hierarchy intact and the entry
        not completed.

        Args:
            entity_type: One of ``greenhouse`` | ``module`` | ``monitoring``.
            entity_local_id: Local id of the root entity to delete.
            outbox_id: Local id of the associated outbox entry to mark completed.
            deleted_at: UTC timestamp recorded as the moment of successful local
                deletion (set together with local_delete_status='completed').

        Returns:
            LocalCascadeResult with success=True when the cascade committed and
            the entry was marked completed, or success=False with an
            error_message on rollback.
        """
        ...
