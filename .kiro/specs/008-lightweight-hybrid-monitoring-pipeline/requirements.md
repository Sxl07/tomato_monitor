# Requirements Document

## Introduction

This specification defines a lightweight hybrid monitoring pipeline for the Tomato Monitor system running on Raspberry Pi 5. The goal is to reduce CPU load, temperature, and resource consumption during live monitoring sessions by controlling loop frequency, gating snapshot capture via scene analysis, and routing all tunable parameters through execution profiles. The critical design principle is that inference runs ONLY on snapshots selected by the Scene Gate or timeout — never on every frame.

## Glossary

- **Monitoring_Worker**: The background thread component that runs the capture loop, evaluates the Scene Gate, captures snapshots, and triggers inference (`src/application/services/monitoring_worker.py`)
- **Scene_Gate**: The lightweight frame comparison subsystem that decides when to capture a snapshot based on ORB feature matching and HSV histogram difference (`src/infrastructure/vision/capture_gate.py`)
- **Inference_Runner**: The component that executes detection, health classification, and maturity estimation on a single snapshot image (`src/infrastructure/vision/snapshot_inference_runner.py`)
- **Execution_Profile**: A frozen dataclass containing all tunable parameters for edge vs full mode operation (`src/infrastructure/config/settings.py`)
- **ACTIVE_PROFILE**: The currently selected execution profile that determines runtime behavior
- **Thermal_Monitor**: The background thread that reads CPU temperature and pauses/resumes the worker when thermal thresholds are exceeded (`src/infrastructure/monitoring/thermal_monitor.py`)
- **Pipeline_Stage**: A discrete measurable step within the monitoring loop (camera read, scene gate evaluation, snapshot save, detection, health classification, maturity estimation, DB persistence)
- **Snapshot_Reason**: The cause that triggered a particular snapshot capture (first_frame, scene_change, timeout)
- **Cooldown_Period**: The minimum time or frame interval that must elapse between consecutive snapshots
- **Timeout_Force**: A maximum time or frame interval after which a snapshot is forced regardless of scene change

## Requirements

### Requirement 1: Performance Instrumentation

**User Story:** As a thesis researcher, I want the system to measure and log the time spent in each pipeline stage during monitoring, so that I can identify bottlenecks and compare configurations with reproducible evidence.

#### Acceptance Criteria

1. WHEN a frame is read from the camera, THE Monitoring_Worker SHALL record the elapsed time for the camera read operation in milliseconds
2. WHEN the Scene_Gate evaluates a frame, THE Monitoring_Worker SHALL record the elapsed time for scene gate evaluation in milliseconds
3. WHEN a snapshot image is saved to the filesystem, THE Monitoring_Worker SHALL record the elapsed time for the save operation in milliseconds
4. WHEN the Inference_Runner executes on a snapshot, THE Monitoring_Worker SHALL record the total inference time, detection time, health classification time, and maturity estimation time individually in milliseconds
5. WHEN the Monitoring_Worker persists results to the database, THE Monitoring_Worker SHALL record the elapsed time for the persistence operation in milliseconds
6. THE Monitoring_Worker SHALL record the current CPU temperature at each snapshot cycle
7. WHEN a monitoring session ends, THE Monitoring_Worker SHALL include the peak temperature observed during the session in session metadata
8. THE Monitoring_Worker SHALL record approximate RSS memory usage in megabytes at each snapshot cycle
9. THE Monitoring_Worker SHALL record the cumulative snapshots-per-minute and inferences-per-minute rates at each snapshot cycle
10. WHEN a snapshot is captured, THE Monitoring_Worker SHALL log the Snapshot_Reason (first_frame, scene_change, or timeout) for that capture
11. THE Monitoring_Worker SHALL emit all pipeline stage timings and metrics to the technical log at DEBUG or INFO level

### Requirement 2: Loop Frequency Control

**User Story:** As a thesis researcher, I want to control the capture loop frequency and snapshot timing using real elapsed time, so that the system behaves predictably even when CPU load causes frame rate variation.

#### Acceptance Criteria

1. THE Monitoring_Worker SHALL support a configurable capture_loop_fps parameter that limits the number of frames read per second
2. THE Monitoring_Worker SHALL support a configurable min_seconds_between_snapshots parameter that enforces a minimum real-time interval between consecutive snapshots
3. THE Monitoring_Worker SHALL support a configurable max_seconds_without_snapshot parameter that forces a snapshot after a maximum real-time interval of inactivity
4. WHEN evaluating snapshot timing, THE Monitoring_Worker SHALL use wall-clock elapsed time (not frame counts) as the primary timing mechanism
5. THE Monitoring_Worker SHALL maintain backward compatibility with the existing frame-based cooldown_frames and timeout_frames parameters from the Execution_Profile
6. WHEN both time-based and frame-based thresholds are configured, THE Monitoring_Worker SHALL use whichever threshold is reached first as the trigger condition
7. IF the actual frame rate drops below the configured capture_loop_fps, THEN THE Monitoring_Worker SHALL log the deviation and continue operating at the reduced rate without error

