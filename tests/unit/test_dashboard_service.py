"""Unit tests for DashboardService — dashboard indicator computation."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import pytest

from src.application.services.dashboard_service import DashboardService


# ---------------------------------------------------------------------------
# Lightweight stubs (same pattern as test_alert_service.py)
# ---------------------------------------------------------------------------


@dataclass
class FakeGreenhouse:
    id: int
    name: str


@dataclass
class FakeModule:
    id: int
    name: str
    crop_type: str = "Tomate Cherry"
    monitoring_frequency_days: Optional[int] = 7


@dataclass
class FakeMonitoring:
    id: int
    status: str
    started_at: Optional[datetime] = None
    total_snapshots: int = 0
    total_detections: int = 0


@dataclass
class FakeExportPackage:
    id: int
    status: str


# ---------------------------------------------------------------------------
# Tests: build_context — basic counts
# ---------------------------------------------------------------------------


class TestDashboardServiceCounts:
    """Tests for greenhouse/module count indicators."""

    def setup_method(self):
        self.service = DashboardService()

    def test_empty_system(self):
        """Empty system returns all zeros."""
        ctx = self.service.build_context(
            greenhouses=[],
            modules=[],
            monitorings_by_module={},
            today=date(2025, 6, 20),
        )
        assert ctx["greenhouse_count"] == 0
        assert ctx["module_count"] == 0
        assert ctx["pending_modules_count"] == 0
        assert ctx["overdue_modules_count"] == 0
        assert ctx["last_monitoring"] is None
        assert ctx["monitorings_this_week"] == 0
        assert ctx["total_snapshots"] == 0
        assert ctx["total_detections"] == 0
        assert ctx["recent_activities"] == []
        assert ctx["pending_exports_count"] == 0
        assert ctx["alerts"] == []

    def test_greenhouse_and_module_counts(self):
        """Counts reflect actual data."""
        greenhouses = [FakeGreenhouse(1, "GH1"), FakeGreenhouse(2, "GH2")]
        modules = [FakeModule(1, "M1"), FakeModule(2, "M2"), FakeModule(3, "M3")]
        ctx = self.service.build_context(
            greenhouses=greenhouses,
            modules=modules,
            monitorings_by_module={1: [], 2: [], 3: []},
            today=date(2025, 6, 20),
        )
        assert ctx["greenhouse_count"] == 2
        assert ctx["module_count"] == 3


# ---------------------------------------------------------------------------
# Tests: build_context — pending and overdue
# ---------------------------------------------------------------------------


class TestDashboardServiceAlerts:
    """Tests for pending/overdue module counts and alert list."""

    def setup_method(self):
        self.service = DashboardService()

    def test_pending_modules_count(self):
        """Modules with no monitorings are counted as pending."""
        module = FakeModule(id=1, name="M1", monitoring_frequency_days=7)
        ctx = self.service.build_context(
            greenhouses=[FakeGreenhouse(1, "GH1")],
            modules=[module],
            monitorings_by_module={1: []},
            today=date(2025, 6, 20),
        )
        assert ctx["pending_modules_count"] >= 1

    def test_overdue_modules_count(self):
        """Modules with old monitorings are counted as overdue."""
        module = FakeModule(id=1, name="M1", monitoring_frequency_days=7)
        monitorings = [
            FakeMonitoring(id=1, status="completed", started_at=datetime(2025, 6, 1))
        ]
        ctx = self.service.build_context(
            greenhouses=[FakeGreenhouse(1, "GH1")],
            modules=[module],
            monitorings_by_module={1: monitorings},
            today=date(2025, 6, 20),
        )
        assert ctx["overdue_modules_count"] >= 1

    def test_alerts_limited_to_5(self):
        """Alerts list is capped at 5 items."""
        modules = [FakeModule(id=i, name=f"M{i}", monitoring_frequency_days=7) for i in range(1, 8)]
        monitorings_by_module = {i: [] for i in range(1, 8)}
        ctx = self.service.build_context(
            greenhouses=[FakeGreenhouse(1, "GH1")],
            modules=modules,
            monitorings_by_module=monitorings_by_module,
            today=date(2025, 6, 20),
        )
        assert len(ctx["alerts"]) <= 5


# ---------------------------------------------------------------------------
# Tests: build_context — last monitoring
# ---------------------------------------------------------------------------


class TestDashboardServiceLastMonitoring:
    """Tests for last monitoring indicator."""

    def setup_method(self):
        self.service = DashboardService()

    def test_last_monitoring_is_most_recent(self):
        """Last monitoring is the one with most recent started_at."""
        m1 = FakeMonitoring(id=1, status="completed", started_at=datetime(2025, 6, 10))
        m2 = FakeMonitoring(id=2, status="completed", started_at=datetime(2025, 6, 15))
        ctx = self.service.build_context(
            greenhouses=[FakeGreenhouse(1, "GH1")],
            modules=[FakeModule(1, "M1")],
            monitorings_by_module={1: [m1, m2]},
            today=date(2025, 6, 20),
        )
        assert ctx["last_monitoring"] is not None
        assert ctx["last_monitoring"].id == 2

    def test_last_monitoring_none_when_no_monitorings(self):
        """Last monitoring is None when there are no monitorings."""
        ctx = self.service.build_context(
            greenhouses=[FakeGreenhouse(1, "GH1")],
            modules=[FakeModule(1, "M1")],
            monitorings_by_module={1: []},
            today=date(2025, 6, 20),
        )
        assert ctx["last_monitoring"] is None


# ---------------------------------------------------------------------------
# Tests: build_context — monitorings this week
# ---------------------------------------------------------------------------


class TestDashboardServiceWeeklyCount:
    """Tests for monitorings_this_week indicator."""

    def setup_method(self):
        self.service = DashboardService()

    def test_monitorings_this_week_counts_correctly(self):
        """Counts monitorings with started_at in the same week as today."""
        # today is Friday 2025-06-20. Week: Mon 2025-06-16 to Sun 2025-06-22.
        today = date(2025, 6, 20)
        m_in_week = FakeMonitoring(id=1, status="completed", started_at=datetime(2025, 6, 18))
        m_out_week = FakeMonitoring(id=2, status="completed", started_at=datetime(2025, 6, 14))
        ctx = self.service.build_context(
            greenhouses=[FakeGreenhouse(1, "GH1")],
            modules=[FakeModule(1, "M1")],
            monitorings_by_module={1: [m_in_week, m_out_week]},
            today=today,
        )
        assert ctx["monitorings_this_week"] == 1

    def test_monitorings_this_week_zero_when_none(self):
        """Zero when no monitorings exist this week."""
        ctx = self.service.build_context(
            greenhouses=[],
            modules=[],
            monitorings_by_module={},
            today=date(2025, 6, 20),
        )
        assert ctx["monitorings_this_week"] == 0


# ---------------------------------------------------------------------------
# Tests: build_context — snapshot and detection totals
# ---------------------------------------------------------------------------


class TestDashboardServiceTotals:
    """Tests for total_snapshots and total_detections."""

    def setup_method(self):
        self.service = DashboardService()

    def test_totals_summed_across_all_monitorings(self):
        """Totals sum across all monitorings in all modules."""
        m1 = FakeMonitoring(id=1, status="completed", started_at=datetime(2025, 6, 18),
                            total_snapshots=10, total_detections=45)
        m2 = FakeMonitoring(id=2, status="completed", started_at=datetime(2025, 6, 19),
                            total_snapshots=5, total_detections=20)
        ctx = self.service.build_context(
            greenhouses=[FakeGreenhouse(1, "GH1")],
            modules=[FakeModule(1, "M1"), FakeModule(2, "M2")],
            monitorings_by_module={1: [m1], 2: [m2]},
            today=date(2025, 6, 20),
        )
        assert ctx["total_snapshots"] == 15
        assert ctx["total_detections"] == 65


# ---------------------------------------------------------------------------
# Tests: build_context — recent activities
# ---------------------------------------------------------------------------


class TestDashboardServiceActivities:
    """Tests for recent_activities indicator."""

    def setup_method(self):
        self.service = DashboardService()

    def test_recent_activities_limited_to_5(self):
        """Only the first 5 activities are included."""
        activities = [{"activity_type_name": f"A{i}", "category": "test", "occurred_at": datetime.now(), "module_id": 1} for i in range(10)]
        ctx = self.service.build_context(
            greenhouses=[],
            modules=[],
            monitorings_by_module={},
            recent_activities=activities,
            today=date(2025, 6, 20),
        )
        assert len(ctx["recent_activities"]) == 5

    def test_recent_activities_none_returns_empty_list(self):
        """None recent_activities returns empty list."""
        ctx = self.service.build_context(
            greenhouses=[],
            modules=[],
            monitorings_by_module={},
            recent_activities=None,
            today=date(2025, 6, 20),
        )
        assert ctx["recent_activities"] == []


# ---------------------------------------------------------------------------
# Tests: build_context — pending exports
# ---------------------------------------------------------------------------


class TestDashboardServiceExports:
    """Tests for pending_exports_count indicator."""

    def setup_method(self):
        self.service = DashboardService()

    def test_pending_exports_counted(self):
        """Counts pending, generating, and error exports."""
        exports = [
            FakeExportPackage(1, "pending"),
            FakeExportPackage(2, "generating"),
            FakeExportPackage(3, "error"),
            FakeExportPackage(4, "completed"),
        ]
        ctx = self.service.build_context(
            greenhouses=[],
            modules=[],
            monitorings_by_module={},
            export_packages=exports,
            today=date(2025, 6, 20),
        )
        assert ctx["pending_exports_count"] == 3

    def test_no_exports_returns_zero(self):
        """No export packages returns 0."""
        ctx = self.service.build_context(
            greenhouses=[],
            modules=[],
            monitorings_by_module={},
            export_packages=None,
            today=date(2025, 6, 20),
        )
        assert ctx["pending_exports_count"] == 0
