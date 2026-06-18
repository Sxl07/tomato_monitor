"""Factory function for auto-detecting the correct FrameSource backend.

Tries picamera2-based capture first (for Raspberry Pi with AI Camera),
falls back to OpenCV VideoCapture (for development PCs with USB webcam),
and returns None if no camera backend is available.
"""

import logging
from typing import Optional

from src.domain.interfaces.frame_source import FrameSource

logger = logging.getLogger(__name__)


def create_frame_source() -> Optional[FrameSource]:
    """Auto-detect and return the best available FrameSource backend.

    Resolution order:
        1. RaspberryCameraFrameSource (picamera2) — preferred on RPi with AI Camera
        2. OpenCvFrameSource (cv2.VideoCapture) — fallback for USB webcams on dev PCs
        3. None — no camera backend is available

    Returns:
        A ready-to-use FrameSource instance, or None if no camera is accessible.
    """
    # Try picamera2 backend first
    try:
        from src.infrastructure.camera.raspberry_camera_frame_source import (
            RaspberryCameraFrameSource,
        )

        source = RaspberryCameraFrameSource()
        if source.is_available():
            logger.info("Camera backend selected: RaspberryCameraFrameSource (picamera2)")
            return source
        logger.debug("picamera2 imported but camera not detected; trying OpenCV fallback.")
    except Exception as e:
        logger.debug(f"picamera2 backend not available: {e}")

    # Try OpenCV fallback
    try:
        from src.infrastructure.camera.opencv_frame_source import OpenCvFrameSource

        source = OpenCvFrameSource()
        if source.is_available():
            logger.info("Camera backend selected: OpenCvFrameSource (cv2.VideoCapture)")
            return source
        logger.debug("OpenCV VideoCapture could not open any device.")
    except Exception as e:
        logger.debug(f"OpenCV backend not available: {e}")

    logger.warning("No camera backend available. Neither picamera2 nor OpenCV could access a device.")
    return None


def get_unavailability_reason() -> str:
    """Return a user-facing reason (in Spanish) when no camera is available.

    Returns:
        A descriptive message suitable for display to the farmer.
    """
    return "No se encontró cámara compatible. Verifica la conexión."