### Requirement 3: Lightweight Scene Gate

**User Story:** As a thesis researcher, I want the Scene Gate to efficiently decide when to capture snapshots using lightweight frame comparison at reduced resolution, so that the system avoids unnecessary inference while detecting meaningful scene changes.

#### Acceptance Criteria

1. THE Scene_Gate SHALL compare frames at a reduced resolution for scene evaluation while preserving full resolution for snapshot capture and inference
2. THE Scene_Gate SHALL use the existing ORB feature matching and HSV histogram comparison approach for scene change detection
3. WHEN a snapshot is triggered by scene change, THE Scene_Gate SHALL log the gate metrics: orb_matches count, hist_diff value, cooldown_ok status, timeout_force status, and trigger_reason
4. WHEN no significant scene change occurs within the max_seconds_without_snapshot interval, THE Scene_Gate SHALL force a snapshot capture with trigger_reason set to "timeout"
5. THE Scene_Gate SHALL respect the min_seconds_between_snapshots cooldown to prevent excessive snapshot capture from sensor noise
6. THE Scene_Gate SHALL accept configurable thresholds for ORB match count and HSV histogram difference from the ACTIVE_PROFILE
7. THE Scene_Gate SHALL NOT trigger inference execution — the Scene_Gate only decides when to capture a snapshot, and inference runs on the captured snapshot separately

### Requirement 4: Execution Profile Integration

**User Story:** As a thesis researcher, I want the monitoring flow to use parameters from the ACTIVE_PROFILE consistently, so that switching between edge and full profiles changes all relevant behavior without code modifications.

#### Acceptance Criteria

1. WHEN a monitoring session starts, THE Monitoring_Worker SHALL read camera resolution (camera_width, camera_height) from the ACTIVE_PROFILE and pass them to the frame source
2. WHEN a monitoring session starts, THE Monitoring_Worker SHALL read camera_fps and capture_loop_fps from the ACTIVE_PROFILE
3. WHEN a monitoring session starts, THE Monitoring_Worker SHALL read inference input size from the ACTIVE_PROFILE and pass the value to the Inference_Runner
4. WHEN a monitoring session starts, THE Monitoring_Worker SHALL read skip_maturity and run_maturity_only_for_healthy flags from the ACTIVE_PROFILE
5. WHEN a monitoring session starts, THE Monitoring_Worker SHALL read detection_score_threshold from the ACTIVE_PROFILE
6. WHEN a monitoring session starts, THE Monitoring_Worker SHALL read scene gate parameters (cooldown, timeout, orb_threshold, hsv_threshold, gate_resolution) from the ACTIVE_PROFILE
7. WHEN a monitoring session starts, THE Thermal_Monitor SHALL read thermal parameters (poll_interval, warning_temp, critical_temp, resume_temp) from the ACTIVE_PROFILE
8. WHEN a monitoring session starts, THE Monitoring_Worker SHALL read memory_warning_rss_mb from the ACTIVE_PROFILE
9. THE Execution_Profile SHALL include time-based parameters (min_seconds_between_snapshots, max_seconds_without_snapshot, capture_loop_fps) alongside the existing frame-based parameters
10. THE Execution_Profile SHALL include camera parameters (camera_width, camera_height, camera_fps) for connecting resolution and FPS to the camera frame source
11. THE system SHALL select the ACTIVE_PROFILE based on the environment variable TOMATO_MONITOR_PROFILE with values "edge" or "full"
12. THE default value for TOMATO_MONITOR_PROFILE SHALL be "edge" when the variable is not set or is empty
13. IF TOMATO_MONITOR_PROFILE contains an invalid value, THEN THE system SHALL use the edge profile and log a WARNING indicating the invalid value was ignored

### Requirement 5: Edge Mode Operation

**User Story:** As a thesis researcher, I want to run monitoring in an edge-optimized mode that reduces resource usage measurably, so that I can compare performance characteristics between full and edge configurations.

#### Acceptance Criteria

1. THE EDGE_PROFILE SHALL configure a lower camera resolution than the FULL_PROFILE
2. THE EDGE_PROFILE SHALL configure a reduced inference input size compared to the FULL_PROFILE
3. THE EDGE_PROFILE SHALL configure a lower capture loop FPS than the FULL_PROFILE
4. THE EDGE_PROFILE SHALL disable maturity estimation (skip_maturity=True) to reduce inference cost
5. THE EDGE_PROFILE SHALL configure a higher detection_score_threshold to reduce false positives at the cost of sensitivity
6. THE EDGE_PROFILE SHALL configure more conservative thermal thresholds (lower warning and critical temperatures) than the FULL_PROFILE
7. WHEN switching from FULL_PROFILE to EDGE_PROFILE, THE system SHALL produce measurably lower average inference time per snapshot
8. WHEN switching from FULL_PROFILE to EDGE_PROFILE, THE system SHALL produce a measurably lower peak temperature during monitoring sessions of equivalent duration

