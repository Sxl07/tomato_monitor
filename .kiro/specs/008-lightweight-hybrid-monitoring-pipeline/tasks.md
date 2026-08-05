# Implementation Plan: Lightweight Hybrid Monitoring Pipeline

## Overview

Additive changes to the monitoring worker organized in 7 phases: configuration first, then metrics, then gate logic, then throttling, then inference wiring, then thermal/memory, then manual validation. Property-based tests are optional; unit tests for core logic are mandatory.

## Tasks

- [x] 1. Fase A — Configuración de perfil + tests
  - [x] 1.1 Extend ExecutionProfile with new fields and env var selection
    - Add fields to ExecutionProfile: `capture_loop_fps`, `min_seconds_between_snapshots`, `max_seconds_without_snapshot`, `gate_resolution`, `camera_width`, `camera_height`, `camera_fps`
    - Update EDGE_PROFILE values: `capture_loop_fps=2.0`, `min_seconds_between_snapshots=8.0`, `max_seconds_without_snapshot=30.0`, `gate_resolution=(160,160)`, `camera_width=480`, `camera_height=360`, `camera_fps=5`
    - Update FULL_PROFILE values: `capture_loop_fps=5.0`, `min_seconds_between_snapshots=4.0`, `max_seconds_without_snapshot=15.0`, `gate_resolution=(320,320)`, `camera_width=640`, `camera_height=480`, `camera_fps=10`
    - Add TOMATO_MONITOR_PROFILE env var reading: default "edge", "full" selects FULL_PROFILE, invalid → warning + edge
    - File: `src/infrastructure/config/settings.py`
    - _Requirements: 4.9, 4.10, 4.11, 4.12, 4.13, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [x] 1.2 Write unit tests for ExecutionProfile and profile selection (MANDATORY)
    - Verify both profiles instantiate with all required fields
    - Verify EDGE_PROFILE has more conservative values than FULL_PROFILE
    - Verify TOMATO_MONITOR_PROFILE="edge" selects EDGE_PROFILE
    - Verify TOMATO_MONITOR_PROFILE="full" selects FULL_PROFILE
    - Verify invalid value defaults to edge with warning log
    - Verify unset variable defaults to edge
    - File: `tests/unit/test_execution_profile.py`
    - _Requirements: 4.11, 4.12, 4.13_

  - [x] 1.3 Write smoke test: app starts with both profiles
    - Script that imports settings and verifies ACTIVE_PROFILE is valid for both env values
    - File: `scripts/smoke_test_profiles.py`
    - _Requirements: 4.11_

- [x] 2. Fase B — PipelineMetrics + salida JSON + tests
  - [x] 2.1 Create PipelineMetrics module
    - Implement CycleMetrics frozen dataclass with all timing fields + snapshot_reason + temperature + rss
    - Implement PipelineMetrics accumulator with add_cycle(), snapshots_per_minute(), inferences_per_minute(), get_session_summary()
    - Implement to_json_file(output_path, profile_name) that writes summary JSON
    - Validate snapshot_reason is one of "first_frame", "scene_change", "timeout"
    - File: `src/application/services/pipeline_metrics.py`
    - _Requirements: 1.1-1.11, 10.1, 10.2, 10.3, 10.4_

  - [x] 2.2 Write unit tests for PipelineMetrics (MANDATORY)
    - Test add_cycle() with concrete inputs
    - Test snapshots_per_minute() and inferences_per_minute() with known values
    - Test get_session_summary() returns expected structure
    - Test to_json_file() produces valid JSON with required keys
    - Test edge cases: zero cycles, all None temperatures, invalid snapshot_reason raises ValueError
    - File: `tests/unit/test_pipeline_metrics.py`
    - _Requirements: 1.7, 1.9, 1.10, 10.1, 10.2_

  - [x] 2.3 Write property tests for PipelineMetrics (OPTIONAL — Properties 1-4)
    - Property 1: Stage timing accuracy
    - Property 2: Peak temperature max invariant
    - Property 3: Throughput rate calculation
    - Property 4: Snapshot reason validity
    - File: `tests/properties/test_pipeline_metrics_properties.py`

