"""Preservation property tests for CameraService — MUST PASS after fix.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

These tests encode the PRESERVED behavior of CameraService for non-bug-condition
inputs (i.e., NOT the RPi + IMX500 scenario). They establish a regression
baseline that must continue to pass after the fix is applied.

After fix, CameraService delegates to FrameSource. The preservation tests
verify:
- check_availability() returns AVAILABLE when FrameSource.is_available() is True
- check_availability() returns NOT_DETECTED when no FrameSource is available
- capture_preview_frame() returns JPEG bytes when FrameSource.read() succeeds
- capture_preview_frame() returns None when FrameSource is unavailable
- Response JSON shape from /api/camera/status is always {"status": "...", "reason": "..."}
- For all random numpy frames, JPEG encoding produces valid bytes starting with FFD8

Testing framework: pytest + hypothesis (property-based tests)
"""

import sys
from unittest.mock import MagicMock, patch
from typing import Optional

import numpy as np
import pytest
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from src.application.services.camera_service import (
    CameraService,
    CameraCheckResult,
    CameraStatus,
)
from src.domain.interfaces.frame_source import FrameSource


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

@st.composite
def non_bug_condition_camera_available(draw):
    """Generate inputs simulating a Dev PC with a USB webcam (non-RPi).

    This is the case where the FrameSource is available and returns a frame.
    """
    # Random frame dimensions representing various USB webcam outputs
    width = draw(st.integers(min_value=160, max_value=1920))
    height = draw(st.integers(min_value=120, max_value=1080))
    frame = np.random.randint(0, 256, (height, width, 3), dtype=np.uint8)
    return {
        "frame": frame,
        "width": width,
        "height": height,
    }


@st.composite
def random_numpy_frames(draw):
    """Generate random numpy arrays representing image frames of various shapes.

    Tests JPEG encoding robustness across different input dimensions.
    Constrained to 3-channel uint8 BGR images (what OpenCV expects).
    """
    width = draw(st.integers(min_value=1, max_value=500))
    height = draw(st.integers(min_value=1, max_value=500))
    channels = 3  # BGR for OpenCV
    frame = np.random.randint(0, 256, (height, width, channels), dtype=np.uint8)
    return frame


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _create_mock_frame_source_available(frame: np.ndarray) -> FrameSource:
    """Create a mock FrameSource that is available and returns a frame."""
    mock_source = MagicMock(spec=FrameSource)
    mock_source.is_available.return_value = True
    mock_source.read.return_value = (True, frame)
    mock_source.release.return_value = None
    return mock_source


def _create_mock_frame_source_unavailable() -> FrameSource:
    """Create a mock FrameSource that is NOT available."""
    mock_source = MagicMock(spec=FrameSource)
    mock_source.is_available.return_value = False
    mock_source.read.return_value = (False, None)
    mock_source.release.return_value = None
    return mock_source


def _create_mock_frame_source_read_fails() -> FrameSource:
    """Create a mock FrameSource where is_available() is True but read() fails."""
    mock_source = MagicMock(spec=FrameSource)
    mock_source.is_available.return_value = True
    mock_source.read.return_value = (False, None)
    mock_source.release.return_value = None
    return mock_source


def _create_mock_frame_source_raises(error: Exception) -> FrameSource:
    """Create a mock FrameSource that raises an exception."""
    mock_source = MagicMock(spec=FrameSource)
    mock_source.is_available.side_effect = error
    mock_source.read.side_effect = error
    mock_source.release.return_value = None
    return mock_source


# ---------------------------------------------------------------------------
# Property tests: CameraService.check_availability() contract
# ---------------------------------------------------------------------------

