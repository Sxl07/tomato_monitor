"""Unit tests for PipelineMetrics and CycleMetrics.

Tests verify:
- CycleMetrics validates snapshot_reason.
- PipelineMetrics accumulates cycles correctly.
- Peak temperature and RSS tracking.
- Throughput rate calculations.
- Session summary structure.
- JSON file output.
"""

import json
import os
import tempfile

import pytest

from src.application.services.pipeline_metrics import (
    CycleMetrics,
    PipelineMetrics,
    VALID_SNAPSHOT_REASONS,
)


def _make_cycle(
    cycle_index: int = 0,
    snapshot_reason: str = "scene_change",
    camera_read_ms: float = 10.0,
    scene_gate_ms: float = 5.0,
    snapshot_save_ms: float = 15.0,
    inference_total_ms: float = 800.0,
    detection_ms: float = 500.0,
    health_ms: float = 200.0,
    maturity_ms: float = 100.0,
    persistence_ms: float = 20.0,
    temperature_c: float = 65.0,
    rss_mb: float = 1500.0,
    timestamp: float = 10.0,
) -> CycleMetrics:
    """Helper to create a CycleMetrics with defaults."""
    return CycleMetrics(
        cycle_index=cycle_index,
        snapshot_reason=snapshot_reason,
        camera_read_ms=camera_read_ms,
        scene_gate_ms=scene_gate_ms,
        snapshot_save_ms=snapshot_save_ms,
        inference_total_ms=inference_total_ms,
        detection_ms=detection_ms,
        health_ms=health_ms,
        maturity_ms=maturity_ms,
        persistence_ms=persistence_ms,
        temperature_c=temperature_c,
        rss_mb=rss_mb,
        timestamp=timestamp,
    )


class TestCycleMetrics:
    """Test CycleMetrics validation and construction."""

    def test_valid_reasons_accepted(self):
        for reason in VALID_SNAPSHOT_REASONS:
            cycle = _make_cycle(snapshot_reason=reason)
            assert cycle.snapshot_reason == reason

    def test_invalid_reason_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid snapshot_reason"):
            _make_cycle(snapshot_reason="invalid_reason")

    def test_empty_reason_raises_value_error(self):
        with pytest.raises(ValueError):
            _make_cycle(snapshot_reason="")

    def test_fields_stored_correctly(self):
        cycle = _make_cycle(
            cycle_index=5,
            camera_read_ms=12.5,
            temperature_c=70.2,
        )
        assert cycle.cycle_index == 5
        assert cycle.camera_read_ms == 12.5
        assert cycle.temperature_c == 70.2

    def test_none_temperature_and_rss_allowed(self):
        cycle = _make_cycle(temperature_c=None, rss_mb=None)
        assert cycle.temperature_c is None
        assert cycle.rss_mb is None


class TestPipelineMetrics:
    """Test PipelineMetrics accumulation and calculations."""

    def test_empty_metrics(self):
        metrics = PipelineMetrics(session_start_time=0.0)
        assert len(metrics.cycles) == 0
        assert metrics.snapshots_per_minute() == 0.0
        assert metrics.inferences_per_minute() == 0.0
        assert metrics.peak_temperature_c == 0.0
        assert metrics.peak_rss_mb == 0.0

    def test_add_cycle(self):
        metrics = PipelineMetrics(session_start_time=0.0)
        cycle = _make_cycle(cycle_index=0, timestamp=10.0)
        metrics.add_cycle(cycle)
        assert len(metrics.cycles) == 1
        assert metrics.cycles[0] is cycle

    def test_peak_temperature_tracking(self):
        metrics = PipelineMetrics(session_start_time=0.0)
        metrics.add_cycle(_make_cycle(temperature_c=60.0, timestamp=5.0))
        metrics.add_cycle(_make_cycle(temperature_c=75.0, timestamp=10.0))
        metrics.add_cycle(_make_cycle(temperature_c=68.0, timestamp=15.0))
        assert metrics.peak_temperature_c == 75.0

    def test_peak_temperature_with_none_values(self):
        metrics = PipelineMetrics(session_start_time=0.0)
        metrics.add_cycle(_make_cycle(temperature_c=None, timestamp=5.0))
        metrics.add_cycle(_make_cycle(temperature_c=70.0, timestamp=10.0))
        metrics.add_cycle(_make_cycle(temperature_c=None, timestamp=15.0))
        assert metrics.peak_temperature_c == 70.0

    def test_peak_temperature_all_none(self):
        metrics = PipelineMetrics(session_start_time=0.0)
        metrics.add_cycle(_make_cycle(temperature_c=None, timestamp=5.0))
        metrics.add_cycle(_make_cycle(temperature_c=None, timestamp=10.0))
        assert metrics.peak_temperature_c == 0.0

    def test_peak_rss_tracking(self):
        metrics = PipelineMetrics(session_start_time=0.0)
        metrics.add_cycle(_make_cycle(rss_mb=1000.0, timestamp=5.0))
        metrics.add_cycle(_make_cycle(rss_mb=2000.0, timestamp=10.0))
        metrics.add_cycle(_make_cycle(rss_mb=1500.0, timestamp=15.0))
        assert metrics.peak_rss_mb == 2000.0

    def test_snapshots_per_minute(self):
        metrics = PipelineMetrics(session_start_time=0.0)
        # 3 cycles over 60 seconds = 3 per minute
        metrics.add_cycle(_make_cycle(timestamp=20.0))
        metrics.add_cycle(_make_cycle(timestamp=40.0))
        metrics.add_cycle(_make_cycle(timestamp=60.0))
        assert metrics.snapshots_per_minute() == pytest.approx(3.0, rel=0.01)

    def test_snapshots_per_minute_30_seconds(self):
        metrics = PipelineMetrics(session_start_time=0.0)
        # 2 cycles over 30 seconds = 4 per minute
        metrics.add_cycle(_make_cycle(timestamp=15.0))
        metrics.add_cycle(_make_cycle(timestamp=30.0))
        assert metrics.snapshots_per_minute() == pytest.approx(4.0, rel=0.01)

    def test_inferences_per_minute_equals_snapshots(self):
        metrics = PipelineMetrics(session_start_time=0.0)
        metrics.add_cycle(_make_cycle(timestamp=30.0))
        metrics.add_cycle(_make_cycle(timestamp=60.0))
        assert metrics.inferences_per_minute() == metrics.snapshots_per_minute()


