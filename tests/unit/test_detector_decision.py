"""Unit tests for the pure `decide_run_detector` decision (Spec 019, Task 1.2).

These tests verify the 1:1 extraction of the detector-execution decision from
`video_inspection_runner`:

- Each `reason` in the closed set is reachable:
  first_frame, max_gap_force, scene_gate, scene_gate_blocked, min_gap_ready,
  cooldown, full_detection.
- Branch precedence is preserved.
- Boundary behavior around `min_gap <= max_gap`.
- Determinism (same input -> same output).
- Purity: no camera, no inference, no disk; the Scene Gate is injected and the
  function only calls it (with the same keyword contract as the legacy code).

The tests never touch OpenCV, Detectron2 or disk. The Scene Gate is a fake
callable that records its invocations.
"""

import numpy as np
import pytest

from src.infrastructure.vision.detector_decision import (
    DetectorDecision,
    decide_run_detector,
)


CLOSED_REASON_SET = {
    "first_frame",
    "max_gap_force",
    "scene_gate",
    "scene_gate_blocked",
    "min_gap_ready",
    "cooldown",
    "full_detection",
}


def _frame():
    """Small deterministic BGR frame; contents are irrelevant to the decision."""
    return np.zeros((4, 4, 3), dtype=np.uint8)


class _RecordingGate:
    """Fake scene gate that returns a fixed trigger and records its calls."""

    def __init__(self, trigger: bool):
        self._trigger = trigger
        self.calls = []

    def __call__(self, *, reference_bgr, current_bgr, frames_since_last_detection):
        self.calls.append(
            {
                "reference_bgr": reference_bgr,
                "current_bgr": current_bgr,
                "frames_since_last_detection": frames_since_last_detection,
            }
        )
        return self._trigger, {"stub": 1.0}


def _never_called_gate(**kwargs):  # pragma: no cover - must never run
    raise AssertionError("scene_gate_fn must not be called in this branch")


def _decide(**overrides):
    """Helper with sensible defaults; override per test."""
    params = dict(
        frame_idx=10,
        frames_since_last_detection=0,
        last_detection_frame=_frame(),
        current_frame=_frame(),
        enable_sparse_detection=True,
        use_scene_gate=True,
        min_frames_between_detections=5,
        max_frames_without_detection=12,
        force_detect_on_first_frame=True,
        scene_gate_fn=_never_called_gate,
    )
    params.update(overrides)
    return decide_run_detector(**params)


class TestFullDetection:
    def test_sparse_disabled_returns_full_detection(self):
        d = _decide(enable_sparse_detection=False)
        assert d == DetectorDecision(True, "full_detection")

    def test_full_detection_takes_precedence_over_first_frame(self):
        # frame 0 with force would be first_frame, but sparse disabled wins.
        d = _decide(
            enable_sparse_detection=False,
            frame_idx=0,
            force_detect_on_first_frame=True,
        )
        assert d.reason == "full_detection"
        assert d.run_detector is True


class TestFirstFrame:
    def test_first_frame_forced(self):
        d = _decide(frame_idx=0, force_detect_on_first_frame=True)
        assert d == DetectorDecision(True, "first_frame")

    def test_first_frame_takes_precedence_over_max_gap(self):
        # gap >= max would be max_gap_force, but first_frame wins.
        d = _decide(
            frame_idx=0,
            force_detect_on_first_frame=True,
            frames_since_last_detection=999,
            max_frames_without_detection=12,
        )
        assert d.reason == "first_frame"

    def test_first_frame_not_forced_falls_through(self):
        # frame 0 but force disabled -> not first_frame. gap 0 < min -> cooldown.
        d = _decide(
            frame_idx=0,
            force_detect_on_first_frame=False,
            frames_since_last_detection=0,
        )
        assert d == DetectorDecision(False, "cooldown")


class TestMaxGapForce:
    def test_gap_equal_max_forces(self):
        d = _decide(frames_since_last_detection=12, max_frames_without_detection=12)
        assert d == DetectorDecision(True, "max_gap_force")

    def test_gap_above_max_forces(self):
        d = _decide(frames_since_last_detection=20, max_frames_without_detection=12)
        assert d == DetectorDecision(True, "max_gap_force")

    def test_max_gap_takes_precedence_over_scene_gate(self):
        gate = _RecordingGate(trigger=False)
        d = _decide(
            frames_since_last_detection=12,
            min_frames_between_detections=5,
            max_frames_without_detection=12,
            scene_gate_fn=gate,
        )
        assert d.reason == "max_gap_force"
        assert gate.calls == []  # gate never consulted when max gap forces


