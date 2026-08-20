"""Repository interface: ActivityTypeRepository.

Defines the abstract contract for activity type persistence operations.
Implementations reside in the infrastructure layer.
"""

from abc import ABC, abstractmethod
from typing import Optional

from src.domain.entities.activity_type import ActivityType


class ActivityTypeRepository(ABC):
    """Abstract repository for ActivityType entities."""

    @abstractmethod
    def create(self, activity_type: ActivityType) -> ActivityType:
        """Persist a new activity type. Return it with assigned id."""
        ...

    @abstractmethod
    def get_by_id(self, id: int) -> Optional[ActivityType]:
        """Return the activity type with the given id, or None if not found."""
        ...

    @abstractmethod
    def get_by_code(self, code: str) -> Optional[ActivityType]:
        """Return the activity type with the given code, or None if not found."""
        ...

    @abstractmethod
    def list_active(self) -> list[ActivityType]:
        """Return all active activity types."""
        ...

    @abstractmethod
    def list_all(self) -> list[ActivityType]:
        """Return all activity types regardless of active status."""
        ...