### Requirement 6: Inference Exclusivity on Snapshots

**User Story:** As a thesis researcher, I want to guarantee that inference runs only on selected snapshots and never on every captured frame, so that CPU resources are reserved for meaningful analysis.

#### Acceptance Criteria

1. THE Monitoring_Worker SHALL execute inference ONLY on frames that the Scene_Gate has selected as snapshots
2. THE Monitoring_Worker SHALL NOT execute inference on frames used solely for scene gate evaluation
3. WHEN a snapshot is captured, THE Monitoring_Worker SHALL run inference on that snapshot and persist results before evaluating the next frame
4. THE Monitoring_Worker SHALL NOT batch snapshots for deferred inference at the end of the session — each snapshot is processed inline to provide partial feedback during monitoring
5. IF inference fails on a single snapshot, THEN THE Monitoring_Worker SHALL log the error and continue capturing subsequent snapshots without terminating the session

### Requirement 7: Session Lifecycle Stability

**User Story:** As a farmer, I want the monitoring system to remain stable across start, preview, stop, and consecutive sessions, so that I can rely on it during daily greenhouse operations.

#### Acceptance Criteria

1. THE system SHALL start monitoring sessions correctly with camera preview visible before monitoring begins
2. THE system SHALL display camera preview both before and after monitoring sessions without errors
3. WHEN the farmer presses the stop button, THE system SHALL release the camera and redirect to the appropriate screen within 15 seconds
4. THE system SHALL support two consecutive monitoring sessions without requiring an application restart
5. WHEN a monitoring session is aborted, THE system SHALL preserve all snapshots, detections, and partial metrics captured up to the abort point
6. IF a Picamera2 allocator or lock error occurs during abort, THEN THE system SHALL handle the error gracefully and mark the session as error state without crashing the application

### Requirement 8: Scope Constraints

**User Story:** As the development team, I want explicit boundaries on what this spec changes, so that unrelated subsystems remain stable.

#### Acceptance Criteria

1. THE system SHALL NOT modify robot movement logic or motor control code in any implementation of this specification
2. THE system SHALL NOT redesign the user interface broadly — only minimal performance indicator additions (snapshot count, temperature warning) are permitted on the monitoring execution screen
3. THE system SHALL NOT break the stable camera lifecycle (preview, abort, consecutive monitorings) validated in prior specifications
4. THE system SHALL NOT run inference on every frame — inference executes only on selected snapshots as defined in Requirement 6
5. THE system SHALL NOT batch all snapshots for inference at the end of a session — partial feedback during monitoring is mandatory as defined in Requirement 6

### Requirement 9: Inference Timing Decomposition

**User Story:** As a thesis researcher, I want individual timing measurements for each sub-stage of inference (detection, health, maturity), so that I can identify which component dominates CPU cost on Raspberry Pi.

#### Acceptance Criteria

1. THE Inference_Runner SHALL return per-stage timing alongside detection results in a backward-compatible manner (existing callers that ignore timing must continue to work)
2. THE Inference_Runner SHALL measure detection_ms as the elapsed time for the Detectron2 detector call
3. THE Inference_Runner SHALL measure health_ms as the total elapsed time for all health classification calls in one snapshot
4. THE Inference_Runner SHALL measure maturity_ms as the total elapsed time for all maturity estimation calls in one snapshot (0.0 if skipped)
5. THE Inference_Runner SHALL measure inference_total_ms as the wall-clock time from start to end of run_inference() including all sub-stages
6. THE system SHALL NOT register approximate or fabricated timing values — all timing fields must come from actual time.perf_counter() measurements

### Requirement 10: Metrics JSON Output for Thesis

**User Story:** As a thesis researcher, I want a JSON file with pipeline metrics written at the end of each monitoring session, so that I can include reproducible performance data in my thesis without depending on log parsing.

#### Acceptance Criteria

1. WHEN a monitoring session ends (completed or aborted), THE system SHALL write a JSON file to `outputs/monitorings/{monitoring_id}/pipeline_metrics.json`
2. THE JSON file SHALL include: profile_name (edge or full), snapshots_per_minute, inferences_per_minute, peak_temperature_c, peak_rss_mb, total_cycles, average timing per stage (camera_read_ms, scene_gate_ms, snapshot_save_ms, inference_total_ms, detection_ms, health_ms, maturity_ms, persistence_ms), and snapshot_reasons breakdown (count per reason type)
3. THE system SHALL NOT require database migration to support this output — the JSON file is written to the filesystem only
4. THE JSON file SHALL be written even when the session is aborted, containing metrics for all completed cycles up to the abort point

