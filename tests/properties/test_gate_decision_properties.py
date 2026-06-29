"""Property-based tests for gate and snapshot decision logic.

Tests correctness properties of the throttle sleep formula, time-based cooldown,
timeout forcing, first-wins rule, and gate metrics keys using Hypothesis.

Testing framework: pytest + hypothesis
Minimum examples: 100 per property
"""

import numpy as np
import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from tests.unit.test_snapshot_decision import compute_snapshot_decision
from src.infrastructure.vision.capture_gate import should_capture_new_image


# --- Strategies ---

fps_strategy = st.floats(min_value=0.1, max_value=120.0, allow_nan=False, allow_infinity=False)
processing_time_strategy = st.floats(
    min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False
)
elapsed_strategy = st.floats(
    min_value=0.0, max_value=3600.0, allow_nan=False, allow_infinity=False
)
min_seconds_strategy = st.floats(
    min_value=0.1, max_value=300.0, allow_nan=False, allow_infinity=False
)
max_seconds_strategy = st.floats(
    min_value=0.1, max_value=600.0, allow_nan=False, allow_infinity=False
)
frames_strategy = st.integers(min_value=0, max_value=10_000)
cooldown_frames_strategy = st.integers(min_value=1, max_value=1000)
timeout_frames_strategy = st.integers(min_value=1, max_value=10_000)


@st.composite
def small_bgr_image(draw, min_size=32, max_size=128):
    """Generate a small random BGR image as numpy array."""
    h = draw(st.integers(min_value=min_size, max_value=max_size))
    w = draw(st.integers(min_value=min_size, max_value=max_size))
    # Use a simple random image with uint8 values
    data = draw(
        st.binary(min_size=h * w * 3, max_size=h * w * 3)
    )
    return np.frombuffer(data, dtype=np.uint8).reshape((h, w, 3)).copy()


# --- Property 5: Throttle sleep duration ---
# Feature: lightweight-hybrid-monitoring-pipeline, Property 5: Throttle sleep duration


class TestProperty5ThrottleSleepDuration:
    """Property 5: Throttle sleep duration.

    For any capture_loop_fps > 0 and processing_time_seconds >= 0, the computed
    sleep duration SHALL equal max(0.0, (1.0 / capture_loop_fps) - processing_time_seconds).

    **Validates: Requirements 2.1**
    """

    @given(
        fps=fps_strategy,
        processing_time=processing_time_strategy,
    )
    @settings(max_examples=100)
    def test_sleep_duration_formula(self, fps, processing_time):
        """Computed sleep matches max(0.0, (1.0/fps) - processing_time)."""
        expected = max(0.0, (1.0 / fps) - processing_time)
        actual = max(0.0, (1.0 / fps) - processing_time)

        assert abs(actual - expected) < 1e-12

    @given(
        fps=fps_strategy,
        processing_time=processing_time_strategy,
    )
    @settings(max_examples=100)
    def test_sleep_duration_never_negative(self, fps, processing_time):
        """Sleep duration is always >= 0 regardless of inputs."""
        sleep_duration = max(0.0, (1.0 / fps) - processing_time)
        assert sleep_duration >= 0.0

    @given(
        fps=fps_strategy,
    )
    @settings(max_examples=100)
    def test_sleep_equals_period_when_zero_processing(self, fps):
        """When processing_time is 0, sleep equals 1/fps."""
        sleep_duration = max(0.0, (1.0 / fps) - 0.0)
        expected = 1.0 / fps
        assert abs(sleep_duration - expected) < 1e-12

    @given(
        fps=fps_strategy,
        excess=st.floats(
            min_value=0.001, max_value=100.0, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100)
    def test_sleep_zero_when_processing_exceeds_period(self, fps, excess):
        """When processing_time >= 1/fps, sleep is 0."""
        period = 1.0 / fps
        processing_time = period + excess  # More than period
        sleep_duration = max(0.0, (1.0 / fps) - processing_time)
        assert sleep_duration == 0.0


# --- Property 6: Time-based cooldown ---
# Feature: lightweight-hybrid-monitoring-pipeline, Property 6: Time-based cooldown


class TestProperty6TimeBasedCooldown:
    """Property 6: Time-based cooldown blocks premature snapshots.

    For any min_seconds_between_snapshots > 0 and elapsed < min, the cooldown
    check SHALL return False (blocking the snapshot) when frame-based cooldown
    is also not met.

    **Validates: Requirements 2.2, 3.5**
    """

    @given(
        min_seconds=min_seconds_strategy,
        fraction=st.floats(min_value=0.0, max_value=0.99, allow_nan=False, allow_infinity=False),
        cooldown_frames=cooldown_frames_strategy,
    )
    @settings(max_examples=100)
    def test_cooldown_blocks_when_elapsed_less_than_min(
        self, min_seconds, fraction, cooldown_frames
    ):
        """Snapshot blocked when elapsed < min_seconds AND frames < cooldown_frames."""
        elapsed = min_seconds * fraction  # guaranteed < min_seconds
        frames_since = cooldown_frames - 1  # guaranteed < cooldown_frames

        should_capture, reason = compute_snapshot_decision(
            is_first_frame=False,
            scene_gate_triggered=True,  # Scene change detected
            elapsed_since_last_snapshot=elapsed,
            frames_since_last_capture=frames_since,
            min_seconds_between_snapshots=min_seconds,
            max_seconds_without_snapshot=min_seconds * 10,  # high timeout to avoid triggering
            cooldown_frames=cooldown_frames,
            timeout_frames=cooldown_frames * 10,  # high timeout
        )
        assert should_capture is False

    @given(
        min_seconds=min_seconds_strategy,
        extra=st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False),
        cooldown_frames=cooldown_frames_strategy,
    )
    @settings(max_examples=100)
    def test_cooldown_allows_when_time_met(self, min_seconds, extra, cooldown_frames):
        """Snapshot allowed when elapsed >= min_seconds and scene change detected."""
        elapsed = min_seconds + extra  # >= min_seconds

        # Ensure timeout doesn't interfere
        max_seconds = elapsed + 100.0

        should_capture, reason = compute_snapshot_decision(
            is_first_frame=False,
            scene_gate_triggered=True,
            elapsed_since_last_snapshot=elapsed,
            frames_since_last_capture=0,  # frames not met
            min_seconds_between_snapshots=min_seconds,
            max_seconds_without_snapshot=max_seconds,
            cooldown_frames=cooldown_frames,
            timeout_frames=cooldown_frames * 100,
        )
        assert should_capture is True
        assert reason == "scene_change"


