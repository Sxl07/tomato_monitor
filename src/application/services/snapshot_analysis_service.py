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

        self._progress = AnalysisProgress()
        self._best_results_by_track: Dict[int, TrackBestResult] = {}
        self._errors: List[str] = []
        self._error_reason: Optional[str] = None

    @property
    def progress(self) -> AnalysisProgress:
        """Current analysis progress (read-only snapshot)."""
        return self._progress

    @property
    def error_reason(self) -> Optional[str]:
        """Reason for fatal error, if any."""
        return self._error_reason

    def run(self) -> AnalysisResult:
        """Execute the full analysis pipeline on all snapshots.

        Returns:
            AnalysisResult with final metrics and per-track best detections.
        """
        start_time = time.time()

        # Reset state
        self._progress = AnalysisProgress()
        self._best_results_by_track = {}
        self._errors = []
        self._error_reason = None

        self._progress.status = "running"

        try:
            # Start thermal monitor (inside try — fatal if it raises)
            if self._thermal_monitor is not None:
                start_fn = getattr(self._thermal_monitor, "start", None)
                if callable(start_fn):
                    start_fn()

            return self._run_inner(start_time)

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
            return self._build_result(
                snapshots_with_detections=0,
                total_detection_rows=0,
                duration=time.time() - start_time,
            )
        finally:
            # Always stop thermal monitor
            if self._thermal_monitor is not None:
                stop_fn = getattr(self._thermal_monitor, "stop", None)
                if callable(stop_fn):
                    try:
                        stop_fn()
                    except Exception:
                        pass

    def _run_inner(self, start_time: float) -> AnalysisResult:
        """Inner run logic wrapped for thermal monitor safety."""
        # 1. Get snapshots
        snapshots = self._snapshot_repo.get_by_monitoring(self._monitoring_id)

        # 2. Sort by frame_index ascending (always explicit sort)
        snapshots.sort(key=lambda s: s.frame_index)

        self._progress.total_snapshots = len(snapshots)

        # 3. If empty → return zeros
        if not snapshots:
            self._progress.status = "completed"
            return AnalysisResult(
                analysis_duration_seconds=time.time() - start_time,
            )

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
                        time.sleep(0.25)

            try:
                has_det, detection_rows = self._process_snapshot(snapshot, components)
                if has_det:
                    snapshots_with_detections += 1
                total_detection_rows += detection_rows
                self._progress.processed_snapshots += 1
            except _FatalPersistenceError as e:
                # DB failure is fatal — stop analysis immediately
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
            except Exception as e:
                # Recoverable error — log, increment failed, continue
                self._progress.failed_snapshots += 1
                error_msg = f"Snapshot {snapshot.frame_index}: {type(e).__name__}: {e}"
                self._record_recoverable_error(error_msg)
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

    def _process_snapshot(self, snapshot: Snapshot, components: Any) -> tuple[bool, int]:
        """Process a single snapshot through the pipeline.

        Returns:
            (has_detections, detection_rows) tuple.

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

        # d. Process detections
        detections = frame_result.get("detections", [])
        has_detections = len(detections) > 0
        detection_rows = len(detections)

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

            # Compare to existing best
            existing = self._best_results_by_track.get(track_id)
            if existing is None or area > existing.best_area:
                self._best_results_by_track[track_id] = TrackBestResult(
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

        return has_detections, detection_rows

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
        )

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
