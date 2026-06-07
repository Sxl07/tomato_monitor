"""MonitoringWorker: background capture loop + inference.

Runs in a daemon thread spawned by MonitoringService. Reads frames from
the FrameSource, evaluates the Scene Gate (functional API from capture_gate.py),
and when triggered, saves the snapshot image, runs inference, and persists
results via repository interfaces.

Threading signals (threading.Event) are used for pause/abort/complete
communication with the service layer.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

import cv2
import numpy as np
from sqlalchemy.orm import Session

from src.domain.entities.inspection_result import DetectionInspectionResult
from src.domain.entities.snapshot import Snapshot
from src.domain.interfaces.frame_source import FrameSource
from src.domain.repositories.inspection_result_repository import (
    InspectionResultRepository,
)
from src.domain.repositories.monitoring_repository import MonitoringRepository
from src.domain.repositories.snapshot_repository import SnapshotRepository
from src.infrastructure.monitoring.thermal_monitor import ThermalMonitor
from src.application.services.log_service import LogLevel, LogService
from src.infrastructure.vision.capture_gate import should_capture_new_image
from src.infrastructure.vision.snapshot_inference_runner import SnapshotInferenceRunner

logger = logging.getLogger(__name__)


class MonitoringWorker:
    """Runs the capture loop + inference in a background thread.

    Uses threading.Event signals for pause/abort/complete communication.
    The Scene Gate is evaluated via the functional API in capture_gate.py,
    which requires a reference frame and a frames-since-last-capture counter.
    """

    def __init__(
        self,
        monitoring_id: int,
        frame_source: FrameSource,
        inference_runner: SnapshotInferenceRunner,
        snapshot_repo: SnapshotRepository,
        inspection_result_repo: InspectionResultRepository,
        monitoring_repo: MonitoringRepository,
        db_session: Session,
        *,
        scene_gate_cooldown_frames: int = 18,
        scene_gate_timeout_frames: int = 45,
        scene_gate_orb_threshold: int = 35,
        scene_gate_hsv_threshold: float = 0.38,
        thermal_monitor: Optional[ThermalMonitor] = None,
        memory_warning_rss_mb: int = 3000,
        log_service: Optional[LogService] = None,
    ) -> None:
        self._monitoring_id = monitoring_id
        self._frame_source = frame_source
        self._inference_runner = inference_runner
        self._snapshot_repo = snapshot_repo
        self._inspection_result_repo = inspection_result_repo
        self._monitoring_repo = monitoring_repo
        self._session = db_session

        # Scene Gate configuration
        self._scene_gate_cooldown_frames = scene_gate_cooldown_frames
        self._scene_gate_timeout_frames = scene_gate_timeout_frames
        self._scene_gate_orb_threshold = scene_gate_orb_threshold
        self._scene_gate_hsv_threshold = scene_gate_hsv_threshold

        # Thermal management
        self._thermal_monitor = thermal_monitor

        # Memory budget
        self._memory_warning_rss_mb = memory_warning_rss_mb

        # Log service (optional, for activity panel)
        self._log_service = log_service

        # Capture loop state
        self._frame_index: int = 0
        self._snapshot_count: int = 0
        self._total_detections: int = 0
        self._frames_since_last_capture: int = 0
        self._reference_frame: Optional[np.ndarray] = None
        self._error_reason: Optional[str] = None

        # Threading signals
        self.pause_event = threading.Event()  # Set = paused
        self.abort_event = threading.Event()  # Set = abort requested
        self.complete_event = threading.Event()  # Set = traversal finished

    @property
    def error_reason(self) -> Optional[str]:
        """Return the error reason if the worker stopped due to an error."""
        return self._error_reason

    @property
    def snapshot_count(self) -> int:
        """Return the number of snapshots captured so far."""
        return self._snapshot_count

    @property
    def total_detections(self) -> int:
        """Return the total number of detections found so far."""
        return self._total_detections

    def _emit_log(self, level: LogLevel, message: str) -> None:
        """Emit a log entry if log_service is available."""
        if self._log_service is not None:
            self._log_service.add_entry(
                monitoring_id=self._monitoring_id,
                level=level,
                source="worker",
                message=message,
            )

    def run(self) -> None:
        """Main capture loop. Call from a daemon thread."""
        try:
            self._emit_log(LogLevel.INFO, "Inicializando cámara...")

            if self._thermal_monitor is not None:
                self._thermal_monitor.start()

            first_frame_read = False

            while not self.abort_event.is_set() and not self.complete_event.is_set():
                # Check pause — spin-wait with short sleep
                if self.pause_event.is_set():
                    time.sleep(0.1)
                    continue

                # Read frame from source
                success, frame = self._frame_source.read()
                if not success or frame is None:
                    self._emit_log(LogLevel.ERROR, "No se pudo acceder a la cámara.")
                    self._error_reason = (
                        "Frame source became unavailable (camera disconnection)"
                    )
                    break

                # Log first successful frame read
                if not first_frame_read:
                    first_frame_read = True
                    self._emit_log(LogLevel.SUCCESS, "Cámara detectada correctamente.")
                    self._emit_log(LogLevel.INFO, "Monitoreo iniciado.")

                self._frame_index += 1
                self._frames_since_last_capture += 1

                # Evaluate scene gate
                if self._should_capture(frame):
                    self._process_snapshot(frame)
                    # Update reference frame after capture
                    self._reference_frame = frame.copy()
                    self._frames_since_last_capture = 0

        except Exception as e:
            self._error_reason = f"Unexpected error in capture loop: {e}"
            logger.error(f"MonitoringWorker crash: {e}", exc_info=True)
        finally:
            self._release_resources()

    def _should_capture(self, frame: np.ndarray) -> bool:
        """Evaluate the Scene Gate to decide if the current frame should be captured.

        On the first frame (no reference), always capture to establish a baseline.
        Subsequent frames are compared against the last captured reference frame.
        """
        if self._reference_frame is None:
            # First frame: always capture to establish reference
            return True

        trigger, _metrics = should_capture_new_image(
            reference_bgr=self._reference_frame,
            current_bgr=frame,
            frames_since_last_capture=self._frames_since_last_capture,
            cooldown_frames=self._scene_gate_cooldown_frames,
            timeout_frames=self._scene_gate_timeout_frames,
            orb_threshold=self._scene_gate_orb_threshold,
            hsv_threshold=self._scene_gate_hsv_threshold,
        )
        return trigger

    def _process_snapshot(self, frame: np.ndarray) -> None:
        """Save snapshot image, run inference, persist results."""
        self._emit_log(
            LogLevel.INFO,
            f"Capturando imagen... (snapshot {self._snapshot_count + 1})",
        )

        # Save image to filesystem
        snapshot_dir = f"outputs/monitorings/{self._monitoring_id}/snapshots"
        os.makedirs(snapshot_dir, exist_ok=True)
        image_path = f"{snapshot_dir}/snapshot_{self._snapshot_count}.jpg"

        try:
            cv2.imwrite(image_path, frame)
        except Exception as e:
            self._emit_log(LogLevel.ERROR, f"Error de almacenamiento: {e}")
            logger.error(f"Failed to save snapshot image: {e}")
            self._error_reason = f"Filesystem error: {e}"
            self.abort_event.set()
            return

        # Run inference
        detection_results: list[dict] = []
        try:
            detection_results = self._inference_runner.run_inference(frame)
        except Exception as e:
            logger.warning(
                f"Inference failed for snapshot {self._snapshot_count}: {e}"
            )
            # Skip inference results but still persist the snapshot (Req 3.6)

        self._emit_log(
            LogLevel.SUCCESS, f"Tomates detectados: {len(detection_results)}"
        )

        has_detections = len(detection_results) > 0

        # Create and persist Snapshot entity
        snapshot = Snapshot(
            monitoring_id=self._monitoring_id,
            image_path=image_path,
            frame_index=self._snapshot_count,
            change_score=None,
            has_detections=has_detections,
        )
        persisted_snapshot = self._snapshot_repo.create(
            self._monitoring_id, snapshot
        )

        # Persist each detection as InspectionResult
        for i, det in enumerate(detection_results):
            result = DetectionInspectionResult(
                snapshot_id=persisted_snapshot.id,
                detection_index=i,
                bbox_x1=det["bbox_x1"],
                bbox_y1=det["bbox_y1"],
                bbox_x2=det["bbox_x2"],
                bbox_y2=det["bbox_y2"],
                detection_score=det["detection_score"],
                health_label=det["health_label"],
                health_confidence=det["health_confidence"],
                maturity_stage=det.get("maturity_stage"),
                maturity_percent=det.get("maturity_percent"),
            )
            self._inspection_result_repo.create(persisted_snapshot.id, result)

        # Update counters
        self._snapshot_count += 1
        self._total_detections += len(detection_results)
        self._monitoring_repo.update_counters(
            self._monitoring_id,
            total_snapshots=self._snapshot_count,
            total_detections=self._total_detections,
        )

        # Commit after each snapshot cycle (session-scoped commit)
        self._session.commit()

        # Check memory usage after inference
        self._check_memory_usage()

        # Release frame reference to aid garbage collection
        frame = None

    def _check_memory_usage(self) -> None:
        """Log warning if RSS memory exceeds budget."""
        try:
            import resource
            # ru_maxrss is in KB on Linux
            rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            rss_mb = rss_kb / 1024
            if rss_mb > self._memory_warning_rss_mb:
                logger.warning(f"Memory usage {rss_mb:.0f} MB exceeds budget {self._memory_warning_rss_mb} MB")
        except Exception:
            pass  # resource module may not be available on all platforms

    def _release_resources(self) -> None:
        """Release frame source and stop thermal monitor when done."""
        try:
            if self._thermal_monitor is not None:
                self._thermal_monitor.stop()
        except Exception as e:
            logger.warning(f"Error stopping thermal monitor: {e}")

        try:
            self._frame_source.release()
        except Exception as e:
            logger.warning(f"Error releasing frame source: {e}")
