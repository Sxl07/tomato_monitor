"""Unit tests for HistoryService.build_combined_history().

Pure logic tests — no database, no FastAPI, no TestClient.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from src.application.services.history_service import HistoryService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_monitoring(
    id: int,
    status: str = "completed",
    started_at: datetime | None = None,
    total_detections: int = 0,
    total_snapshots: int = 5,
):
    if started_at is None:
        started_at = datetime(2025, 7, 10, 14, 30, 0)
    return SimpleNamespace(
        id=id,
        status=status,
        started_at=started_at,
        total_detections=total_detections,
        total_snapshots=total_snapshots,
    )


def _make_metrics(monitoring_id: int, total_tomatoes: int = 45, pct_healthy: float = 78.0):
    return SimpleNamespace(
        monitoring_id=monitoring_id,
        total_tomatoes=total_tomatoes,
        pct_healthy=pct_healthy,
    )


def _make_activity_log(
    id: int,
    activity_type_id: int = 1,
    occurred_at: datetime | None = None,
    product_name: str | None = None,
    quantity: float | None = None,
    unit: str | None = None,
    notes: str | None = None,
):
    if occurred_at is None:
        occurred_at = datetime(2025, 7, 9, 10, 0, 0)
    return SimpleNamespace(
        id=id,
        activity_type_id=activity_type_id,
        occurred_at=occurred_at,
        product_name=product_name,
        quantity=quantity,
        unit=unit,
        notes=notes,
    )


def _make_activity_type(
    id: int = 1,
    code: str = "riego",
    name: str = "Riego",
    category: str = "mantenimiento",
):
    return SimpleNamespace(id=id, code=code, name=name, category=category)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildCombinedHistoryEmpty:
    """Empty inputs produce empty output."""

    def test_all_empty(self):
        service = HistoryService()
        result = service.build_combined_history([], {}, [], [])
        assert result == []

    def test_no_activities(self):
        service = HistoryService()
        m = _make_monitoring(1, "completed")
        result = service.build_combined_history([m], {}, [], [])
        assert len(result) == 1

    def test_no_monitorings(self):
        service = HistoryService()
        log = _make_activity_log(1)
        at = _make_activity_type()
        result = service.build_combined_history([], {}, [log], [at])
        assert len(result) == 1


class TestMonitoringItems:
    """Monitoring items are built correctly."""

    def test_completed_monitoring_url(self):
        service = HistoryService()
        m = _make_monitoring(5, "completed")
        result = service.build_combined_history([m], {}, [], [])
        item = result[0]
        assert item["url"] == "/monitoreos/5/reporte"
        assert item["action_label"] == "Ver reporte"

    def test_analyzing_monitoring_url(self):
        service = HistoryService()
        m = _make_monitoring(7, "analyzing")
        result = service.build_combined_history([m], {}, [], [])
        item = result[0]
        assert item["url"] == "/monitoreos/7/ejecucion"
        assert item["action_label"] == "Ver ejecución"

    def test_analyzing_monitoring_badge_and_subtitle(self):
        # Spec 020 hotfix: analyzing shows "Análisis en curso".
        service = HistoryService()
        m = _make_monitoring(7, "analyzing")
        result = service.build_combined_history([m], {}, [], [])
        item = result[0]
        assert item["badge_label"] == "Análisis en curso"
        assert item["subtitle"] == "Análisis en curso"

    def test_ready_for_analysis_url_and_action(self):
        # Spec 020 hotfix: ready_for_analysis reuses the EXISTING execution screen.
        service = HistoryService()
        m = _make_monitoring(9, "ready_for_analysis", total_snapshots=0)
        result = service.build_combined_history([m], {}, [], [])
        item = result[0]
        assert item["url"] == "/monitoreos/9/ejecucion"
        assert item["action_label"] == "Ver ejecución"

    def test_ready_for_analysis_badge_label(self):
        service = HistoryService()
        m = _make_monitoring(9, "ready_for_analysis", total_snapshots=0)
        result = service.build_combined_history([m], {}, [], [])
        item = result[0]
        assert item["badge_label"] == "Captura finalizada · análisis pendiente"
        assert item["badge_class"] == "badge-info"

    def test_ready_for_analysis_subtitle_not_zero_snapshots(self):
        # Must NOT show "0 snapshots capturados"; indicate captured video pending.
        service = HistoryService()
        m = _make_monitoring(9, "ready_for_analysis", total_snapshots=0)
        result = service.build_combined_history([m], {}, [], [])
        item = result[0]
        assert "snapshots capturados" not in item["subtitle"]
        assert item["subtitle"] == "Video capturado · pendiente de análisis"

    def test_running_monitoring_url(self):
        service = HistoryService()
        m = _make_monitoring(3, "running")
        result = service.build_combined_history([m], {}, [], [])
        item = result[0]
        assert item["url"] == "/monitoreos/3/ejecucion"
        assert item["action_label"] == "Ver ejecución"

    def test_error_monitoring_badge(self):
        service = HistoryService()
        m = _make_monitoring(2, "error")
        result = service.build_combined_history([m], {}, [], [])
        item = result[0]
        assert item["badge_class"] == "badge-danger"
        assert item["badge_label"] == "Error"

    def test_aborted_monitoring_url(self):
        service = HistoryService()
        m = _make_monitoring(4, "aborted")
        result = service.build_combined_history([m], {}, [], [])
        item = result[0]
        assert item["url"] == "/monitoreos/4/reporte"
        assert item["action_label"] == "Ver detalle"

    def test_initializing_skipped(self):
        service = HistoryService()
        m = _make_monitoring(1, "initializing")
        result = service.build_combined_history([m], {}, [], [])
        assert len(result) == 0

    def test_metrics_in_subtitle(self):
        service = HistoryService()
        m = _make_monitoring(1, "completed")
        metrics = _make_metrics(1, total_tomatoes=45, pct_healthy=78.0)
        result = service.build_combined_history([m], {1: metrics}, [], [])
        item = result[0]
        assert "45 tomates" in item["subtitle"]
        assert "78% sanos" in item["subtitle"]

    def test_missing_metrics_uses_detections(self):
        service = HistoryService()
        m = _make_monitoring(1, "completed", total_detections=20)
        result = service.build_combined_history([m], {}, [], [])
        item = result[0]
        assert "20 tomates detectados" in item["subtitle"]

    def test_no_metrics_no_detections_uses_snapshots(self):
        service = HistoryService()
        m = _make_monitoring(1, "completed", total_detections=0, total_snapshots=8)
        result = service.build_combined_history([m], {}, [], [])
        item = result[0]
        assert "8 snapshots capturados" in item["subtitle"]

    def test_item_type_is_monitoring(self):
        service = HistoryService()
        m = _make_monitoring(1, "completed")
        result = service.build_combined_history([m], {}, [], [])
        assert result[0]["item_type"] == "monitoring"

    def test_icon_is_camera(self):
        service = HistoryService()
        m = _make_monitoring(1, "completed")
        result = service.build_combined_history([m], {}, [], [])
        assert result[0]["icon_key"] == "monitoring"


class TestActivityItems:
    """Activity items are built correctly."""

    def test_with_type_uses_name(self):
        service = HistoryService()
        log = _make_activity_log(1, activity_type_id=1)
        at = _make_activity_type(id=1, name="Riego", code="riego")
        result = service.build_combined_history([], {}, [log], [at])
        item = result[0]
        assert item["title"] == "Riego"

    def test_without_type_uses_fallback(self):
        service = HistoryService()
        log = _make_activity_log(1, activity_type_id=99)
        result = service.build_combined_history([], {}, [log], [])
        item = result[0]
        assert item["title"] == "Actividad agrícola"

    def test_product_in_subtitle(self):
        service = HistoryService()
        log = _make_activity_log(1, product_name="Fungicida X")
        at = _make_activity_type()
        result = service.build_combined_history([], {}, [log], [at])
        item = result[0]
        assert "Fungicida X" in item["subtitle"]

    def test_quantity_in_subtitle(self):
        service = HistoryService()
        log = _make_activity_log(1, quantity=2.5, unit="L")
        at = _make_activity_type()
        result = service.build_combined_history([], {}, [log], [at])
        item = result[0]
        assert "2.5 L" in item["subtitle"]

    def test_item_type_is_activity(self):
        service = HistoryService()
        log = _make_activity_log(1)
        at = _make_activity_type()
        result = service.build_combined_history([], {}, [log], [at])
        assert result[0]["item_type"] == "activity"

    def test_url_is_none(self):
        service = HistoryService()
        log = _make_activity_log(1)
        at = _make_activity_type()
        result = service.build_combined_history([], {}, [log], [at])
        assert result[0]["url"] is None

    def test_known_icon(self):
        service = HistoryService()
        log = _make_activity_log(1, activity_type_id=1)
        at = _make_activity_type(id=1, code="riego")
        result = service.build_combined_history([], {}, [log], [at])
        assert result[0]["icon_key"] == "watering"

    def test_unknown_code_default_icon(self):
        service = HistoryService()
        log = _make_activity_log(1, activity_type_id=1)
        at = _make_activity_type(id=1, code="desconocido")
        result = service.build_combined_history([], {}, [log], [at])
        assert result[0]["icon_key"] == "note"


class TestSortingOrder:
    """Items are sorted by occurred_at descending."""

    def test_mixed_items_sorted_descending(self):
        service = HistoryService()
        # Monitoring: July 10 14:30
        m = _make_monitoring(1, "completed", started_at=datetime(2025, 7, 10, 14, 30))
        # Activity: July 11 09:00 (more recent)
        log = _make_activity_log(1, occurred_at=datetime(2025, 7, 11, 9, 0))
        at = _make_activity_type()
        result = service.build_combined_history([m], {}, [log], [at])
        assert len(result) == 2
        # Activity should be first (more recent)
        assert result[0]["item_type"] == "activity"
        assert result[1]["item_type"] == "monitoring"

    def test_multiple_monitorings_sorted(self):
        service = HistoryService()
        m1 = _make_monitoring(1, "completed", started_at=datetime(2025, 7, 1, 10, 0))
        m2 = _make_monitoring(2, "completed", started_at=datetime(2025, 7, 5, 10, 0))
        result = service.build_combined_history([m1, m2], {}, [], [])
        assert result[0]["id"] == 2  # More recent first
        assert result[1]["id"] == 1

    def test_none_occurred_at_goes_last(self):
        service = HistoryService()
        m = _make_monitoring(1, "completed", started_at=datetime(2025, 7, 10, 14, 30))
        # Create a log with None occurred_at
        log = SimpleNamespace(
            id=2,
            activity_type_id=1,
            occurred_at=None,
            product_name=None,
            quantity=None,
            unit=None,
            notes=None,
        )
        at = _make_activity_type()
        result = service.build_combined_history([m], {}, [log], [at])
        assert result[-1]["occurred_at"] is None
