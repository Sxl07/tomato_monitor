"""Unit tests for the RetinaNet threshold-sweep diagnostic (pure logic only).

Covers the post-filter, inclusive comparison, score bands, per-threshold
metrics and base-prediction serialization. RetinaNet is NEVER built or run here;
the heavy predictor/cv2 paths live behind lazy imports in the script and are not
exercised.
"""

from __future__ import annotations

import json

from scripts.benchmarks.threshold_sweep_detection import (
    Prediction,
    SnapshotPredictions,
    filter_predictions,
    compute_threshold_metrics,
    compute_metrics_by_threshold,
    compute_score_bands,
    parse_frame_index,
    serialize_base_predictions,
    _prepare_threshold_output_dirs,
    _threshold_dir_name,
    SCORE_BANDS,
    EVALUATED_THRESHOLDS,
    BASE_PREDICTOR_THRESHOLD,
)


def _pred(score, bbox=(0, 0, 10, 10), class_id=0):
    return Prediction(bbox=list(bbox), score=score, class_id=class_id)


# --------------------------------------------------------------------------- #
# 1 & 2. Post-filter counts + inclusive comparison
# --------------------------------------------------------------------------- #

class TestPostFilter:
    def test_counts_across_thresholds(self):
        preds = [_pred(0.85), _pred(0.75), _pred(0.65), _pred(0.55), _pred(0.35)]
        assert len(filter_predictions(preds, 0.80)) == 1
        assert len(filter_predictions(preds, 0.70)) == 2
        assert len(filter_predictions(preds, 0.60)) == 3
        assert len(filter_predictions(preds, 0.50)) == 4

    def test_inclusive_boundary_080(self):
        # A score of exactly 0.80 must be included at threshold 0.80.
        preds = [_pred(0.80)]
        assert len(filter_predictions(preds, 0.80)) == 1

    def test_inclusive_boundaries_all(self):
        for t in (0.80, 0.70, 0.60, 0.50):
            assert len(filter_predictions([_pred(t)], t)) == 1

    def test_below_threshold_excluded(self):
        assert filter_predictions([_pred(0.7999)], 0.80) == []

    def test_does_not_mutate_input(self):
        preds = [_pred(0.85), _pred(0.35)]
        _ = filter_predictions(preds, 0.80)
        assert len(preds) == 2  # original list untouched
        assert preds[0].score == 0.85 and preds[1].score == 0.35


# --------------------------------------------------------------------------- #
# 3. Score bands (boundary classification)
# --------------------------------------------------------------------------- #

class TestScoreBands:
    def _bands_by_range(self, snaps):
        return {(b["low"], b["high"]): b["count"] for b in compute_score_bands(snaps)}

    def test_boundary_membership(self):
        # 0.80 -> top band; 0.70 -> [0.70,0.80); 0.60 -> [0.60,0.70);
        # 0.50 -> [0.50,0.60); 0.30 -> [0.30,0.50); 1.00 -> top band (inclusive).
        snap = SnapshotPredictions(
            filename="snapshot_000000.jpg",
            frame_index=0,
            predictions=[
                _pred(1.00), _pred(0.80), _pred(0.70),
                _pred(0.60), _pred(0.50), _pred(0.30),
            ],
        )
        counts = self._bands_by_range([snap])
        assert counts[(0.80, 1.00)] == 2   # 1.00 and 0.80
        assert counts[(0.70, 0.80)] == 1   # 0.70
        assert counts[(0.60, 0.70)] == 1   # 0.60
        assert counts[(0.50, 0.60)] == 1   # 0.50
        assert counts[(0.30, 0.50)] == 1   # 0.30

    def test_just_below_080_goes_to_070_band(self):
        snap = SnapshotPredictions("s.jpg", 0, [_pred(0.7999)])
        counts = self._bands_by_range([snap])
        assert counts[(0.70, 0.80)] == 1
        assert counts[(0.80, 1.00)] == 0

    def test_percentages_sum_to_100_when_predictions_present(self):
        snap = SnapshotPredictions("s.jpg", 0, [_pred(0.85), _pred(0.55), _pred(0.35)])
        bands = compute_score_bands([snap])
        total_pct = sum(b["percentage_of_base_predictions"] for b in bands)
        # Each band is rounded to 2 decimals, so the total may differ from 100
        # by a small per-band rounding residue (e.g. 3 preds -> 3x33.33 = 99.99).
        assert abs(total_pct - 100.0) < 0.5

    def test_empty_predictions_zero_counts(self):
        bands = compute_score_bands([SnapshotPredictions("s.jpg", 0, [])])
        assert all(b["count"] == 0 for b in bands)
        assert all(b["percentage_of_base_predictions"] == 0.0 for b in bands)


