"""Bug condition exploration test for CameraService on RPi with AI Camera (IMX500).

**Validates: Requirements 1.1, 1.2, 2.1, 2.2**

Bug Condition: On Raspberry Pi with the AI Camera (IMX500), picamera2 is available
and can access the camera, but cv2.VideoCapture(0) cannot open the device because
the IMX500 is NOT a V4L2 device.

Expected Behavior (what the fix should achieve):
- CameraService.check_availability() returns CameraStatus.AVAILABLE
- CameraService.capture_preview_frame() returns valid JPEG bytes

After fix: CameraService delegates to FrameSource. When a picamera2-based
FrameSource is injected (or auto-detected via factory), the AI Camera works.

Testing framework: pytest + hypothesis (property-based tests)
"""

import sys
from unittest.mock import MagicMock, patch
from typing import Tuple, Optional, Any

import numpy as np
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from src.application.services.camera_service import CameraService, CameraStatus
from src.domain.interfaces.frame_source import FrameSource


# --- Strategies for generating bug-condition inputs ---

@st.composite
def bug_condition_inputs(draw):
    """Generate inputs where isBugCondition is True.

    The bug condition is: RPi + IMX500 AI Camera + picamera2 available
    + cv2.VideoCapture cannot open the device.

    We generate random frame dimensions that picamera2 would return,
    simulating various valid camera outputs.
    """
    # Random frame dimensions that picamera2 would produce
    width = draw(st.integers(min_value=320, max_value=1920))
    height = draw(st.integers(min_value=240, max_value=1080))
    # Random RGB frame data (picamera2 returns RGB by default)
    frame = draw(
        st.just(np.random.randint(0, 256, (height, width, 3), dtype=np.uint8))
    )
    return {
        "platform": "raspberry_pi",
        "camera_hardware": "imx500_ai_camera",
        "picamera2_available": True,
        "cv2_videocapture_opens": False,
        "frame_width": width,
        "frame_height": height,
        "frame": frame,
    }


def _create_mock_frame_source(available: bool, frame: Optional[np.ndarray] = None) -> FrameSource:
    """Create a mock FrameSource simulating picamera2 on RPi with AI Camera."""
    mock_source = MagicMock(spec=FrameSource)
    mock_source.is_available.return_value = available
    if available and frame is not None:
        mock_source.read.return_value = (True, frame)
    else:
        mock_source.read.return_value = (False, None)
    mock_source.release.return_value = None
    return mock_source


class TestCameraServiceBugCondition:
    """Property-based tests confirming the bug condition is fixed.

    These tests encode the EXPECTED behavior: when a picamera2-based FrameSource
    is available (simulating RPi + AI Camera), CameraService returns AVAILABLE
    and captures valid JPEG bytes.

    **Validates: Requirements 1.1, 1.2, 2.1, 2.2**
    """

    @given(inputs=bug_condition_inputs())
    @settings(
        max_examples=10,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_check_availability_returns_available_when_picamera2_works(self, inputs):
        """Property: When picamera2 is available and can detect the camera,
        CameraService.check_availability() SHOULD return AVAILABLE.

        **Validates: Requirements 1.1, 2.1**

        Bug: Original code used cv2.VideoCapture(0) which returned NOT_DETECTED
        because IMX500 is not a V4L2 device.
        Fix: CameraService now delegates to FrameSource (picamera2 backend).
        """
        # Simulate RPi + AI Camera: FrameSource (picamera2) is available
        mock_source = _create_mock_frame_source(available=True, frame=inputs["frame"])

        # Inject the picamera2-based FrameSource directly
        service = CameraService(frame_source=mock_source)
        result = service.check_availability()

        # EXPECTED BEHAVIOR: status should be AVAILABLE
        # because picamera2 can access the camera via FrameSource
        assert result.status == CameraStatus.AVAILABLE, (
            f"CameraService returned status='{result.status}' "
            f"(reason: '{result.reason}') when FrameSource (picamera2) is available. "
            f"Expected: status='available'."
        )

    @given(inputs=bug_condition_inputs())
    @settings(
        max_examples=10,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_capture_preview_frame_returns_bytes_when_picamera2_works(self, inputs):
        """Property: When picamera2 is available and can capture frames,
        CameraService.capture_preview_frame() SHOULD return valid JPEG bytes.

        **Validates: Requirements 1.2, 2.2**

        Bug: Original code used cv2.VideoCapture(0) which returned None
        because IMX500 is not a V4L2 device.
        Fix: CameraService now delegates to FrameSource.read() and encodes via cv2.imencode.
        """
        # Simulate RPi + AI Camera: FrameSource (picamera2) returns a valid frame
        mock_source = _create_mock_frame_source(available=True, frame=inputs["frame"])

        service = CameraService(frame_source=mock_source)
        result = service.capture_preview_frame()

        # EXPECTED BEHAVIOR: should return JPEG bytes
        # because FrameSource (picamera2) can capture frames
        assert result is not None, (
            f"CameraService.capture_preview_frame() returned None "
            f"when FrameSource (picamera2) can capture frames. "
            f"Expected: valid JPEG bytes."
        )
        assert isinstance(result, bytes), (
            f"Expected bytes, got {type(result)}"
        )
        # Valid JPEG starts with FFD8
        assert result[:2] == b'\xff\xd8', (
            f"Expected JPEG header FFD8, got {result[:2].hex()}"
        )
