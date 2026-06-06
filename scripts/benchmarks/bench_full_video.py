"""
bench_full_video.py — Phase 4: Full pipeline benchmark for Raspberry Pi 5 baseline.

Measures the end-to-end performance of the video inspection pipeline:
  - Total runtime (wall-clock seconds)
  - Frames processed
  - Average FPS and effective FPS
  - Peak RAM usage (via psutil, best-effort)
  - Initial, peak, and final CPU/SoC temperature
  - Detector runs vs. optical flow frames (Scene Gate effectiveness)
  - Unique tracks detected

Uses `run_video_inspection()` from the existing pipeline — no pipeline logic
is modified.  By default, annotated video and snapshots are disabled to isolate
pure pipeline computation cost.

Usage (on RPi 5):
    python -m scripts.benchmarks.bench_full_video
    python -m scripts.benchmarks.bench_full_video --no-save-video --no-save-snapshots
    python -m scripts.benchmarks.bench_full_video --max-frames 50

Output includes a human-readable summary and a Markdown table ready to paste into
docs/benchmarks/raspberry-baseline.md.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
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
            cwd=str(_PROJECT_ROOT),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# RAM monitoring — background thread, best-effort via psutil
# ---------------------------------------------------------------------------

class RamMonitor:
    """
    Background thread that periodically samples process RSS and records
    the peak value observed.  Gracefully does nothing if psutil is unavailable.
    """

    def __init__(self, interval_s: float = 0.5):
        self._interval = interval_s
        self._peak_bytes: int = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._psutil_available = False

        try:
            import psutil  # type: ignore  # noqa: F401
            self._psutil_available = True
        except ImportError:
            pass

    @property
    def available(self) -> bool:
        return self._psutil_available

    @property
    def peak_bytes(self) -> int:
        return self._peak_bytes

    @property
    def peak_mb(self) -> float:
        return self._peak_bytes / (1024 * 1024)

    def start(self) -> None:
        if not self._psutil_available:
            return
        self._running = True
        self._peak_bytes = self._current_rss()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _current_rss(self) -> int:
        import psutil  # type: ignore
        return psutil.Process(os.getpid()).memory_info().rss

    def _loop(self) -> None:
        while self._running:
            try:
                rss = self._current_rss()
                if rss > self._peak_bytes:
                    self._peak_bytes = rss
            except Exception:  # noqa: BLE001
                pass
            time.sleep(self._interval)


# ---------------------------------------------------------------------------
# Temperature monitoring — background thread that records peak temperature
# ---------------------------------------------------------------------------

class TempMonitor:
    """
    Background thread that periodically reads CPU/SoC temperature and records
    the peak value observed during the monitoring window.
    """

    def __init__(self, interval_s: float = 2.0):
        self._interval = interval_s
        self._peak_temp: Optional[float] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    @property
    def peak_temp(self) -> Optional[float]:
        return self._peak_temp

    def start(self) -> None:
        self._running = True
        temp = read_temperature()
        if temp is not None:
            self._peak_temp = temp
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _loop(self) -> None:
        while self._running:
            try:
                temp = read_temperature()
                if temp is not None:
                    if self._peak_temp is None or temp > self._peak_temp:
                        self._peak_temp = temp
            except Exception:  # noqa: BLE001
                pass
            time.sleep(self._interval)


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def run_benchmark(
    save_video: bool,
    save_snapshots: bool,
    save_crops: bool,
    max_frames: Optional[int],
) -> dict:
    """
    Execute the full pipeline benchmark and return a results dict.

    Keys:
        total_runtime_s         — float, wall-clock seconds for the pipeline run
        total_frames            — int, frames processed
        avg_fps                 — float, total_frames / total_runtime_s
        effective_fps           — float, as reported by the pipeline summary
        peak_ram_mb             — Optional[float], peak RSS in MB (None if psutil unavailable)
        temp_initial            — Optional[float], °C before pipeline run
        temp_peak               — Optional[float], °C peak during pipeline run
        temp_final              — Optional[float], °C after pipeline run
        detector_runs           — int, frames where detector actually ran
        detector_skips          — int, frames where optical flow was used
        detector_run_ratio      — float, detector_runs / total_frames
        unique_tracks_detected  — int, unique track IDs observed
        strategy_name           — str, pipeline strategy used
        video_name              — str, name of the video processed
        git_commit              — str, HEAD commit hash
        save_video              — bool, whether annotated video was saved
        save_snapshots          — bool, whether detection snapshots were saved
        save_crops              — bool, whether detection crops were saved
        max_frames              — Optional[int], frame limit (None = all frames)
        avg_detector_frame_sec  — float, avg time per detector frame
        avg_skipped_frame_sec   — float, avg time per skipped frame
        reason_counts           — dict, detector reason breakdown
    """
    from src.infrastructure.vision.video_inspection_runner import run_video_inspection

    git_commit = get_git_commit()

    print("=" * 60)
    print("Tomato Monitor — Full Pipeline Benchmark")
    print("=" * 60)
    print(f"Git commit       : {git_commit}")
    print(f"Save video       : {save_video}")
    print(f"Save snapshots   : {save_snapshots}")
    print(f"Save crops       : {save_crops}")
    print(f"Max frames       : {max_frames if max_frames is not None else 'all'}")
    print()

    # --- Read initial temperature ---
    temp_initial = read_temperature()
    print(f"Temperature (initial) : {format_temp(temp_initial)}")
    print()

    # --- Start background monitors ---
    ram_monitor = RamMonitor(interval_s=0.5)
    temp_monitor = TempMonitor(interval_s=2.0)

    ram_monitor.start()
    temp_monitor.start()

    # --- Run the pipeline ---
    print("Running full pipeline ...")
    print("-" * 60)

    t_start = time.perf_counter()

    summary = run_video_inspection(
        strategy_name="benchmark_full_pipeline",
        enable_sparse_detection=True,
        enable_flow_propagation=True,
        use_scene_gate=True,
        min_frames_between_detections=18,
        max_frames_without_detection=45,
        force_detect_on_first_frame=True,
        save_detection_snapshots=save_snapshots,
        save_detection_crops=save_crops,
        save_annotated_video=save_video,
        video_index=0,
        max_frames_to_process=max_frames,
        verbose=True,
    )

    total_runtime_s = time.perf_counter() - t_start

    print("-" * 60)
    print()

    # --- Stop background monitors ---
    ram_monitor.stop()
    temp_monitor.stop()

    # --- Read final temperature ---
    temp_final = read_temperature()
    temp_peak = temp_monitor.peak_temp

    # --- Compute derived metrics ---
    total_frames = summary["total_frames"]
    avg_fps = total_frames / total_runtime_s if total_runtime_s > 0 else 0.0

    # --- Build results dict ---
    results = {
        "total_runtime_s": total_runtime_s,
        "total_frames": total_frames,
        "avg_fps": avg_fps,
        "effective_fps": summary["effective_fps"],
        "peak_ram_mb": ram_monitor.peak_mb if ram_monitor.available else None,
        "temp_initial": temp_initial,
        "temp_peak": temp_peak,
        "temp_final": temp_final,
        "detector_runs": summary["detector_runs"],
        "detector_skips": summary["detector_skips"],
        "detector_run_ratio": summary["detector_run_ratio"],
        "unique_tracks_detected": summary["unique_tracks_detected"],
        "strategy_name": summary["strategy_name"],
        "video_name": summary["video_name"],
        "git_commit": git_commit,
        "save_video": save_video,
        "save_snapshots": save_snapshots,
        "save_crops": save_crops,
        "max_frames": max_frames,
        "avg_detector_frame_sec": summary["avg_detector_frame_sec"],
        "avg_skipped_frame_sec": summary["avg_skipped_frame_sec"],
        "avg_propagation_sec": summary["avg_propagation_sec"],
        "reason_counts": {
            "first_frame": summary["reason_first_frame"],
            "scene_gate": summary["reason_scene_gate"],
            "scene_gate_blocked": summary["reason_scene_gate_blocked"],
            "max_gap_force": summary["reason_max_gap_force"],
            "min_gap_ready": summary["reason_min_gap_ready"],
            "cooldown": summary["reason_cooldown"],
            "full_detection": summary["reason_full_detection"],
        },
    }

    return results


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def _format_ram(value: Optional[float]) -> str:
    """Format RAM in MB, or 'N/A'."""
    if value is None:
        return "N/A (psutil not available)"
    return f"{value:.1f} MB"


def print_summary(results: dict) -> None:
    """Print a human-readable summary to stdout."""
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Video               : {results['video_name']}")
    print(f"  Strategy            : {results['strategy_name']}")
    print(f"  Total frames        : {results['total_frames']}")
    print(f"  Total runtime       : {results['total_runtime_s']:.2f} s")
    print(f"  Average FPS         : {results['avg_fps']:.2f}")
    print(f"  Effective FPS       : {results['effective_fps']:.2f}")
    print()
    print(f"  Peak RAM            : {_format_ram(results['peak_ram_mb'])}")
    print()
    print(f"  Temp initial        : {format_temp(results['temp_initial'])}")
    print(f"  Temp peak           : {format_temp(results['temp_peak'])}")
    print(f"  Temp final          : {format_temp(results['temp_final'])}")
    print()
    print(f"  Detector runs       : {results['detector_runs']}")
    print(f"  Detector skips      : {results['detector_skips']}")
    print(f"  Detector run ratio  : {results['detector_run_ratio']:.2%}")
    print(f"  Avg detector frame  : {results['avg_detector_frame_sec'] * 1000:.1f} ms")
    print(f"  Avg skipped frame   : {results['avg_skipped_frame_sec'] * 1000:.1f} ms")
    print(f"  Avg propagation     : {results['avg_propagation_sec'] * 1000:.1f} ms")
    print()
    print(f"  Unique tracks       : {results['unique_tracks_detected']}")
    print()
    print(f"  Scene Gate reasons  :")
    for reason, count in results["reason_counts"].items():
        print(f"    {reason:24s}: {count}")
    print()
    print(f"  Save video          : {results['save_video']}")
    print(f"  Save snapshots      : {results['save_snapshots']}")
    print(f"  Save crops          : {results['save_crops']}")
    print(f"  Max frames          : {results['max_frames'] if results['max_frames'] is not None else 'all'}")
    print(f"  Git commit          : {results['git_commit']}")
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
    print("### Phase 4 — Full Pipeline Benchmark")
    print()
    print(f"**Git commit:** `{results['git_commit']}`")
    print()
    print("| Metric | Value |")
    print("|--------|-------|")
    print(f"| Video | {results['video_name']} |")
    print(f"| Total frames processed | {results['total_frames']} |")
    print(f"| Total runtime | {results['total_runtime_s']:.2f} s |")
    print(f"| Average FPS (wall-clock) | {results['avg_fps']:.2f} |")
    print(f"| Effective FPS (pipeline) | {results['effective_fps']:.2f} |")
    print(f"| Peak RAM | {_format_ram(results['peak_ram_mb'])} |")
    print(f"| Temperature (initial) | {format_temp(results['temp_initial'])} |")
    print(f"| Temperature (peak) | {format_temp(results['temp_peak'])} |")
    print(f"| Temperature (final) | {format_temp(results['temp_final'])} |")
    print(f"| Detector runs | {results['detector_runs']} |")
    print(f"| Detector skips (optical flow) | {results['detector_skips']} |")
    print(f"| Detector run ratio | {results['detector_run_ratio']:.2%} |")
    print(f"| Avg time per detector frame | {results['avg_detector_frame_sec'] * 1000:.1f} ms |")
    print(f"| Avg time per skipped frame | {results['avg_skipped_frame_sec'] * 1000:.1f} ms |")
    print(f"| Unique tracks detected | {results['unique_tracks_detected']} |")
    print(f"| Annotated video saved | {'Yes' if results['save_video'] else 'No'} |")
    print(f"| Snapshots saved | {'Yes' if results['save_snapshots'] else 'No'} |")
    print()
    print("**Scene Gate reason breakdown:**")
    print()
    print("| Reason | Count |")
    print("|--------|-------|")
    for reason, count in results["reason_counts"].items():
        print(f"| {reason} | {count} |")
    print()


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 4 — Full pipeline benchmark for Raspberry Pi 5 baseline."
    )
    parser.add_argument(
        "--no-save-video",
        dest="save_video",
        action="store_false",
        default=False,
        help="Disable saving annotated video (default: disabled for benchmark).",
    )
    parser.add_argument(
        "--save-video",
        dest="save_video",
        action="store_true",
        help="Enable saving annotated video.",
    )
    parser.add_argument(
        "--no-save-snapshots",
        dest="save_snapshots",
        action="store_false",
        default=False,
        help="Disable saving detection snapshots (default: disabled for benchmark).",
    )
    parser.add_argument(
        "--save-snapshots",
        dest="save_snapshots",
        action="store_true",
        help="Enable saving detection snapshots.",
    )
    parser.add_argument(
        "--no-save-crops",
        dest="save_crops",
        action="store_false",
        default=False,
        help="Disable saving detection crops (default: disabled for benchmark).",
    )
    parser.add_argument(
        "--save-crops",
        dest="save_crops",
        action="store_true",
        help="Enable saving detection crops.",
    )
    parser.add_argument(
        "--video",
        type=Path,
        default=None,
        help="Path to the input video file (default: uses pipeline video_index=0).",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Maximum number of frames to process (default: all frames).",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Entry point — guarded so the script is safely importable on Windows
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = parse_args()

    # If a custom video path is provided, warn that it is not directly supported
    # by run_video_inspection (which uses video_index). The user should place their
    # video in data/videos/ and it will be picked up by index 0 (sorted order).
    if args.video is not None:
        print(f"WARNING: --video flag provided ({args.video}), but run_video_inspection")
        print("         uses video_index=0 (sorted files in VIDEOS_DIR).")
        print("         Ensure your video is the first file in data/videos/ (sorted).")
        print()

    try:
        results = run_benchmark(
            save_video=args.save_video,
            save_snapshots=args.save_snapshots,
            save_crops=args.save_crops,
            max_frames=args.max_frames,
        )
        print_summary(results)
        print_markdown_table(results)
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
