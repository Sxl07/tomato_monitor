# Requirements Document

## Introduction

This spec defines the complete monitoring execution flow for Tomato Monitor. It covers the lifecycle of a monitoring session from initialization through completion, including live camera integration, intelligent snapshot capture via Scene Gate, per-snapshot inference (detection + health + maturity), result persistence in SQLite, and API endpoints for session control. The MonitoringService orchestrates the entire flow as a background task on the Raspberry Pi 5.

## Glossary

- **Monitoring_Service**: Application-layer service (`src/application/`) that orchestrates the monitoring session lifecycle, coordinates camera capture, Scene Gate evaluation, inference execution, and result persistence.
- **Scene_Gate**: Infrastructure component (`src/infrastructure/vision/capture_gate.py`) that evaluates frame similarity using ORB keypoint matching and HSV histogram comparison to decide when a scene change warrants a new snapshot capture.
- **Frame_Source**: Domain interface (`src/domain/interfaces/frame_source.py`) implemented by `RaspberryCameraFrameSource` for live camera input. Provides `read()`, `release()`, and `is_available()` methods.
- **Inference_Pipeline**: The sequential execution of DetectronDetector → Cropper → ResNetHealthClassifier → MaturityEstimator on a single captured snapshot image.
- **Monitoring_Session**: A domain entity representing a single robot traversal of a greenhouse module, with status, counters, and timestamps.
- **Monitoring_Status**: A value object implementing the finite state machine with validated transitions: initializing → running → paused → finishing → completed/aborted/error.
- **Snapshot**: A single image captured during monitoring when the Scene Gate triggers, stored on the filesystem with metadata in SQLite.
- **Inspection_Result**: A detected tomato within a snapshot with bounding box, health classification, and optional maturity estimation.
- **Monitoring_Metrics**: Pre-computed aggregated metrics (totals, percentages) calculated at monitoring completion or abort.
- **Background_Task**: An asynchronous task running outside the HTTP request-response cycle, allowing the farmer to monitor progress without blocking the API.
- **Active_Cooling**: The official Raspberry Pi 5 case fan, mandatory during sustained inference workloads to prevent thermal throttling.

## Requirements

### Requirement 1: Session Initialization

**User Story:** As a farmer, I want the system to prepare the camera and AI models when I start a monitoring session, so that the monitoring begins reliably without manual setup.

#### Acceptance Criteria

1. WHEN the farmer requests a new monitoring session for a module, THE Monitoring_Service SHALL create a Monitoring_Session entity with status `initializing`, associate it with the specified module_id, and persist it via MonitoringRepository within 1 second of the request.
2. WHILE the Monitoring_Session status is `initializing`, THE Monitoring_Service SHALL load the Frame_Source (RaspberryCameraFrameSource) and verify camera availability via `is_available()`.
3. WHILE the Monitoring_Session status is `initializing`, THE Monitoring_Service SHALL load the Inference_Pipeline components (DetectronDetector, ResNetHealthClassifier, MaturityEstimator).
4. WHEN the Frame_Source and Inference_Pipeline components load successfully, THE Monitoring_Service SHALL transition the Monitoring_Session status to `running` and record the `started_at` timestamp.
5. IF the Frame_Source reports unavailability during initialization, THEN THE Monitoring_Service SHALL transition the Monitoring_Session status to `error` and record an error reason indicating the camera could not be reached.
6. IF any Inference_Pipeline component fails to load during initialization, THEN THE Monitoring_Service SHALL transition the Monitoring_Session status to `error` and record an error reason identifying which component failed to load.
7. IF the initialization process does not complete within 30 seconds, THEN THE Monitoring_Service SHALL transition the Monitoring_Session status to `error` and record an error reason indicating initialization timed out.

### Requirement 2: Capture Loop Execution

**User Story:** As a farmer, I want the robot to automatically capture images only when the scene changes meaningfully, so that the system processes relevant snapshots without wasting resources.

#### Acceptance Criteria

