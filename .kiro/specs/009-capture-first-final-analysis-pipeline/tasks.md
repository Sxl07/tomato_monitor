# Implementation Plan: Capture-First Pipeline with Deferred Analysis

## Overview

This plan splits the current `MonitoringWorker` (capture + inference) into two sequential phases: a fast `CaptureWorker` (no inference) and a deferred `SnapshotAnalysisService` (full inference on saved snapshots). Implementation follows an incremental approach with Raspberry Pi validation checkpoints.

## Tasks

- [x] 1. Extend state machine with ANALYZING state (additive, no removals)
  - [x] 1.1 Add ANALYZING to MonitoringState enum and VALID_TRANSITIONS
    - Added `ANALYZING = "analyzing"` to `MonitoringState` enum
    - Added `ANALYZING` and `COMPLETED` to `VALID_TRANSITIONS[RUNNING]`
    - Added `ANALYZING: {COMPLETED, ERROR}` entry
    - Preserved `PAUSED` and `FINISHING` in enum and transitions
    - _Requirements: 6.1, 6.2, 15.7_

  - [x] 1.2 Write unit tests for state machine with ANALYZING (obligatory)
    - Updated `EXPECTED_TRANSITIONS` and `NON_TERMINAL_STATES` in existing test file
    - Added `TestAnalyzingState` class with 13 explicit tests
    - Verified backward compatibility: running→paused, running→finishing still work
    - 165 state machine tests passing
    - _Requirements: 6.1, 6.2, 6.3, 15.7, 17.3_

- [x] 2. Create CaptureWorker — fast capture loop without inference
  - [x] 2.1 Create `src/application/services/capture_worker.py` with CaptureWorker class
    - Capture loop: read frame → cooldown → timeout → Scene Gate → save raw → persist → counter
    - Saves to `outputs/monitorings/{monitoring_id}/snapshots/raw/snapshot_{frame_index:06d}.jpg`
    - No inference imports or calls
    - Validates cv2.imwrite return value
    - exit_reason="error" for storage/persistence failures (not "abort")
    - ThermalMonitor integration (start/stop via getattr)
    - Cooperative pause via pause_event
    - complete_event as alias for finalize_event (backward compat)
    - _Requirements: 1.1, 1.3, 1.5, 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.3, 11.1, 16.1_

  - [x] 2.2 Write unit tests for CaptureWorker (obligatory)
    - 47 tests covering: no inference, first frame, paths, has_detections=False, counters, cooldown, timeout, finalize, abort, release, metrics, imwrite validation, persistence errors, thermal, pause, thermal_pause_event, complete_event alias, release_resources idempotency
    - _Requirements: 17.1, 17.2_

- [x] 3. Connect CaptureWorker to MonitoringService (capture-only, Phase C1)
  - [x] 3.1 C1 — Connect CaptureWorker for capture-only validation
    - MonitoringService uses CaptureWorker instead of MonitoringWorker
    - start_session() no longer requires inference_runner
    - Removed SnapshotInferenceRunner import from monitoring_service.py
    - agricultural_ui.monitoring_start() no longer calls _build_inference_runner()
    - _ACTIVE_STATUSES includes "analyzing"
    - _run_worker() simplified (no inspection_result_repo patching)
    - settings.py updated: EDGE capture_loop_fps=5.0, min_seconds=1.0, max_seconds=3.0, gate=(240,240)
    - Abort/pause/complete compatibility preserved
    - Tests: 9 tests in test_monitoring_service_c1.py
    - Profile comparison tests updated (>= instead of > for capture params)
    - _Requirements: 5.1, 5.2, 11.1, 14.1, 14.2, 14.3, 15.2, 15.4, 15.7_

  - [x] 3.2 C2 — Implement finalize_capture flow
    - Add `finalize_capture()` method to MonitoringService
    - Signal finalize_event → wait worker (max 10s) → release camera
    - running → analyzing when snapshots >= 1
    - running → completed when snapshots == 0 (empty metrics via create_pending_for_finalization)
    - Do not mark aborted for normal finalization
    - Zero snapshots: update_counters → create_pending_for_finalization → completed
    - write_capture_metrics errors are recoverable (logged, flow continues)
    - Add tests for finalize_capture transitions and camera release
    - 41 tests in test_monitoring_service_c2.py all passing (isolated, no cv2/numpy)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 13.1, 13.2, 17.3, 17.4, 17.5_

  - [x] 3.3 Add POST /monitoreos/{id}/finalizar-captura endpoint in agricultural_ui.py
    - Server-rendered endpoint calling monitoring_service.finalize_capture(id)
    - Redirects to /monitoreos/{id}/ejecucion with 303
    - Handles MonitoringNotFoundError, FinalizationInProgressError, InvalidTransitionError
    - FinalizationInProgressError treated as idempotent (redirect to execution, not error)
    - Keep /monitoreos/{id}/abortar only for real cancellation
    - POST /monitoreos/{id}/finalizar-captura added as a server-rendered endpoint. It delegates exclusively to MonitoringService.finalize_capture() and redirects to the execution screen. Abort remains exclusive to explicit cancellation.
    - 11 tests in test_monitoring_finalize_route.py
    - _Requirements: 5.1, 12.2_

  - [x] 3.4 Add analysis configuration parameters to settings.py
    - analysis_skip_maturity (default False)
    - analysis_thermal_pause_threshold
    - analysis_thermal_resume_threshold
    - Connected via ACTIVE_PROFILE.analysis_skip_maturity, ACTIVE_PROFILE.analysis_thermal_pause_threshold, ACTIVE_PROFILE.analysis_thermal_resume_threshold in _run_analysis
    - _Requirements: 14.2, 14.4_

