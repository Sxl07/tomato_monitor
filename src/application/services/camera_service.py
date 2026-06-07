"""Camera availability and preview service."""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False


class CameraStatus(str, Enum):
    AVAILABLE = "available"
    NOT_DETECTED = "not_detected"
    ERROR = "error"


@dataclass
class CameraCheckResult:
    status: CameraStatus
    reason: Optional[str] = None


class CameraService:
    """Camera availability check and single-frame JPEG capture.

    Does NOT maintain a persistent connection. Each operation opens,
    acts, and releases the camera resource.
    """

    def __init__(self, camera_device_index: int = 0, timeout_seconds: float = 5.0):
        self._device_index = camera_device_index
        self._timeout = timeout_seconds

    def check_availability(self) -> CameraCheckResult:
        """Check if camera is physically connected and accessible."""
        if not CV2_AVAILABLE:
            return CameraCheckResult(CameraStatus.NOT_DETECTED, "OpenCV not available")
        try:
            cap = cv2.VideoCapture(self._device_index)
            if not cap.isOpened():
                return CameraCheckResult(CameraStatus.NOT_DETECTED)
            ret, _ = cap.read()
            cap.release()
            if ret:
                return CameraCheckResult(CameraStatus.AVAILABLE)
            return CameraCheckResult(CameraStatus.NOT_DETECTED, "Could not read frame")
        except Exception as e:
            logger.error(f"Camera check error: {e}")
            return CameraCheckResult(CameraStatus.ERROR, str(e))

    def capture_preview_frame(self) -> Optional[bytes]:
        """Capture a single JPEG frame and release the camera immediately.

        Returns JPEG bytes on success, None on failure.
        """
        if not CV2_AVAILABLE:
            return None
        try:
            cap = cv2.VideoCapture(self._device_index)
            if not cap.isOpened():
                return None
            ret, frame = cap.read()
            cap.release()
            if not ret or frame is None:
                return None
            _, jpeg_bytes = cv2.imencode(".jpg", frame)
            return jpeg_bytes.tobytes()
        except Exception as e:
            logger.error(f"Camera capture error: {e}")
            return None
