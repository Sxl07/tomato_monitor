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
from src.application.services.pipeline_metrics import CycleMetrics, PipelineMetrics
from src.infrastructure.config.settings import ACTIVE_PROFILE
from src.infrastructure.vision.capture_gate import should_capture_new_image
from src.infrastructure.vision.snapshot_inference_runner import SnapshotInferenceRunner

logger = logging.getLogger(__name__)


class MonitoringWorker:
    """Runs the capture loop + inference in a background thread.

    Uses threading.Event signals for pause/abort/complete communication.
    The Scene Gate is evaluated via the functional API in capture_gate.py,
    which requires a reference frame and a frames-since-last-capture counter.

    Snapshot decision logic uses a "first wins" rule combining:
    - Time-based cooldown/timeout (wall-clock via time.monotonic())
    - Frame-based cooldown/timeout (legacy fallback)
    Whichever threshold fires first triggers the capture.
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
        min_seconds_between_snapshots: float = 8.0,
        max_seconds_without_snapshot: float = 30.0,
        capture_loop_fps: float = 2.0,
        gate_resolution: Optional[tuple[int, int]] = None,
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

        # Time-based snapshot decision parameters
        self._min_seconds_between_snapshots = min_seconds_between_snapshots
        self._max_seconds_without_snapshot = max_seconds_without_snapshot

        # Loop frequency control
        self._capture_loop_fps = capture_loop_fps

        # Gate resolution for scene comparison (passed to should_capture_new_image)
        self._gate_resolution = gate_resolution

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

        # Peak metrics tracking (session-level)
        self._peak_temperature_c: float = 0.0
        self._peak_rss_mb: float = 0.0

        # Time-based tracking (wall-clock via time.monotonic())
        self._last_snapshot_time: float = 0.0
        self._last_snapshot_reason: Optional[str] = None

        # Pipeline metrics accumulator (written to JSON at session end)
        self._pipeline_metrics = PipelineMetrics(session_start_time=time.monotonic())

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

    @property
    def last_snapshot_reason(self) -> Optional[str]:
        """Return the reason for the last snapshot capture.

        Values: "first_frame", "scene_change", or "timeout".
        """
        return self._last_snapshot_reason

    @property
    def peak_temperature_c(self) -> float:
        """Return the peak temperature observed during the session."""
        return self._peak_temperature_c

    @property
    def peak_rss_mb(self) -> float:
        """Return the peak RSS memory in MB observed during the session."""
        return self._peak_rss_mb

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
                # Record iteration start for loop frequency throttle
                iteration_start = time.perf_counter()

                # Check pause — spin-wait with short sleep
                if self.pause_event.is_set():
                    time.sleep(0.1)
                    continue

                # Read frame from source
                camera_read_start = time.perf_counter()
                success, frame = self._frame_source.read()
                camera_read_ms = (time.perf_counter() - camera_read_start) * 1000

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

                # Evaluate scene gate (timed)
                scene_gate_start = time.perf_counter()
                should_capture = self._should_capture(frame)
                scene_gate_ms = (time.perf_counter() - scene_gate_start) * 1000

                if should_capture:
                    self._process_snapshot(
                        frame,
                        camera_read_ms=camera_read_ms,
                        scene_gate_ms=scene_gate_ms,
                    )
                    # Update reference frame after capture
                    self._reference_frame = frame.copy()
                    self._frames_since_last_capture = 0
                    # Update last snapshot time for time-based decision logic
                    self._last_snapshot_time = time.monotonic()

                # Loop frequency throttle: cap at capture_loop_fps
                elapsed = time.perf_counter() - iteration_start
                target_period = 1.0 / self._capture_loop_fps
                if elapsed > target_period:
                    actual_fps = 1.0 / elapsed if elapsed > 0 else 0.0
                    logger.warning(
                        f"Loop iteration took {elapsed * 1000:.1f}ms — "
                        f"actual FPS {actual_fps:.1f} below target {self._capture_loop_fps:.1f}"
                    )
                sleep_duration = max(0.0, target_period - elapsed)
                if sleep_duration > 0:
                    time.sleep(sleep_duration)

        except Exception as e:
            self._error_reason = f"Unexpected error in capture loop: {e}"
            logger.error(f"MonitoringWorker crash: {e}", exc_info=True)
        finally:
            # Log why the loop exited
            if self.abort_event.is_set():
                self._emit_log(LogLevel.INFO, "Monitoreo detenido por el usuario.")
                logger.info(f"Worker {self._monitoring_id}: abort signal received, exiting loop.")
            elif self.complete_event.is_set():
                self._emit_log(LogLevel.INFO, "Recorrido completado.")
                logger.info(f"Worker {self._monitoring_id}: complete signal received.")
            elif self._error_reason:
                logger.info(f"Worker {self._monitoring_id}: exiting due to error.")

            # Write pipeline metrics JSON for thesis data (Req 10.1, 10.4)
            try:
                metrics_path = f"outputs/monitorings/{self._monitoring_id}/pipeline_metrics.json"
                self._pipeline_metrics.to_json_file(metrics_path, ACTIVE_PROFILE.name)
            except Exception as e:
                logger.error(f"Failed to write pipeline metrics: {e}")

            # Log session summary at INFO level
            summary = self._pipeline_metrics.get_session_summary()
            logger.info(
                f"Worker {self._monitoring_id} session summary: "
                f"cycles={summary['total_cycles']}, "
                f"snapshots/min={summary['snapshots_per_minute']:.2f}, "
                f"inferences/min={summary['inferences_per_minute']:.2f}, "
                f"peak_temp={summary['peak_temperature_c']:.1f}°C, "
                f"peak_rss={summary['peak_rss_mb']:.1f}MB"
            )

            self._release_resources()

    def _should_capture(self, frame: np.ndarray) -> bool:
        """Evaluate snapshot decision combining time-based and frame-based logic.

        Uses a "first wins" rule: whichever threshold (time or frame) is met
        first triggers the capture. Time-based cooldown blocks even if the
        scene gate would otherwise trigger.

        Sets self._last_snapshot_reason to one of:
        - "first_frame": first frame of the session, always captured
        - "scene_change": ORB/HSV gate triggered and cooldown satisfied
        - "timeout": max_seconds_without_snapshot or scene_gate_timeout_frames elapsed

        Returns True if a snapshot should be taken.
        """
        now = time.monotonic()

        # First frame: always capture to establish reference
        if self._reference_frame is None:
            self._last_snapshot_reason = "first_frame"
            return True

        # Compute elapsed time since last snapshot
        elapsed_seconds = now - self._last_snapshot_time

        # Time-based cooldown: block premature snapshots regardless of gate signal
        time_cooldown_ok = elapsed_seconds >= self._min_seconds_between_snapshots

        # Frame-based cooldown: legacy mechanism
        frame_cooldown_ok = self._frames_since_last_capture >= self._scene_gate_cooldown_frames

        # Time-based timeout: force capture if too long without snapshot
        time_timeout = elapsed_seconds >= self._max_seconds_without_snapshot

        # Frame-based timeout (legacy fallback): force capture
        frame_timeout = self._frames_since_last_capture >= self._scene_gate_timeout_frames

        # "First wins" rule for timeout: either time or frame timeout triggers
        if time_timeout or frame_timeout:
            self._last_snapshot_reason = "timeout"
            return True

        # "First wins" for cooldown: either time or frame cooldown met enables scene gate
        cooldown_passed = time_cooldown_ok or frame_cooldown_ok
        if not cooldown_passed:
            return False

        # Evaluate scene gate (ORB + HSV) with optional gate_resolution.
        # If time-based cooldown already passed, override cooldown_frames to 0
        # so the gate's internal frame-based cooldown doesn't block the trigger.
        # This implements the "first wins" rule correctly: time cooldown being
        # satisfied is sufficient to enable scene-change detection.
        # The gate's internal timeout is disabled (set to a high value) because
        # the worker handles both time-based and frame-based timeouts above.
        effective_cooldown_frames = (
            0 if time_cooldown_ok else self._scene_gate_cooldown_frames
        )
        gate_kwargs: dict = {
            "reference_bgr": self._reference_frame,
            "current_bgr": frame,
            "frames_since_last_capture": self._frames_since_last_capture,
            "cooldown_frames": effective_cooldown_frames,
            "timeout_frames": 999999,
            "orb_threshold": self._scene_gate_orb_threshold,
            "hsv_threshold": self._scene_gate_hsv_threshold,
        }
        if self._gate_resolution is not None:
            gate_kwargs["gate_resolution"] = self._gate_resolution

        trigger, _metrics = should_capture_new_image(**gate_kwargs)

        if trigger:
            self._last_snapshot_reason = "scene_change"
            return True

        return False

    def _process_snapshot(
        self,
        frame: np.ndarray,
        *,
        camera_read_ms: float = 0.0,
        scene_gate_ms: float = 0.0,
    ) -> None:
        """Save snapshot image, run inference, persist results.

        Checks abort_event cooperatively before expensive operations
        to ensure fast response to abort signals.
        Constructs CycleMetrics and records pipeline timings.
        """
        # Cooperative abort check — don't start new work if abort requested
        if self.abort_event.is_set():
            return

        self._emit_log(
            LogLevel.INFO,
            f"Capturando imagen... (snapshot {self._snapshot_count + 1})",
        )

        # Save image to filesystem (timed)
        snapshot_dir = f"outputs/monitorings/{self._monitoring_id}/snapshots"
        os.makedirs(snapshot_dir, exist_ok=True)
        image_path = f"{snapshot_dir}/snapshot_{self._snapshot_count}.jpg"

        snapshot_save_start = time.perf_counter()
        try:
            cv2.imwrite(image_path, frame)
        except Exception as e:
            self._emit_log(LogLevel.ERROR, f"Error de almacenamiento: {e}")
            logger.error(f"Failed to save snapshot image: {e}")
            self._error_reason = f"Filesystem error: {e}"
            self.abort_event.set()
            return
        snapshot_save_ms = (time.perf_counter() - snapshot_save_start) * 1000

        # Cooperative abort check before inference (most expensive operation)
        if self.abort_event.is_set():
            return

        # Run inference (with timing instrumentation)
        detection_results: list[dict] = []
        _inference_timings = None
        try:
            detection_results, _inference_timings = self._inference_runner.run_inference_timed(frame)
        except Exception as e:
            logger.warning(
                f"Inference failed for snapshot {self._snapshot_count}: {e}"
            )
            # Skip inference results but still persist the snapshot (Req 3.6)

        # Cooperative abort check before persistence
        if self.abort_event.is_set():
            return

        self._emit_log(
            LogLevel.SUCCESS, f"Tomates detectados: {len(detection_results)}"
        )

        has_detections = len(detection_results) > 0

        # Create and persist Snapshot entity (timed)
        persistence_start = time.perf_counter()

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

        persistence_ms = (time.perf_counter() - persistence_start) * 1000

        # Read temperature from thermal monitor (if available)
        current_temperature: Optional[float] = None
        if self._thermal_monitor is not None:
            current_temperature = self._thermal_monitor.peak_temperature
            # The thermal monitor tracks its own peak via its polling loop.
            # We also read the latest polled value as the cycle temperature.

        # Read RSS memory and check budget
        rss_mb = self._check_memory_usage()

        # Track peak temperature and peak RSS for session-level metrics
        if current_temperature is not None and current_temperature > self._peak_temperature_c:
            self._peak_temperature_c = current_temperature

        if rss_mb is not None and rss_mb > self._peak_rss_mb:
            self._peak_rss_mb = rss_mb

        # Construct CycleMetrics and record in PipelineMetrics
        snapshot_reason = self._last_snapshot_reason or "first_frame"
        cycle = CycleMetrics(
            cycle_index=self._snapshot_count - 1,
            snapshot_reason=snapshot_reason,
            camera_read_ms=camera_read_ms,
            scene_gate_ms=scene_gate_ms,
            snapshot_save_ms=snapshot_save_ms,
            inference_total_ms=_inference_timings.total_ms if _inference_timings else 0.0,
            detection_ms=_inference_timings.detection_ms if _inference_timings else 0.0,
            health_ms=_inference_timings.health_ms if _inference_timings else 0.0,
            maturity_ms=_inference_timings.maturity_ms if _inference_timings else 0.0,
            persistence_ms=persistence_ms,
            temperature_c=current_temperature,
            rss_mb=rss_mb,
            timestamp=time.monotonic(),
        )
        self._pipeline_metrics.add_cycle(cycle)

        # Log snapshot_reason at INFO level (Req 1.10)
        logger.info(
            f"Snapshot {self._snapshot_count} captured — reason: {snapshot_reason}"
        )

        # Log all timings at DEBUG level (Req 1.11)
        logger.debug(
            f"Cycle {cycle.cycle_index} timings: "
            f"camera_read={camera_read_ms:.2f}ms, "
            f"scene_gate={scene_gate_ms:.2f}ms, "
            f"snapshot_save={snapshot_save_ms:.2f}ms, "
            f"inference_total={cycle.inference_total_ms:.2f}ms "
            f"(det={cycle.detection_ms:.2f}ms, health={cycle.health_ms:.2f}ms, "
            f"maturity={cycle.maturity_ms:.2f}ms), "
            f"persistence={persistence_ms:.2f}ms, "
            f"temp={current_temperature}°C, rss={rss_mb}MB"
        )

        # Release frame reference to aid garbage collection
        frame = None

    def _check_memory_usage(self) -> Optional[float]:
        """Log warning if RSS memory exceeds budget. Returns RSS in MB or None."""
        try:
            import resource
            # ru_maxrss is in KB on Linux
            rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            rss_mb = rss_kb / 1024
            if rss_mb > self._memory_warning_rss_mb:
                logger.warning(f"Memory usage {rss_mb:.0f} MB exceeds budget {self._memory_warning_rss_mb} MB")
            return rss_mb
        except Exception:
            pass  # resource module may not be available on all platforms
        return None

    def _release_resources(self) -> None:
        """Release frame source and stop thermal monitor when done."""
        self._emit_log(LogLevel.INFO, "Liberando recursos de cámara...")

        try:
            if self._thermal_monitor is not None:
                self._thermal_monitor.stop()
        except Exception as e:
            logger.warning(f"Error stopping thermal monitor: {e}")

        try:
            self._frame_source.release()
            self._emit_log(LogLevel.SUCCESS, "Cámara liberada correctamente.")
            logger.info(f"Camera released for monitoring {self._monitoring_id}")
        except Exception as e:
            self._emit_log(LogLevel.WARNING, f"Error al liberar cámara: {e}")
            logger.warning(f"Error releasing frame source: {e}")
