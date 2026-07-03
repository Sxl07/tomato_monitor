"""CaptureWorker — fast capture loop without inference.

Runs in a daemon thread. Reads frames from the FrameSource, evaluates
the Scene Gate (time-based cooldown/timeout + ORB/HSV change detection),
and when triggered, saves the raw snapshot image and persists metadata.

NO inference is executed during capture. The capture loop is designed to
complete each iteration in under 500ms (excluding throttle sleep).

Spec 009: capture-first-final-analysis-pipeline, Phase B.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np
from sqlalchemy.orm import Session

from src.domain.entities.snapshot import Snapshot
from src.domain.interfaces.frame_source import FrameSource
from src.domain.repositories.monitoring_repository import MonitoringRepository
from src.domain.repositories.snapshot_repository import SnapshotRepository
from src.infrastructure.vision.capture_gate import should_capture_new_image

logger = logging.getLogger(__name__)


@dataclass
class CaptureMetrics:
    """Metrics collected during the capture phase."""

    duration_seconds: float = 0.0
    total_snapshots: int = 0
    total_loop_cycles: int = 0
    effective_fps: float = 0.0
    capture_reasons: dict[str, int] = field(
        default_factory=lambda: {"first_frame": 0, "scene_change": 0, "timeout": 0}
    )
    avg_iteration_ms: float = 0.0
    avg_scene_gate_ms: float = 0.0
    avg_save_ms: float = 0.0
    peak_temperature_c: float = 0.0
    thermal_events: int = 0
    exit_reason: str = ""  # "finalize", "abort", "error", "frame_source_exhausted"


class CaptureWorker:
    """Fast capture-only worker for monitoring sessions.

    Reads frames from a FrameSource, evaluates time-based cooldown/timeout
    and Scene Gate (ORB + HSV), saves raw JPEG snapshots, and persists
    metadata to the database. Does NOT execute any inference.

    Designed for Raspberry Pi 5 edge deployment where the capture loop
    must remain fast (~200ms/iteration target) to avoid missing scene changes.
    """

    def __init__(
        self,
        monitoring_id: int,
        frame_source: FrameSource,
        snapshot_repo: SnapshotRepository,
        monitoring_repo: MonitoringRepository,
        db_session: Session,
        *,
        capture_loop_fps: float = 5.0,
        min_seconds_between_snapshots: float = 1.0,
        max_seconds_without_snapshot: float = 3.0,
        gate_resolution: tuple[int, int] = (240, 240),
        scene_gate_orb_threshold: int = 35,
        scene_gate_hsv_threshold: float = 0.38,
        thermal_monitor: Optional[object] = None,
        log_service: Optional[object] = None,
    ) -> None:
        self._monitoring_id = monitoring_id
        self._frame_source = frame_source
        self._snapshot_repo = snapshot_repo
        self._monitoring_repo = monitoring_repo
        self._db_session = db_session

        # Capture parameters
        self._capture_loop_fps = capture_loop_fps
        self._min_seconds_between_snapshots = min_seconds_between_snapshots
        self._max_seconds_without_snapshot = max_seconds_without_snapshot
        self._gate_resolution = gate_resolution
        self._scene_gate_orb_threshold = scene_gate_orb_threshold
        self._scene_gate_hsv_threshold = scene_gate_hsv_threshold

        # Optional services
        self._thermal_monitor = thermal_monitor
        self._log_service = log_service

        # Threading signals
        self.abort_event = threading.Event()
        self.finalize_event = threading.Event()
        self.pause_event = threading.Event()  # Cooperative pause (thermal, manual)
        self.complete_event = self.finalize_event  # Alias for backward compat with complete_session()

        # State
        self._snapshot_count: int = 0
        self._error_reason: Optional[str] = None
        self._reference_frame: Optional[np.ndarray] = None
        self._last_snapshot_time: float = 0.0
        self._capture_start_time: float = 0.0

        # Metrics accumulators (initialized here so capture_metrics is safe before run())
        self._total_loop_cycles: int = 0
        self._iteration_times: list[float] = []
        self._scene_gate_times: list[float] = []
        self._save_times: list[float] = []
        self._metrics_reasons: dict[str, int] = {
            "first_frame": 0,
            "scene_change": 0,
            "timeout": 0,
        }
        self._exit_reason: str = ""

    @property
    def snapshot_count(self) -> int:
        """Number of snapshots captured so far."""
        return self._snapshot_count

    @property
    def error_reason(self) -> Optional[str]:
        """Error reason if the worker exited due to an error."""
        return self._error_reason

    @property
    def capture_metrics(self) -> CaptureMetrics:
        """Capture phase metrics for pipeline_metrics.json."""
        duration = time.monotonic() - self._capture_start_time if self._capture_start_time else 0.0
        avg_iter = (
            (sum(self._iteration_times) / len(self._iteration_times) * 1000)
            if self._iteration_times
            else 0.0
        )
        avg_gate = (
            (sum(self._scene_gate_times) / len(self._scene_gate_times) * 1000)
            if self._scene_gate_times
            else 0.0
        )
        avg_save = (
            (sum(self._save_times) / len(self._save_times) * 1000)
            if self._save_times
            else 0.0
        )
        effective_fps = self._total_loop_cycles / duration if duration > 0 else 0.0
        peak_temp = 0.0
        thermal_events = 0
        if self._thermal_monitor is not None:
            peak_temp = getattr(self._thermal_monitor, "peak_temperature", 0.0)
            thermal_events = getattr(self._thermal_monitor, "pause_count", 0)

        return CaptureMetrics(
            duration_seconds=duration,
            total_snapshots=self._snapshot_count,
            total_loop_cycles=self._total_loop_cycles,
            effective_fps=effective_fps,
            capture_reasons=dict(self._metrics_reasons),
            avg_iteration_ms=avg_iter,
            avg_scene_gate_ms=avg_gate,
            avg_save_ms=avg_save,
            peak_temperature_c=peak_temp,
            thermal_events=thermal_events,
            exit_reason=self._exit_reason,
        )

    def run(self) -> None:
        """Main capture loop. Exits on finalize_event, abort_event, or error.

        Always releases the frame source (and stops ThermalMonitor) in the finally block.
        """
        self._capture_start_time = time.monotonic()
        # Reset metrics for this run (safe to call run() multiple times in tests)
        self._metrics_reasons = {
            "first_frame": 0,
            "scene_change": 0,
            "timeout": 0,
        }
        self._exit_reason = ""
        loop_period = 1.0 / self._capture_loop_fps if self._capture_loop_fps > 0 else 0.2

        # Start thermal monitor if available
        if self._thermal_monitor is not None:
            start_fn = getattr(self._thermal_monitor, "start", None)
            if callable(start_fn):
                start_fn()

        try:
            self._emit_log("info", "Captura iniciada — esperando frames...")

            while not self.abort_event.is_set() and not self.finalize_event.is_set():
                # Cooperative pause: wait while pause_event is set
                while self.pause_event.is_set() and not self.abort_event.is_set() and not self.finalize_event.is_set():
                    time.sleep(0.1)

                # Re-check exit conditions after pause
                if self.abort_event.is_set() or self.finalize_event.is_set():
                    break

                iter_start = time.monotonic()
                self._total_loop_cycles += 1

                # Read frame
                success, frame = self._frame_source.read()
                if not success or frame is None:
                    # Frame source exhausted or failed
                    self._error_reason = "Frame source no disponible"
                    self._exit_reason = "frame_source_exhausted"
                    self._emit_log("error", "La cámara dejó de responder.")
                    break

                # Decide whether to capture
                should_capture, reason = self._should_capture(frame)

                if should_capture:
                    save_start = time.monotonic()
                    self._save_snapshot(frame, reason)
                    self._save_times.append(time.monotonic() - save_start)

                # Record iteration time
                iter_elapsed = time.monotonic() - iter_start
                self._iteration_times.append(iter_elapsed)

                # Throttle to capture_loop_fps
                sleep_time = loop_period - iter_elapsed
                if sleep_time > 0:
                    # Use short sleeps so we can respond quickly to events
                    time.sleep(sleep_time)

            # Determine exit reason if not already set
            if not self._exit_reason:
                if self.finalize_event.is_set():
                    self._exit_reason = "finalize"
                elif self.abort_event.is_set():
                    self._exit_reason = "abort"

            self._emit_log(
                "info",
                f"Captura finalizada — {self._snapshot_count} snapshots capturados.",
            )

        except Exception as e:
            self._error_reason = f"Error inesperado en captura: {e}"
            self._exit_reason = "error"
            logger.error(f"CaptureWorker {self._monitoring_id} crashed: {e}")
            self._emit_log("error", f"Error en captura: {e}")

        finally:
            # Always release the frame source
            try:
                self._frame_source.release()
            except Exception as release_err:
                logger.warning(f"Error releasing frame source: {release_err}")

            # Stop thermal monitor if available
            if self._thermal_monitor is not None:
                stop_fn = getattr(self._thermal_monitor, "stop", None)
                if callable(stop_fn):
                    try:
                        stop_fn()
                    except Exception:
                        pass

    def _should_capture(self, frame: np.ndarray) -> tuple[bool, str]:
        """Evaluate whether to capture this frame.

        Returns (should_capture, reason) where reason is one of:
        "first_frame", "scene_change", "timeout", or "" (no capture).
        """
        now = time.monotonic()

        # First frame: always capture
        if self._reference_frame is None:
            return True, "first_frame"

        # Time since last snapshot
        elapsed = now - self._last_snapshot_time

        # Cooldown: don't evaluate gate if too soon
        if elapsed < self._min_seconds_between_snapshots:
            return False, ""

        # Timeout: force capture regardless of gate
        if elapsed >= self._max_seconds_without_snapshot:
            return True, "timeout"

        # Evaluate Scene Gate (between cooldown and timeout)
        gate_start = time.monotonic()
        try:
            trigger, _metrics = should_capture_new_image(
                reference_bgr=self._reference_frame,
                current_bgr=frame,
                frames_since_last_capture=999,  # Not using frame-based, pass high value
                cooldown_frames=0,  # Disabled — using time-based cooldown instead
                timeout_frames=999999,  # Disabled — using time-based timeout instead
                orb_threshold=self._scene_gate_orb_threshold,
                hsv_threshold=self._scene_gate_hsv_threshold,
                gate_resolution=self._gate_resolution,
            )
        except Exception as e:
            logger.warning(f"Scene Gate evaluation failed: {e}")
            trigger = False
        finally:
            self._scene_gate_times.append(time.monotonic() - gate_start)

        if trigger:
            return True, "scene_change"

        return False, ""

    def _save_snapshot(self, frame: np.ndarray, reason: str) -> None:
        """Save raw snapshot to disk and persist metadata to DB."""
        # Build path
        snapshot_dir = f"outputs/monitorings/{self._monitoring_id}/snapshots/raw"
        os.makedirs(snapshot_dir, exist_ok=True)
        image_filename = f"snapshot_{self._snapshot_count:06d}.jpg"
        image_path = f"{snapshot_dir}/{image_filename}"

        # Write JPEG to disk — validate return value
        try:
            ok = cv2.imwrite(image_path, frame)
            if not ok:
                raise OSError(f"No se pudo escribir snapshot en {image_path}")
        except Exception as e:
            self._error_reason = f"Error guardando snapshot: {e}"
            self._exit_reason = "error"
            self._emit_log("error", f"Error de almacenamiento: {e}")
            logger.error(f"Failed to save snapshot image: {e}")
            self.abort_event.set()
            return

        # Persist Snapshot entity to DB
        try:
            snapshot = Snapshot(
                monitoring_id=self._monitoring_id,
                image_path=image_path,
                frame_index=self._snapshot_count,
                has_detections=False,
            )
            self._snapshot_repo.create(self._monitoring_id, snapshot)

            # Update counters (total_detections stays 0 during capture)
            self._snapshot_count += 1
            self._monitoring_repo.update_counters(
                self._monitoring_id,
                total_snapshots=self._snapshot_count,
                total_detections=0,
            )

            self._db_session.commit()
        except Exception as e:
            self._error_reason = f"Error de persistencia: {e}"
            self._exit_reason = "error"
            self._emit_log("error", f"Error guardando metadatos: {e}")
            logger.error(f"Failed to persist snapshot: {e}")
            self.abort_event.set()
            return

        # Update state
        self._reference_frame = frame.copy()
        self._last_snapshot_time = time.monotonic()
        self._metrics_reasons[reason] = self._metrics_reasons.get(reason, 0) + 1

        self._emit_log(
            "info",
            f"Snapshot {self._snapshot_count} capturado ({reason})",
        )

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
                source="capture_worker",
                message=message,
            )
        except Exception:
            pass  # Log service failure should not crash capture
