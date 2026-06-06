# Requirements - Camera Live Integration

## Objective

Integrate the Raspberry Pi AI Camera as a live image source for Tomato Monitor, without replacing the current offline video pipeline during the initial integration phase.

The goal of this spec is to evolve the system from offline video processing toward live camera capture in a controlled, measurable, and Raspberry-compatible way.

## Context

Tomato Monitor currently processes offline video files. The Raspberry Pi 5 environment has already been prepared, the FastAPI application runs correctly, Detectron2 was installed, and model inference was validated.

The Raspberry Pi AI Camera is available and working at hardware level. However, the current codebase does not yet use live camera frames as input. The AI Camera should first be integrated as a capture device, not as an automatic inference accelerator.

## Functional Requirements

### RF-001: Camera availability check

WHEN the application or a camera validation script is executed  
THE SYSTEM SHALL verify whether the Raspberry Pi AI Camera is available.

### RF-002: Live frame capture

WHEN the camera is available  
THE SYSTEM SHALL capture frames from the Raspberry Pi AI Camera.

### RF-003: Frame conversion

WHEN a frame is captured from the camera  
THE SYSTEM SHALL convert it into a format compatible with the current vision pipeline.

### RF-004: Camera preview or validation mode

WHEN the user runs a camera validation command or script  
THE SYSTEM SHALL display or save a sample frame to confirm that live capture works.

### RF-005: Optional pipeline connection

WHEN live frames are captured and the user explicitly enables processing  
THE SYSTEM SHALL allow captured frames to be passed into the existing vision pipeline or a future edge-oriented variant.

### RF-006: Error handling

WHEN the camera is not available, disconnected, busy, or fails during capture  
THE SYSTEM SHALL report a clear error without crashing the full application unexpectedly.

### RF-007: Configuration-driven input source

WHEN the application starts a vision process  
THE SYSTEM SHALL allow choosing between offline video input and live camera input through configuration or explicit parameters.

## Non-Functional Requirements

### RNF-001: Raspberry Pi compatibility

THE SYSTEM SHALL run on Raspberry Pi 5 using CPU by default.

### RNF-002: No premature detector replacement

THE SYSTEM SHALL NOT replace Detectron2 or the current detector as part of this spec.

### RNF-003: No assumed AI Camera acceleration

THE SYSTEM SHALL NOT assume that the AI Camera accelerates the current Detectron2 pipeline automatically.

### RNF-004: Thermal safety

THE SYSTEM SHALL avoid long live processing sessions without explicit user control and temperature monitoring.

### RNF-005: Minimal dependencies

THE SYSTEM SHALL avoid adding unnecessary dependencies. Any new camera-related dependency must be justified.

### RNF-006: Backward compatibility

THE SYSTEM SHALL preserve the existing offline video workflow.

## Acceptance Criteria

- A camera validation script or command exists.
- The system can capture at least one frame from the Raspberry Pi AI Camera.
- The captured frame can be converted to an OpenCV-compatible format.
- Offline video processing remains functional.
- Camera errors are reported clearly.
- No detector replacement is implemented in this spec.
- Documentation is updated with camera setup and validation steps.

## Out of Scope

- Replacing Detectron2 with AI Camera inference.
- Training or converting models for IMX500.
- Real-time optimized pipeline.
- Autonomous robot navigation.
- Production deployment.