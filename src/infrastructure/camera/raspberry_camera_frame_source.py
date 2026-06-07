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

    Gracefully handles the case where picamera2 is not installed (non-RPi environments).
    Configuration: width, height, fps.
    """

    def __init__(self, width: int = 640, height: int = 480, fps: int = 5):
        self._width = width
        self._height = height
        self._fps = fps
        self._camera = None
        self._started = False

    def read(self) -> Tuple[bool, Optional[Any]]:
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

    def release(self) -> None:
        if self._camera is not None and self._started:
            try:
                self._camera.stop()
            except Exception as e:
                logger.warning(f"Error stopping camera: {e}")
            self._camera = None
            self._started = False

    def is_available(self) -> bool:
        if not PICAMERA2_AVAILABLE:
            return False
        try:
            if self._camera is None:
                cam = Picamera2()
                cam.close()
            return True
        except Exception:
            return False

    def _start_camera(self) -> None:
        """Initialize and start the camera with configured resolution."""
        self._camera = Picamera2()
        config = self._camera.create_still_configuration(
            main={"size": (self._width, self._height)}
        )
        self._camera.configure(config)
        self._camera.start()
        self._started = True
