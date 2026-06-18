import logging
import time
from typing import Any, Optional, Tuple

import cv2

from src.domain.interfaces.frame_source import FrameSource

logger = logging.getLogger(__name__)


class OpenCvFrameSource(FrameSource):
    """Frame source that captures from a USB webcam via OpenCV VideoCapture.

    Designed as the fallback for development PCs without picamera2.
    Uses lazy initialization: the capture device is opened on first use
    and kept open for subsequent reads to avoid repeated open/close cycles
    that fail on USB passthrough (e.g., WSL with usbipd).
    """

    def __init__(self, device_index: int = 0):
        self._device_index = device_index
        self._cap: Optional[cv2.VideoCapture] = None

    def _ensure_open(self) -> bool:
        """Open the capture device if not already open. Returns True if ready."""
        if self._cap is not None and self._cap.isOpened():
            return True
        try:
            self._cap = cv2.VideoCapture(self._device_index)
            if not self._cap.isOpened():
                logger.warning(
                    f"OpenCV VideoCapture could not open device {self._device_index}."
                )
                self._cap = None
                return False
            # Allow camera to stabilize (USB cameras need warm-up time)
            time.sleep(0.1)
            return True
        except Exception as e:
            logger.error(f"OpenCV VideoCapture open failed: {e}")
            self._cap = None
            return False

    def read(self) -> Tuple[bool, Optional[Any]]:
        """Read one frame from the capture device."""
        try:
            if not self._ensure_open():
                return (False, None)
            ret, frame = self._cap.read()
            if not ret or frame is None:
                logger.warning(
                    f"OpenCV VideoCapture failed to read frame from device {self._device_index}."
                )
                return (False, None)
            return (True, frame)
        except Exception as e:
            logger.error(f"OpenCV frame capture failed: {e}")
            return (False, None)

    def release(self) -> None:
        """Release the capture device."""
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception as e:
                logger.warning(f"Error releasing OpenCV capture: {e}")
            self._cap = None

    def is_available(self) -> bool:
        """Check if the device can be opened via OpenCV VideoCapture."""
        try:
            if not self._ensure_open():
                return False
            return True
        except Exception:
            return False
