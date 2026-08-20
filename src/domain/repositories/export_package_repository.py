"""Repository interface: ExportPackageRepository.

Defines the abstract contract for export package persistence operations.
Implementations reside in the infrastructure layer.
"""

from abc import ABC, abstractmethod
from typing import Optional

from src.domain.entities.export_package import ExportPackage


class ExportPackageRepository(ABC):
    """Abstract repository for ExportPackage entities."""

    @abstractmethod
    def create(self, export_package: ExportPackage) -> ExportPackage:
        """Persist a new export package. Return it with assigned id."""
        ...

    @abstractmethod
    def get_by_id(self, id: int) -> Optional[ExportPackage]:
        """Return the export package with the given id, or None if not found."""
        ...

    @abstractmethod
    def list_by_user(self, user_id: int) -> list[ExportPackage]:
        """Return all export packages for the given user, newest first."""
        ...

    @abstractmethod
    def update(self, id: int, fields: dict) -> ExportPackage:
        """Update export package fields. Return updated entity."""
        ...

    @abstractmethod
    def list_pending(self) -> list[ExportPackage]:
        """Return all export packages with status 'pending' or 'generating'."""
        ...