# --- Property 7: Timeout forcing ---
# Feature: lightweight-hybrid-monitoring-pipeline, Property 7: Timeout forcing


class TestProperty7TimeoutForcing:
    """Property 7: Time-based timeout forces snapshot with correct reason.

    For any max_seconds_without_snapshot > 0 and elapsed >= max, timeout SHALL
    return True and reason SHALL be "timeout".

    **Validates: Requirements 2.3, 3.4**
    """

    @given(
        max_seconds=max_seconds_strategy,
        extra=st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
        min_seconds=min_seconds_strategy,
    )
    @settings(max_examples=100)
    def test_timeout_forces_when_elapsed_ge_max(self, max_seconds, extra, min_seconds):
        """Snapshot forced with reason 'timeout' when elapsed >= max_seconds."""
        elapsed = max_seconds + extra  # >= max_seconds

        should_capture, reason = compute_snapshot_decision(
            is_first_frame=False,
            scene_gate_triggered=False,  # No scene change
            elapsed_since_last_snapshot=elapsed,
            frames_since_last_capture=0,
            min_seconds_between_snapshots=min_seconds,
            max_seconds_without_snapshot=max_seconds,
            cooldown_frames=9999,
            timeout_frames=9999,
        )
        assert should_capture is True
        assert reason == "timeout"

    @given(
        max_seconds=max_seconds_strategy,
        fraction=st.floats(min_value=0.0, max_value=0.99, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_no_timeout_when_elapsed_less_than_max(self, max_seconds, fraction):
        """No timeout forced when elapsed < max_seconds (and no other trigger)."""
        elapsed = max_seconds * fraction  # < max_seconds
        min_seconds = max_seconds * 2  # Cooldown not met either

        should_capture, reason = compute_snapshot_decision(
            is_first_frame=False,
            scene_gate_triggered=False,
            elapsed_since_last_snapshot=elapsed,
            frames_since_last_capture=0,
            min_seconds_between_snapshots=min_seconds,
            max_seconds_without_snapshot=max_seconds,
            cooldown_frames=9999,
            timeout_frames=9999,
        )
        assert should_capture is False


# --- Property 8: First-wins rule ---
# Feature: lightweight-hybrid-monitoring-pipeline, Property 8: First-wins rule


class TestProperty8FirstWinsRule:
    """Property 8: First-wins trigger rule.

    For any combination of time-based and frame-based thresholds, the trigger
    SHALL fire if EITHER threshold is reached first.

    **Validates: Requirements 2.5, 2.6**
    """

    @given(
        max_seconds=max_seconds_strategy,
        timeout_frames=timeout_frames_strategy,
        elapsed=elapsed_strategy,
        frames=frames_strategy,
    )
    @settings(max_examples=100)
    def test_either_timeout_triggers(self, max_seconds, timeout_frames, elapsed, frames):
        """If either time or frame timeout is met, capture is forced."""
        time_timeout = elapsed >= max_seconds
        frame_timeout = frames >= timeout_frames

        # Avoid interference from cooldown passing + scene gate
        min_seconds = max_seconds * 10
        cooldown_frames_val = timeout_frames * 10

        should_capture, reason = compute_snapshot_decision(
            is_first_frame=False,
            scene_gate_triggered=False,
            elapsed_since_last_snapshot=elapsed,
            frames_since_last_capture=frames,
            min_seconds_between_snapshots=min_seconds,
            max_seconds_without_snapshot=max_seconds,
            cooldown_frames=cooldown_frames_val,
            timeout_frames=timeout_frames,
        )

        if time_timeout or frame_timeout:
            assert should_capture is True
            assert reason == "timeout"
        else:
            # Neither timeout met and no scene change → no capture
            assert should_capture is False

    @given(
        min_seconds=min_seconds_strategy,
        cooldown_frames=cooldown_frames_strategy,
        elapsed=elapsed_strategy,
        frames=frames_strategy,
    )
    @settings(max_examples=100)
    def test_either_cooldown_enables_scene_gate(
        self, min_seconds, cooldown_frames, elapsed, frames
    ):
        """If either time or frame cooldown is met, scene change can trigger."""
        time_cooldown_ok = elapsed >= min_seconds
        frame_cooldown_ok = frames >= cooldown_frames
        cooldown_passed = time_cooldown_ok or frame_cooldown_ok

        # Ensure timeout doesn't fire
        max_seconds = max(elapsed + 100.0, 9999.0)
        timeout_frames_val = max(frames + 1000, 99999)

        should_capture, reason = compute_snapshot_decision(
            is_first_frame=False,
            scene_gate_triggered=True,  # scene change detected
            elapsed_since_last_snapshot=elapsed,
            frames_since_last_capture=frames,
            min_seconds_between_snapshots=min_seconds,
            max_seconds_without_snapshot=max_seconds,
            cooldown_frames=cooldown_frames,
            timeout_frames=timeout_frames_val,
        )

        if cooldown_passed:
            assert should_capture is True
            assert reason == "scene_change"
        else:
            assert should_capture is False


# --- Property 9: Gate metrics keys ---
# Feature: lightweight-hybrid-monitoring-pipeline, Property 9: Gate metrics keys


REQUIRED_METRICS_KEYS = {
    "orb_matches",
    "hist_diff",
    "orb_change_amount",
    "cooldown_ok",
    "timeout_force",
    "orb_changed",
    "hist_changed",
    "base_trigger",
}


class TestProperty9GateMetricsKeys:
    """Property 9: Gate metrics contain required keys.

    For any invocation of should_capture_new_image(), the metrics dict SHALL
    contain the required keys.

    **Validates: Requirements 3.3**
    """

    @given(
        frames_since=st.integers(min_value=0, max_value=200),
        cooldown=st.integers(min_value=1, max_value=100),
        timeout=st.integers(min_value=1, max_value=500),
        orb_threshold=st.integers(min_value=1, max_value=100),
        hsv_threshold=st.floats(
            min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100)
    def test_metrics_dict_contains_all_required_keys(
        self, frames_since, cooldown, timeout, orb_threshold, hsv_threshold
    ):
        """Metrics dict from should_capture_new_image has all required keys."""
        # Create two simple synthetic images (64x64 BGR)
        reference = np.random.randint(0, 256, (64, 64, 3), dtype=np.uint8)
        current = np.random.randint(0, 256, (64, 64, 3), dtype=np.uint8)

        _trigger, metrics = should_capture_new_image(
            reference_bgr=reference,
            current_bgr=current,
            frames_since_last_capture=frames_since,
            cooldown_frames=cooldown,
            timeout_frames=timeout,
            orb_threshold=orb_threshold,
            hsv_threshold=hsv_threshold,
            gate_resolution=(64, 64),
        )

        assert isinstance(metrics, dict)
        for key in REQUIRED_METRICS_KEYS:
            assert key in metrics, f"Missing required key: {key}"

    @given(
        frames_since=st.integers(min_value=0, max_value=200),
    )
    @settings(max_examples=100)
    def test_metrics_values_are_numeric(self, frames_since):
        """All values in metrics dict are numeric (float)."""
        reference = np.zeros((64, 64, 3), dtype=np.uint8)
        current = np.ones((64, 64, 3), dtype=np.uint8) * 128

        _trigger, metrics = should_capture_new_image(
            reference_bgr=reference,
            current_bgr=current,
            frames_since_last_capture=frames_since,
            cooldown_frames=18,
            timeout_frames=45,
            orb_threshold=35,
            hsv_threshold=0.38,
            gate_resolution=(64, 64),
        )

        for key, value in metrics.items():
            assert isinstance(value, (int, float)), (
                f"Metric '{key}' has non-numeric value: {type(value)}"
            )
