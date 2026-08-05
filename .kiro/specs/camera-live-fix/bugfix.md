# Bugfix Requirements Document

## Introduction

The monitoring setup screen (`/modulos/{id}/monitoreo/configurar`) shows "Imagen no disponible" and "Cámara no detectada" on Raspberry Pi 5 with the AI Camera (IMX500). The "Iniciar Monitoreo" button remains permanently disabled because the `CameraService` uses `cv2.VideoCapture(0)` to check camera availability and capture preview frames. The Raspberry Pi AI Camera is NOT a V4L2 device — it is only accessible through `picamera2`. The project already has a correct implementation (`RaspberryCameraFrameSource`) that uses `picamera2`, but the `CameraService` used by the API endpoints does not delegate to it.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the system runs on Raspberry Pi with the AI Camera (IMX500) and the `/api/camera/status` endpoint is called THEN the system returns `{"status": "not_detected"}` because `cv2.VideoCapture(0)` cannot open the device

1.2 WHEN the system runs on Raspberry Pi with the AI Camera (IMX500) and the `/api/camera/preview` endpoint is called THEN the system returns HTTP 503 with "Cámara no disponible" because the OpenCV capture fails

1.3 WHEN the camera status returns "not_detected" THEN the "Iniciar Monitoreo" button remains permanently disabled, preventing the farmer from starting any monitoring session

1.4 WHEN the system runs on a development machine without any camera connected and the `/api/camera/status` endpoint is called THEN the system returns `{"status": "not_detected"}` with no indication of whether the failure is expected or actionable

### Expected Behavior (Correct)

2.1 WHEN the system runs on Raspberry Pi with the AI Camera (IMX500) and the `/api/camera/status` endpoint is called THEN the system SHALL check availability using `picamera2` (via the `FrameSource` interface) and return `{"status": "available"}` if the camera is operational

2.2 WHEN the system runs on Raspberry Pi with the AI Camera (IMX500) and the `/api/camera/preview` endpoint is called THEN the system SHALL capture a frame using `picamera2` (via the `FrameSource` interface) and return the JPEG image with HTTP 200

2.3 WHEN the camera status returns "available" THEN the "Iniciar Monitoreo" button SHALL become enabled, allowing the farmer to start a monitoring session

2.4 WHEN the system runs on a development machine without any camera connected and the `/api/camera/status` endpoint is called THEN the system SHALL return `{"status": "not_detected"}` with a clear reason message indicating no camera backend is available (e.g., "No se encontró cámara compatible. Verifica la conexión.")

2.5 WHEN the system runs on a development machine with a USB webcam connected and the `/api/camera/status` endpoint is called THEN the system SHALL fall back to OpenCV `VideoCapture` and return `{"status": "available"}` if the webcam is accessible

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a monitoring session is running and the `MonitoringWorker` reads frames from the `FrameSource` THEN the system SHALL CONTINUE TO use `RaspberryCameraFrameSource` (picamera2) for live frame capture during monitoring execution

3.2 WHEN the `/api/monitoring/{id}/log` endpoint is called THEN the system SHALL CONTINUE TO return log entries for the monitoring session without any change

3.3 WHEN the `/api/monitoring/{id}/last-snapshot` endpoint is called THEN the system SHALL CONTINUE TO return the most recent snapshot image from the filesystem without any change

3.4 WHEN the monitoring setup page loads and the model status is checked THEN the system SHALL CONTINUE TO display the model availability badge independently of camera status

3.5 WHEN the farmer clicks "Actualizar cámara" on the monitoring setup screen THEN the system SHALL CONTINUE TO re-check camera status and refresh the preview image

---

## Bug Condition (Formal)

```pascal
FUNCTION isBugCondition(X)
  INPUT: X of type CameraCheckRequest (contains: platform, camera_hardware)
  OUTPUT: boolean
  
  // The bug triggers when running on RPi where the camera requires picamera2
  // but CameraService uses cv2.VideoCapture which cannot access the device
  RETURN X.platform = "raspberry_pi" AND X.camera_hardware = "imx500_ai_camera"
END FUNCTION
```

## Fix Property

```pascal
// Property: Fix Checking - Camera accessible on RPi via picamera2
FOR ALL X WHERE isBugCondition(X) DO
  status_result ← checkCameraStatus'(X)
  preview_result ← capturePreview'(X)
  ASSERT status_result.status = "available"
  ASSERT preview_result IS valid_jpeg_bytes
  ASSERT no_cv2_VideoCapture_used(status_result)
END FOR
```

## Preservation Property

```pascal
// Property: Preservation Checking - Non-RPi behavior unchanged
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT checkCameraStatus(X) = checkCameraStatus'(X)
  ASSERT capturePreview(X) = capturePreview'(X)
END FOR
```
