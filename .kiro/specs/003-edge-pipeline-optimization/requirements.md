# Requirements Document

## Introduction

This spec defines optimization requirements for the Tomato Monitor live monitoring flow targeting sustained operation on Raspberry Pi 5. The system captures snapshots from a live camera, evaluates a Scene Gate for change detection, runs per-snapshot inference (Detectron2 + ResNet-18 + maturity estimation), and produces agricultural reports. The optimization goal is to reduce per-snapshot inference time, manage thermal behavior, and control memory usage during sustained monitoring sessions — without replacing core model components.

The system has evolved from a video processing pipeline to a live camera monitoring system:
- **Previous flow**: Video file → frame-by-frame processing → annotated video output
- **Current flow**: Live camera → Scene Gate evaluation → snapshot capture → per-snapshot inference → agricultural report

All optimizations must be evidence-based (ADR-003), configurable, and documented with before/after benchmark data for thesis reproducibility.

## Glossary

- **Optimizer**: The configuration and runtime subsystem that applies edge-specific settings to reduce resource consumption during monitoring sessions
- **Monitoring_Worker**: The background thread component (`MonitoringWorker`) that runs the capture loop, evaluates the Scene Gate, and triggers snapshot inference
- **Scene_Gate**: The change detection mechanism (`capture_gate.py`) using ORB features and HSV histogram comparison to decide when to capture a new snapshot
- **Inference_Runner**: The `SnapshotInferenceRunner` component that executes detector + health classifier + maturity estimator on a single captured image
- **Execution_Profile**: A named configuration preset ("edge" or "full") that controls resolution, inference options, thermal thresholds, and artifact generation
- **Thermal_Monitor**: The subsystem responsible for reading CPU temperature and triggering pause/resume behavior when thresholds are exceeded
- **Frame_Source**: The camera abstraction (`RaspberryCameraFrameSource`) that provides frames at configured resolution and FPS
- **Benchmark_Runner**: The script or tool that measures inference time, memory usage, temperature, and throughput for a given configuration

## Requirements

### Requirement 1: Execution Profiles

**User Story:** As a thesis developer, I want to select between "edge" and "full" execution profiles, so that the system applies appropriate defaults for Raspberry Pi 5 or desktop environments.

#### Acceptance Criteria

1. WHEN the Execution_Profile is set to "edge", THE Optimizer SHALL apply conservative defaults for camera resolution, inference options, and Scene Gate parameters suitable for sustained Raspberry Pi 5 operation
2. WHEN the Execution_Profile is set to "full", THE Optimizer SHALL apply desktop defaults that preserve all inference stages and higher resolution
3. THE Optimizer SHALL load the active Execution_Profile from the centralized configuration in `settings.py`
4. WHEN the Execution_Profile is changed, THE Optimizer SHALL apply the new settings without requiring application restart if the monitoring session has not started
5. IF an invalid Execution_Profile name is provided, THEN THE Optimizer SHALL raise a configuration error with the valid profile names listed

### Requirement 2: Camera Resolution Optimization

**User Story:** As a thesis developer, I want to configure camera capture resolution independently from inference input size, so that I can reduce computational load while preserving capture quality for documentation.

#### Acceptance Criteria

1. THE Frame_Source SHALL accept configurable width and height parameters for camera capture resolution
2. WHEN the Execution_Profile is set to "edge", THE Frame_Source SHALL default to a reduced capture resolution (480×360 or lower, configurable)
3. WHEN the Execution_Profile is set to "full", THE Frame_Source SHALL default to 640×480 capture resolution
4. THE Inference_Runner SHALL accept a configurable inference input size parameter independent of capture resolution
5. WHEN the inference input size differs from the capture resolution, THE Inference_Runner SHALL resize the snapshot before passing it to the detector
6. THE Optimizer SHALL preserve the original capture resolution image for storage and documentation regardless of inference input size

### Requirement 3: Scene Gate Parameter Tuning

**User Story:** As a thesis developer, I want to configure Scene Gate parameters per execution profile, so that I can control snapshot capture frequency for thermal management.

#### Acceptance Criteria

1. THE Scene_Gate SHALL accept configurable cooldown frames (minimum frames between captures, current default: 18)
2. THE Scene_Gate SHALL accept a configurable timeout parameter (maximum frames without capture, current default: 45)
3. THE Scene_Gate SHALL accept a configurable ORB match threshold (current default: 35)
4. THE Scene_Gate SHALL accept a configurable HSV histogram difference threshold (current default: 0.38)
5. WHEN the Execution_Profile is set to "edge", THE Scene_Gate SHALL use higher cooldown and timeout values to reduce capture frequency
6. WHEN the Execution_Profile is set to "full", THE Scene_Gate SHALL use the current default parameters (cooldown=18, timeout=45)

### Requirement 4: Inference Optimization

**User Story:** As a thesis developer, I want to reduce per-snapshot inference time on Raspberry Pi 5 without replacing Detectron2, so that the system can sustain monitoring without thermal throttling.

#### Acceptance Criteria

