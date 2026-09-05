"""Regression tests for the video-first Scene Gate wiring (Spec 019, Task 8.2).

Confirmed defect (Raspberry monitoring 16): VideoAnalysisService passed 3/8 to
decide_run_detector correctly, but the resolved scene_gate_fn
(should_run_detector_by_scene_change) called should_capture_new_image with the
legacy defaults MIN_FRAMES_BETWEEN_CAPTURES=18 / MAX_FRAMES_WITHOUT_CAPTURE=45.
As a result the Scene Gate could never fire in the min-gap window (3–7): the
internal legacy cooldown of 18 blocked every evaluation, and at gap 8
decide_run_detector already forced a run via max_gap_force.

These tests verify the fix:
  1. With config min=3/max=8 and a strong visual change, the Scene Gate can
     return "scene_gate" in a gap between 3 and 7.
  2. With the same config but no visual change, it returns "scene_gate_blocked".
  3. At gap == 8, max_gap_force still wins and the Scene Gate does not alter
     that precedence (the gate is not even consulted).
  4. VideoAnalysisService binds the Scene Gate to its VideoAnalysisConfig gaps,
     not to the legacy 18/45 constants.

Pure tests: no cv2 heavy path is exercised through decide_run_detector when we
inject a fake gate; the real-gate case uses solid frames (OpenCV ORB/HSV on
tiny solid images, no detector/torch).
"""

from __future__ import annotations

import numpy as np
import pytest

from src.infrastructure.vision.detector_decision import decide_run_detector
from src.infrastructure.vision.capture_gate import (
    should_run_detector_by_scene_change,
)
from src.application.services.video_analysis_service import (
    VideoAnalysisConfig,
    VideoAnalysisService,
)


def _solid(color, w=128, h=128):
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:] = color
    return frame


# Two strongly different SATURATED colors (BGR). A pure red vs pure green frame
# yields orb_matches == 0 and an HSV histogram difference of ~1.0, i.e. a clear
# scene change that clears both the ORB and HSV thresholds. Brightness-only
# changes (black vs white) do NOT — their hue/saturation histograms match.
_RED = (0, 0, 255)
_GREEN = (0, 255, 0)


def _config(min_gap=3, max_gap=8):
    return VideoAnalysisConfig(
        min_frames_between_detections=min_gap,
        max_frames_without_detection=max_gap,
        use_scene_gate=True,
        enable_flow_propagation=False,
    )


def _bound_gate(cfg):
    """The exact scene_gate_fn VideoAnalysisService would resolve for cfg."""
    from functools import partial
    return partial(
        should_run_detector_by_scene_change,
        cooldown_frames=cfg.min_frames_between_detections,
        timeout_frames=cfg.max_frames_without_detection,
    )


class TestSceneGateFiresInMinWindow:
    """(1) Strong visual change → scene_gate in a gap between 3 and 7."""

    @pytest.mark.parametrize("gap", [3, 4, 5, 6, 7])
    def test_scene_gate_fires_on_change(self, gap):
        cfg = _config(3, 8)
        gate = _bound_gate(cfg)
        ref = _solid(_RED)
        cur = _solid(_GREEN)  # strong HSV/ORB scene change

        decision = decide_run_detector(
            frame_idx=100,
            frames_since_last_detection=gap,
            last_detection_frame=ref,
            current_frame=cur,
            enable_sparse_detection=True,
            use_scene_gate=True,
            min_frames_between_detections=cfg.min_frames_between_detections,
            max_frames_without_detection=cfg.max_frames_without_detection,
            force_detect_on_first_frame=True,
            scene_gate_fn=gate,
        )
        assert decision.run_detector is True
        assert decision.reason == "scene_gate"


