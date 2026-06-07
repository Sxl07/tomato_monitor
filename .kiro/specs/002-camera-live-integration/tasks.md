# Implementation Plan: Camera Live Integration

## Overview

Integrate the Raspberry Pi AI Camera as a live image source. Create the camera adapter, frame source abstraction, validation script, error handling, and documentation. The actual hardware validation will be done manually on the Raspberry Pi.

## Tasks

- [x] 1. Create frame source abstraction
  - [x] 1.1 Create FrameSource interface in domain layer
  - [x] 1.2 Create VideoFileFrameSource implementation
  - [x] 1.3 Create RaspberryCameraFrameSource implementation
  - [x] 1.4 Create camera configuration in settings

- [x] 2. Create camera validation scripts
  - [x] 2.1 Create validation script (`scripts/camera/validate_ai_camera.py`)
  - [x] 2.2 Create single-frame inference script (`scripts/camera/single_frame_inference.py`)

- [x] 3. Documentation
  - [x] 3.1 Create/update camera integration documentation (`docs/camera-live-integration.md`)
  - [x] 3.2 Update hardware and setup docs

- [x] 4. Final verification
  - All modules import cleanly (picamera2 absence handled gracefully)
  - FastAPI app starts without errors
  - Camera imports verified on WSL

## Pending hardware validation (manual on Raspberry Pi)

- [ ] Run `scripts/camera/validate_ai_camera.py` on RPi with AI Camera connected
- [ ] Confirm frame capture and JPEG save
- [ ] Run `scripts/camera/single_frame_inference.py` on RPi with detector loaded
- [ ] Record temperature observations
- [ ] Document results in `docs/benchmarks/`

## Notes

- picamera2 is NOT in requirements.txt (RPi-only, pre-installed on RPi OS)
- The RaspberryCameraFrameSource handles missing picamera2 gracefully
- Offline video pipeline remains fully functional and is the default mode
- No detector replacement was introduced
