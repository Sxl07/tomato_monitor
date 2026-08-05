"""Snapshot Analysis Service — Core isolated analysis pipeline.

Processes captured snapshots through the vision pipeline (detection, tracking,
health classification, maturity estimation) without importing Detectron2 at
module level. All heavy dependencies are injected via factory callables.

This module MUST be importable without Detectron2 installed.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import cv2
import numpy as np

from src.domain.entities.inspection_result import DetectionInspectionResult
from src.domain.entities.snapshot import Snapshot
from src.domain.repositories.inspection_result_repository import InspectionResultRepository
from src.domain.repositories.snapshot_repository import SnapshotRepository


class _FatalPersistenceError(Exception):
    """Internal exception for DB failures that must stop analysis."""
    pass


@dataclass
class AnalysisProgress:
    """Tracks progress of the snapshot analysis pipeline."""

    total_snapshots: int = 0
    processed_snapshots: int = 0
    failed_snapshots: int = 0
    current_frame_index: int = -1
    unique_tracks: int = 0
    status: str = "pending"  # "pending", "running", "completed", "error"
    thermal_paused: bool = False
    thermal_current_temperature_c: Optional[float] = None
    thermal_peak_temperature_c: float = 0.0
    thermal_pause_count: int = 0
    thermal_pause_duration_seconds: float = 0.0


@dataclass
class TrackBestResult:
    """Best detection result for a specific track across all snapshots."""

    track_id: int
    snapshot_id: int
    frame_index: int
    bbox: tuple  # (x1, y1, x2, y2)
    detection_score: float
    best_area: int
    health_label: str  # "healthy", "unhealthy", "unknown"
    health_confidence: float
    maturity_stage: Optional[str]
    maturity_percent: Optional[float]


@dataclass
class AnalysisResult:
    """Final result of the snapshot analysis pipeline."""

    total_snapshots: int = 0
    processed_snapshots: int = 0
    failed_snapshots: int = 0
    unique_tomatoes: int = 0
    healthy_count: int = 0
    unhealthy_count: int = 0
    unknown_health_count: int = 0
    snapshots_with_detections: int = 0
    maturity_counts: dict = field(
        default_factory=lambda: {
            "green": 0,
            "breaker": 0,
            "turning": 0,
            "pink": 0,
            "light_red": 0,
            "red": 0,
        }
    )
    total_detection_rows: int = 0
    analysis_duration_seconds: float = 0.0
    best_results_by_track: dict = field(default_factory=dict)  # track_id → TrackBestResult
    errors: list = field(default_factory=list)
    report_paths: dict = field(default_factory=dict)
    thermal_peak_temperature_c: float = 0.0
    thermal_pause_count: int = 0
    thermal_pause_duration_seconds: float = 0.0
    thermal_cooling_warning_at_start: bool = False
    thermal_was_paused: bool = False


class SnapshotAnalysisService:
    """Processes captured snapshots through the full vision pipeline.

    All heavy ML dependencies are injected via factory callables to allow
    this module to be imported without Detectron2 installed.
    """

    def __init__(
        self,
        monitoring_id: int,
        snapshot_repo: SnapshotRepository,
        inspection_result_repo: InspectionResultRepository,
        db_session: Any,
        *,
        components_factory: Optional[Callable] = None,
        process_frame_fn: Optional[Callable] = None,
        annotation_renderer: Optional[Callable] = None,
        thermal_monitor: Any = None,
        analysis_skip_maturity: bool = False,
        log_service: Any = None,
        report_writer: Optional[object] = None,
        profile_name: Optional[str] = None,
    ):
        self._monitoring_id = monitoring_id
        self._snapshot_repo = snapshot_repo
        self._inspection_result_repo = inspection_result_repo
        self._db_session = db_session
        self._components_factory = components_factory
        self._process_frame_fn = process_frame_fn
        self._annotation_renderer = annotation_renderer
        self._thermal_monitor = thermal_monitor
        self._analysis_skip_maturity = analysis_skip_maturity
        self._log_service = log_service
        self._report_writer = report_writer
        self._profile_name = profile_name

        self._progress = AnalysisProgress()
        self._best_results_by_track: Dict[int, TrackBestResult] = {}
        self._errors: List[str] = []
        self._error_reason: Optional[str] = None
        self._per_snapshot_rows: List[dict] = []
        self._per_detection_rows: List[dict] = []

    @property
    def progress(self) -> AnalysisProgress:
        """Current analysis progress (read-only snapshot)."""
        self._refresh_thermal_progress()
        return self._progress

    @property
    def error_reason(self) -> Optional[str]:
        """Reason for fatal error, if any."""
        return self._error_reason

    def _refresh_thermal_progress(self) -> None:
        """Safely read thermal state into progress fields.

        Prefers get_session_metadata() for a consistent snapshot, falling
        back to direct property reads on older monitors.
        """
        try:
            tm = self._thermal_monitor
            if tm is None:
                return

            # Try get_session_metadata() first
            metadata = None
            get_meta_fn = getattr(tm, "get_session_metadata", None)
            if callable(get_meta_fn):
                try:
                    metadata = get_meta_fn()
                except Exception:
                    metadata = None

            if metadata and isinstance(metadata, dict):
                # Read from metadata dict
                try:
                    val = metadata.get("peak_temperature_c")
                    if val is not None and self._is_numeric(val):
                        self._progress.thermal_peak_temperature_c = max(0.0, float(val))
                except Exception:
                    pass

                try:
                    val = metadata.get("current_temperature_c")
                    if val is not None and self._is_numeric(val):
                        self._progress.thermal_current_temperature_c = max(0.0, float(val))
                    elif val is None:
                        self._progress.thermal_current_temperature_c = None
                except Exception:
                    pass

                try:
                    val = metadata.get("pause_count")
                    if val is not None and self._is_numeric(val):
                        self._progress.thermal_pause_count = max(0, int(val))
                except Exception:
                    pass

                try:
                    val = metadata.get("total_pause_duration_s")
                    if val is not None and self._is_numeric(val):
                        self._progress.thermal_pause_duration_seconds = max(0.0, float(val))
                except Exception:
                    pass

                try:
                    val = metadata.get("cooling_warning_at_start")
                    if isinstance(val, bool):
                        self._progress.thermal_cooling_warning_at_start = val
                except Exception:
                    pass

                try:
                    val = metadata.get("is_paused")
                    if isinstance(val, bool):
                        self._progress.thermal_paused = val
                except Exception:
                    pass
            else:
                # Fallback to direct property reads
                try:
                    pause_event = getattr(tm, "pause_event", None)
                    if pause_event is not None:
                        self._progress.thermal_paused = pause_event.is_set()
                except Exception:
                    pass

                try:
                    current_temp = getattr(tm, "current_temperature", None)
                    if current_temp is not None and self._is_numeric(current_temp):
                        self._progress.thermal_current_temperature_c = max(0.0, float(current_temp))
                    elif current_temp is None:
                        self._progress.thermal_current_temperature_c = None
                except Exception:
                    pass

                try:
                    peak = getattr(tm, "peak_temperature", None)
                    if peak is not None and self._is_numeric(peak):
                        self._progress.thermal_peak_temperature_c = max(0.0, float(peak))
                except Exception:
                    pass

                try:
                    pause_count = getattr(tm, "pause_count", None)
                    if pause_count is not None and self._is_numeric(pause_count):
                        self._progress.thermal_pause_count = max(0, int(pause_count))
                except Exception:
                    pass

                try:
                    total_pause = getattr(tm, "total_pause_duration_seconds", None)
                    if total_pause is not None and self._is_numeric(total_pause):
                        self._progress.thermal_pause_duration_seconds = max(0.0, float(total_pause))
                except Exception:
                    pass

                try:
                    cooling_flag = getattr(tm, "cooling_warning_at_start", None)
                    if isinstance(cooling_flag, bool):
                        self._progress.thermal_cooling_warning_at_start = cooling_flag
                except Exception:
                    pass
        except Exception:
            pass  # Thermal read failure must never crash progress

    @staticmethod
    def _is_numeric(val) -> bool:
        """Check if a value is a real numeric type (not a mock or string)."""
        if isinstance(val, bool):
            return False
        if isinstance(val, (int, float)):
            # Guard against mock objects that pass isinstance checks
            try:
                float(val)
                return True
            except (TypeError, ValueError):
                return False
        return False

    def _reset_state(self) -> None:
        """Reset all mutable state for a fresh run."""
        self._progress = AnalysisProgress()
        self._best_results_by_track = {}
        self._errors = []
        self._error_reason = None
        self._per_snapshot_rows = []
        self._per_detection_rows = []
        self._start_time = time.time()

    def run(self) -> AnalysisResult:
        """Execute the full analysis pipeline on all snapshots.

        Returns:
            AnalysisResult with final metrics and per-track best detections.
        """
        self._reset_state()
        result = None

        try:
            # Start thermal monitor (inside try — fatal if it raises)
            if self._thermal_monitor is not None:
                start_fn = getattr(self._thermal_monitor, "start", None)
                if callable(start_fn):
                    start_fn()

            result = self._run_inner(self._start_time)

        except Exception as e:
            # Catch-all for any unexpected fatal error not handled inside _run_inner
            if self._error_reason is None:
                self._error_reason = f"Fatal: {e}"
            if self._progress.status != "error":
                self._progress.status = "error"
            try:
                self._db_session.rollback()
            except Exception:
                pass
            result = self._build_result(
                snapshots_with_detections=0,
                total_detection_rows=0,
                duration=time.time() - self._start_time,
            )
        finally:
            # Stop thermal
            if self._thermal_monitor is not None:
                stop_fn = getattr(self._thermal_monitor, "stop", None)
                if callable(stop_fn):
                    try:
                        stop_fn()
                    except Exception:
                        pass
            # Generate reports AFTER thermal stop, for ALL outcomes
            if result is not None:
                self._generate_reports(result)

        return result

    def _run_inner(self, start_time: float) -> AnalysisResult:
        """Inner run logic wrapped for thermal monitor safety."""
        self._progress.status = "running"

        # 1. Get snapshots
        snapshots = self._snapshot_repo.get_by_monitoring(self._monitoring_id)

        # 2. Sort by frame_index ascending (always explicit sort)
        snapshots.sort(key=lambda s: s.frame_index)

        self._progress.total_snapshots = len(snapshots)

        # 3. If empty → return zeros
        if not snapshots:
            self._progress.status = "completed"
            result = AnalysisResult(
                analysis_duration_seconds=time.time() - start_time,
            )
            return result

        # 4. Build pipeline components exactly once
        try:
            components = self._get_components_factory()()
        except Exception as e:
            self._error_reason = f"Fatal: {e}"
            self._progress.status = "error"
            try:
                self._db_session.rollback()
            except Exception:
                pass
            return self._build_result(
                snapshots_with_detections=0,
                total_detection_rows=0,
                duration=time.time() - start_time,
            )

        # 5. Process each snapshot in order
        snapshots_with_detections = 0
        total_detection_rows = 0

        for snapshot in snapshots:
            # Update current_frame_index for EVERY snapshot attempted
            self._progress.current_frame_index = snapshot.frame_index

            # Cooperative pause for thermal monitor
            if self._thermal_monitor is not None:
                pause_event = getattr(self._thermal_monitor, "pause_event", None)
                if pause_event is not None:
                    while pause_event.is_set():
                        self._refresh_thermal_progress()
                        self._progress.thermal_paused = True
                        time.sleep(0.25)
                    if self._progress.thermal_paused:
                        self._refresh_thermal_progress()
                        self._progress.thermal_paused = False

            try:
                has_det, detection_rows, frame_result, staged_best = self._process_snapshot(snapshot, components)

                # Merge staged best results ONLY after successful commit
                self._merge_staged_best(staged_best)

                if has_det:
                    snapshots_with_detections += 1
                total_detection_rows += detection_rows
                self._progress.processed_snapshots += 1

                # Collect per_snapshot row (after commit)
                times = frame_result.get("times", {})
                self._per_snapshot_rows.append({
                    "monitoring_id": self._monitoring_id,
                    "snapshot_id": snapshot.id,
                    "frame_index": snapshot.frame_index,
                    "image_path": snapshot.image_path,
                    "status": "processed",
                    "has_detections": has_det,
                    "detections_count": frame_result.get("detections_count", 0),
                    "tracked_count": frame_result.get("tracked_count", 0),
                    "new_tracks_count": frame_result.get("new_tracks_count", 0),
                    "reused_count": frame_result.get("reused_count", 0),
                    "health_executed_count": frame_result.get("health_executed_count", 0),
                    "maturity_executed_count": frame_result.get("maturity_executed_count", 0),
                    "detection_sec": times.get("detection_sec"),
                    "tracking_sec": times.get("tracking_sec"),
                    "total_frame_sec": times.get("total_frame_sec"),
                    "error": None,
                })

                # Collect per_detection rows (only after successful commit)
                for det in frame_result.get("detections", []):
                    self._per_detection_rows.append(
                        self._build_per_detection_row(snapshot, det, frame_result)
                    )

            except _FatalPersistenceError as e:
                # DB failure is fatal — stop analysis immediately
                self._error_reason = f"Fatal: {e}"
                self._progress.status = "error"

                # Record per_snapshot row for fatal error
                self._per_snapshot_rows.append({
                    "monitoring_id": self._monitoring_id,
                    "snapshot_id": snapshot.id,
                    "frame_index": snapshot.frame_index,
                    "image_path": snapshot.image_path,
                    "status": "fatal_error",
                    "has_detections": False,
                    "detections_count": 0,
                    "tracked_count": 0,
                    "new_tracks_count": 0,
                    "reused_count": 0,
                    "health_executed_count": 0,
                    "maturity_executed_count": 0,
                    "detection_sec": None,
                    "tracking_sec": None,
                    "total_frame_sec": None,
                    "error": str(e),
                })

                try:
                    self._db_session.rollback()
                except Exception:
                    pass
                return self._build_result(
                    snapshots_with_detections=snapshots_with_detections,
                    total_detection_rows=total_detection_rows,
                    duration=time.time() - start_time,
                )
            except Exception as e:
                # Recoverable error — log, increment failed, continue
                self._progress.failed_snapshots += 1
                error_msg = f"Snapshot {snapshot.frame_index}: {type(e).__name__}: {e}"
                self._record_recoverable_error(error_msg)

                # Record per_snapshot row for failed
                self._per_snapshot_rows.append({
                    "monitoring_id": self._monitoring_id,
                    "snapshot_id": snapshot.id,
                    "frame_index": snapshot.frame_index,
                    "image_path": snapshot.image_path,
                    "status": "failed",
                    "has_detections": False,
                    "detections_count": 0,
                    "tracked_count": 0,
                    "new_tracks_count": 0,
                    "reused_count": 0,
                    "health_executed_count": 0,
                    "maturity_executed_count": 0,
                    "detection_sec": None,
                    "tracking_sec": None,
                    "total_frame_sec": None,
                    "error": str(e),
                })
                continue

            self._progress.unique_tracks = len(self._best_results_by_track)

        # 6. Persist final results: one DetectionInspectionResult per track
        try:
            self._persist_best_results()
            self._db_session.commit()
        except Exception as e:
            self._error_reason = f"Fatal: {e}"
            self._progress.status = "error"
            try:
                self._db_session.rollback()
            except Exception:
                pass
            return self._build_result(
                snapshots_with_detections=snapshots_with_detections,
                total_detection_rows=total_detection_rows,
                duration=time.time() - start_time,
            )

        # 7. Build and return AnalysisResult
        self._progress.status = "completed"
        duration = time.time() - start_time

        result = self._build_result(
            snapshots_with_detections=snapshots_with_detections,
            total_detection_rows=total_detection_rows,
            duration=duration,
        )

        return result

    def _process_snapshot(self, snapshot: Snapshot, components: Any) -> tuple[bool, int, dict, dict]:
        """Process a single snapshot through the pipeline.

        Returns:
            (has_detections, detection_rows, frame_result, staged_best) tuple.
            staged_best is a dict[int, TrackBestResult] computed locally — NOT yet
            merged into self._best_results_by_track (caller merges after commit).

        Raises:
            RuntimeError: If image cannot be read.
            ValueError: If path validation fails.
        """
        # a. Resolve image path (validates path traversal)
        image_path = self._resolve_image_path(snapshot.image_path)

        # b. Read image
        image_bgr = cv2.imread(image_path)
        if image_bgr is None:
            error_msg = f"Failed to read image: {image_path}"
            raise RuntimeError(error_msg)

        # c. Call process_frame_fn
        process_fn = self._get_process_frame_fn()
        image_name = os.path.basename(image_path)
        frame_result = process_fn(
            image_bgr, components, image_name,
            skip_maturity=self._analysis_skip_maturity,
        )

        # d. Process detections — stage best results locally
        detections = frame_result.get("detections", [])
        has_detections = len(detections) > 0
        detection_rows = len(detections)

        staged_best: Dict[int, TrackBestResult] = {}

        for det in detections:
            track_id = det.get("track_id")
            if track_id is None:
                continue

            reused = det.get("reused_previous_result", False)
            if reused:
                continue

            bbox = det["bbox"]
            x1, y1, x2, y2 = bbox
            area = max(0, x2 - x1) * max(0, y2 - y1)

            # Extract health data
            health_result = det.get("health_result") or {}
            health_label = health_result.get("label", "unknown")
            health_confidence = health_result.get("confidence", 0.0)

            # Extract maturity data
            maturity_result = det.get("maturity_result") or {}
            maturity_stage = maturity_result.get("usda_stage")
            maturity_percent = maturity_result.get("maturity_percent")

            # If analysis_skip_maturity, null out maturity fields (safety net)
            if self._analysis_skip_maturity:
                maturity_stage = None
                maturity_percent = None

            # Compare to existing best (check both committed and staged)
            existing = self._best_results_by_track.get(track_id)
            staged_existing = staged_best.get(track_id)
            # Use the better of committed vs staged as baseline
            best_so_far = existing
            if staged_existing is not None:
                if best_so_far is None or staged_existing.best_area > best_so_far.best_area:
                    best_so_far = staged_existing

            if best_so_far is None or area > best_so_far.best_area:
                staged_best[track_id] = TrackBestResult(
                    track_id=track_id,
                    snapshot_id=snapshot.id,
                    frame_index=snapshot.frame_index,
                    bbox=tuple(bbox),
                    detection_score=det.get("det_score", 0.0),
                    best_area=area,
                    health_label=health_label,
                    health_confidence=health_confidence,
                    maturity_stage=maturity_stage,
                    maturity_percent=maturity_percent,
                )

        # e. Generate annotated snapshot (recoverable — failure doesn't stop analysis)
        try:
            renderer = self._get_annotation_renderer()
            annotated = renderer(image_bgr, frame_result)
            annotated_dir = (
                f"outputs/monitorings/{self._monitoring_id}/annotated_snapshots"
            )
            os.makedirs(annotated_dir, exist_ok=True)
            annotated_path = os.path.join(
                annotated_dir, f"snapshot_{snapshot.frame_index:06d}.jpg"
            )
            ok = cv2.imwrite(annotated_path, annotated)
            if not ok:
                self._record_recoverable_error(f"Failed to save annotated: {annotated_path}")
        except Exception as e:
            self._record_recoverable_error(f"Annotation failed for snapshot {snapshot.frame_index}: {e}")

        # f. Generate crops for non-reused detections (recoverable)
        try:
            self._generate_crops(image_bgr, detections, snapshot.frame_index)
        except Exception as e:
            self._record_recoverable_error(f"Crop generation failed for snapshot {snapshot.frame_index}: {e}")

        # g. Update snapshot has_detections and commit (DB failure is FATAL)
        try:
            self._snapshot_repo.update_has_detections(snapshot.id, has_detections)
            self._db_session.commit()
        except Exception as e:
            # DB errors are fatal — session may be invalid
            raise _FatalPersistenceError(
                f"DB failure on snapshot {snapshot.frame_index}: {e}"
            ) from e

        return has_detections, detection_rows, frame_result, staged_best

    def _merge_staged_best(self, staged: Dict[int, TrackBestResult]) -> None:
        """Merge staged best results into self._best_results_by_track.

        Only called AFTER successful commit — prevents contamination on fatal errors.
        """
        for track_id, candidate in staged.items():
            existing = self._best_results_by_track.get(track_id)
            if existing is None or candidate.best_area > existing.best_area:
                self._best_results_by_track[track_id] = candidate

    def _generate_crops(
        self, image_bgr: np.ndarray, detections: list, frame_index: int
    ) -> None:
        """Generate crop images for non-reused detections."""
        from src.infrastructure.vision.cropper import (
            clamp_box_xyxy,
            crop_from_box,
            expand_box,
        )

        h, w = image_bgr.shape[:2]
        crop_dir = (
            f"outputs/monitorings/{self._monitoring_id}/crops"
            f"/snapshot_{frame_index:06d}"
        )

        crops_created = False
        for det in detections:
            if det.get("reused_previous_result", False):
                continue

            track_id = det.get("track_id")
            if track_id is None:
                continue

            bbox = det["bbox"]
            x1, y1, x2, y2 = bbox
            x1, y1, x2, y2 = clamp_box_xyxy(x1, y1, x2, y2, w, h)
            x1, y1, x2, y2 = expand_box(x1, y1, x2, y2, w, h)
            crop = crop_from_box(image_bgr, (x1, y1, x2, y2))

            if crop is not None and crop.size > 0:
                if not crops_created:
                    os.makedirs(crop_dir, exist_ok=True)
                    crops_created = True
                crop_path = os.path.join(crop_dir, f"track_{track_id:03d}.jpg")
                ok = cv2.imwrite(crop_path, crop)
                if not ok:
                    self._record_recoverable_error(f"Failed to save crop: {crop_path}")

    def _persist_best_results(self) -> None:
        """Persist one DetectionInspectionResult per track for the best view."""
        for track_id, best in self._best_results_by_track.items():
            result_entity = DetectionInspectionResult(
                snapshot_id=best.snapshot_id,
                detection_index=best.track_id,  # Use track_id as detection_index
                bbox_x1=int(best.bbox[0]),
                bbox_y1=int(best.bbox[1]),
                bbox_x2=int(best.bbox[2]),
                bbox_y2=int(best.bbox[3]),
                detection_score=best.detection_score,
                health_label=best.health_label,
                health_confidence=best.health_confidence,
                maturity_stage=best.maturity_stage,
                maturity_percent=best.maturity_percent,
            )
            self._inspection_result_repo.create(best.snapshot_id, result_entity)

    def _build_result(
        self,
        snapshots_with_detections: int,
        total_detection_rows: int,
        duration: float,
    ) -> AnalysisResult:
        """Build the final AnalysisResult from accumulated state."""
        healthy_count = 0
        unhealthy_count = 0
        unknown_health_count = 0
        maturity_counts = {
            "green": 0,
            "breaker": 0,
            "turning": 0,
            "pink": 0,
            "light_red": 0,
            "red": 0,
        }

        for best in self._best_results_by_track.values():
            if best.health_label == "healthy":
                healthy_count += 1
            elif best.health_label == "unhealthy":
                unhealthy_count += 1
            else:
                unknown_health_count += 1

            if best.maturity_stage and best.maturity_stage in maturity_counts:
                maturity_counts[best.maturity_stage] += 1

        return AnalysisResult(
            total_snapshots=self._progress.total_snapshots,
            processed_snapshots=self._progress.processed_snapshots,
            failed_snapshots=self._progress.failed_snapshots,
            unique_tomatoes=len(self._best_results_by_track),
            healthy_count=healthy_count,
            unhealthy_count=unhealthy_count,
            unknown_health_count=unknown_health_count,
            snapshots_with_detections=snapshots_with_detections,
            maturity_counts=maturity_counts,
            total_detection_rows=total_detection_rows,
            analysis_duration_seconds=duration,
            best_results_by_track=dict(self._best_results_by_track),
            errors=list(self._errors),
            thermal_peak_temperature_c=self._get_thermal_metric("peak_temperature", 0.0),
            thermal_pause_count=self._get_thermal_metric("pause_count", 0),
            thermal_pause_duration_seconds=self._get_thermal_metric("total_pause_duration_seconds", 0.0),
            thermal_cooling_warning_at_start=self._get_thermal_flag("_cooling_warning_at_start", False),
            thermal_was_paused=self._get_thermal_metric("pause_count", 0) > 0,
        )

    def _get_thermal_metric(self, attr: str, default):
        """Safely read a metric property from thermal_monitor."""
        try:
            tm = self._thermal_monitor
            if tm is None:
                return default
            val = getattr(tm, attr, default)
            if val is None:
                return default
            # Guard against non-numeric values (e.g., MagicMock)
            if not isinstance(val, (int, float)):
                return default
            return val
        except Exception:
            return default

    def _get_thermal_flag(self, attr: str, default: bool) -> bool:
        """Safely read an internal flag from thermal_monitor."""
        try:
            tm = self._thermal_monitor
            if tm is None:
                return default
            val = getattr(tm, attr, default)
            if isinstance(val, bool):
                return val
            return default
        except Exception:
            return default

    def _build_per_detection_row(self, snapshot: Snapshot, det: dict, frame_result: dict) -> dict:
        """Build a per-detection row dict for reporting."""
        bbox = det.get("bbox", [0, 0, 0, 0])
        x1, y1, x2, y2 = bbox
        area = max(0, x2 - x1) * max(0, y2 - y1)

        health_result = det.get("health_result") or {}
        maturity_result = det.get("maturity_result") or {}
        times = det.get("times", {})

        return {
            "monitoring_id": self._monitoring_id,
            "snapshot_id": snapshot.id,
            "frame_index": snapshot.frame_index,
            "image_name": frame_result.get("image_name", os.path.basename(snapshot.image_path)),
            "track_id": det.get("track_id"),
            "detection_id": det.get("detection_id"),
            "is_new_track": det.get("is_new_track", False),
            "track_hits": det.get("track_hits", 0),
            "reused_previous_result": det.get("reused_previous_result", False),
            "selected_as_best": False,  # Marked later in _generate_reports
            "decision_reason": det.get("decision_reason"),
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "bbox_area": area,
            "det_score": det.get("det_score", 0.0),
            "health_executed": det.get("health_executed", False),
            "health_label": health_result.get("label"),
            "health_confidence": health_result.get("confidence"),
            "prob_healthy": health_result.get("prob_healthy"),
            "prob_unhealthy": health_result.get("prob_unhealthy"),
            "maturity_executed": det.get("maturity_executed", False),
            "usda_stage": maturity_result.get("usda_stage"),
            "maturity_percent": maturity_result.get("maturity_percent"),
            "maturity_confidence": maturity_result.get("confidence"),
            "maturity_warning": maturity_result.get("warning"),
            "crop_sec": times.get("crop_sec"),
            "health_sec": times.get("health_sec"),
            "maturity_sec": times.get("maturity_sec"),
            "detection_pipeline_sec": times.get("detection_pipeline_sec"),
        }

    def _record_result_error(self, result: AnalysisResult, message: str) -> None:
        """Record an error discovered during report generation into both self._errors and result.errors."""
        if message not in self._errors:
            self._errors.append(message)
        if message not in result.errors:
            result.errors.append(message)
        self._emit_log("error", message)

    def _generate_reports(self, result: AnalysisResult) -> None:
        """Generate report files. Must NEVER trigger rollback or crash analysis.

        Called AFTER thermal_monitor.stop() in the finally block has already executed.
        """
        try:
            # Lazy import of writer (inside try — Correction 4)
            writer = self._report_writer
            if writer is None:
                from src.infrastructure.persistence.local.snapshot_analysis_report_writer import SnapshotAnalysisReportWriter
                writer = SnapshotAnalysisReportWriter()

            # Lazy import of profile (inside try — Correction 4)
            profile = self._profile_name
            if profile is None:
                try:
                    from src.infrastructure.config.settings import ACTIVE_PROFILE
                    profile = ACTIVE_PROFILE.name
                except Exception:
                    profile = None

            # Mark selected_as_best without overwriting decision_reason
            selected_tracks = set()
            for row in self._per_detection_rows:
                row["selected_as_best"] = False

            for track_id, best in self._best_results_by_track.items():
                if track_id in selected_tracks:
                    continue
                for row in self._per_detection_rows:
                    if (row["track_id"] == track_id
                            and row["snapshot_id"] == best.snapshot_id
                            and row["frame_index"] == best.frame_index
                            and row["x1"] == best.bbox[0]
                            and row["y1"] == best.bbox[1]
                            and row["x2"] == best.bbox[2]
                            and row["y2"] == best.bbox[3]
                            and not row["reused_previous_result"]):
                        row["selected_as_best"] = True
                        selected_tracks.add(track_id)
                        break
                else:
                    # Correction 5: sync errors into both self._errors and result.errors
                    self._record_result_error(result, f"Could not find best row for track {track_id}")

            # Build summary_row
            summary_row = {
                "monitoring_id": self._monitoring_id,
                "status": "completed" if self._error_reason is None else "error",
                "total_snapshots": result.total_snapshots,
                "processed_snapshots": result.processed_snapshots,
                "failed_snapshots": result.failed_snapshots,
                "snapshots_with_detections": result.snapshots_with_detections,
                "total_detection_rows": result.total_detection_rows,
                "unique_tomatoes": result.unique_tomatoes,
                "healthy_count": result.healthy_count,
                "unhealthy_count": result.unhealthy_count,
                "unknown_health_count": result.unknown_health_count,
                "maturity_green_count": result.maturity_counts.get("green", 0),
                "maturity_breaker_count": result.maturity_counts.get("breaker", 0),
                "maturity_turning_count": result.maturity_counts.get("turning", 0),
                "maturity_pink_count": result.maturity_counts.get("pink", 0),
                "maturity_light_red_count": result.maturity_counts.get("light_red", 0),
                "maturity_red_count": result.maturity_counts.get("red", 0),
                "analysis_duration_seconds": result.analysis_duration_seconds,
                "errors_count": len(result.errors),
                "error_reason": self._error_reason,
                "thermal_peak_temperature_c": result.thermal_peak_temperature_c,
                "thermal_pause_count": result.thermal_pause_count,
                "thermal_pause_duration_seconds": result.thermal_pause_duration_seconds,
                "thermal_cooling_warning_at_start": result.thermal_cooling_warning_at_start,
                "thermal_was_paused": result.thermal_was_paused,
            }

            # Compute execution_counts from accumulated rows
            execution_counts = {
                "new_tracks": sum(r.get("new_tracks_count", 0) for r in self._per_snapshot_rows if r["status"] == "processed"),
                "reused_rows": sum(1 for r in self._per_detection_rows if r.get("reused_previous_result")),
                "health_executions": sum(1 for r in self._per_detection_rows if r.get("health_executed")),
                "maturity_executions": sum(1 for r in self._per_detection_rows if r.get("maturity_executed")),
            }

            # Compute average_timings_seconds from processed rows
            processed_rows = [r for r in self._per_snapshot_rows if r["status"] == "processed"]

            def safe_avg(rows, key):
                vals = [r[key] for r in rows if r.get(key) is not None]
                return sum(vals) / len(vals) if vals else 0.0

            average_timings_seconds = {
                "detection": safe_avg(processed_rows, "detection_sec"),
                "tracking": safe_avg(processed_rows, "tracking_sec"),
                "total_frame": safe_avg(processed_rows, "total_frame_sec"),
                "crop": safe_avg(self._per_detection_rows, "crop_sec"),
                "health": safe_avg(self._per_detection_rows, "health_sec"),
                "maturity": safe_avg(self._per_detection_rows, "maturity_sec"),
                "detection_pipeline": safe_avg(self._per_detection_rows, "detection_pipeline_sec"),
            }

            duration = result.analysis_duration_seconds
            seconds_per_processed_snapshot = (
                (duration / result.processed_snapshots)
                if result.processed_snapshots > 0
                else 0.0
            )
            analysis_metrics = {
                "status": "completed" if self._error_reason is None else "error",
                "error_reason": self._error_reason,
                "total_snapshots": result.total_snapshots,
                "processed_snapshots": result.processed_snapshots,
                "failed_snapshots": result.failed_snapshots,
                "snapshots_with_detections": result.snapshots_with_detections,
                "total_detection_rows": result.total_detection_rows,
                "unique_tomatoes": result.unique_tomatoes,
                "healthy_count": result.healthy_count,
                "unhealthy_count": result.unhealthy_count,
                "unknown_health_count": result.unknown_health_count,
                "maturity_counts": dict(result.maturity_counts),
                "analysis_duration_seconds": duration,
                "seconds_per_processed_snapshot": seconds_per_processed_snapshot,
                "processed_snapshots_per_second": (result.processed_snapshots / duration) if duration > 0 else 0.0,
                "detection_rows_per_second": (result.total_detection_rows / duration) if duration > 0 else 0.0,
                "execution_counts": execution_counts,
                "average_timings_seconds": average_timings_seconds,
                "thermal": {
                    "peak_temperature_c": result.thermal_peak_temperature_c,
                    "pause_count": result.thermal_pause_count,
                    "total_pause_duration_seconds": result.thermal_pause_duration_seconds,
                    "cooling_warning_at_start": result.thermal_cooling_warning_at_start,
                    "was_paused": result.thermal_was_paused,
                },
                "performance_config": {
                    "analysis_skip_maturity": self._analysis_skip_maturity,
                    "profile_name": profile,
                },
                "errors_count": len(result.errors),
                "errors": list(result.errors),
                "report_files": {},  # Filled after write
            }

            # Call report_writer
            write_result = writer.write_reports(
                monitoring_id=self._monitoring_id,
                per_snapshot_rows=self._per_snapshot_rows,
                per_detection_rows=self._per_detection_rows,
                summary_row=summary_row,
                analysis_metrics=analysis_metrics,
                profile_name=profile,
            )

            # Merge report paths and errors into result
            result.report_paths = write_result.paths

            # Correction 8: Errors from writer emitted via LogService
            if write_result.errors:
                for err in write_result.errors:
                    self._record_result_error(result, f"Report: {err}")

        except Exception as e:
            # Report generation must NEVER crash analysis or trigger rollback
            self._record_result_error(result, f"Report generation failed: {e}")

    def _resolve_image_path(self, image_path: str) -> str:
        """Resolve image path and validate it's within the monitoring directory.

        Uses pathlib.Path.resolve() for robust traversal detection.
        Rejects paths that resolve outside outputs/monitorings/{monitoring_id}/.

        Raises:
            ValueError: If path is outside the allowed directory.
        """
        base_dir = (
            Path.cwd() / "outputs" / "monitorings" / str(self._monitoring_id)
        ).resolve()

        candidate = Path(image_path)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (Path.cwd() / candidate).resolve()

        try:
            resolved.relative_to(base_dir)
        except ValueError:
            raise ValueError(
                f"Path outside monitoring directory: {image_path} "
                f"(resolved to {resolved}, expected under {base_dir})"
            )

        return str(resolved)

    def _get_components_factory(self) -> Callable:
        """Return the components factory, lazily importing default if None."""
        if self._components_factory is not None:
            return self._components_factory
        # Lazy import to avoid loading Detectron2 at module level
        from src.infrastructure.vision.pipeline_orchestrator import (
            build_pipeline_components,
        )
        return build_pipeline_components

    def _get_process_frame_fn(self) -> Callable:
        """Return the process_frame function, lazily importing default if None."""
        if self._process_frame_fn is not None:
            return self._process_frame_fn
        # Lazy import to avoid loading Detectron2 at module level
        from src.infrastructure.vision.pipeline_orchestrator import process_frame
        return process_frame

    def _get_annotation_renderer(self) -> Callable:
        """Return the annotation renderer, lazily importing default if None."""
        if self._annotation_renderer is not None:
            return self._annotation_renderer
        from src.infrastructure.vision.annotation_renderer import (
            render_snapshot_annotations,
        )
        return render_snapshot_annotations

    def _emit_log(self, level: str, message: str) -> None:
        """Emit a log entry via LogService if available."""
        if self._log_service is None:
            return
        try:
            from src.application.services.log_service import LogLevel
            log_level = LogLevel(level)
            self._log_service.add_entry(
                monitoring_id=self._monitoring_id,
                level=log_level,
                source="snapshot_analysis_service",
                message=message,
            )
        except Exception:
            pass  # LogService failure must never crash analysis

    def _record_recoverable_error(self, message: str) -> None:
        """Record a recoverable error: append to errors list and emit log."""
        self._errors.append(message)
        self._emit_log("error", message)
