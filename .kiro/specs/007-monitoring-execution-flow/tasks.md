# Implementation Plan: Monitoring Execution Flow

## Overview

This plan implements the complete monitoring execution flow for Tomato Monitor. The implementation follows bottom-up ordering: infrastructure (inference runner) → application (worker, service) → presentation (DTOs, routes) → integration wiring → tests. Each task builds on the previous, ending with full integration.

## Tasks

- [x] 1. Implement SnapshotInferenceRunner (Infrastructure)
  - [x] 1.1 Create `src/infrastructure/vision/snapshot_inference_runner.py`
    - Implement `SnapshotInferenceRunner` class with constructor accepting `DetectronDetector`, `ResNetHealthClassifier`, `MaturityEstimator`, and `Cropper`
    - Implement `run_inference(image: ndarray) -> list[dict]` method that runs: detect → crop → health classify → maturity estimate
    - Apply detection threshold (0.80), health threshold (0.70), and maturity-only-for-healthy policy
    - Each result dict contains: bbox (x1, y1, x2, y2), detection_score, health_label, health_confidence, maturity_stage (nullable), maturity_percent (nullable)
    - Handle single-detection failures gracefully (skip corrupt crop, continue with remaining detections)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.6, 10.2_

  - [ ]* 1.2 Write unit tests for SnapshotInferenceRunner
    - Test with mock detector returning 0, 1, and multiple detections
    - Test maturity skipped for unhealthy detections
    - Test maturity skipped for low-confidence detections (< 0.80)
    - Test graceful handling when classifier raises exception on a single crop
    - _Requirements: 3.1, 3.2, 3.3, 3.6_

- [x] 2. Implement MonitoringWorker (Application)
  - [x] 2.1 Create `src/application/services/monitoring_worker.py`
    - Implement `MonitoringWorker` class with threading signals: `pause_event`, `abort_event`, `complete_event` (all `threading.Event`)
    - Constructor accepts: monitoring_id, frame_source, scene_gate, inference_runner, snapshot_repo, inspection_result_repo, monitoring_repo, db_session
    - Implement `run()` method with capture loop: read frame → check signals → evaluate scene gate → process snapshot → persist results → update counters
    - Implement `_process_snapshot(frame)`: save image to `outputs/monitorings/{monitoring_id}/snapshots/snapshot_{frame_index}.jpg`, run inference, persist Snapshot and InspectionResult entities
    - Implement `_finalize()`: release frame source, set terminal state
    - Handle camera disconnection (read failure) → transition to error state
    - Handle single snapshot inference failure → skip snapshot, continue loop
    - Handle filesystem errors (OSError on image write) → transition to error state
    - Maintain frame counter for scene gate cooldown/timeout tracking
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.4, 3.5, 3.7, 4.3, 7.1, 10.1, 10.5_

  - [ ]* 2.2 Write unit tests for MonitoringWorker
    - Test with mock FrameSource returning N frames then stopping — verify snapshot count matches scene gate triggers
    - Test pause signal stops frame capture, resume continues
    - Test abort signal stops loop and calls finalize
    - Test camera disconnection (read returns False) transitions to error
    - Test single inference failure is skipped, loop continues
    - _Requirements: 2.1, 2.3, 2.5, 4.3, 7.1_

  - [ ]* 2.3 Write property test for counter consistency
    - **Property 5: Counter consistency**
    - Generate random sequences of snapshots with varying detection counts (0-10 per snapshot)
    - Verify total_snapshots equals count of Snapshot records and total_detections equals sum of InspectionResults
    - **Validates: Requirement 3.7**

