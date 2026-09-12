"""VideoRecordingWorker — records the walk to video (Spec 019 + Spec 023).

Analogous to ``CaptureWorker`` but it RECORDS a video instead of selecting
snapshots. It reads frames from an injected ``FrameSource`` and, per Spec 023,
DECOUPLES the preview cadence from the recording cadence:

    - It reads EVERY physical frame from the FrameSource (~camera_stream_fps).
    - It updates the preview buffer for EVERY successful frame (so the live
      preview runs at ~camera_stream_fps), incrementing ``preview_sequence``.
    - It persists ONLY the frames accepted by the injected ``RecordingSampler``
      (a temporal policy that yields ~recording_target_fps). No inference, Scene
      Gate, dedup or downscale happens here.

Threading model (matches CaptureWorker):
    - This class does NOT subclass ``threading.Thread`` and does NOT spawn a
      thread. ``run()`` is synchronous. ``MonitoringService`` runs it in a daemon
      thread.

Layer/boundary rules:
    - This module lives in the application layer and MUST NOT import ``cv2``,
      ``torch``, ``detectron2`` or ``picamera2`` (directly or indirectly). The
      ``VideoRecorder`` type is only referenced under ``TYPE_CHECKING``; the real
      object is injected. It MAY import ``RecordingSampler`` (also application).
    - The worker does NOT create the camera. It consumes ``frame_source.read()``;
      the FrameSource is the single camera owner (persistent lock acquired on
      first read). No ``_camera_lock`` is touched here.

Cadence:
    - The camera/frame source controls the physical cadence (Spec 023:
      camera_stream_fps via FrameDurationLimits). The worker adds NO FPS throttle:
      in the normal recording path it performs ZERO sleeps. The only sleep is the
      cooperative pause wait while ``pause_event``/``thermal_pause_event`` is set.
    - The recording cadence is decided by ``RecordingSampler.should_write()``.
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
    from src.application.services.recording_sampler import RecordingSampler

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

    # --- Spec 023 diagnostic metrics (appended; do not reorder/rename above) ---
    # camera_frames_produced: successful FrameSource.read() calls (~camera_stream).
    # preview_frames_updated: times the preview buffer was actually updated.
    # camera_capture_elapsed_seconds: active-capture window (first→last frame)
    #   minus paused time; NOT the whole worker lifetime.
    # accumulated_pause_seconds: paused time that falls INSIDE the capture window.
    # configured_camera_stream_fps: the requested physical cadence.
    # effective_camera_stream_fps: (N-1)/camera_capture_elapsed_seconds for N>=2.
    camera_frames_produced: int = 0
    preview_frames_updated: int = 0
    camera_capture_elapsed_seconds: float = 0.0
    accumulated_pause_seconds: float = 0.0
    configured_camera_stream_fps: float = 0.0
    effective_camera_stream_fps: float = 0.0


class VideoRecordingWorker:
    """Reads every physical frame from a FrameSource, updates the preview for
    every successful frame, and persists ONLY the frames accepted by the injected
    ``RecordingSampler`` (Spec 023 preview/recording decoupling).

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
        configured_camera_stream_fps: float,
        recording_sampler: "RecordingSampler",
        thermal_monitor: Optional[object] = None,
        log_service: Optional[object] = None,
    ) -> None:
        self._monitoring_id = monitoring_id
        self._frame_source = frame_source
        self._video_recorder = video_recorder
        self._monitoring_repo = monitoring_repo
        self._db_session = db_session
        self._configured_recording_fps = float(configured_recording_fps)
        self._configured_camera_stream_fps = float(configured_camera_stream_fps)
        self._recording_sampler = recording_sampler
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
        # the camera lock). ``_preview_sequence`` increments under the same lock
        # whenever the buffer is updated, so a reader can atomically pair a frame
        # with its sequence (Spec 023).
        self._last_frame = None
        self._last_frame_lock = threading.Lock()
        self._preview_sequence: int = 0

        # State / metrics.
        self._error_reason: Optional[str] = None
        self._exit_reason: str = ""
        self._recorded_width: int = 0
        self._recorded_height: int = 0
        self._start_time: float = 0.0
        self._end_time: float = 0.0
        self._resources_released: bool = False

        # --- Spec 023 camera/preview cadence metrics state ---
        self._camera_frames_produced: int = 0
        self._preview_frames_updated: int = 0
        self._first_camera_frame_time: Optional[float] = None
        self._last_camera_frame_time: Optional[float] = None
        # Pause accounting: only pauses that fall INSIDE the capture window
        # [first_frame, last_frame] are discounted. A pause is held in
        # ``_pending_pause_duration`` when it ends and only consolidated into
        # ``_accumulated_pause_seconds`` when the NEXT successful frame arrives.
        self._accumulated_pause_seconds: float = 0.0
        self._pending_pause_duration: float = 0.0
        self._pause_started_at: Optional[float] = None

    # --- public accessors -------------------------------------------------- #

    @property
    def error_reason(self) -> Optional[str]:
        """Error reason if the worker exited due to an error, else None."""
        return self._error_reason

    def get_last_frame(self):
        """Return a COPY of the last preview frame, or None if none yet.

        Thread-safe: never returns the internal mutable buffer. Semantics are
        unchanged from Spec 019 (None before the first frame, a copy after).
        """
        with self._last_frame_lock:
            if self._last_frame is None:
                return None
            return self._last_frame.copy()

    def get_preview_frame_snapshot(self):
        """Return ``(preview_sequence, frame_copy)`` or None before the first frame.

        The sequence and the frame are read atomically under ``_last_frame_lock``
        so a consumer (e.g. the Spec 023 preview stream) can detect a new frame by
        sequence without comparing pixels. The frame is a COPY; it never shares
        mutable memory with the internal buffer.
        """
        with self._last_frame_lock:
            if self._last_frame is None:
                return None
            return (self._preview_sequence, self._last_frame.copy())

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

        # --- Spec 023 camera-cadence metrics (active-capture window) ---
        camera_capture_elapsed = 0.0
        effective_camera_fps = 0.0
        if (
            self._first_camera_frame_time is not None
            and self._last_camera_frame_time is not None
        ):
            camera_capture_elapsed = max(
                0.0,
                self._last_camera_frame_time
                - self._first_camera_frame_time
                - self._accumulated_pause_seconds,
            )
        if self._camera_frames_produced >= 2 and camera_capture_elapsed > 0:
            effective_camera_fps = (
                (self._camera_frames_produced - 1) / camera_capture_elapsed
            )

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
            camera_frames_produced=self._camera_frames_produced,
            preview_frames_updated=self._preview_frames_updated,
            camera_capture_elapsed_seconds=camera_capture_elapsed,
            accumulated_pause_seconds=self._accumulated_pause_seconds,
            configured_camera_stream_fps=self._configured_camera_stream_fps,
            effective_camera_stream_fps=effective_camera_fps,
        )

    # --- lifecycle --------------------------------------------------------- #

    def run(self) -> None:
        """Capture loop (Spec 023). Reads every physical frame, updates the
        preview for every successful frame, and writes ONLY sampler-selected
        frames. Always cleans up in ``finally``.

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
                # it is not an FPS throttle. Pause timing is only MEASURED here
                # (no behavior change): if at least one frame has been produced,
                # record when the pause starts and, when it ends, hold its
                # duration as pending (consolidated on the next successful frame).
                paused = self.pause_event.is_set() or self.thermal_pause_event.is_set()
                if paused and self._first_camera_frame_time is not None \
                        and self._pause_started_at is None:
                    self._pause_started_at = time.monotonic()
                while (
                    (self.pause_event.is_set() or self.thermal_pause_event.is_set())
                    and not self.abort_event.is_set()
                    and not self.finalize_event.is_set()
                ):
                    time.sleep(0.1)
                if self._pause_started_at is not None and not (
                    self.pause_event.is_set() or self.thermal_pause_event.is_set()
                ):
                    self._pending_pause_duration += (
                        time.monotonic() - self._pause_started_at
                    )
                    self._pause_started_at = None

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

                # --- Successful physical frame (Spec 023) ---
                now = time.monotonic()
                self._camera_frames_produced += 1
                if self._first_camera_frame_time is None:
                    self._first_camera_frame_time = now
                else:
                    # Consolidate any pause that ended before this frame; it lies
                    # inside the [first_frame, this_frame] capture window.
                    if self._pending_pause_duration:
                        self._accumulated_pause_seconds += self._pending_pause_duration
                        self._pending_pause_duration = 0.0
                self._last_camera_frame_time = now

                # Update the preview ALWAYS and independently of recording, so a
                # frame not selected for recording is still shown live.
                self._set_last_frame(frame)

                # Decide whether this real frame is persisted (recording cadence).
                if not self._recording_sampler.should_write():
                    continue

                # Open the recorder lazily from the FIRST ACCEPTED frame's size.
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

                # Write the selected frame exactly once.
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
        """Store a thread-safe copy of the last frame for preview and bump the
        sequence. Called for EVERY successful frame, independently of recording
        (Spec 023). Returns after incrementing ``_preview_sequence`` under lock.
        """
        copy = frame.copy()
        with self._last_frame_lock:
            self._last_frame = copy
            self._preview_sequence += 1
        self._preview_frames_updated += 1

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
