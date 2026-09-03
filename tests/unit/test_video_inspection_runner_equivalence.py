"""Legacy equivalence test for the `decide_run_detector` extraction (Task 1.5).

Requirement 16.3: when `video_inspection_runner` is refactored to use
`decide_run_detector`, its observable behavior must remain unchanged.

The runner's observable per-frame decision is fully determined by the
`(run_detector, detector_reason)` pair it computes for each frame. This test
pins that behavior by:

1. Reproducing the ORIGINAL inline decision block (pre-refactor) as a reference
   oracle (`legacy_inline_decision`), copied verbatim from the code that lived
   in `run_video_inspection`.
2. Driving BOTH the oracle and the extracted `decide_run_detector` over the same
   fixed synthetic sequence of frames, mirroring the exact state updates the
   runner performs (`frames_since_last_detection`, `last_detection_frame`), and
   asserting the two agree on every frame.

The Scene Gate is injected as a deterministic fake in both paths (identical
contract), so the comparison isolates the decision logic without OpenCV,
Detectron2, a real video, or disk I/O.
"""

import numpy as np

from src.infrastructure.vision.detector_decision import decide_run_detector


def legacy_inline_decision(
    *,
    frame_idx,
    frames_since_last_detection,
    last_detection_frame,
    frame,
    enable_sparse_detection,
    use_scene_gate,
    min_frames_between_detections,
    max_frames_without_detection,
    force_detect_on_first_frame,
    scene_gate_fn,
):
    """Verbatim reproduction of the pre-refactor inline decision block.

    Mirrors exactly the `run_detector` / `detector_reason` logic that used to
    live in `run_video_inspection` before Task 1.4.
    """
    run_detector = False
    detector_reason = "skipped"

    if not enable_sparse_detection:
        run_detector = True
        detector_reason = "full_detection"

    elif frame_idx == 0 and force_detect_on_first_frame:
        run_detector = True
        detector_reason = "first_frame"

    elif frames_since_last_detection >= max_frames_without_detection:
        run_detector = True
        detector_reason = "max_gap_force"

    elif frames_since_last_detection >= min_frames_between_detections:
        if use_scene_gate and last_detection_frame is not None:
            trigger, _ = scene_gate_fn(
                reference_bgr=last_detection_frame,
                current_bgr=frame,
                frames_since_last_detection=frames_since_last_detection,
            )
            if trigger:
                run_detector = True
                detector_reason = "scene_gate"
            else:
                run_detector = False
                detector_reason = "scene_gate_blocked"
        else:
            run_detector = True
            detector_reason = "min_gap_ready"
    else:
        run_detector = False
        detector_reason = "cooldown"

    return run_detector, detector_reason


class _AlternatingGate:
    """Deterministic scene gate: triggers on a fixed schedule of calls."""

    def __init__(self, schedule):
        self._schedule = list(schedule)
        self._i = 0

    def __call__(self, *, reference_bgr, current_bgr, frames_since_last_detection):
        # Deterministic: cycle through the schedule.
        trigger = self._schedule[self._i % len(self._schedule)]
        self._i += 1
        return trigger, {"stub": 1.0}


def _frame(seed):
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, size=(8, 8, 3), dtype=np.uint8)


def _run_sequence(*, n_frames, config, gate_schedule):
    """Drive both decision paths over the same synthetic sequence.

    Reproduces the exact state transitions the runner applies:
    on a detector run, reset gap to 0 and set last_detection_frame; otherwise
    increment gap. Returns two lists of (run_detector, reason).
    """
    frames = [_frame(i) for i in range(n_frames)]

    # Oracle path
    oracle_gate = _AlternatingGate(gate_schedule)
    oracle_results = []
    gap = 0
    last_frame = None
    for idx in range(n_frames):
        run, reason = legacy_inline_decision(
            frame_idx=idx,
            frames_since_last_detection=gap,
            last_detection_frame=last_frame,
            frame=frames[idx],
            scene_gate_fn=oracle_gate,
            **config,
        )
        oracle_results.append((run, reason))
        if run:
            last_frame = frames[idx].copy()
            gap = 0
        else:
            gap += 1

    # Extracted-function path (same schedule -> same gate outputs)
    new_gate = _AlternatingGate(gate_schedule)
    new_results = []
    gap = 0
    last_frame = None
    for idx in range(n_frames):
        decision = decide_run_detector(
            frame_idx=idx,
            frames_since_last_detection=gap,
            last_detection_frame=last_frame,
            current_frame=frames[idx],
            scene_gate_fn=new_gate,
            **config,
        )
        new_results.append((decision.run_detector, decision.reason))
        if decision.run_detector:
            last_frame = frames[idx].copy()
            gap = 0
        else:
            gap += 1

    return oracle_results, new_results


BASELINE_CONFIG = dict(
    enable_sparse_detection=True,
    use_scene_gate=True,
    min_frames_between_detections=5,
    max_frames_without_detection=12,
    force_detect_on_first_frame=True,
)


def test_equivalence_baseline_sparse_with_scene_gate():
    oracle, new = _run_sequence(
        n_frames=60,
        config=BASELINE_CONFIG,
        gate_schedule=[False, False, True, False, True],
    )
    assert new == oracle


def test_equivalence_full_detection_mode():
    config = dict(BASELINE_CONFIG, enable_sparse_detection=False)
    oracle, new = _run_sequence(
        n_frames=40,
        config=config,
        gate_schedule=[True],
    )
    assert new == oracle
    # Full detection: every frame runs with full_detection reason.
    assert all(run and reason == "full_detection" for run, reason in new)


def test_equivalence_scene_gate_disabled():
    config = dict(BASELINE_CONFIG, use_scene_gate=False)
    oracle, new = _run_sequence(
        n_frames=50,
        config=config,
        gate_schedule=[True],
    )
    assert new == oracle


def test_equivalence_first_frame_not_forced():
    config = dict(BASELINE_CONFIG, force_detect_on_first_frame=False)
    oracle, new = _run_sequence(
        n_frames=50,
        config=config,
        gate_schedule=[True, False],
    )
    assert new == oracle


def test_equivalence_gate_always_blocks_forces_max_gap():
    # Gate never triggers -> detection only via max_gap_force after cooldown.
    oracle, new = _run_sequence(
        n_frames=80,
        config=BASELINE_CONFIG,
        gate_schedule=[False],
    )
    assert new == oracle
    # Sanity: the sequence exercises cooldown, scene_gate_blocked and max_gap_force.
    reasons = {reason for _, reason in new}
    assert "cooldown" in reasons
    assert "scene_gate_blocked" in reasons
    assert "max_gap_force" in reasons


def test_equivalence_tight_gaps():
    config = dict(
        BASELINE_CONFIG,
        min_frames_between_detections=1,
        max_frames_without_detection=3,
    )
    oracle, new = _run_sequence(
        n_frames=40,
        config=config,
        gate_schedule=[False, True, False],
    )
    assert new == oracle
