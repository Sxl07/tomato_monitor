"""Repository interface: ActivityLogRepository.

Defines the abstract contract for activity log persistence operations.
Implementations reside in the infrastructure layer.
"""

from abc import ABC, abstractmethod
from typing import Optional

from src.domain.entities.activity_log import ActivityLog


class ActivityLogRepository(ABC):
    """Abstract repository for ActivityLog entities."""

    @abstractmethod
    def create(self, activity_log: ActivityLog) -> ActivityLog:
        """Persist a new activity log entry. Return it with assigned id."""
        ...

    @abstractmethod
    def get_by_id(self, id: int) -> Optional[ActivityLog]:
        """Return the activity log with the given id, or None if not found."""
        ...

    @abstractmethod
    def list_by_module(self, module_id: int) -> list[ActivityLog]:
        """Return all activity logs for the given module, newest first."""
        ...

    @abstractmethod
    def list_recent(self, limit: int = 10) -> list[ActivityLog]:
        """Return the most recent activity logs across all modules."""
        ...

    @abstractmethod
    def list_by_user(self, user_id: int) -> list[ActivityLog]:
        """Return all activity logs for the given user, newest first."""
        ...

    @abstractmethod
    def list_all(self) -> list[ActivityLog]:
        """Return all activity logs, ordered by occurred_at descending."""
        ...

    @abstractmethod
    def update_sync_status(self, ids: list[int], status: str) -> None:
        """Update sync_status for the given activity log ids."""
        ...

    # --- Recovery primitives (Spec 022, block D1) ---

    @abstractmethod
    def find_by_remote_id(self, remote_id: str) -> Optional[ActivityLog]:
        """Return the activity log mapped to the given remote_id, or None.

        The remote UUID is stored in the local ``remote_id`` column; the local
        primary key remains an integer autoincrement value.
        """
        ...

    @abstractmethod
    def insert_preserving_remote_id(
        self, entity: ActivityLog, remote_id: str
    ) -> ActivityLog:
        """Insert a recovered activity log, mapping remote_id -> local remote_id.

        Import-missing-only: builds a NEW local row with a fresh autoincrement
        id (the entity carries module_id, activity_type_id, user_id and the
        business fields), sets remote sync metadata to a synced state, and never
        modifies an existing local row.

        Raises:
            RecoveredEntityAlreadyExistsError: if a local activity log already
                exists with the given remote_id.
        """
        ...
