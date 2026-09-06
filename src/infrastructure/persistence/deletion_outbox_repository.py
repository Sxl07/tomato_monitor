"""DeletionOutboxRepository: infrastructure adapter for the durable outbox.

Implements DeletionOutboxPort by reading/writing the local-only Deletion_Outbox
tables (``deletion_outbox``, ``deletion_outbox_storage_path`` and
``deletion_outbox_local_artifact``) via SQLAlchemy sessions.

Each mutation commits immediately to ensure durable checkpoints: entries and
their children survive a process restart before the Local_Cascade runs
(commit-before-cascade). Sessions are short-lived (created and closed per
operation), mirroring ``SyncStateRepository``.

This module is purely local persistence — no network calls, no Supabase,
no httpx. The two-transaction orchestration (TX1 prepared + COMMIT, TX2
cascade + completed) is composed by ``DeletionService``; this repository only
provides the durable persistence operations that flow depends on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, List, Optional

from sqlalchemy.orm import Session

from src.application.interfaces.deletion_outbox_port import (
    DeletionOutboxEntry,
    DeletionOutboxEntryInput,
    OutboxLocalArtifactRow,
    OutboxStoragePathRow,
)
from src.infrastructure.persistence.models.deletion_outbox_model import (
    DeletionOutboxLocalArtifactModel,
    DeletionOutboxModel,
    DeletionOutboxStoragePathModel,
)

# Local-delete durability values that are NOT yet completed and therefore make
# an existing entry reusable for idempotent re-enqueue.
_NON_COMPLETED_LOCAL_STATUSES = ("prepared", "failed")

# Remote propagation statuses considered retryable. Persisted "syncing" is
# stale after a crash/restart and is retried.
_RETRYABLE_REMOTE_STATUSES = ("pending", "error", "syncing")

# Allowed status values for the child rows written through the caller-facing
# setters. These guard against arbitrary strings BEFORE any DB write (matching
# the CHECK constraints declared on the ORM models).
_ALLOWED_STORAGE_PATH_STATUSES = frozenset({"pending", "removed", "error"})
_ALLOWED_LOCAL_ARTIFACT_STATUSES = frozenset({"pending", "done", "error"})


def _utcnow() -> datetime:
    """Return current UTC time as a timezone-naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_entry(model: DeletionOutboxModel) -> DeletionOutboxEntry:
    """Map a DeletionOutboxModel to a DeletionOutboxEntry DTO."""
    return DeletionOutboxEntry(
        id=model.id,
        entity_type=model.entity_type,
        entity_local_id=model.entity_local_id,
        remote_table=model.remote_table,
        remote_id=model.remote_id,
        created_at=model.created_at,
        status=model.status,
        local_delete_status=model.local_delete_status,
        cleanup_status=model.cleanup_status,
        deleted_at=model.deleted_at,
        last_error=model.last_error,
        retry_count=model.retry_count,
    )