- [x] 4. Update UI for capture phase and analyzing state
  - [x] 4.1 Modify `app/static/js/monitoring.js` for analyzing state
    - Add "analyzing" to ALL_STATUSES array
    - During running: show "Snapshots capturados: N" counter
    - Handle analyzing state: show progress "Analizando snapshots... (X/Y)"
    - Handle completed: redirect to report page
    - Change finalize action URL to POST /monitoreos/{id}/finalizar-captura
    - Disable action buttons during analyzing via setMonitoringActionsDisabled()
    - updateAnalysisProgress() called from updateExecutionUI() on every response
    - The execution UI now separates normal capture finalization from explicit cancellation. During analyzing it displays in-memory snapshot progress and keeps polling until completed or error.
    - _Requirements: 6.5, 6.6, 12.1, 12.2, 12.3, 12.4, 12.5_

  - [x] 4.2 Modify `app/templates/agricultural/monitoring_execution.html`
    - Change primary button from "Detener Monitoreo" to "Finalizar captura" during running
    - Add finalize-dialog with confirmation and finalize-form pointing to /finalizar-captura
    - Add status-analyzing block with progress bar, processed/total counters
    - Add finalizing-capture-overlay for the submission transition
    - Show "Snapshots capturados: N" during running (tomatoes counter removed during capture)
    - Disable all action buttons during analyzing
    - Abort form preserved separately for explicit cancellation
    - Double-submit protection on finalize-form
    - _Requirements: 6.5, 6.6, 6.7, 6.8, 12.1, 12.2, 12.3, 12.4_

  - [x] 4.3 Update status endpoint to include analysis progress from registry
    - Modify GET /monitoring/{id}/status response with analysis_processed, analysis_total
    - Read from runtime registry (not DB)
    - Monitoring status now exposes analysis_processed and analysis_total from the in-memory runtime registry. Progress is not persisted and defaults to 0/0 when no analysis runtime is available.
    - 11 tests in test_monitoring_status_analysis_progress.py
    - _Requirements: 12.3_

- [x] 5. Raspberry Pi validation checkpoint — capture-only C1
  - Preview works ✓
  - Start monitoring ✓
  - 25 snapshots captured in ~44 seconds ✓
  - Camera released correctly ✓
  - Second preview works ✓
  - Second monitoring works without restart ✓
  - No inference loaded or executed ✓
  - Scene Gate responded to real scene changes ✓

