"""Raspberry Pi AI Camera frame source via picamera2.

Provides two usage patterns:
- Persistent mode: for monitoring capture loops (open once, read many, release once).
- Single-frame mode: for preview snapshots (open, capture, close in one call).

IMPORTANT: Picamera2 on the IMX500 requires careful lifecycle management.
After stop()+close(), the underlying libcamera resources need time to fully
release. A settling delay is introduced in release() to prevent "Camera in
Running state" errors on subsequent opens.
"""

import logging
import time
from typing import Any, Optional, Tuple

from src.domain.interfaces.frame_source import FrameSource

logger = logging.getLogger(__name__)

try:
    from picamera2 import Picamera2
    PICAMERA2_AVAILABLE = True
except ImportError:
    PICAMERA2_AVAILABLE = False

# Settling time (seconds) after camera close before allowing reopen.
# The IMX500/libcamera stack needs this to fully release hardware resources.
_CAMERA_SETTLE_SECONDS = 1.0


class RaspberryCameraFrameSource(FrameSource):
    """Frame source for Raspberry Pi AI Camera (IMX500) via picamera2.

    Lifecycle for monitoring (persistent mode):
        source = RaspberryCameraFrameSource()
        # read() auto-starts on first call
        success, frame = source.read()  # opens camera
        success, frame = source.read()  # reuses open camera
        source.release()  # stops + closes + settles

    Lifecycle for preview (single-frame mode):
        source = RaspberryCameraFrameSource()
        success, frame = source.capture_single_frame()  # open-capture-close
    """

    def __init__(self, width: int = 640, height: int = 480, fps: int = 5):
        self._width = width
        self._height = height
        self._fps = fps
        self._camera: Optional[Any] = None
        self._started = False

    def read(self) -> Tuple[bool, Optional[Any]]:
        """Read a frame in persistent mode. Auto-starts camera on first call."""
        if not PICAMERA2_AVAILABLE:
            logger.error("picamera2 not available. Cannot capture frames.")
            return (False, None)

        try:
            if not self._started:
                self._start_camera()
            frame = self._camera.capture_array()
            import cv2
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            return (True, frame_bgr)
        except Exception as e:
            logger.error(f"Camera frame capture failed: {e}")
            # If read fails after camera was started, attempt immediate cleanup
            self._force_cleanup()
            return (False, None)

    def capture_single_frame(self) -> Tuple[bool, Optional[Any]]:
        """Capture one frame and immediately release all camera resources.

        This is the ONLY method that should be used for preview/status checks.
        It guarantees no persistent camera state is left behind.
        """
        if not PICAMERA2_AVAILABLE:
            return (False, None)

        cam = None
        try:
            cam = Picamera2()
            config = cam.create_still_configuration(
                main={"size": (self._width, self._height)}
            )
            cam.configure(config)
            cam.start()
            # Allow sensor to stabilize (auto-exposure, white balance)
            time.sleep(0.4)
            frame = cam.capture_array()
            cam.stop()
            cam.close()
            # Settle time for libcamera to fully release
            time.sleep(_CAMERA_SETTLE_SECONDS)
            cam = None

            import cv2
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            return (True, frame_bgr)
        except Exception as e:
            logger.error(f"Single frame capture failed: {e}")
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

    def release(self) -> None:
        """Release persistent camera resources with proper settling.

        After this call, the camera hardware is guaranteed to be fully
        available for a new open (after the settle period completes).
        """
        if self._camera is not None:
            logger.info("RaspberryCameraFrameSource: stopping camera...")
            try:
                self._camera.stop()
                logger.info("RaspberryCameraFrameSource: camera stopped.")
            except Exception as e:
                logger.warning(f"Error stopping camera: {e}")

            logger.info("RaspberryCameraFrameSource: closing camera...")
            try:
                self._camera.close()
                logger.info("RaspberryCameraFrameSource: camera closed.")
            except Exception as e:
                logger.warning(f"Error closing camera: {e}")

            self._camera = None
            self._started = False

            # Critical: allow libcamera/IMX500 hardware to fully release.
            # Without this, immediate reopen causes "Camera in Running state"
            # or "allocator" errors.
            logger.info(
                f"RaspberryCameraFrameSource: settling {_CAMERA_SETTLE_SECONDS}s "
                f"for hardware release..."
            )
            time.sleep(_CAMERA_SETTLE_SECONDS)
            logger.info("RaspberryCameraFrameSource: camera fully released.")
        else:
            self._started = False

    def is_available(self) -> bool:
        """Check if picamera2 is importable and camera hardware is present.

        NOTE: This does NOT open the camera. It only checks if the picamera2
        library can instantiate and detect the sensor. This avoids the
        open/close cycle that causes state conflicts.
        """
        if not PICAMERA2_AVAILABLE:
            return False
        try:
            # Picamera2() constructor alone detects hardware without starting it.
            # close() releases the detection resources immediately.
            cam = Picamera2()
            cam.close()
            # Brief settle after even a detection-only open
            time.sleep(0.2)
            return True
        except Exception as e:
            logger.debug(f"Camera not available: {e}")
            return False

    def _start_camera(self) -> None:
        """Initialize and start camera for persistent/monitoring use."""
        logger.info("RaspberryCameraFrameSource: starting camera (persistent mode)...")
        self._camera = Picamera2()
        config = self._camera.create_still_configuration(
            main={"size": (self._width, self._height)}
        )
        self._camera.configure(config)
        self._camera.start()
        self._started = True
        # Allow initial stabilization
        time.sleep(0.3)
        logger.info("RaspberryCameraFrameSource: camera started successfully.")

    def _force_cleanup(self) -> None:
        """Emergency cleanup when read() fails on a started camera."""
        if self._camera is not None:
            logger.warning("RaspberryCameraFrameSource: force cleanup after read failure.")
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
