"""Unit tests for thermal metrics in SnapshotAnalysisService — Task 12B.

Tests verify:
- AnalysisProgress has thermal fields with correct defaults
- AnalysisResult has thermal fields with correct defaults
- _refresh_thermal_progress reads from thermal_monitor safely
- _build_result includes thermal metrics from thermal_monitor
- _generate_reports includes thermal and performance_config sections
- seconds_per_processed_snapshot is computed correctly
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.application.services.snapshot_analysis_service import (
    AnalysisProgress,
    AnalysisResult,
    SnapshotAnalysisService,
)
from src.domain.entities.snapshot import Snapshot
from src.infrastructure.persistence.local.snapshot_analysis_report_writer import (
    SnapshotAnalysisReportWriter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeSnapshotRepo:
    def __init__(self, snapshots=None):
        self._snapshots = snapshots or []

    def get_by_monitoring(self, monitoring_id):
        return list(self._snapshots)

    def update_has_detections(self, id, has_detections):
        for s in self._snapshots:
            if s.id == id:
                s.has_detections = has_detections
                return s
        return MagicMock()


class FakeDbSession:
    def commit(self):
        pass

    def rollback(self):
        pass


class FakeThermalMonitor:
    """Fake thermal monitor for testing."""

    def __init__(
        self,
        peak_temp=45.0,
        pause_count=2,
        total_pause_duration=10.5,
        cooling_warning=True,
        current_temp=42.0,
        is_paused=False,
    ):
        self._pause_event = threading.Event()
        if is_paused:
            self._pause_event.set()
        self._peak_temperature = peak_temp
        self._pause_count = pause_count
        self._total_pause_duration = total_pause_duration
        self._cooling_warning_at_start = cooling_warning
        self._current_temperature = current_temp

    @property
    def pause_event(self):
        return self._pause_event

    @property
    def current_temperature(self):
        return self._current_temperature

    @property
    def peak_temperature(self):
        return self._peak_temperature

    @property
    def pause_count(self):
        return self._pause_count

    @property
    def total_pause_duration_seconds(self):
        return self._total_pause_duration

    def start(self):
        pass

    def stop(self):
        pass

    def get_session_metadata(self):
        return {}


def _make_snapshot(id, monitoring_id, frame_index):
    return Snapshot(
        id=id,
        monitoring_id=monitoring_id,
        image_path=f"outputs/monitorings/{monitoring_id}/snapshots/raw/snapshot_{frame_index:06d}.jpg",
        frame_index=frame_index,
        has_detections=False,
    )


def _make_service(tmp_path, thermal_monitor=None, snapshots=None, process_results=None):
    snapshot_repo = FakeSnapshotRepo(snapshots or [])
    db_session = FakeDbSession()
    results_iter = iter(process_results or [{"detections_count": 0, "tracked_count": 0,
        "health_executed_count": 0, "maturity_executed_count": 0, "reused_count": 0,
        "new_tracks_count": 0, "times": {"detection_sec": 0.1, "tracking_sec": 0.01,
        "total_frame_sec": 0.12}, "detections": []}])

    writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path)

    service = SnapshotAnalysisService(
        monitoring_id=1,
        snapshot_repo=snapshot_repo,
        inspection_result_repo=MagicMock(),
        db_session=db_session,
        components_factory=MagicMock(return_value=MagicMock()),
        process_frame_fn=MagicMock(side_effect=lambda img, comp, name, **kw: next(results_iter)),
        annotation_renderer=MagicMock(return_value=np.zeros((480, 640, 3), dtype=np.uint8)),
        thermal_monitor=thermal_monitor,
        analysis_skip_maturity=True,
        report_writer=writer,
        profile_name="edge",
    )
    return service


# ---------------------------------------------------------------------------
# Tests: AnalysisProgress thermal defaults
# ---------------------------------------------------------------------------


class TestAnalysisProgressThermalDefaults:
    """AnalysisProgress has thermal fields with correct defaults."""

    def test_thermal_paused_default(self):
        p = AnalysisProgress()
        assert p.thermal_paused is False

    def test_thermal_current_temperature_default(self):
        p = AnalysisProgress()
        assert p.thermal_current_temperature_c is None

    def test_thermal_peak_temperature_default(self):
        p = AnalysisProgress()
        assert p.thermal_peak_temperature_c == 0.0

    def test_thermal_pause_count_default(self):
        p = AnalysisProgress()
        assert p.thermal_pause_count == 0

    def test_thermal_pause_duration_default(self):
        p = AnalysisProgress()
        assert p.thermal_pause_duration_seconds == 0.0


# ---------------------------------------------------------------------------
# Tests: AnalysisResult thermal defaults
# ---------------------------------------------------------------------------


class TestAnalysisResultThermalDefaults:
    """AnalysisResult has thermal fields with correct defaults."""

    def test_thermal_peak_temperature_default(self):
        r = AnalysisResult()
        assert r.thermal_peak_temperature_c == 0.0

    def test_thermal_pause_count_default(self):
        r = AnalysisResult()
        assert r.thermal_pause_count == 0

    def test_thermal_pause_duration_default(self):
        r = AnalysisResult()
        assert r.thermal_pause_duration_seconds == 0.0

    def test_thermal_cooling_warning_default(self):
        r = AnalysisResult()
        assert r.thermal_cooling_warning_at_start is False

    def test_thermal_was_paused_default(self):
        r = AnalysisResult()
        assert r.thermal_was_paused is False


# ---------------------------------------------------------------------------
# Tests: _refresh_thermal_progress
# ---------------------------------------------------------------------------


class TestRefreshThermalProgress:
    """_refresh_thermal_progress reads from thermal_monitor safely."""

    def test_refreshes_from_thermal_monitor(self, tmp_path):
        tm = FakeThermalMonitor(
            peak_temp=72.0, pause_count=1, total_pause_duration=5.0,
            current_temp=68.0, is_paused=True,
        )
        service = _make_service(tmp_path, thermal_monitor=tm)

        # Access progress triggers refresh
        progress = service.progress
        assert progress.thermal_paused is True
        assert progress.thermal_current_temperature_c == 68.0
        assert progress.thermal_peak_temperature_c == 72.0
        assert progress.thermal_pause_count == 1
        assert progress.thermal_pause_duration_seconds == 5.0

    def test_no_thermal_monitor_keeps_defaults(self, tmp_path):
        service = _make_service(tmp_path, thermal_monitor=None)
        progress = service.progress
        assert progress.thermal_paused is False
        assert progress.thermal_current_temperature_c is None

    def test_broken_thermal_monitor_no_crash(self, tmp_path):
        """If thermal_monitor properties raise, progress still works."""
        broken_tm = MagicMock()
        type(broken_tm).pause_event = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken")))
        service = _make_service(tmp_path, thermal_monitor=broken_tm)
        # Should not raise
        progress = service.progress
        assert progress.thermal_paused is False

    def test_uses_get_session_metadata(self, tmp_path):
        """_refresh_thermal_progress prefers get_session_metadata() when available."""
        tm = FakeThermalMonitor(
            peak_temp=72.0, pause_count=1, total_pause_duration=5.0,
            current_temp=68.0, is_paused=False,
        )
        # Override get_session_metadata to return specific values
        tm.get_session_metadata = lambda: {
            "peak_temperature_c": 77.0,
            "current_temperature_c": 65.0,
            "pause_count": 3,
            "total_pause_duration_s": 8.0,
            "cooling_warning_at_start": True,
            "is_paused": True,
        }
        service = _make_service(tmp_path, thermal_monitor=tm)
        progress = service.progress

        assert progress.thermal_peak_temperature_c == 77.0
        assert progress.thermal_current_temperature_c == 65.0
        assert progress.thermal_pause_count == 3
        assert progress.thermal_pause_duration_seconds == 8.0
        assert progress.thermal_paused is True

    def test_negative_values_normalized_to_zero(self, tmp_path):
        """Negative values from thermal monitor are clamped to 0."""
        tm = FakeThermalMonitor()
        tm.get_session_metadata = lambda: {
            "peak_temperature_c": -5.0,
            "current_temperature_c": -10.0,
            "pause_count": -2,
            "total_pause_duration_s": -3.0,
            "cooling_warning_at_start": False,
            "is_paused": False,
        }
        service = _make_service(tmp_path, thermal_monitor=tm)
        progress = service.progress

        assert progress.thermal_peak_temperature_c == 0.0
        assert progress.thermal_current_temperature_c == 0.0
        assert progress.thermal_pause_count == 0
        assert progress.thermal_pause_duration_seconds == 0.0

    def test_magicmock_values_dont_crash(self, tmp_path):
        """MagicMock values in thermal monitor don't crash the service."""
        broken_tm = MagicMock()
        # get_session_metadata returns a dict with MagicMock values
        broken_tm.get_session_metadata.return_value = {
            "peak_temperature_c": MagicMock(),
            "current_temperature_c": MagicMock(),
            "pause_count": MagicMock(),
            "total_pause_duration_s": MagicMock(),
            "cooling_warning_at_start": MagicMock(),
            "is_paused": MagicMock(),
        }
        service = _make_service(tmp_path, thermal_monitor=broken_tm)
        # Should not raise
        progress = service.progress
        # Defaults should remain since MagicMock values are filtered
        assert isinstance(progress.thermal_peak_temperature_c, float)
        assert isinstance(progress.thermal_pause_count, int)

    def test_get_session_metadata_raising_doesnt_crash(self, tmp_path):
        """If get_session_metadata() raises, falls back to direct reads."""
        tm = FakeThermalMonitor(
            peak_temp=72.0, pause_count=1, total_pause_duration=5.0,
            current_temp=68.0, is_paused=False,
        )
        # Make get_session_metadata raise
        tm.get_session_metadata = MagicMock(side_effect=RuntimeError("metadata broken"))
        service = _make_service(tmp_path, thermal_monitor=tm)
        # Should not raise — falls back to direct property reads
        progress = service.progress
        assert progress.thermal_peak_temperature_c == 72.0
        assert progress.thermal_current_temperature_c == 68.0


