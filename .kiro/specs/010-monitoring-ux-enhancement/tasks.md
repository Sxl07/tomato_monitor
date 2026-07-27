# Implementation Plan: Monitoring UX Enhancement

## Overview

This plan implements the monitoring UX enhancement in order: core services → abstract interfaces → API endpoints → worker integration → templates (preparation + execution screens) → dependency wiring → unit tests. Each step builds incrementally on the previous, ensuring no orphaned code.

## Tasks

- [x] 1. Implement core application services
  - [x] 1.1 Create LogService with LogEntry dataclass and LogLevel enum
    - Create `src/application/services/log_service.py`
    - Implement `LogLevel` enum (info, success, warning, error)
    - Implement `LogEntry` frozen dataclass (timestamp, level, source, message)
    - Implement `LogService` class with thread-safe `deque` storage (maxlen=200)
    - Methods: `add_entry()`, `get_entries(since=None)`, `clear_session()`
    - Use `threading.Lock` for concurrent access safety
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 14.1_

  - [x] 1.2 Create CameraService with availability check and JPEG capture
    - Create `src/application/services/camera_service.py`
    - Implement `CameraStatus` enum (available, not_detected, error)
    - Implement `CameraCheckResult` dataclass
    - Implement `CameraService` class with `cv2.VideoCapture` internally
    - Methods: `check_availability()`, `capture_preview_frame()` returning JPEG bytes
    - Timeout of 5 seconds, release camera immediately after capture
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 14.2, 14.5_

  - [x] 1.3 Create ModelService with file existence check
    - Create `src/application/services/model_service.py`
    - Implement `ModelStatus` enum (available, not_found)
    - Implement `ModelService` class with `Path.exists()` check only
    - Constructor accepts `model_path: Path` from settings
    - _Requirements: 5.1, 5.2, 5.3_

- [x] 2. Create abstract domain interfaces
  - [x] 2.1 Create RobotMovementService ABC
    - Create `src/domain/interfaces/robot_movement_service.py`
    - Define `RobotPosition` dataclass (x_meters, y_meters, heading_degrees)
    - Define abstract methods: `advance()`, `pause()`, `stop()`, `get_position()`
    - No implementation logic — only signatures with docstrings
    - _Requirements: 12.1, 12.3, 12.4_

  - [x] 2.2 Create DecisionService ABC
    - Create `src/domain/interfaces/decision_service.py`
    - Define `MovementDecision` enum (advance, pause, wait)
    - Define abstract method: `decide(frame_context)` returning `MovementDecision`
    - No implementation logic — only signature with docstring
    - _Requirements: 12.2, 12.3, 12.4_

- [x] 3. Implement API endpoints
  - [x] 3.1 Create monitoring API router with camera endpoints
    - Create `app/routes/monitoring_api.py`
    - Implement `GET /api/camera/preview` returning `image/jpeg` or 503 JSON
    - Implement `GET /api/camera/status` returning JSON `{status, reason}`
    - Use FastAPI `Depends()` to inject CameraService
    - _Requirements: 8.1, 8.2, 8.3_

  - [x] 3.2 Add log and last-snapshot API endpoints
    - Implement `GET /api/monitoring/{monitoring_id}/log` returning JSON array
    - Support optional `?since=ISO8601` query parameter for incremental polling
    - Implement `GET /api/monitoring/{monitoring_id}/last-snapshot` returning JPEG or 404
    - Use FastAPI `Depends()` to inject LogService and SnapshotRepository
    - _Requirements: 9.1, 9.2, 10.1, 10.2_

- [x] 4. Integrate LogService with MonitoringWorker
  - [x] 4.1 Add log_service parameter to MonitoringWorker and emit lifecycle log entries
    - Add optional `log_service: Optional[LogService]` param to `MonitoringWorker.__init__`
    - Add `_emit_log(level, message)` helper method
    - Emit log entries at: camera init, camera OK, model load start, model load OK, running transition, each snapshot capture, each inference result, completion, camera error, model error, temperature warning
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10, 7.11_

  - [x] 4.2 Pass LogService from MonitoringService.start_session to worker
    - Update `MonitoringService.start_session()` to accept optional `log_service` param
    - Forward `log_service` to `MonitoringWorker` constructor
    - _Requirements: 11.3_

- [x] 5. Checkpoint — Ensure all services and API compile correctly
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Update Preparation Screen template
  - [x] 6.1 Enhance monitoring_setup.html with camera preview, status badges, and conditional button
    - Add Camera_Preview component (img tag loaded from `/api/camera/preview`)
    - Add camera status badge: "Cámara activa" (green) / "Cámara no detectada" (red) / "Imagen no disponible" (gray)
    - Add model status badge: "Modelo disponible" (green) / "Modelo no encontrado" (red)
    - Add "Actualizar cámara" button that re-fetches preview and status
    - Disable "Iniciar Monitoreo" button when camera is not `available`
    - Display module name, crop type, and configured dimensions
    - Preserve existing dimension form inputs (width, length) and notes field
    - Pre-fill width/length from module entity when available
    - Add JS `fetch()` calls to `/api/camera/status` and `/api/camera/preview`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 13.1, 13.2, 13.3, 13.4, 13.5_

