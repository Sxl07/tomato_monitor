"""SPEC 026 — POST-method unit tests for OpticalFlowVisualTracker retention
(Faceta 2 / CA-09) and temporal-coherence invariant (CA-10).

These use a REAL OpticalFlowVisualTracker and therefore require cv2. They cover:
  - Retention of a live-but-undetected track when live_track_ids is provided.
  - Discarding a track that is no longer alive in SimpleTracker (expired).
  - Legacy behavior when live_track_ids is None (rebuild from detections only).
  - Re-anchoring: a previously retained track that is detected again is reseeded
    from the real bbox.
  - CA-10 temporal coherence: after propagate(frame_B) + update_from_detection_
    result(frame_B, [], {7}), the retained track's points/bbox correspond to
    frame B and prev_gray is frame B, so propagate(frame_C) continues without
    desynchronization.
"""

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from src.infrastructure.vision.visual_tracker import (
    OpticalFlowVisualTracker,
    VisualTrackState,
)


def _textured_frame(shift=0, w=200, h=160):
    """A deterministic, textured BGR frame; a horizontal shift moves texture.

    Optical flow needs trackable corners, so we draw filled rectangles /
    gradients rather than pure noise, and translate them by ``shift`` px so LK
    finds a consistent motion.
    """
    img = np.zeros((h, w, 3), dtype=np.uint8)
    # A grid of bright squares that translate with `shift`.
    for gy in range(0, h, 20):
        for gx in range(0, w, 20):
            x = (gx + shift) % w
            img[gy:gy + 8, x:min(x + 8, w)] = 255
    return img


def _det(track_id, bbox, score=0.9):
    return {
        "track_id": track_id,
        "bbox": bbox,
        "det_score": score,
        "health_result": {"label": "healthy", "confidence": 0.9},
        "maturity_result": None,
    }


# --------------------------------------------------------------------------- #
# Retention (variant B) — CA-09
# --------------------------------------------------------------------------- #

class TestRetention:
    def test_retains_live_but_undetected_track(self):
        tracker = OpticalFlowVisualTracker()
        frame_a = _textured_frame(shift=0)
        # Seed two tracks (3 and 7) from detections.
        tracker.update_from_detection_result(
            frame_a,
            [_det(3, (10, 10, 40, 40)), _det(7, (100, 100, 140, 140))],
            live_track_ids={3, 7},
        )
        assert set(tracker.tracks.keys()) == {3, 7}
        state7_before = tracker.tracks[7]

        # SPEC 026 variant A: on the next DETECTOR frame the service runs
        # propagate(frame_B) BEFORE update_from_detection_result, so the visual
        # state (and prev_gray) are advanced to frame B first. Reproduce that
        # real flow here (do NOT retain a frame-A state against frame-B prev_gray).
        frame_b = _textured_frame(shift=5)
        tracker.propagate(frame_b)
        state7_after_propagate = tracker.tracks[7]

        # Only 3 is detected on frame B, but 7 is still alive -> retained.
        tracker.update_from_detection_result(
            frame_b, [_det(3, (14, 10, 44, 40))], live_track_ids={3, 7}
        )
        assert set(tracker.tracks.keys()) == {3, 7}          # 7 retained
        # The retained state is the one propagated to frame B (coherent with
        # prev_gray=B), NOT the stale frame-A state.
        assert tracker.tracks[7] is state7_after_propagate
        assert tracker.tracks[7] is not state7_before
        # 3 was re-seeded (new state object, from the real bbox at frame_b).
        assert tracker.tracks[3].bbox == (14, 10, 44, 40)
        # prev_gray corresponds to frame B (invariant points ↔ prev_gray).
        assert np.array_equal(
            tracker.prev_gray, cv2.cvtColor(frame_b, cv2.COLOR_BGR2GRAY)
        )

    def test_discards_expired_track(self):
        tracker = OpticalFlowVisualTracker()
        frame_a = _textured_frame(shift=0)
        tracker.update_from_detection_result(
            frame_a,
            [_det(3, (10, 10, 40, 40)), _det(7, (100, 100, 140, 140))],
            live_track_ids={3, 7},
        )
        # Detector frame B (variant A): propagate first (real flow), then 7 has
        # expired in SimpleTracker -> not in live_track_ids -> discarded.
        frame_b = _textured_frame(shift=5)
        tracker.propagate(frame_b)
        tracker.update_from_detection_result(
            frame_b, [_det(3, (14, 10, 44, 40))], live_track_ids={3}
        )
        assert set(tracker.tracks.keys()) == {3}
        assert 7 not in tracker.tracks

    def test_legacy_behavior_when_live_ids_none(self):
        tracker = OpticalFlowVisualTracker()
        frame_a = _textured_frame(shift=0)
        tracker.update_from_detection_result(
            frame_a,
            [_det(3, (10, 10, 40, 40)), _det(7, (100, 100, 140, 140))],
        )
        # Legacy: rebuild from detections only -> 7 dropped (no retention).
        frame_b = _textured_frame(shift=5)
        tracker.update_from_detection_result(
            frame_b, [_det(3, (14, 10, 44, 40))]
        )
        assert set(tracker.tracks.keys()) == {3}

    def test_reanchor_previously_retained_track(self):
        tracker = OpticalFlowVisualTracker()
        frame_a = _textured_frame(shift=0)
        tracker.update_from_detection_result(
            frame_a, [_det(7, (100, 100, 140, 140))], live_track_ids={7}
        )
        # Detector frame B (variant A): propagate first, then 7 is omitted but
        # retained (with its already-propagated-to-B state).
        frame_b = _textured_frame(shift=5)
        tracker.propagate(frame_b)
        tracker.update_from_detection_result(
            frame_b, [], live_track_ids={7}
        )
        retained_bbox = tracker.tracks[7].bbox
        # Detector frame C (variant A): propagate first, then 7 is detected
        # again at a new bbox -> re-anchored to the real bbox.
        frame_c = _textured_frame(shift=10)
        tracker.propagate(frame_c)
        tracker.update_from_detection_result(
            frame_c, [_det(7, (120, 100, 160, 140))], live_track_ids={7}
        )
        assert tracker.tracks[7].bbox == (120, 100, 160, 140)
        assert tracker.tracks[7].bbox != retained_bbox