1. WHILE the Monitoring_Session status is `running`, THE Monitoring_Service SHALL continuously read frames from the Frame_Source at the camera's configured frame rate.
2. WHILE the Monitoring_Session status is `running`, THE Monitoring_Service SHALL evaluate each frame against the Scene_Gate to determine if the scene has changed, where a scene change is detected when: the cooldown period (>=18 frames since last capture) has elapsed AND ORB feature matches drop below 35 AND HSV histogram difference exceeds 0.38, OR when the timeout threshold (>=45 frames without capture) is reached.
3. WHEN the Scene_Gate triggers (scene change detected), THE Monitoring_Service SHALL capture the current frame as a Snapshot and persist the image to the filesystem at path `outputs/monitorings/{monitoring_id}/snapshots/snapshot_{frame_index}.jpg`.
4. WHEN a Snapshot is captured, THE Monitoring_Service SHALL create a Snapshot entity with the frame_index, image_path, captured_at timestamp (UTC), and change_score, and persist it via SnapshotRepository.
5. IF the Frame_Source returns a read failure (camera disconnection or I/O error), THEN THE Monitoring_Service SHALL transition the Monitoring_Session status to `error`, record an error reason indicating the frame source became unavailable, and persist all snapshots and results captured up to that point.
6. WHILE the Monitoring_Session status is `running`, THE Monitoring_Service SHALL maintain a frame counter to track the number of frames elapsed since the last capture, used for Scene_Gate cooldown and timeout evaluation.

### Requirement 3: Per-Snapshot Inference

**User Story:** As a farmer, I want the system to analyze each captured snapshot for tomato detection, health, and maturity, so that I receive accurate agricultural metrics.

#### Acceptance Criteria

1. WHEN a Snapshot is captured, THE Monitoring_Service SHALL execute the DetectronDetector on the snapshot image to produce detection results, where each detection includes a bounding box (x1, y1, x2, y2) and a confidence score between 0.0 and 1.0.
2. WHEN the DetectronDetector produces detections with confidence score >= the configured detection threshold (0.80), THE Monitoring_Service SHALL crop each detected region and execute the ResNetHealthClassifier on each crop to produce a health_label (`healthy` or `unhealthy`) and health_confidence.
3. WHEN the ResNetHealthClassifier classifies a detection as `healthy` with detection score >= the configured maturity threshold (0.80), THE Monitoring_Service SHALL execute the MaturityEstimator on that crop to produce a maturity_stage (one of: green, breaker, turning, pink, light_red, red) and maturity_percent (0-100).
4. WHEN inference completes for a snapshot, THE Monitoring_Service SHALL persist each detection as an Inspection_Result entity via InspectionResultRepository, including bounding box coordinates, detection_score, health_label, health_confidence, and optional maturity_stage and maturity_percent (null when maturity was not executed).
5. WHEN inference completes for a snapshot, THE Monitoring_Service SHALL update the Snapshot entity's `has_detections` field to `True` if at least one detection was found, or `False` if zero detections were found.
6. IF inference on a single snapshot fails (model error or corrupt image), THEN THE Monitoring_Service SHALL record the error in the Snapshot entity, skip that snapshot's results, and continue processing the next snapshot without terminating the monitoring session.
7. WHEN inference completes for a snapshot, THE Monitoring_Service SHALL increment the Monitoring_Session counters (`total_snapshots` by 1, `total_detections` by the number of detections found in that snapshot) and persist the updated values via MonitoringRepository.

### Requirement 4: Session Pause and Resume

**User Story:** As a farmer, I want to pause and resume the monitoring session, so that the system can handle interruptions without losing progress.

#### Acceptance Criteria

1. WHEN the farmer requests to pause the monitoring, THE Monitoring_Service SHALL validate that the Monitoring_Session status is `running` and transition it to `paused`.
2. IF the farmer requests to pause the monitoring while the Monitoring_Session status is not `running`, THEN THE Monitoring_Service SHALL reject the request and return an error indicating the current status does not allow pausing.
3. WHILE the Monitoring_Session status is `paused`, THE Monitoring_Service SHALL stop reading frames from the Frame_Source but keep the Frame_Source resource open without releasing it.
4. WHEN the farmer requests to resume the monitoring, THE Monitoring_Service SHALL verify Frame_Source availability via `is_available()` and, if available, transition the Monitoring_Session status from `paused` to `running` and resume the capture loop from the next frame.
5. IF the farmer requests to resume the monitoring while the Monitoring_Session status is not `paused`, THEN THE Monitoring_Service SHALL reject the request and return an error indicating the current status does not allow resuming.
6. WHILE the Monitoring_Session status is `paused`, THE Monitoring_Service SHALL retain all previously captured Snapshots and Inspection_Results without modification or deletion.
7. IF the Frame_Source reports unavailability when the farmer requests to resume, THEN THE Monitoring_Service SHALL transition the Monitoring_Session status to `error` and record a descriptive error reason indicating that the camera became unavailable during pause.

### Requirement 5: Session Completion

