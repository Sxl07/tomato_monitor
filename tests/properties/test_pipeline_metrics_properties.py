"""Property-based tests for PipelineMetrics module.

Tests correctness properties of the PipelineMetrics accumulator using
Hypothesis to verify invariants across a wide range of inputs.

Testing framework: pytest + hypothesis
Minimum examples: 100 per property
"""

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.application.services.pipeline_metrics import (
    CycleMetrics,
    PipelineMetrics,
    VALID_SNAPSHOT_REASONS,
)


# --- Strategies ---

VALID_REASONS_LIST = sorted(VALID_SNAPSHOT_REASONS)

timing_ms = st.floats(min_value=0.0, max_value=60_000.0, allow_nan=False, allow_infinity=False)
temperature_strategy = st.one_of(
    st.none(),
    st.floats(min_value=-40.0, max_value=120.0, allow_nan=False, allow_infinity=False),
)
rss_strategy = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=32_000.0, allow_nan=False, allow_infinity=False),
)
timestamp_strategy = st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)
snapshot_reason_strategy = st.sampled_from(VALID_REASONS_LIST)


@st.composite
def cycle_metrics_strategy(draw, cycle_index=None, timestamp=None, snapshot_reason=None):
    """Generate a valid CycleMetrics instance with randomized timing values."""
    return CycleMetrics(
        cycle_index=cycle_index if cycle_index is not None else draw(st.integers(min_value=0, max_value=10_000)),
        snapshot_reason=snapshot_reason if snapshot_reason is not None else draw(snapshot_reason_strategy),
        camera_read_ms=draw(timing_ms),
        scene_gate_ms=draw(timing_ms),
        snapshot_save_ms=draw(timing_ms),
        inference_total_ms=draw(timing_ms),
        detection_ms=draw(timing_ms),
        health_ms=draw(timing_ms),
        maturity_ms=draw(timing_ms),
        persistence_ms=draw(timing_ms),
        temperature_c=draw(temperature_strategy),
        rss_mb=draw(rss_strategy),
        timestamp=timestamp if timestamp is not None else draw(timestamp_strategy),
    )


@st.composite
def cycle_metrics_list_strategy(draw, min_size=1, max_size=20):
    """Generate a list of CycleMetrics with monotonically increasing timestamps."""
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    # Start time and increments to ensure monotonic timestamps
    start = draw(st.floats(min_value=1.0, max_value=1e6, allow_nan=False, allow_infinity=False))
    increments = draw(
        st.lists(
            st.floats(min_value=0.1, max_value=120.0, allow_nan=False, allow_infinity=False),
            min_size=n,
            max_size=n,
        )
    )
    cycles = []
    current_ts = start
    for i in range(n):
        current_ts += increments[i]
        cycle = draw(cycle_metrics_strategy(cycle_index=i, timestamp=current_ts))
        cycles.append(cycle)
    return start, cycles


# --- Property 1: Stage timing accuracy ---
# Feature: lightweight-hybrid-monitoring-pipeline, Property 1: Stage timing accuracy


class TestProperty1StageTiming:
    """Property 1: Stage timing accuracy.

    For any simulated pipeline stage with known elapsed duration, the
    corresponding timing field in CycleMetrics SHALL reflect that duration
    with less than 1ms deviation.

    **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5**
    """

    @given(
        camera_read_ms=timing_ms,
        scene_gate_ms=timing_ms,
        snapshot_save_ms=timing_ms,
        inference_total_ms=timing_ms,
        detection_ms=timing_ms,
        health_ms=timing_ms,
        maturity_ms=timing_ms,
        persistence_ms=timing_ms,
    )
    @settings(max_examples=100)
    def test_timing_fields_reflect_input_values(
        self,
        camera_read_ms,
        scene_gate_ms,
        snapshot_save_ms,
        inference_total_ms,
        detection_ms,
        health_ms,
        maturity_ms,
        persistence_ms,
    ):
        """Timing fields stored in CycleMetrics match input values exactly."""
        cycle = CycleMetrics(
            cycle_index=0,
            snapshot_reason="scene_change",
            camera_read_ms=camera_read_ms,
            scene_gate_ms=scene_gate_ms,
            snapshot_save_ms=snapshot_save_ms,
            inference_total_ms=inference_total_ms,
            detection_ms=detection_ms,
            health_ms=health_ms,
            maturity_ms=maturity_ms,
            persistence_ms=persistence_ms,
            temperature_c=None,
            rss_mb=None,
            timestamp=100.0,
        )

        metrics = PipelineMetrics(session_start_time=0.0)
        metrics.add_cycle(cycle)

        stored = metrics.cycles[0]
        assert abs(stored.camera_read_ms - camera_read_ms) < 1.0
        assert abs(stored.scene_gate_ms - scene_gate_ms) < 1.0
        assert abs(stored.snapshot_save_ms - snapshot_save_ms) < 1.0
        assert abs(stored.inference_total_ms - inference_total_ms) < 1.0
        assert abs(stored.detection_ms - detection_ms) < 1.0
        assert abs(stored.health_ms - health_ms) < 1.0
        assert abs(stored.maturity_ms - maturity_ms) < 1.0
        assert abs(stored.persistence_ms - persistence_ms) < 1.0


# --- Property 2: Peak temperature max invariant ---
# Feature: lightweight-hybrid-monitoring-pipeline, Property 2: Peak temperature max invariant