# --------------------------------------------------------------------------- #
# 4. Per-threshold metrics
# --------------------------------------------------------------------------- #

class TestThresholdMetrics:
    def test_total_boxes_and_snapshots_with_boxes(self):
        snaps = [
            SnapshotPredictions("s0.jpg", 0, [_pred(0.85), _pred(0.75)]),  # 2 boxes
            SnapshotPredictions("s1.jpg", 1, [_pred(0.55)]),               # 1 box
            SnapshotPredictions("s2.jpg", 2, []),                          # 0 boxes
        ]
        m080 = compute_threshold_metrics(snaps, 0.80)
        # Only the single 0.85 passes 0.80 -> 1 box in 1 snapshot.
        assert m080["total_boxes"] == 1
        assert m080["snapshots_with_boxes"] == 1
        assert m080["snapshots_without_boxes"] == 2

        m050 = compute_threshold_metrics(snaps, 0.50)
        # 0.85 + 0.75 + 0.55 = 3 boxes across 2 snapshots.
        assert m050["total_boxes"] == 3
        assert m050["snapshots_with_boxes"] == 2
        assert m050["snapshots_without_boxes"] == 1

    def test_boxes_per_snapshot_stats(self):
        snaps = [
            SnapshotPredictions("s0.jpg", 0, [_pred(0.9), _pred(0.9), _pred(0.9)]),
            SnapshotPredictions("s1.jpg", 1, [_pred(0.9)]),
        ]
        m = compute_threshold_metrics(snaps, 0.80)
        assert m["total_boxes"] == 4
        assert m["boxes_per_snapshot_min"] == 1
        assert m["boxes_per_snapshot_max"] == 3
        assert m["boxes_per_snapshot_mean"] == 2.0

    def test_score_stats_present_when_boxes(self):
        snaps = [SnapshotPredictions("s0.jpg", 0, [_pred(0.85), _pred(0.95)])]
        m = compute_threshold_metrics(snaps, 0.80)
        assert m["score_min"] == 0.85
        assert m["score_max"] == 0.95
        assert m["score_mean"] == 0.9

    def test_score_stats_none_when_no_boxes(self):
        snaps = [SnapshotPredictions("s0.jpg", 0, [_pred(0.4)])]
        m = compute_threshold_metrics(snaps, 0.80)
        assert m["total_boxes"] == 0
        assert m["score_min"] is None
        assert m["score_max"] is None
        assert m["score_mean"] is None

    def test_deltas_vs_080(self):
        snaps = [SnapshotPredictions("s0.jpg", 0, [_pred(0.85), _pred(0.55)])]
        metrics = compute_metrics_by_threshold(snaps)
        by_t = {m["threshold"]: m for m in metrics}
        # 0.80 baseline: 1 box, 1 positive snapshot.
        assert by_t[0.80]["additional_boxes_vs_080"] == 0
        assert by_t[0.80]["additional_positive_snapshots_vs_080"] == 0
        # 0.50: 2 boxes -> +1 box; same single snapshot -> +0 positive snapshots.
        assert by_t[0.50]["additional_boxes_vs_080"] == 1
        assert by_t[0.50]["additional_positive_snapshots_vs_080"] == 0

    def test_all_thresholds_present(self):
        snaps = [SnapshotPredictions("s0.jpg", 0, [_pred(0.9)])]
        metrics = compute_metrics_by_threshold(snaps)
        assert [m["threshold"] for m in metrics] == list(EVALUATED_THRESHOLDS)


# --------------------------------------------------------------------------- #
# 5. Base-predictions serialization (JSON-safe)
# --------------------------------------------------------------------------- #

class TestSerialization:
    def test_json_safe_roundtrip(self):
        snaps = [
            SnapshotPredictions(
                "snapshot_000003.jpg", 3,
                [_pred(0.83, bbox=(1, 2, 30, 40), class_id=0)],
            )
        ]
        data = serialize_base_predictions(snaps)
        # Must be JSON-serializable without custom encoders.
        text = json.dumps(data)
        reloaded = json.loads(text)
        assert reloaded[0]["filename"] == "snapshot_000003.jpg"
        assert reloaded[0]["frame_index"] == 3
        pred = reloaded[0]["predictions"][0]
        assert pred["bbox"] == [1, 2, 30, 40]
        assert pred["score"] == 0.83
        assert pred["class_id"] == 0

    def test_bbox_ints_and_score_float(self):
        p = Prediction(bbox=[1, 2, 3, 4], score=0.5, class_id=0).to_json()
        assert all(isinstance(v, int) for v in p["bbox"])
        assert isinstance(p["score"], float)
        assert isinstance(p["class_id"], int)


