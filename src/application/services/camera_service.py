"""Camera availability and preview service.

Delegates camera operations to the FrameSource interface. Uses the global
camera lock to determine availability without invasively opening the hardware.
"""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from src.domain.interfaces.frame_source import FrameSource

logger = logging.getLogger(__name__)

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False


class CameraStatus(str, Enum):
    AVAILABLE = "available"
    NOT_DETECTED = "not_detected"
    BUSY = "busy"
    ERROR = "error"


@dataclass
class CameraCheckResult:
    status: CameraStatus
    reason: Optional[str] = None


class CameraService:
    """Camera availability check and single-frame JPEG capture.

    Uses capture_single_frame() for preview — a self-contained operation
    that acquires the global camera lock, captures one frame, and releases.
    """

    def __init__(self, frame_source: Optional[FrameSource] = None):
        if frame_source is not None:
            self._frame_source: Optional[FrameSource] = frame_source
        else:
            from src.application.services.frame_source_factory import (
                create_frame_source,
            )
            self._frame_source = create_frame_source()

    def check_availability(self) -> CameraCheckResult:
        """Check if camera is available WITHOUT opening hardware.

        Uses is_available() which checks:
        - picamera2 importable (on RPi)
        - global camera lock not held (camera not busy with monitoring)

        This does NOT instantiate Picamera2 or touch libcamera.
        """
        if self._frame_source is None:
            from src.application.services.frame_source_factory import (
                get_unavailability_reason,
            )
            return CameraCheckResult(
                CameraStatus.NOT_DETECTED, get_unavailability_reason()
            )
        try:
            if self._frame_source.is_available():
                return CameraCheckResult(CameraStatus.AVAILABLE)

            # Check if it's busy vs truly unavailable
            from src.infrastructure.camera.raspberry_camera_frame_source import (
                is_camera_locked,
                PICAMERA2_AVAILABLE,
            )
            if PICAMERA2_AVAILABLE and is_camera_locked():
                return CameraCheckResult(
                    CameraStatus.BUSY,
                    "Cámara ocupada por monitoreo activo.",
                )

            from src.application.services.frame_source_factory import (
                get_unavailability_reason,
            )
            return CameraCheckResult(
                CameraStatus.NOT_DETECTED, get_unavailability_reason()
            )
        except Exception as e:
            logger.error(f"Camera check error: {e}")
            return CameraCheckResult(CameraStatus.ERROR, str(e))

    def capture_preview_frame(self) -> Optional[bytes]:
        """Capture a single JPEG frame for preview.

        Uses capture_single_frame() which acquires the global lock internally.
        If the camera is busy (lock held by monitoring), returns None immediately.
        """
        if self._frame_source is None:
            return None
        if not CV2_AVAILABLE:
            logger.warning("cv2 not available for JPEG encoding")
            return None
        try:
            if hasattr(self._frame_source, 'capture_single_frame'):
                ret, frame = self._frame_source.capture_single_frame()
            else:
                ret, frame = self._frame_source.read()
            if not ret or frame is None:
                return None
            _, jpeg_bytes = cv2.imencode(".jpg", frame)
            return jpeg_bytes.tobytes()
        except Exception as e:
            logger.error(f"Camera capture error: {e}")
            return None
