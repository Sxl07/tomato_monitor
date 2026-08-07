"""Sync service for computing sync status and triggering manual local sync.

Pure computation and coordination — no FastAPI or SQLAlchemy imports.
Delegates to repository methods for persistence updates.
"""

from src.domain.entities.monitoring import Monitoring
from src.domain.entities.activity_log import ActivityLog
from src.application.services.export_service import ExportResult


class SyncService:
    """Service for sync status computation and manual local sync coordination."""

    def compute_sync_status(
        self, monitorings: list[Monitoring], activity_logs: list[ActivityLog]
    ) -> dict:
        """Compute counts by sync_status for monitorings and activities.

        Returns a dict with counts for pending, exported, and synced
        records across both monitorings and activity logs.
        """
        m_pending = sum(1 for m in monitorings if m.sync_status == "pending")
        m_exported = sum(1 for m in monitorings if m.sync_status == "exported")
        m_synced = sum(1 for m in monitorings if m.sync_status == "synced")

        a_pending = sum(1 for a in activity_logs if a.sync_status == "pending")
        a_exported = sum(1 for a in activity_logs if a.sync_status == "exported")
        a_synced = sum(1 for a in activity_logs if a.sync_status == "synced")

        return {
            "monitorings_pending": m_pending,
            "monitorings_exported": m_exported,
            "monitorings_synced": m_synced,
            "activities_pending": a_pending,
            "activities_exported": a_exported,
            "activities_synced": a_synced,
            "total_pending": m_pending + a_pending,
            "total_exported": m_exported + a_exported,
            "total_synced": m_synced + a_synced,
        }

    def manual_local_sync(
        self,
        export_result: ExportResult,
        monitoring_repo,
        activity_log_repo,
        monitorings: list[Monitoring],
        activity_logs: list[ActivityLog],
    ) -> dict:
        """Mark pending records as exported after a successful local export.

        Args:
            export_result: Result from ExportService.generate_export().
            monitoring_repo: MonitoringRepository with update_sync_status().
            activity_log_repo: ActivityLogRepository with update_sync_status().
            monitorings: All monitorings to check for pending status.
            activity_logs: All activity logs to check for pending status.

        Returns:
            Dict with success flag, message, and counts of updated records.
        """
        if export_result.status != "completed":
            return {
                "success": False,
                "message": "La exportación local falló.",
                "updated_monitorings": 0,
                "updated_activities": 0,
            }

        # Collect pending record IDs
        pending_m_ids = [
            m.id for m in monitorings if m.sync_status == "pending" and m.id
        ]
        pending_a_ids = [
            a.id for a in activity_logs if a.sync_status == "pending" and a.id
        ]

        if pending_m_ids:
            monitoring_repo.update_sync_status(pending_m_ids, "exported")
        if pending_a_ids:
            activity_log_repo.update_sync_status(pending_a_ids, "exported")

        return {
            "success": True,
            "message": "Registros marcados como exportados.",
            "updated_monitorings": len(pending_m_ids),
            "updated_activities": len(pending_a_ids),
        }