- [x] 3. Fase C — gate_resolution + decisión por tiempo + tests
  - [x] 3.1 Add configurable gate_resolution to CaptureGate
    - Add `gate_resolution: Optional[tuple[int,int]]` parameter to `should_capture_new_image()` and `preprocess_for_scene_compare()`
    - If provided, resize to gate_resolution instead of hardcoded (320, 320)
    - Verify metrics dict still contains all required keys
    - File: `src/infrastructure/vision/capture_gate.py`
    - _Requirements: 3.1, 3.6_

  - [x] 3.2 Add time-based snapshot decision logic to MonitoringWorker
    - Add constructor params: `min_seconds_between_snapshots`, `max_seconds_without_snapshot` from profile
    - Track `_last_snapshot_time` with `time.monotonic()`
    - Combine time-based + frame-based thresholds with "first wins" rule
    - Determine snapshot_reason: "first_frame", "scene_change", or "timeout"
    - Preserve existing frame-based cooldown/timeout as fallback
    - File: `src/application/services/monitoring_worker.py`
    - _Requirements: 2.2, 2.3, 2.4, 2.5, 2.6, 3.4, 3.5_

  - [x] 3.3 Write unit tests for snapshot decision logic (MANDATORY)
    - Test time-based cooldown blocks premature snapshot
    - Test time-based timeout forces snapshot with reason "timeout"
    - Test first-wins logic with various combinations
    - Test first frame always captures with reason "first_frame"
    - File: `tests/unit/test_snapshot_decision.py`
    - _Requirements: 2.2, 2.3, 2.5, 2.6_

  - [x] 3.4 Write unit tests for gate_resolution (MANDATORY)
    - Test should_capture_new_image() with gate_resolution=(160,160)
    - Verify metrics dict contains all required keys
    - Test None gate_resolution falls back to default
    - File: `tests/unit/test_capture_gate.py`
    - _Requirements: 3.1, 3.3_

  - [x] 3.5 Write property tests for gate and decision (OPTIONAL — Properties 5-9)
    - Property 5: Throttle sleep duration
    - Property 6: Time-based cooldown
    - Property 7: Timeout forcing
    - Property 8: First-wins rule
    - Property 9: Gate metrics keys
    - File: `tests/properties/test_gate_decision_properties.py`

- [x] 4. Fase D — Throttling del worker
  - [x] 4.1 Add loop frequency throttle to MonitoringWorker
    - Accept capture_loop_fps from profile in constructor
    - Add time.sleep() at end of each loop iteration: `max(0.0, (1/fps) - elapsed)`
    - Log WARNING if actual FPS drops below target
    - File: `src/application/services/monitoring_worker.py`
    - _Requirements: 2.1, 2.7_

- [x] 5. Fase E — Wiring de inferencia desde perfil
  - [x] 5.1 Add run_inference_timed() to SnapshotInferenceRunner
    - New method that returns (results, InferenceTimings) with real perf_counter measurements
    - InferenceTimings dataclass: detection_ms, health_ms, maturity_ms, total_ms
    - Existing run_inference() remains unchanged for backward compatibility
    - File: `src/infrastructure/vision/snapshot_inference_runner.py`
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

  - [x] 5.2 Connect camera resolution from ACTIVE_PROFILE to frame source
    - Pass camera_width, camera_height, camera_fps from ACTIVE_PROFILE when constructing RaspberryCameraFrameSource
    - Ensure preview flow (capture_single_frame) is not broken — preview can use its own defaults or profile values
    - File: `app/routes/agricultural_ui.py` (monitoring_start route) or `app/dependencies.py`
    - _Requirements: 4.1, 4.2, 4.10_

  - [x] 5.3 Wire inference parameters from profile to worker/runner
    - Pass inference_input_size, skip_maturity, detection_score_threshold, run_maturity_only_for_healthy from ACTIVE_PROFILE to SnapshotInferenceRunner construction
    - Worker calls run_inference_timed() instead of run_inference() for timing
    - File: `src/application/services/monitoring_worker.py`, `app/routes/agricultural_ui.py`
    - _Requirements: 4.3, 4.4, 4.5, 6.1, 6.2_

  - [x] 5.4 Write property tests for inference (OPTIONAL — Properties 10-11)
    - Property 10: Inference count equals snapshot count
    - Property 11: Inference failure does not terminate session
    - File: `tests/properties/test_inference_properties.py`

