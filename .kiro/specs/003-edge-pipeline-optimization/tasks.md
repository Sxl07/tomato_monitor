# Implementation Plan: Edge Pipeline Optimization

## Overview

This plan implements profile-based configuration, inference optimization, thermal management, and benchmarking for sustained Raspberry Pi 5 operation. The implementation order is: configuration (profiles) → component modifications → thermal monitor → integration → benchmark → documentation. All code can be developed on PC; hardware-dependent validation (thermal, benchmark) requires Raspberry Pi 5.

## Tasks

- [x] 1. Define ExecutionProfile dataclass and profile constants
  - [x] 1.1 Create the ExecutionProfile frozen dataclass in `src/infrastructure/config/settings.py`
    - Add `from dataclasses import dataclass` import
    - Define `ExecutionProfile` with all fields: name, camera_width, camera_height, camera_fps, inference_input_width, inference_input_height, skip_maturity, detection_score_threshold, run_maturity_only_for_healthy, scene_gate_cooldown_frames, scene_gate_timeout_frames, scene_gate_orb_threshold, scene_gate_hsv_threshold, thermal_poll_interval_seconds, thermal_warning_temp, thermal_critical_temp, thermal_resume_temp, memory_warning_rss_mb
    - Define `EDGE_PROFILE` constant with edge defaults (480×360 camera, 416×312 inference, skip_maturity=True, threshold=0.85, cooldown=30, timeout=75, thermal_critical=78.0)
    - Define `FULL_PROFILE` constant with full defaults (640×480 camera, 640×480 inference, skip_maturity=False, threshold=0.80, cooldown=18, timeout=45, thermal_critical=85.0)
    - Preserve existing `CAMERA_WIDTH`, `CAMERA_HEIGHT`, `CAMERA_FPS` constants as documentation of original defaults
    - _Requirements: 1.1, 1.2, 1.3, 8.1, 8.3_

  - [x] 1.2 Create the profile loader module `src/infrastructure/config/profile_loader.py`
    - Implement `load_profile(profile_name: str, **overrides) -> ExecutionProfile` that selects EDGE_PROFILE or FULL_PROFILE by name, applies keyword overrides via `dataclasses.replace()`, raises `ValueError` for invalid profile names
    - Implement `get_config_summary(profile: ExecutionProfile) -> dict[str, Any]` that returns a JSON-serializable dict of all profile fields
    - _Requirements: 1.3, 1.4, 1.5, 8.4, 8.5_

  - [ ]* 1.3 Write unit tests for ExecutionProfile and profile_loader
    - Test `load_profile("edge")` returns EDGE_PROFILE values
    - Test `load_profile("full")` returns FULL_PROFILE values
    - Test `load_profile("invalid")` raises ValueError with valid names listed
    - Test overrides are applied correctly via `load_profile("edge", skip_maturity=False)`
    - Test `get_config_summary` returns all expected keys
    - _Requirements: 1.5, 8.4_

- [x] 2. Modify SnapshotInferenceRunner for configurable inference
  - [x] 2.1 Add configurable parameters to SnapshotInferenceRunner constructor
    - Add keyword-only parameters: `skip_maturity: bool = False`, `detection_score_threshold: float = 0.80`, `inference_input_size: tuple[int, int] | None = None`, `run_maturity_only_for_healthy: bool = True`
    - Store as instance attributes, remove dependency on module-level constants `DETECTION_SCORE_THRESHOLD` and `RUN_MATURITY_ONLY_FOR_HEALTHY`
    - _Requirements: 4.1, 4.3, 4.6, 2.4, 2.5, 8.2_

  - [x] 2.2 Implement inference input resize logic
    - In `run_inference()`, if `self._inference_input_size` is set and differs from actual frame dimensions, create a resized copy using `cv2.resize(frame, inference_input_size, interpolation=cv2.INTER_LINEAR)` for detection
    - The original frame is preserved unchanged for cropping and storage
    - _Requirements: 2.5, 2.6_

  - [x] 2.3 Implement skip_maturity logic
    - When `self._skip_maturity` is True, skip the maturity estimation block entirely; set `maturity_stage = None` and `maturity_percent = None` in results
    - Use `self._detection_score_threshold` instead of the imported constant for filtering detections
    - Use `self._run_maturity_only_for_healthy` instead of the module-level constant
    - _Requirements: 4.1, 4.2, 4.3_

  - [ ]* 2.4 Write unit tests for SnapshotInferenceRunner modifications
    - Test that `skip_maturity=True` produces None values for maturity fields
    - Test that `detection_score_threshold=0.90` filters out lower-confidence detections
    - Test that `inference_input_size` resizes the frame before detection (mock detector)
    - Test backward compatibility: constructor with only (detector, health_model, health_transform) still works
    - _Requirements: 4.1, 4.2, 4.3, 2.5_

