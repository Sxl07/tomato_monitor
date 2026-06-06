"""
bench_single_inference.py — Phase 3: Single inference benchmark for Raspberry Pi 5 baseline.

Measures the per-frame inference cost of the detection pipeline on a single frame:
  - Preprocessing time  : frame extraction + BGR numpy array validation
  - Detector inference  : run_detection() call only
  - Postprocessing time : extract_detection_dicts() call only
  - Total inference time: sum of the three phases above
  - Health classifier   : predict_health() on first crop (optional, --run-health flag)

Both models are loaded once at startup before any timing begins.
No snapshots, crops, or video frames are written to disk.

Usage (on RPi 5):
    python -m scripts.benchmarks.bench_single_inference
    python -m scripts.benchmarks.bench_single_inference --no-run-health

Output includes a human-readable summary and a Markdown table ready to paste into
docs/benchmarks/raspberry-baseline.md.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Project root — ensure imports work regardless of cwd
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Temperature helpers — imported from system_monitor (single source of truth)
# ---------------------------------------------------------------------------

from scripts.benchmarks.system_monitor import format_temp, read_temperature  # noqa: E402


# ---------------------------------------------------------------------------
# Git commit hash — best-effort
# ---------------------------------------------------------------------------

def get_git_commit() -> str:
    """Return the current git HEAD commit hash (short), or 'unknown'."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(_PROJECT_ROOT),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Model loading — both models at startup, no inference yet
# ---------------------------------------------------------------------------

def load_models() -> Tuple:
    """
    Load detector and health classifier.

    Returns:
        (predictor, health_model, health_transform)
    """
    from src.infrastructure.vision.detectron_detector import build_tomato_detector
    from src.infrastructure.vision.resnet_health_classifier import build_health_model_resnet

    print("[1/2] Loading Detectron2 detector ...")
    predictor = build_tomato_detector()
    print("      Done.")

    print("[2/2] Loading ResNet-18 health classifier ...")
    health_model, health_transform = build_health_model_resnet()
    print("      Done.")
    print()

    return predictor, health_model, health_transform


# ---------------------------------------------------------------------------
# Frame extraction — single frame from video (index 0)
# ---------------------------------------------------------------------------

