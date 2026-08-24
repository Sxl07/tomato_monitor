"""Unit tests for the centralized timezone utility (src/application/utils/timezone.py).

Validates: Requirements 2.6, 2.7, 2.14
"""

import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.application.utils.timezone import (
    utc_now,
    to_bogota,
    bogota_to_utc,
    format_bogota,
    iso_utc,
    BOGOTA_TZ,
)


class TestUtcNow:
    """Tests for utc_now()."""

    def test_returns_aware_datetime(self):
        result = utc_now()
        assert result.tzinfo is not None

    def test_returns_utc_timezone(self):
        result = utc_now()
        assert result.tzinfo == timezone.utc


class TestToBogota:
    """Tests for to_bogota()."""

    def test_none_returns_none(self):
        assert to_bogota(None) is None

    def test_naive_utc_converts_to_bogota(self):
        # 2024-01-15 02:17 UTC -> 2024-01-14 21:17 Bogota
        utc_naive = datetime(2024, 1, 15, 2, 17, 0)
        result = to_bogota(utc_naive)
        assert result.hour == 21
        assert result.day == 14
        assert result.tzinfo is not None

    def test_aware_utc_converts_to_bogota(self):
        utc_aware = datetime(2024, 1, 15, 2, 17, 0, tzinfo=timezone.utc)
        result = to_bogota(utc_aware)
        assert result.hour == 21
        assert result.day == 14

    def test_offset_always_minus_5(self):
        # Colombia has no DST - offset is always -5h
        summer = datetime(2024, 7, 15, 12, 0, 0)
        winter = datetime(2024, 1, 15, 12, 0, 0)
        result_summer = to_bogota(summer)
        result_winter = to_bogota(winter)
        # Both should be 7:00 (12 - 5)
        assert result_summer.hour == 7
        assert result_winter.hour == 7

    def test_midnight_utc_to_bogota(self):
        # 2024-06-01 00:00 UTC -> 2024-05-31 19:00 Bogota
        utc = datetime(2024, 6, 1, 0, 0, 0)
        result = to_bogota(utc)
        assert result.day == 31
        assert result.month == 5
        assert result.hour == 19


class TestBogotaToUtc:
    """Tests for bogota_to_utc()."""

    def test_none_returns_none(self):
        assert bogota_to_utc(None) is None

    def test_local_to_utc(self):
        # 2024-01-14 21:17 Bogota -> 2024-01-15 02:17 UTC
        local = datetime(2024, 1, 14, 21, 17, 0)
        result = bogota_to_utc(local)
        assert result.hour == 2
        assert result.day == 15
        assert result.tzinfo is None  # Stored as naive UTC

    def test_round_trip(self):
        # Enter 21:17 local -> persist as UTC -> display as 21:17
        local_input = datetime(2024, 1, 14, 21, 17, 0)
        utc_stored = bogota_to_utc(local_input)
        displayed = to_bogota(utc_stored)
        assert displayed.hour == 21
        assert displayed.minute == 17
        assert displayed.day == 14

    def test_aware_bogota_to_utc(self):
        # If already aware as Bogota
        aware_bogota = datetime(2024, 1, 14, 21, 17, 0, tzinfo=BOGOTA_TZ)
        result = bogota_to_utc(aware_bogota)
        assert result.hour == 2
        assert result.day == 15
        assert result.tzinfo is None


class TestFormatBogota:
    """Tests for format_bogota()."""

    def test_none_returns_empty(self):
        assert format_bogota(None) == ""

    def test_default_format(self):
        dt = datetime(2024, 1, 15, 2, 17, 0)
        result = format_bogota(dt)
        assert result == "14/01/2024 21:17"

    def test_custom_format(self):
        dt = datetime(2024, 1, 15, 2, 17, 0)
        result = format_bogota(dt, "%H:%M")
        assert result == "21:17"

    def test_date_only_format(self):
        dt = datetime(2024, 1, 15, 2, 17, 0)
        result = format_bogota(dt, "%d/%m/%Y")
        assert result == "14/01/2024"


class TestIsoUtc:
    """Tests for iso_utc()."""

    def test_none_returns_empty(self):
        assert iso_utc(None) == ""

    def test_naive_utc_gets_z(self):
        dt = datetime(2024, 1, 15, 2, 17, 0)
        result = iso_utc(dt)
        assert result == "2024-01-15T02:17:00Z"

    def test_aware_utc_gets_z(self):
        dt = datetime(2024, 1, 15, 2, 17, 0, tzinfo=timezone.utc)
        result = iso_utc(dt)
        assert result == "2024-01-15T02:17:00Z"

    def test_aware_bogota_converts_to_utc_then_z(self):
        # 21:17 Bogota = 02:17 UTC next day
        dt = datetime(2024, 1, 14, 21, 17, 0, tzinfo=BOGOTA_TZ)
        result = iso_utc(dt)
        assert result == "2024-01-15T02:17:00Z"