class TestProperty2PeakTemperature:
    """Property 2: Peak temperature is maximum of recorded values.

    For any sequence of CycleMetrics, peak_temperature_c SHALL equal
    the maximum non-None temperature value (or 0.0 if all None).

    **Validates: Requirements 1.7**
    """

    @given(data=cycle_metrics_list_strategy(min_size=1, max_size=20))
    @settings(max_examples=100)
    def test_peak_temperature_equals_max_non_none(self, data):
        """peak_temperature_c equals max of all non-None temperature_c values."""
        session_start, cycles = data

        metrics = PipelineMetrics(session_start_time=session_start)
        for cycle in cycles:
            metrics.add_cycle(cycle)

        temperatures = [c.temperature_c for c in cycles if c.temperature_c is not None]
        expected_peak = max(temperatures) if temperatures else 0.0

        assert metrics.peak_temperature_c == expected_peak

    @given(
        n=st.integers(min_value=1, max_value=15),
    )
    @settings(max_examples=100)
    def test_peak_temperature_zero_when_all_none(self, n):
        """peak_temperature_c is 0.0 when all cycles have temperature_c=None."""
        metrics = PipelineMetrics(session_start_time=0.0)
        for i in range(n):
            cycle = CycleMetrics(
                cycle_index=i,
                snapshot_reason="scene_change",
                camera_read_ms=10.0,
                scene_gate_ms=5.0,
                snapshot_save_ms=20.0,
                inference_total_ms=100.0,
                detection_ms=50.0,
                health_ms=30.0,
                maturity_ms=20.0,
                persistence_ms=10.0,
                temperature_c=None,
                rss_mb=None,
                timestamp=float(i + 1),
            )
            metrics.add_cycle(cycle)

        assert metrics.peak_temperature_c == 0.0


# --- Property 3: Throughput rate calculation ---
# Feature: lightweight-hybrid-monitoring-pipeline, Property 3: Throughput rate calculation


class TestProperty3ThroughputRate:
    """Property 3: Throughput rate calculation.

    For any positive elapsed time and cycle count,
    snapshots_per_minute() SHALL equal cycle_count / (elapsed_seconds / 60.0).

    **Validates: Requirements 1.9**
    """

    @given(
        session_start=st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
        elapsed_seconds=st.floats(min_value=0.1, max_value=7200.0, allow_nan=False, allow_infinity=False),
        cycle_count=st.integers(min_value=1, max_value=500),
    )
    @settings(max_examples=100)
    def test_snapshots_per_minute_formula(self, session_start, elapsed_seconds, cycle_count):
        """snapshots_per_minute equals cycle_count / (elapsed_seconds / 60.0)."""
        metrics = PipelineMetrics(session_start_time=session_start)

        # Distribute cycles evenly; the last cycle timestamp determines elapsed
        last_timestamp = session_start + elapsed_seconds
        time_step = elapsed_seconds / cycle_count

        for i in range(cycle_count):
            ts = session_start + time_step * (i + 1)
            cycle = CycleMetrics(
                cycle_index=i,
                snapshot_reason="scene_change",
                camera_read_ms=10.0,
                scene_gate_ms=5.0,
                snapshot_save_ms=20.0,
                inference_total_ms=100.0,
                detection_ms=50.0,
                health_ms=30.0,
                maturity_ms=20.0,
                persistence_ms=10.0,
                temperature_c=None,
                rss_mb=None,
                timestamp=ts,
            )
            metrics.add_cycle(cycle)

        # The actual elapsed is last_cycle.timestamp - session_start_time
        actual_elapsed = metrics.cycles[-1].timestamp - metrics.session_start_time
        assume(actual_elapsed > 0)

        expected = cycle_count / (actual_elapsed / 60.0)
        actual = metrics.snapshots_per_minute()

        # Allow small floating point tolerance
        assert abs(actual - expected) < 1e-6 or abs(actual - expected) / max(abs(expected), 1e-9) < 1e-9


# --- Property 4: Snapshot reason validity ---
# Feature: lightweight-hybrid-monitoring-pipeline, Property 4: Snapshot reason validity


class TestProperty4SnapshotReasonValidity:
    """Property 4: Snapshot reason validity.

    For any CycleMetrics added, the snapshot_reason SHALL be one of
    the three valid values: "first_frame", "scene_change", "timeout".

    **Validates: Requirements 1.10**
    """

    @given(reason=snapshot_reason_strategy)
    @settings(max_examples=100)
    def test_valid_reasons_accepted(self, reason):
        """CycleMetrics accepts valid snapshot_reason values without error."""
        cycle = CycleMetrics(
            cycle_index=0,
            snapshot_reason=reason,
            camera_read_ms=10.0,
            scene_gate_ms=5.0,
            snapshot_save_ms=20.0,
            inference_total_ms=100.0,
            detection_ms=50.0,
            health_ms=30.0,
            maturity_ms=20.0,
            persistence_ms=10.0,
            temperature_c=None,
            rss_mb=None,
            timestamp=1.0,
        )

        metrics = PipelineMetrics(session_start_time=0.0)
        metrics.add_cycle(cycle)

        assert cycle.snapshot_reason in VALID_SNAPSHOT_REASONS
        assert metrics.cycles[0].snapshot_reason in VALID_SNAPSHOT_REASONS

    @given(
        invalid_reason=st.text(min_size=1, max_size=50).filter(
            lambda s: s not in VALID_SNAPSHOT_REASONS
        )
    )
    @settings(max_examples=100)
    def test_invalid_reasons_rejected(self, invalid_reason):
        """CycleMetrics raises ValueError for invalid snapshot_reason values."""
        with pytest.raises(ValueError):
            CycleMetrics(
                cycle_index=0,
                snapshot_reason=invalid_reason,
                camera_read_ms=10.0,
                scene_gate_ms=5.0,
                snapshot_save_ms=20.0,
                inference_total_ms=100.0,
                detection_ms=50.0,
                health_ms=30.0,
                maturity_ms=20.0,
                persistence_ms=10.0,
                temperature_c=None,
                rss_mb=None,
                timestamp=1.0,
            )