class TestCheckAvailabilityPreservation:
    """Property tests for check_availability() on non-RPi environments.

    **Validates: Requirements 3.1, 3.5**
    """

    @given(inputs=non_bug_condition_camera_available())
    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_returns_available_when_frame_source_available(self, inputs):
        """Property: For all non-bug-condition inputs where FrameSource is available,
        check_availability() returns CameraStatus.AVAILABLE.

        **Validates: Requirements 3.1**

        Preservation: When camera (via FrameSource) is accessible,
        the service returns AVAILABLE — same behavior as before.
        """
        mock_source = _create_mock_frame_source_available(inputs["frame"])

        service = CameraService(frame_source=mock_source)
        result = service.check_availability()

        # Contract: result is always a CameraCheckResult
        assert isinstance(result, CameraCheckResult)
        # Preservation: status is AVAILABLE when camera works
        assert result.status == CameraStatus.AVAILABLE
        # Contract: reason is Optional[str]
        assert result.reason is None or isinstance(result.reason, str)

    def test_returns_not_detected_when_no_frame_source(self):
        """Property: When no FrameSource is found (factory returns None),
        check_availability() returns CameraStatus.NOT_DETECTED with Spanish reason.

        **Validates: Requirements 3.1**

        Preservation: When no camera is available, returns NOT_DETECTED.
        The reason message is now in Spanish per UX guidelines.
        """
        # Patch the factory where it is imported inside CameraService.__init__
        with patch(
            "src.application.services.frame_source_factory.create_frame_source",
            return_value=None,
        ):
            service = CameraService(frame_source=None)

        result = service.check_availability()

        assert isinstance(result, CameraCheckResult)
        assert result.status == CameraStatus.NOT_DETECTED
        assert result.reason is not None
        assert isinstance(result.reason, str)
        # Spanish reason message
        assert "No se encontró cámara compatible" in result.reason

    def test_returns_not_detected_when_frame_source_not_available(self):
        """Property: When FrameSource exists but is_available() returns False,
        check_availability() returns NOT_DETECTED.

        **Validates: Requirements 3.1**

        Preservation: When camera is detected but not working, returns NOT_DETECTED.
        """
        mock_source = _create_mock_frame_source_unavailable()

        service = CameraService(frame_source=mock_source)
        result = service.check_availability()

        assert isinstance(result, CameraCheckResult)
        assert result.status == CameraStatus.NOT_DETECTED
        assert result.reason is not None
        assert isinstance(result.reason, str)

    def test_returns_error_when_exception_raised(self):
        """Property: When an exception occurs during camera check,
        check_availability() returns ERROR with exception message.

        **Validates: Requirements 3.1**

        Preservation: returns ERROR with str(exception) as reason.
        """
        mock_source = _create_mock_frame_source_raises(RuntimeError("Device busy"))

        service = CameraService(frame_source=mock_source)
        result = service.check_availability()

        assert isinstance(result, CameraCheckResult)
        assert result.status == CameraStatus.ERROR
        assert "Device busy" in result.reason

    @given(inputs=non_bug_condition_camera_available())
    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_status_always_valid_enum_value(self, inputs):
        """Property: For all inputs, check_availability().status is always a valid
        CameraStatus enum member.

        **Validates: Requirements 3.5**

        This ensures the public API contract is preserved — status is always
        one of {AVAILABLE, NOT_DETECTED, ERROR}.
        """
        mock_source = _create_mock_frame_source_available(inputs["frame"])

        service = CameraService(frame_source=mock_source)
        result = service.check_availability()

        assert result.status in {
            CameraStatus.AVAILABLE,
            CameraStatus.NOT_DETECTED,
            CameraStatus.ERROR,
        }


# ---------------------------------------------------------------------------
# Property tests: CameraService.capture_preview_frame() contract
# ---------------------------------------------------------------------------