# --------------------------------------------------------------------------- #
# CA-10 — temporal coherence (points ↔ prev_gray)
# --------------------------------------------------------------------------- #

class TestCA10TemporalCoherence:
    def test_variant_a_keeps_points_coherent_with_prev_gray(self):
        """Simulate the production order on a detector frame that MISSES 7:

            propagate(frame_B)  (advances 7's points/bbox and prev_gray to B)
            update_from_detection_result(frame_B, [], {7})  (retain 7)

        Then propagate(frame_C) must continue 7 without desync (no exception,
        track still present). This is the sequence that would FAIL with a naive
        retention that kept frame-A points while prev_gray became frame B.
        """
        tracker = OpticalFlowVisualTracker()

        # Detector A: seed 7 from a detection; prev_gray = A.
        frame_a = _textured_frame(shift=0)
        tracker.update_from_detection_result(
            frame_a, [_det(7, (40, 40, 90, 90))], live_track_ids={7}
        )
        gray_a = cv2.cvtColor(frame_a, cv2.COLOR_BGR2GRAY)
        assert np.array_equal(tracker.prev_gray, gray_a)

        # Detector B (variant A): propagate to B BEFORE detection.
        frame_b = _textured_frame(shift=6)
        propagated = tracker.propagate(frame_b)
        # After propagate, tracks + prev_gray correspond to frame B.
        gray_b = cv2.cvtColor(frame_b, cv2.COLOR_BGR2GRAY)
        assert np.array_equal(tracker.prev_gray, gray_b)
        assert 7 in tracker.tracks
        state_after_propagate = tracker.tracks[7]

        # RetinaNet MISSES 7 on frame B, but 7 is still alive -> retained.
        tracker.update_from_detection_result(frame_b, [], live_track_ids={7})
        assert 7 in tracker.tracks
        # The retained state is the one propagated to frame B (coherent), and
        # prev_gray is frame B.
        assert tracker.tracks[7] is state_after_propagate
        assert np.array_equal(tracker.prev_gray, gray_b)

        # Next: propagate to frame C must continue 7 without desync/exception.
        frame_c = _textured_frame(shift=12)
        result_c = tracker.propagate(frame_c)
        propagated_ids = {r["track_id"] for r in result_c}
        assert 7 in propagated_ids  # track continued, not lost
        assert np.array_equal(
            tracker.prev_gray, cv2.cvtColor(frame_c, cv2.COLOR_BGR2GRAY)
        )

    def test_retained_state_points_match_prev_gray_frame(self):
        """The retained VisualTrackState.points are the ones produced against
        the frame that becomes prev_gray (frame B), never frame A's points."""
        tracker = OpticalFlowVisualTracker()
        frame_a = _textured_frame(shift=0)
        tracker.update_from_detection_result(
            frame_a, [_det(7, (40, 40, 90, 90))], live_track_ids={7}
        )
        points_a = tracker.tracks[7].points.copy()

        frame_b = _textured_frame(shift=6)
        tracker.propagate(frame_b)                       # advances 7 to B
        points_after_b = tracker.tracks[7].points.copy()
        tracker.update_from_detection_result(frame_b, [], live_track_ids={7})

        # Retained points are the post-propagate (frame-B) points, not frame A's.
        assert np.array_equal(tracker.tracks[7].points, points_after_b)
        # And they differ from frame A's original points (object moved).
        assert not np.array_equal(tracker.tracks[7].points, points_a)
