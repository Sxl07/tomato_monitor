"""Alert service for computing operational alerts from system state.

Alerts are computed dynamically — not persisted. When a condition is no longer
true, the alert simply disappears on the next computation.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from src.domain.entities.operational_alert import OperationalAlert

# Default frequency for tomato cherry if not configured
_DEFAULT_FREQUENCY_TOMATO_CHERRY = 7
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

# Valid monitoring statuses for frequency compliance
_VALID_LAST_MONITORING_STATUSES = {
    "completed",
    "analyzing",
    "running",
    "paused",
    "finishing",
}

# Recognized tomato cherry crop type patterns (case-insensitive)
_TOMATO_CHERRY_PATTERNS = {"tomate cherry", "cherry tomato"}


def _is_tomato_cherry(crop_type: str) -> bool:
    """Check if crop_type represents tomato cherry specifically.

    Matches 'Tomate Cherry', 'tomate cherry', 'Cherry Tomato', etc.
    Does NOT match 'Tomate', 'Tomate de Árbol', or other variants.
    """
    normalized = crop_type.strip().lower()
    return normalized in _TOMATO_CHERRY_PATTERNS


class AlertService:
    """Computes operational alerts from system state."""

    def get_effective_frequency(self, module) -> Optional[int]:
        """Get effective monitoring frequency for a module.

        Returns the configured frequency, or 7 for tomato cherry modules,
        or None if frequency cannot be determined.
        """
        if module.monitoring_frequency_days is not None:
            return module.monitoring_frequency_days
        # Default only for tomato cherry (not other tomato variants)
        if module.crop_type and _is_tomato_cherry(module.crop_type):
            return _DEFAULT_FREQUENCY_TOMATO_CHERRY
        return None

    def calculate_next_monitoring_due(
        self, module, monitorings: list, today: Optional[date] = None
    ) -> Optional[date]:
        """Calculate when next monitoring is due based on frequency and last valid monitoring.

        Returns None if frequency is not set or no valid monitorings exist.
        """
        frequency = self.get_effective_frequency(module)
        if frequency is None:
            return None

        # Find last valid monitoring
        valid = [
            m
            for m in monitorings
            if m.status in _VALID_LAST_MONITORING_STATUSES and m.started_at
        ]
        if not valid:
            return None  # Never monitored — pending immediately

        last = max(valid, key=lambda m: m.started_at)
        last_date = (
            last.started_at.date()
            if isinstance(last.started_at, datetime)
            else last.started_at
        )
        return last_date + timedelta(days=frequency)

    def compute_alerts(
        self,
        modules: list,
        monitorings_by_module: dict,
        export_packages: Optional[list] = None,
        today: Optional[date] = None,
    ) -> list[OperationalAlert]:
        """Compute all operational alerts from current system state.

        Args:
            modules: List of Module entities to check.
            monitorings_by_module: Mapping of module_id → list of monitorings.
            export_packages: Optional list of ExportPackage entities.
            today: Override for current date (useful for testing).

        Returns:
            List of OperationalAlert sorted by severity (critical first).
        """
        if today is None:
            today = date.today()

        alerts: list[OperationalAlert] = []

        for module in modules:
            monitorings = monitorings_by_module.get(module.id, [])
            alerts.extend(self._check_frequency_alerts(module, monitorings, today))
            alerts.extend(self._check_error_alerts(module, monitorings))

        if export_packages is not None:
            alerts.extend(self._check_export_alerts(export_packages))

        # Sort: critical first, then warning, then info
        alerts.sort(key=lambda a: _SEVERITY_ORDER.get(a.severity, 99))
        return alerts

    def _check_frequency_alerts(
        self, module, monitorings, today: date
    ) -> list[OperationalAlert]:
        """Check if a module is overdue or pending monitoring."""
        frequency = self.get_effective_frequency(module)
        if frequency is None:
            return []

        valid = [
            m
            for m in monitorings
            if m.status in _VALID_LAST_MONITORING_STATUSES and m.started_at
        ]
        if not valid:
            # Never monitored
            return [
                OperationalAlert(
                    alert_type="monitoring_pending",
                    severity="warning",
                    title="Monitoreo pendiente",
                    message=f"El módulo '{module.name}' no tiene monitoreos registrados.",
                    module_id=module.id,
                )
            ]

        next_due = self.calculate_next_monitoring_due(module, monitorings, today)
        if next_due is None:
            return []

        days_overdue = (today - next_due).days
        if days_overdue > 0:
            severity = "critical" if days_overdue >= frequency else "warning"
            return [
                OperationalAlert(
                    alert_type="monitoring_overdue",
                    severity=severity,
                    title="Monitoreo vencido",
                    message=(
                        f"El módulo '{module.name}' tiene "
                        f"{days_overdue} día(s) de retraso."
                    ),
                    module_id=module.id,
                )
            ]
        elif days_overdue == 0:
            return [
                OperationalAlert(
                    alert_type="monitoring_pending",
                    severity="info",
                    title="Monitoreo programado hoy",
                    message=(
                        f"El módulo '{module.name}' tiene monitoreo "
                        f"programado para hoy."
                    ),
                    module_id=module.id,
                )
            ]
        return []

    def _check_error_alerts(self, module, monitorings) -> list[OperationalAlert]:
        """Check for monitorings that ended with error status."""
        errors = [m for m in monitorings if m.status == "error"]
        alerts = []
        for m in errors:
            alerts.append(
                OperationalAlert(
                    alert_type="analysis_error",
                    severity="critical",
                    title="Error en monitoreo",
                    message=(
                        f"El monitoreo del módulo '{module.name}' finalizó "
                        f"con error y requiere revisión."
                    ),
                    module_id=module.id,
                    monitoring_id=m.id,
                )
            )
        return alerts

    def _check_export_alerts(self, export_packages) -> list[OperationalAlert]:
        """Check for pending or errored export packages."""
        pending = [
            p
            for p in export_packages
            if p.status in ("pending", "generating", "error")
        ]
        if not pending:
            return []
        error_count = sum(1 for p in pending if p.status == "error")
        other_count = len(pending) - error_count
        alerts = []
        if error_count > 0:
            alerts.append(
                OperationalAlert(
                    alert_type="export_pending",
                    severity="warning",
                    title="Exportación con errores",
                    message=(
                        f"Hay {error_count} exportación(es) con error "
                        f"que requieren reintento."
                    ),
                )
            )
        if other_count > 0:
            alerts.append(
                OperationalAlert(
                    alert_type="export_pending",
                    severity="info",
                    title="Exportaciones pendientes",
                    message=f"Hay {other_count} exportación(es) en proceso.",
                )
            )
        return alerts
