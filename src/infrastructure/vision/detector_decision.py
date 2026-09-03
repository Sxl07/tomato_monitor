"""Pure detector-execution decision for sparse video sampling.

This module encapsulates the "should the detector run on this frame?" logic
that was previously embedded inline in
``video_inspection_runner.run_video_inspection``. The extraction is a strict 1:1
reproduction of that block so it can be reused by both the legacy runner
(benchmark/diagnostic tool) and the new video-first analysis service without
duplicating the decision logic or changing observable behavior.

The module intentionally avoids heavy dependencies: it does not import torch,
detectron2 or cv2 at module level. ``numpy`` is referenced only for type hints
under ``TYPE_CHECKING``. The Scene Gate is not called directly; it is injected as
a callable (``scene_gate_fn``), keeping this function pure over its inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np


@dataclass(frozen=True)
class DetectorDecision:
    """Result of the sparse-sampling detector decision for a single frame.

    Attributes:
        run_detector: Whether the detector should run on the current frame.
        reason: Exactly one label from the closed set:
            ``first_frame`` | ``max_gap_force`` | ``scene_gate`` |
            ``scene_gate_blocked`` | ``min_gap_ready`` | ``cooldown`` |
            ``full_detection``.
    """

    run_detector: bool
    reason: str


def decide_run_detector(
    *,
    frame_idx: int,
    frames_since_last_detection: int,
    last_detection_frame: "np.ndarray | None",
    current_frame: "np.ndarray",
    enable_sparse_detection: bool,
    use_scene_gate: bool,
    min_frames_between_detections: int,
    max_frames_without_detection: int,
    force_detect_on_first_frame: bool,
    scene_gate_fn: Callable[..., "tuple[bool, dict]"],
) -> DetectorDecision:
    """Decide whether the detector runs on the current frame (pure function).

    This is a 1:1 extraction of the ``run_detector`` / ``detector_reason`` block
    from ``video_inspection_runner.run_video_inspection``. It preserves the exact
    precedence of branches:

        full_detection -> first_frame -> max_gap_force ->
        (scene_gate | scene_gate_blocked | min_gap_ready) -> cooldown

    The function does not open the camera, run inference or touch disk. The only
    external call is to ``scene_gate_fn``, which is itself pure over two frames.

    Args:
        frame_idx: Index of the current frame in the sequence.
        frames_since_last_detection: Consecutive frames since the detector last ran.
        last_detection_frame: Reference frame from the last detector run, or None.
        current_frame: The current frame being evaluated.
        enable_sparse_detection: If False, the detector always runs (diagnostic).
        use_scene_gate: Whether the Scene Gate is consulted in the min-gap window.
        min_frames_between_detections: Minimum frame gap before considering a run.
        max_frames_without_detection: Maximum frame gap that forces a run.
        force_detect_on_first_frame: Whether frame 0 always runs the detector.
        scene_gate_fn: Injected Scene Gate callable returning ``(trigger, metrics)``.

    Returns:
        A :class:`DetectorDecision` with ``run_detector`` and exactly one ``reason``
        from the closed set.
    """
    if not enable_sparse_detection:
        return DetectorDecision(True, "full_detection")

    if frame_idx == 0 and force_detect_on_first_frame:
        return DetectorDecision(True, "first_frame")

    if frames_since_last_detection >= max_frames_without_detection:
        return DetectorDecision(True, "max_gap_force")

    if frames_since_last_detection >= min_frames_between_detections:
        if use_scene_gate and last_detection_frame is not None:
            trigger, _ = scene_gate_fn(
                reference_bgr=last_detection_frame,
                current_bgr=current_frame,
                frames_since_last_detection=frames_since_last_detection,
            )
            if trigger:
                return DetectorDecision(True, "scene_gate")
            return DetectorDecision(False, "scene_gate_blocked")
        return DetectorDecision(True, "min_gap_ready")

    return DetectorDecision(False, "cooldown")
