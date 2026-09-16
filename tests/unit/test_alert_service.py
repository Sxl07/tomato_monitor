"""Unit tests for AlertService — frequency calculation and alert computation."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import pytest

from src.application.services.alert_service import AlertService


# ---------------------------------------------------------------------------
# Lightweight stubs for testing (no DB, no ORM)
# ---------------------------------------------------------------------------


@dataclass
class FakeModule:
    id: int
    name: str
    greenhouse_id: int = 1
    crop_type: str = "Tomate Cherry"
    monitoring_frequency_days: Optional[int] = None


@dataclass
class FakeMonitoring:
    id: int
    status: str
    started_at: Optional[datetime] = None


@dataclass
class FakeExportPackage:
    id: int
    status: str


# ---------------------------------------------------------------------------
# Tests: get_effective_frequency
# ---------------------------------------------------------------------------


class TestGetEffectiveFrequency:
    def setup_method(self):
        self.service = AlertService()

    def test_explicit_frequency(self):
        module = FakeModule(id=1, name="M1", monitoring_frequency_days=14)
        assert self.service.get_effective_frequency(module) == 14

    def test_default_for_tomato_cherry(self):
        module = FakeModule(id=1, name="M1", crop_type="Tomate Cherry")
        assert self.service.get_effective_frequency(module) == 7

    def test_default_for_tomate_de_arbol_returns_none(self):
        """Tomate de Árbol is NOT tomato cherry — no default frequency."""
        module = FakeModule(id=1, name="M1", crop_type="Tomate de Árbol")
        assert self.service.get_effective_frequency(module) is None

    def test_default_for_cherry_tomato_english(self):
        """Cherry tomato (English) also gets default 7."""
        module = FakeModule(id=1, name="M1", crop_type="Cherry Tomato")
        assert self.service.get_effective_frequency(module) == 7

    def test_generic_tomate_returns_none(self):
        """Generic 'Tomate' without 'cherry' does not get default frequency."""
        module = FakeModule(id=1, name="M1", crop_type="Tomate")
        assert self.service.get_effective_frequency(module) is None

    def test_none_for_non_tomato_without_frequency(self):
        module = FakeModule(id=1, name="M1", crop_type="Lechuga")
        assert self.service.get_effective_frequency(module) is None

    def test_explicit_overrides_default(self):
        module = FakeModule(
            id=1, name="M1", crop_type="Tomate Cherry", monitoring_frequency_days=3
        )
        assert self.service.get_effective_frequency(module) == 3


# ---------------------------------------------------------------------------
# Tests: calculate_next_monitoring_due
# ---------------------------------------------------------------------------


class TestCalculateNextMonitoringDue:
    def setup_method(self):
        self.service = AlertService()

    def test_no_valid_monitorings_returns_none(self):
        module = FakeModule(id=1, name="M1", monitoring_frequency_days=7)
        monitorings = [FakeMonitoring(id=1, status="aborted", started_at=datetime(2025, 1, 1))]
        assert self.service.calculate_next_monitoring_due(module, monitorings) is None

    def test_completed_monitoring_sets_due_date(self):
        module = FakeModule(id=1, name="M1", monitoring_frequency_days=7)
        monitorings = [
            FakeMonitoring(id=1, status="completed", started_at=datetime(2025, 6, 10))
        ]
        due = self.service.calculate_next_monitoring_due(module, monitorings)
        assert due == date(2025, 6, 17)

    def test_uses_most_recent_valid_monitoring(self):
        module = FakeModule(id=1, name="M1", monitoring_frequency_days=7)
        monitorings = [
            FakeMonitoring(id=1, status="completed", started_at=datetime(2025, 6, 5)),
            FakeMonitoring(id=2, status="completed", started_at=datetime(2025, 6, 12)),
        ]
        due = self.service.calculate_next_monitoring_due(module, monitorings)
        assert due == date(2025, 6, 19)

    def test_aborted_does_not_count(self):
        module = FakeModule(id=1, name="M1", monitoring_frequency_days=7)
        monitorings = [
            FakeMonitoring(id=1, status="completed", started_at=datetime(2025, 6, 5)),
            FakeMonitoring(id=2, status="aborted", started_at=datetime(2025, 6, 12)),
        ]
        due = self.service.calculate_next_monitoring_due(module, monitorings)
        assert due == date(2025, 6, 12)

    def test_error_does_not_count(self):
        module = FakeModule(id=1, name="M1", monitoring_frequency_days=7)
        monitorings = [
            FakeMonitoring(id=1, status="completed", started_at=datetime(2025, 6, 5)),
            FakeMonitoring(id=2, status="error", started_at=datetime(2025, 6, 12)),
        ]
        due = self.service.calculate_next_monitoring_due(module, monitorings)
        assert due == date(2025, 6, 12)

    def test_no_frequency_returns_none(self):
        module = FakeModule(id=1, name="M1", crop_type="Lechuga")
        monitorings = [
            FakeMonitoring(id=1, status="completed", started_at=datetime(2025, 6, 10))
        ]
        assert self.service.calculate_next_monitoring_due(module, monitorings) is None


# ---------------------------------------------------------------------------
# Tests: compute_alerts — frequency-based
# ---------------------------------------------------------------------------


class TestComputeAlertsFrequency:
    def setup_method(self):
        self.service = AlertService()

    def test_no_monitorings_produces_pending_alert(self):
        module = FakeModule(id=1, name="M1", monitoring_frequency_days=7)
        alerts = self.service.compute_alerts(
            modules=[module],
            monitorings_by_module={1: []},
            today=date(2025, 6, 20),
        )
        assert len(alerts) == 1
        assert alerts[0].alert_type == "monitoring_pending"
        assert alerts[0].severity == "warning"
        assert "M1" in alerts[0].message

    def test_last_completed_3_days_ago_no_alert(self):
        module = FakeModule(id=1, name="M1", monitoring_frequency_days=7)
        monitorings = [
            FakeMonitoring(id=1, status="completed", started_at=datetime(2025, 6, 17))
        ]
        alerts = self.service.compute_alerts(
            modules=[module],
            monitorings_by_module={1: monitorings},
            today=date(2025, 6, 20),
        )
        assert len(alerts) == 0

    def test_due_today_produces_info_alert(self):
        module = FakeModule(id=1, name="M1", monitoring_frequency_days=7)
        monitorings = [
            FakeMonitoring(id=1, status="completed", started_at=datetime(2025, 6, 13))
        ]
        alerts = self.service.compute_alerts(
            modules=[module],
            monitorings_by_module={1: monitorings},
            today=date(2025, 6, 20),
        )
        assert len(alerts) == 1
        assert alerts[0].alert_type == "monitoring_pending"
        assert alerts[0].severity == "info"
        assert "hoy" in alerts[0].message

    def test_overdue_3_days_produces_warning(self):
        module = FakeModule(id=1, name="M1", monitoring_frequency_days=7)
        monitorings = [
            FakeMonitoring(id=1, status="completed", started_at=datetime(2025, 6, 10))
        ]
        alerts = self.service.compute_alerts(
            modules=[module],
            monitorings_by_module={1: monitorings},
            today=date(2025, 6, 20),
        )
        assert len(alerts) == 1
        assert alerts[0].alert_type == "monitoring_overdue"
        assert alerts[0].severity == "warning"
        assert "3 día(s)" in alerts[0].message

    def test_very_overdue_produces_critical(self):
        """When overdue >= frequency days, severity is critical."""
        module = FakeModule(id=1, name="M1", monitoring_frequency_days=7)
        monitorings = [
            FakeMonitoring(id=1, status="completed", started_at=datetime(2025, 6, 1))
        ]
        # today = June 15 → due = June 8 → 7 days overdue (== frequency)
        alerts = self.service.compute_alerts(
            modules=[module],
            monitorings_by_module={1: monitorings},
            today=date(2025, 6, 15),
        )
        assert len(alerts) == 1
        assert alerts[0].alert_type == "monitoring_overdue"
        assert alerts[0].severity == "critical"

    def test_module_without_frequency_no_alerts(self):
        module = FakeModule(id=1, name="M1", crop_type="Lechuga")
        alerts = self.service.compute_alerts(
            modules=[module],
            monitorings_by_module={1: []},
            today=date(2025, 6, 20),
        )
        assert len(alerts) == 0


# ---------------------------------------------------------------------------
# Tests: compute_alerts — error-based
# ---------------------------------------------------------------------------


class TestComputeAlertsError:
    def setup_method(self):
        self.service = AlertService()

    def test_monitoring_with_error_produces_critical_alert(self):
        module = FakeModule(id=1, name="M1", crop_type="Lechuga")
        monitorings = [
            FakeMonitoring(id=10, status="error", started_at=datetime(2025, 6, 15))
        ]
        alerts = self.service.compute_alerts(
            modules=[module],
            monitorings_by_module={1: monitorings},
            today=date(2025, 6, 20),
        )
        assert len(alerts) == 1
        assert alerts[0].alert_type == "analysis_error"
        assert alerts[0].severity == "critical"
        assert alerts[0].monitoring_id == 10

    def test_multiple_errors_produce_multiple_alerts(self):
        module = FakeModule(id=1, name="M1", crop_type="Lechuga")
        monitorings = [
            FakeMonitoring(id=10, status="error", started_at=datetime(2025, 6, 14)),
            FakeMonitoring(id=11, status="error", started_at=datetime(2025, 6, 15)),
        ]
        alerts = self.service.compute_alerts(
            modules=[module],
            monitorings_by_module={1: monitorings},
            today=date(2025, 6, 20),
        )
        assert len(alerts) == 2
        assert all(a.alert_type == "analysis_error" for a in alerts)


# ---------------------------------------------------------------------------
# Tests: greenhouse context in operational alerts
# ---------------------------------------------------------------------------


class TestAlertGreenhouseContext:
    def setup_method(self):
        self.service = AlertService()

    def test_pending_alert_includes_greenhouse_name(self):
        module = FakeModule(
            id=1,
            name="M1",
            greenhouse_id=10,
            monitoring_frequency_days=7,
        )
        alerts = self.service.compute_alerts(
            modules=[module],
            monitorings_by_module={1: []},
            greenhouse_names_by_id={10: "Sofia"},
            today=date(2025, 6, 20),
        )
        assert alerts[0].message == (
            "El módulo 'M1' del invernadero 'Sofia' "
            "no tiene monitoreos registrados."
        )

    def test_overdue_alert_includes_greenhouse_name(self):
        module = FakeModule(
            id=1,
            name="M2",
            greenhouse_id=10,
            monitoring_frequency_days=7,
        )
        monitorings = [
            FakeMonitoring(
                id=1,
                status="completed",
                started_at=datetime(2025, 6, 10),
            )
        ]
        alerts = self.service.compute_alerts(
            modules=[module],
            monitorings_by_module={1: monitorings},
            greenhouse_names_by_id={10: "Sofia"},
            today=date(2025, 6, 20),
        )
        assert alerts[0].message == (
            "El módulo 'M2' del invernadero 'Sofia' "
            "tiene 3 día(s) de retraso."
        )

    def test_due_today_alert_includes_greenhouse_name(self):
        module = FakeModule(
            id=1,
            name="M3",
            greenhouse_id=10,
            monitoring_frequency_days=7,
        )
        monitorings = [
            FakeMonitoring(
                id=1,
                status="completed",
                started_at=datetime(2025, 6, 13),
            )
        ]
        alerts = self.service.compute_alerts(
            modules=[module],
            monitorings_by_module={1: monitorings},
            greenhouse_names_by_id={10: "Sofia"},
            today=date(2025, 6, 20),
        )
        assert alerts[0].message == (
            "El módulo 'M3' del invernadero 'Sofia' "
            "tiene monitoreo programado para hoy."
        )

    def test_analysis_error_alert_includes_greenhouse_name(self):
        module = FakeModule(
            id=1,
            name="M4",
            greenhouse_id=10,
            crop_type="Lechuga",
        )
        monitorings = [
            FakeMonitoring(
                id=12,
                status="error",
                started_at=datetime(2025, 6, 20),
            )
        ]
        alerts = self.service.compute_alerts(
            modules=[module],
            monitorings_by_module={1: monitorings},
            greenhouse_names_by_id={10: "Sofia"},
            today=date(2025, 6, 20),
        )
        assert len(alerts) == 1
        assert alerts[0].message == (
            "El monitoreo del módulo 'M4' del invernadero 'Sofia' "
            "finalizó con error y requiere revisión."
        )

    def test_missing_greenhouse_mapping_keeps_legacy_wording(self):
        module = FakeModule(
            id=1,
            name="M1",
            greenhouse_id=99,
            monitoring_frequency_days=7,
        )
        alerts = self.service.compute_alerts(
            modules=[module],
            monitorings_by_module={1: []},
            greenhouse_names_by_id={10: "Sofia"},
            today=date(2025, 6, 20),
        )
        assert alerts[0].message == (
            "El módulo 'M1' no tiene monitoreos registrados."
        )


# ---------------------------------------------------------------------------
# Tests: compute_alerts — export-based
# ---------------------------------------------------------------------------


class TestComputeAlertsExport:
    def setup_method(self):
        self.service = AlertService()

    def test_export_error_produces_warning(self):
        exports = [FakeExportPackage(id=1, status="error")]
        alerts = self.service.compute_alerts(
            modules=[],
            monitorings_by_module={},
            export_packages=exports,
            today=date(2025, 6, 20),
        )
        assert len(alerts) == 1
        assert alerts[0].alert_type == "export_pending"
        assert alerts[0].severity == "warning"
        assert "error" in alerts[0].title.lower()

    def test_export_pending_produces_info(self):
        exports = [FakeExportPackage(id=1, status="pending")]
        alerts = self.service.compute_alerts(
            modules=[],
            monitorings_by_module={},
            export_packages=exports,
            today=date(2025, 6, 20),
        )
        assert len(alerts) == 1
        assert alerts[0].alert_type == "export_pending"
        assert alerts[0].severity == "info"

    def test_completed_export_no_alerts(self):
        exports = [FakeExportPackage(id=1, status="completed")]
        alerts = self.service.compute_alerts(
            modules=[],
            monitorings_by_module={},
            export_packages=exports,
            today=date(2025, 6, 20),
        )
        assert len(alerts) == 0

    def test_none_export_packages_no_export_alerts(self):
        alerts = self.service.compute_alerts(
            modules=[],
            monitorings_by_module={},
            export_packages=None,
            today=date(2025, 6, 20),
        )
        assert len(alerts) == 0


# ---------------------------------------------------------------------------
# Tests: alert sorting
# ---------------------------------------------------------------------------


class TestAlertSorting:
    def setup_method(self):
        self.service = AlertService()

    def test_alerts_sorted_by_severity_critical_first(self):
        module = FakeModule(id=1, name="M1", monitoring_frequency_days=7)
        monitorings = [
            FakeMonitoring(id=1, status="error", started_at=datetime(2025, 6, 14)),
        ]
        exports = [FakeExportPackage(id=1, status="pending")]
        # Module has no valid monitorings → pending alert (warning)
        # Module has error monitoring → analysis_error (critical)
        # Export pending → info
        alerts = self.service.compute_alerts(
            modules=[module],
            monitorings_by_module={1: monitorings},
            export_packages=exports,
            today=date(2025, 6, 20),
        )
        severities = [a.severity for a in alerts]
        # critical before warning before info
        assert severities == sorted(
            severities,
            key=lambda s: {"critical": 0, "warning": 1, "info": 2}[s],
        )