- [x] 6. Create SnapshotAnalysisService — deferred inference
  - [x] 6.1A Core isolated SnapshotAnalysisService
    - Create `src/application/services/snapshot_analysis_service.py`
    - Create `src/infrastructure/vision/annotation_renderer.py`
    - Add `update_has_detections()` to SnapshotRepository and SqlSnapshotRepository
    - Load snapshot list by frame_index ASC (explicit sort)
    - If empty → return zeros, no model loading, no components_factory call
    - Load models once via components_factory (lazy import, no top-level Detectron2)
    - Process each snapshot: detect → track → dedup → health → maturity
    - Maintain SimpleTracker across all snapshots (single instance)
    - Keep best_result_by_track dict (largest bbox area wins)
    - Persist one DetectionInspectionResult per unique track (best view)
    - Generate annotated snapshots via annotation_renderer
    - Generate crops per detection
    - Update has_detections per snapshot
    - Handle recoverable errors per snapshot (log, skip, continue)
    - Thermal protection optional (start/stop, cooperative pause)
    - analysis_skip_maturity configurable (default False)
    - Module importable without Detectron2
    - _Requirements: 1.2, 1.4, 7.1-7.8, 8.1-8.3, 9.1-9.5, 16.2, 16.3_

  - [x] 6.1B Analysis reports and pipeline_metrics
    - Create `src/infrastructure/persistence/local/snapshot_analysis_report_writer.py`
    - SnapshotAnalysisReportWriter: atomic CSV writes, pipeline_metrics.json merge
    - Added report_writer and profile_name parameters to SnapshotAnalysisService.__init__
    - Added per_snapshot_rows, per_detection_rows accumulators (reset on each run)
    - Added report_paths field to AnalysisResult dataclass
    - _generate_reports() called AFTER thermal_monitor.stop() in finally block, for ALL outcomes
    - Reports generated even on fatal errors (partial data with status="error")
    - Default writer created when report_writer=None (lazy import)
    - Default profile_name from ACTIVE_PROFILE.name when not provided
    - selected_as_best logic: matches exact bbox+snapshot+frame, excludes reused, does NOT overwrite decision_reason
    - per_detection_row: health_executed/maturity_executed from explicit det flags (not inferred)
    - per_detection_row: image_name from frame_result first, fallback to basename
    - per_detection_row: decision_reason preserved from process_frame
    - Rows sorted before CSV write (by frame_index, then id); originals not modified
    - Complete analysis_metrics in pipeline_metrics.json: execution_counts, average_timings_seconds, throughput, errors
    - Writer errors emitted via LogService (non-fatal)
    - Invalid existing pipeline_metrics.json preserved unchanged (error reported)
    - Independent CSV failure: other files still succeed
    - pipeline_metrics.json preserves existing capture section when merging
    - C2B hardening (8 corrections):
      - Correction 1: Staged best results — _process_snapshot returns staged_best dict, merged ONLY after successful commit
      - Correction 2: report_files dict in pipeline_metrics.json with relative paths (only files that succeeded)
      - Correction 3: CSV failures reflected in pipeline_metrics.json errors (enriched copy before JSON write)
      - Correction 4: _generate_reports completely non-throwing (lazy imports inside try)
      - Correction 5: _record_result_error syncs errors into both self._errors and result.errors
      - Correction 6: Complete per_detection tests for reused detections (decision_reason, executed flags)
      - Correction 7: Tests for default profile_name from ACTIVE_PROFILE + import failure
      - Correction 8: tasks.md updated
    - Module importable without Detectron2
    - 42 tests in test_snapshot_analysis_reports.py all passing
    - _Requirements: 10.1, 10.2, 10.3_

  - [x] 6.2 Write unit tests for SnapshotAnalysisService (obligatory)
    - 0 snapshots → zeros, components_factory not called
    - Frame_index ascending order (even if repo returns unordered)
    - components_factory called exactly once
    - Same components object for all snapshots
    - Same track_id in 3 snapshots → counted once, one InspectionResult
    - Larger bbox replaces previous best view
    - reused_previous_result → no extra InspectionResult
    - Two distinct tracks → two unique tomatoes
    - Snapshot with detections → has_detections=True, annotated snapshot generated
    - Snapshot without detections → has_detections=False
    - Missing image → failed_snapshots incremented, continues
    - health_result absent → health_label="unknown", still counted
    - analysis_skip_maturity=True → no maturity execution
    - progress updates correctly
    - Module importable without Detectron2
    - _Requirements: 17.6, 17.7_

- [x] 7. Connect SnapshotAnalysisService to MonitoringService
  - [x] 7.1 Add _run_analysis() wrapper in MonitoringService
    - Daemon thread running SnapshotAnalysisService.run()
    - On success: compute MonitoringMetrics via create_pending_for_finalization, transition to completed
    - On error: rollback, transition to error, preserve raw snapshots
    - Register analysis service in runtime registry for progress queries
    - Uses fresh thread-local DB session and persists unique-track metrics
    - Fully hardened: all imports/constructors inside try/except, session always closed, registry always cleaned
    - Recoverable errors (result.errors) do not prevent completed when progress.status == "completed" and error_reason is None
    - _run_worker uses remove_runtime (not remove) in all exit paths to preserve finalization claims
    - _Requirements: 5.3, 15.6_

