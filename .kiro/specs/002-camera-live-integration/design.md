# Design - Camera Live Integration

## Overview

This spec introduces live camera capture as a new input source while preserving the existing offline video pipeline. The integration must be incremental and should not force a redesign of the detector, classifier, tracker, or persistence layer.

The first goal is to validate that frames from the Raspberry Pi AI Camera can be captured and represented in the same format currently expected by the vision pipeline.

## Design Principles

- Keep offline video processing intact.
- Add live capture as an alternative input source.
- Avoid detector replacement in this phase.
- Keep camera-specific logic isolated from business logic.
- Do not couple FastAPI routes directly to low-level camera APIs.
- Use configuration to choose input mode.
- Keep the integration testable with small scripts before connecting it to the full app.

## Proposed Components

### Camera capture adapter

Suggested location:

src/infrastructure/camera/

Suggested files:

src/infrastructure/camera/__init__.py
src/infrastructure/camera/raspberry_camera_capture.py
src/infrastructure/camera/camera_frame_source.py

Responsibilities:

Initialize the Raspberry Pi AI Camera.
Capture frames.
Convert frames to OpenCV-compatible format.
Handle camera errors.
Release camera resources cleanly.
Frame source abstraction

A simple abstraction can be introduced to avoid coupling the pipeline only to video files.

Possible concept:

class FrameSource:
    def read(self):
        ...

    def release(self):
        ...

Possible implementations:

VideoFileFrameSource
RaspberryCameraFrameSource

This should only be added if it can be done without a large refactor.

Camera validation script

Suggested location:

scripts/camera/

Suggested file:

scripts/camera/validate_ai_camera.py

Responsibilities:

Check camera availability.
Capture a single frame.
Save the frame to a test output path.
Print frame shape, resolution, and basic status.
Documentation

Suggested files to update:

docs/hardware.md
docs/raspberry-setup.md
docs/camera-live-integration.md
Input Modes

The system should support at least two input modes:

offline_video
live_camera

The default mode should remain offline_video to avoid breaking the current workflow.

Configuration

Possible configuration fields:

INPUT_SOURCE=offline_video
CAMERA_ENABLED=false
CAMERA_WIDTH=640
CAMERA_HEIGHT=480
CAMERA_FPS=5

These values should not be hardcoded deep inside the pipeline.

Error Handling

Camera-related errors should be explicit:

Camera not detected.
Camera busy.
Frame capture failed.
Unsupported frame format.
Permission or driver issue.
Timeout.

The system should fail gracefully and give the user an actionable message.

Performance Considerations

Initial live capture tests should use conservative settings:

Low resolution first.
Low FPS first.
No automatic video annotation.
No automatic snapshot flooding.
No long-running inference without monitoring.

Suggested starting point:

640x480 at 1-5 FPS
Security Considerations
Do not expose camera access through public endpoints without control.
Do not allow arbitrary file paths for saving frames.
Do not overwrite existing outputs without explicit configuration.
Avoid logging sensitive local paths unnecessarily.
Risks
Risk	Impact	Mitigation
Camera API differs from OpenCV expectations	Medium	Add adapter layer
Live processing overloads CPU	High	Start with capture-only validation
FastAPI blocks during camera loop	High	Keep long-running capture outside request cycle initially
AI Camera assumed to accelerate Detectron2	High	Explicitly document that it does not
Thermal throttling	High	Monitor temperature and limit FPS
Validation Strategy
Validate camera through system command.
Validate camera through Python script.
Save one frame.
Verify OpenCV compatibility.
Optionally pass one captured frame to existing inference code.
Document results.
Out of Scope Design Notes

This spec does not design model conversion for the AI Camera IMX500. That should be handled in a future spec only after the baseline benchmark and capture integration are complete.