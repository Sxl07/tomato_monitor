"""Dashboard service for computing contextual indicators from system state.

Builds dashboard context from pre-fetched data. Contains no database access —
all data must be pre-fetched and passed as arguments.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from src.application.services.alert_service import AlertService


class DashboardService:
    """Builds dashboard context from system data."""

    def build_context(
        self,
        greenhouses: list,
        modules: list,
        monitorings_by_module: dict,
        recent_activities: list | None = None,
        export_packages: list | None = None,
        today: date | None = None,
        alerts: list | None = None,
    ) -> dict:
        """Build dashboard context dictionary from pre-fetched data.

        Args:
            greenhouses: List of Greenhouse entities.
            modules: List of Module entities.
            monitorings_by_module: Mapping of module_id → list of Monitoring entities.
            recent_activities: Pre-enriched activity dicts (with type name, category, etc.).
            export_packages: List of ExportPackage entities.
            today: Override for current date (useful for testing).
            alerts: Existing full operational alert list, when already computed.

        Returns:
            Dictionary with all dashboard indicators.
        """
        if today is None:
            today = date.today()

        # Flatten all monitorings
        all_monitorings = []
        for module_monitorings in monitorings_by_module.values():
            all_monitorings.extend(module_monitorings)

        # Compute alerts. Include greenhouse names so modules with the same
        # name in different greenhouses are unambiguous in alert messages.
        if alerts is None:
            greenhouse_names_by_id = {
                greenhouse.id: greenhouse.name
                for greenhouse in greenhouses
            }
            alert_service = AlertService()
            alerts = alert_service.compute_alerts(
                modules=modules,
                monitorings_by_module=monitorings_by_module,
                export_packages=export_packages,
                today=today,
                greenhouse_names_by_id=greenhouse_names_by_id,
            )

        # Count pending/overdue
        pending_count = sum(
            1 for a in alerts if a.alert_type == "monitoring_pending"
        )
        overdue_count = sum(
            1 for a in alerts if a.alert_type == "monitoring_overdue"
        )

        # Last monitoring (most recent by started_at)
        completed_or_active = [m for m in all_monitorings if m.started_at]
        last_monitoring = (
            max(completed_or_active, key=lambda m: m.started_at)
            if completed_or_active
            else None
        )

        # Monitorings this week (Monday to Sunday containing today)
        week_start = today - timedelta(days=today.weekday())  # Monday
        week_end = week_start + timedelta(days=6)  # Sunday
        monitorings_this_week = sum(
            1
            for m in all_monitorings
            if m.started_at and week_start <= m.started_at.date() <= week_end
        )

        # Total snapshots and detections from all monitorings
        total_snapshots = sum(m.total_snapshots for m in all_monitorings)
        total_detections = sum(m.total_detections for m in all_monitorings)

        # Pending exports
        pending_exports = 0
        if export_packages:
            pending_exports = sum(
                1
                for p in export_packages
                if p.status in ("pending", "generating", "error")
            )

        return {
            "greenhouse_count": len(greenhouses),
            "module_count": len(modules),
            "pending_modules_count": pending_count,
            "overdue_modules_count": overdue_count,
            "last_monitoring": last_monitoring,
            "monitorings_this_week": monitorings_this_week,
            "total_snapshots": total_snapshots,
            "total_detections": total_detections,
            "recent_activities": (recent_activities or [])[:5],
            "pending_exports_count": pending_exports,
            "alert_count": len(alerts),
            "alerts": alerts[:5],
        }