class TestCapturePreviewFramePreservation:
    """Property tests for capture_preview_frame() on non-RPi environments.

    **Validates: Requirements 3.1, 3.5**
    """

    @given(inputs=non_bug_condition_camera_available())
    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_returns_jpeg_bytes_when_frame_source_available(self, inputs):
        """Property: For all non-bug-condition inputs where FrameSource returns a frame,
        capture_preview_frame() returns bytes representing a valid JPEG.

        **Validates: Requirements 3.1**

        Preservation: When camera provides a frame, the service encodes it
        as JPEG and returns bytes starting with FFD8 header.
        """
        mock_source = _create_mock_frame_source_available(inputs["frame"])

        service = CameraService(frame_source=mock_source)
        result = service.capture_preview_frame()

        # Preservation: returns bytes (JPEG) when camera works
        assert result is not None
        assert isinstance(result, bytes)
        # Valid JPEG starts with FFD8
        assert result[:2] == b'\xff\xd8', (
            f"Expected JPEG header FFD8, got {result[:2].hex()}"
        )

    def test_returns_none_when_no_frame_source(self):
        """Property: When no FrameSource is available (None),
        capture_preview_frame() returns None.

        **Validates: Requirements 3.1**

        Preservation: returns None when camera not available.
        """
        service = CameraService(frame_source=MagicMock(spec=FrameSource))
        service._frame_source = None  # Simulate no backend found

        result = service.capture_preview_frame()

        assert result is None

    def test_returns_none_when_read_fails(self):
        """Property: When FrameSource.read() fails, capture_preview_frame() returns None.

        **Validates: Requirements 3.1**
        """
        mock_source = _create_mock_frame_source_read_fails()

        service = CameraService(frame_source=mock_source)
        result = service.capture_preview_frame()

        assert result is None

    def test_returns_none_when_cv2_not_available(self):
        """Property: When CV2_AVAILABLE is False, capture_preview_frame() returns None.

        **Validates: Requirements 3.1**

        cv2 is still needed for imencode (JPEG encoding utility).
        """
        mock_source = _create_mock_frame_source_available(
            np.zeros((100, 100, 3), dtype=np.uint8)
        )

        with patch("src.application.services.camera_service.CV2_AVAILABLE", False):
            service = CameraService(frame_source=mock_source)
            result = service.capture_preview_frame()

        assert result is None

    def test_returns_none_when_exception_raised(self):
        """Property: When an exception occurs, capture_preview_frame() returns None.

        **Validates: Requirements 3.1**

        Preservation: gracefully returns None on exceptions.
        """
        mock_source = _create_mock_frame_source_raises(RuntimeError("Device busy"))

        service = CameraService(frame_source=mock_source)
        result = service.capture_preview_frame()

        assert result is None


# ---------------------------------------------------------------------------
# Property test: JPEG encoding produces valid bytes for all frames
# ---------------------------------------------------------------------------

class TestJpegEncodingPreservation:
    """Property-based test: for all random numpy frames, JPEG encoding
    produces valid bytes starting with FFD8 header.

    **Validates: Requirements 3.5**
    """

    @given(frame=random_numpy_frames())
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_jpeg_encoding_produces_valid_bytes(self, frame):
        """Property: For all random numpy frames (various shapes/dtypes),
        cv2.imencode(".jpg", frame) produces valid bytes starting with FFD8.

        **Validates: Requirements 3.5**

        This verifies the JPEG encoding path in CameraService is robust
        across different frame dimensions — the encoding step is preserved
        regardless of the camera backend used.
        """
        import cv2

        success, jpeg_data = cv2.imencode(".jpg", frame)

        # JPEG encoding should always succeed for valid uint8 BGR frames
        assert success, f"imencode failed for frame shape {frame.shape}"
        jpeg_bytes = jpeg_data.tobytes()

        # Valid JPEG always starts with FFD8
        assert jpeg_bytes[:2] == b'\xff\xd8', (
            f"Invalid JPEG header for frame shape {frame.shape}: "
            f"got {jpeg_bytes[:2].hex()}"
        )
        # Valid JPEG always ends with FFD9
        assert jpeg_bytes[-2:] == b'\xff\xd9', (
            f"Invalid JPEG footer for frame shape {frame.shape}: "
            f"got {jpeg_bytes[-2:].hex()}"
        )
        # Must have non-trivial size
        assert len(jpeg_bytes) > 0


# ---------------------------------------------------------------------------
# Property test: API response JSON shape preservation
# ---------------------------------------------------------------------------

