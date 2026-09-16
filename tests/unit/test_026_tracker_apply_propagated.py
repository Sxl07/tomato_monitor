"""SPEC 026 — POST-method unit + property tests for
SimpleTracker.apply_propagated_positions.

These are written AFTER the method exists (they are NOT the pre-fix RED test,
which lives at the VideoAnalysisService level). They assert the deliberate,
minimal semantics of the new method:

  - CA-01: updates ONLY track.bbox of an existing track; track_id, hits,
    best_area, last_health, last_maturity, has_been_processed unchanged; no new
    track; works with a large (>120px) bbox change.
  - CA-03: two tracks are updated independently; no merge / id swap.
  - CA-04: unknown track_id is ignored (no track created, no exception);
    empty list is a no-op.
  - CA-05: no counters / inference side effects (there are no counters on
    SimpleTracker; only track.bbox changes).
  - missed intact: propagation does not reset/alter missed.
"""

import math

import pytest

from src.infrastructure.vision.tracker_adapter import SimpleTracker, bbox_area


def _new_track(tracker, bbox, score=0.9):
    out = tracker.update([{"bbox": bbox, "score": score}])
    return out[0]["track_id"]


def _prop(track_id, bbox, **extra):
    d = {
        "track_id": track_id,
        "bbox": bbox,
        "det_score": 0.9,
        "is_new_track": False,
        "track_hits": 0,
        "reused_previous_result": True,
        "propagated": True,
        "health_result": {"label": "healthy", "confidence": 0.9},
        "maturity_result": None,
    }
    d.update(extra)
    return d


# --------------------------------------------------------------------------- #
# CA-01 — position updated, identity intact
# --------------------------------------------------------------------------- #

class TestCA01PositionUpdatedIdentityIntact:
    def test_bbox_updated_other_fields_unchanged_large_shift(self):
        tracker = SimpleTracker()
        tid = _new_track(tracker, (100, 100, 140, 140))
        track = tracker.get_track(tid)

        # Seed processed-state fields so we can prove they are NOT touched.
        track.hits = 5
        track.best_area = 9999
        track.last_health = {"label": "unhealthy", "confidence": 0.8}
        track.last_maturity = {"usda_stage": "red", "maturity_percent": 90.0}
        track.has_been_processed = True
        track.missed = 2
        original_score = track.score
        n_before = len(tracker.tracks)

        # Large (>120px) displacement.
        tracker.apply_propagated_positions([_prop(tid, (300, 100, 340, 140))])

        assert track.bbox == (300, 100, 340, 140)  # ONLY bbox changed
        assert track.track_id == tid
        assert track.hits == 5
        assert track.best_area == 9999
        assert track.last_health == {"label": "unhealthy", "confidence": 0.8}
        assert track.last_maturity == {"usda_stage": "red", "maturity_percent": 90.0}
        assert track.has_been_processed is True
        assert track.score == original_score
        assert len(tracker.tracks) == n_before  # no new track

    def test_missed_is_not_modified_by_propagation(self):
        tracker = SimpleTracker()
        tid = _new_track(tracker, (10, 10, 30, 30))
        tracker.get_track(tid).missed = 2

        tracker.apply_propagated_positions([_prop(tid, (200, 10, 220, 30))])

        assert tracker.get_track(tid).missed == 2

    def test_bbox_normalized_to_int_tuple(self):
        tracker = SimpleTracker()
        tid = _new_track(tracker, (10, 10, 30, 30))
        tracker.apply_propagated_positions([_prop(tid, (12.6, 10.2, 32.9, 30.4))])
        bbox = tracker.get_track(tid).bbox
        assert bbox == (12, 10, 32, 30)
        assert all(isinstance(v, int) for v in bbox)


# --------------------------------------------------------------------------- #
# CA-03 — two independent tracks
# --------------------------------------------------------------------------- #

class TestCA03TwoIndependentTracks:
    def test_two_tracks_updated_independently_no_merge(self):
        tracker = SimpleTracker()
        out = tracker.update([
            {"bbox": (0, 0, 20, 20), "score": 0.9},
            {"bbox": (400, 400, 420, 420), "score": 0.9},
        ])
        id1 = out[0]["track_id"]
        id2 = out[1]["track_id"]
        assert id1 != id2

        tracker.apply_propagated_positions([
            _prop(id1, (50, 0, 70, 20)),
            _prop(id2, (450, 400, 470, 420)),
        ])

        assert tracker.get_track(id1).bbox == (50, 0, 70, 20)
        assert tracker.get_track(id2).bbox == (450, 400, 470, 420)
        assert len(tracker.tracks) == 2


# --------------------------------------------------------------------------- #
# CA-04 — unknown id ignored; empty list no-op
# --------------------------------------------------------------------------- #

