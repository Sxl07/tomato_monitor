"""Local ZIP sync adapter — treats a completed local export as the sync mechanism.

This is the initial concrete implementation of RemoteSyncAdapter.
It does not make HTTP calls or contact remote servers. Instead, it
verifies that the export package is completed and marks the sync as
'exported' (local ZIP generated).
"""

from src.application.ports.remote_sync_adapter import RemoteSyncAdapter, SyncAdapterResult
from src.domain.entities.export_package import ExportPackage


class LocalZipSyncAdapter(RemoteSyncAdapter):
    """Adapter that uses local ZIP export as the synchronization mechanism."""

    def provider_name(self) -> str:
        """Return the provider identifier."""
        return "local_zip"

    def sync_export_package(self, export_package: ExportPackage) -> SyncAdapterResult:
        """Verify the package is completed and return success.

        For local ZIP, the package is already 'synced' by being exported
        to disk. No remote upload occurs.
        """
        if export_package and export_package.status == "completed":
            return SyncAdapterResult(
                success=True,
                provider="local_zip",
                status="exported",
                message="Paquete local generado correctamente.",
            )
        return SyncAdapterResult(
            success=False,
            provider="local_zip",
            status="error",
            message="El paquete no está completado.",
        )