class TestApiResponseShapePreservation:
    """Property tests verifying the JSON response shape from the camera status
    endpoint logic is always {"status": "...", "reason": "..."}.

    **Validates: Requirements 3.2, 3.5**

    We test the response construction logic directly — the same logic that
    the /api/camera/status endpoint uses.
    """

    @given(inputs=non_bug_condition_camera_available())
    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_status_response_shape_when_available(self, inputs):
        """Property: The response dict from camera_status logic always has
        keys 'status' and 'reason' when camera is available.

        **Validates: Requirements 3.2, 3.5**
        """
        mock_source = _create_mock_frame_source_available(inputs["frame"])

        service = CameraService(frame_source=mock_source)
        result = service.check_availability()

        # Simulate what the endpoint does: {"status": result.status.value, "reason": result.reason}
        response = {"status": result.status.value, "reason": result.reason}

        # Shape preservation: always has both keys
        assert "status" in response
        assert "reason" in response
        # status is always a string
        assert isinstance(response["status"], str)
        # status value is a valid CameraStatus value
        assert response["status"] in {"available", "not_detected", "error"}
        # reason is always str or None
        assert response["reason"] is None or isinstance(response["reason"], str)

    def test_status_response_shape_when_unavailable(self):
        """Property: The response dict from camera_status logic always has
        keys 'status' and 'reason' when camera is NOT available.

        **Validates: Requirements 3.2, 3.5**
        """
        mock_source = _create_mock_frame_source_unavailable()

        service = CameraService(frame_source=mock_source)
        result = service.check_availability()

        response = {"status": result.status.value, "reason": result.reason}

        assert "status" in response
        assert "reason" in response
        assert isinstance(response["status"], str)
        assert response["status"] in {"available", "not_detected", "error"}
        assert response["reason"] is None or isinstance(response["reason"], str)

    def test_status_response_shape_on_error(self):
        """Property: The response dict has correct shape even on error condition.

        **Validates: Requirements 3.2, 3.5**
        """
        mock_source = _create_mock_frame_source_raises(OSError("Permission denied"))

        service = CameraService(frame_source=mock_source)
        result = service.check_availability()

        response = {"status": result.status.value, "reason": result.reason}

        assert "status" in response
        assert "reason" in response
        assert response["status"] == "error"
        assert isinstance(response["reason"], str)
        assert "Permission denied" in response["reason"]


# ---------------------------------------------------------------------------
# Property test: Monitoring endpoints are unaffected by CameraService
# ---------------------------------------------------------------------------

class TestMonitoringEndpointsPreservation:
    """Verify that monitoring log and snapshot endpoint logic is independent
    of CameraService. These endpoints should be completely unaffected.

    **Validates: Requirements 3.2, 3.3, 3.4**
    """

    @given(
        monitoring_id=st.integers(min_value=1, max_value=1000),
        num_entries=st.integers(min_value=0, max_value=10),
    )
    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_log_entry_shape_preserved(self, monitoring_id, num_entries):
        """Property: Log entries always produce dicts with keys
        {timestamp, level, source, message} regardless of CameraService state.

        **Validates: Requirements 3.2, 3.3**

        The monitoring log endpoint transforms LogEntry objects into dicts.
        This transformation must be preserved after the camera fix.
        """
        from datetime import datetime
        from src.application.services.log_service import LogService, LogLevel

        log_service = LogService()

        # Add random entries
        for i in range(num_entries):
            log_service.add_entry(
                monitoring_id=monitoring_id,
                level=LogLevel.INFO,
                source="test",
                message=f"Test message {i}",
            )

        entries = log_service.get_entries(monitoring_id)

        # Transform entries the same way the endpoint does
        response = [
            {
                "timestamp": entry.timestamp.isoformat(),
                "level": entry.level.value,
                "source": entry.source,
                "message": entry.message,
            }
            for entry in entries
        ]

        assert len(response) == num_entries
        for item in response:
            # Shape: always has these 4 keys
            assert set(item.keys()) == {"timestamp", "level", "source", "message"}
            assert isinstance(item["timestamp"], str)
            assert isinstance(item["level"], str)
            assert item["level"] in {"info", "success", "warning", "error"}
            assert isinstance(item["source"], str)
            assert isinstance(item["message"], str)

    def test_log_service_independent_of_camera_service(self):
        """Property: LogService operates entirely independently of CameraService.

        **Validates: Requirements 3.3**

        Modifying CameraService should not affect LogService behavior at all.
        """
        from src.application.services.log_service import LogService, LogLevel

        log_service = LogService()
        log_service.add_entry(1, LogLevel.INFO, "camera", "Cámara inicializada")
        log_service.add_entry(1, LogLevel.SUCCESS, "worker", "Captura completada")

        entries = log_service.get_entries(1)
        assert len(entries) == 2
        assert entries[0].source == "camera"
        assert entries[1].source == "worker"

        # CameraService instantiation does not affect log service
        mock_source = _create_mock_frame_source_unavailable()
        camera_service = CameraService(frame_source=mock_source)
        camera_service.check_availability()

        # Entries unchanged
        entries_after = log_service.get_entries(1)
        assert len(entries_after) == 2
        assert entries_after[0].message == "Cámara inicializada"
        assert entries_after[1].message == "Captura completada"