- [x] 3. Implement MonitoringService (Application)
  - [x] 3.1 Create `src/application/services/monitoring_service.py`
    - Implement `MonitoringService` class with constructor accepting all repository interfaces (monitoring, snapshot, inspection_result, metrics, module)
    - Implement `start_session(module_id, width_m, length_m, notes)`: validate module exists, enforce one-active-session-per-module, create Monitoring entity (status=initializing), spawn background worker thread (daemon=True), transition to running
    - Implement `pause_session(monitoring_id)`: validate current status is running, signal worker pause_event, transition to paused
    - Implement `resume_session(monitoring_id)`: validate current status is paused, verify frame_source availability, clear pause_event, transition to running
    - Implement `abort_session(monitoring_id)`: validate non-terminal status, signal abort_event, compute partial metrics, transition to aborted
    - Implement `complete_session(monitoring_id)`: validate status is running, signal complete_event, transition to finishing → compute metrics → transition to completed
    - Implement `get_status(monitoring_id)`: return current Monitoring entity with counters
    - Implement `_compute_metrics(monitoring_id)`: aggregate InspectionResults into MonitoringMetrics (total_tomatoes, healthy/unhealthy counts, percentages by maturity stage, snapshots_with_detections)
    - Enforce state machine transitions via MonitoringStatus value object (raise domain exception for invalid transitions)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 4.1, 4.2, 4.4, 4.5, 4.6, 4.7, 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 7.1, 7.3, 7.4, 7.5, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 10.1, 10.4_

  - [ ]* 3.2 Write unit tests for MonitoringService
    - Test start_session creates monitoring with status initializing and spawns worker
    - Test start_session rejects second active session for same module (one-active enforcement)
    - Test pause_session from running → paused succeeds
    - Test pause_session from non-running state raises domain exception
    - Test resume_session from paused → running succeeds
    - Test abort_session computes partial metrics and transitions to aborted
    - Test complete_session computes full metrics and transitions to completed
    - Test _compute_metrics with zero results produces all-zero metrics
    - Test state machine enforcement (invalid transitions rejected)
    - _Requirements: 1.1, 1.4, 4.1, 4.2, 5.3, 5.5, 6.1, 6.2, 6.5, 7.4, 8.5_

  - [ ]* 3.3 Write property test for session state consistency
    - **Property 1: Session state consistency**
    - Generate random sequences of valid and invalid state transition commands
    - Verify status always reflects a valid MonitoringStatus enum value after each operation
    - Verify invalid transitions raise domain exceptions without changing state
    - **Validates: Requirements 1.4, 4.1, 5.1, 6.1, 7.4**

  - [ ]* 3.4 Write property test for one active session per module
    - **Property 2: One active session per module**
    - Generate random sequences of start/complete/abort operations on the same module_id
    - Verify at most one session with active status exists at any point
    - Verify concurrent start attempts for same module fail
    - **Validates: Requirement 8.5**

- [x] 4. Checkpoint — Core application logic
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement DTOs (Application)
  - [x] 5.1 Create `src/application/dtos/monitoring_dtos.py`
    - Implement `StartMonitoringRequest(BaseModel)`: module_id (int), width_m (float, gt=0, le=1000), length_m (float, gt=0, le=1000), notes (Optional[str], max_length=500)
    - Implement `MonitoringResponse(BaseModel)`: id, module_id, status, started_at, completed_at, total_snapshots, total_detections
    - Implement `MonitoringStatusResponse(MonitoringResponse)`: width_m, length_m, notes (extends MonitoringResponse)
    - Implement `ErrorResponse(BaseModel)`: detail (str), current_status (Optional[str])
    - Add `model_config` with `from_attributes = True` for ORM compatibility
    - _Requirements: 9.1, 9.5_

