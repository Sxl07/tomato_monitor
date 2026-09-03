"""Runner wiring test for `decide_run_detector` (Spec 019, Task 1.5 — part B).

The sibling equivalence test (`test_video_inspection_runner_equivalence.py`)
proves `legacy_inline_decision == decide_run_detector`. It does NOT prove that
`run_video_inspection` is actually wired to the extracted function with the
correct arguments and consumes its result. This test closes that gap.

It runs `run_video_inspection()` end to end with ALL heavy dependencies faked
(no Detectron2, no real inference, no real video, no disk writes) by
monkeypatching the names bound in the `video_inspection_runner` module:

- `cv2.VideoCapture` / `cv2.VideoWriter` (fake video of a few frames)
- `build_pipeline_components`, `process_frame`
- `OpticalFlowVisualTracker`
- `write_csv`, `save_image`
- `VIDEOS_DIR` (so a fake video file path is discovered)

A spy wraps the REAL `decide_run_detector` so it records the exact keyword
arguments it receives and the `DetectorDecision` it returns, while still
delegating to the real implementation (so the runner consumes a genuine result).

Assertions demonstrate:
1. `run_video_inspection()` actually invokes `decide_run_detector` (once per frame).
2. It passes the correct arguments, including
   `scene_gate_fn=should_run_detector_by_scene_change` and the exact
   min/max gaps and flags.
3. The returned `DetectorDecision` drives detector run/skip, `detector_reason`
   and the reason counters in the summary.
"""

import numpy as np
import pytest

import src.infrastructure.vision.video_inspection_runner as runner_mod
from src.infrastructure.vision.capture_gate import should_run_detector_by_scene_change
from src.infrastructure.vision.detector_decision import (
    DetectorDecision,
    decide_run_detector as real_decide_run_detector,
)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class _FakeVideoCapture:
    """Minimal cv2.VideoCapture stand-in yielding a fixed number of frames."""

    def __init__(self, path, n_frames=3, width=16, height=12, fps=30.0):
        self._n = n_frames
        self._i = 0
        self._w = width
        self._h = height
        self._fps = fps

    def isOpened(self):
        return True

    def get(self, prop):
        import cv2
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._w)
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._h)
        if prop == cv2.CAP_PROP_FPS:
            return self._fps
        return 0.0

    def read(self):
        if self._i >= self._n:
            return False, None
        # Deterministic distinct frames.
        frame = np.full((self._h, self._w, 3), self._i * 10, dtype=np.uint8)
        self._i += 1
        return True, frame

    def release(self):
        pass


class _FakeWriter:
    def write(self, frame):
        pass

    def release(self):
        pass


class _FakeVisualTracker:
    def update_from_detection_result(self, frame, detections):
        pass

    def propagate(self, frame):
        return []


def _fake_build_pipeline_components():
    return object()


def _fake_process_frame(frame, components, frame_name):
    """Return a result dict shaped like the real pipeline output (no detections)."""
    return {
        "frame_idx": 0,
        "image_name": frame_name,
        "detections_count": 0,
        "tracked_count": 0,
        "new_tracks_count": 0,
        "reused_count": 0,
        "health_executed_count": 0,
        "maturity_executed_count": 0,
        "times": {"detection_sec": 0.0, "tracking_sec": 0.0},
        "detections": [],
    }