- [x] 3. Extend Scene Gate for configurable parameters
  - [x] 3.1 Extend `should_capture_new_image()` to accept keyword arg overrides
    - Add optional keyword parameters: `cooldown_frames`, `timeout_frames`, `orb_threshold`, `hsv_threshold`
    - Default values remain current constants (MIN_FRAMES_BETWEEN_CAPTURES, MAX_FRAMES_WITHOUT_CAPTURE, ORB_MIN_MATCH_COUNT, HSV_HIST_DIFF_THRESHOLD)
    - Use the passed values in the logic instead of module-level constants
    - Preserve backward compatibility: existing callers without keyword args get current defaults
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 8.2_

  - [ ]* 3.2 Write unit tests for Scene Gate parameter overrides
    - Test that higher cooldown_frames delays capture
    - Test that custom orb_threshold and hsv_threshold are used
    - Test backward compatibility: call without keyword args works as before
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 4. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement ThermalMonitor
  - [x] 5.1 Create `src/infrastructure/monitoring/thermal_monitor.py`
    - Create the `src/infrastructure/monitoring/` directory with `__init__.py`
    - Implement `ThermalMonitor` class with constructor accepting: `pause_event: threading.Event`, `poll_interval_seconds`, `warning_temp`, `critical_temp`, `resume_temp`
    - Implement `start()` → spawns daemon thread; `stop()` → signals thread to exit
    - Read temperature via `subprocess.run(["vcgencmd", "measure_temp"], capture_output=True)`, parse float from `temp=XX.X'C`
    - If `vcgencmd` is not available: log info once, thread exits cleanly (no-op mode for PC development)
    - When temp ≥ critical_temp: set pause_event; when temp drops below resume_temp and pause_event is set: clear pause_event
    - When temp ≥ warning_temp but < critical_temp: log warning, continue
    - Track `peak_temperature`, `pause_count`, `total_pause_duration_seconds`
    - Implement `get_session_metadata() -> dict` returning thermal stats
    - At start, check current temp; log warning if already above warning_temp (proxy for cooling detection)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

  - [ ]* 5.2 Write unit tests for ThermalMonitor
    - Mock `subprocess.run` to simulate temperature readings
    - Test that critical temp triggers pause_event.set()
    - Test that resume temp clears pause_event
    - Test that peak_temperature and pause_count are tracked correctly
    - Test graceful no-op when vcgencmd is unavailable (FileNotFoundError)
    - Test `get_session_metadata()` returns expected keys
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.7_

- [x] 6. Integrate profile into MonitoringWorker
  - [x] 6.1 Pass Scene Gate parameters from profile to `should_capture_new_image()` in MonitoringWorker
    - Modify `MonitoringWorker.__init__()` to accept scene gate params (cooldown_frames, timeout_frames, orb_threshold, hsv_threshold) or an ExecutionProfile
    - In `_should_capture()`, pass the configured thresholds as keyword arguments to `should_capture_new_image()`
    - _Requirements: 3.5, 3.6, 1.1, 1.2_

  - [x] 6.2 Integrate ThermalMonitor into MonitoringWorker
    - Accept an optional `ThermalMonitor` instance in the constructor
    - Start the ThermalMonitor at the beginning of `run()` and stop it in `_release_resources()`
    - The ThermalMonitor uses the existing `self.pause_event` to pause/resume the capture loop (already handled by the spin-wait)
    - _Requirements: 5.3, 5.4_

  - [x] 6.3 Add RSS memory check after inference
    - After `_process_snapshot()` completes, check current process RSS using `os.getpid()` and reading `/proc/{pid}/status` or `resource.getrusage()`
    - If RSS exceeds the configured `memory_warning_rss_mb`, log a warning with current usage
    - Explicitly release the frame reference after processing (`del frame` pattern or set to None)
    - _Requirements: 6.2, 6.3, 6.5_

  - [ ]* 6.4 Write unit tests for MonitoringWorker integration
    - Test that Scene Gate params are passed to `should_capture_new_image()`
    - Test that ThermalMonitor is started/stopped with the worker
    - Test that memory warning is logged when RSS exceeds budget (mock resource reading)
    - _Requirements: 3.5, 5.3, 6.5_

