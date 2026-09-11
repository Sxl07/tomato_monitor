"""Port: SyncStatePort.

Defines the abstract contract for managing synchronization state
of local entities. Concrete implementations (e.g., SyncStateRepository
backed by SQLAlchemy) reside in the infrastructure layer.

The port is independent of the agricultural domain repositories and
does not import SQLAlchemy or any persistence library.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol


@dataclass
class StoragePaths:
    """Remote storage paths for a snapshot's image files.

    Attributes:
        raw_storage_path: Remote path of the raw snapshot image, or None.
        annotated_storage_path: Remote path of the annotated image, or None.
    """

    raw_storage_path: Optional[str] = None
    annotated_storage_path: Optional[str] = None


class SyncStatePort(Protocol):
    """Abstract port for synchronization state management.

    Implementations track which local entities have been synced remotely,
    their remote IDs, and their current sync status. This port is
    independent of the agricultural domain repositories.

    Contract:
        - get_pending_entities returns entities that need synchronization.
        - reserve_remote_id stores a remote UUID before the actual remote write.
        - State transitions: pending → syncing → synced | error.
        - SQLite PK integers are preserved; remote UUIDs are stored alongside.
    """

    def get_pending_entities(
        self,
        entity_type: str,
    ) -> list[dict]:
        """Return local entities pending synchronization.

        Conceptually includes entities with status: pending, error,
        or stale syncing entries. Filtering logic is implementation-defined.

        Args:
            entity_type: Type of entity (e.g., "greenhouse", "module",
                "monitoring", "snapshot", "monitoring_metrics",
                "inspection_result", "activity_log").

        Returns:
            List of dictionaries with entity data relevant for sync.
        """
        ...

    def get_remote_id(
        self,
        entity_type: str,
        local_id: int,
    ) -> Optional[str]:
        """Return the remote UUID for a local entity, or None if not synced.

        Args:
            entity_type: Type of entity.
            local_id: Local integer primary key.

        Returns:
            Remote UUID string if previously synced/reserved, None otherwise.
        """
        ...

    def reserve_remote_id(
        self,
        entity_type: str,
        local_id: int,
        remote_id: str,
    ) -> None:
        """Store a pre-generated remote UUID for a local entity.

        Called before the actual remote write to ensure the UUID is
        available for foreign key resolution during sync.

        Args:
            entity_type: Type of entity.
            local_id: Local integer primary key.
            remote_id: Pre-generated remote UUID to reserve.
        """
        ...

    def mark_syncing(
        self,
        entity_type: str,
        local_id: int,
    ) -> None:
        """Mark a local entity as currently being synchronized.

        Args:
            entity_type: Type of entity.
            local_id: Local integer primary key.
        """
        ...

    def mark_synced(
        self,
        entity_type: str,
        local_id: int,
        remote_id: str,
    ) -> None:
        """Mark a local entity as successfully synchronized.

        Args:
            entity_type: Type of entity.
            local_id: Local integer primary key.
            remote_id: Confirmed remote UUID after successful write.
        """
        ...

    def mark_error(
        self,
        entity_type: str,
        local_id: int,
        error_msg: str,
    ) -> None:
        """Mark a local entity sync as failed with an error message.

        Args:
            entity_type: Type of entity.
            local_id: Local integer primary key.
            error_msg: Human-readable error description.
        """
        ...

    def get_user_remote_id(
        self,
        local_user_id: int,
    ) -> Optional[str]:
        """Return the remote_user_id (UUID / auth.uid()) for a local user.

        Used to map a greenhouse's local owner (``owner_user_id`` -> users.id)
        to the owner's remote identity for the sync payload. Returns None when
        the user does not exist or has no ``remote_user_id`` assigned.

        Args:
            local_user_id: Local integer primary key of the owning user.

        Returns:
            The user's remote UUID string, or None.
        """
        ...

    def get_effective_owner_local_user_id(
        self,
        entity_type: str,
        local_id: int,
    ) -> Optional[int]:
        """Return the effective owner's LOCAL user id for an entity.

        Resolves ownership by walking the FK chain up to the root Greenhouse
        and returning its ``owner_user_id`` (the local ``users.id``), WITHOUT
        depending on ``users.remote_user_id``:

            greenhouse           -> owner_user_id
            module               -> greenhouse -> owner_user_id
            monitoring           -> module -> greenhouse -> owner_user_id
            monitoring_metrics   -> monitoring -> ... -> owner_user_id
            snapshot             -> monitoring -> ... -> owner_user_id
            inspection_result    -> snapshot -> ... -> owner_user_id
            activity_log         -> module -> ... -> owner_user_id

        Used to scope a sync to the current user BEFORE any remote write, based
        on LOCAL ownership (independent of whether the owner has a
        remote_user_id yet). Entities whose effective local owner differs from
        the current user's local id are skipped (no-touch).

        Returns:
            The owner's local user id, or None when the chain cannot be
            resolved or the root greenhouse has no owner_user_id (legacy NULL).
        """
        ...

    def get_local_user_id_by_remote_id(
        self,
        user_remote_id: str,
    ) -> Optional[int]:
        """Return the local users.id whose remote_user_id matches, or None.

        Used to map the session's ``user_remote_id`` back to the local user id
        so sync scoping can compare LOCAL ownership.
        """
        ...

    def get_storage_paths(
        self,
        snapshot_id: int,
    ) -> StoragePaths:
        """Return the remote storage paths for a snapshot's images.

        Args:
            snapshot_id: Local snapshot integer primary key.

        Returns:
            StoragePaths with the remote paths, or None values if not set.
        """
        ...

    def set_storage_paths(
        self,
        snapshot_id: int,
        raw_path: Optional[str],
        annotated_path: Optional[str],
    ) -> None:
        """Store the remote storage paths for a snapshot's images.

        Args:
            snapshot_id: Local snapshot integer primary key.
            raw_path: Remote path of the raw snapshot image, or None.
            annotated_path: Remote path of the annotated image, or None.
        """
        ...

    def get_sync_status_counts(self) -> "SyncStatusCounts":
        """Return aggregated sync status counts across all syncable entities.

        Counts entities by their remote_sync_status across all supported
        entity types (greenhouse, module, monitoring, monitoring_metrics,
        snapshot, inspection_result, activity_log).

        Returns:
            SyncStatusCounts with pending, synced, error counts and last_sync_at.
        """
        ...


@dataclass
class SyncStatusCounts:
    """Aggregated sync status counts for the GET /api/sync/status endpoint.

    Attributes:
        pending_count: Entities with status pending, error, or syncing (retryable).
        synced_count: Entities with status synced.
        error_count: Entities with status error specifically.
        last_sync_at: Most recent last_synced_at timestamp across all entities,
            or None if nothing has been synced.
    """

    pending_count: int = 0
    synced_count: int = 0
    error_count: int = 0
    last_sync_at: Optional[datetime] = None