class TestSessionSummary:
    """Test get_session_summary() output structure."""

    def test_empty_summary(self):
        metrics = PipelineMetrics(session_start_time=0.0)
        summary = metrics.get_session_summary()
        assert summary["total_cycles"] == 0
        assert summary["snapshots_per_minute"] == 0.0
        assert summary["average_timings_ms"] == {}
        assert summary["snapshot_reasons"] == {}

    def test_summary_with_cycles(self):
        metrics = PipelineMetrics(session_start_time=0.0)
        metrics.add_cycle(_make_cycle(
            snapshot_reason="first_frame",
            camera_read_ms=10.0,
            detection_ms=500.0,
            timestamp=10.0,
        ))
        metrics.add_cycle(_make_cycle(
            snapshot_reason="scene_change",
            camera_read_ms=12.0,
            detection_ms=600.0,
            timestamp=20.0,
        ))

        summary = metrics.get_session_summary()
        assert summary["total_cycles"] == 2
        assert summary["average_timings_ms"]["camera_read_ms"] == 11.0
        assert summary["average_timings_ms"]["detection_ms"] == 550.0
        assert summary["snapshot_reasons"] == {"first_frame": 1, "scene_change": 1, "timeout": 0}

    def test_summary_has_all_required_keys(self):
        metrics = PipelineMetrics(session_start_time=0.0)
        metrics.add_cycle(_make_cycle(timestamp=10.0))
        summary = metrics.get_session_summary()

        required_keys = {
            "total_cycles", "snapshots_per_minute", "inferences_per_minute",
            "peak_temperature_c", "peak_rss_mb", "average_timings_ms",
            "snapshot_reasons",
        }
        assert required_keys.issubset(set(summary.keys()))

    def test_average_timings_has_all_stage_keys(self):
        metrics = PipelineMetrics(session_start_time=0.0)
        metrics.add_cycle(_make_cycle(timestamp=10.0))
        summary = metrics.get_session_summary()

        timing_keys = {
            "camera_read_ms", "scene_gate_ms", "snapshot_save_ms",
            "inference_total_ms", "detection_ms", "health_ms",
            "maturity_ms", "persistence_ms",
        }
        assert timing_keys.issubset(set(summary["average_timings_ms"].keys()))


class TestJsonOutput:
    """Test to_json_file() writes valid JSON with required fields."""

    def test_writes_valid_json_file(self):
        metrics = PipelineMetrics(session_start_time=0.0)
        metrics.add_cycle(_make_cycle(timestamp=30.0))
        metrics.add_cycle(_make_cycle(snapshot_reason="timeout", timestamp=60.0))

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "pipeline_metrics.json")
            metrics.to_json_file(path, profile_name="edge")

            assert os.path.exists(path)
            with open(path) as f:
                data = json.load(f)

            assert data["profile_name"] == "edge"
            assert data["total_cycles"] == 2
            assert "snapshots_per_minute" in data
            assert "peak_temperature_c" in data
            assert "peak_rss_mb" in data
            assert "average_timings_ms" in data
            assert "snapshot_reasons" in data

    def test_writes_json_even_with_zero_cycles(self):
        metrics = PipelineMetrics(session_start_time=0.0)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "pipeline_metrics.json")
            metrics.to_json_file(path, profile_name="full")

            assert os.path.exists(path)
            with open(path) as f:
                data = json.load(f)
            assert data["total_cycles"] == 0
            assert data["profile_name"] == "full"

    def test_creates_output_directory_if_needed(self):
        metrics = PipelineMetrics(session_start_time=0.0)
        metrics.add_cycle(_make_cycle(timestamp=10.0))

        with tempfile.TemporaryDirectory() as tmpdir:
            nested = os.path.join(tmpdir, "sub", "dir", "metrics.json")
            metrics.to_json_file(nested, profile_name="edge")
            assert os.path.exists(nested)