def extract_frame(video_path: Path) -> np.ndarray:
    """
    Open video_path and return frame index 0 as a BGR numpy array.

    Raises:
        FileNotFoundError: if the video file does not exist.
        RuntimeError: if the frame cannot be read.
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    cap = cv2.VideoCapture(str(video_path))
    try:
        ret, frame = cap.read()
        if not ret or frame is None:
            raise RuntimeError(f"Could not read frame 0 from {video_path}")
    finally:
        cap.release()

    return frame  # BGR numpy array, as expected by the detector


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def run_benchmark(run_health: bool, video_path: Path) -> dict:
    """
    Execute the single-inference benchmark and return a results dict.

    Keys:
        preprocess_s        — float, preprocessing time (frame read + validation)
        detector_s          — float, detector inference time (run_detection only)
        postprocess_s       — float, postprocessing time (extract_detection_dicts only)
        total_inference_s   — float, sum of the three phases
        health_s            — Optional[float], health classifier inference time (or None)
        num_detections      — int, number of detections found
        run_health          — bool, whether health inference was attempted
        health_skipped      — bool, True if health was requested but no detections found
        temp_before         — Optional[float], °C before inference sequence
        temp_after          — Optional[float], °C after inference sequence
        git_commit          — str, HEAD commit hash
    """
    from src.infrastructure.vision.detectron_detector import (
        extract_detection_dicts,
        run_detection,
    )
    from src.infrastructure.vision.cropper import clamp_box_xyxy, crop_from_box, expand_box
    from src.infrastructure.vision.resnet_health_classifier import predict_health

    git_commit = get_git_commit()

    print("=" * 60)
    print("Tomato Monitor — Single Inference Benchmark")
    print("=" * 60)
    print(f"Git commit : {git_commit}")
    print(f"Video      : {video_path}")
    print(f"Run health : {run_health}")
    print()

    # Load both models before any timing starts
    predictor, health_model, health_transform = load_models()

    # ----------------------------------------------------------------
    # Phase 1 — Preprocessing
    # Definition: frame read from disk + confirm it is a BGR numpy array.
    # The detector expects a BGR uint8 ndarray, which is exactly what
    # cv2.VideoCapture.read() returns, so no conversion is needed —
    # but we explicitly validate shape and dtype to mirror real usage.
    # ----------------------------------------------------------------
    print("[ Phase 1 ] Preprocessing (frame extraction + validation) ...")
    t_pre_start = time.perf_counter()
    frame = extract_frame(video_path)
    # Validate: must be a 3-channel uint8 BGR array (no conversion required here)
    assert frame.ndim == 3 and frame.shape[2] == 3, "Unexpected frame shape"
    assert frame.dtype == np.uint8, "Unexpected frame dtype"
    preprocess_s = time.perf_counter() - t_pre_start
    h, w = frame.shape[:2]
    print(f"      Frame size       : {w}×{h}")
    print(f"      Preprocessing    : {preprocess_s * 1000:.1f} ms")
    print()

    # ----------------------------------------------------------------
    # Phase 2 — Detector inference
    # Definition: time only the run_detection() call.
    # ----------------------------------------------------------------
    print("[ Phase 2 ] Detector inference (run_detection) ...")
    temp_before = read_temperature()
    print(f"      Temperature before: {format_temp(temp_before)}")

    t_det_start = time.perf_counter()
    outputs = run_detection(predictor, frame)
    detector_s = time.perf_counter() - t_det_start
    print(f"      Inference time   : {detector_s * 1000:.1f} ms")
    print()

    # ----------------------------------------------------------------
    # Phase 3 — Postprocessing
    # Definition: time only the extract_detection_dicts() call.
    # ----------------------------------------------------------------
    print("[ Phase 3 ] Postprocessing (extract_detection_dicts) ...")
    t_post_start = time.perf_counter()
    detections = extract_detection_dicts(outputs)
    postprocess_s = time.perf_counter() - t_post_start
    num_detections = len(detections)
    print(f"      Detections found : {num_detections}")
    print(f"      Postprocess time : {postprocess_s * 1000:.1f} ms")
    print()

    total_inference_s = preprocess_s + detector_s + postprocess_s

    # ----------------------------------------------------------------
    # Phase 4 — Health classifier (optional)
    # Runs on the first detection crop if --run-health and detections > 0.
    # No crops or frames are saved to disk.
    # ----------------------------------------------------------------
    health_s: Optional[float] = None
    health_skipped = False

    if run_health:
        if num_detections == 0:
            print("[ Phase 4 ] Health classifier SKIPPED (no detections found).")
            health_skipped = True
        else:
            print("[ Phase 4 ] Health classifier inference on first crop ...")
            first = detections[0]
            x1, y1, x2, y2 = first["bbox"]
            # Expand and clamp box — same logic as the real pipeline
            x1e, y1e, x2e, y2e = expand_box(x1, y1, x2, y2, w, h)
            x1c, y1c, x2c, y2c = clamp_box_xyxy(x1e, y1e, x2e, y2e, w, h)
            crop = crop_from_box(frame, (x1c, y1c, x2c, y2c))

            if crop is None or crop.size == 0:
                print("      Crop is empty — health inference skipped.")
                health_skipped = True
            else:
                t_health_start = time.perf_counter()
                health_result = predict_health(health_model, health_transform, crop)
                health_s = time.perf_counter() - t_health_start
                print(f"      Crop size        : {crop.shape[1]}×{crop.shape[0]} px")
                print(f"      Health label     : {health_result['label']} "
                      f"(confidence: {health_result['confidence']:.3f})")
                print(f"      Health inf. time : {health_s * 1000:.1f} ms")
        print()
    else:
        print("[ Phase 4 ] Health classifier SKIPPED (--no-run-health).")
        print()

    temp_after = read_temperature()
    print(f"Temperature after inference : {format_temp(temp_after)}")
    print()

    return {
        "preprocess_s": preprocess_s,
        "detector_s": detector_s,
        "postprocess_s": postprocess_s,
        "total_inference_s": total_inference_s,
        "health_s": health_s,
        "num_detections": num_detections,
        "run_health": run_health,
        "health_skipped": health_skipped,
        "temp_before": temp_before,
        "temp_after": temp_after,
        "git_commit": git_commit,
    }


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def _ms(seconds: Optional[float]) -> str:
    """Format seconds as milliseconds string, or 'N/A'."""
    if seconds is None:
        return "N/A"
    return f"{seconds * 1000:.1f} ms"


def print_summary(results: dict) -> None:
    """Print a human-readable summary to stdout."""
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Preprocessing time      : {_ms(results['preprocess_s'])}")
    print(f"  Detector inference time : {_ms(results['detector_s'])}")
    print(f"  Postprocessing time     : {_ms(results['postprocess_s'])}")
    print(f"  Total inference time    : {_ms(results['total_inference_s'])}")

    if results["run_health"]:
        if results["health_skipped"]:
            print(f"  Health classifier time  : N/A (skipped — no detections)")
        else:
            print(f"  Health classifier time  : {_ms(results['health_s'])}")
    else:
        print(f"  Health classifier time  : N/A (--no-run-health)")

    print()
    print(f"  Detections found        : {results['num_detections']}")
    print(f"  Temperature before      : {format_temp(results['temp_before'])}")
    print(f"  Temperature after       : {format_temp(results['temp_after'])}")
    print(f"  Git commit              : {results['git_commit']}")
    print()


def print_markdown_table(results: dict) -> None:
    """
    Print a Markdown-formatted block ready to paste into
    docs/benchmarks/raspberry-baseline.md.
    """
    print("=" * 60)
    print("MARKDOWN OUTPUT (paste into raspberry-baseline.md)")
    print("=" * 60)
    print()
    print("### Phase 3 — Single Inference Benchmark")
    print()
    print(f"**Git commit:** `{results['git_commit']}`")
    print()
    print("| Metric | Value |")
    print("|--------|-------|")
    print(f"| Preprocessing time (frame read + validation) | {_ms(results['preprocess_s'])} |")
    print(f"| Detector inference time (`run_detection`) | {_ms(results['detector_s'])} |")
    print(f"| Postprocessing time (`extract_detection_dicts`) | {_ms(results['postprocess_s'])} |")
    print(f"| **Total inference time** | **{_ms(results['total_inference_s'])}** |")

    if results["run_health"] and not results["health_skipped"]:
        print(f"| Health classifier time (`predict_health`) | {_ms(results['health_s'])} |")
    else:
        reason = "no detections" if results["health_skipped"] else "--no-run-health"
        print(f"| Health classifier time (`predict_health`) | N/A ({reason}) |")

    print(f"| Detections found | {results['num_detections']} |")
    print(f"| Temperature before inference | {format_temp(results['temp_before'])} |")
    print(f"| Temperature after inference | {format_temp(results['temp_after'])} |")
    print()


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 3 — Single inference benchmark for Raspberry Pi 5 baseline."
    )
    parser.add_argument(
        "--run-health",
        dest="run_health",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run health classifier on the first detected crop (default: True).",
    )
    parser.add_argument(
        "--video",
        type=Path,
        default=_PROJECT_ROOT / "data" / "videos" / "video_02.mp4",
        help="Path to the input video file (default: data/videos/video_02.mp4).",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Entry point — guarded so the script is safely importable on Windows
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = parse_args()
    try:
        results = run_benchmark(run_health=args.run_health, video_path=args.video)
        print_summary(results)
        print_markdown_table(results)
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
