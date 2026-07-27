# Camera Live Fix — Bugfix Design

## Overview

The `CameraService` in `src/application/services/camera_service.py` uses `cv2.VideoCapture(0)` directly to check camera availability and capture preview frames. On Raspberry Pi 5 with the AI Camera (IMX500), this fails because the AI Camera is not a V4L2 device — it is only accessible through `picamera2`. The project already has a `FrameSource` interface and a working `RaspberryCameraFrameSource` implementation, but the `CameraService` does not use them.

The fix refactors `CameraService` to delegate frame capture and availability checks to the `FrameSource` interface. A factory function selects the correct backend at runtime: `picamera2` on Raspberry Pi, OpenCV `VideoCapture` on development PCs with a USB webcam, or a clear error when neither works.

## Glossary

- **Bug_Condition (C)**: The system runs on Raspberry Pi with the AI Camera (IMX500) and `CameraService` attempts to use `cv2.VideoCapture(0)`, which cannot access the device
- **Property (P)**: Camera availability and preview capture work correctly on both RPi (via picamera2) and development PCs (via OpenCV fallback)
- **Preservation**: The `MonitoringWorker` frame capture during execution, the `/api/monitoring/{id}/log` endpoint, the `/api/monitoring/{id}/last-snapshot` endpoint, and all frontend behavior remain unchanged
- **FrameSource**: Abstract interface (`src/domain/interfaces/frame_source.py`) with `read()`, `release()`, and `is_available()` methods
- **RaspberryCameraFrameSource**: Existing picamera2-based implementation (`src/infrastructure/camera/raspberry_camera_frame_source.py`)
- **CameraService**: Application service (`src/application/services/camera_service.py`) responsible for camera status checks and preview frame capture for the monitoring setup API
- **OpenCvFrameSource**: New FrameSource implementation that wraps `cv2.VideoCapture` for USB webcams on development PCs

## Bug Details

### Bug Condition

The bug manifests when the system runs on Raspberry Pi 5 with the AI Camera (IMX500) and the `/api/camera/status` or `/api/camera/preview` endpoints are called. The `CameraService` uses `cv2.VideoCapture(0)` which cannot open the IMX500 device because it is not exposed as a V4L2 device — it requires `picamera2`.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type CameraCheckRequest (platform, camera_hardware, endpoint)
  OUTPUT: boolean
  
  RETURN input.platform = "raspberry_pi"
         AND input.camera_hardware = "imx500_ai_camera"
         AND input.endpoint IN ["/api/camera/status", "/api/camera/preview"]
         AND cameraService_uses_cv2_VideoCapture()