- [x] 6. Implement API Routes (Presentation)
  - [x] 6.1 Create `app/routes/monitoring.py`
    - Create `APIRouter(prefix="/monitoring", tags=["monitoring"])`
    - Implement `POST /monitoring/start` — accept StartMonitoringRequest, call service.start_session, return MonitoringResponse (HTTP 201)
    - Implement `POST /monitoring/{monitoring_id}/pause` — call service.pause_session, return MonitoringResponse
    - Implement `POST /monitoring/{monitoring_id}/resume` — call service.resume_session, return MonitoringResponse
    - Implement `POST /monitoring/{monitoring_id}/abort` — call service.abort_session, return MonitoringResponse
    - Implement `POST /monitoring/{monitoring_id}/complete` — call service.complete_session, return MonitoringResponse
    - Implement `GET /monitoring/{monitoring_id}/status` — call service.get_status, return MonitoringStatusResponse
    - Handle domain exceptions: invalid state transition → HTTP 409 Conflict with error message
    - Handle not found: monitoring_id not in DB → HTTP 404
    - Handle validation: invalid module_id or dimensions → HTTP 422
    - Handle active session conflict: module already has active session → HTTP 409
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9, 9.10_

  - [ ]* 6.2 Write API route tests with TestClient
    - Test POST /monitoring/start with valid payload returns 201
    - Test POST /monitoring/start with invalid module_id returns 422
    - Test POST /monitoring/start with active session returns 409
    - Test POST /monitoring/{id}/pause from running returns 200
    - Test POST /monitoring/{id}/pause from invalid state returns 409
    - Test GET /monitoring/{id}/status returns correct counters
    - Test GET /monitoring/{nonexistent}/status returns 404
    - Mock MonitoringService for all route tests
    - _Requirements: 9.1, 9.2, 9.7, 9.8, 9.9, 9.10_

- [x] 7. Integration wiring
  - [x] 7.1 Register monitoring routes and dependencies in `app/main.py` and `app/dependencies.py`
    - Add `get_monitoring_service()` dependency factory in `app/dependencies.py` that creates MonitoringService with all required repositories from DatabaseManager
    - Register the monitoring router in `app/main.py` with the application
    - Ensure DatabaseManager session is properly injected into the service
    - Create `outputs/monitorings/` directory structure on first use
    - _Requirements: 8.1, 9.1_

- [x] 8. Checkpoint — Full API integration
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Integration tests
  - [ ]* 9.1 Write integration test for full monitoring flow
    - Use in-memory SQLite and mock FrameSource (returns N frames then stops)
    - Test: start → worker processes frames → scene gate triggers snapshots → complete → verify MonitoringMetrics
    - Verify snapshot count, detection count, and metrics percentages are consistent
    - _Requirements: 1.1, 1.4, 2.3, 3.4, 5.3, 5.4_

  - [ ]* 9.2 Write integration test for pause/resume flow
    - Start session → pause → verify no new snapshots accumulate → resume → verify capture resumes → complete
    - _Requirements: 4.1, 4.3, 4.4, 4.6_

  - [ ]* 9.3 Write integration test for abort flow
    - Start session → capture some snapshots → abort → verify partial metrics computed and results preserved
    - _Requirements: 6.1, 6.4, 6.5_

  - [ ]* 9.4 Write integration test for error handling
    - Start session with FrameSource that fails after N frames → verify error state and partial results preserved
    - _Requirements: 7.1, 7.3_

- [x] 10. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The design uses Python with existing project conventions (pytest, FastAPI, SQLAlchemy)
- All vision components (detector, health classifier, maturity estimator, cropper, capture gate) already exist in `src/infrastructure/vision/`
- All repository interfaces and domain entities already exist from Spec 006
- The `MonitoringStatus` value object with state machine validation already exists at `src/domain/value_objects/monitoring_status.py`
- Frame source implementations (RaspberryCameraFrameSource, VideoFileFrameSource) already exist in `src/infrastructure/camera/`

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "5.1"] },
    { "id": 1, "tasks": ["1.2", "2.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "3.1"] },
    { "id": 3, "tasks": ["3.2", "3.3", "3.4"] },
    { "id": 4, "tasks": ["6.1"] },
    { "id": 5, "tasks": ["6.2", "7.1"] },
    { "id": 6, "tasks": ["9.1", "9.2", "9.3", "9.4"] }
  ]
}
```
