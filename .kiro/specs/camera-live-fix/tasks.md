# Implementation Plan

## Overview

Fix the `CameraService` to delegate camera operations to the `FrameSource` interface instead of using `cv2.VideoCapture(0)` directly. This enables the AI Camera (IMX500) on Raspberry Pi 5 to work via `picamera2`, while preserving OpenCV fallback on development PCs. The fix follows the exploratory bugfix workflow: write tests to confirm the bug, write preservation tests, implement the fix, then verify all tests pass.

## Tasks

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - CameraService fails on RPi with AI Camera (IMX500)
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate CameraService uses cv2.VideoCapture even when picamera2 is available
  - **Scoped PBT Approach**: Scope the property to the concrete failing case: platform=raspberry_pi, camera_hardware=imx500_ai_camera, picamera2 available but cv2.VideoCapture(0) cannot open device
  - Test file: `tests/unit/test_camera_service_bug_condition.py`
  - Mock environment: `picamera2` importable and camera detected, but `cv2.VideoCapture(0).isOpened()` returns False
  - Property: For all inputs where `isBugCondition(input)` is True (RPi + IMX500 + picamera2 available), calling `CameraService().check_availability()` should return `CameraStatus.AVAILABLE` and `CameraService().capture_preview_frame()` should return valid JPEG bytes
  - The test assertions match Expected Behavior: status="available" and preview returns bytes
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS because current CameraService uses cv2.VideoCapture(0) which returns "not_detected" even when picamera2 could access the camera
  - Document counterexample: `CameraService().check_availability()` returns `CameraStatus.NOT_DETECTED` when picamera2 is available but cv2.VideoCapture fails
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 2.1, 2.2_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Non-RPi camera behavior and monitoring endpoints unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - Test file: `tests/unit/test_camera_service_preservation.py`
  - Observe on UNFIXED code:
    - `CameraService().check_availability()` returns `CameraStatus.AVAILABLE` when `cv2.VideoCapture(0).isOpened()` is True and `cap.read()` succeeds (Dev PC with USB webcam)
    - `CameraService().check_availability()` returns `CameraStatus.NOT_DETECTED` when `cv2.VideoCapture(0).isOpened()` is False (Dev PC without camera)
    - `CameraService().capture_preview_frame()` returns JPEG bytes when cv2.VideoCapture succeeds
    - `CameraService().capture_preview_frame()` returns None when cv2.VideoCapture fails
    - `/api/monitoring/{id}/log` endpoint returns log entries unchanged
    - `/api/monitoring/{id}/last-snapshot` endpoint returns snapshot image unchanged
  - Write property-based tests (using Hypothesis): for all non-bug-condition inputs (platform != raspberry_pi OR camera_hardware != imx500), the public API contract is preserved:
    - `check_availability()` returns a `CameraCheckResult` with status in {AVAILABLE, NOT_DETECTED, ERROR}
    - `capture_preview_frame()` returns `Optional[bytes]` (JPEG when available, None otherwise)
    - Response JSON shape from `/api/camera/status` is always `{"status": "...", "reason": "..."}`
  - Property-based test: for all random numpy frames (various shapes/dtypes), JPEG encoding produces valid bytes starting with FFD8 header
  - Verify tests PASS on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 3. Fix CameraService to delegate to FrameSource interface

  - [x] 3.1 Create OpenCvFrameSource implementation
    - New file: `src/infrastructure/camera/opencv_frame_source.py`
    - Implement `FrameSource` interface (read, release, is_available)
    - `__init__(device_index: int = 0)` — stores device index
    - `is_available()` — attempts `cv2.VideoCapture(device_index).isOpened()`, releases immediately
    - `read()` — opens capture, reads one frame, releases, returns `(bool, Optional[ndarray])`
    - `release()` — no-op (each read is self-contained)
    - _Requirements: 2.5_

  - [x] 3.2 Create frame_source_factory
    - New file: `src/application/services/frame_source_factory.py`
    - `create_frame_source() -> Optional[FrameSource]`:
      - Try importing picamera2 → instantiate `RaspberryCameraFrameSource`, call `is_available()` → return if True
      - If picamera2 unavailable or camera not detected → try `OpenCvFrameSource`, call `is_available()` → return if True
      - If neither works → return None
    - `get_unavailability_reason() -> str`:
      - Returns Spanish-language reason: "No se encontró cámara compatible. Verifica la conexión."
    - _Requirements: 2.1, 2.4, 2.5_

  - [x] 3.3 Refactor CameraService to use FrameSource
    - Modify: `src/application/services/camera_service.py`
    - Change constructor: `__init__(frame_source: Optional[FrameSource] = None)`
    - If no FrameSource provided, use factory to auto-detect
    - `check_availability()` → delegates to `frame_source.is_available()` if source exists; returns NOT_DETECTED with Spanish reason if factory returned None
    - `capture_preview_frame()` → delegates to `frame_source.read()`, encodes as JPEG via cv2.imencode
    - Remove all direct `cv2.VideoCapture` usage from this class
    - Preserve public API: `CameraCheckResult`, `CameraStatus` enum, method signatures unchanged
    - Spanish reason messages: "No se encontró cámara compatible. Verifica la conexión."
    - _Bug_Condition: isBugCondition(input) where input.platform = "raspberry_pi" AND input.camera_hardware = "imx500_ai_camera"_
    - _Expected_Behavior: check_availability() returns AVAILABLE when picamera2 can access camera; capture_preview_frame() returns valid JPEG bytes_
    - _Preservation: Non-camera-API behavior unchanged; same CameraCheckResult shape; same JPEG response format_
    - _Requirements: 1.1, 1.2, 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 3.5_

  - [x] 3.4 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - CameraService uses picamera2 on RPi with AI Camera
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior (status="available", preview returns JPEG bytes)
    - When this test passes, it confirms the fix correctly delegates to picamera2 via FrameSource
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.5 Verify preservation tests still pass
    - **Property 2: Preservation** - Non-RPi camera behavior and monitoring endpoints unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all tests still pass after fix (no regressions)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 4. Checkpoint - Ensure all tests pass
  - Run full test suite: `pytest tests/`
  - Verify Property 1 (Bug Condition) test passes after fix
  - Verify Property 2 (Preservation) tests pass after fix
  - Verify no import errors or broken dependencies
  - Verify `monitoring_api.py` routes work with refactored CameraService (same public API)
  - Ensure all tests pass, ask the user if questions arise.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2"] },
    { "id": 1, "tasks": ["3.1"] },
    { "id": 2, "tasks": ["3.2"] },
    { "id": 3, "tasks": ["3.3"] },
    { "id": 4, "tasks": ["3.4", "3.5"] },
    { "id": 5, "tasks": ["4"] }
  ]
}
```

Tasks 1 and 2 can run in parallel (wave 1). Tasks 3.1 → 3.2 → 3.3 are sequential (waves 2-4). Tasks 3.4 and 3.5 depend on 3.3 (wave 5). Task 4 depends on all prior tasks (wave 6).

## Notes

- The exploration test (task 1) is expected to FAIL on unfixed code — this is correct behavior that confirms the bug exists
- The preservation tests (task 2) are expected to PASS on unfixed code — this confirms baseline behavior
- After implementing the fix (task 3.3), the exploration test should PASS and preservation tests should STILL PASS
- Use `pytest` with Hypothesis for property-based tests
- All user-facing messages must be in Spanish per the UX steering guidelines
- The `monitoring_api.py` routes do NOT need modification — they instantiate `CameraService()` which will auto-detect the backend via the factory