END FUNCTION
```

### Examples

- **RPi + AI Camera + /api/camera/status**: Expected `{"status": "available"}`, actual `{"status": "not_detected"}` — the button stays disabled
- **RPi + AI Camera + /api/camera/preview**: Expected JPEG bytes (HTTP 200), actual HTTP 503 with "Cámara no disponible"
- **Dev PC + USB webcam + /api/camera/status**: Expected `{"status": "available"}` (fallback to OpenCV), actual `{"status": "available"}` (this already works, but the fix must preserve it)
- **Dev PC + no camera + /api/camera/status**: Expected `{"status": "not_detected"}` with a clear reason message — currently works but with no helpful message

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- The `MonitoringWorker` continues to use `RaspberryCameraFrameSource` directly for frame capture during active monitoring sessions
- The `/api/monitoring/{id}/log` endpoint continues to return log entries unchanged
- The `/api/monitoring/{id}/last-snapshot` endpoint continues to return snapshot images from the filesystem unchanged
- The model status badge on the monitoring setup page operates independently of camera status
- The "Actualizar cámara" button re-checks camera status and refreshes the preview image
- The frontend JavaScript receives the same JSON shape (`{"status": "...", "reason": "..."}`) and the same image response format (JPEG bytes)
- No new endpoints are added; existing endpoint paths remain identical

**Scope:**
All inputs that do NOT involve the `/api/camera/status` or `/api/camera/preview` endpoints should be completely unaffected by this fix. This includes:
- All monitoring execution operations (start, pause, resume, abort, complete)
- All monitoring log and snapshot retrieval endpoints
- All agricultural UI routes and templates
- All module and greenhouse CRUD operations

## Hypothesized Root Cause

Based on the bug analysis, the root cause is clear and singular:

1. **Direct cv2.VideoCapture dependency**: `CameraService` instantiates `cv2.VideoCapture(0)` directly instead of using the `FrameSource` interface. The IMX500 AI Camera does not register as a V4L2 device under `/dev/video*`, so OpenCV cannot open it. The `picamera2` library provides the only supported access path.

2. **Missing abstraction layer**: The `CameraService` was written before the `FrameSource` interface and `RaspberryCameraFrameSource` were created. It was never updated to delegate to them.

3. **No backend auto-detection**: There is no factory or strategy that selects the appropriate camera backend based on what is available in the current environment. The code assumes OpenCV + V4L2 is always the correct approach.

4. **No fallback chain**: On development PCs without `picamera2`, the system needs to fall back to OpenCV for USB webcams. Currently, there is only the hard-coded OpenCV path with no awareness of `picamera2` at all.

## Correctness Properties

Property 1: Bug Condition - Camera accessible on RPi via picamera2

_For any_ input where the system runs on Raspberry Pi with the AI Camera (IMX500) and `picamera2` is available, the fixed `CameraService` SHALL check camera availability using `picamera2` (via the `FrameSource` interface) and return `{"status": "available"}`. The `/api/camera/preview` endpoint SHALL return valid JPEG bytes.

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation - Non-camera-API behavior unchanged

_For any_ input that does NOT involve the `/api/camera/status` or `/api/camera/preview` endpoints, the fixed code SHALL produce exactly the same behavior as the original code, preserving all existing functionality for monitoring execution, log retrieval, snapshot retrieval, and UI rendering.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

Property 3: Fallback - OpenCV fallback on development PCs

_For any_ input where `picamera2` is NOT available but OpenCV can open a video capture device, the fixed `CameraService` SHALL fall back to the OpenCV-based `FrameSource` and return `{"status": "available"}` with a working preview.

**Validates: Requirements 2.5**

Property 4: Graceful failure - No camera available

_For any_ input where neither `picamera2` nor OpenCV can access a camera device, the fixed `CameraService` SHALL return `{"status": "not_detected"}` with a clear, actionable reason message in Spanish.

**Validates: Requirements 2.4**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `src/infrastructure/camera/opencv_frame_source.py` (NEW)

**Purpose**: New `FrameSource` implementation wrapping `cv2.VideoCapture` for USB webcams on development PCs.

**Specific Changes**:
1. **Create `OpenCvFrameSource`** implementing `FrameSource`:
   - `__init__(device_index: int = 0)` — stores the device index
   - `read()` — opens capture, reads one frame, releases, returns `(bool, Optional[ndarray])`
   - `release()` — no-op (each read is self-contained, no persistent connection)
   - `is_available()` — attempts `cv2.VideoCapture(device_index).isOpened()`, releases immediately

---

**File**: `src/application/services/frame_source_factory.py` (NEW)

**Purpose**: Factory function that auto-detects the correct `FrameSource` backend.

**Specific Changes**:
2. **Create `create_frame_source()` factory**:
   - Try `picamera2` import → if available, instantiate `RaspberryCameraFrameSource`, call `is_available()` → if True, return it
   - If `picamera2` not available or camera not detected, try `OpenCvFrameSource`, call `is_available()` → if True, return it
   - If neither works, return `None`
   - Also provide a `get_unavailability_reason()` helper that returns a Spanish-language reason string

---

**File**: `src/application/services/camera_service.py` (MODIFY)

**Function**: `CameraService` class

**Specific Changes**:
3. **Refactor `CameraService` to accept a `FrameSource`**:
   - Change constructor to accept an optional `FrameSource` instance
   - If no `FrameSource` provided, use the factory to auto-detect
   - `check_availability()` → delegates to `frame_source.is_available()` if a source was found; returns "not_detected" with reason if factory returned None
   - `capture_preview_frame()` → delegates to `frame_source.read()`, encodes result as JPEG
   - Remove all direct `cv2.VideoCapture` usage from this class

4. **Preserve the same public API**:
   - `check_availability()` still returns `CameraCheckResult`
   - `capture_preview_frame()` still returns `Optional[bytes]`
   - `CameraStatus` enum unchanged

5. **Add descriptive reason messages** in Spanish:
   - When picamera2 is not installed: "picamera2 no disponible. Instala picamera2 para usar la cámara del Raspberry Pi."
   - When OpenCV cannot open device: "No se encontró cámara compatible. Verifica la conexión."
   - When no backend works: "No se encontró cámara compatible. Verifica la conexión."

---

**File**: `app/routes/monitoring_api.py` (MINOR MODIFY)

**Specific Changes**:
6. **No structural change needed**: The routes already instantiate `CameraService()` and call its methods. Since we preserve the public API, the routes continue to work. However, we may optionally allow dependency injection of `CameraService` via `app.state` for testability — this is optional and non-breaking.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write unit tests that mock the environment (picamera2 available, cv2.VideoCapture failing) and call `CameraService.check_availability()` and `capture_preview_frame()`. Run these on the UNFIXED code to observe failures.

**Test Cases**:
1. **Picamera2 available, VideoCapture fails**: Simulate RPi environment — `cv2.VideoCapture(0).isOpened()` returns False but picamera2 would work. Call `check_availability()` (will return "not_detected" on unfixed code)
2. **Status endpoint returns not_detected on RPi**: Call `/api/camera/status` with mocked RPi environment (will fail on unfixed code)
3. **Preview endpoint returns 503 on RPi**: Call `/api/camera/preview` with mocked RPi environment (will return 503 on unfixed code)

**Expected Counterexamples**:
- `check_availability()` returns `CameraStatus.NOT_DETECTED` even when picamera2 could access the camera
- Possible cause: `CameraService` never attempts picamera2, only cv2.VideoCapture

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  camera_service := CameraService(frame_source=RaspberryCameraFrameSource())
  status_result := camera_service.check_availability()
  ASSERT status_result.status = "available"
  
  preview_result := camera_service.capture_preview_frame()
  ASSERT preview_result IS NOT None
  ASSERT preview_result IS valid_jpeg_bytes
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT checkCameraStatus_original(input) = checkCameraStatus_fixed(input)
  ASSERT capturePreview_original(input) = capturePreview_fixed(input)
  ASSERT monitoringLog_original(input) = monitoringLog_fixed(input)
  ASSERT lastSnapshot_original(input) = lastSnapshot_fixed(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain (various combinations of platform/hardware/camera availability)
- It catches edge cases that manual unit tests might miss (e.g., picamera2 installed but camera disconnected)
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Observe behavior on UNFIXED code first for non-RPi environments, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Dev PC with USB webcam preservation**: Verify that OpenCV fallback returns "available" and JPEG bytes — same as before
2. **Dev PC without camera preservation**: Verify that "not_detected" is returned — same as before, but now with a clearer reason
3. **Monitoring endpoints preservation**: Verify `/api/monitoring/{id}/log` and `/api/monitoring/{id}/last-snapshot` remain unchanged
4. **Frontend JSON shape preservation**: Verify the response format (`{"status": "...", "reason": "..."}`) is identical

### Unit Tests

- Test `OpenCvFrameSource.is_available()` with mocked cv2.VideoCapture (open success and failure)
- Test `OpenCvFrameSource.read()` returns valid frame when camera accessible
- Test `create_frame_source()` factory returns `RaspberryCameraFrameSource` when picamera2 available and camera detected
- Test `create_frame_source()` factory returns `OpenCvFrameSource` when picamera2 not available but OpenCV works
- Test `create_frame_source()` factory returns None when neither backend works
- Test refactored `CameraService.check_availability()` delegates to FrameSource
- Test refactored `CameraService.capture_preview_frame()` delegates to FrameSource and encodes JPEG
- Test `CameraService` returns descriptive Spanish reason messages

### Property-Based Tests

- Generate random combinations of (picamera2_available, opencv_available, camera_connected) and verify the factory always selects the correct backend or returns None
- Generate random frame data (numpy arrays of various shapes) and verify JPEG encoding always produces valid bytes
- Generate random environments and verify the public API contract (CameraCheckResult shape) is always satisfied

### Integration Tests

- Test full flow: `/api/camera/status` → correct JSON response with mocked FrameSource
- Test full flow: `/api/camera/preview` → JPEG response with mocked FrameSource returning a frame
- Test full flow: `/api/camera/preview` → 503 response when FrameSource is unavailable
- Test that `/api/monitoring/{id}/log` and `/api/monitoring/{id}/last-snapshot` are completely unaffected by the camera service changes
