"""Unit tests for AnalysisPreflight (Spec 020, Task 5.4)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.application.services.analysis_preflight import AnalysisPreflight
from src.domain.value_objects.monitoring_status import MonitoringState
from src.infrastructure.monitoring.undervoltage_probe import UndervoltageStatus

BASE = Path.cwd()
GOOD_PATH = "outputs/monitorings/1/video/monitoring.mp4"


class FakeRegistry:
    def __init__(
        self,
        analysis_claimed=False,
        any_live=False,
        capture_active=False,
        global_analysis_active=False,
    ):
        self._claimed = analysis_claimed
        self._any_live = any_live
        self._capture_active = capture_active
        self._global_analysis_active = global_analysis_active

    def is_analysis_claimed(self, monitoring_id):
        return self._claimed

    def has_any_live_thread(self):
        return self._any_live

    def has_active_capture(self):
        # Spec 020: GLOBAL, module-independent active-capture detection.
        return self._capture_active

    def is_global_analysis_active(self):
        # Spec 020: device-global single-heavy-analysis guard.
        return self._global_analysis_active


class FakeMonitoringRepo:
    def __init__(self, others=None):
        self._others = others or []

    def get_by_module(self, module_id):
        return self._others


class FakeProbe:
    def __init__(self, status=UndervoltageStatus.UNAVAILABLE):
        self._status = status

    def check(self):
        return self._status


def _monitoring(status=MonitoringState.READY_FOR_ANALYSIS.value, video_path=GOOD_PATH, mid=1, module_id=10):
    return SimpleNamespace(id=mid, module_id=module_id, status=status, video_path=video_path)


def _build(
    *,
    monitoring_repo=None,
    registry=None,
    threshold=78.0,
    probe=None,
    temp=None,
    readable=True,
    exists=True,
    size=100,
):
    return AnalysisPreflight(
        base_dir=BASE,
        monitoring_repo=monitoring_repo or FakeMonitoringRepo(),
        runtime_registry=registry or FakeRegistry(),
        thermal_pause_threshold_c=threshold,
        undervoltage_probe=probe or FakeProbe(),
        temperature_reader=lambda: temp,
        video_readable=lambda p: readable,
        os_path_exists=lambda p: exists,
        os_path_getsize=lambda p: size,
    )


class TestPreflightSuccess:
    def test_all_checks_pass(self):
        pf = _build(temp=60.0)
        result = pf.run(_monitoring())
        assert result.ok is True
        assert result.temperature_c == 60.0

    def test_success_when_temperature_unavailable(self):
        pf = _build(temp=None)
        result = pf.run(_monitoring())
        assert result.ok is True
        assert result.temperature_c is None

    def test_success_when_undervoltage_unavailable(self):
        pf = _build(probe=FakeProbe(UndervoltageStatus.UNAVAILABLE))
        assert pf.run(_monitoring()).ok is True

    def test_success_when_undervoltage_absent(self):
        pf = _build(probe=FakeProbe(UndervoltageStatus.ABSENT))
        assert pf.run(_monitoring()).ok is True


class TestPreflightOrderedFailures:
    def test_invalid_status(self):
        pf = _build()
        r = pf.run(_monitoring(status=MonitoringState.RUNNING.value))
        assert not r.ok and r.reason_code == "invalid_status"

    def test_missing_video_path(self):
        pf = _build()
        r = pf.run(_monitoring(video_path=None))
        assert not r.ok and r.reason_code == "video_missing_path"

    def test_unsafe_path(self):
        pf = _build()
        r = pf.run(_monitoring(video_path="../../etc/passwd"))
        assert not r.ok and r.reason_code == "video_unsafe_path"

    def test_absolute_path_rejected(self):
        pf = _build()
        r = pf.run(_monitoring(video_path="/etc/passwd"))
        assert not r.ok and r.reason_code == "video_unsafe_path"

    def test_file_not_found(self):
        pf = _build(exists=False)
        r = pf.run(_monitoring())
        assert not r.ok and r.reason_code == "video_not_found"

    def test_empty_file(self):
        pf = _build(size=0)
        r = pf.run(_monitoring())
        assert not r.ok and r.reason_code == "video_empty"

    def test_unreadable_video(self):
        pf = _build(readable=False)
        r = pf.run(_monitoring())
        assert not r.ok and r.reason_code == "video_unreadable"

    def test_analysis_already_claimed(self):
        pf = _build(registry=FakeRegistry(analysis_claimed=True))
        r = pf.run(_monitoring())
        assert not r.ok and r.reason_code == "analysis_in_progress"

    def test_module_session_conflict_excludes_self(self):
        other = SimpleNamespace(id=2, status=MonitoringState.RUNNING.value)
        pf = _build(monitoring_repo=FakeMonitoringRepo(others=[other]))
        r = pf.run(_monitoring(mid=1))
        assert not r.ok and r.reason_code == "module_session_conflict"

    def test_own_id_not_a_conflict(self):
        # The monitoring's own ready_for_analysis record must not conflict with itself.
        me = SimpleNamespace(id=1, status=MonitoringState.READY_FOR_ANALYSIS.value)
        pf = _build(monitoring_repo=FakeMonitoringRepo(others=[me]), temp=50.0)
        r = pf.run(_monitoring(mid=1))
        assert r.ok is True

    def test_device_capture_active_blocks(self):
        # Spec 020: a capture active on ANY module (global) blocks a deferred analysis.
        pf = _build(registry=FakeRegistry(capture_active=True))
        r = pf.run(_monitoring())
        assert not r.ok and r.reason_code == "device_capture_active"

    def test_device_analysis_active_blocks(self):
        # Spec 020: at most one heavy analysis on the device.
        pf = _build(registry=FakeRegistry(global_analysis_active=True))
        r = pf.run(_monitoring())
        assert not r.ok and r.reason_code == "device_analysis_active"

    def test_temperature_above_threshold_blocks(self):
        pf = _build(temp=85.0, threshold=78.0)
        r = pf.run(_monitoring())
        assert not r.ok and r.reason_code == "temperature_high"
        assert r.temperature_c == 85.0

    def test_undervoltage_present_blocks(self):
        pf = _build(probe=FakeProbe(UndervoltageStatus.PRESENT), temp=60.0)
        r = pf.run(_monitoring())
        assert not r.ok and r.reason_code == "undervoltage_present"


class TestPreflightMessagesSpanish:
    def test_messages_are_actionable_spanish(self):
        pf = _build(exists=False)
        r = pf.run(_monitoring())
        assert "no existe en disco" in r.message
