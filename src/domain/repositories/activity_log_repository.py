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