class TestSceneGate:
    def test_scene_gate_triggers(self):
        gate = _RecordingGate(trigger=True)
        d = _decide(
            frames_since_last_detection=7,
            min_frames_between_detections=5,
            max_frames_without_detection=12,
            use_scene_gate=True,
            scene_gate_fn=gate,
        )
        assert d == DetectorDecision(True, "scene_gate")
        assert len(gate.calls) == 1

    def test_scene_gate_blocked(self):
        gate = _RecordingGate(trigger=False)
        d = _decide(
            frames_since_last_detection=7,
            min_frames_between_detections=5,
            max_frames_without_detection=12,
            use_scene_gate=True,
            scene_gate_fn=gate,
        )
        assert d == DetectorDecision(False, "scene_gate_blocked")
        assert len(gate.calls) == 1

    def test_scene_gate_called_with_legacy_keyword_contract(self):
        gate = _RecordingGate(trigger=True)
        ref = _frame()
        cur = _frame()
        _decide(
            frames_since_last_detection=8,
            min_frames_between_detections=5,
            max_frames_without_detection=12,
            use_scene_gate=True,
            last_detection_frame=ref,
            current_frame=cur,
            scene_gate_fn=gate,
        )
        call = gate.calls[0]
        assert call["reference_bgr"] is ref
        assert call["current_bgr"] is cur
        assert call["frames_since_last_detection"] == 8


class TestMinGapReady:
    def test_min_gap_ready_when_scene_gate_disabled(self):
        d = _decide(
            frames_since_last_detection=7,
            min_frames_between_detections=5,
            max_frames_without_detection=12,
            use_scene_gate=False,
            scene_gate_fn=_never_called_gate,
        )
        assert d == DetectorDecision(True, "min_gap_ready")

    def test_min_gap_ready_when_no_reference_frame(self):
        # use_scene_gate True but no last_detection_frame -> min_gap_ready.
        d = _decide(
            frames_since_last_detection=7,
            min_frames_between_detections=5,
            max_frames_without_detection=12,
            use_scene_gate=True,
            last_detection_frame=None,
            scene_gate_fn=_never_called_gate,
        )
        assert d == DetectorDecision(True, "min_gap_ready")

    def test_gap_equal_min_enters_min_gap_window(self):
        d = _decide(
            frames_since_last_detection=5,
            min_frames_between_detections=5,
            max_frames_without_detection=12,
            use_scene_gate=False,
        )
        assert d.reason == "min_gap_ready"


class TestCooldown:
    def test_gap_below_min_is_cooldown(self):
        d = _decide(
            frames_since_last_detection=4,
            min_frames_between_detections=5,
            max_frames_without_detection=12,
        )
        assert d == DetectorDecision(False, "cooldown")

    def test_gap_zero_is_cooldown(self):
        d = _decide(
            frames_since_last_detection=0,
            min_frames_between_detections=5,
            max_frames_without_detection=12,
        )
        assert d == DetectorDecision(False, "cooldown")


class TestBoundariesAndTotality:
    def test_never_cooldown_when_gap_ge_max(self):
        # For gap >= max, decision must force detection (max_gap_force),
        # never cooldown -- even when min == max.
        for gap in range(12, 20):
            d = _decide(
                frames_since_last_detection=gap,
                min_frames_between_detections=12,
                max_frames_without_detection=12,
            )
            assert d.reason != "cooldown"
            assert d.reason == "max_gap_force"

    def test_min_equal_max_boundary(self):
        # gap just below the shared boundary -> cooldown.
        d = _decide(
            frames_since_last_detection=11,
            min_frames_between_detections=12,
            max_frames_without_detection=12,
        )
        assert d == DetectorDecision(False, "cooldown")

    def test_reason_always_in_closed_set(self):
        gate_true = _RecordingGate(trigger=True)
        gate_false = _RecordingGate(trigger=False)
        scenarios = [
            dict(enable_sparse_detection=False),
            dict(frame_idx=0, force_detect_on_first_frame=True),
            dict(frames_since_last_detection=99, max_frames_without_detection=12),
            dict(frames_since_last_detection=7, use_scene_gate=True, scene_gate_fn=gate_true),
            dict(frames_since_last_detection=7, use_scene_gate=True, scene_gate_fn=gate_false),
            dict(frames_since_last_detection=7, use_scene_gate=False),
            dict(frames_since_last_detection=1),
        ]
        for overrides in scenarios:
            d = _decide(**overrides)
            assert d.reason in CLOSED_REASON_SET


class TestDeterminism:
    def test_same_input_same_output(self):
        kwargs = dict(
            frames_since_last_detection=7,
            min_frames_between_detections=5,
            max_frames_without_detection=12,
            use_scene_gate=True,
            scene_gate_fn=_RecordingGate(trigger=True),
        )
        first = _decide(**kwargs)
        second = _decide(**kwargs)
        assert first == second


class TestPurity:
    def test_no_scene_gate_call_outside_min_gap_window(self):
        # cooldown branch must not consult the gate.
        _decide(
            frames_since_last_detection=2,
            min_frames_between_detections=5,
            max_frames_without_detection=12,
            scene_gate_fn=_never_called_gate,
        )

    def test_frame_inputs_not_mutated(self):
        ref = _frame()
        cur = _frame()
        ref_copy = ref.copy()
        cur_copy = cur.copy()
        _decide(
            frames_since_last_detection=7,
            min_frames_between_detections=5,
            max_frames_without_detection=12,
            use_scene_gate=True,
            last_detection_frame=ref,
            current_frame=cur,
            scene_gate_fn=_RecordingGate(trigger=True),
        )
        assert np.array_equal(ref, ref_copy)
        assert np.array_equal(cur, cur_copy)
