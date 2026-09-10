"""Port: DeletionOutboxPort.

Defines the abstract contract for the durable local deletion outbox.
Concrete implementations (e.g., SQLAlchemy-backed repository with immediate
commit) reside in the infrastructure layer.

The Deletion_Outbox is a durable, local-only mechanism that records pending
deletions so they can be propagated to the remote backend later, in an
eventually consistent way. Local deletion follows a two-transaction protocol:

    TX1 -- create the outbox entry (+ storage paths + local artifacts) with
           local_delete_status='prepared' and COMMIT (durability before cascade).
    TX2 -- execute the Local_Cascade AND set local_delete_status='completed'
           together with deleted_at, within the SAME transaction.

Only entries with local_delete_status='completed' are ever propagated remotely.

Two independent status axes are tracked per entry:
    - status (pending | syncing | synced | error): remote propagation.
    - local_delete_status (prepared | completed | failed): local delete durability.

Idempotency is guaranteed by the pair (entity_type, entity_local_id), so a
'prepared'/'failed' entry can be retried safely without creating a duplicate.

Storage-path rows track remote object cleanup (pending | removed | error, with
'removed' as the only terminal state). Local-artifact rows track deferred
physical cleanup of files under OUTPUTS_DIR (pending | done | error); their
relative_path values are relative to OUTPUTS_DIR (e.g. ``monitorings/{id}``)
and never carry the ``outputs/`` prefix.

The port depends only on stdlib/typing/dataclasses. It performs no network,
filesystem, or ORM work; those concerns belong to the infrastructure adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Protocol, Sequence


@dataclass
class OutboxStoragePathInput:
    """Input describing a single remote Storage object to be removed.

    Attributes:
        storage_path: Provider-relative remote object path
            (e.g. ``monitorings/<uuid>/raw/snapshot_000001.jpg``).
    """

    storage_path: str


@dataclass
class OutboxLocalArtifactInput:
    """Input describing a single local physical artifact to be cleaned up.

    Attributes:
        relative_path: Filesystem path relative to OUTPUTS_DIR, without the
            ``outputs/`` prefix (e.g. ``monitorings/{monitoring_id}``). The
            actual filesystem removal is handled by a later cleanup phase.
    """

    relative_path: str


@dataclass
class DeletionOutboxEntryInput:
    """Input payload for enqueuing a new deletion outbox entry (TX1).

    The entry is created with local_delete_status='prepared'; deleted_at is
    NOT set here (it marks successful local deletion and is set in TX2).

    Attributes:
        entity_type: One of ``greenhouse`` | ``module`` | ``monitoring``.
        entity_local_id: Local id of the deleted root entity.
        remote_table: Target remote table for the root DELETE (e.g. ``monitorings``).
        remote_id: Remote UUID of the root entity; None if never synced.
        owner_user_id: Local id of the user who owns the deleted entity; None
            when ownership is unknown or not applicable.
        storage_paths: Descendant remote Storage object paths to remove.
        local_artifacts: Local physical artifact paths (relative to OUTPUTS_DIR).
    """

    entity_type: str
    entity_local_id: int
    remote_table: str
    remote_id: Optional[str] = None
    owner_user_id: Optional[int] = None
    storage_paths: Sequence[OutboxStoragePathInput] = field(default_factory=list)
    local_artifacts: Sequence[OutboxLocalArtifactInput] = field(default_factory=list)


@dataclass
class OutboxStoragePathRow:
    """A remote Storage-path row of an outbox entry as read back from persistence.

    Storage-path rows track remote object cleanup for a single deletion entry.
    Their status is one of ``pending`` | ``removed`` | ``error``, where
    ``removed`` is the only terminal state. Both ``pending`` and ``error`` rows
    are reprocessed on the next sync.

    Attributes:
        id: Local primary key of the Storage-path row.
        storage_path: Provider-relative remote object path to remove.
        status: One of ``pending`` | ``removed`` | ``error``.
    """

    id: int
    storage_path: str
    status: str


@dataclass
class OutboxLocalArtifactRow:
    """A local-artifact row of an outbox entry as read back from persistence.

    Local-artifact rows track deferred physical cleanup of files under
    OUTPUTS_DIR for a single deletion entry. Their status is one of
    ``pending`` | ``done`` | ``error``; both ``pending`` and ``error`` rows are
    reprocessed on the next cleanup run.

    Attributes:
        id: Local primary key of the local-artifact row.
        relative_path: Filesystem path relative to OUTPUTS_DIR, without the
            ``outputs/`` prefix (e.g. ``monitorings/{monitoring_id}``).
        status: One of ``pending`` | ``done`` | ``error``.
        last_error: Detail of the last cleanup failure, if any.
    """

    id: int
    relative_path: str
    status: str
    last_error: Optional[str] = None


@dataclass
class DeletionOutboxEntry:
    """A durable deletion outbox entry as read back from persistence.

    Attributes:
        id: Local primary key of the outbox entry.
        entity_type: One of ``greenhouse`` | ``module`` | ``monitoring``.
        entity_local_id: Local id of the deleted root entity.
        remote_table: Target remote table for the root DELETE.
        remote_id: Remote UUID of the root entity; None if never synced.
        created_at: UTC timestamp when the entry was enqueued.
        status: Remote propagation status
            (``pending`` | ``syncing`` | ``synced`` | ``error``).
        local_delete_status: Local delete durability
            (``prepared`` | ``completed`` | ``failed``).
        cleanup_status: Physical cleanup status (``pending`` | ``done``).
        deleted_at: UTC timestamp of successful LOCAL deletion. It is set only
            when local_delete_status transitions to ``completed`` (TX2). It does
            NOT start the Retention_Window while the entry is ``prepared``.
        last_error: Description + UTC timestamp of the last failure, if any.
        retry_count: Number of failed remote-propagation attempts.
    """

    id: int
    entity_type: str
    entity_local_id: int
    remote_table: str
    remote_id: Optional[str]
    created_at: datetime
    status: str
    local_delete_status: str
    cleanup_status: str
    deleted_at: Optional[datetime]
    last_error: Optional[str]
    retry_count: int
    owner_user_id: Optional[int] = None


class DeletionOutboxPort(Protocol):
    """Abstract port for durable deletion-outbox operations.

    Implementations MUST persist writes synchronously (commit before returning)
    so entries survive a process restart. All timestamps are UTC.

    Contract highlights:
        - enqueue creates a durable entry with local_delete_status='prepared'
          and COMMITs before the Local_Cascade (commit-before-cascade).
        - Idempotency by (entity_type, entity_local_id): re-enqueuing while a
          non-completed entry exists returns the existing entry instead of
          creating a duplicate.
        - mark_local_completed sets local_delete_status='completed' AND
          deleted_at within TX2 (executed together with the Local_Cascade).
        - Only entries with local_delete_status='completed' are eligible for
          remote propagation.
    """

    def enqueue(self, entry: DeletionOutboxEntryInput) -> DeletionOutboxEntry:
        """Create and durably commit a new outbox entry (TX1).

        Creates the outbox entry together with its child Storage-path rows and
        local-artifact rows, initialized with local_delete_status='prepared',
        status='pending', and no deleted_at yet. The write is committed
        synchronously before returning (durability before cascade).

        Idempotency: if a non-completed entry already exists for the same
        (entity_type, entity_local_id), the existing entry is returned without
        creating a duplicate, so 'prepared'/'failed' entries can be retried
        safely.

        Args:
            entry: The deletion payload (entity identity, remote target,
                descendant Storage paths, and local artifact paths).

        Returns:
            The persisted DeletionOutboxEntry.
        """
        ...

    def find_blocking_by_remote_identity(
        self, entity_type: str, remote_id: str
    ) -> Optional[DeletionOutboxEntry]:
        """Return a BLOCKING tombstone for (entity_type, remote_id), or None.

        Anti-resurrection lookup (Spec 022, D3.1). A tombstone blocks recovery
        when ``entity_type`` and ``remote_id`` match EXACTLY and its remote
        propagation ``status`` is not yet ``synced`` (one of
        ``pending`` | ``syncing`` | ``error``). A ``synced`` tombstone does NOT
        block, and a NULL ``remote_id`` never blocks a valid remote UUID.
        Matching is by remote identity only.

        Read-only: MUST NOT mutate outbox state.
        """
        ...

    def get_pending_for_propagation(
        self, owner_user_id: int
    ) -> List[DeletionOutboxEntry]:
        """Return entries eligible for remote propagation, oldest first.

        User-scoped (Spec 022): only entries whose ``owner_user_id`` equals the
        given ``owner_user_id`` are returned. Legacy entries with a NULL
        ``owner_user_id`` are NEVER returned for any user, so they are never
        propagated, never change status, and never increment retry_count.

        Among the owner's entries, selects those whose remote status is
        ``pending``, ``error``, or a ``syncing`` recovered after a crash/restart
        (persisted ``syncing`` is retriable), AND whose local_delete_status is
        ``completed``. Entries with local_delete_status ``prepared`` or
        ``failed`` are never returned. Ordered by created_at ASC.

        Returns:
            Retriable, locally-completed outbox entries owned by
            ``owner_user_id``, ordered by created_at ASC.
        """
        ...

    def get_storage_paths_for_entry(
        self, outbox_id: int
    ) -> List[OutboxStoragePathRow]:
        """Return the Storage-path rows of an outbox entry, oldest first.

        Reads all Storage-path rows associated with the entry so the remote
        propagation phase can remove each remote object and mark it ``removed``.
        Rows are ordered by id for deterministic processing.

        Args:
            outbox_id: Local id of the parent outbox entry.

        Returns:
            The entry's Storage-path rows (id, storage_path, status), ordered
            by id ascending. Empty when the entry has no Storage paths (e.g.
            never-synced or no snapshots).
        """
        ...

    def get_pending_cleanup_entries(self) -> List[DeletionOutboxEntry]:
        """Return entries eligible for deferred physical cleanup, oldest first.

        Selects entries whose ``local_delete_status`` is ``completed`` AND whose
        ``cleanup_status`` is ``pending`` (i.e. the local delete finished but the
        physical artifacts under OUTPUTS_DIR have not been removed yet). Ordered
        by created_at ASC. Independent of remote propagation (does NOT require
        ``status='synced'``).

        Returns:
            Cleanup-pending, locally-completed outbox entries, oldest first.
        """
        ...

    def get_local_artifacts_for_entry(
        self, outbox_id: int
    ) -> List[OutboxLocalArtifactRow]:
        """Return the local-artifact rows of an outbox entry, oldest first.

        Reads all local-artifact rows for the entry (any status) so the cleanup
        process can (re)process ``pending`` and ``error`` rows. Ordered by id.

        Args:
            outbox_id: Local id of the parent outbox entry.

        Returns:
            The entry's local-artifact rows, ordered by id ascending.
        """
        ...

    def mark_cleanup_done(self, outbox_id: int) -> None:
        """Set cleanup_status='done' for the given entry.

        Should only be applied once ALL of the entry's local-artifact rows have
        reached ``done``.

        Args:
            outbox_id: Local id of the outbox entry.
        """
        ...

    def mark_local_prepared(self, outbox_id: int) -> None:
        """Set local_delete_status='prepared' for the given entry.

        Args:
            outbox_id: Local id of the outbox entry.
        """
        ...

    def mark_local_completed(self, outbox_id: int, deleted_at: datetime) -> None:
        """Set local_delete_status='completed' AND deleted_at (TX2).

        This transition marks successful LOCAL deletion. It MUST be applied
        within the same transaction (TX2) as the Local_Cascade. Setting
        deleted_at here is what starts the Retention_Window; it is never set
        during TX1/'prepared'.

        Args:
            outbox_id: Local id of the outbox entry.
            deleted_at: UTC timestamp of the successful local deletion.
        """
        ...

    def mark_local_failed(self, outbox_id: int) -> None:
        """Set local_delete_status='failed' for the given entry.

        Used when the Local_Cascade (TX2) fails and is rolled back; the entry
        remains non-completed and is not propagated remotely.

        Args:
            outbox_id: Local id of the outbox entry.
        """
        ...

    def mark_syncing(self, outbox_id: int) -> None:
        """Set remote status='syncing' for the given entry.

        Args:
            outbox_id: Local id of the outbox entry.
        """
        ...

    def mark_synced(self, outbox_id: int) -> None:
        """Set remote status='synced' for the given entry.

        Should only be applied when the data DELETE and all Storage paths have
        reached ``removed`` (no orphaned storage objects remain).

        Args:
            outbox_id: Local id of the outbox entry.
        """
        ...

    def mark_error(self, outbox_id: int, error_message: str) -> None:
        """Set remote status='error' and record the failure detail.

        Args:
            outbox_id: Local id of the outbox entry.
            error_message: Human-readable failure description (a UTC timestamp
                is recorded alongside it via record_last_error semantics).
        """
        ...

    def mark_storage_path_status(self, storage_path_id: int, status: str) -> None:
        """Set the status of a single Storage-path row.

        Args:
            storage_path_id: Local id of the Storage-path row.
            status: One of ``pending`` | ``removed`` | ``error``
                (``removed`` is the only terminal state).
        """
        ...

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
        """
        ...

    def increment_retry_count(self, outbox_id: int) -> None:
        """Increment the retry_count of the given entry by one.

        Args:
            outbox_id: Local id of the outbox entry.
        """
        ...

    def record_last_error(
        self,
        outbox_id: int,
        error_message: str,
        occurred_at: datetime,
    ) -> None:
        """Record the last error with its UTC timestamp.

        Args:
            outbox_id: Local id of the outbox entry.
            error_message: Human-readable failure description.
            occurred_at: UTC timestamp of the failure.
        """
        ...