- [x] 7. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Create benchmark script
  - [x] 8.1 Create `scripts/benchmarks/benchmark_edge_profiles.py`
    - Accept CLI args: `--images` (path to test images), `--output` (markdown output path, default `docs/benchmarks/edge-profile-comparison.md`)
    - Load test images from the specified directory
    - For each profile ("edge" and "full"): load models once, run inference on all images, measure per-snapshot wall-clock time
    - Measure peak RSS via `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` or `psutil.Process().memory_info().rss`
    - Read temperature at start, after each snapshot, and at end (graceful no-op on non-RPi)
    - Compute throughput: snapshots/minute
    - Record device info: `platform.machine()`, OS, git commit hash (via `subprocess`), datetime
    - Output a markdown report following `docs/benchmarks/benchmark-template.md` format
    - Include the config summary from `get_config_summary()` in the output
    - **⚠️ Hardware-dependent: full benchmark requires Raspberry Pi 5 with active cooling**
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

  - [ ]* 8.2 Write unit test for benchmark script argument parsing and output formatting
    - Test CLI argument parsing
    - Test markdown report generation with mock data
    - Test that device info collection works on current platform
    - _Requirements: 7.5, 7.6_

- [x] 9. Create documentation
  - [x] 9.1 Create ADR for default behavior changes (`docs/decisions/ADR-005-edge-profile-defaults.md`)
    - Document the decision to make "edge" the default profile on Raspberry Pi
    - State context: sustained inference causes thermal throttling
    - Document consequences: maturity estimation skipped by default in edge mode, reduced capture frequency
    - Document trade-offs: fewer detections at higher threshold, no maturity data in edge mode
    - Reference Spec 003 and benchmark evidence (to be filled after benchmark execution)
    - _Requirements: 4.4, 4.5_

  - [x] 9.2 Create placeholder benchmark results document (`docs/benchmarks/edge-profile-comparison.md`)
    - Create the file with headers and structure from `benchmark-template.md`
    - Include "PENDING: Run on Raspberry Pi 5" placeholders for measured values
    - Document the command to run the benchmark: `python scripts/benchmarks/benchmark_edge_profiles.py --images data/images/`
    - **⚠️ Actual results require execution on Raspberry Pi 5 hardware**
    - _Requirements: 7.6, 7.7_

- [x] 10. Final checkpoint — Ensure all tests pass and code is importable
  - Ensure all tests pass, ask the user if questions arise.
  - Verify all new modules are importable without hardware-specific dependencies failing on PC
  - Confirm ThermalMonitor degrades gracefully on non-RPi platforms

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- **Hardware-dependent tasks:** Tasks 8.1 (benchmark execution) and 5.1 (thermal validation on real hardware) require Raspberry Pi 5. The code can be written and unit-tested on PC, but real measurements need RPi
- ThermalMonitor uses no-op mode on non-RPi platforms (graceful degradation)
- All optimizations remain configurable through the profile system — nothing is hardcoded
- The benchmark script compares against baseline from Spec 001

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "2.1"] },
    { "id": 2, "tasks": ["1.3", "2.2", "2.3", "3.1", "5.1"] },
    { "id": 3, "tasks": ["2.4", "3.2", "5.2"] },
    { "id": 4, "tasks": ["6.1", "6.2", "6.3"] },
    { "id": 5, "tasks": ["6.4"] },
    { "id": 6, "tasks": ["8.1", "9.1", "9.2"] },
    { "id": 7, "tasks": ["8.2"] }
  ]
}
```
