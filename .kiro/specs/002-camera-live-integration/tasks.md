# Tasks - Camera Live Integration

## Phase 1: Preparation

- [ ] Review current video input flow in the pipeline.
- [ ] Identify where frames are currently read from offline videos.
- [ ] Document the current input assumptions of the pipeline.
- [ ] Confirm Raspberry Pi AI Camera works through system-level commands.
- [ ] Create `docs/camera-live-integration.md`.

## Phase 2: Camera validation

- [ ] Create `scripts/camera/` directory if it does not exist.
- [ ] Create `scripts/camera/validate_ai_camera.py`.
- [ ] Implement a minimal camera availability check.
- [ ] Capture a single frame.
- [ ] Save a sample frame to a controlled output path.
- [ ] Print frame dimensions and capture status.
- [ ] Document validation steps in `docs/raspberry-setup.md`.

## Phase 3: Frame source design

- [ ] Review whether a frame source abstraction is needed.
- [ ] If needed, design a minimal `FrameSource` interface.
- [ ] Avoid large refactors during this spec.
- [ ] Keep offline video input as the default path.
- [ ] Document the proposed input source design.

## Phase 4: Optional pipeline connection

- [ ] Add a controlled way to pass one captured frame into the existing vision flow.
- [ ] Validate single-frame inference from camera capture.
- [ ] Do not enable continuous live inference by default.
- [ ] Record CPU, RAM, and temperature observations.
- [ ] Document results in `docs/benchmarks/`.

## Phase 5: Error handling and safety

- [ ] Add clear error handling for camera unavailable.
- [ ] Add clear error handling for frame capture failure.
- [ ] Ensure camera resources are released properly.
- [ ] Avoid infinite loops without explicit stop condition.
- [ ] Avoid long-running camera inference without thermal monitoring.

## Phase 6: Documentation and review

- [ ] Update `docs/hardware.md`.
- [ ] Update `docs/raspberry-setup.md`.
- [ ] Update `docs/camera-live-integration.md`.
- [ ] Create an ADR if camera integration changes architecture.
- [ ] Review that offline video processing still works.
- [ ] Review compatibility with Raspberry Pi 5.

## Completion Criteria

- [ ] Raspberry Pi AI Camera can be validated through a project script.
- [ ] At least one frame can be captured and saved.
- [ ] Captured frame format is compatible with OpenCV.
- [ ] Offline video mode remains functional.
- [ ] Camera integration is documented.
- [ ] No detector replacement was introduced.