# --------------------------------------------------------------------------- #
# Helpers / constants
# --------------------------------------------------------------------------- #

class TestHelpers:
    def test_parse_frame_index(self):
        assert parse_frame_index("snapshot_000123.jpg") == 123
        assert parse_frame_index("snapshot_000000.jpg") == 0
        assert parse_frame_index("no_digits.jpg") is None

    def test_threshold_dir_name(self):
        assert _threshold_dir_name(0.80) == "080"
        assert _threshold_dir_name(0.70) == "070"
        assert _threshold_dir_name(0.60) == "060"
        assert _threshold_dir_name(0.50) == "050"

    def test_constants(self):
        assert EVALUATED_THRESHOLDS == (0.80, 0.70, 0.60, 0.50)
        assert BASE_PREDICTOR_THRESHOLD == 0.30
        # Bands cover the full [0.30, 1.00] diagnostic range.
        assert SCORE_BANDS[0] == (0.80, 1.00)
        assert SCORE_BANDS[-1] == (0.30, 0.50)


# --------------------------------------------------------------------------- #
# Safe cleanup of the per-threshold output dirs (real tmp filesystem, no cv2)
# --------------------------------------------------------------------------- #

class TestPrepareThresholdOutputDirs:
    def test_creates_four_threshold_dirs(self, tmp_path):
        out_root = tmp_path / "threshold_sweep_monitoring_21"
        dirs = _prepare_threshold_output_dirs(out_root)

        assert set(dirs.keys()) == set(EVALUATED_THRESHOLDS)
        for t in EVALUATED_THRESHOLDS:
            d = out_root / _threshold_dir_name(t)
            assert d.is_dir()
            assert dirs[t] == d

    def test_removes_stale_jpegs_in_threshold_dirs(self, tmp_path):
        out_root = tmp_path / "threshold_sweep_monitoring_21"
        # Seed a stale JPEG in the 0.80 dir from a "previous run".
        stale_dir = out_root / "080"
        stale_dir.mkdir(parents=True)
        stale_file = stale_dir / "snapshot_999999.jpg"
        stale_file.write_bytes(b"stale")
        assert stale_file.exists()

        _prepare_threshold_output_dirs(out_root)

        # The threshold dir is recreated empty; the stale file is gone.
        assert stale_dir.is_dir()
        assert not stale_file.exists()
        assert list(stale_dir.iterdir()) == []

    def test_does_not_touch_sibling_or_root_files(self, tmp_path):
        out_root = tmp_path / "threshold_sweep_monitoring_21"
        out_root.mkdir(parents=True)
        # Files living directly in out_root must be preserved.
        base_json = out_root / "base_predictions.json"
        summary_json = out_root / "sweep_summary.json"
        base_json.write_text("[]", encoding="utf-8")
        summary_json.write_text("{}", encoding="utf-8")
        # A sibling diagnostics folder must be untouched.
        sibling = tmp_path / "threshold_sweep_monitoring_20"
        sibling.mkdir(parents=True)
        sibling_file = sibling / "keep.txt"
        sibling_file.write_text("keep", encoding="utf-8")

        _prepare_threshold_output_dirs(out_root)

        assert base_json.exists()
        assert summary_json.exists()
        assert sibling.is_dir()
        assert sibling_file.exists()

    def test_idempotent_across_runs(self, tmp_path):
        out_root = tmp_path / "threshold_sweep_monitoring_21"
        _prepare_threshold_output_dirs(out_root)
        # Drop a file, then re-prepare: the file must be cleaned.
        leftover = out_root / "070" / "snapshot_000000.jpg"
        leftover.write_bytes(b"x")
        _prepare_threshold_output_dirs(out_root)
        assert not leftover.exists()
        # All four dirs still present and empty.
        for t in EVALUATED_THRESHOLDS:
            d = out_root / _threshold_dir_name(t)
            assert d.is_dir()
            assert list(d.iterdir()) == []