class TestSceneGateBlockedNoChange:
    """(2) No visual change → scene_gate_blocked in the min window."""

    @pytest.mark.parametrize("gap", [3, 4, 5, 6, 7])
    def test_scene_gate_blocked_without_change(self, gap):
        cfg = _config(3, 8)
        gate = _bound_gate(cfg)
        frame = _solid((120, 120, 120))

        decision = decide_run_detector(
            frame_idx=100,
            frames_since_last_detection=gap,
            last_detection_frame=frame,
            current_frame=frame.copy(),  # identical → no change
            enable_sparse_detection=True,
            use_scene_gate=True,
            min_frames_between_detections=cfg.min_frames_between_detections,
            max_frames_without_detection=cfg.max_frames_without_detection,
            force_detect_on_first_frame=True,
            scene_gate_fn=gate,
        )
        assert decision.run_detector is False
        assert decision.reason == "scene_gate_blocked"


class TestMaxGapForcePrecedence:
    """(3) At gap == max (8) max_gap_force wins; the gate is not consulted."""

    def test_gap_at_max_forces_without_calling_gate(self):
        cfg = _config(3, 8)

        called = {"n": 0}

        def _tracking_gate(**kwargs):
            called["n"] += 1
            return True, {"stub": 1.0}

        # Even with a strong change available, at gap == 8 the decision must be
        # max_gap_force and the gate must NOT be consulted.
        decision = decide_run_detector(
            frame_idx=100,
            frames_since_last_detection=8,
            last_detection_frame=_solid(_RED),
            current_frame=_solid(_GREEN),
            enable_sparse_detection=True,
            use_scene_gate=True,
            min_frames_between_detections=cfg.min_frames_between_detections,
            max_frames_without_detection=cfg.max_frames_without_detection,
            force_detect_on_first_frame=True,
            scene_gate_fn=_tracking_gate,
        )
        assert decision.run_detector is True
        assert decision.reason == "max_gap_force"
        assert called["n"] == 0

    def test_gap_above_max_also_forces(self):
        cfg = _config(3, 8)
        gate = _bound_gate(cfg)
        decision = decide_run_detector(
            frame_idx=100,
            frames_since_last_detection=20,
            last_detection_frame=_solid((0, 0, 0)),
            current_frame=_solid((0, 0, 0)),
            enable_sparse_detection=True,
            use_scene_gate=True,
            min_frames_between_detections=cfg.min_frames_between_detections,
            max_frames_without_detection=cfg.max_frames_without_detection,
            force_detect_on_first_frame=True,
            scene_gate_fn=gate,
        )
        assert decision.reason == "max_gap_force"


class TestServiceBindsGateToConfig:
    """(4) VideoAnalysisService binds the gate to its config, not to 18/45."""

    def test_resolved_gate_uses_config_gaps(self):
        cfg = _config(3, 8)
        svc = VideoAnalysisService(
            monitoring_id=1,
            video_path="outputs/monitorings/1/video/monitoring.mp4",
            snapshot_repo=object(),
            inspection_result_repo=object(),
            monitoring_repo=object(),
            db_session=object(),
            config=cfg,
            video_reader=object(),
        )
        gate = svc._resolve_scene_gate_fn()

        # The resolved gate must carry the config gaps as bound keywords, NOT
        # the legacy 18/45. functools.partial exposes them via .keywords.
        keywords = getattr(gate, "keywords", None)
        assert keywords is not None, "resolved gate should be a bound partial"
        assert keywords.get("cooldown_frames") == 3
        assert keywords.get("timeout_frames") == 8

    def test_bound_gate_fires_in_min_window(self):
        """End-to-end: the service-resolved gate fires on change at gap 5."""
        cfg = _config(3, 8)
        svc = VideoAnalysisService(
            monitoring_id=1,
            video_path="outputs/monitorings/1/video/monitoring.mp4",
            snapshot_repo=object(),
            inspection_result_repo=object(),
            monitoring_repo=object(),
            db_session=object(),
            config=cfg,
            video_reader=object(),
        )
        gate = svc._resolve_scene_gate_fn()
        trigger, _metrics = gate(
            reference_bgr=_solid(_RED),
            current_bgr=_solid(_GREEN),
            frames_since_last_detection=5,
        )
        assert trigger is True