1. WHEN the Execution_Profile is set to "edge", THE Inference_Runner SHALL skip maturity estimation to reduce inference time
2. WHEN maturity estimation is disabled, THE Inference_Runner SHALL return null values for maturity_stage and maturity_percent fields
3. THE Inference_Runner SHALL accept a configurable detection score threshold (current default: 0.80) that can be increased in edge mode to reduce post-processing on low-confidence detections
4. WHERE the detection score threshold is configured above 0.80, THE Inference_Runner SHALL document the expected reduction in detected tomatoes as a trade-off
5. THE Optimizer SHALL NOT replace Detectron2 with an alternative detector without comparative benchmark evidence documented in an ADR (ADR-003)
6. THE Inference_Runner SHALL load models once at session start and reuse them across all snapshots within a monitoring session

### Requirement 5: Thermal Management

**User Story:** As a thesis developer, I want the system to monitor CPU temperature and auto-pause monitoring when thermal limits are approached, so that the Raspberry Pi 5 operates within safe thermal bounds during extended sessions.

#### Acceptance Criteria

1. THE Thermal_Monitor SHALL read CPU temperature at configurable intervals during active monitoring
2. WHEN the CPU temperature exceeds a configurable warning threshold, THE Thermal_Monitor SHALL log a warning and continue operation
3. WHEN the CPU temperature exceeds a configurable critical threshold, THE Thermal_Monitor SHALL pause the Monitoring_Worker capture loop automatically
4. WHILE the Monitoring_Worker is paused due to thermal limits, THE Thermal_Monitor SHALL resume operation when the temperature drops below a configurable resume threshold
5. THE Thermal_Monitor SHALL record peak temperature, pause count, and total pause duration as monitoring session metadata
6. IF active cooling is not detected at monitoring start, THEN THE Thermal_Monitor SHALL log a warning indicating that sustained inference requires active cooling
7. THE Thermal_Monitor SHALL expose configurable thresholds: warning temperature, critical temperature, and resume temperature

### Requirement 6: Memory Optimization

**User Story:** As a thesis developer, I want to control memory usage during sustained monitoring sessions, so that the system stays within the 3 GB RSS budget on Raspberry Pi 5.

#### Acceptance Criteria

1. THE Inference_Runner SHALL load detector and health models once per monitoring session, not per snapshot
2. THE Monitoring_Worker SHALL release captured frame buffers after inference completes for each snapshot
3. WHEN the Execution_Profile is set to "edge", THE Inference_Runner SHALL disable any intermediate result caching that exceeds the memory budget
4. THE Optimizer SHALL document the measured RSS memory usage for each profile configuration in benchmark reports
5. IF the system RSS memory exceeds 3 GB during a monitoring session, THEN THE Monitoring_Worker SHALL log a memory warning with current usage

### Requirement 7: Benchmark Comparison

**User Story:** As a thesis developer, I want to measure and document before/after metrics for every optimization applied, so that each change is justified with reproducible evidence for the thesis.

#### Acceptance Criteria

1. THE Benchmark_Runner SHALL measure per-snapshot inference time (detector + health + maturity) for both "edge" and "full" profiles
2. THE Benchmark_Runner SHALL measure peak RSS memory during a monitoring session for both profiles
3. THE Benchmark_Runner SHALL measure peak CPU temperature during sustained inference for both profiles
4. THE Benchmark_Runner SHALL measure snapshot throughput (snapshots processed per minute) for both profiles
5. THE Benchmark_Runner SHALL record device information (model, OS version, git commit, date) with every benchmark result
6. WHEN a benchmark is executed, THE Benchmark_Runner SHALL output results in the format defined by `docs/benchmarks/benchmark-template.md`
7. THE Benchmark_Runner SHALL compare edge profile results against the baseline measurements from Spec 001

### Requirement 8: Configuration Architecture

**User Story:** As a thesis developer, I want all optimization parameters to be centralized and configurable, so that no optimization is hardcoded and every change can be reverted.

#### Acceptance Criteria

1. THE Optimizer SHALL define all edge-specific parameters in `src/infrastructure/config/settings.py` with clear defaults per profile
2. THE Optimizer SHALL NOT hardcode optimization values in vision module source code
3. WHEN a parameter is modified for optimization purposes, THE Optimizer SHALL preserve the original default value as documentation
4. THE Optimizer SHALL expose a configuration summary function that reports active profile, resolution, Scene Gate parameters, thermal thresholds, and inference options
5. WHEN results are generated, THE Optimizer SHALL include the active configuration snapshot in monitoring session metadata for traceability

## Out of Scope

- Replacing Detectron2 with an alternative detector (requires separate ADR with benchmark evidence)
- Integrating AI Camera NPU for inference acceleration (ADR-002 pending validation)
- Model quantization implementation (INT8/FP16) — research feasibility only, no implementation in this spec
- Training new or fine-tuned models
- Cloud offloading or remote inference
- Modifying the agricultural report output format
- Robot motor control or traversal optimization
- Changes to the legacy video processing pipeline

