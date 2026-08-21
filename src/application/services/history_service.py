"""History service for building combined module timeline.

Merges monitorings and activities into a unified timeline ordered by date.
Pure computation — no database access, no framework imports.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from src.application.utils.timezone import format_bogota


_MONITORING_BADGES = {
    "completed": ("Completado", "badge-success"),
    "analyzing": ("Analizando", "badge-info"),
    "running": ("En ejecución", "badge-info"),
    "paused": ("Pausado", "badge-warning"),
    "finishing": ("Finalizando", "badge-info"),
    "aborted": ("Cancelado", "badge-warning"),
    "error": ("Error", "badge-danger"),
    "initializing": ("Iniciando", "badge-info"),
}

_ACTIVITY_ICONS = {
    "riego": "watering",
    "fertilizacion": "fertilization",
    "fitosanitario": "phytosanitary",
    "cosecha": "harvest",
    "poda": "pruning",
    "deshoje": "defoliation",
    "tutorado": "trellising",
    "limpieza": "cleaning",
    "inspeccion_visual": "inspection",
    "monitoreo_plagas": "pest_monitoring",
    "monitoreo_enfermedades": "pest_monitoring",
    "observacion_general": "observation",
}


class HistoryService:
    """Builds a combined history timeline from monitorings and activities."""

    def build_combined_history(
        self,
        monitorings: list,
        metrics_by_monitoring: Optional[dict] = None,
        activity_logs: Optional[list] = None,
        activity_types: Optional[list] = None,
    ) -> list[dict]:
        """Merge monitorings and activities into a unified timeline.

        Args:
            monitorings: List of Monitoring entities.
            metrics_by_monitoring: Dict mapping monitoring_id → MonitoringMetrics.
            activity_logs: List of ActivityLog entities.
            activity_types: List of ActivityType entities (for name/category lookup).

        Returns:
            List of timeline item dicts, sorted by occurred_at descending.
        """
        if metrics_by_monitoring is None:
            metrics_by_monitoring = {}
        if activity_logs is None:
            activity_logs = []
        if activity_types is None:
            activity_types = []

        types_map = {t.id: t for t in activity_types}
        items: list[dict] = []

        for m in monitorings:
            # Skip initializing (brief transient state)
            if m.status == "initializing":
                continue
            metrics = metrics_by_monitoring.get(m.id)
            items.append(self._build_monitoring_item(m, metrics))

        for log in activity_logs:
            at = types_map.get(log.activity_type_id)
            items.append(self._build_activity_item(log, at))

        # Sort by occurred_at descending, None at end
        items.sort(key=lambda x: x["occurred_at"] or datetime.min, reverse=True)
        return items

    def _build_monitoring_item(self, monitoring, metrics) -> dict:
        """Build a timeline item dict for a monitoring session."""
        badge_label, badge_class = _MONITORING_BADGES.get(
            monitoring.status, ("", "")
        )

        # Subtitle from metrics or fallback
        if metrics and hasattr(metrics, "total_tomatoes"):
            subtitle = (
                f"{metrics.total_tomatoes} tomates — "
                f"{metrics.pct_healthy:.0f}% sanos"
            )
        elif monitoring.total_detections > 0:
            subtitle = f"{monitoring.total_detections} tomates detectados"
        else:
            subtitle = f"{monitoring.total_snapshots} snapshots capturados"

        # URL and action label based on status
        if monitoring.status == "completed":
            url = f"/monitoreos/{monitoring.id}/reporte"
            action_label = "Ver reporte"
        elif monitoring.status in ("running", "paused", "finishing", "analyzing"):
            url = f"/monitoreos/{monitoring.id}/ejecucion"
            action_label = "Ver ejecución"
        else:
            url = f"/monitoreos/{monitoring.id}/reporte"
            action_label = "Ver detalle"

        return {
            "item_type": "monitoring",
            "id": monitoring.id,
            "occurred_at": monitoring.started_at,
            "date_display": format_bogota(monitoring.started_at, "%d/%m/%Y"),
            "time_display": format_bogota(monitoring.started_at, "%H:%M"),
            "title": "Monitoreo visual",
            "subtitle": subtitle,
            "status": monitoring.status,
            "badge_label": badge_label,
            "badge_class": badge_class,
            "icon_key": "monitoring",
            "url": url,
            "action_label": action_label,
        }

    def _build_activity_item(self, log, activity_type) -> dict:
        """Build a timeline item dict for an activity log entry."""
        name = activity_type.name if activity_type else "Actividad agrícola"
        category = activity_type.category if activity_type else ""
        code = activity_type.code if activity_type else ""
        icon_key = _ACTIVITY_ICONS.get(code, "note")

        # Build subtitle from available fields
        parts: list[str] = []
        if log.product_name:
            parts.append(f"Producto: {log.product_name}")
        if log.quantity is not None:
            parts.append(f"{log.quantity} {log.unit or ''}")
        if log.notes:
            parts.append(log.notes[:80])
        subtitle = " — ".join(parts) if parts else category

        return {
            "item_type": "activity",
            "id": log.id,
            "occurred_at": log.occurred_at,
            "date_display": format_bogota(log.occurred_at, "%d/%m/%Y"),
            "time_display": format_bogota(log.occurred_at, "%H:%M"),
            "title": name,
            "subtitle": subtitle,
            "status": None,
            "badge_label": category or "Actividad",
            "badge_class": "badge-info",
            "icon_key": icon_key,
            "url": None,
            "action_label": None,
        }
