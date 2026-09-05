"""RetinaNet score-threshold sweep — diagnostic benchmark (one-pass).

Purpose
-------
Evaluate the RetinaNet detector over the raw snapshots of a single monitoring
(default: Monitoring 21) at several score thresholds (0.80/0.70/0.60/0.50) while
running RetinaNet ONLY ONCE per image.

Methodology (diagnostic, controlled)
------------------------------------
The predictor is built once with ``SCORE_THRESH_TEST = 0.30`` purely for
diagnosis. For each image a single inference produces the base predictions
(score >= 0.30). The SAME base predictions are then post-filtered at
0.80/0.70/0.60/0.50 to compare thresholds WITHOUT re-inference. This is a
controlled diagnostic comparison over the same model, weights, images and base
run; it is not claimed to be bit-for-bit identical to running the predictor
directly at each threshold.

Isolation
---------
- READS only ``outputs/monitorings/{id}/snapshots/raw/*.jpg``.
- WRITES only under ``outputs/diagnostics/threshold_sweep_monitoring_{id}/``.
- Never touches the DB, ``outputs/monitorings/{id}/`` production artifacts
  (annotated_snapshots, crops, pipeline_metrics, reports), tracking, health or
  maturity. No production module is modified.

Run (on the Raspberry Pi, with active cooling)::

    python scripts/benchmarks/threshold_sweep_detection.py --monitoring-id 21

Heavy dependencies (torch/detectron2/cv2) are imported lazily inside the
functions that need them, so the pure post-filter/metrics logic in this module
is importable and unit-testable without them.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Thresholds evaluated by post-filtering the base predictions.
EVALUATED_THRESHOLDS: Tuple[float, ...] = (0.80, 0.70, 0.60, 0.50)
# Base predictor threshold used ONCE for diagnosis (kept intentionally low so
# lower-confidence proposals can be inspected via post-filtering).
BASE_PREDICTOR_THRESHOLD: float = 0.30

# Score band edges for the global distribution of base predictions.
# Each band is [low, high) except the top band which is inclusive at 1.0.
SCORE_BANDS: Tuple[Tuple[float, float], ...] = (
    (0.80, 1.00),
    (0.70, 0.80),
    (0.60, 0.70),
    (0.50, 0.60),
    (0.30, 0.50),
)


# --------------------------------------------------------------------------- #
# Pure data structures (JSON-serializable, no torch/detectron2 objects)
# --------------------------------------------------------------------------- #

@dataclass
class Prediction:
    """A single RetinaNet proposal, kept as plain JSON-safe values."""

    bbox: List[int]  # [x1, y1, x2, y2]
    score: float
    class_id: int

    def to_json(self) -> Dict[str, Any]:
        return {
            "bbox": [int(v) for v in self.bbox],
            "score": float(self.score),
            "class_id": int(self.class_id),
        }


@dataclass
class SnapshotPredictions:
    """Base predictions for one snapshot."""

    filename: str
    frame_index: Optional[int]
    predictions: List[Prediction] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {
            "filename": self.filename,
            "frame_index": self.frame_index,
            "predictions": [p.to_json() for p in self.predictions],
        }


# --------------------------------------------------------------------------- #
# Pure logic — post-filter, metrics, score bands (unit-testable, no heavy deps)
# --------------------------------------------------------------------------- #

def filter_predictions(
    predictions: List[Prediction], threshold: float
) -> List[Prediction]:
    """Return predictions with ``score >= threshold`` (inclusive comparison).

    Does not mutate bbox/score/class; returns a new filtered list.
    """
    return [p for p in predictions if p.score >= threshold]


def compute_threshold_metrics(
    snapshots: List[SnapshotPredictions], threshold: float
) -> Dict[str, Any]:
    """Compute per-threshold detection metrics over all snapshots.

    Reports total boxes, snapshots with/without boxes, boxes-per-snapshot
    stats, and score stats. All derived by post-filtering the SAME base
    predictions — no re-inference.
    """
    per_snapshot_counts: List[int] = []
    all_scores: List[float] = []
    total_boxes = 0
    snapshots_with_boxes = 0

    for snap in snapshots:
        kept = filter_predictions(snap.predictions, threshold)
        n = len(kept)
        per_snapshot_counts.append(n)
        total_boxes += n
        if n > 0:
            snapshots_with_boxes += 1
            all_scores.extend(p.score for p in kept)

    n_snap = len(snapshots)
    snapshots_without_boxes = n_snap - snapshots_with_boxes

    metrics: Dict[str, Any] = {
        "threshold": round(float(threshold), 2),
        "total_boxes": total_boxes,
        "snapshots_with_boxes": snapshots_with_boxes,
        "snapshots_without_boxes": snapshots_without_boxes,
        "boxes_per_snapshot_mean": (
            round(total_boxes / n_snap, 4) if n_snap > 0 else 0.0
        ),
        "boxes_per_snapshot_min": min(per_snapshot_counts) if per_snapshot_counts else 0,
        "boxes_per_snapshot_max": max(per_snapshot_counts) if per_snapshot_counts else 0,
    }

    if all_scores:
        metrics["score_min"] = round(min(all_scores), 4)
        metrics["score_max"] = round(max(all_scores), 4)
        metrics["score_mean"] = round(sum(all_scores) / len(all_scores), 4)
    else:
        metrics["score_min"] = None
        metrics["score_max"] = None
        metrics["score_mean"] = None

    return metrics


def compute_metrics_by_threshold(
    snapshots: List[SnapshotPredictions],
    thresholds: Tuple[float, ...] = EVALUATED_THRESHOLDS,
) -> List[Dict[str, Any]]:
    """Compute metrics for every threshold and add deltas vs the 0.80 baseline."""
    metrics = [compute_threshold_metrics(snapshots, t) for t in thresholds]

    # Locate the 0.80 baseline entry (if present) for delta computation.
    baseline = next(
        (m for m in metrics if abs(m["threshold"] - 0.80) < 1e-9), None
    )
    base_boxes = baseline["total_boxes"] if baseline else None
    base_positive = baseline["snapshots_with_boxes"] if baseline else None

    for m in metrics:
        if base_boxes is None:
            m["additional_boxes_vs_080"] = None
            m["additional_positive_snapshots_vs_080"] = None
        else:
            m["additional_boxes_vs_080"] = m["total_boxes"] - base_boxes
            m["additional_positive_snapshots_vs_080"] = (
                m["snapshots_with_boxes"] - base_positive
            )
    return metrics


def _classify_band(score: float) -> Optional[Tuple[float, float]]:
    """Return the band a score falls into ([low, high), top band inclusive)."""
    for low, high in SCORE_BANDS:
        if high >= 1.0:
            if low <= score <= high:
                return (low, high)
        elif low <= score < high:
            return (low, high)
    return None


def compute_score_bands(
    snapshots: List[SnapshotPredictions],
) -> List[Dict[str, Any]]:
    """Global distribution of base predictions across the score bands."""
    counts: Dict[Tuple[float, float], int] = {b: 0 for b in SCORE_BANDS}
    total = 0
    for snap in snapshots:
        for p in snap.predictions:
            total += 1
            band = _classify_band(p.score)
            if band is not None:
                counts[band] += 1

    bands: List[Dict[str, Any]] = []
    for low, high in SCORE_BANDS:
        c = counts[(low, high)]
        bands.append(
            {
                "band": f"[{low:.2f}, {high:.2f}{']' if high >= 1.0 else ')'}",
                "low": low,
                "high": high,
                "count": c,
                "percentage_of_base_predictions": (
                    round(100.0 * c / total, 2) if total > 0 else 0.0
                ),
            }
        )
    return bands


def parse_frame_index(filename: str) -> Optional[int]:
    """Extract the integer frame index from ``snapshot_000123.jpg`` style names."""
    stem = Path(filename).stem  # snapshot_000123
    digits = ""
    for ch in reversed(stem):
        if ch.isdigit():
            digits = ch + digits
        else:
            break
    return int(digits) if digits else None


def serialize_base_predictions(
    snapshots: List[SnapshotPredictions],
) -> List[Dict[str, Any]]:
    """Return a JSON-safe list of per-snapshot base predictions."""
    return [s.to_json() for s in snapshots]


# --------------------------------------------------------------------------- #
# Heavy path — predictor build + inference + JPEG drawing (lazy imports)
# --------------------------------------------------------------------------- #

def _build_diagnostic_predictor():
    """Build a DefaultPredictor replicating production config, EXCEPT the
    score threshold which is lowered to BASE_PREDICTOR_THRESHOLD for diagnosis.

    Replicated exactly from production (see detectron_detector.build_tomato_detector):
      - config: COCO-Detection/retinanet_R_50_FPN_1x.yaml
      - MODEL.RETINANET.NUM_CLASSES = 1
      - MODEL.WEIGHTS = DETECTION_MODEL_PATH
      - MODEL.DEVICE = DEVICE ("cpu")
    ONLY override: MODEL.RETINANET.SCORE_THRESH_TEST = 0.30.
    Nothing else is touched (INPUT.MIN_SIZE_TEST/MAX_SIZE_TEST, NMS, TOPK, ...).

    This is a PRIVATE helper local to the diagnostic script; production's
    build_tomato_detector is not modified or reused.
    """
    import os
    import detectron2
    from detectron2.config import get_cfg
    from detectron2.engine import DefaultPredictor
    from src.infrastructure.config.settings import DETECTION_MODEL_PATH, DEVICE

    cfg = get_cfg()
    cfg_path = os.path.join(
        os.path.dirname(detectron2.__file__),
        "model_zoo",
        "configs",
        "COCO-Detection",
        "retinanet_R_50_FPN_1x.yaml",
    )
    cfg.merge_from_file(cfg_path)
    cfg.MODEL.RETINANET.NUM_CLASSES = 1
    cfg.MODEL.WEIGHTS = str(DETECTION_MODEL_PATH)
    cfg.MODEL.DEVICE = DEVICE
    # ONLY diagnostic override:
    cfg.MODEL.RETINANET.SCORE_THRESH_TEST = BASE_PREDICTOR_THRESHOLD
    return DefaultPredictor(cfg)


def _extract_predictions(outputs: Dict[str, Any]) -> List[Prediction]:
    """Convert Detectron2 outputs into plain Prediction objects (no tensors)."""
    instances = outputs["instances"].to("cpu")
    if len(instances) == 0 or not instances.has("pred_boxes"):
        return []
    boxes = instances.pred_boxes.tensor.numpy()
    scores = (
        instances.scores.numpy() if instances.has("scores") else [1.0] * len(boxes)
    )
    classes = (
        instances.pred_classes.numpy()
        if instances.has("pred_classes")
        else [0] * len(boxes)
    )
    preds: List[Prediction] = []
    for box, score, cls_id in zip(boxes, scores, classes):
        x1, y1, x2, y2 = [int(v) for v in box.tolist()]
        preds.append(
            Prediction(bbox=[x1, y1, x2, y2], score=float(score), class_id=int(cls_id))
        )
    return preds


def _draw_boxes(image, predictions: List[Prediction]):
    """Draw minimal detection-only annotations (bbox + 'TOMATO <score>')."""
    import cv2

    drawn = image.copy()
    for p in predictions:
        x1, y1, x2, y2 = p.bbox
        color = (0, 255, 0)
        cv2.rectangle(drawn, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
        label = f"TOMATO {p.score:.2f}"
        cv2.putText(
            drawn,
            label,
            (int(x1), max(15, int(y1) - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            2,
        )
    return drawn


def _list_raw_snapshots(raw_dir: Path) -> List[Path]:
    """Return sorted *.jpg paths under raw_dir (by frame_index then name)."""
    files = [p for p in raw_dir.iterdir() if p.suffix.lower() == ".jpg"]

    def _key(p: Path):
        idx = parse_frame_index(p.name)
        return (idx if idx is not None else 1_000_000_000, p.name)

    return sorted(files, key=_key)


def _threshold_dir_name(threshold: float) -> str:
    """Return the per-threshold subdir name (e.g. 0.80 -> '080')."""
    return f"{int(round(threshold * 100)):03d}"


def _prepare_threshold_output_dirs(
    out_root: Path, thresholds: Tuple[float, ...] = EVALUATED_THRESHOLDS
) -> Dict[float, Path]:
    """Recreate ONLY this benchmark's per-threshold JPEG dirs, cleanly.

    Removes the ``out_root/{080,070,060,050}`` subdirectories (if present) and
    recreates them empty, so a re-run never leaves stale JPEGs from a previous
    corrida (e.g. when an image failed or disappeared). It ONLY touches the
    threshold subdirectories under ``out_root``; it never removes ``out_root``
    itself, sibling diagnostics folders, or anything under
    ``outputs/monitorings/{id}``. ``base_predictions.json`` / ``sweep_summary.json``
    live directly in ``out_root`` and are overwritten normally (not deleted here).

    Returns a {threshold: dir_path} mapping.
    """
    import shutil

    out_root.mkdir(parents=True, exist_ok=True)
    dirs: Dict[float, Path] = {}
    for t in thresholds:
        d = out_root / _threshold_dir_name(t)
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=False)
        dirs[t] = d
    return dirs


# --------------------------------------------------------------------------- #
# Orchestration (heavy path)
# --------------------------------------------------------------------------- #

def run_sweep(monitoring_id: int) -> Dict[str, Any]:
    """Run the one-pass diagnostic sweep and write all diagnostic artifacts."""
    import cv2  # lazy
    from src.infrastructure.config.settings import (
        OUTPUTS_DIR,
        DETECTION_MODEL_PATH,
        DEVICE,
    )

    raw_dir = OUTPUTS_DIR / "monitorings" / str(monitoring_id) / "snapshots" / "raw"
    out_root = OUTPUTS_DIR / "diagnostics" / f"threshold_sweep_monitoring_{monitoring_id}"

    errors: List[Dict[str, str]] = []

    if not raw_dir.is_dir():
        raise SystemExit(f"Raw snapshot directory not found: {raw_dir}")

    raw_files = _list_raw_snapshots(raw_dir)
    snapshots_found = len(raw_files)
    print(f"Found {snapshots_found} JPG snapshot(s) under {raw_dir}")
    if snapshots_found == 0:
        raise SystemExit("No JPG snapshots found; nothing to do.")

    # Clean stale JPEGs from a previous run: recreate ONLY the four threshold
    # subdirs. Never touches out_root's JSON files, sibling diagnostics, or
    # outputs/monitorings/{id}.
    threshold_dirs = _prepare_threshold_output_dirs(out_root)

    print(
        f"Building diagnostic predictor "
        f"(SCORE_THRESH_TEST={BASE_PREDICTOR_THRESHOLD})..."
    )
    predictor = _build_diagnostic_predictor()

    snapshots: List[SnapshotPredictions] = []
    inference_count = 0
    inference_seconds_total = 0.0
    t_start = time.monotonic()

    for i, path in enumerate(raw_files, start=1):
        print(f"[{i}/{snapshots_found}] {path.name}")
        image = cv2.imread(str(path))
        if image is None:
            errors.append({"filename": path.name, "error": "cv2.imread returned None"})
            continue

        try:
            t0 = time.monotonic()
            outputs = predictor(image)  # ONE inference for this snapshot
            inference_seconds_total += time.monotonic() - t0
            # Only count the image as processed after BOTH inference AND
            # extraction succeed, so inference_count == snapshots_processed.
            # The predictor still runs at most once per snapshot (no retry).
            preds = _extract_predictions(outputs)
            inference_count += 1
        except Exception as exc:  # do not fabricate metrics for a failed image
            errors.append({"filename": path.name, "error": f"{type(exc).__name__}: {exc}"})
            continue

        snap = SnapshotPredictions(
            filename=path.name,
            frame_index=parse_frame_index(path.name),
            predictions=preds,
        )
        snapshots.append(snap)

        # Diagnostic JPEGs: one per threshold (post-filtered, no re-inference).
        # A failed write is recorded and skipped; it does NOT affect
        # inference_count / snapshots_processed (the inference was valid).
        for t in EVALUATED_THRESHOLDS:
            kept = filter_predictions(preds, t)
            drawn = _draw_boxes(image, kept)
            out_path = threshold_dirs[t] / path.name
            ok = cv2.imwrite(str(out_path), drawn)
            if not ok:
                errors.append(
                    {
                        "filename": path.name,
                        "threshold": f"{t:.2f}",
                        "error": f"cv2.imwrite returned False for {out_path}",
                    }
                )

    total_seconds = time.monotonic() - t_start
    snapshots_processed = len(snapshots)

    # Persist base predictions once (reanalyze thresholds later without re-infer).
    base_pred_path = out_root / "base_predictions.json"
    with open(base_pred_path, "w", encoding="utf-8") as f:
        json.dump(serialize_base_predictions(snapshots), f, ensure_ascii=False, indent=2)

    metrics_by_threshold = compute_metrics_by_threshold(snapshots)
    score_bands = compute_score_bands(snapshots)

    summary: Dict[str, Any] = {
        "monitoring_id": monitoring_id,
        "model_path": str(DETECTION_MODEL_PATH),
        "device": DEVICE,
        "base_predictor_threshold": BASE_PREDICTOR_THRESHOLD,
        "evaluated_thresholds": list(EVALUATED_THRESHOLDS),
        "snapshots_found": snapshots_found,
        "snapshots_processed": snapshots_processed,
        "inference_count": inference_count,
        "total_seconds": round(total_seconds, 3),
        "mean_inference_seconds": (
            round(inference_seconds_total / inference_count, 4)
            if inference_count > 0
            else None
        ),
        "metrics_by_threshold": metrics_by_threshold,
        "score_bands": score_bands,
        "errors": errors,
    }

    summary_path = out_root / "sweep_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\nProcessed {snapshots_processed}/{snapshots_found} snapshot(s).")
    print(f"inference_count = {inference_count} (expected == snapshots_processed)")
    print(f"Total time: {total_seconds:.2f}s")
    if inference_count > 0:
        print(f"Mean inference: {inference_seconds_total / inference_count:.4f}s")
    print(f"Summary: {summary_path}")
    print(f"Base predictions: {base_pred_path}")
    return summary


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RetinaNet score-threshold sweep (one-pass diagnostic)."
    )
    parser.add_argument(
        "--monitoring-id",
        type=int,
        default=21,
        help="Monitoring id whose raw snapshots are swept (default: 21).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    run_sweep(args.monitoring_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
