"""Camera availability and preview service.

Delegates camera operations to the FrameSource interface. A factory function
auto-detects the correct backend (picamera2 on RPi, OpenCV on dev PCs).
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
    ERROR = "error"


@dataclass
class CameraCheckResult:
    status: CameraStatus
    reason: Optional[str] = None


class CameraService:
    """Camera availability check and single-frame JPEG capture.

    Delegates all camera access to a FrameSource implementation.
    If no FrameSource is provided, the factory auto-detects the best
    available backend (picamera2 → OpenCV → None).
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
        """Check if camera is physically connected and accessible."""
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
        """Capture a single JPEG frame for preview purposes.

        Uses capture_single_frame() if available (RPi) to avoid leaving
        camera resources retained. Falls back to read() for OpenCV sources.

        Returns JPEG bytes on success, None on failure.
        """
        if self._frame_source is None:
            return None
        if not CV2_AVAILABLE:
            logger.warning("cv2 not available for JPEG encoding")
            return None
        try:
            # Use single-frame capture if available (avoids resource retention)
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
