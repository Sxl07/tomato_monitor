"""Repository interface: UserRepository.

Defines the abstract contract for user persistence operations.
Implementations reside in the infrastructure layer.
"""

from abc import ABC, abstractmethod
from typing import Optional

from src.domain.entities.user import User


class UserRepository(ABC):
    """Abstract repository for User entities."""

    @abstractmethod
    def create(self, user: User) -> User:
        """Persist a new user. Return it with assigned id."""
        ...

    @abstractmethod
    def get_by_id(self, id: int) -> Optional[User]:
        """Return the user with the given id, or None if not found."""
        ...

    @abstractmethod
    def get_by_email(self, email: str) -> Optional[User]:
        """Return the user with the given email, or None if not found."""
        ...

    @abstractmethod
    def update(self, id: int, fields: dict) -> User:
        """Update user fields. Return updated entity."""
        ...

    @abstractmethod
    def list_active(self) -> list[User]:
        """Return all active users."""
        ...
