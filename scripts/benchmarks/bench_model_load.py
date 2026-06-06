"""
bench_model_load.py — Phase 2: Model load benchmark for Raspberry Pi 5 baseline.

Measures the cold-start load time of:
  - Detectron2 RetinaNet detector (build_tomato_detector)
  - ResNet-18 health classifier (build_health_model_resnet)

Records CPU temperature before and after each load (best-effort via vcgencmd on RPi,
or psutil on other platforms).  No inference is executed.

Usage (on RPi 5):
    python -m scripts.benchmarks.bench_model_load

Output includes a human-readable summary and a Markdown table ready to paste into
docs/benchmarks/raspberry-baseline.md.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

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
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Model load functions — no inference, load only
# ---------------------------------------------------------------------------

def load_detector():
    """Load Detectron2 RetinaNet detector and return it. No inference."""
    from src.infrastructure.vision.detectron_detector import build_tomato_detector  # noqa: PLC0415

    return build_tomato_detector()


def load_health_model():
    """Load ResNet-18 health classifier and return (model, transform). No inference."""
    from src.infrastructure.vision.resnet_health_classifier import build_health_model_resnet  # noqa: PLC0415

    return build_health_model_resnet()


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def run_benchmark() -> dict:
    """
    Execute the model load benchmark and return a results dict.

    Keys:
        detector_load_s      — float, seconds to load the detector
        health_load_s        — float, seconds to load the health model
        combined_load_s      — float, sum of both load times
        temp_before_detector — Optional[float], °C before detector load
        temp_after_detector  — Optional[float], °C after detector load
        temp_before_health   — Optional[float], °C before health model load
        temp_after_health    — Optional[float], °C after health model load
        git_commit           — str, HEAD commit hash
    """
    git_commit = get_git_commit()

    print("=" * 60)
    print("Tomato Monitor — Model Load Benchmark")
    print("=" * 60)
    print(f"Git commit : {git_commit}")
    print()

    # --- Detector ---
    print("[1/2] Loading Detectron2 detector ...")
    temp_before_detector = read_temperature()
    print(f"      Temperature before : {format_temp(temp_before_detector)}")

    t0 = time.perf_counter()
    load_detector()
    detector_load_s = time.perf_counter() - t0

    temp_after_detector = read_temperature()
    print(f"      Temperature after  : {format_temp(temp_after_detector)}")
    print(f"      Load time          : {detector_load_s:.3f} s")
    print()

    # --- Health model ---
    print("[2/2] Loading ResNet-18 health model ...")
    temp_before_health = read_temperature()
    print(f"      Temperature before : {format_temp(temp_before_health)}")

    t0 = time.perf_counter()
    load_health_model()
    health_load_s = time.perf_counter() - t0

    temp_after_health = read_temperature()
    print(f"      Temperature after  : {format_temp(temp_after_health)}")
    print(f"      Load time          : {health_load_s:.3f} s")
    print()

    combined_load_s = detector_load_s + health_load_s

    return {
        "detector_load_s": detector_load_s,
        "health_load_s": health_load_s,
        "combined_load_s": combined_load_s,
        "temp_before_detector": temp_before_detector,
        "temp_after_detector": temp_after_detector,
        "temp_before_health": temp_before_health,
        "temp_after_health": temp_after_health,
        "git_commit": git_commit,
    }


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def print_summary(results: dict) -> None:
    """Print a human-readable summary to stdout."""
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Detector load time    : {results['detector_load_s']:.3f} s")
    print(f"  Health model load time: {results['health_load_s']:.3f} s")
    print(f"  Combined load time    : {results['combined_load_s']:.3f} s")
    print()
    print(f"  Temp before detector  : {format_temp(results['temp_before_detector'])}")
    print(f"  Temp after detector   : {format_temp(results['temp_after_detector'])}")
    print(f"  Temp before health    : {format_temp(results['temp_before_health'])}")
    print(f"  Temp after health     : {format_temp(results['temp_after_health'])}")
    print()
    print(f"  Git commit            : {results['git_commit']}")
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
    print("### Phase 2 — Model Load Benchmark")
    print()
    print(f"**Git commit:** `{results['git_commit']}`")
    print()
    print("| Metric | Value |")
    print("|--------|-------|")
    print(f"| Detector (Detectron2) load time | {results['detector_load_s']:.3f} s |")
    print(f"| Health model (ResNet-18) load time | {results['health_load_s']:.3f} s |")
    print(f"| Combined load time | {results['combined_load_s']:.3f} s |")
    print(f"| Temperature before detector load | {format_temp(results['temp_before_detector'])} |")
    print(f"| Temperature after detector load | {format_temp(results['temp_after_detector'])} |")
    print(f"| Temperature before health model load | {format_temp(results['temp_before_health'])} |")
    print(f"| Temperature after health model load | {format_temp(results['temp_after_health'])} |")
    print()


# ---------------------------------------------------------------------------
# Entry point — guarded so the script is safely importable on Windows
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        results = run_benchmark()
        print_summary(results)
        print_markdown_table(results)
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