- [x] 6. Fase F — Thermal/memory metrics + timing wrappers + JSON output
  - [x] 6.1 Wire ThermalMonitor from ACTIVE_PROFILE and integrate temperature/RSS into metrics
    - Pass thermal params from profile to ThermalMonitor construction
    - Read temperature at each snapshot cycle and record in CycleMetrics
    - Read RSS memory at each snapshot cycle
    - Track peak_temperature_c and peak_rss_mb in PipelineMetrics
    - File: `src/application/services/monitoring_worker.py`
    - _Requirements: 1.6, 1.7, 1.8, 4.7, 4.8_

  - [x] 6.2 Add timing wrappers and metrics emission to worker
    - Wrap each pipeline stage with time.perf_counter() timing
    - After each snapshot cycle, construct CycleMetrics and call PipelineMetrics.add_cycle()
    - Emit all timings to logger at DEBUG level
    - Log snapshot_reason at INFO level
    - At session end, call PipelineMetrics.to_json_file() to write outputs/monitorings/{id}/pipeline_metrics.json
    - Log session summary at INFO level
    - File: `src/application/services/monitoring_worker.py`
    - _Requirements: 1.1-1.5, 1.9, 1.10, 1.11, 10.1, 10.4_

  - [x] 6.3 Write property test: Abort preserves data (OPTIONAL — Property 12)
    - Property 12: Abort preserves completed cycle data
    - File: `tests/properties/test_abort_preservation_properties.py`

- [x] 7. Fase G — Prueba manual en Raspberry + checkpoint final
  - [x] 7.1 Run full regression test on Raspberry Pi
    - Start app with TOMATO_MONITOR_PROFILE=edge (no --reload)
    - Open module, enter new monitoring setup
    - Verify camera preview works
    - Start monitoring, wait for ≥3 snapshots
    - Press "Detener Monitoreo"
    - Verify redirect to module (not stuck in "Deteniendo...")
    - Verify pipeline_metrics.json was written
    - Open new monitoring setup — verify preview works again
    - Start second monitoring — verify it works without Picamera2 errors
    - Document results in `docs/benchmarks/`
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 8.3_

  - [x] 7.2 Final checkpoint — Ensure all mandatory tests pass
    - Run pytest on unit tests (test_execution_profile, test_pipeline_metrics, test_snapshot_decision, test_capture_gate)
    - Verify no import errors
    - Verify smoke_test_profiles.py passes
    - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are OPTIONAL (property-based tests) and do not block MVP
- Tasks without `*` are MANDATORY
- Each phase is self-contained and can be validated independently
- The ordering follows: config → metrics → gate → throttle → inference → thermal → manual test
- No changes to camera lifecycle, lock, preview, or abort flow
- All code is Python 3.10+ on ARM64/CPU

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3"] },
    { "id": 2, "tasks": ["2.1"] },
    { "id": 3, "tasks": ["2.2", "2.3"] },
    { "id": 4, "tasks": ["3.1", "3.2"] },
    { "id": 5, "tasks": ["3.3", "3.4", "3.5"] },
    { "id": 6, "tasks": ["4.1"] },
    { "id": 7, "tasks": ["5.1", "5.2"] },
    { "id": 8, "tasks": ["5.3", "5.4"] },
    { "id": 9, "tasks": ["6.1"] },
    { "id": 10, "tasks": ["6.2", "6.3"] },
    { "id": 11, "tasks": ["7.1"] },
    { "id": 12, "tasks": ["7.2"] }
  ]
}
```