**User Story:** As a farmer, I want the system to finalize the monitoring and produce a metrics summary when the robot completes its traversal, so that I can review the results.

#### Acceptance Criteria

1. WHEN the monitoring traversal is signaled as complete via external trigger (API call) or the Frame_Source reports end-of-stream, THE Monitoring_Service SHALL transition the Monitoring_Session status from `running` to `finishing`.
2. WHILE the Monitoring_Session status is `finishing`, THE Monitoring_Service SHALL release the Frame_Source and compute Monitoring_Metrics from all persisted Inspection_Results associated with the session.
3. WHEN Monitoring_Metrics computation completes, THE Monitoring_Service SHALL persist the MonitoringMetrics entity via MonitoringMetricsRepository, including total_tomatoes, healthy_count, unhealthy_count, pct_healthy, pct_unhealthy, percentages by maturity stage (pct_green, pct_breaker, pct_turning, pct_pink, pct_light_red, pct_red), and snapshots_with_detections.
4. WHEN MonitoringMetrics persistence succeeds, THE Monitoring_Service SHALL transition the Monitoring_Session status from `finishing` to `completed` and set the `completed_at` field to the current UTC timestamp.
5. IF the monitoring session has zero Inspection_Results when transitioning to `finishing`, THEN THE Monitoring_Service SHALL persist MonitoringMetrics with all counts set to 0 and all percentages set to 0, and still transition to `completed`.

### Requirement 6: Session Abort

**User Story:** As a farmer, I want to stop the monitoring early and keep partial results, so that my time spent monitoring is not wasted.

#### Acceptance Criteria

1. WHEN the farmer requests to abort the monitoring, THE Monitoring_Service SHALL validate that the Monitoring_Session status is in a non-terminal state (`initializing`, `running`, or `paused`) and transition it to `aborted`.
2. IF the farmer requests to abort the monitoring while the Monitoring_Session is in a terminal state (`completed`, `aborted`, or `error`), THEN THE Monitoring_Service SHALL reject the request and return an error indicating the session is already terminated.
3. WHEN the Monitoring_Session transitions to `aborted`, THE Monitoring_Service SHALL release the Frame_Source resource if it is currently open.
4. WHEN the Monitoring_Session transitions to `aborted`, THE Monitoring_Service SHALL preserve all previously captured Snapshots and Inspection_Results without deletion.
5. WHEN the Monitoring_Session transitions to `aborted`, THE Monitoring_Service SHALL compute and persist partial Monitoring_Metrics from available Inspection_Results, setting all counts to 0 and percentages to 0 if no results exist.
6. WHEN the Monitoring_Session transitions to `aborted`, THE Monitoring_Service SHALL set the `completed_at` field to the current UTC timestamp.

### Requirement 7: Error Handling and Recovery

**User Story:** As a farmer, I want the system to handle failures gracefully and preserve my progress, so that equipment issues do not result in data loss.

#### Acceptance Criteria

1. IF a camera disconnection occurs (Frame_Source `read()` returns failure) while the Monitoring_Session is in `running` state, THEN THE Monitoring_Service SHALL transition the Monitoring_Session status to `error`, persist all Snapshots and Inspection_Results captured up to that point, and record the error reason.
2. IF the CPU temperature exceeds 80 degrees C during monitoring (as reported by the system thermal sensor), THEN THE Monitoring_Service SHALL transition the Monitoring_Session status from `running` to `paused` automatically and record the temperature event.
3. IF an unrecoverable error occurs (model crash, filesystem full, or database write failure) during `running` or `finishing` state, THEN THE Monitoring_Service SHALL transition the Monitoring_Session status to `error`, persist all Snapshots and Inspection_Results that were successfully written prior to the failure, and record the error reason.
4. THE Monitoring_Service SHALL delegate all status transitions to the MonitoringStatus value object, which SHALL reject any transition not defined in the state machine by raising a domain exception.
5. IF an error occurs during the `finishing` phase (metrics computation or persistence failure), THEN THE Monitoring_Service SHALL transition the Monitoring_Session status to `error` while preserving all previously persisted Snapshots and Inspection_Results, and record the error reason.

### Requirement 8: Background Task Execution

**User Story:** As a farmer, I want the monitoring to run in the background without blocking the interface, so that I can check progress or interact with other screens while monitoring runs.

#### Acceptance Criteria

