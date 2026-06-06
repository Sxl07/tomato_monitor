# Camera Live Integration — Tomato Monitor

**Status:** Pending implementation — Spec 002

This document will describe the live camera integration for Tomato Monitor once Spec 002 is implemented.

It will cover:

- Validation procedure for the Raspberry Pi AI Camera
- Frame capture using `picamera2`
- Frame format conversion to OpenCV-compatible format
- Input mode configuration (`offline_video` vs `live_camera`)
- Error handling for camera unavailable, frame capture failure, and resource release
- Performance observations from live capture vs offline video
- Known limitations and thermal considerations

---

## Prerequisites

Before live camera integration can be implemented:

1. Spec 001 (baseline benchmark) must be completed.
   - Reason: live capture performance must be compared against the offline baseline.
2. The Raspberry Pi AI Camera must be validated at the system level.
   - Validation command: `rpicam-hello` or `libcamera-hello`

---

## Related Documents

- `docs/hardware.md` — AI Camera hardware details
- `docs/raspberry-setup.md` — system setup and validation steps
- `docs/decisions/ADR-002` — AI Camera integration strategy
- `.kiro/specs/002-camera-live-integration/` — full spec (requirements, design, tasks)
