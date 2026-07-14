"""Snapshot Analysis Report Writer — Generates CSV reports and pipeline_metrics.json.

Lightweight file writer for the deferred analysis phase. Produces:
- per_snapshot.csv: One row per processed snapshot
- per_detection.csv: One row per detection across all snapshots
- summary.csv: Single-row summary of the analysis
- pipeline_metrics.json: Merged metrics file (preserves capture section)

No imports of Detectron2, Torch, OpenCV, MonitoringService, pipeline_orchestrator,
or video_inspection_runner. Module must be importable without heavy dependencies.
"""

from __future__ import annotations

import copy
import csv
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class AnalysisReportWriteResult:
    """Result of writing all analysis report files."""

    paths: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


# Column schemas as constants
PER_SNAPSHOT_COLUMNS = [
    "monitoring_id", "snapshot_id", "frame_index", "image_path", "status",
    "has_detections", "detections_count", "tracked_count", "new_tracks_count",
    "reused_count", "health_executed_count", "maturity_executed_count",
    "detection_sec", "tracking_sec", "total_frame_sec", "error",
]

PER_DETECTION_COLUMNS = [
    "monitoring_id", "snapshot_id", "frame_index", "image_name", "track_id",
    "detection_id", "is_new_track", "track_hits", "reused_previous_result",
    "selected_as_best", "decision_reason", "x1", "y1", "x2", "y2", "bbox_area",
    "det_score", "health_executed", "health_label", "health_confidence",
    "prob_healthy", "prob_unhealthy", "maturity_executed", "usda_stage",
    "maturity_percent", "maturity_confidence", "maturity_warning",
    "crop_sec", "health_sec", "maturity_sec", "detection_pipeline_sec",
]

SUMMARY_COLUMNS = [
    "monitoring_id", "status", "total_snapshots", "processed_snapshots",
    "failed_snapshots", "snapshots_with_detections", "total_detection_rows",
    "unique_tomatoes", "healthy_count", "unhealthy_count", "unknown_health_count",
    "maturity_green_count", "maturity_breaker_count", "maturity_turning_count",
    "maturity_pink_count", "maturity_light_red_count", "maturity_red_count",
    "analysis_duration_seconds", "errors_count", "error_reason",
]


