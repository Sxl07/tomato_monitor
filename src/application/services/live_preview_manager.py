"""LivePreviewManager — persistent pre-monitoring camera preview (Spec 023).

Single runtime owner of the PHYSICAL preview camera before a monitoring starts.
It composes a ``FrameSource`` (video mode, ~camera_stream_fps) in a daemon thread,
encodes each frame to JPEG once and publishes it to a latest-frame ``PreviewBuffer``
shared by all HTTP stream consumers. Multi-subscriber with a single physical owner;
when the last subscriber leaves, an idle timeout stops capture and releases the
camera.

Boundary: application layer. It MUST NOT import cv2, picamera2,
RaspberryCameraFrameSource or the module-global ``_camera_lock``. It encodes via
``CameraService.encode_frame_jpeg`` (which delegates to the infrastructure encoder)
and acquires the camera only through the injected ``frame_source_factory``. It does
not touch FastAPI and creates no endpoints.

Concurrency rules (see methods):
    - Two state axes: ``_capture_state`` (IDLE/STARTING/RUNNING/STOPPING/ERROR)
      and ``_subscriptions_suspended`` (blocks new subscribers, survives stop()).
    - Exactly one physical capture session per IDLE->STARTING->RUNNING transition,
      and exactly one ``PreviewBuffer.reset()`` (new generation) per session.
    - The manager lock is held ONLY for short state reads/writes. It is NEVER held
      across thread.join(), frame_source.read()/release(), JPEG encode,
      Condition.wait() or time.sleep().
    - ``frame_source.release()`` happens exactly once and always OUTSIDE the lock,
      arbitrated by ``_claim_source_for_release()``.
"""

from __future__ import annotations

import logging
import threading
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Stable module-level terminal sentinel returned by PreviewBuffer.wait_for_new.
STREAM_STOPPED = object()