- [x] 7. Update Execution Screen template
  - [x] 7.1 Enhance monitoring_execution.html with counters, log panel, last image, and auto-redirect
    - Add elapsed time counter (MM:SS) updating every second via JS `setInterval`
    - Add total snapshots counter and total tomatoes counter (updated via polling)
    - Add last snapshot thumbnail (polled from `/api/monitoring/{id}/last-snapshot`)
    - Add Activity_Log_Panel: scrollable div (max-height 200px), auto-scroll to bottom
    - Display each log entry with HH:MM:SS timestamp, color-coded level, and message in Spanish
    - Add "Detener Monitoreo" button with confirmation dialog
    - Add temperature warning banner (visible when temp > 70°C)
    - Implement auto-redirect to report screen when status transitions to `completed`
    - Implement polling every 2 seconds: status + log + last-snapshot (max 3 requests per cycle)
    - Display placeholder SVG when no snapshot available yet
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 9.3, 10.3, 14.3, 14.4_

- [x] 8. Wire dependencies and register router
  - [x] 8.1 Register services in app/dependencies.py and register API router in main.py
    - Add `get_log_service(request)` dependency (singleton on `app.state.log_service`)
    - Add `get_camera_service()` dependency returning CameraService instance
    - Add `get_model_service()` dependency returning ModelService with path from settings
    - Initialize LogService singleton in `lifespan()` and store on `app.state`
    - Register `monitoring_api.router` in `main.py` with `app.include_router()`
    - Update `get_monitoring_service()` to pass `log_service` through to worker
    - _Requirements: 11.4, 11.5_

- [x] 9. Checkpoint — Full integration verification
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Unit tests
  - [ ]* 10.1 Write unit tests for LogService
    - Test `add_entry` creates entry with correct fields
    - Test `get_entries` returns entries for correct session only
    - Test 200-entry cap with FIFO eviction
    - Test `get_entries(since=T)` filters correctly
    - Test `clear_session` removes entries
    - Test thread-safety with concurrent add/get operations
    - _Requirements: 1.1, 1.2, 1.4, 1.5_

  - [ ]* 10.2 Write unit tests for CameraService (mock cv2)
    - Mock `cv2.VideoCapture` to simulate available, not_detected, error paths
    - Test `check_availability()` returns correct status for each path
    - Test `capture_preview_frame()` returns JPEG bytes on success, None on failure
    - Test camera is released after capture
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [ ]* 10.3 Write unit tests for ModelService (temp files)
    - Use `tmp_path` fixture to create/omit model files
    - Test returns `available` when file exists
    - Test returns `not_found` when file does not exist
    - _Requirements: 5.1, 5.2_

  - [ ]* 10.4 Write unit tests for MonitoringWorker log emissions
    - Mock LogService and verify each lifecycle event emits correct LogEntry
    - Verify log messages match Requirement 7 specifications
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10, 7.11_

  - [ ]* 10.5 Write property tests for LogService (Hypothesis)
    - **Property 1: Log entry storage round-trip**
    - **Validates: Requirements 1.1, 1.2**

  - [ ]* 10.6 Write property test for FIFO eviction cap (Hypothesis)
    - **Property 2: LogService 200-entry cap with FIFO eviction**
    - **Validates: Requirements 1.4**

  - [ ]* 10.7 Write property test for timestamp ordering (Hypothesis)
    - **Property 3: LogService retrieval is ordered by timestamp ascending**
    - **Validates: Requirements 1.5**

  - [ ]* 10.8 Write property test for since-filter (Hypothesis)
    - **Property 4: Log entry timestamp filtering**
    - **Validates: Requirements 9.2**

  - [ ]* 10.9 Write property test for elapsed time formatting (Hypothesis)
    - **Property 5: Elapsed time MM:SS formatting**
    - **Validates: Requirements 6.1**

- [x] 11. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The design uses Python — no language selection needed
- LogService is a singleton stored on `app.state` for thread-safe sharing between worker thread and HTTP handlers
- CameraService is stateless and instantiated per-request (no persistent connection)
- Abstract interfaces (RobotMovementService, DecisionService) have no implementation — stubs only

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.3", "2.1", "2.2"] },
    { "id": 1, "tasks": ["1.2", "3.1"] },
    { "id": 2, "tasks": ["3.2", "4.1"] },
    { "id": 3, "tasks": ["4.2"] },
    { "id": 4, "tasks": ["6.1", "7.1"] },
    { "id": 5, "tasks": ["8.1"] },
    { "id": 6, "tasks": ["10.1", "10.2", "10.3"] },
    { "id": 7, "tasks": ["10.4", "10.5", "10.6", "10.7", "10.8", "10.9"] }
  ]
}
```