class DeletionOutboxRepository:
    """Infrastructure adapter implementing DeletionOutboxPort via SQLAlchemy.

    Uses a session_factory callable to create short-lived sessions. Each
    mutation commits immediately for durable checkpoints.
    """

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        """Initialize with a callable that produces new SQLAlchemy Sessions.

        Args:
            session_factory: Callable returning a fresh Session instance.
                             Compatible with DatabaseManager.get_session.
        """
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # Enqueue (TX1) + reads
    # ------------------------------------------------------------------

    def enqueue(self, entry: DeletionOutboxEntryInput) -> DeletionOutboxEntry:
        """Create and durably commit a new outbox entry (TX1).

        Creates the outbox entry together with its child Storage-path rows and
        local-artifact rows, initialized with local_delete_status='prepared',
        status='pending' and deleted_at=None. The write is committed
        synchronously before returning (durability before cascade).

        Idempotency by (entity_type, entity_local_id): if a non-completed entry
        already exists for the same pair, it is returned without creating a
        duplicate so 'prepared'/'failed' entries can be retried safely.
        """
        session = self._session_factory()
        try:
            existing = (
                session.query(DeletionOutboxModel)
                .filter(
                    DeletionOutboxModel.entity_type == entry.entity_type,
                    DeletionOutboxModel.entity_local_id == entry.entity_local_id,
                    DeletionOutboxModel.local_delete_status.in_(
                        _NON_COMPLETED_LOCAL_STATUSES
                    ),
                )
                .order_by(DeletionOutboxModel.created_at.asc())
                .first()
            )
            if existing is not None:
                return _to_entry(existing)

            model = DeletionOutboxModel(
                entity_type=entry.entity_type,
                entity_local_id=entry.entity_local_id,
                remote_table=entry.remote_table,
                remote_id=entry.remote_id,
                status="pending",
                local_delete_status="prepared",
                cleanup_status="pending",
                retry_count=0,
                last_error=None,
                deleted_at=None,
            )
            for sp in entry.storage_paths:
                model.storage_paths.append(
                    DeletionOutboxStoragePathModel(
                        storage_path=sp.storage_path,
                        status="pending",
                    )
                )
            for la in entry.local_artifacts:
                model.local_artifacts.append(
                    DeletionOutboxLocalArtifactModel(
                        relative_path=la.relative_path,
                        status="pending",
                        last_error=None,
                    )
                )

            session.add(model)
            session.commit()
            session.refresh(model)
            return _to_entry(model)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_pending_for_propagation(self) -> List[DeletionOutboxEntry]:
        """Return entries eligible for remote propagation, oldest first.

        Selects entries whose remote status is pending/error/syncing (persisted
        'syncing' is treated as retryable) AND whose local_delete_status is
        'completed'. Ordered by created_at ASC.
        """
        session = self._session_factory()
        try:
            models = (
                session.query(DeletionOutboxModel)
                .filter(
                    DeletionOutboxModel.local_delete_status == "completed",
                    DeletionOutboxModel.status.in_(_RETRYABLE_REMOTE_STATUSES),
                )
                .order_by(DeletionOutboxModel.created_at.asc())
                .all()
            )
            return [_to_entry(m) for m in models]
        finally:
            session.close()

    def get_storage_paths_for_entry(
        self, outbox_id: int
    ) -> List[OutboxStoragePathRow]:
        """Return the Storage-path rows of an outbox entry, ordered by id.

        Reads all Storage-path rows for the entry (any status) so the caller
        can process both 'pending' and 'error' rows. Ordered by id for
        deterministic processing.
        """
        session = self._session_factory()
        try:
            models = (
                session.query(DeletionOutboxStoragePathModel)
                .filter(DeletionOutboxStoragePathModel.outbox_id == outbox_id)
                .order_by(DeletionOutboxStoragePathModel.id.asc())
                .all()
            )
            return [
                OutboxStoragePathRow(
                    id=m.id,
                    storage_path=m.storage_path,
                    status=m.status,
                )
                for m in models
            ]
        finally:
            session.close()

    def get_pending_cleanup_entries(self) -> List[DeletionOutboxEntry]:
        """Return entries eligible for deferred physical cleanup, oldest first.

        Selects entries with local_delete_status='completed' AND
        cleanup_status='pending'. Independent of remote propagation status.
        Ordered by created_at ASC.
        """
        session = self._session_factory()
        try:
            models = (
                session.query(DeletionOutboxModel)
                .filter(
                    DeletionOutboxModel.local_delete_status == "completed",
                    DeletionOutboxModel.cleanup_status == "pending",
                )
                .order_by(DeletionOutboxModel.created_at.asc())
                .all()
            )
            return [_to_entry(m) for m in models]
        finally:
            session.close()

    def get_local_artifacts_for_entry(
        self, outbox_id: int
    ) -> List[OutboxLocalArtifactRow]:
        """Return the local-artifact rows of an outbox entry, ordered by id."""
        session = self._session_factory()
        try:
            models = (
                session.query(DeletionOutboxLocalArtifactModel)
                .filter(DeletionOutboxLocalArtifactModel.outbox_id == outbox_id)
                .order_by(DeletionOutboxLocalArtifactModel.id.asc())
                .all()
            )
            return [
                OutboxLocalArtifactRow(
                    id=m.id,
                    relative_path=m.relative_path,
                    status=m.status,
                    last_error=m.last_error,
                )
                for m in models
            ]
        finally:
            session.close()

    def mark_cleanup_done(self, outbox_id: int) -> None:
        """Set cleanup_status='done' for the given entry."""
        self._set_fields(outbox_id, cleanup_status="done")

    # ------------------------------------------------------------------
    # Local-delete durability transitions
    # ------------------------------------------------------------------

    def mark_local_prepared(self, outbox_id: int) -> None:
        """Set local_delete_status='prepared' for the given entry."""
        self._set_fields(outbox_id, local_delete_status="prepared")

    def mark_local_completed(self, outbox_id: int, deleted_at: datetime) -> None:
        """Set local_delete_status='completed' AND deleted_at (TX2).

        Marks successful LOCAL deletion. Setting deleted_at here is what starts
        the Retention_Window; it is never set during TX1/'prepared'.
        """
        self._set_fields(
            outbox_id,
            local_delete_status="completed",
            deleted_at=deleted_at,
        )

    def mark_local_failed(self, outbox_id: int) -> None:
        """Set local_delete_status='failed' for the given entry."""
        self._set_fields(outbox_id, local_delete_status="failed")

    # ------------------------------------------------------------------
    # Remote propagation transitions
    # ------------------------------------------------------------------

    def mark_syncing(self, outbox_id: int) -> None:
        """Set remote status='syncing' for the given entry."""
        self._set_fields(outbox_id, status="syncing")

    def mark_synced(self, outbox_id: int) -> None:
        """Set remote status='synced' for the given entry."""
        self._set_fields(outbox_id, status="synced")

    def mark_error(self, outbox_id: int, error_message: str) -> None:
        """Set remote status='error' and record the failure detail.

        The last_error is recorded with a UTC timestamp, matching
        record_last_error semantics.
        """
        self._set_fields(
            outbox_id,
            status="error",
            last_error=self._format_error(error_message, _utcnow()),
        )

    def increment_retry_count(self, outbox_id: int) -> None:
        """Increment the retry_count of the given entry by one."""
        session = self._session_factory()
        try:
            model = self._require(session, outbox_id)
            model.retry_count = (model.retry_count or 0) + 1
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def record_last_error(
        self,
        outbox_id: int,
        error_message: str,
        occurred_at: datetime,
    ) -> None:
        """Record the last error with its UTC timestamp."""
        self._set_fields(
            outbox_id,
            last_error=self._format_error(error_message, occurred_at),
        )

    # ------------------------------------------------------------------
    # Child-row transitions
    # ------------------------------------------------------------------

    def mark_storage_path_status(self, storage_path_id: int, status: str) -> None:
        """Set the status of a single Storage-path row.

        Args:
            storage_path_id: Local id of the Storage-path row.
            status: One of ``pending`` | ``removed`` | ``error``.

        Raises:
            ValueError: If ``status`` is not an allowed value, or if the
                Storage-path row does not exist.
        """
        # Validate BEFORE any DB access so no write happens on invalid input.
        if status not in _ALLOWED_STORAGE_PATH_STATUSES:
            raise ValueError(
                f"Invalid storage-path status '{status}'; expected one of "
                f"{sorted(_ALLOWED_STORAGE_PATH_STATUSES)}"
            )
        session = self._session_factory()
        try:
            model = session.get(DeletionOutboxStoragePathModel, storage_path_id)
            if model is None:
                raise ValueError(
                    f"Storage-path row with id={storage_path_id} not found"
                )
            model.status = status
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def mark_local_artifact_status(
        self,
        artifact_id: int,
        status: str,
        last_error: Optional[str] = None,
    ) -> None:
        """Set the status of a single local-artifact row.

        Args:
            artifact_id: Local id of the local-artifact row.
            status: One of ``pending`` | ``done`` | ``error``.
            last_error: Failure detail when status is ``error``; else None.

        Raises:
            ValueError: If ``status`` is not an allowed value, or if the
                local-artifact row does not exist.
        """
        # Validate BEFORE any DB access so no write happens on invalid input.
        if status not in _ALLOWED_LOCAL_ARTIFACT_STATUSES:
            raise ValueError(
                f"Invalid local-artifact status '{status}'; expected one of "
                f"{sorted(_ALLOWED_LOCAL_ARTIFACT_STATUSES)}"
            )
        session = self._session_factory()
        try:
            model = session.get(DeletionOutboxLocalArtifactModel, artifact_id)
            if model is None:
                raise ValueError(
                    f"Local-artifact row with id={artifact_id} not found"
                )
            model.status = status
            model.last_error = last_error
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_error(error_message: str, occurred_at: datetime) -> str:
        """Compose an error string with an ISO UTC timestamp prefix."""
        return f"[{occurred_at.isoformat()}] {error_message}"

    @staticmethod
    def _require(session: Session, outbox_id: int) -> DeletionOutboxModel:
        """Load an outbox entry or raise ValueError if it does not exist."""
        model = session.get(DeletionOutboxModel, outbox_id)
        if model is None:
            raise ValueError(f"Deletion outbox entry with id={outbox_id} not found")
        return model

    def _set_fields(self, outbox_id: int, **fields) -> None:
        """Apply the given column values to an outbox entry and commit.

        Raises:
            ValueError: If the outbox entry does not exist.
        """
        session = self._session_factory()
        try:
            model = self._require(session, outbox_id)
            for name, value in fields.items():
                setattr(model, name, value)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