class CaptureState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class PreviewBuffer:
    """Thread-safe latest-frame buffer (one JPEG) with a session generation.

    The generation is bumped on ``reset()`` (a new physical capture session). A
    consumer bound to generation N can NEVER receive a JPEG from generation N+1,
    even if ``stop()`` -> ``reset()`` happens before it re-acquires the lock.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._generation = 0
        self._sequence = 0
        self._jpeg: Optional[bytes] = None
        self._stopped = False

    def current_generation(self) -> int:
        with self._condition:
            return self._generation

    def reset(self) -> int:
        """Start a new session: bump generation, clear frame/sequence, leave
        stopped. Returns the new generation. Old consumers see a mismatch."""
        with self._condition:
            self._generation += 1
            self._sequence = 0
            self._jpeg = None
            self._stopped = False
            self._condition.notify_all()
            return self._generation

    def publish(self, jpeg: bytes) -> None:
        with self._condition:
            self._jpeg = jpeg
            self._sequence += 1
            self._condition.notify_all()

    def stop(self) -> None:
        """Mark terminal and wake all waiters (they receive STREAM_STOPPED)."""
        with self._condition:
            self._stopped = True
            self._condition.notify_all()

    def latest(self):
        with self._condition:
            if self._jpeg is None:
                return None
            return (self._sequence, self._jpeg)

    def wait_for_new(self, expected_generation, after_sequence, timeout):
        """Wait (with a monotonic deadline) for a newer JPEG in ``expected_generation``.

        Returns ``(sequence, jpeg)``; ``STREAM_STOPPED`` if stopped or the
        generation changed; or ``None`` on timeout. Robust to spurious wakeups
        via a re-checking loop bounded by an absolute deadline.
        """
        import time as _time

        deadline = _time.monotonic() + max(0.0, timeout)
        with self._condition:
            while True:
                if self._stopped or self._generation != expected_generation:
                    return STREAM_STOPPED
                if self._sequence > after_sequence and self._jpeg is not None:
                    return (self._sequence, self._jpeg)
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)


class LivePreviewManager:
    """Single-owner persistent pre-monitoring preview runtime."""

    def __init__(
        self,
        frame_source_factory: Callable[..., object],
        runtime_registry,
        camera_is_locked: Callable[[], bool],
        *,
        camera_stream_fps: float,
        camera_wh,
        idle_timeout_s: float = 2.0,
        clock: Callable[[], float] = None,  # type: ignore[assignment]
        camera_lock_timeout_seconds: float = 1.0,
    ) -> None:
        import time as _time

        self._frame_source_factory = frame_source_factory
        self._runtime_registry = runtime_registry
        self._camera_is_locked = camera_is_locked
        self._camera_stream_fps = float(camera_stream_fps)
        self._camera_wh = (int(camera_wh[0]), int(camera_wh[1]))
        self._idle_timeout_s = float(idle_timeout_s)
        self._clock = clock if clock is not None else _time.monotonic
        self._camera_lock_timeout_seconds = float(camera_lock_timeout_seconds)

        self._lock = threading.RLock()
        # State cond lets a concurrent subscriber wait for STARTING to resolve.
        self._state_cond = threading.Condition(self._lock)
        self._capture_state = CaptureState.IDLE
        self._subscriptions_suspended = False

        self._buffer = PreviewBuffer()
        self._frame_source = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._released = False
        self._generation = 0

        # Subscribers.
        self._subscribers: set[int] = set()
        self._next_token = 1
        self._last_active: float = self._clock()

        # Per-session metrics (reset on each physical session start).
        self._camera_frames_produced = 0
        self._preview_frames_encoded = 0
        self._first_frame_time: Optional[float] = None
        self._last_frame_time: Optional[float] = None

    # --- public API -------------------------------------------------------- #

    def is_running(self) -> bool:
        with self._lock:
            return self._capture_state == CaptureState.RUNNING

    def subscribe(self):
        """Return ``(token, generation)`` or ``None`` if a subscription is not
        allowed. Starts a physical session if IDLE and the device is free."""
        with self._lock:
            if self._subscriptions_suspended:
                return None
            if self._capture_state == CaptureState.RUNNING:
                token = self._add_subscriber_locked()
                return (token, self._generation)
            if self._capture_state == CaptureState.STARTING:
                # Another subscriber owns startup; wait for it to resolve.
                return self._wait_for_startup_locked()
            if self._capture_state == CaptureState.ERROR:
                return None
            if self._capture_state == CaptureState.STOPPING:
                return None
            # IDLE: this subscriber becomes the startup owner if the device is
            # free. Do NOT start the camera while holding the lock.
            if not self._can_enable_preview_locked():
                return None
            self._capture_state = CaptureState.STARTING
            token = self._add_subscriber_locked()
        # Perform startup OUTSIDE the lock.
        ok = self._start_session()
        with self._lock:
            if ok and self._capture_state == CaptureState.RUNNING:
                return (token, self._generation)
            # Startup failed or was cancelled; drop this token.
            self._subscribers.discard(token)
            return None

    def unsubscribe(self, token) -> None:
        with self._lock:
            self._subscribers.discard(token)
            if not self._subscribers:
                self._last_active = self._clock()

    def get_latest_preview(self):
        return self._buffer.latest()

    def wait_for_preview(self, expected_generation, after_sequence, timeout):
        return self._buffer.wait_for_new(expected_generation, after_sequence, timeout)

    def suspend_for_handoff(self, *, timeout: float = 5.0) -> bool:
        """Block new subscribers, wake current consumers, stop capture. Returns
        True only if the camera was released. The suspension survives the stop."""
        with self._lock:
            self._subscriptions_suspended = True
        self._buffer.stop()
        return self._stop_capture(timeout=timeout)

    def resume_after_failed_handoff(self) -> None:
        """Re-enable future subscriptions ONLY if the device is free. Does NOT
        reset the buffer, create a generation or start the camera."""
        self._reenable_subscriptions_if_safe()

    def enable_preview(self) -> None:
        """Re-enable future subscriptions when returning to the setup screen ONLY
        if the device is free. Does NOT reset the buffer or start the camera."""
        self._reenable_subscriptions_if_safe()

    def stop(self, *, timeout: float = 5.0) -> bool:
        """Stop capture for an external caller (idempotent). Does NOT touch
        ``_subscriptions_suspended``."""
        self._buffer.stop()
        return self._stop_capture(timeout=timeout)

    def diagnostics(self) -> dict:
        with self._lock:
            first = self._first_frame_time
            last = self._last_frame_time
            produced = self._camera_frames_produced
            encoded = self._preview_frames_encoded
            state = self._capture_state.value
            suspended = self._subscriptions_suspended
            subs = len(self._subscribers)
            generation = self._generation
        elapsed = 0.0
        if first is not None and last is not None:
            elapsed = max(0.0, last - first)
        eff = (produced - 1) / elapsed if produced >= 2 and elapsed > 0 else 0.0
        return {
            "camera_frames_produced": produced,
            "preview_frames_encoded": encoded,
            "active_subscribers": subs,
            "camera_capture_elapsed_seconds": elapsed,
            "effective_camera_stream_fps": eff,
            "capture_state": state,
            "subscriptions_suspended": suspended,
            "generation": generation,
        }

    # --- internal helpers (locked) ---------------------------------------- #

    def _add_subscriber_locked(self) -> int:
        token = self._next_token
        self._next_token += 1
        self._subscribers.add(token)
        return token

    def _can_enable_preview_locked(self) -> bool:
        # has_active_capture / is_global_analysis_active come from Spec 020's
        # registry; camera_is_locked is the final physical guard.
        try:
            if self._runtime_registry.has_active_capture():
                return False
            if self._runtime_registry.is_global_analysis_active():
                return False
        except Exception:  # defensive: a broken registry must not open the camera
            return False
        if self._camera_is_locked():
            return False
        return True

    def _wait_for_startup_locked(self):
        # Called with the lock held; wait (bounded by a total monotonic deadline)
        # until STARTING resolves. A second subscriber timing out NEVER cancels
        # the startup owner and NEVER creates another camera; it just returns None.
        import time as _time

        deadline = _time.monotonic() + 5.0
        while self._capture_state == CaptureState.STARTING:
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                return None
            self._state_cond.wait(timeout=remaining)
        if self._capture_state == CaptureState.RUNNING:
            token = self._add_subscriber_locked()
            return (token, self._generation)
        return None

    def _reenable_subscriptions_if_safe(self) -> None:
        with self._lock:
            if self._capture_state in (CaptureState.STARTING, CaptureState.RUNNING):
                # Already usable; just allow subscriptions.
                self._subscriptions_suspended = False
                return
            # For IDLE/ERROR, only re-enable when the device is verifiably free
            # and no capture thread is still alive.
            thread_alive = self._thread is not None and self._thread.is_alive()
            if thread_alive:
                return
            if not self._can_enable_preview_locked():
                return
            if self._capture_state == CaptureState.ERROR:
                self._capture_state = CaptureState.IDLE
            self._subscriptions_suspended = False

    # --- startup / capture loop ------------------------------------------- #

    def _start_session(self) -> bool:
        """Create the frame source and start the capture thread. Returns True on
        success. The physical acquisition (factory) runs OUTSIDE the manager lock;
        the small startup commit (revalidate + assign + thread.start) runs INSIDE
        the lock so a concurrent stop()/suspend cannot be lost. Exactly one
        PreviewBuffer.reset() per session."""
        # Preflight (A.1): revalidate under the lock and clear stop_event HERE,
        # before any window in which stop()/suspend() could set it. After this
        # block, any stop_event.set() is authoritative and is NEVER cleared again
        # during this startup.
        with self._lock:
            if self._capture_state != CaptureState.STARTING:
                return False
            if self._subscriptions_suspended or not self._can_enable_preview_locked():
                self._capture_state = CaptureState.IDLE
                self._state_cond.notify_all()
                return False
            self._stop_event.clear()
            self._released = False
            # New physical session: reset buffer (new generation) + metrics.
            generation = self._buffer.reset()
            self._camera_frames_produced = 0
            self._preview_frames_encoded = 0
            self._first_frame_time = None
            self._last_frame_time = None

        # Physical acquisition OUTSIDE the lock.
        try:
            frame_source = self._frame_source_factory(
                width=self._camera_wh[0],
                height=self._camera_wh[1],
                fps=self._camera_stream_fps,
                camera_mode="video",
                camera_lock_timeout_seconds=self._camera_lock_timeout_seconds,
            )
        except Exception as exc:
            logger.error("LivePreviewManager: frame source creation failed: %s", exc)
            frame_source = None

        if frame_source is None:
            with self._lock:
                self._capture_state = CaptureState.IDLE
                self._state_cond.notify_all()
            self._buffer.stop()
            return False

        # Build the Thread object (does not run yet).
        thread = threading.Thread(
            target=self._capture_loop,
            args=(generation,),
            daemon=True,
            name="live-preview-capture",
        )

        # Startup commit (A.2/A.3): re-enter the lock and revalidate that the
        # startup is STILL valid before assigning/starting. If a stop/suspend
        # arrived meanwhile, do NOT publish RUNNING.
        cancelled = False
        start_failed = False
        with self._lock:
            if (
                self._capture_state != CaptureState.STARTING
                or self._subscriptions_suspended
                or self._stop_event.is_set()
            ):
                cancelled = True
            else:
                self._frame_source = frame_source
                self._thread = thread
                self._generation = generation
                try:
                    thread.start()  # brief; the loop blocks on the lock if needed
                    self._capture_state = CaptureState.RUNNING
                except Exception as exc:  # A.4: thread.start() failure
                    logger.error("LivePreviewManager: thread.start() failed: %s", exc)
                    start_failed = True
                    self._thread = None
                    self._frame_source = None
                    self._capture_state = CaptureState.ERROR
                    # Claim the release here, under the lock, so no other path
                    # can also release the source. The physical release() below
                    # runs OUTSIDE the lock.
                    self._released = True
                self._state_cond.notify_all()

        if cancelled:
            # A stop/suspend won the race; the source we created is not owned by
            # the manager (never assigned), so release it directly, once.
            self._buffer.stop()
            try:
                frame_source.release()
            except Exception:
                pass
            return False

        if start_failed:
            # thread.start() failed: _released was already set under the lock so
            # nobody else can release the source. Do the physical release here,
            # OUTSIDE the lock, exactly once. Do not touch _released again.
            self._buffer.stop()
            try:
                frame_source.release()
            except Exception:
                pass
            return False

        return True

    def _capture_loop(self, generation: int) -> None:
        """Daemon capture loop. Never calls stop()/_stop_capture (no self-join).
        Releases the source exactly once in ``finally`` via the claim helper."""
        from src.application.services.camera_service import CameraService

        frame_source = self._frame_source
        try:
            while not self._stop_event.is_set():
                # Idle-stop: no subscribers for longer than the timeout.
                with self._lock:
                    if not self._subscribers:
                        idle = self._clock() - self._last_active
                        if idle >= self._idle_timeout_s:
                            break

                try:
                    success, frame = frame_source.read()
                except Exception as exc:
                    if not self._stop_event.is_set():
                        logger.error("LivePreviewManager: read() raised: %s", exc)
                        self._set_state(CaptureState.ERROR)
                    break

                if not success or frame is None:
                    if not self._stop_event.is_set():
                        logger.warning("LivePreviewManager: frame source unavailable.")
                        self._set_state(CaptureState.ERROR)
                    break

                now = self._clock()
                self._camera_frames_produced += 1
                if self._first_frame_time is None:
                    self._first_frame_time = now
                self._last_frame_time = now

                try:
                    jpeg = CameraService.encode_frame_jpeg(frame)
                except Exception as exc:
                    logger.warning("LivePreviewManager: JPEG encode failed: %s", exc)
                    jpeg = None
                if jpeg is not None:
                    self._preview_frames_encoded += 1
                    self._buffer.publish(jpeg)
        finally:
            # Idle-stop / error / external stop all converge here.
            self._buffer.stop()
            src = self._claim_source_for_release()
            if src is not None:
                try:
                    src.release()
                except Exception as exc:
                    logger.warning("LivePreviewManager: release() failed: %s", exc)
            with self._lock:
                if self._capture_state not in (CaptureState.ERROR,):
                    self._capture_state = CaptureState.IDLE
                self._state_cond.notify_all()

    # --- stop / release ---------------------------------------------------- #

    def _set_state(self, state: CaptureState) -> None:
        with self._lock:
            self._capture_state = state
            self._state_cond.notify_all()

    def _claim_source_for_release(self):
        """Return the frame source to release exactly once, or None. Under lock."""
        with self._lock:
            if self._released or self._frame_source is None:
                return None
            self._released = True
            src = self._frame_source
            self._frame_source = None
            return src

    def _safe_release(self, frame_source_override=None) -> None:
        if frame_source_override is not None:
            try:
                frame_source_override.release()
            except Exception:
                pass
            return
        src = self._claim_source_for_release()
        if src is not None:
            try:
                src.release()
            except Exception:
                pass

    def _stop_capture(self, *, timeout: float = 5.0) -> bool:
        """Stop the capture thread within a bounded time. Returns True if the
        thread terminated (camera released). If it did not, state=ERROR and the
        camera is NOT force-released (the thread may still be inside read())."""
        with self._lock:
            thread = self._thread
            if self._capture_state in (CaptureState.RUNNING, CaptureState.STARTING):
                self._capture_state = CaptureState.STOPPING
            self._stop_event.set()

        if thread is None or not thread.is_alive():
            # No live thread: ensure any lingering source is released once.
            src = self._claim_source_for_release()
            if src is not None:
                try:
                    src.release()
                except Exception:
                    pass
            with self._lock:
                if self._capture_state != CaptureState.ERROR:
                    self._capture_state = CaptureState.IDLE
            return True

        thread.join(timeout)
        if thread.is_alive():
            with self._lock:
                self._capture_state = CaptureState.ERROR
            return False

        # Thread finished; its finally released the source. Settle final state.
        with self._lock:
            if self._capture_state != CaptureState.ERROR:
                self._capture_state = CaptureState.IDLE
            self._thread = None
        return True
