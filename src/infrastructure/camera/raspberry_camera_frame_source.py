"""Raspberry Pi AI Camera frame source via picamera2.

CRITICAL DESIGN: Uses a module-level threading.Lock (_camera_lock) to ensure
only one code path can interact with Picamera2/libcamera at any time.
This prevents "Camera in Running state" and "allocator" errors that occur
when multiple opens/closes overlap on the IMX500 hardware.

Two usage modes:
- Persistent mode (monitoring): acquire lock on first read, hold until release().
- Single-frame mode (preview): acquire lock, open-capture-close, release lock.
"""

import logging
import threading
import time
from typing import Any, Optional, Tuple

from src.domain.interfaces.frame_source import FrameSource

logger = logging.getLogger(__name__)

try:
    from picamera2 import Picamera2
    PICAMERA2_AVAILABLE = True
except ImportError:
    PICAMERA2_AVAILABLE = False

# =============================================================================
# GLOBAL CAMERA LOCK — the single source of truth for camera ownership.
# Any code path that touches Picamera2 must hold this lock.
# =============================================================================
_camera_lock = threading.Lock()

# Settling time after camera hardware release (seconds).
# IMX500/libcamera needs this before a new Picamera2 instance can be created.
_CAMERA_SETTLE_SECONDS = 0.8


def is_camera_locked() -> bool:
    """Check if the camera lock is currently held (non-blocking).

    Use this from external code to determine if camera is busy
    without attempting to acquire it.
    """
    acquired = _camera_lock.acquire(blocking=False)
    if acquired:
        _camera_lock.release()
        return False
    return True