class SnapshotAnalysisReportWriter:
    """Writes analysis report CSV files and pipeline_metrics.json.

    Each file is written independently — failure of one does not stop others.
    All writes are atomic (write to temp, then os.replace).
    """

    def __init__(self, base_outputs_dir: Optional[Path] = None):
        if base_outputs_dir is None:
            base_outputs_dir = Path.cwd() / "outputs" / "monitorings"
        self._base_dir = base_outputs_dir

    def write_reports(
        self,
        monitoring_id: int,
        per_snapshot_rows: list[dict],
        per_detection_rows: list[dict],
        summary_row: dict,
        analysis_metrics: dict,
        profile_name: Optional[str] = None,
    ) -> AnalysisReportWriteResult:
        """Write all report files. Each file independently — failure of one doesn't stop others.

        CSVs are written first. Then pipeline_metrics.json is written with an enriched
        copy of analysis_metrics that includes CSV errors and report_files.
        """
        result = AnalysisReportWriteResult()
        reports_dir = self._base_dir / str(monitoring_id) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        metrics_path = self._base_dir / str(monitoring_id) / "pipeline_metrics.json"

        # Correction 6: Sort copies before writing (do NOT modify original lists)
        sorted_snapshot_rows = sorted(per_snapshot_rows, key=lambda r: (r.get("frame_index") or 0, r.get("snapshot_id") or 0))
        sorted_detection_rows = sorted(per_detection_rows, key=lambda r: (r.get("frame_index") or 0, r.get("detection_id") or 0, r.get("track_id") or 0))

        # --- Write CSVs first ---

        # Write per_snapshot.csv
        try:
            path = self._write_csv_atomic(
                reports_dir / "per_snapshot.csv", PER_SNAPSHOT_COLUMNS, sorted_snapshot_rows
            )
            result.paths["per_snapshot_csv"] = str(path)
        except Exception as e:
            result.errors.append(f"per_snapshot.csv: {e}")

        # Write per_detection.csv
        try:
            path = self._write_csv_atomic(
                reports_dir / "per_detection.csv", PER_DETECTION_COLUMNS, sorted_detection_rows
            )
            result.paths["per_detection_csv"] = str(path)
        except Exception as e:
            result.errors.append(f"per_detection.csv: {e}")

        # Write summary.csv
        try:
            path = self._write_csv_atomic(
                reports_dir / "summary.csv", SUMMARY_COLUMNS, [summary_row]
            )
            result.paths["summary_csv"] = str(path)
        except Exception as e:
            result.errors.append(f"summary.csv: {e}")

        # --- Build report_files based on what succeeded so far ---
        report_files: dict[str, str] = {}
        if "per_snapshot_csv" in result.paths:
            report_files["per_snapshot_csv"] = "reports/per_snapshot.csv"
        if "per_detection_csv" in result.paths:
            report_files["per_detection_csv"] = "reports/per_detection.csv"
        if "summary_csv" in result.paths:
            report_files["summary_csv"] = "reports/summary.csv"
        # Anticipate pipeline_metrics.json (self-referential)
        report_files["pipeline_metrics_json"] = "pipeline_metrics.json"

        # --- Enrich metrics for JSON (don't mutate original) ---
        metrics_for_json = copy.deepcopy(analysis_metrics)
        if result.errors:
            existing_errors = metrics_for_json.get("errors", [])
            metrics_for_json["errors"] = existing_errors + [f"Report: {e}" for e in result.errors]
            metrics_for_json["errors_count"] = len(metrics_for_json["errors"])
        metrics_for_json["report_files"] = report_files

        # --- Write/update pipeline_metrics.json ---
        try:
            path = self._write_pipeline_metrics(
                metrics_path, monitoring_id, metrics_for_json, profile_name
            )
            result.paths["pipeline_metrics_json"] = str(path)
        except Exception as e:
            result.errors.append(f"pipeline_metrics.json: {e}")
            # Remove self-referential entry since write failed
            # (report_files in the already-failed JSON is moot, but keep result consistent)

        return result

    def _write_csv_atomic(
        self, target_path: Path, columns: list[str], rows: list[dict]
    ) -> Path:
        """Write CSV with headers atomically. Empty rows still produce headers."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(target_path.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f, fieldnames=columns, extrasaction="ignore"
                )
                writer.writeheader()
                for row in rows:
                    # Replace None with empty string
                    clean_row = {k: ("" if v is None else v) for k, v in row.items()}
                    writer.writerow(clean_row)
                f.flush()
            os.replace(tmp_path, str(target_path))
        except Exception:
            # Clean up temp file on error
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return target_path

    def write_capture_metrics(
        self,
        monitoring_id: int,
        capture_metrics: dict,
        profile_name: Optional[str] = None,
    ) -> AnalysisReportWriteResult:
        """Write capture phase metrics to pipeline_metrics.json.

        Preserves existing 'analysis' section and unknown keys.
        Uses atomic write. Invalid existing JSON is not overwritten.
        """
        result = AnalysisReportWriteResult()
        metrics_path = self._base_dir / str(monitoring_id) / "pipeline_metrics.json"

        try:
            path = self._write_pipeline_metrics_section(
                metrics_path, monitoring_id, "capture", capture_metrics, profile_name
            )
            result.paths["pipeline_metrics_json"] = str(path)
        except Exception as e:
            result.errors.append(f"pipeline_metrics.json: {e}")

        return result

    def _write_pipeline_metrics_section(
        self,
        target_path: Path,
        monitoring_id: int,
        section_key: str,
        section_data: dict,
        profile_name: Optional[str],
    ) -> Path:
        """Write/update a section in pipeline_metrics.json preserving other sections."""
        existing: dict = {}
        if target_path.exists():
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if not isinstance(existing, dict):
                    raise ValueError("Not a JSON object")
            except (json.JSONDecodeError, ValueError) as e:
                raise RuntimeError(f"Invalid existing pipeline_metrics.json: {e}")

        existing["schema_version"] = 1
        existing["monitoring_id"] = monitoring_id
        if profile_name is not None:
            existing["profile_name"] = profile_name
        existing[section_key] = section_data

        # Atomic write
        target_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(target_path.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2, default=str)
                f.flush()
            os.replace(tmp_path, str(target_path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return target_path

    def _write_pipeline_metrics(
        self,
        target_path: Path,
        monitoring_id: int,
        analysis_metrics: dict,
        profile_name: Optional[str],
    ) -> Path:
        """Update pipeline_metrics.json preserving existing sections (analysis section)."""
        return self._write_pipeline_metrics_section(
            target_path, monitoring_id, "analysis", analysis_metrics, profile_name
        )
