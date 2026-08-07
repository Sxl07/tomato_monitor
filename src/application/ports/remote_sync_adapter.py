"""Abstract interface for remote synchronization providers.

Concrete implementations (future) will handle upload to Google Drive, S3, etc.
For MVP, only LocalZipSyncAdapter is implemented — it treats a completed local
ZIP export as the "sync" mechanism.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from src.domain.entities.export_package import ExportPackage


@dataclass
class SyncAdapterResult:
    """Result of a sync adapter operation."""

    success: bool
    provider: str
    status: str  # "exported", "synced", "error"
    message: Optional[str] = None
    remote_reference: Optional[str] = None


class RemoteSyncAdapter(ABC):
    """Abstract adapter for remote synchronization providers.

    Each concrete implementation represents one sync mechanism
    (local ZIP, Google Drive, S3, etc.). The SyncService delegates
    to whichever adapter is configured.
    """

    @abstractmethod
    def provider_name(self) -> str:
        """Return the unique provider identifier (e.g., 'local_zip')."""
        ...

    @abstractmethod
    def sync_export_package(self, export_package: ExportPackage) -> SyncAdapterResult:
        """Attempt to sync the given export package.

        For local ZIP: verifies the package is completed.
        For remote providers (future): uploads the ZIP and returns a reference.
        """
        ...