class RaspberryCameraFrameSource(FrameSource):
    """Frame source for Raspberry Pi AI Camera (IMX500) via picamera2.

    All Picamera2 interactions are serialized through _camera_lock.
    This class NEVER creates two Picamera2 instances simultaneously.
    """

    def __init__(self, width: int = 640, height: int = 480, fps: int = 5):
        self._width = width
        self._height = height
        self._fps = fps
        self._camera: Optional[Any] = None
        self._started = False
        self._lock_held = False  # True if we're holding _camera_lock in persistent mode

    def read(self) -> Tuple[bool, Optional[Any]]:
        """Read a frame in persistent mode.

        On first call, acquires the global lock and starts the camera.
        Subsequent calls reuse the open camera without re-acquiring the lock.
        The lock is only released when release() is called.
        """
        if not PICAMERA2_AVAILABLE:
            logger.error("picamera2 not available.")
            return (False, None)

        try:
            if not self._started:
                # Acquire global lock for persistent mode
                logger.info("Worker: acquiring camera lock...")
                acquired = _camera_lock.acquire(timeout=15.0)
                if not acquired:
                    logger.error("Worker: could not acquire camera lock within 15s.")
                    return (False, None)
                self._lock_held = True
                logger.info("Worker: camera lock acquired.")
                try:
                    self._start_camera()
                except Exception as e:
                    logger.error(f"Worker: failed to start camera: {e}")
                    # Clean up any partial Picamera2 instance before releasing lock
                    self._cleanup_partial_camera()
                    _camera_lock.release()
                    self._lock_held = False
                    return (False, None)

            frame = self._camera.capture_array()
            import cv2
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            return (True, frame_bgr)
        except Exception as e:
            logger.error(f"Camera frame capture failed: {e}")
            # Attempt cleanup on failure
            self._force_cleanup()
            return (False, None)

    def capture_single_frame(self) -> Tuple[bool, Optional[Any]]:
        """Capture one frame with exclusive lock, then release everything.

        Used ONLY for preview. Acquires lock, opens camera, captures,
        closes camera, releases lock. Fully self-contained.
        """
        if not PICAMERA2_AVAILABLE:
            return (False, None)

        # Try to acquire lock — if camera is busy (monitoring), fail fast
        acquired = _camera_lock.acquire(timeout=5.0)
        if not acquired:
            logger.warning("Preview: camera lock busy (monitoring active?). Skipping.")
            return (False, None)

        cam = None
        try:
            logger.debug("Preview: opening camera for single frame...")
            cam = Picamera2()
            config = cam.create_still_configuration(
                main={"size": (self._width, self._height)}
            )
            cam.configure(config)
            cam.start()
            time.sleep(0.3)  # Sensor stabilization
            frame = cam.capture_array()
            cam.stop()
            cam.close()
            cam = None
            time.sleep(_CAMERA_SETTLE_SECONDS)
            logger.debug("Preview: camera released successfully.")

            import cv2
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            return (True, frame_bgr)
        except Exception as e:
            logger.error(f"Preview single frame capture failed: {e}")
            if cam is not None:
                try:
                    cam.stop()
                except Exception:
                    pass
                try:
                    cam.close()
                except Exception:
                    pass
                time.sleep(_CAMERA_SETTLE_SECONDS)
            return (False, None)
        finally:
            _camera_lock.release()

    def release(self) -> None:
        """Release persistent camera and the global lock.

        After this returns, the camera hardware is fully available.
        """
        if self._camera is not None:
            logger.info("Worker: releasing camera — stopping...")
            try:
                self._camera.stop()
                logger.info("Worker: camera stopped.")
            except Exception as e:
                logger.warning(f"Worker: error stopping camera: {e}")

            logger.info("Worker: closing camera...")
            try:
                self._camera.close()
                logger.info("Worker: camera closed.")
            except Exception as e:
                logger.warning(f"Worker: error closing camera: {e}")

            self._camera = None
            self._started = False

            # Settle for hardware to fully release
            logger.info(f"Worker: settling {_CAMERA_SETTLE_SECONDS}s...")
            time.sleep(_CAMERA_SETTLE_SECONDS)
            logger.info("Worker: camera hardware fully released.")

        # Release the global lock
        if self._lock_held:
            _camera_lock.release()
            self._lock_held = False
            logger.info("Worker: camera lock released.")
        else:
            self._started = False

    def is_available(self) -> bool:
        """Check camera availability WITHOUT opening Picamera2.

        Returns True if picamera2 is importable and the lock is not held.
        Does NOT instantiate Picamera2 — avoids interfering with active sessions.
        """
        if not PICAMERA2_AVAILABLE:
            return False
        # If the lock is held, camera is busy — report not available for new use
        if is_camera_locked():
            logger.debug("is_available: camera lock is held, reporting busy.")
            return False
        # picamera2 is available and lock is free — camera should be accessible
        return True

    def _start_camera(self) -> None:
        """Initialize and start camera. Caller MUST hold _camera_lock."""
        logger.info("Worker: initializing Picamera2...")
        self._camera = Picamera2()
        config = self._camera.create_still_configuration(
            main={"size": (self._width, self._height)}
        )
        self._camera.configure(config)
        self._camera.start()
        self._started = True
        time.sleep(0.3)  # Initial stabilization
        logger.info("Worker: camera started successfully (persistent mode).")

    def _force_cleanup(self) -> None:
        """Emergency cleanup when read() fails after camera was started."""
        logger.warning("Worker: force cleanup after read failure.")
        self._cleanup_partial_camera()
        if self._lock_held:
            _camera_lock.release()
            self._lock_held = False

    def _cleanup_partial_camera(self) -> None:
        """Clean up any partial or fully-started Picamera2 instance.

        Safe to call even if _camera is None or partially initialized.
        Does NOT release the lock — caller is responsible for lock management.
        """
        if self._camera is not None:
            logger.info("Cleaning up Picamera2 instance...")
            try:
                self._camera.stop()
            except Exception:
                pass
            try:
                self._camera.close()
            except Exception:
                pass
            self._camera = None
            self._started = False
            time.sleep(_CAMERA_SETTLE_SECONDS)
            logger.info("Partial camera cleanup complete.")
        else:
            self._started = False
