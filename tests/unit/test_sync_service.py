"""Unit tests for SyncService — compute_sync_status and manual_local_sync."""

import pytest
from unittest.mock import MagicMock

from src.application.services.sync_service import SyncService
from src.application.services.export_service import ExportResult
from src.domain.entities.monitoring import Monitoring
from src.domain.entities.activity_log import ActivityLog


def _make_monitoring(id: int, sync_status: str = "pending") -> Monitoring:
    """Create a test Monitoring entity with the given sync_status."""
    return Monitoring(
        id=id,
        module_id=1,
        width_m=2.0,
        length_m=5.0,
        sync_status=sync_status,
    )


def _make_activity_log(id: int, sync_status: str = "pending") -> ActivityLog:
    """Create a test ActivityLog entity with the given sync_status."""
    return ActivityLog(
        id=id,
        module_id=1,
        activity_type_id=1,
        user_id=1,
        sync_status=sync_status,
    )


class TestComputeSyncStatus:
    """Tests for SyncService.compute_sync_status."""

    def test_empty_lists(self):
        """Empty monitorings and activities return all zeros."""
        svc = SyncService()
        result = svc.compute_sync_status([], [])
        assert result["total_pending"] == 0
        assert result["total_exported"] == 0
        assert result["total_synced"] == 0

    def test_counts_pending(self):
        """Counts pending monitorings and activities correctly."""
        svc = SyncService()
        monitorings = [_make_monitoring(1, "pending"), _make_monitoring(2, "pending")]
        activities = [_make_activity_log(1, "pending")]
        result = svc.compute_sync_status(monitorings, activities)
        assert result["monitorings_pending"] == 2
        assert result["activities_pending"] == 1
        assert result["total_pending"] == 3

    def test_counts_exported(self):
        """Counts exported records correctly."""
        svc = SyncService()
        monitorings = [_make_monitoring(1, "exported")]
        activities = [_make_activity_log(1, "exported"), _make_activity_log(2, "exported")]
        result = svc.compute_sync_status(monitorings, activities)
        assert result["monitorings_exported"] == 1
        assert result["activities_exported"] == 2
        assert result["total_exported"] == 3

    def test_counts_synced(self):
        """Counts synced records correctly."""
        svc = SyncService()
        monitorings = [_make_monitoring(1, "synced")]
        activities = [_make_activity_log(1, "synced")]
        result = svc.compute_sync_status(monitorings, activities)
        assert result["monitorings_synced"] == 1
        assert result["activities_synced"] == 1
        assert result["total_synced"] == 2

    def test_mixed_statuses(self):
        """Mixed statuses are counted correctly per category."""
        svc = SyncService()
        monitorings = [
            _make_monitoring(1, "pending"),
            _make_monitoring(2, "exported"),
            _make_monitoring(3, "synced"),
        ]
        activities = [
            _make_activity_log(1, "pending"),
            _make_activity_log(2, "synced"),
        ]
        result = svc.compute_sync_status(monitorings, activities)
        assert result["monitorings_pending"] == 1
        assert result["monitorings_exported"] == 1
        assert result["monitorings_synced"] == 1
        assert result["activities_pending"] == 1
        assert result["activities_synced"] == 1
        assert result["total_pending"] == 2
        assert result["total_exported"] == 1
        assert result["total_synced"] == 2


class TestManualLocalSync:
    """Tests for SyncService.manual_local_sync."""

    def _make_export_result(self, status: str = "completed") -> ExportResult:
        """Create a test ExportResult."""
        return ExportResult(
            file_path="/tmp/test.zip",
            file_size_bytes=1024,
            records_count=5,
            images_count=2,
            files_included=7,
            files_missing=[],
            manifest={},
            status=status,
        )

    def test_failed_export_returns_failure(self):
        """If export status is not 'completed', returns failure."""
        svc = SyncService()
        export_result = self._make_export_result("error")
        mock_m_repo = MagicMock()
        mock_a_repo = MagicMock()

        result = svc.manual_local_sync(
            export_result, mock_m_repo, mock_a_repo, [], []
        )
        assert result["success"] is False
        assert result["updated_monitorings"] == 0
        assert result["updated_activities"] == 0
        mock_m_repo.update_sync_status.assert_not_called()
        mock_a_repo.update_sync_status.assert_not_called()

    def test_marks_pending_as_exported(self):
        """Successful export marks pending records as exported."""
        svc = SyncService()
        export_result = self._make_export_result("completed")
        mock_m_repo = MagicMock()
        mock_a_repo = MagicMock()

        monitorings = [_make_monitoring(1, "pending"), _make_monitoring(2, "exported")]
        activities = [_make_activity_log(1, "pending"), _make_activity_log(2, "pending")]

        result = svc.manual_local_sync(
            export_result, mock_m_repo, mock_a_repo, monitorings, activities
        )
        assert result["success"] is True
        assert result["updated_monitorings"] == 1
        assert result["updated_activities"] == 2
        mock_m_repo.update_sync_status.assert_called_once_with([1], "exported")
        mock_a_repo.update_sync_status.assert_called_once_with([1, 2], "exported")

    def test_no_pending_records(self):
        """If no records are pending, no updates are made."""
        svc = SyncService()
        export_result = self._make_export_result("completed")
        mock_m_repo = MagicMock()
        mock_a_repo = MagicMock()

        monitorings = [_make_monitoring(1, "exported")]
        activities = [_make_activity_log(1, "synced")]

        result = svc.manual_local_sync(
            export_result, mock_m_repo, mock_a_repo, monitorings, activities
        )
        assert result["success"] is True
        assert result["updated_monitorings"] == 0
        assert result["updated_activities"] == 0
        mock_m_repo.update_sync_status.assert_not_called()
        mock_a_repo.update_sync_status.assert_not_called()

    def test_only_monitorings_pending(self):
        """Only pending monitorings are marked when no activities are pending."""
        svc = SyncService()
        export_result = self._make_export_result("completed")
        mock_m_repo = MagicMock()
        mock_a_repo = MagicMock()

        monitorings = [_make_monitoring(1, "pending"), _make_monitoring(2, "pending")]
        activities = [_make_activity_log(1, "exported")]

        result = svc.manual_local_sync(
            export_result, mock_m_repo, mock_a_repo, monitorings, activities
        )
        assert result["updated_monitorings"] == 2
        assert result["updated_activities"] == 0
        mock_m_repo.update_sync_status.assert_called_once_with([1, 2], "exported")
        mock_a_repo.update_sync_status.assert_not_called()
