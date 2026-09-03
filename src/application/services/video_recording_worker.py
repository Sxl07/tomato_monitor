"""VideoRecordingWorker — records the walk to video without inference (Spec 019).

Analogous to ``CaptureWorker`` but it RECORDS the full video instead of selecting
snapshots. It reads frames from an injected ``FrameSource`` and writes EVERY frame
to an injected ``VideoRecorder``. No inference, Scene Gate, sampling, dedup,
downscale or frame skipping happens here.

Threading model (matches CaptureWorker):
    - This class does NOT subclass ``threading.Thread`` and does NOT spawn a
      thread. ``run()`` is synchronous. ``MonitoringService`` (Task 10) will run
      it in a daemon thread.

Layer/boundary rules:
    - This module lives in the application layer and MUST NOT import ``cv2``,
      ``torch``, ``detectron2`` or ``picamera2`` (directly or indirectly). The
      ``VideoRecorder`` type is only referenced under ``TYPE_CHECKING``; the real
      object is injected.
    - The worker does NOT create the camera. It consumes ``frame_source.read()``;
      the FrameSource is the single camera owner (persistent lock acquired on
      first read). No ``_camera_lock`` is touched here.

Cadence:
    - The camera/frame source controls the physical cadence (Task 4.2
      FrameDurationLimits). The worker adds NO FPS throttle: in the normal
      recording path it performs ZERO sleeps. The only sleep is the cooperative
      pause wait while ``pause_event``/``thermal_pause_event`` is set.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from src.domain.interfaces.frame_source import FrameSource

if TYPE_CHECKING:  # pragma: no cover - typing only; avoids importing cv2 at runtime
    from src.infrastructure.camera.video_recorder import VideoRecorder

logger = logging.getLogger(__name__)


@dataclass
class RecordingMetrics:
    """Metrics collected during the recording phase.

    ``container_fps`` always equals ``configured_recording_fps`` (the fps the
    VideoWriter was opened with). ``effective_recording_fps`` and the deviation
    are DIAGNOSTIC only — the video is never rewritten to "correct" fps.
    """

    recording_duration_seconds: float = 0.0
    frames_written: int = 0
    configured_recording_fps: float = 0.0
    container_fps: float = 0.0
    effective_recording_fps: float = 0.0
    deviation_between_configured_and_effective_fps: float = 0.0
    recorded_width: int = 0
    recorded_height: int = 0
    codec_used: str = ""
    peak_temperature_c: float = 0.0
    exit_reason: str = ""  # "finalize" | "abort" | "error" | "frame_source_exhausted"


class VideoRecordingWorker:
    """Records every frame from a FrameSource into a VideoRecorder.

    The worker is the exclusive consumer of the frame source during recording,
    but it never opens the camera itself — the injected FrameSource owns it.
    """

    def __init__(
        self,
        monitoring_id: int,
        frame_source: FrameSource,
        video_recorder: "VideoRecorder",
        monitoring_repo,
        db_session,
        *,
        configured_recording_fps: float,
        thermal_monitor: Optional[object] = None,
        log_service: Optional[object] = None,
    ) -> None:
        self._monitoring_id = monitoring_id
        self._frame_source = frame_source
        self._video_recorder = video_recorder
        self._monitoring_repo = monitoring_repo
        self._db_session = db_session
        self._configured_recording_fps = float(configured_recording_fps)
        self._thermal_monitor = thermal_monitor
        self._log_service = log_service

        # Threading signals (same names/semantics as CaptureWorker).
        self.abort_event = threading.Event()
        self.finalize_event = threading.Event()
        # Backward-compatible alias: MonitoringService consumers historically call
        # worker.complete_event.set(). It MUST be the SAME object as finalize_event
        # (not a second Event), matching CaptureWorker.
        self.complete_event = self.finalize_event
        self.pause_event = threading.Event()
        self.thermal_pause_event = threading.Event()

        # Last-frame buffer for preview (protected by its OWN lock, unrelated to
        # the camera lock).
        self._last_frame = None
        self._last_frame_lock = threading.Lock()

        # State / metrics.
        self._error_reason: Optional[str] = None
        self._exit_reason: str = ""
        self._recorded_width: int = 0
        self._recorded_height: int = 0
        self._start_time: float = 0.0
        self._end_time: float = 0.0
        self._resources_released: bool = False

    # --- public accessors -------------------------------------------------- #

    @property
    def error_reason(self) -> Optional[str]:
        """Error reason if the worker exited due to an error, else None."""
        return self._error_reason

    def get_last_frame(self):
        """Return a COPY of the last recorded frame, or None if none yet.

        Thread-safe: never returns the internal mutable buffer.
        """
        with self._last_frame_lock:
            if self._last_frame is None:
                return None
            return self._last_frame.copy()

    @property
    def recording_metrics(self) -> RecordingMetrics:
        """Compute recording metrics from the recorder (single source of truth)."""
        if self._end_time > 0.0:
            duration = self._end_time - self._start_time if self._start_time else 0.0
        elif self._start_time > 0.0:
            duration = time.monotonic() - self._start_time
        else:
            duration = 0.0

        frames_written = getattr(self._video_recorder, "frames_written", 0)
        codec_used = getattr(self._video_recorder, "codec_used", "") or ""

        effective_fps = frames_written / duration if duration > 0 else 0.0
        configured = self._configured_recording_fps
        deviation = configured - effective_fps

        peak_temp = 0.0
        if self._thermal_monitor is not None:
            peak_temp = getattr(self._thermal_monitor, "peak_temperature", 0.0)

        return RecordingMetrics(
            recording_duration_seconds=duration,
            frames_written=frames_written,
            configured_recording_fps=configured,
            container_fps=configured,  # container_fps == configured_recording_fps
            effective_recording_fps=effective_fps,
            deviation_between_configured_and_effective_fps=deviation,
            recorded_width=self._recorded_width,
            recorded_height=self._recorded_height,
            codec_used=codec_used,
            peak_temperature_c=peak_temp,
            exit_reason=self._exit_reason,
        )

    # --- lifecycle --------------------------------------------------------- #

    def run(self) -> None:
        """Recording loop. Writes every frame; always cleans up in ``finally``.

        Exits on ``finalize_event``, ``abort_event``, frame-source exhaustion, or
        error. Does NOT spawn a thread (the caller runs this synchronously in a
        daemon thread).
        """
        self._start_time = time.monotonic()
        self._end_time = 0.0
        self._exit_reason = ""
        recorder_opened = False

        try:
            if self._thermal_monitor is not None:
                start_fn = getattr(self._thermal_monitor, "start", None)
                if callable(start_fn):
                    start_fn()

            self._emit_log("info", "Grabación iniciada — esperando frames...")

            while not self.abort_event.is_set() and not self.finalize_event.is_set():
                # Cooperative pause (manual OR thermal). This is the ONLY sleep;
                # it is not an FPS throttle.
                while (
                    (self.pause_event.is_set() or self.thermal_pause_event.is_set())
                    and not self.abort_event.is_set()
                    and not self.finalize_event.is_set()
                ):
                    time.sleep(0.1)

                if self.abort_event.is_set() or self.finalize_event.is_set():
                    break

                # Read a frame. A read() exception is an error exit.
                try:
                    success, frame = self._frame_source.read()
                except Exception as exc:
                    self._error_reason = f"Error leyendo frame: {exc}"
                    self._exit_reason = "error"
                    self._emit_log("error", "Error leyendo de la cámara.")
                    logger.error(
                        "VideoRecordingWorker %s: frame_source.read() raised: %s",
                        self._monitoring_id,
                        exc,
                    )
                    break

                if not success or frame is None:
                    self._error_reason = "Frame source no disponible"
                    self._exit_reason = "frame_source_exhausted"
                    self._emit_log("error", "La cámara dejó de responder.")
                    break

                # Open the recorder lazily from the FIRST real frame's size.
                if not recorder_opened:
                    height = frame.shape[0]
                    width = frame.shape[1]
                    try:
                        self._video_recorder.open(frame_size=(width, height))
                    except Exception as exc:
                        self._error_reason = f"No se pudo iniciar la grabación: {exc}"
                        self._exit_reason = "error"
                        self._emit_log("error", "No se pudo iniciar la grabación de video.")
                        logger.error(
                            "VideoRecordingWorker %s: recorder.open() raised: %s",
                            self._monitoring_id,
                            exc,
                        )
                        break
                    recorder_opened = True
                    self._recorded_width = width
                    self._recorded_height = height

                # Write EVERY frame exactly once (including the first).
                try:
                    self._video_recorder.write(frame)
                except Exception as exc:
                    self._error_reason = f"Error escribiendo frame: {exc}"
                    self._exit_reason = "error"
                    self._emit_log("error", "Error al grabar el video.")
                    logger.error(
                        "VideoRecordingWorker %s: recorder.write() raised: %s",
                        self._monitoring_id,
                        exc,
                    )
                    break

                # Update the preview buffer only after a successful write.
                self._set_last_frame(frame)

            # Determine exit reason if not already set by an error path.
            if not self._exit_reason:
                if self.finalize_event.is_set():
                    self._exit_reason = "finalize"
                elif self.abort_event.is_set():
                    self._exit_reason = "abort"

            self._emit_log("info", "Grabación finalizada.")

        except Exception as exc:  # defensive catch-all
            self._error_reason = f"Error inesperado en grabación: {exc}"
            self._exit_reason = "error"
            logger.error(
                "VideoRecordingWorker %s crashed: %s", self._monitoring_id, exc
            )
            self._emit_log("error", f"Error en grabación: {exc}")

        finally:
            if self._end_time == 0.0:
                self._end_time = time.monotonic()
            self.release_resources()

    def release_resources(self) -> None:
        """Close the recorder and release the frame source; idempotent.

        The two cleanups are INDEPENDENT: a failure closing the recorder must
        never prevent releasing the camera (the camera has cleanup priority).
        Also stops the thermal monitor if present. Safe to call before ``run()``
        and multiple times.
        """
        if self._resources_released:
            return
        self._resources_released = True

        # 1) Close the recorder (best-effort; failure must not block camera release).
        try:
            self._video_recorder.close()
        except Exception as exc:
            logger.warning(
                "VideoRecordingWorker %s: recorder.close() failed: %s",
                self._monitoring_id,
                exc,
            )

        # 2) Release the frame source (camera) — highest cleanup priority.
        try:
            self._frame_source.release()
        except Exception as exc:
            logger.warning(
                "VideoRecordingWorker %s: frame_source.release() failed: %s",
                self._monitoring_id,
                exc,
            )

        # 3) Stop the thermal monitor if present.
        if self._thermal_monitor is not None:
            stop_fn = getattr(self._thermal_monitor, "stop", None)
            if callable(stop_fn):
                try:
                    stop_fn()
                except Exception:
                    pass

    # --- internal helpers -------------------------------------------------- #

    def _set_last_frame(self, frame) -> None:
        """Store a thread-safe copy of the last recorded frame for preview."""
        copy = frame.copy()
        with self._last_frame_lock:
            self._last_frame = copy

    def _emit_log(self, level: str, message: str) -> None:
        """Emit a log entry via LogService if available (defensive)."""
        if self._log_service is None:
            return
        try:
            from src.application.services.log_service import LogLevel

            log_level = LogLevel(level)
            self._log_service.add_entry(
                monitoring_id=self._monitoring_id,
                level=log_level,
                source="video_recording_worker",
                message=message,
            )
        except Exception:
            pass  # Log service failure must not crash recording.