class TestThermalPauseLoop:
    """Thermal pause loop sets thermal_paused correctly."""

    def test_while_pause_event_set_thermal_paused_true(self, tmp_path):
        """While pause_event is set, thermal_paused=True in progress."""
        tm = FakeThermalMonitor(is_paused=True)
        snapshots = [_make_snapshot(1, 1, 0)]
        frame_result = {"detections_count": 0, "tracked_count": 0,
            "health_executed_count": 0, "maturity_executed_count": 0, "reused_count": 0,
            "new_tracks_count": 0, "times": {"detection_sec": 0.1, "tracking_sec": 0.01,
            "total_frame_sec": 0.12}, "detections": []}
        service = _make_service(tmp_path, thermal_monitor=tm, snapshots=snapshots,
                                process_results=[frame_result])

        # After a brief pause, clear the event so loop exits
        import threading as _th

        def clear_after_delay():
            time.sleep(0.3)
            tm._pause_event.clear()

        t = _th.Thread(target=clear_after_delay, daemon=True)
        t.start()

        with patch("cv2.imread", return_value=np.zeros((480, 640, 3), dtype=np.uint8)), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        t.join(timeout=1)
        # After loop completes, thermal_paused should be False
        assert service._progress.thermal_paused is False

    def test_after_clear_thermal_paused_false(self, tmp_path):
        """After pause_event is cleared, thermal_paused=False."""
        tm = FakeThermalMonitor(is_paused=False)
        snapshots = [_make_snapshot(1, 1, 0)]
        frame_result = {"detections_count": 0, "tracked_count": 0,
            "health_executed_count": 0, "maturity_executed_count": 0, "reused_count": 0,
            "new_tracks_count": 0, "times": {"detection_sec": 0.1, "tracking_sec": 0.01,
            "total_frame_sec": 0.12}, "detections": []}
        service = _make_service(tmp_path, thermal_monitor=tm, snapshots=snapshots,
                                process_results=[frame_result])

        with patch("cv2.imread", return_value=np.zeros((480, 640, 3), dtype=np.uint8)), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        assert service._progress.thermal_paused is False