@pytest.fixture
def fake_video_dir(tmp_path, monkeypatch):
    """Provide a VIDEOS_DIR containing one fake .mp4 file (never actually read)."""
    video_file = tmp_path / "fake_video_02.mp4"
    video_file.write_bytes(b"\x00")  # existence only; _FakeVideoCapture ignores content
    monkeypatch.setattr(runner_mod, "VIDEOS_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def patch_runner_deps(monkeypatch, tmp_path):
    """Patch all heavy dependencies bound in the runner module."""
    # Redirect output artifacts to a temp dir so reports_dir.mkdir() /
    # annotated_dir.mkdir() never touch the real outputs/experiments/ tree.
    monkeypatch.setattr(runner_mod, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(runner_mod, "build_pipeline_components", _fake_build_pipeline_components)
    monkeypatch.setattr(runner_mod, "process_frame", _fake_process_frame)
    monkeypatch.setattr(runner_mod, "OpticalFlowVisualTracker", _FakeVisualTracker)
    monkeypatch.setattr(runner_mod, "write_csv", lambda *a, **k: None)
    monkeypatch.setattr(runner_mod, "save_image", lambda *a, **k: None)

    # Patch cv2.VideoCapture / VideoWriter used by the runner.
    import cv2
    monkeypatch.setattr(cv2, "VideoCapture", lambda path: _FakeVideoCapture(path, n_frames=3))
    monkeypatch.setattr(cv2, "VideoWriter", lambda *a, **k: _FakeWriter())
    # VideoWriter_fourcc is called with a codec string; keep it harmless.
    monkeypatch.setattr(cv2, "VideoWriter_fourcc", lambda *a, **k: 0)


class _DecideSpy:
    """Wraps the real decide_run_detector, recording calls and delegating."""

    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        decision = real_decide_run_detector(**kwargs)
        self.calls.append({"kwargs": kwargs, "decision": decision})
        return decision


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_runner_invokes_decide_run_detector_with_correct_wiring(
    monkeypatch, fake_video_dir, patch_runner_deps
):
    spy = _DecideSpy()
    monkeypatch.setattr(runner_mod, "decide_run_detector", spy)

    summary = runner_mod.run_video_inspection(
        strategy_name="wiring_test",
        enable_sparse_detection=True,
        enable_flow_propagation=True,
        use_scene_gate=True,
        min_frames_between_detections=5,
        max_frames_without_detection=12,
        force_detect_on_first_frame=True,
        save_detection_snapshots=False,
        save_detection_crops=False,
        save_annotated_video=False,
        verbose=False,
    )

    # 1) decide_run_detector was actually invoked, once per frame (3 frames).
    assert len(spy.calls) == 3

    # 2) Arguments are wired correctly on every call.
    for idx, call in enumerate(spy.calls):
        kw = call["kwargs"]
        assert kw["frame_idx"] == idx
        assert kw["enable_sparse_detection"] is True
        assert kw["use_scene_gate"] is True
        assert kw["min_frames_between_detections"] == 5
        assert kw["max_frames_without_detection"] == 12
        assert kw["force_detect_on_first_frame"] is True
        assert kw["scene_gate_fn"] is should_run_detector_by_scene_change
        assert kw["current_frame"] is not None
        # frames_since_last_detection is a non-negative int.
        assert isinstance(kw["frames_since_last_detection"], int)
        assert kw["frames_since_last_detection"] >= 0

    # frame 0: forced first_frame -> gap 0, no reference frame yet.
    first = spy.calls[0]["kwargs"]
    assert first["frames_since_last_detection"] == 0
    assert first["last_detection_frame"] is None

    # 3) The returned DetectorDecision drives run/skip, reason and counters.
    # Frame 0 must be first_frame (forced) and run the detector.
    assert spy.calls[0]["decision"] == DetectorDecision(True, "first_frame")

    # The summary reason counts equal the reasons returned by the spy.
    from collections import Counter
    expected_reason_counts = Counter(c["decision"].reason for c in spy.calls)
    assert summary["reason_first_frame"] == expected_reason_counts.get("first_frame", 0)
    assert summary["reason_scene_gate"] == expected_reason_counts.get("scene_gate", 0)
    assert summary["reason_scene_gate_blocked"] == expected_reason_counts.get("scene_gate_blocked", 0)
    assert summary["reason_max_gap_force"] == expected_reason_counts.get("max_gap_force", 0)
    assert summary["reason_min_gap_ready"] == expected_reason_counts.get("min_gap_ready", 0)
    assert summary["reason_cooldown"] == expected_reason_counts.get("cooldown", 0)
    assert summary["reason_full_detection"] == expected_reason_counts.get("full_detection", 0)

    # detector_runs equals the number of decisions with run_detector=True.
    expected_runs = sum(1 for c in spy.calls if c["decision"].run_detector)
    expected_skips = sum(1 for c in spy.calls if not c["decision"].run_detector)
    assert summary["detector_runs"] == expected_runs
    assert summary["detector_skips"] == expected_skips
    assert summary["total_frames"] == 3


def test_runner_after_first_frame_updates_gap_and_reference(
    monkeypatch, fake_video_dir, patch_runner_deps
):
    """The runner feeds updated gap/last_detection_frame back into the decision.

    Frame 0 runs the detector (forced first_frame) and resets the gap to 0.
    The gap is only incremented on a skip, so the gaps passed into the decision
    are [0, 0, 1]: frame 1 sees the post-reset 0, is a cooldown skip (0 < 5),
    which then increments the gap to 1 that frame 2 sees. This proves the runner
    reset the gap on the frame-0 run and fed the updated gap back into
    decide_run_detector on later frames.
    """
    spy = _DecideSpy()
    monkeypatch.setattr(runner_mod, "decide_run_detector", spy)

    runner_mod.run_video_inspection(
        strategy_name="wiring_gap_test",
        enable_sparse_detection=True,
        enable_flow_propagation=True,
        use_scene_gate=True,
        min_frames_between_detections=5,
        max_frames_without_detection=12,
        force_detect_on_first_frame=True,
        save_detection_snapshots=False,
        save_detection_crops=False,
        save_annotated_video=False,
        verbose=False,
    )

    gaps = [c["kwargs"]["frames_since_last_detection"] for c in spy.calls]
    # Gap resets to 0 on the frame-0 run; increments only on skips.
    assert gaps == [0, 0, 1]

    # After the frame-0 detector run, the reference frame is passed on next calls.
    assert spy.calls[0]["kwargs"]["last_detection_frame"] is None
    assert spy.calls[1]["kwargs"]["last_detection_frame"] is not None
    assert spy.calls[2]["kwargs"]["last_detection_frame"] is not None

    # Frames 1 and 2 are cooldown (gap < min), i.e. detector skipped.
    assert spy.calls[1]["decision"] == DetectorDecision(False, "cooldown")
    assert spy.calls[2]["decision"] == DetectorDecision(False, "cooldown")


def test_runner_full_detection_flag_flows_into_decision(
    monkeypatch, fake_video_dir, patch_runner_deps
):
    """enable_sparse_detection=False must reach the decision -> full_detection."""
    spy = _DecideSpy()
    monkeypatch.setattr(runner_mod, "decide_run_detector", spy)

    summary = runner_mod.run_video_inspection(
        strategy_name="wiring_full_test",
        enable_sparse_detection=False,
        enable_flow_propagation=False,
        use_scene_gate=False,
        min_frames_between_detections=5,
        max_frames_without_detection=12,
        force_detect_on_first_frame=True,
        save_detection_snapshots=False,
        save_detection_crops=False,
        save_annotated_video=False,
        verbose=False,
    )

    assert all(c["kwargs"]["enable_sparse_detection"] is False for c in spy.calls)
    assert all(c["decision"] == DetectorDecision(True, "full_detection") for c in spy.calls)
    assert summary["reason_full_detection"] == 3
    assert summary["detector_runs"] == 3
