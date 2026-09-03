"""JPEG encoding helper (infrastructure).

Confines the OpenCV JPEG encoding (``cv2.imencode``) to the infrastructure
layer so the application layer (e.g. ``CameraService``) stays free of ``cv2``.
Both preview paths (pre-monitoring single-frame capture and the video-first
recording preview) reuse this single encoder.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import cv2

    CV2_AVAILABLE = True
except ImportError:  # pragma: no cover - environment without OpenCV
    CV2_AVAILABLE = False


def encode_frame_to_jpeg(frame) -> Optional[bytes]:
    """Encode an already-captured BGR frame to JPEG bytes.

    Pure encoding: does NOT touch the camera or acquire any lock. Returns None
    if the frame is missing, OpenCV is unavailable, or encoding fails.

    Args:
        frame: A BGR ``ndarray`` (e.g. a copy from a frame source or the
            recording worker's ``get_last_frame()``).

    Returns:
        JPEG-encoded bytes, or None on any failure.
    """
    if frame is None:
        return None
    if not CV2_AVAILABLE:
        logger.warning("cv2 not available for JPEG encoding")
        return None
    try:
        ok, jpeg = cv2.imencode(".jpg", frame)
        if not ok:
            return None
        return jpeg.tobytes()
    except Exception as e:  # never leak raw cv2 errors
        logger.error(f"JPEG encode error: {e}")
        return None
