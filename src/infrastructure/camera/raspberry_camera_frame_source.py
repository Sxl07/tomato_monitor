import logging
from typing import Any, Optional, Tuple
from src.domain.interfaces.frame_source import FrameSource

logger = logging.getLogger(__name__)

try:
    from picamera2 import Picamera2
    PICAMERA2_AVAILABLE = True
except ImportError:
    PICAMERA2_AVAILABLE = False


class RaspberryCameraFrameSource(FrameSource):
    """Frame source that captures from the Raspberry Pi AI Camera via picamera2.

    Supports two modes:
    - Persistent mode (default for monitoring): camera stays open between reads.
      Call open() before read() and release() when done.
    - Snapshot mode (for preview): each read() opens, captures, and closes.
      Use capture_single_frame() for this pattern.

    Gracefully handles the case where picamera2 is not installed (non-RPi environments).
    """

    def __init__(self, width: int = 640, height: int = 480, fps: int = 5):
        self._width = width
        self._height = height
        self._fps = fps
        self._camera: Optional[Any] = None
        self._started = False

    def read(self) -> Tuple[bool, Optional[Any]]:
        """Read a frame. Starts the camera if not already started (persistent mode)."""
        if not PICAMERA2_AVAILABLE:
            logger.error("picamera2 not available. Cannot capture frames.")
            return (False, None)

        try:
            if not self._started:
                self._start_camera()
            frame = self._camera.capture_array()
            # picamera2 returns RGB by default; convert to BGR for OpenCV compatibility
            import cv2
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            return (True, frame_bgr)
        except Exception as e:
            logger.error(f"Camera frame capture failed: {e}")
            return (False, None)

    def capture_single_frame(self) -> Tuple[bool, Optional[Any]]:
        """Capture a single frame and immediately release the camera.

        Use this for preview/snapshot operations that must NOT leave
        camera resources retained. Safe to call repeatedly without
        conflicting with other Picamera2 instances.
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
            # Small delay for camera to stabilize
            import time
            time.sleep(0.3)
            frame = cam.capture_array()
            cam.stop()
            cam.close()
            cam = None

            import cv2
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            return (True, frame_bgr)
        except Exception as e:
            logger.error(f"Single frame capture failed: {e}")
            if cam is not None:
                try:
                    cam.stop()
                    cam.close()
                except Exception:
                    pass
            return (False, None)

    def release(self) -> None:
        """Release persistent camera resources."""
        if self._camera is not None and self._started:
            try:
                self._camera.stop()
            except Exception as e:
                logger.warning(f"Error stopping camera: {e}")
            try:
                self._camera.close()
            except Exception as e:
                logger.warning(f"Error closing camera: {e}")
            self._camera = None
            self._started = False

    def is_available(self) -> bool:
        """Check if picamera2 is available and can detect the camera.

        Does NOT leave resources open — opens and immediately closes.
        """
        if not PICAMERA2_AVAILABLE:
            return False
        try:
            cam = Picamera2()
            cam.close()
            return True
        except Exception:
            return False

    def _start_camera(self) -> None:
        """Initialize and start the camera for persistent/monitoring use."""
        self._camera = Picamera2()
        config = self._camera.create_still_configuration(
            main={"size": (self._width, self._height)}
        )
        self._camera.configure(config)
        self._camera.start()
        self._started = True
