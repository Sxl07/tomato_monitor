"""Unit tests for RemoteSyncAdapter interface and LocalZipSyncAdapter."""

import pytest

from src.application.ports.remote_sync_adapter import RemoteSyncAdapter, SyncAdapterResult
from src.application.services.local_zip_sync_adapter import LocalZipSyncAdapter
from src.domain.entities.export_package import ExportPackage


class TestSyncAdapterResult:
    """Tests for the SyncAdapterResult dataclass."""

    def test_success_result(self):
        """SyncAdapterResult with success=True has correct fields."""
        result = SyncAdapterResult(
            success=True,
            provider="local_zip",
            status="exported",
            message="OK",
        )
        assert result.success is True
        assert result.provider == "local_zip"
        assert result.status == "exported"
        assert result.message == "OK"
        assert result.remote_reference is None

    def test_failure_result(self):
        """SyncAdapterResult with success=False has correct fields."""
        result = SyncAdapterResult(
            success=False,
            provider="local_zip",
            status="error",
            message="Failed",
        )
        assert result.success is False
        assert result.status == "error"


class TestRemoteSyncAdapterInterface:
    """Tests that RemoteSyncAdapter is a proper abstract interface."""

    def test_cannot_instantiate_abstract(self):
        """RemoteSyncAdapter cannot be instantiated directly."""
        with pytest.raises(TypeError):
            RemoteSyncAdapter()

    def test_subclass_must_implement_methods(self):
        """A subclass missing implementations raises TypeError."""

        class IncompleteAdapter(RemoteSyncAdapter):
            pass

        with pytest.raises(TypeError):
            IncompleteAdapter()

    def test_complete_subclass_works(self):
        """A subclass implementing all methods can be instantiated."""

        class StubAdapter(RemoteSyncAdapter):
            def provider_name(self) -> str:
                return "stub"

            def sync_export_package(self, export_package):
                return SyncAdapterResult(
                    success=True, provider="stub", status="synced"
                )

        adapter = StubAdapter()
        assert adapter.provider_name() == "stub"


class TestLocalZipSyncAdapter:
    """Tests for the LocalZipSyncAdapter concrete implementation."""

    def test_provider_name(self):
        """LocalZipSyncAdapter reports 'local_zip' as provider."""
        adapter = LocalZipSyncAdapter()
        assert adapter.provider_name() == "local_zip"

    def test_is_instance_of_abstract(self):
        """LocalZipSyncAdapter is a proper subclass of RemoteSyncAdapter."""
        adapter = LocalZipSyncAdapter()
        assert isinstance(adapter, RemoteSyncAdapter)

    def test_sync_completed_package_succeeds(self):
        """Syncing a completed export package returns success."""
        adapter = LocalZipSyncAdapter()
        package = ExportPackage(
            created_by_user_id=1,
            scope="full",
            status="completed",
            id=1,
        )
        result = adapter.sync_export_package(package)
        assert result.success is True
        assert result.status == "exported"
        assert result.provider == "local_zip"

    def test_sync_pending_package_fails(self):
        """Syncing a non-completed package returns failure."""
        adapter = LocalZipSyncAdapter()
        package = ExportPackage(
            created_by_user_id=1,
            scope="full",
            status="pending",
            id=1,
        )
        result = adapter.sync_export_package(package)
        assert result.success is False
        assert result.status == "error"

    def test_sync_generating_package_fails(self):
        """Syncing a generating package returns failure."""
        adapter = LocalZipSyncAdapter()
        package = ExportPackage(
            created_by_user_id=1,
            scope="full",
            status="generating",
            id=1,
        )
        result = adapter.sync_export_package(package)
        assert result.success is False
        assert result.status == "error"

    def test_no_cloud_references(self):
        """LocalZipSyncAdapter source code has no cloud provider references."""
        import inspect
        source = inspect.getsource(LocalZipSyncAdapter)
        cloud_terms = ["google", "drive", "oauth", "s3", "supabase", "dropbox", "http"]
        for term in cloud_terms:
            assert term.lower() not in source.lower(), (
                f"Found cloud term '{term}' in LocalZipSyncAdapter source"
            )