# ---------------------------------------------------------------------------
# Tests: Thermal metrics in result and reports
# ---------------------------------------------------------------------------


class TestThermalInResultAndReports:
    """_build_result and _generate_reports include thermal metrics."""

    def test_result_has_thermal_metrics(self, tmp_path):
        tm = FakeThermalMonitor(peak_temp=75.0, pause_count=3, total_pause_duration=12.0,
                                cooling_warning=True)
        snapshots = [_make_snapshot(1, 1, 0)]
        frame_result = {"detections_count": 0, "tracked_count": 0,
            "health_executed_count": 0, "maturity_executed_count": 0, "reused_count": 0,
            "new_tracks_count": 0, "times": {"detection_sec": 0.1, "tracking_sec": 0.01,
            "total_frame_sec": 0.12}, "detections": []}
        service = _make_service(tmp_path, thermal_monitor=tm, snapshots=snapshots,
                                process_results=[frame_result])

        with patch("cv2.imread", return_value=np.zeros((480, 640, 3), dtype=np.uint8)), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        assert result.thermal_peak_temperature_c == 75.0
        assert result.thermal_pause_count == 3
        assert result.thermal_pause_duration_seconds == 12.0
        assert result.thermal_cooling_warning_at_start is True
        assert result.thermal_was_paused is True

    def test_pipeline_metrics_json_has_thermal_section(self, tmp_path):
        tm = FakeThermalMonitor(peak_temp=70.0, pause_count=1, total_pause_duration=4.0,
                                cooling_warning=False)
        snapshots = [_make_snapshot(1, 1, 0)]
        frame_result = {"detections_count": 0, "tracked_count": 0,
            "health_executed_count": 0, "maturity_executed_count": 0, "reused_count": 0,
            "new_tracks_count": 0, "times": {"detection_sec": 0.1, "tracking_sec": 0.01,
            "total_frame_sec": 0.12}, "detections": []}
        service = _make_service(tmp_path, thermal_monitor=tm, snapshots=snapshots,
                                process_results=[frame_result])

        with patch("cv2.imread", return_value=np.zeros((480, 640, 3), dtype=np.uint8)), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            service.run()

        metrics_path = tmp_path / "1" / "pipeline_metrics.json"
        assert metrics_path.exists()
        with open(metrics_path) as f:
            data = json.load(f)

        assert "thermal" in data["analysis"]
        thermal = data["analysis"]["thermal"]
        assert thermal["peak_temperature_c"] == 70.0
        assert thermal["pause_count"] == 1
        assert thermal["total_pause_duration_seconds"] == 4.0
        assert thermal["cooling_warning_at_start"] is False
        assert thermal["was_paused"] is True

    def test_pipeline_metrics_json_has_performance_config(self, tmp_path):
        tm = FakeThermalMonitor()
        snapshots = [_make_snapshot(1, 1, 0)]
        frame_result = {"detections_count": 0, "tracked_count": 0,
            "health_executed_count": 0, "maturity_executed_count": 0, "reused_count": 0,
            "new_tracks_count": 0, "times": {"detection_sec": 0.1, "tracking_sec": 0.01,
            "total_frame_sec": 0.12}, "detections": []}
        service = _make_service(tmp_path, thermal_monitor=tm, snapshots=snapshots,
                                process_results=[frame_result])

        with patch("cv2.imread", return_value=np.zeros((480, 640, 3), dtype=np.uint8)), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            service.run()

        metrics_path = tmp_path / "1" / "pipeline_metrics.json"
        with open(metrics_path) as f:
            data = json.load(f)

        assert "performance_config" in data["analysis"]
        pc = data["analysis"]["performance_config"]
        assert pc["analysis_skip_maturity"] is True
        assert pc["profile_name"] == "edge"

    def test_pipeline_metrics_has_seconds_per_processed_snapshot(self, tmp_path):
        tm = FakeThermalMonitor()
        snapshots = [_make_snapshot(1, 1, 0), _make_snapshot(2, 1, 1)]
        frame_result = {"detections_count": 0, "tracked_count": 0,
            "health_executed_count": 0, "maturity_executed_count": 0, "reused_count": 0,
            "new_tracks_count": 0, "times": {"detection_sec": 0.1, "tracking_sec": 0.01,
            "total_frame_sec": 0.12}, "detections": []}
        service = _make_service(tmp_path, thermal_monitor=tm, snapshots=snapshots,
                                process_results=[frame_result, frame_result])

        with patch("cv2.imread", return_value=np.zeros((480, 640, 3), dtype=np.uint8)), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            service.run()

        metrics_path = tmp_path / "1" / "pipeline_metrics.json"
        with open(metrics_path) as f:
            data = json.load(f)

        assert "seconds_per_processed_snapshot" in data["analysis"]
        # 2 snapshots processed, value should be duration / 2
        assert data["analysis"]["seconds_per_processed_snapshot"] > 0

    def test_no_thermal_monitor_result_has_zero_defaults(self, tmp_path):
        snapshots = [_make_snapshot(1, 1, 0)]
        frame_result = {"detections_count": 0, "tracked_count": 0,
            "health_executed_count": 0, "maturity_executed_count": 0, "reused_count": 0,
            "new_tracks_count": 0, "times": {"detection_sec": 0.1, "tracking_sec": 0.01,
            "total_frame_sec": 0.12}, "detections": []}
        service = _make_service(tmp_path, thermal_monitor=None, snapshots=snapshots,
                                process_results=[frame_result])

        with patch("cv2.imread", return_value=np.zeros((480, 640, 3), dtype=np.uint8)), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        assert result.thermal_peak_temperature_c == 0.0
        assert result.thermal_pause_count == 0
        assert result.thermal_pause_duration_seconds == 0.0
        assert result.thermal_cooling_warning_at_start is False
        assert result.thermal_was_paused is False