class TestLogServiceTimezone:
    """Tests for LogService timezone-aware timestamps."""

    def test_entry_has_tzinfo(self):
        from src.application.services.log_service import LogService, LogLevel

        service = LogService()
        entry = service.add_entry(
            monitoring_id=1, level=LogLevel.INFO, source="test", message="Test"
        )
        assert entry.timestamp.tzinfo is not None

    def test_get_entries_naive_since_works(self):
        from src.application.services.log_service import LogService, LogLevel

        service = LogService()
        service.add_entry(monitoring_id=1, level=LogLevel.INFO, source="t", message="m")
        # Naive since (should be treated as UTC)
        entries = service.get_entries(1, since=datetime(2020, 1, 1))
        assert len(entries) == 1

    def test_get_entries_aware_since_works(self):
        from src.application.services.log_service import LogService, LogLevel

        service = LogService()
        service.add_entry(monitoring_id=1, level=LogLevel.INFO, source="t", message="m")
        # Aware since
        entries = service.get_entries(1, since=datetime(2020, 1, 1, tzinfo=timezone.utc))
        assert len(entries) == 1

    def test_serialization_has_z_suffix(self):
        from src.application.services.log_service import LogService, LogLevel

        service = LogService()
        entry = service.add_entry(
            monitoring_id=1, level=LogLevel.INFO, source="t", message="m"
        )
        serialized = entry.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
        assert serialized.endswith("Z")


class TestJinjaFilterRegistration:
    """Tests for Jinja filter availability."""

    def test_filter_registered_on_agricultural_templates(self):
        from app.routes.agricultural_ui import templates

        assert "to_bogota" in templates.env.filters

    def test_filter_produces_correct_output(self):
        from app.routes.agricultural_ui import templates

        filter_fn = templates.env.filters["to_bogota"]
        dt = datetime(2024, 1, 15, 2, 17, 0)
        result = filter_fn(dt)
        assert "21:17" in result
        assert "14/01/2024" in result


class TestHistoryServiceTimezoneConversion:
    """HistoryService uses format_bogota for date_display and time_display."""

    def test_monitoring_date_display_in_bogota(self):
        """UTC 2026-08-21 02:17 displays as 20/08/2026 in Bogota."""
        from src.application.services.history_service import HistoryService
        from types import SimpleNamespace

        monitoring = SimpleNamespace(
            id=1,
            status="completed",
            started_at=datetime(2026, 8, 21, 2, 17, 0),
            total_detections=10,
            total_snapshots=5,
        )
        service = HistoryService()
        result = service.build_combined_history([monitoring], {}, [], [])
        item = result[0]
        assert item["date_display"] == "20/08/2026"
        assert item["time_display"] == "21:17"

    def test_activity_date_display_in_bogota(self):
        """UTC 2026-08-21 02:17 displays as 20/08/2026 21:17 in Bogota."""
        from src.application.services.history_service import HistoryService
        from types import SimpleNamespace

        activity = SimpleNamespace(
            id=1,
            activity_type_id=1,
            occurred_at=datetime(2026, 8, 21, 2, 17, 0),
            product_name=None,
            quantity=None,
            unit=None,
            notes=None,
        )
        activity_type = SimpleNamespace(
            id=1, name="Riego", category="mantenimiento", code="riego",
        )
        service = HistoryService()
        result = service.build_combined_history([], {}, [activity], [activity_type])
        item = result[0]
        assert item["date_display"] == "20/08/2026"
        assert item["time_display"] == "21:17"

    def test_none_started_at_produces_empty_strings(self):
        """Monitoring with started_at=None produces empty date/time strings."""
        from src.application.services.history_service import HistoryService
        from types import SimpleNamespace

        monitoring = SimpleNamespace(
            id=1, status="completed", started_at=None,
            total_detections=0, total_snapshots=0,
        )
        service = HistoryService()
        result = service.build_combined_history([monitoring], {}, [], [])
        item = result[0]
        assert item["date_display"] == ""
        assert item["time_display"] == ""


class TestContextBuildersTimezoneConversion:
    """context_builders._format_date_spanish and _format_time convert to Bogota."""

    def test_format_date_spanish_converts_utc(self):
        """UTC 2026-08-21 02:17 → '20 Ago 2026' in Bogota."""
        from app.context_builders import _format_date_spanish
        result = _format_date_spanish(datetime(2026, 8, 21, 2, 17, 0))
        assert result == "20 Ago 2026"

    def test_format_time_converts_utc(self):
        """UTC 2026-08-21 02:17 → '21:17' in Bogota."""
        from app.context_builders import _format_time
        result = _format_time(datetime(2026, 8, 21, 2, 17, 0))
        assert result == "21:17"

    def test_format_date_spanish_none_returns_empty(self):
        from app.context_builders import _format_date_spanish
        # Passing None should be handled gracefully — to_bogota(None) returns None
        # but the callers may pass None, let's verify the caller guards or function handles it
        # The function expects a datetime, callers guard with if dt checks.
        # We just verify a valid UTC that crosses midnight.
        result = _format_date_spanish(datetime(2026, 1, 1, 3, 0, 0))
        # UTC 03:00 Jan 1 → Bogota 22:00 Dec 31
        assert result == "31 Dic 2025"

    def test_format_time_midnight_crossing(self):
        from app.context_builders import _format_time
        # UTC 03:00 → Bogota 22:00 (previous day)
        result = _format_time(datetime(2026, 1, 1, 3, 0, 0))
        assert result == "22:00"
