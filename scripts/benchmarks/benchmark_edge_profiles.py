#!/usr/bin/env python3
"""
benchmark_edge_profiles.py — Profile comparison benchmark for edge vs full.

Measures per-snapshot inference time, peak RSS memory, CPU temperature, and
throughput for each execution profile. Outputs a markdown report documenting
the comparison for thesis evidence.

⚠️ Full benchmark requires Raspberry Pi 5 with active cooling.
On PC, models may not be loadable — the script handles this gracefully.

Usage:
    python scripts/benchmarks/benchmark_edge_profiles.py
    python scripts/benchmarks/benchmark_edge_profiles.py --images data/images/ --profiles edge,full
    python scripts/benchmarks/benchmark_edge_profiles.py --output docs/benchmarks/edge-profile-comparison.md
"""
from __future__ import annotations

import argparse
import datetime
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import cv2

try:
    import resource
except ImportError:
    # resource module is not available on Windows
    resource = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Project root — ensure imports work regardless of cwd
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.benchmarks.system_monitor import format_temp, read_temperature  # noqa: E402
from src.infrastructure.config.profile_loader import (  # noqa: E402
    get_config_summary,
    load_profile,
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def get_git_commit() -> str:
    """Return the current git HEAD commit hash (short), or 'unknown'."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
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


def get_peak_rss_mb() -> float:
    """Return peak RSS in MB using resource.getrusage.

    On Linux, ru_maxrss is in KB. On macOS, it is in bytes.
    On Windows, falls back to psutil or returns 0.
    """
    if resource is not None:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss_kb = usage.ru_maxrss
        if platform.system() == "Darwin":
            # macOS reports bytes
            return rss_kb / (1024 * 1024)
        # Linux reports KB
        return rss_kb / 1024

    # Windows fallback: try psutil
    try:
        import psutil  # type: ignore[import-untyped]
        process = psutil.Process(os.getpid())
        return process.memory_info().rss / (1024 * 1024)
    except ImportError:
        return 0.0


def load_images(images_dir: Path) -> list[tuple[str, Any]]:
    """Load all images from a directory.

    Returns:
        List of (filename, numpy_array) tuples.
    """
    supported_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".tiff"}
    images = []

    if not images_dir.exists():
        print(f"WARNING: Images directory not found: {images_dir}")
        return images

    for img_path in sorted(images_dir.iterdir()):
        if img_path.suffix.lower() in supported_extensions:
            img = cv2.imread(str(img_path))
            if img is not None:
                images.append((img_path.name, img))
            else:
                print(f"  WARNING: Could not read image: {img_path.name}")

    return images


def load_models() -> Optional[tuple]:
    """Load detector and health classifier models.

    Returns:
        (predictor, health_model, health_transform) or None if unavailable.
    """
    try:
        from src.infrastructure.vision.detectron_detector import (
            build_tomato_detector,
        )
        from src.infrastructure.vision.resnet_health_classifier import (
            build_health_model_resnet,
        )

        print("  Loading Detectron2 detector ...")
        predictor = build_tomato_detector()
        print("  Loading ResNet-18 health classifier ...")
        health_model, health_transform = build_health_model_resnet()
        return predictor, health_model, health_transform
    except ImportError as e:
        print(f"  WARNING: Cannot load models — {e}")
        print("  (torch/detectron2 may not be installed on this platform)")
        return None
    except Exception as e:
        print(f"  WARNING: Model loading failed — {e}")
        return None


# ---------------------------------------------------------------------------
# Benchmark execution
# ---------------------------------------------------------------------------


def run_profile_benchmark(
    profile_name: str,
    images: list[tuple[str, Any]],
    models: tuple,
) -> dict[str, Any]:
    """Run the benchmark for a single profile.

    Args:
        profile_name: "edge" or "full"
        images: List of (filename, image_array) tuples.
        models: (predictor, health_model, health_transform)

    Returns:
        Dict with benchmark results.
    """
    from src.infrastructure.vision.snapshot_inference_runner import (
        SnapshotInferenceRunner,
    )

    profile = load_profile(profile_name)
    config_summary = get_config_summary(profile)

    predictor, health_model, health_transform = models

    # Create runner with profile parameters
    runner = SnapshotInferenceRunner(
        detector=predictor,
        health_model=health_model,
        health_transform=health_transform,
        skip_maturity=profile.skip_maturity,
        detection_score_threshold=profile.detection_score_threshold,
        inference_input_size=(
            profile.inference_input_width,
            profile.inference_input_height,
        ),
        run_maturity_only_for_healthy=profile.run_maturity_only_for_healthy,
    )

    # Measure inference for each image
    per_snapshot_times_ms: list[float] = []
    total_detections = 0
    temp_start = read_temperature()

    print(f"  Running inference on {len(images)} images ...")
    for idx, (filename, image) in enumerate(images):
        t_start = time.perf_counter()
        results = runner.run_inference(image)
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0

        per_snapshot_times_ms.append(elapsed_ms)
        total_detections += len(results)

        if (idx + 1) % 5 == 0 or (idx + 1) == len(images):
            print(f"    [{idx + 1}/{len(images)}] {filename}: "
                  f"{elapsed_ms:.1f} ms, {len(results)} detections")

    temp_end = read_temperature()
    peak_rss_mb = get_peak_rss_mb()

    # Compute statistics
    if per_snapshot_times_ms:
        mean_time_ms = sum(per_snapshot_times_ms) / len(per_snapshot_times_ms)
        sorted_times = sorted(per_snapshot_times_ms)
        median_time_ms = sorted_times[len(sorted_times) // 2]
        total_time_s = sum(per_snapshot_times_ms) / 1000.0
        throughput = (len(images) / total_time_s) * 60.0 if total_time_s > 0 else 0
    else:
        mean_time_ms = 0
        median_time_ms = 0
        throughput = 0

    # Peak temperature
    temps = [t for t in [temp_start, temp_end] if t is not None]
    peak_temp = max(temps) if temps else None

    return {
        "profile": profile_name,
        "config_summary": config_summary,
        "num_images": len(images),
        "total_detections": total_detections,
        "per_snapshot_time_ms": per_snapshot_times_ms,
        "mean_inference_time_ms": mean_time_ms,
        "median_inference_time_ms": median_time_ms,
        "peak_rss_mb": peak_rss_mb,
        "peak_temperature_c": peak_temp,
        "temp_start_c": temp_start,
        "temp_end_c": temp_end,
        "throughput_snapshots_per_min": throughput,
    }


# ---------------------------------------------------------------------------
# Markdown report generation
# ---------------------------------------------------------------------------


def generate_markdown_report(
    results: list[dict[str, Any]],
    device_info: dict[str, str],
) -> str:
    """Generate a markdown comparison report from benchmark results."""
    lines: list[str] = []

    lines.append("# Comparación de perfiles: Edge vs Full")
    lines.append("")
    lines.append(f"**Estado:** Ejecutado — {device_info['date']}")
    lines.append("")

    # Device info
    lines.append("## Dispositivo")
    lines.append(f"- Hardware: {device_info['machine']}")
    lines.append(f"- OS: {device_info['system']} {device_info['release']}")
    lines.append(f"- Commit: `{device_info['git_commit']}`")
    lines.append(f"- Fecha: {device_info['date']}")
    lines.append("")

    # Command
    lines.append("## Comando de ejecución")
    lines.append("```bash")
    lines.append(
        "python scripts/benchmarks/benchmark_edge_profiles.py "
        "--images data/images/ --output docs/benchmarks/edge-profile-comparison.md"
    )
    lines.append("```")
    lines.append("")

    # Per-profile results
    lines.append("## Resultados")
    lines.append("")

    for r in results:
        profile = r["profile"]
        config = r["config_summary"]
        lines.append(f"### Perfil: {profile}")
        lines.append("| Métrica | Valor |")
        lines.append("|---|---|")
        lines.append(
            f"| Resolución de inferencia | "
            f"{config['inference_input_width']}×{config['inference_input_height']} |"
        )
        lines.append(
            f"| Skip madurez | {'Sí' if config['skip_maturity'] else 'No'} |"
        )
        lines.append(
            f"| Threshold detección | {config['detection_score_threshold']} |"
        )
        lines.append(
            f"| Cooldown Scene Gate | {config['scene_gate_cooldown_frames']} frames |"
        )
        lines.append(
            f"| Tiempo inferencia promedio | {r['mean_inference_time_ms']:.1f} ms |"
        )
        lines.append(f"| RSS pico | {r['peak_rss_mb']:.1f} MB |")
        lines.append(
            f"| Temperatura pico | {format_temp(r['peak_temperature_c'])} |"
        )
        lines.append(
            f"| Throughput | {r['throughput_snapshots_per_min']:.1f} snapshots/min |"
        )
        lines.append(f"| Imágenes procesadas | {r['num_images']} |")
        lines.append(f"| Detecciones totales | {r['total_detections']} |")
        lines.append("")

    # Comparison table (only if we have exactly 2 profiles)
    if len(results) == 2:
        lines.append("## Comparación")
        lines.append("| Métrica | Edge | Full | Diferencia |")
        lines.append("|---|---|---|---|")

        r_edge = next((r for r in results if r["profile"] == "edge"), results[0])
        r_full = next((r for r in results if r["profile"] == "full"), results[1])

        # Inference time
        diff_time = r_edge["mean_inference_time_ms"] - r_full["mean_inference_time_ms"]
        lines.append(
            f"| Tiempo inferencia | {r_edge['mean_inference_time_ms']:.1f} ms | "
            f"{r_full['mean_inference_time_ms']:.1f} ms | "
            f"{diff_time:+.1f} ms |"
        )

        # Temperature
        t_edge = format_temp(r_edge["peak_temperature_c"])
        t_full = format_temp(r_full["peak_temperature_c"])
        if r_edge["peak_temperature_c"] and r_full["peak_temperature_c"]:
            diff_temp = (
                r_edge["peak_temperature_c"] - r_full["peak_temperature_c"]
            )
            lines.append(
                f"| Temperatura pico | {t_edge} | {t_full} | {diff_temp:+.1f} °C |"
            )
        else:
            lines.append(f"| Temperatura pico | {t_edge} | {t_full} | N/A |")

        # Throughput
        diff_tp = (
            r_edge["throughput_snapshots_per_min"]
            - r_full["throughput_snapshots_per_min"]
        )
        lines.append(
            f"| Throughput | {r_edge['throughput_snapshots_per_min']:.1f}/min | "
            f"{r_full['throughput_snapshots_per_min']:.1f}/min | "
            f"{diff_tp:+.1f}/min |"
        )
        lines.append("")

    # Config summaries
    lines.append("## Configuración de perfiles")
    lines.append("")
    for r in results:
        lines.append(f"### {r['profile']}")
        lines.append("```")
        for key, value in r["config_summary"].items():
            lines.append(f"  {key}: {value}")
        lines.append("```")
        lines.append("")

    # Conclusions
    lines.append("## Conclusiones")
    lines.append("")
    if len(results) == 2:
        r_edge = next((r for r in results if r["profile"] == "edge"), results[0])
        r_full = next((r for r in results if r["profile"] == "full"), results[1])
        if r_full["mean_inference_time_ms"] > 0:
            reduction_pct = (
                (r_full["mean_inference_time_ms"] - r_edge["mean_inference_time_ms"])
                / r_full["mean_inference_time_ms"]
            ) * 100
            lines.append(
                f"- Reducción de tiempo de inferencia (edge vs full): "
                f"{reduction_pct:.1f}%"
            )
        lines.append(
            f"- Detecciones edge: {r_edge['total_detections']} vs "
            f"full: {r_full['total_detections']}"
        )
    lines.append("")

    # Decisions
    lines.append("## Decisiones derivadas")
    lines.append("- ADR-005: Perfiles de ejecución edge vs full")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark edge vs full execution profiles. "
            "⚠️ Full benchmark requires Raspberry Pi 5 with active cooling."
        )
    )
    parser.add_argument(
        "--images",
        type=Path,
        default=_PROJECT_ROOT / "data" / "images",
        help="Path to directory containing test images (default: data/images/).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_PROJECT_ROOT / "docs" / "benchmarks" / "edge-profile-comparison.md",
        help=(
            "Path for markdown output report "
            "(default: docs/benchmarks/edge-profile-comparison.md)."
        ),
    )
    parser.add_argument(
        "--profiles",
        type=str,
        default="edge,full",
        help="Comma-separated profile names to benchmark (default: edge,full).",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    args = parse_args()
    profile_names = [p.strip() for p in args.profiles.split(",")]

    print("=" * 60)
    print("Tomato Monitor — Edge Profile Comparison Benchmark")
    print("=" * 60)
    print(f"⚠️  Full benchmark requires Raspberry Pi 5 with active cooling")
    print()

    # Collect device info
    device_info = {
        "machine": platform.machine(),
        "system": platform.system(),
        "release": platform.release(),
        "git_commit": get_git_commit(),
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    print(f"Device     : {device_info['machine']} / {device_info['system']}")
    print(f"Git commit : {device_info['git_commit']}")
    print(f"Date       : {device_info['date']}")
    print(f"Profiles   : {', '.join(profile_names)}")
    print(f"Images dir : {args.images}")
    print(f"Output     : {args.output}")
    print()

    # Load test images
    print("Loading test images ...")
    images = load_images(args.images)
    if not images:
        print("ERROR: No images found. Cannot run benchmark.")
        return 1
    print(f"  Loaded {len(images)} images.")
    print()

    # Load models (once, shared across profiles)
    print("Loading models ...")
    models = load_models()
    if models is None:
        print("ERROR: Models could not be loaded. Benchmark cannot proceed.")
        print("  Ensure torch and detectron2 are installed, and model files exist.")
        return 1
    print("  Models loaded successfully.")
    print()

    # Run benchmark for each profile
    all_results: list[dict[str, Any]] = []
    for profile_name in profile_names:
        print(f"{'─' * 60}")
        print(f"Benchmarking profile: {profile_name}")
        print(f"{'─' * 60}")

        try:
            load_profile(profile_name)  # Validate profile exists
        except ValueError as e:
            print(f"  ERROR: {e}")
            print(f"  Skipping profile '{profile_name}'.")
            continue

        result = run_profile_benchmark(profile_name, images, models)
        all_results.append(result)

        print(f"  Mean inference time : {result['mean_inference_time_ms']:.1f} ms")
        print(f"  Peak RSS            : {result['peak_rss_mb']:.1f} MB")
        print(f"  Peak temperature    : {format_temp(result['peak_temperature_c'])}")
        print(f"  Throughput          : {result['throughput_snapshots_per_min']:.1f} snapshots/min")
        print()

    if not all_results:
        print("ERROR: No profiles could be benchmarked.")
        return 1

    # Generate and write markdown report
    report = generate_markdown_report(all_results, device_info)

    output_path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"Report written to: {output_path}")
    print()

    # Print summary to stdout
    print("=" * 60)
    print("BENCHMARK COMPLETE")
    print("=" * 60)
    for r in all_results:
        print(f"  {r['profile']:6s} — "
              f"{r['mean_inference_time_ms']:.1f} ms/snapshot, "
              f"{r['throughput_snapshots_per_min']:.1f} snapshots/min, "
              f"RSS {r['peak_rss_mb']:.1f} MB")

    return 0


if __name__ == "__main__":
    sys.exit(main())