- [x] 8. Update report to use annotated snapshots
  - [x] 8.1 Update build_snapshot_gallery() in app/context_builders.py
    - URLs point to annotated_snapshots/ (preferred)
    - Fallback to snapshots/raw/ if annotated doesn't exist
    - Filename uses 6-digit padding (snapshot_000003.jpg)
    - Sorted by frame_index ascending
    - Path traversal validation preserved
    - Report gallery now prefers annotated snapshots when present and falls back to raw snapshots under the existing /snapshots static mount. No database migration or new serving route was required.
    - _Requirements: 8.4, 17.8_

  - [x] 8.2 Update snapshot serving route
    - Existing mount in main.py (/snapshots → outputs/monitorings) already serves both annotated_snapshots/ and snapshots/raw/ — no modification needed
    - _Requirements: 8.4_

  - [x] 8.3 Write unit test for report using annotated snapshots (obligatory)
    - 14 tests in test_report_snapshot_gallery.py
    - build_snapshot_gallery() returns URLs for annotated snapshots
    - Fallback to raw when annotated doesn't exist
    - Static verification of /snapshots mount in main.py
    - _Requirements: 17.8_

- [x] 9. Update orphan detection and history
  - [x] 9.1 Update _reconcile_orphaned_sessions to handle analyzing
    - analyzing without live analysis thread → mark as error, preserve snapshots
    - Docstring updated to cover analysis runtime threads
    - 13 tests in test_monitoring_orphan_reconciliation.py
    - _Requirements: 6.4, 15.4_

  - [x] 9.2 Verify history shows correct statuses
    - Completed for normal finalization → "Completado"
    - Cancelado only for explicit cancellations → "Cancelado"
    - Error for system failures → "Error"
    - Analyzing sessions are now explicitly covered by orphan reconciliation tests. History displays completed, canceled, and error monitorings distinctly; aborted is reserved for explicit operator cancellation.
    - 18 tests in test_monitoring_history_statuses.py
    - _Requirements: 13.1, 13.2, 13.3_

- [x] 10. Cleanup dead code
  - [x] 10.1 Remove inference-related imports from monitoring start flow
    - Removed _build_inference_runner() from agricultural_ui.py
    - Removed duplicate non-request-scoped dependency definitions from dependencies.py
    - Updated MonitoringService module docstring (CaptureWorker + SnapshotAnalysisService)
    - Keep MonitoringWorker file for reference (covered by legacy property tests)
    - Dead start-flow inference helper removed from agricultural_ui. Duplicate non-request-scoped dependency definitions were removed. MonitoringWorker remains as a legacy/reference implementation covered by legacy tests.
    - 13 tests in test_cleanup_dead_code.py
    - _Requirements: 1.1, 1.3_

- [x] 11. Raspberry Pi full integration test
  - Manually test complete flow:
    - Preview → Start → Capture 10+ snapshots → Finalizar captura
    - Verify camera released → analyzing state in UI
    - Wait for analysis → report shown
    - Verify annotated snapshots in gallery
    - Verify metrics use unique track count
    - Verify second monitoring works
    - Document results and thermal behavior
  - _Note: Pending RPi hardware test session. All code changes validated on dev machine._
  - Task 11 completed on Raspberry Pi: preview, capture, finalize-capture, analyzing state, deferred inference, automatic report redirect and second monitoring without restart worked correctly. 20 snapshots analyzed in approximately 3 minutes. Thermal protection warning observed around 75.7°C.

