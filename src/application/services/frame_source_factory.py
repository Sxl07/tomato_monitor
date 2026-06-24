"""Factory function for selecting the correct FrameSource backend.

On Raspberry Pi (picamera2 importable): returns RaspberryCameraFrameSource.
On dev PC (no picamera2): returns OpenCvFrameSource if a device is accessible.
Otherwise: returns None.

IMPORTANT: This factory does NOT open or probe the camera hardware.
It only checks which libraries are importable and returns the appropriate
implementation. The actual hardware access happens later when read() or
capture_single_frame() is called.
"""

import logging
from typing import Optional

from src.domain.interfaces.frame_source import FrameSource

logger = logging.getLogger(__name__)


def create_frame_source() -> Optional[FrameSource]:
    """Select the best available FrameSource backend.

    Resolution order:
        1. RaspberryCameraFrameSource — if picamera2 is importable (RPi).
           Does NOT open the camera or check hardware state.
        2. OpenCvFrameSource — if picamera2 is NOT importable (dev PC) and
           OpenCV can access a video device.
        3. None — no camera backend available.

    Returns:
        A FrameSource instance (not yet connected to hardware), or None.
    """
    # Check if picamera2 is available (RPi scenario)
    try:
        from src.infrastructure.camera.raspberry_camera_frame_source import (
            RaspberryCameraFrameSource,
            PICAMERA2_AVAILABLE,
        )

        if PICAMERA2_AVAILABLE:
            # On RPi with picamera2 — use it. Do NOT probe hardware here.
            logger.info(
                "Camera backend selected: RaspberryCameraFrameSource (picamera2)"
            )
            return RaspberryCameraFrameSource()
    except Exception as e:
        logger.debug(f"picamera2 backend not available: {e}")

    # Fallback: OpenCV for dev PCs (only if picamera2 is NOT importable)
    try:
        from src.infrastructure.camera.raspberry_camera_frame_source import (
            PICAMERA2_AVAILABLE,
        )
        if PICAMERA2_AVAILABLE:
            # picamera2 IS available but we got here somehow — don't fall back
            logger.error(
                "picamera2 is installed but camera initialization failed. "
                "NOT falling back to OpenCV. Check cable/firmware."
            )
            return None
    except Exception:
        pass

    try:
        from src.infrastructure.camera.opencv_frame_source import OpenCvFrameSource

        source = OpenCvFrameSource()
        if source.is_available():
            logger.info(
                "Camera backend selected: OpenCvFrameSource (cv2.VideoCapture)"
            )
            return source
        logger.debug("OpenCV VideoCapture could not open any device.")
    except Exception as e:
        logger.debug(f"OpenCV backend not available: {e}")

    logger.warning("No camera backend available.")
    return None


def get_unavailability_reason() -> str:
    """Return a user-facing reason (in Spanish) when no camera is available."""
    return "No se encontró cámara compatible. Verifica la conexión."
