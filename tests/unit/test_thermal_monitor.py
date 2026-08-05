"""Unit tests for ThermalMonitor — Task 12B.

Tests verify:
- pause_event property returns the internal Event
- current_temperature property returns last read temp (initially None)
- stop() accounts for in-progress pause duration
- get_session_metadata() includes current_temperature_c and is_paused
- _record_temperature updates current_temperature
- _record_temperature updates peak_temperature
- _record_temperature doesn't lower peak
- cooling_warning_at_start property returns flag
- Non-RPi platform doesn't break
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch, MagicMock

import pytest

from src.infrastructure.monitoring.thermal_monitor import ThermalMonitor


class TestPauseEventProperty:
    """pause_event property returns the internal threading.Event."""

    def test_pause_event_returns_event(self):
        event = threading.Event()
        monitor = ThermalMonitor(pause_event=event)
        assert monitor.pause_event is event

    def test_pause_event_type(self):
        event = threading.Event()
        monitor = ThermalMonitor(pause_event=event)
        assert isinstance(monitor.pause_event, threading.Event)


class TestCurrentTemperatureProperty:
    """current_temperature property returns last read temp."""

    def test_initial_current_temperature_is_none(self):
        event = threading.Event()
        monitor = ThermalMonitor(pause_event=event)
        assert monitor.current_temperature is None

    def test_current_temperature_updated_after_read(self):
        """Simulate temperature update by writing internal attribute."""
        event = threading.Event()
        monitor = ThermalMonitor(pause_event=event)
        # Simulate what _monitor_loop does
        monitor._current_temperature = 55.3
        assert monitor.current_temperature == 55.3


class TestRecordTemperature:
    """_record_temperature updates current and peak temperature."""

    def test_record_temperature_updates_current(self):
        event = threading.Event()
        monitor = ThermalMonitor(pause_event=event)
        monitor._record_temperature(65.2)
        assert monitor.current_temperature == 65.2

    def test_record_temperature_updates_peak(self):
        event = threading.Event()
        monitor = ThermalMonitor(pause_event=event)
        monitor._record_temperature(72.5)
        assert monitor.peak_temperature == 72.5

    def test_record_temperature_does_not_lower_peak(self):
        event = threading.Event()
        monitor = ThermalMonitor(pause_event=event)
        monitor._record_temperature(80.0)
        monitor._record_temperature(60.0)
        assert monitor.peak_temperature == 80.0

    def test_record_temperature_none_is_noop(self):
        event = threading.Event()
        monitor = ThermalMonitor(pause_event=event)
        monitor._record_temperature(None)
        assert monitor.current_temperature is None
        assert monitor.peak_temperature == 0.0

    def test_record_temperature_converts_to_float(self):
        event = threading.Event()
        monitor = ThermalMonitor(pause_event=event)
        monitor._record_temperature(70)  # int
        assert monitor.current_temperature == 70.0
        assert isinstance(monitor.current_temperature, float)


class TestCoolingWarningAtStartProperty:
    """cooling_warning_at_start property returns the flag."""

    def test_cooling_warning_at_start_default_false(self):
        event = threading.Event()
        monitor = ThermalMonitor(pause_event=event)
        assert monitor.cooling_warning_at_start is False

    def test_cooling_warning_at_start_returns_flag(self):
        event = threading.Event()
        monitor = ThermalMonitor(pause_event=event)
        monitor._cooling_warning_at_start = True
        assert monitor.cooling_warning_at_start is True


class TestStopAccountsForInProgressPause:
    """stop() adds in-progress pause duration to total."""

    def test_stop_with_active_pause_accumulates_duration(self):
        event = threading.Event()
        monitor = ThermalMonitor(pause_event=event)
        # Simulate an active pause that started 2 seconds ago
        monitor._pause_start_time = time.time() - 2.0
        monitor._total_pause_duration = 1.0  # Previously accumulated

        monitor.stop()

        # Should have added ~2 seconds
        assert monitor.total_pause_duration_seconds >= 2.9  # 1.0 + ~2.0
        assert monitor._pause_start_time is None

    def test_stop_without_active_pause_no_change(self):
        event = threading.Event()
        monitor = ThermalMonitor(pause_event=event)
        monitor._total_pause_duration = 5.0
        monitor._pause_start_time = None

        monitor.stop()

        assert monitor.total_pause_duration_seconds == 5.0

    def test_stop_resets_pause_start_time(self):
        event = threading.Event()
        monitor = ThermalMonitor(pause_event=event)
        monitor._pause_start_time = time.time() - 1.0

        monitor.stop()

        assert monitor._pause_start_time is None


class TestGetSessionMetadata:
    """get_session_metadata() includes new fields."""

    def test_metadata_includes_current_temperature_c(self):
        event = threading.Event()
        monitor = ThermalMonitor(pause_event=event)
        monitor._current_temperature = 62.5

        meta = monitor.get_session_metadata()

        assert meta["current_temperature_c"] == 62.5

    def test_metadata_current_temperature_none_when_never_read(self):
        event = threading.Event()
        monitor = ThermalMonitor(pause_event=event)

        meta = monitor.get_session_metadata()

        assert meta["current_temperature_c"] is None

    def test_metadata_includes_is_paused_false(self):
        event = threading.Event()
        monitor = ThermalMonitor(pause_event=event)

        meta = monitor.get_session_metadata()

        assert meta["is_paused"] is False

    def test_metadata_includes_is_paused_true(self):
        event = threading.Event()
        event.set()
        monitor = ThermalMonitor(pause_event=event)

        meta = monitor.get_session_metadata()

        assert meta["is_paused"] is True

    def test_metadata_has_all_expected_keys(self):
        event = threading.Event()
        monitor = ThermalMonitor(pause_event=event)

        meta = monitor.get_session_metadata()

        expected_keys = {
            "peak_temperature_c",
            "pause_count",
            "total_pause_duration_s",
            "cooling_warning_at_start",
            "current_temperature_c",
            "is_paused",
        }
        assert set(meta.keys()) == expected_keys


class TestNonRPiPlatform:
    """Non-RPi platform doesn't break thermal monitor."""

    def test_start_stop_without_vcgencmd(self):
        """On non-RPi, start and stop should not raise."""
        event = threading.Event()
        monitor = ThermalMonitor(pause_event=event, poll_interval_seconds=0.1)

        with patch.object(monitor, "_check_vcgencmd", return_value=False):
            monitor.start()
            time.sleep(0.2)
            monitor.stop()

        # Should complete without error
        assert monitor.current_temperature is None
        assert monitor.peak_temperature == 0.0