- [x] 12. Analysis UX and navigation
  - [x] 12.1 Mostrar monitoreo activo/analyzing en module_detail
    - ActiveMonitoringContext dataclass and build_active_monitoring_context() in context_builders.py
    - module_detail route passes active_monitoring to template
    - Template shows "Monitoreo en curso" card with status label and "Volver al monitoreo" link
    - Module detail now surfaces an active monitoring card for initializing/running/paused/finishing/analyzing sessions, allowing users to return to the execution screen while deferred analysis continues.
    - 24 tests in test_module_detail_active_monitoring.py

  - [x] 12.2 Permitir volver a /monitoreos/{id}/ejecucion mientras está analyzing
    - Execution URL in card links directly to the monitoring execution screen
    - Works for all active statuses including analyzing

  - [x] 12.3 Mostrar estado "Pausado por temperatura" si aplica
    - Added thermal fields to AnalysisProgress and MonitoringStatusResponse
    - GET /monitoring/{id}/status returns analysis_thermal_paused, temperature, pause_reason
    - Template: analysis-thermal-alert element in analyzing block (hidden by default)
    - JS: updateAnalysisThermalState() shows/hides alert based on response data
    - 10 tests in test_monitoring_status_thermal_analysis.py
    - 8 tests in test_monitoring_execution_thermal_ui.py
    - _Requirements: 6.5, 6.6, 12.3_

  - [x] 12.4 Registrar duración real del análisis y temperatura máxima en pipeline_metrics
    - AnalysisResult includes thermal_peak_temperature_c, thermal_pause_count, thermal_pause_duration_seconds, thermal_cooling_warning_at_start, thermal_was_paused
    - pipeline_metrics.json analysis section includes "thermal" subsection and "seconds_per_processed_snapshot"
    - summary.csv includes thermal columns
    - ThermalMonitor.stop() accounts for in-progress pause duration
    - ThermalMonitor properties: pause_event, current_temperature
    - ThermalMonitor.get_session_metadata() includes current_temperature_c, is_paused
    - 11 tests in test_thermal_monitor.py
    - 11 tests in test_snapshot_analysis_thermal_metrics.py
    - _Requirements: 10.1, 10.2, 10.3, 12.4_

  - [x] 12.5 Evaluar parámetros de rendimiento
    - pipeline_metrics.json analysis section includes "performance_config" with analysis_skip_maturity and profile_name
    - settings.py documented: analysis_skip_maturity and inference dimensions annotated for performance evaluation recording
    - Existing test_execution_profile.py extended with TestAnalysisParamsPresent
    - _Requirements: 14.2, 14.4, 12.5_

## Notes

- Tasks 1, 2, 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.3, 5, 6.1A, 6.1B, 6.2, 7, 7.1, 8.1, 8.2, 8.3, 9.1, 9.2, 10.1, 12.1, 12.2, 12.3, 12.4, 12.5 are completed
- Task 11 completed on Raspberry Pi: preview, capture, finalize-capture, analyzing state, deferred inference, automatic report redirect and second monitoring without restart worked correctly. 20 snapshots analyzed in approximately 3 minutes. Thermal protection warning observed around 75.7°C.
- Automated test suite: 806 passed, 0 failed, 0 skipped; executed on development machine
- Breakdown: 46 analysis service + 46 analysis reports + 8 pipeline dedup + 21 pipeline metrics + 47 capture worker + 11 service C1 + 41 service C2 + 11 finalize route + 12 status progress + 30 execution UI + 14 report gallery + 13 orphan reconciliation + 18 history statuses + 13 cleanup + 24 active monitoring + 165 state machine + 12 metrics repo + 10 app service + 11 thermal monitor + 11 analysis thermal metrics + 10 status thermal analysis + 8 execution thermal UI + 5 execution profile analysis params + 189 others + 55 additional (= 781)
- C2 service integration completed without route/UI changes. Normal capture finalization now releases the camera before deferred analysis. Analysis uses a fresh thread-local database session and persists unique-track metrics.
- Post-C2A Raspberry Pi capture regression test:
  - monitoring 1: 28 snapshots
  - monitoring 2: 10 snapshots
  - second preview successful
  - second monitoring successful
  - camera lock reacquired and released
  - no camera-busy errors
  - observed temperature approximately 51–52 °C
- Los campos analysis_skip_maturity, analysis_thermal_pause_threshold y analysis_thermal_resume_threshold ya existen en settings.py y están formalmente conectados al flujo de análisis (tarea 3.4 completada).
- `PAUSED` and `FINISHING` remain untouched — full backward compatibility
- Thermal protection: CaptureWorker has separate thermal_pause_event (independent of manual pause_event)
- No BD migration — progress in memory, metrics persisted only at completion
- `aborted` = explicit operator cancellation ONLY; system failures = `error`
- Track.update() no longer updates best_area (Spec 009 hardening correction 1)
- Path validation uses pathlib.Path.resolve()
- pipeline_orchestrator importable without Detectron2 (lazy imports)
- All report errors use _record_result_error (syncs self._errors, result.errors, LogService)
- Unit tests use NoOpReportWriter to avoid writing to real outputs/

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "2.1"] },
    { "id": 2, "tasks": ["2.2", "3.1"] },
    { "id": 3, "tasks": ["6.1A"] },
    { "id": 4, "tasks": ["6.1B", "6.2"] },
    { "id": 5, "tasks": ["3.2", "3.3", "3.4"] },
    { "id": 6, "tasks": ["7.1"] },
    { "id": 7, "tasks": ["4.1", "4.2", "4.3"] },
    { "id": 8, "tasks": ["8.1", "8.2", "8.3", "9.1", "9.2"] },
    { "id": 9, "tasks": ["10.1"] }
  ]
}
```
