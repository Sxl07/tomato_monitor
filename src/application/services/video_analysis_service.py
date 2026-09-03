"""VideoAnalysisService — deferred offline analysis of a recorded video (Spec 019).

WAVE 1 SCOPE (this file): contract, configuration, lifecycle and I/O ONLY.

This wave establishes the offline read architecture:
    receive video path -> reader.open() -> verify available -> read metadata
    (via VideoReaderPort) -> sequentially read frames to EOF -> always release().

It deliberately does NOT yet perform sparse detection decisions, tracking,
Optical Flow, Scene Gate, snapshot/crop persistence, inference (RetinaNet /
ResNet / maturity) or final metrics. Those arrive in later waves. The
constructor already accepts the injected dependencies the design defines so the
signature stays stable, but they are not exercised in this wave.

Layer/boundary rules (enforced by tests):
    - Application layer: MUST NOT import cv2, torch, torchvision, detectron2 or
      picamera2 (directly or indirectly at module import time).
    - All video reading/metadata goes through the injected ``VideoReaderPort``;
      this service never touches ``cv2.VideoCapture`` and never reuses
      ``VideoFileFrameSource``.
    - EOF of an OPEN reader (``(False, None)``) is a normal loop end, NOT an
      error. Open/metadata/read backend failures surface as ``VideoReaderError``.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

from src.application.interfaces.video_reader_port import (
    VideoReaderError,
    VideoReaderPort,
)
from src.domain.entities.inspection_result import DetectionInspectionResult
from src.domain.entities.snapshot import Snapshot

# decide_run_detector is a pure, dependency-light module (no cv2/torch/detectron2),
# so it is safe to import at module level.
from src.infrastructure.vision.detector_decision import decide_run_detector

if TYPE_CHECKING:  # pragma: no cover - typing only; no heavy imports at runtime
    from src.application.interfaces.video_reader_port import VideoMetadata

logger = logging.getLogger(__name__)


class _FatalPersistenceError(Exception):
    """Internal: DB/persistence failures that must stop the whole analysis."""


@dataclass
class TrackBestResult:
    """Best (largest-bbox) observation for a track across the video."""

    track_id: int
    snapshot_id: int
    frame_index: int
    bbox: tuple  # (x1, y1, x2, y2)
    detection_score: float
    best_area: int
    health_label: str
    health_confidence: float
    maturity_stage: Optional[str]
    maturity_percent: Optional[float]


@dataclass
class VideoAnalysisConfig:
    """Configuration for deferred offline video analysis.

    Sampling gaps are expressed IN FRAMES (no frame<->time conversion). These
    fields are provided by the caller (profile -> config mapping happens
    elsewhere); the service does not read ACTIVE_PROFILE directly.

    Note: the sparse/tracking fields are defined here for a stable contract but
    are NOT used in Wave 1 (I/O only).
    """

    min_frames_between_detections: int
    max_frames_without_detection: int
    use_scene_gate: bool
    enable_flow_propagation: bool
    enable_sparse_detection: bool = True
    force_detect_on_first_frame: bool = True
    full_detection: bool = False
    save_annotated_video: bool = False

    def __post_init__(self) -> None:
        if self.min_frames_between_detections < 0:
            raise ValueError(
                "min_frames_between_detections must be >= 0, got "
                f"{self.min_frames_between_detections}"
            )
        if self.max_frames_without_detection < self.min_frames_between_detections:
            raise ValueError(
                "max_frames_without_detection must be >= "
                "min_frames_between_detections "
                f"({self.max_frames_without_detection} < "
                f"{self.min_frames_between_detections})"
            )


@dataclass
class VideoAnalysisProgress:
    """Lightweight progress for the offline analysis."""

    status: str = "pending"  # "pending" | "running" | "completed" | "error"
    total_frames_read: int = 0
    current_frame_index: int = -1
    unique_tracks: int = 0
    thermal_paused: bool = False


@dataclass
class VideoAnalysisResult:
    """Result of the offline video analysis.

    Carries I/O metadata, the official Task 8.4 execution counters, aggregate
    counts for MonitoringMetrics (persisted by MonitoringService in Task 10),
    and diagnostic fields. The invariant
    ``analysis_successful_frames + analysis_failed_frames == detector_scheduled_frames``
    holds on a completed run.
    """

    status: str = "pending"  # "completed" | "error"
    error_reason: Optional[str] = None

    # I/O metadata
    total_frames_read: int = 0
    source_fps: float = 0.0
    source_width: int = 0
    source_height: int = 0
    source_frame_count: int = 0

    # Official execution counters (Task 8.4)
    detector_scheduled_frames: int = 0
    analysis_successful_frames: int = 0
    analysis_failed_frames: int = 0

    # Aggregates for MonitoringMetrics (computed here; persisted by Task 10).
    # Canonical names match the historical AnalysisResult contract used by
    # MonitoringService._build_metrics_from_analysis_result:
    #   unique_tomatoes == len(best_by_track)
    #   total_detection_rows == total detections observed in successful frames
    # unique_tracks / total_detections are exact aliases (same values).
    unique_tomatoes: int = 0
    total_detection_rows: int = 0
    unique_tracks: int = 0
    total_detections: int = 0
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
    best_results_by_track: dict = field(default_factory=dict)

    # Diagnostics
    reason_counts: dict = field(default_factory=dict)
    analysis_duration_seconds: float = 0.0
    errors: list = field(default_factory=list)
    thermal_peak_temperature_c: float = 0.0
    thermal_pause_count: int = 0
    thermal_pause_duration_seconds: float = 0.0
    report_paths: dict = field(default_factory=dict)


class VideoAnalysisService:
    """Reads a recorded monitoring video offline via an injected VideoReaderPort.

    Wave 1 implements only the read lifecycle. Inference-related dependencies are
    accepted for a stable constructor but are not used yet.
    """

    def __init__(
        self,
        monitoring_id: int,
        video_path: str,
        snapshot_repo: Any,
        inspection_result_repo: Any,
        monitoring_repo: Any,
        db_session: Any,
        *,
        config: VideoAnalysisConfig,
        video_reader: VideoReaderPort,
        components_factory: Optional[Callable] = None,
        process_frame_fn: Optional[Callable] = None,
        annotation_renderer: Optional[Callable] = None,
        thermal_monitor: Any = None,
        report_writer: Optional[object] = None,
        profile_name: Optional[str] = None,
    ) -> None:
        self._monitoring_id = monitoring_id
        self._video_path = video_path
        self._snapshot_repo = snapshot_repo
        self._inspection_result_repo = inspection_result_repo
        self._monitoring_repo = monitoring_repo
        self._db_session = db_session
        self._config = config
        self._video_reader = video_reader
        # Accepted for a stable signature; NOT used in Wave 1.
        self._components_factory = components_factory
        self._process_frame_fn = process_frame_fn
        self._annotation_renderer = annotation_renderer
        self._thermal_monitor = thermal_monitor
        self._report_writer = report_writer
        self._profile_name = profile_name

        self._progress = VideoAnalysisProgress()
        self._error_reason: Optional[str] = None
        # Internal sparse-decision reason counts (Wave 2). Not exposed publicly
        # yet; Task 8.4 will surface these via pipeline_metrics.json.
        self._detector_reason_counts: Counter = Counter()

        # Task 8.3 per-run state (reset in run()).
        self._detector_scheduled_frames: int = 0
        self._analysis_successful_frames: int = 0
        self._analysis_failed_frames: int = 0
        self._best_by_track: dict[int, TrackBestResult] = {}
        self._errors: list[str] = []
        self._start_time: float = 0.0
        self._snapshots_with_detections: int = 0
        # Total detection rows observed across SUCCESSFUL scheduled frames
        # (distinct from unique tracks). Reset per run().
        self._total_detection_rows: int = 0

    # --- lazy resolvers (keep heavy vision deps out of module import) ------ #

    def _resolve_components_factory(self) -> Callable:
        """Return the injected components factory or lazily import the default."""
        if self._components_factory is not None:
            return self._components_factory
        from src.infrastructure.vision.pipeline_orchestrator import (
            build_pipeline_components,
        )
        return build_pipeline_components

    def _resolve_process_frame_fn(self) -> Callable:
        """Return the injected process_frame or lazily import the default."""
        if self._process_frame_fn is not None:
            return self._process_frame_fn
        from src.infrastructure.vision.pipeline_orchestrator import process_frame
        return process_frame

    def _resolve_scene_gate_fn(self) -> Callable:
        """Lazily import the Scene Gate callable (only when use_scene_gate)."""
        from src.infrastructure.vision.capture_gate import (
            should_run_detector_by_scene_change,
        )
        return should_run_detector_by_scene_change

    def _create_flow_tracker(self):
        """Lazily create a single OpticalFlowVisualTracker (only when flow on)."""
        from src.infrastructure.vision.visual_tracker import OpticalFlowVisualTracker
        return OpticalFlowVisualTracker()

    # --- persistence & fault-tolerance helpers (Task 8.3) ------------------ #

    def _save_image(self, relative_path: str, image) -> bool:
        """Write an image via infrastructure save_image, resolving under BASE_DIR.

        Keeps ``cv2`` out of this application module: the actual write happens in
        ``file_utils.save_image`` (infrastructure). The DB stores the RELATIVE
        path; the physical file is written to ``BASE_DIR / relative_path`` so the
        result does not depend on the current working directory. Returns the
        bool from ``save_image`` (True on success).
        """
        from src.infrastructure.config.settings import BASE_DIR
        from src.infrastructure.persistence.local.file_utils import save_image

        return bool(save_image(image, BASE_DIR / relative_path))

    def _persist_raw_snapshot(self, frame, frame_idx: int) -> Snapshot:
        """Save raw JPEG + create Snapshot(has_detections=False) + commit.

        This is a FATAL step: any failure raises _FatalPersistenceError so the
        run ends with status="error". The relative path is stored in the DB.
        """
        rel_dir = f"outputs/monitorings/{self._monitoring_id}/snapshots/raw"
        rel_path = f"{rel_dir}/snapshot_{frame_idx:06d}.jpg"
        try:
            ok = self._save_image(rel_path, frame)
            if not ok:
                raise _FatalPersistenceError(
                    f"No se pudo escribir el snapshot en {rel_path}"
                )
        except _FatalPersistenceError:
            raise
        except Exception as exc:
            raise _FatalPersistenceError(
                f"Fallo al guardar snapshot crudo (frame {frame_idx}): {exc}"
            ) from exc

        try:
            snapshot = Snapshot(
                monitoring_id=self._monitoring_id,
                image_path=rel_path,
                frame_index=frame_idx,
                has_detections=False,
            )
            created = self._snapshot_repo.create(self._monitoring_id, snapshot)
            self._db_session.commit()
            return created
        except Exception as exc:
            raise _FatalPersistenceError(
                f"Fallo al persistir snapshot (frame {frame_idx}): {exc}"
            ) from exc

    def _update_has_detections(self, snapshot_id: int, has_detections: bool) -> None:
        """Update Snapshot.has_detections and commit (FATAL on DB failure)."""
        try:
            self._snapshot_repo.update_has_detections(snapshot_id, has_detections)
            self._db_session.commit()
        except Exception as exc:
            raise _FatalPersistenceError(
                f"Fallo al actualizar has_detections (snapshot {snapshot_id}): {exc}"
            ) from exc

    def _generate_crops(self, frame, detections, frame_idx: int) -> None:
        """Generate crops for real, non-reused detections (reuses cropper.py).

        Filesystem/cv2 usage is lazy (writes go through infrastructure
        save_image). Raises on failure — including a False return from
        save_image — so the caller counts the frame as a recoverable failure
        (the raw snapshot is already persisted).
        """
        from src.infrastructure.vision.cropper import (
            clamp_box_xyxy,
            crop_from_box,
            expand_box,
        )

        h, w = frame.shape[:2]
        rel_crop_dir = (
            f"outputs/monitorings/{self._monitoring_id}/crops"
            f"/snapshot_{frame_idx:06d}"
        )
        for det in detections:
            if det.get("reused_previous_result", False):
                continue
            track_id = det.get("track_id")
            if track_id is None:
                continue

            x1, y1, x2, y2 = det["bbox"]
            x1, y1, x2, y2 = clamp_box_xyxy(x1, y1, x2, y2, w, h)
            x1, y1, x2, y2 = expand_box(x1, y1, x2, y2, w, h)
            crop = crop_from_box(frame, (x1, y1, x2, y2))
            if crop is not None and crop.size > 0:
                crop_path = f"{rel_crop_dir}/track_{track_id:03d}.jpg"
                ok = self._save_image(crop_path, crop)
                if not ok:
                    # Recoverable (NOT _FatalPersistenceError): the per-frame
                    # handler counts this frame as failed and continues.
                    raise RuntimeError(
                        f"No se pudo escribir el crop en {crop_path}"
                    )

    def _stage_best(self, detections, snapshot: Snapshot) -> dict:
        """Stage best-by-track candidates for a frame (largest bbox area).

        Ignores reused_previous_result and track_id=None. Returns a local dict
        that the caller merges only after a successful commit.
        """
        staged: dict[int, TrackBestResult] = {}
        for det in detections:
            track_id = det.get("track_id")
            if track_id is None:
                continue
            if det.get("reused_previous_result", False):
                continue

            x1, y1, x2, y2 = det["bbox"]
            area = max(0, x2 - x1) * max(0, y2 - y1)

            health = det.get("health_result") or {}
            maturity = det.get("maturity_result") or {}

            existing = self._best_by_track.get(track_id)
            staged_existing = staged.get(track_id)
            baseline = existing
            if staged_existing is not None and (
                baseline is None or staged_existing.best_area > baseline.best_area
            ):
                baseline = staged_existing

            if baseline is None or area > baseline.best_area:
                staged[track_id] = TrackBestResult(
                    track_id=track_id,
                    snapshot_id=snapshot.id,
                    frame_index=snapshot.frame_index,
                    bbox=(int(x1), int(y1), int(x2), int(y2)),
                    detection_score=det.get("det_score", 0.0),
                    best_area=area,
                    health_label=health.get("label", "unknown"),
                    health_confidence=health.get("confidence", 0.0),
                    maturity_stage=maturity.get("usda_stage"),
                    maturity_percent=maturity.get("maturity_percent"),
                )
        return staged

    def _merge_best(self, staged: dict) -> None:
        """Merge staged best candidates into best_by_track (largest area wins)."""
        for track_id, candidate in staged.items():
            existing = self._best_by_track.get(track_id)
            if existing is None or candidate.best_area > existing.best_area:
                self._best_by_track[track_id] = candidate
        self._progress.unique_tracks = len(self._best_by_track)

    def _persist_best_results(self) -> None:
        """Persist ≤1 DetectionInspectionResult per track + final commit (FATAL)."""
        try:
            for _track_id, best in self._best_by_track.items():
                result_entity = DetectionInspectionResult(
                    snapshot_id=best.snapshot_id,
                    detection_index=best.track_id,
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
            self._db_session.commit()
        except Exception as exc:
            raise _FatalPersistenceError(
                f"Fallo al persistir resultados finales: {exc}"
            ) from exc

    def _safe_rollback(self) -> None:
        """Best-effort DB rollback; never raises."""
        try:
            self._db_session.rollback()
        except Exception:
            pass

    # --- thermal (cooperative pause, same pattern as SnapshotAnalysisService) #

    def _thermal_start(self) -> None:
        """Start the thermal monitor if present (fatal for the run if it raises)."""
        if self._thermal_monitor is None:
            return
        start_fn = getattr(self._thermal_monitor, "start", None)
        if callable(start_fn):
            start_fn()

    def _thermal_stop(self) -> None:
        """Stop the thermal monitor if present; never raises."""
        if self._thermal_monitor is None:
            return
        stop_fn = getattr(self._thermal_monitor, "stop", None)
        if callable(stop_fn):
            try:
                stop_fn()
            except Exception:
                pass

    def _thermal_cooperative_pause(self) -> None:
        """Wait cooperatively while the thermal monitor's pause_event is set."""
        tm = self._thermal_monitor
        if tm is None:
            return
        pause_event = getattr(tm, "pause_event", None)
        if pause_event is None:
            return
        try:
            while pause_event.is_set():
                self._progress.thermal_paused = True
                time.sleep(0.25)
            self._progress.thermal_paused = False
        except Exception:
            # Thermal read failure must never crash analysis.
            pass

    def _thermal_metric(self, attr: str, default):
        """Safely read a numeric metric from the thermal monitor."""
        tm = self._thermal_monitor
        if tm is None:
            return default
        try:
            val = getattr(tm, attr, default)
            if val is None or isinstance(val, bool):
                return default
            if isinstance(val, (int, float)):
                return val
            return default
        except Exception:
            return default

    # --- result aggregates + reporting (Task 8.4) -------------------------- #

    def _populate_result(self, result: VideoAnalysisResult) -> None:
        """Fill counters, aggregates, diagnostics and thermal onto the result.

        Aggregates (unique_tracks, healthy/unhealthy, maturity, ...) are computed
        from best_by_track for MonitoringService (Task 10) to persist as
        MonitoringMetrics. Persistence is NOT done here (no metrics repo).
        """
        result.detector_scheduled_frames = self._detector_scheduled_frames
        result.analysis_successful_frames = self._analysis_successful_frames
        result.analysis_failed_frames = self._analysis_failed_frames

        result.reason_counts = dict(self._detector_reason_counts)
        result.errors = list(self._errors)
        result.analysis_duration_seconds = (
            time.monotonic() - self._start_time if self._start_time else 0.0
        )
        result.snapshots_with_detections = self._snapshots_with_detections

        # Aggregate best-by-track for MonitoringMetrics.
        healthy = unhealthy = unknown = 0
        maturity_counts = {
            "green": 0, "breaker": 0, "turning": 0,
            "pink": 0, "light_red": 0, "red": 0,
        }
        for best in self._best_by_track.values():
            if best.health_label == "healthy":
                healthy += 1
            elif best.health_label == "unhealthy":
                unhealthy += 1
            else:
                unknown += 1
            if best.maturity_stage in maturity_counts:
                maturity_counts[best.maturity_stage] += 1

        unique = len(self._best_by_track)
        # Canonical names (historical AnalysisResult contract).
        result.unique_tomatoes = unique
        result.total_detection_rows = self._total_detection_rows
        # Exact aliases (same values; no ambiguous semantics).
        result.unique_tracks = unique
        result.total_detections = self._total_detection_rows
        result.healthy_count = healthy
        result.unhealthy_count = unhealthy
        result.unknown_health_count = unknown
        result.maturity_counts = maturity_counts
        result.best_results_by_track = dict(self._best_by_track)

        # Thermal diagnostics (when available).
        result.thermal_peak_temperature_c = self._thermal_metric("peak_temperature", 0.0)
        result.thermal_pause_count = self._thermal_metric("pause_count", 0)
        result.thermal_pause_duration_seconds = self._thermal_metric(
            "total_pause_duration_seconds", 0.0
        )

    def _resolve_report_writer(self):
        """Return the injected report writer or lazily build the default one."""
        if self._report_writer is not None:
            return self._report_writer
        from src.infrastructure.persistence.local.snapshot_analysis_report_writer import (
            SnapshotAnalysisReportWriter,
        )
        from src.infrastructure.config.settings import OUTPUTS_DIR

        return SnapshotAnalysisReportWriter(base_outputs_dir=OUTPUTS_DIR / "monitorings")

    def _resolve_profile_name(self) -> Optional[str]:
        """Return the profile name, lazily reading ACTIVE_PROFILE if needed."""
        if self._profile_name is not None:
            return self._profile_name
        try:
            from src.infrastructure.config.settings import ACTIVE_PROFILE
            return ACTIVE_PROFILE.name
        except Exception:
            return None

    def _build_analysis_metrics(self, result: VideoAnalysisResult) -> dict:
        """Build the 'analysis' section for pipeline_metrics.json.

        Gaps are expressed IN FRAMES; the temporal equivalence is approximate
        (gap / fps) and NOT auto-converted to seconds.
        """
        cfg = self._config
        fps = result.source_fps or 0.0

        def _temporal_note(gap_frames: int):
            approx = (gap_frames / fps) if fps > 0 else None
            return {"frames": gap_frames, "approx_seconds": approx}

        return {
            "status": result.status,
            "error_reason": result.error_reason,
            "execution_counts": {
                "detector_scheduled_frames": result.detector_scheduled_frames,
                "analysis_successful_frames": result.analysis_successful_frames,
                "analysis_failed_frames": result.analysis_failed_frames,
                "total_frames_read": result.total_frames_read,
            },
            "reason_counts": result.reason_counts,
            "source_video_fps": result.source_fps,
            "sparse_config": {
                "min_frames_between_detections": cfg.min_frames_between_detections,
                "max_frames_without_detection": cfg.max_frames_without_detection,
                "use_scene_gate": cfg.use_scene_gate,
                "enable_flow_propagation": cfg.enable_flow_propagation,
                "enable_sparse_detection": cfg.enable_sparse_detection,
                "full_detection": cfg.full_detection,
                "save_annotated_video": cfg.save_annotated_video,
            },
            "sampling_note": (
                "Gaps are expressed in FRAMES; temporal equivalence is approximate "
                "(gap / fps) and is not auto-converted to seconds."
            ),
            "gap_temporal_equivalence": {
                "min_frames_between_detections": _temporal_note(
                    cfg.min_frames_between_detections
                ),
                "max_frames_without_detection": _temporal_note(
                    cfg.max_frames_without_detection
                ),
            },
            "unique_tracks": result.unique_tracks,
            "detections": result.total_detections,
            "snapshots_with_detections": result.snapshots_with_detections,
            "aggregates": {
                "healthy_count": result.healthy_count,
                "unhealthy_count": result.unhealthy_count,
                "unknown_health_count": result.unknown_health_count,
                "maturity_counts": dict(result.maturity_counts),
            },
            "timings": {
                "analysis_duration_seconds": result.analysis_duration_seconds,
            },
            "thermal": {
                "peak_temperature_c": result.thermal_peak_temperature_c,
                "pause_count": result.thermal_pause_count,
                "total_pause_duration_seconds": result.thermal_pause_duration_seconds,
            },
            "errors_count": len(result.errors),
            "errors": list(result.errors),
        }

    def _generate_reports(self, result: VideoAnalysisResult) -> None:
        """Write pipeline_metrics.json for all outcomes; never raises.

        Reuses SnapshotAnalysisReportWriter (merges into any existing capture/
        recording section). CSV rows are not produced by the video flow yet
        (Task 10 owns recording metrics wiring); we pass empty CSV row lists.
        """
        try:
            writer = self._resolve_report_writer()
            profile = self._resolve_profile_name()
            analysis_metrics = self._build_analysis_metrics(result)

            summary_row = {
                "monitoring_id": self._monitoring_id,
                "status": result.status,
                "unique_tomatoes": result.unique_tracks,
                "healthy_count": result.healthy_count,
                "unhealthy_count": result.unhealthy_count,
                "unknown_health_count": result.unknown_health_count,
                "snapshots_with_detections": result.snapshots_with_detections,
                "analysis_duration_seconds": result.analysis_duration_seconds,
                "errors_count": len(result.errors),
                "error_reason": result.error_reason,
                "thermal_peak_temperature_c": result.thermal_peak_temperature_c,
                "thermal_pause_count": result.thermal_pause_count,
                "thermal_pause_duration_seconds": result.thermal_pause_duration_seconds,
            }

            write_result = writer.write_reports(
                monitoring_id=self._monitoring_id,
                per_snapshot_rows=[],
                per_detection_rows=[],
                summary_row=summary_row,
                analysis_metrics=analysis_metrics,
                profile_name=profile,
            )
            result.report_paths = write_result.paths
            for err in write_result.errors:
                msg = f"Report: {err}"
                if msg not in result.errors:
                    result.errors.append(msg)
        except Exception as exc:
            # Report generation must never crash the analysis outcome.
            logger.warning(
                "VideoAnalysisService %s: report generation failed: %s",
                self._monitoring_id,
                exc,
            )

    @property
    def detector_reason_counts(self) -> Counter:
        """Internal reason counts (Wave 2 diagnostics; not an official metric)."""
        return self._detector_reason_counts

    @property
    def detector_scheduled_frames(self) -> int:
        """Frames where decide_run_detector returned run_detector=True."""
        return self._detector_scheduled_frames

    @property
    def analysis_successful_frames(self) -> int:
        """Scheduled frames whose process_frame() completed successfully."""
        return self._analysis_successful_frames

    @property
    def analysis_failed_frames(self) -> int:
        """Scheduled frames whose recoverable analysis step raised."""
        return self._analysis_failed_frames

    @property
    def best_by_track(self) -> dict:
        """Best-by-track observations accumulated during the run."""
        return self._best_by_track

    @property
    def errors(self) -> list:
        """Recoverable error messages recorded during the run."""
        return list(self._errors)

    @property
    def progress(self) -> VideoAnalysisProgress:
        """Current progress snapshot."""
        return self._progress

    @property
    def error_reason(self) -> Optional[str]:
        """Error reason if analysis failed, else None."""
        return self._error_reason

    def run(self) -> VideoAnalysisResult:
        """Open the video, read metadata, iterate frames to EOF, always release.

        Returns a VideoAnalysisResult. On any reader failure (open/metadata/read
        backend error, or reader unavailable) the result status is "error" with
        an error_reason; normal EOF ends the loop with status "completed". The
        reader is released in all paths.
        """
        self._progress = VideoAnalysisProgress(status="running")
        self._error_reason = None
        # One reason Counter PER run(): reset before opening/reading so repeated
        # runs on the same instance never accumulate across executions.
        self._detector_reason_counts = Counter()
        # Reset Task 8.3 per-run state so repeated runs never accumulate.
        self._detector_scheduled_frames = 0
        self._analysis_successful_frames = 0
        self._analysis_failed_frames = 0
        self._best_by_track = {}
        self._errors = []
        self._snapshots_with_detections = 0
        self._total_detection_rows = 0
        self._start_time = time.monotonic()
        result = VideoAnalysisResult(status="error")

        try:
            # Start the thermal monitor (cooperative pause pattern, same as
            # SnapshotAnalysisService). A start() failure is fatal for the run.
            self._thermal_start()

            # Open the reader. A VideoReaderError here is a hard failure. Even so,
            # release() is attempted in `finally` (it is idempotent and safe when
            # no resource is active) to protect adapters that may have partially
            # acquired resources before failing.
            self._video_reader.open()

            if not self._video_reader.is_available():
                self._error_reason = "El video no está disponible o es ilegible."
                self._progress.status = "error"
                result.error_reason = self._error_reason
                return result

            meta = self._video_reader.metadata()
            result.source_fps = meta.fps
            result.source_width = meta.width
            result.source_height = meta.height
            result.source_frame_count = meta.total_frames

            # Build the pipeline components EXACTLY ONCE for the whole video. The
            # same object (and thus the same components.tracker / SimpleTracker)
            # is reused across all process_frame calls to provide cross-frame
            # tracking of RetinaNet detections.
            components_factory = self._resolve_components_factory()
            components = components_factory()
            process_frame_fn = self._resolve_process_frame_fn()

            # full_detection (or disabling sparse) forces detection on every frame
            # via decide_run_detector — no separate manual decision branch.
            effective_sparse = (
                self._config.enable_sparse_detection
                and not self._config.full_detection
            )

            # Resolve the Scene Gate only when enabled; when disabled, use a guard
            # that must never be called (decide_run_detector won't call it) so we
            # don't load OpenCV just because the code path exists.
            if self._config.use_scene_gate:
                scene_gate_fn = self._resolve_scene_gate_fn()
            else:
                def scene_gate_fn(*_args, **_kwargs):  # pragma: no cover - guard
                    raise AssertionError(
                        "scene_gate_fn must not be called when use_scene_gate is False"
                    )

            # Create a single Optical Flow tracker only when flow is enabled.
            flow_tracker = None
            if self._config.enable_flow_propagation:
                flow_tracker = self._create_flow_tracker()

            frames_read = 0
            frame_idx = 0
            frames_since_last_detection = 0
            last_detection_frame = None

            while True:
                # Cooperative thermal pause (same pattern as SnapshotAnalysisService):
                # wait while the thermal monitor's pause_event is set.
                self._thermal_cooperative_pause()

                success, frame = self._video_reader.read()
                if not success or frame is None:
                    # Normal EOF of an open reader: end the loop.
                    break

                frames_read += 1
                # Keep running counts on result AND progress so a later failure
                # preserves how many frames were read before it.
                result.total_frames_read = frames_read
                self._progress.total_frames_read = frames_read
                self._progress.current_frame_index = frame_idx

                decision = decide_run_detector(
                    frame_idx=frame_idx,
                    frames_since_last_detection=frames_since_last_detection,
                    last_detection_frame=last_detection_frame,
                    current_frame=frame,
                    enable_sparse_detection=effective_sparse,
                    use_scene_gate=self._config.use_scene_gate,
                    min_frames_between_detections=self._config.min_frames_between_detections,
                    max_frames_without_detection=self._config.max_frames_without_detection,
                    force_detect_on_first_frame=self._config.force_detect_on_first_frame,
                    scene_gate_fn=scene_gate_fn,
                )
                self._detector_reason_counts[decision.reason] += 1

                if decision.run_detector:
                    # Mark the scheduled detection state BEFORE inference so the
                    # gap/reference semantics match the legacy runner.
                    last_detection_frame = frame.copy()
                    frames_since_last_detection = 0
                    self._detector_scheduled_frames += 1

                    frame_name = (
                        f"monitoring_{self._monitoring_id}_frame_{frame_idx:06d}"
                    )

                    # FATAL step: persist the raw snapshot (JPEG + DB row + commit)
                    # BEFORE inference, so evidence survives even if analysis fails.
                    snapshot = self._persist_raw_snapshot(frame, frame_idx)

                    # RECOVERABLE step: inference + flow update + crops + best
                    # staging + has_detections. A failure here counts the frame as
                    # failed but keeps the raw snapshot and continues.
                    try:
                        frame_result = process_frame_fn(frame, components, frame_name)

                        if flow_tracker is not None:
                            flow_tracker.update_from_detection_result(
                                frame,
                                frame_result.get("detections", []),
                            )

                        detections = frame_result.get("detections", [])
                        self._generate_crops(frame, detections, frame_idx)
                        staged_best = self._stage_best(detections, snapshot)

                        # FATAL: update has_detections + commit.
                        self._update_has_detections(
                            snapshot.id, bool(detections)
                        )
                        # Only merge best AFTER the successful commit above.
                        self._merge_best(staged_best)
                        self._analysis_successful_frames += 1
                        # Count detection ROWS only for fully-successful frames
                        # (distinct from unique tracks).
                        self._total_detection_rows += len(detections)
                        if detections:
                            self._snapshots_with_detections += 1
                    except _FatalPersistenceError:
                        raise
                    except Exception as exc:
                        self._analysis_failed_frames += 1
                        self._errors.append(
                            f"frame {frame_idx}: {type(exc).__name__}: {exc}"
                        )
                        logger.warning(
                            "VideoAnalysisService %s: recoverable analysis failure "
                            "on frame %d: %s",
                            self._monitoring_id,
                            frame_idx,
                            exc,
                        )
                else:
                    frames_since_last_detection += 1
                    if flow_tracker is not None:
                        flow_tracker.propagate(frame)

                frame_idx += 1

            # Final persistence: at most one DetectionInspectionResult per track.
            self._persist_best_results()

            result.status = "completed"
            self._progress.status = "completed"
            return result

        except _FatalPersistenceError as exc:
            self._error_reason = f"Error de persistencia: {exc}"
            self._progress.status = "error"
            result.status = "error"
            result.error_reason = self._error_reason
            self._safe_rollback()
            logger.error(
                "VideoAnalysisService %s: fatal persistence error: %s",
                self._monitoring_id,
                exc,
            )
            return result

        except VideoReaderError as exc:
            self._error_reason = f"Error de lectura del video: {exc}"
            self._progress.status = "error"
            result.status = "error"
            result.error_reason = self._error_reason
            logger.warning(
                "VideoAnalysisService %s: reader error: %s",
                self._monitoring_id,
                exc,
            )
            return result

        except Exception as exc:  # unexpected failure — still release below
            self._error_reason = f"Error inesperado en el análisis: {exc}"
            self._progress.status = "error"
            result.status = "error"
            result.error_reason = self._error_reason
            logger.error(
                "VideoAnalysisService %s: unexpected error: %s",
                self._monitoring_id,
                exc,
            )
            return result

        finally:
            # Canonical final order: thermal.stop() -> populate result (POST-stop
            # thermal metrics) -> release reader -> generate reports. Populating
            # here also covers early returns (e.g. is_available()==False) that did
            # not populate the result before reaching finally.
            self._thermal_stop()
            self._populate_result(result)

            # Always attempt release(), even if open() failed. Per the Task 2
            # contract release() is idempotent, safe when no resource is active,
            # and does not reopen. A release() failure is logged and must never
            # mask the primary outcome.
            try:
                self._video_reader.release()
            except Exception as rel_exc:
                logger.warning(
                    "VideoAnalysisService %s: reader.release() failed: %s",
                    self._monitoring_id,
                    rel_exc,
                )

            # Generate pipeline_metrics.json for ALL outcomes (after thermal stop
            # and result population), preserving any partial data. Never raises.
            self._generate_reports(result)
