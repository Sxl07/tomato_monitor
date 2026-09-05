"""Unit tests for capture_gate.py with configurable gate_resolution.

Tests verify:
- should_capture_new_image() works with custom gate_resolution.
- Metrics dict contains all required keys.
- None/default gate_resolution falls back to (320, 320).
- Timeout forces capture.
- Cooldown blocks premature capture.
"""

import numpy as np
import pytest

from src.infrastructure.vision.capture_gate import (
    should_capture_new_image,
    should_run_detector_by_scene_change,
    preprocess_for_scene_compare,
)
from src.infrastructure.config.thresholds import (
    MIN_FRAMES_BETWEEN_CAPTURES,
    MAX_FRAMES_WITHOUT_CAPTURE,
)


def _make_random_frame(width=640, height=480):
    """Create a random BGR frame."""
    return np.random.randint(0, 255, (height, width, 3), dtype=np.uint8)


def _make_solid_frame(color, width=640, height=480):
    """Create a solid-color BGR frame."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = color
    return frame


REQUIRED_METRICS_KEYS = {
    "orb_matches", "hist_diff", "cooldown_ok", "timeout_force",
    "orb_changed", "hist_changed", "base_trigger",
}


class TestGateResolutionParameter:
    """Test that gate_resolution parameter is accepted and used."""

    def test_default_resolution_320x320(self):
        ref = _make_random_frame()
        cur = _make_random_frame()
        # Should not raise — uses default (320, 320)
        trigger, metrics = should_capture_new_image(
            ref, cur, frames_since_last_capture=20,
        )
        assert isinstance(trigger, bool)
        assert isinstance(metrics, dict)

    def test_custom_resolution_160x160(self):
        ref = _make_random_frame()
        cur = _make_random_frame()
        trigger, metrics = should_capture_new_image(
            ref, cur, frames_since_last_capture=20,
            gate_resolution=(160, 160),
        )
        assert isinstance(trigger, bool)
        assert isinstance(metrics, dict)

    def test_custom_resolution_80x80(self):
        ref = _make_random_frame()
        cur = _make_random_frame()
        trigger, metrics = should_capture_new_image(
            ref, cur, frames_since_last_capture=20,
            gate_resolution=(80, 80),
        )
        assert isinstance(trigger, bool)

    def test_preprocess_uses_gate_resolution(self):
        frame = _make_random_frame(640, 480)
        result = preprocess_for_scene_compare(frame, gate_resolution=(160, 160))
        assert result.shape[0] == 160
        assert result.shape[1] == 160

    def test_preprocess_default_is_320x320(self):
        frame = _make_random_frame(640, 480)
        result = preprocess_for_scene_compare(frame)
        assert result.shape[0] == 320
        assert result.shape[1] == 320

    def test_none_gate_resolution_falls_back_to_default(self):
        """Explicitly passing None should fall back to (320, 320)."""
        frame = _make_random_frame(640, 480)
        result = preprocess_for_scene_compare(frame, gate_resolution=None)
        assert result.shape[0] == 320
        assert result.shape[1] == 320

    def test_should_capture_with_none_gate_resolution(self):
        """should_capture_new_image with None gate_resolution uses default."""
        ref = _make_random_frame()
        cur = _make_random_frame()
        trigger, metrics = should_capture_new_image(
            ref, cur, frames_since_last_capture=20,
            gate_resolution=None,
        )
        assert isinstance(trigger, bool)
        assert isinstance(metrics, dict)


class TestMetricsKeys:
    """Verify metrics dict contains all required keys."""

    def test_metrics_contains_all_required_keys(self):
        ref = _make_random_frame()
        cur = _make_random_frame()
        _, metrics = should_capture_new_image(
            ref, cur, frames_since_last_capture=20,
        )
        assert REQUIRED_METRICS_KEYS.issubset(set(metrics.keys()))

    def test_metrics_keys_with_custom_gate_resolution(self):
        ref = _make_random_frame()
        cur = _make_random_frame()
        _, metrics = should_capture_new_image(
            ref, cur, frames_since_last_capture=20,
            gate_resolution=(160, 160),
        )
        assert REQUIRED_METRICS_KEYS.issubset(set(metrics.keys()))

    def test_metrics_values_are_numeric(self):
        ref = _make_random_frame()
        cur = _make_random_frame()
        _, metrics = should_capture_new_image(
            ref, cur, frames_since_last_capture=20,
        )
        for key in REQUIRED_METRICS_KEYS:
            assert isinstance(metrics[key], (int, float))


class TestTimeoutAndCooldown:
    """Test frame-based timeout and cooldown logic."""

    def test_timeout_forces_capture(self):
        # Same frame = no scene change, but timeout should force
        frame = _make_solid_frame((100, 100, 100))
        trigger, metrics = should_capture_new_image(
            frame, frame.copy(), frames_since_last_capture=100,
            timeout_frames=50,
        )
        assert trigger is True
        assert metrics["timeout_force"] == 1.0

    def test_cooldown_blocks_capture(self):
        # Different frames but cooldown not met
        ref = _make_solid_frame((0, 0, 0))
        cur = _make_solid_frame((255, 255, 255))
        trigger, metrics = should_capture_new_image(
            ref, cur, frames_since_last_capture=1,
            cooldown_frames=18,
        )
        # Cooldown not met, should not trigger (even if scene changed)
        assert metrics["cooldown_ok"] == 0.0

    def test_scene_change_after_cooldown(self):
        # Very different frames + cooldown met
        ref = _make_solid_frame((0, 0, 0))
        cur = _make_solid_frame((255, 255, 255))
        trigger, metrics = should_capture_new_image(
            ref, cur, frames_since_last_capture=30,
            cooldown_frames=18,
        )
        assert metrics["cooldown_ok"] == 1.0


class TestSceneGateCooldownTimeoutOverrides:
    """should_run_detector_by_scene_change honors optional cooldown/timeout.

    Regression for the Spec 019 / Task 8.2 defect: in video-first the Scene
    Gate was wired with the legacy internal cooldown (18) even though the
    analysis config used gaps 3/8, so the gate could never fire in the min-gap
    window. The wrapper now forwards explicit overrides while keeping the
    legacy 18/45 defaults when none are provided.
    """

    def test_defaults_match_legacy_thresholds(self):
        """No overrides → uses legacy MIN/MAX capture thresholds (18/45)."""
        # A scene change with a gap below the legacy cooldown (18) must stay
        # blocked when no overrides are supplied — legacy behavior preserved.
        ref = _make_solid_frame((0, 0, 0))
        cur = _make_solid_frame((255, 255, 255))
        trigger, metrics = should_run_detector_by_scene_change(
            reference_bgr=ref,
            current_bgr=cur,
            frames_since_last_detection=5,  # 3 <= 5 < 18
        )
        assert metrics["cooldown_ok"] == 0.0
        assert trigger is False
        # Sanity: the defaults really are the legacy constants.
        assert MIN_FRAMES_BETWEEN_CAPTURES == 18
        assert MAX_FRAMES_WITHOUT_CAPTURE == 45

    def test_override_cooldown_allows_gate_in_min_window(self):
        """With cooldown=3, a strong scene change fires the gate at gap 5.

        Uses two saturated colors (pure red vs pure green): orb_matches == 0
        and HSV histogram difference ~1.0 clears both thresholds. A brightness-
        only change (black vs white) would not — its hue/saturation matches.
        """
        ref = _make_solid_frame((0, 0, 255))
        cur = _make_solid_frame((0, 255, 0))
        trigger, metrics = should_run_detector_by_scene_change(
            reference_bgr=ref,
            current_bgr=cur,
            frames_since_last_detection=5,
            cooldown_frames=3,
            timeout_frames=8,
        )
        assert metrics["cooldown_ok"] == 1.0
        assert trigger is True

    def test_override_no_scene_change_stays_blocked(self):
        """Same config, but no visual change → blocked (not a timeout force)."""
        frame = _make_solid_frame((100, 100, 100))
        trigger, metrics = should_run_detector_by_scene_change(
            reference_bgr=frame,
            current_bgr=frame.copy(),
            frames_since_last_detection=5,
            cooldown_frames=3,
            timeout_frames=8,
        )
        # Cooldown satisfied but no scene change and not yet timeout → blocked.
        assert metrics["cooldown_ok"] == 1.0
        assert metrics["timeout_force"] == 0.0
        assert trigger is False

    def test_override_timeout_still_forces(self):
        """At/after the override timeout, a static scene is still forced."""
        frame = _make_solid_frame((100, 100, 100))
        trigger, metrics = should_run_detector_by_scene_change(
            reference_bgr=frame,
            current_bgr=frame.copy(),
            frames_since_last_detection=8,
            cooldown_frames=3,
            timeout_frames=8,
        )
        assert metrics["timeout_force"] == 1.0
        assert trigger is True