class TestCA04SafeIgnoreAndNoop:
    def test_unknown_track_id_ignored_no_exception_no_track(self):
        tracker = SimpleTracker()
        tid = _new_track(tracker, (0, 0, 20, 20))
        before = dict(tracker.tracks)

        # 999 does not exist -> ignored; existing track still updated.
        tracker.apply_propagated_positions([
            _prop(999, (10, 10, 30, 30)),
            _prop(tid, (5, 0, 25, 20)),
        ])

        assert 999 not in tracker.tracks
        assert len(tracker.tracks) == len(before)
        assert tracker.get_track(tid).bbox == (5, 0, 25, 20)

    def test_empty_list_is_noop(self):
        tracker = SimpleTracker()
        tid = _new_track(tracker, (0, 0, 20, 20))
        bbox_before = tracker.get_track(tid).bbox
        tracker.apply_propagated_positions([])
        assert tracker.get_track(tid).bbox == bbox_before
        assert len(tracker.tracks) == 1

    def test_missing_bbox_or_track_id_skipped_gracefully(self):
        tracker = SimpleTracker()
        tid = _new_track(tracker, (0, 0, 20, 20))
        # Entries with no track_id / no bbox must be skipped without raising.
        tracker.apply_propagated_positions([
            {"bbox": (1, 1, 2, 2)},          # no track_id
            {"track_id": tid},               # no bbox
        ])
        assert tracker.get_track(tid).bbox == (0, 0, 20, 20)


# --------------------------------------------------------------------------- #
# CA-05 — continuity after >120px displacement (association succeeds post-fix)
# --------------------------------------------------------------------------- #

class TestContinuityAfterLargeShift:
    def test_update_after_propagation_keeps_same_id(self):
        tracker = SimpleTracker()
        tid = _new_track(tracker, (100, 100, 140, 140))

        # Optical Flow moved the track by 160px (> 120px center distance).
        tracker.apply_propagated_positions([_prop(tid, (260, 100, 300, 140))])

        # Real detection arrives at the displaced position -> same id.
        out = tracker.update([{"bbox": (262, 100, 302, 140), "score": 0.9}])
        assert out[0]["is_new_track"] is False
        assert out[0]["track_id"] == tid
        assert len(tracker.tracks) == 1

    def test_without_propagation_large_shift_fragments(self):
        # Control: without applying propagation, the same 160px jump fragments.
        tracker = SimpleTracker()
        tid = _new_track(tracker, (100, 100, 140, 140))
        out = tracker.update([{"bbox": (262, 100, 302, 140), "score": 0.9}])
        assert out[0]["is_new_track"] is True
        assert out[0]["track_id"] != tid
        assert len(tracker.tracks) == 2


# --------------------------------------------------------------------------- #
# Property-based tests (Hypothesis) — structural preservation invariant
# --------------------------------------------------------------------------- #

try:
    from hypothesis import given, strategies as st

    _HAS_HYPOTHESIS = True
except Exception:  # pragma: no cover
    _HAS_HYPOTHESIS = False


@pytest.mark.skipif(not _HAS_HYPOTHESIS, reason="hypothesis not installed")
class TestPropertyStructuralInvariant:
    @given(
        x1=st.integers(min_value=0, max_value=500),
        y1=st.integers(min_value=0, max_value=500),
        w=st.integers(min_value=1, max_value=200),
        h=st.integers(min_value=1, max_value=200),
        dx=st.integers(min_value=-400, max_value=400),
        dy=st.integers(min_value=-400, max_value=400),
    )
    def test_only_bbox_changes(self, x1, y1, w, h, dx, dy):
        tracker = SimpleTracker()
        tid = _new_track(tracker, (x1, y1, x1 + w, y1 + h))
        track = tracker.get_track(tid)
        track.hits = 3
        track.best_area = 1234
        track.last_health = {"label": "healthy", "confidence": 0.7}
        track.has_been_processed = True
        track.missed = 1
        snapshot = (
            track.track_id, track.hits, track.best_area, track.score,
            track.last_health, track.last_maturity, track.has_been_processed,
            track.missed,
        )
        next_id_before = tracker.next_track_id

        new_bbox = (x1 + dx, y1 + dy, x1 + w + dx, y1 + h + dy)
        tracker.apply_propagated_positions([_prop(tid, new_bbox)])

        after = (
            track.track_id, track.hits, track.best_area, track.score,
            track.last_health, track.last_maturity, track.has_been_processed,
            track.missed,
        )
        assert after == snapshot                       # nothing but bbox moved
        assert track.bbox == tuple(int(v) for v in new_bbox)
        assert len(tracker.tracks) == 1                # no new track
        assert tracker.next_track_id == next_id_before  # no id allocation

    @given(
        unknown_ids=st.lists(
            st.integers(min_value=100, max_value=200), min_size=0, max_size=5
        )
    )
    def test_unknown_ids_never_create_tracks(self, unknown_ids):
        tracker = SimpleTracker()
        tid = _new_track(tracker, (0, 0, 10, 10))  # allocates id 1
        n_before = len(tracker.tracks)
        prop = [_prop(uid, (uid, uid, uid + 5, uid + 5)) for uid in unknown_ids]
        tracker.apply_propagated_positions(prop)
        # None of the unknown ids (>=100) exist -> track count unchanged.
        assert len(tracker.tracks) == n_before
        assert tid in tracker.tracks