1. WHEN the farmer starts a monitoring session via the API, THE Monitoring_Service SHALL execute the capture loop as a background task and return the HTTP response within 2 seconds of receiving the request.
2. THE Monitoring_Service SHALL expose the current Monitoring_Session status and counters (total_snapshots, total_detections) for polling by the presentation layer, updated after each snapshot is processed.
3. WHILE the background task is running, THE Monitoring_Service SHALL accept pause, resume, abort, and status query commands and acknowledge them within 2 seconds, without waiting for the capture loop to complete a cycle.
4. IF the background task crashes unexpectedly, THEN THE Monitoring_Service SHALL transition the Monitoring_Session to `error` status, persist any snapshots and InspectionResults captured before the failure, and record the failure reason.
5. THE Monitoring_Service SHALL ensure only one active monitoring session (status in `initializing`, `running`, or `paused`) exists per module at any given time, rejecting new session requests for that module until the existing session reaches a terminal state (`completed`, `aborted`, or `error`).
6. IF the background task receives a pause command, THEN THE Monitoring_Service SHALL stop capturing new snapshots within the current cycle and transition the Monitoring_Session to `paused` status within 5 seconds of command receipt.

### Requirement 9: API Endpoints for Session Control

**User Story:** As a farmer (via the UI), I want HTTP endpoints to start, pause, resume, abort, and query monitoring sessions, so that the touchscreen interface can control the monitoring flow.

#### Acceptance Criteria

1. THE API SHALL expose a POST endpoint to start a new monitoring session for a given module, accepting module_id (integer, must reference an existing module), width_m (float, greater than 0 and at most 1000), length_m (float, greater than 0 and at most 1000), and optional notes (string, maximum 500 characters) as parameters.
2. THE API SHALL expose a POST endpoint to pause an active monitoring session identified by monitoring_id, transitioning the session from `running` to `paused` status.
3. THE API SHALL expose a POST endpoint to resume a paused monitoring session identified by monitoring_id, transitioning the session from `paused` to `running` status.
4. THE API SHALL expose a POST endpoint to abort a monitoring session identified by monitoring_id, transitioning the session from `running` or `paused` to `aborted` status and persisting any partial results.
5. THE API SHALL expose a GET endpoint to retrieve the current status, counters (total_snapshots, total_detections), and state of a monitoring session by monitoring_id, returning results within 1 second.
6. THE API SHALL expose a POST endpoint to signal monitoring completion (traversal finished) by monitoring_id, transitioning the session from `running` to `finishing` and then to `completed` once metric aggregation is done.
7. IF a session control request targets a monitoring session in an incompatible state (per the state machine), THEN THE API SHALL return an HTTP 409 Conflict response with an error message indicating the current state and the disallowed transition.
8. IF a start request targets a module that already has an active (non-terminal: `initializing`, `running`, or `paused`) monitoring session, THEN THE API SHALL return an HTTP 409 Conflict response indicating that only one active session per module is allowed.
9. THE API SHALL validate that module_id references an existing module and that width_m and length_m are positive float values no greater than 1000 before creating a monitoring session, returning HTTP 422 with an error message indicating which parameter failed validation on failure.
10. IF a session control request references a monitoring_id that does not exist, THEN THE API SHALL return an HTTP 404 response with an error message indicating the session was not found.

### Requirement 10: Resource Management

**User Story:** As a system operator, I want the monitoring service to manage hardware resources (camera, memory, CPU) responsibly, so that the Raspberry Pi 5 remains stable during extended monitoring sessions.

#### Acceptance Criteria

1. WHEN the monitoring session reaches any terminal state (`completed`, `aborted`, or `error`), THE Monitoring_Service SHALL release the Frame_Source resource within 3 seconds of the state transition.
2. THE Monitoring_Service SHALL execute inference (detection, health classification, maturity estimation) only on captured snapshots triggered by the Scene Gate, not on every frame read from the camera.
3. WHILE the Monitoring_Session status is `running`, THE Monitoring_Service SHALL keep total process memory usage (models + frame buffer + SQLite) at or below 3 GB as measured by resident set size (RSS).
4. THE Monitoring_Service SHALL load Inference_Pipeline models (Detectron2 detector and ResNet-18 health classifier) once during session initialization and reuse them across all snapshots within the same monitoring session without reloading.
5. IF the Frame_Source is released, THEN THE Monitoring_Service SHALL ensure no further read attempts are made against the released resource and SHALL raise an error if any component attempts to read from it after release.
6. IF total process memory usage exceeds 3 GB during a running session, THEN THE Monitoring_Service SHALL log a warning and transition the session to `paused` status to allow the operator to assess system stability.
