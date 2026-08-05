# Implementation Plan

## Overview

This task list implements the monitoring session flow fix using the exploratory bugfix workflow: write tests to confirm the bug exists, write preservation tests for non-buggy behavior, implement the fix, then verify all tests pass. The fix refactors `monitoring_start` and `monitoring_abort` routes in `app/routes/agricultural_ui.py` to delegate to `MonitoringService` instead of calling repositories directly.

## Tasks

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Start/Abort Routes Bypass MonitoringService
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the routes bypass MonitoringService
  - **Scoped PBT Approach**: Scope the property to concrete failing cases:
    - POST `/modulos/{id}/monitoreo/iniciar` with valid dimensions → assert `MonitoringService.start_session()` is called
    - POST `/monitoreos/{id}/abortar` for existing monitoring → assert `MonitoringService.abort_session()` is called
  - Test implementation:
    - Use FastAPI TestClient with mocked `MonitoringService` (patch `get_monitoring_service`)
    - Mock `monitoring_repo`, `module_repo`, `create_frame_source`, `SnapshotInferenceRunner`
    - Submit valid start form (width=5.0, length=2.0, module exists) → assert `start_session` was called
    - Submit abort request (monitoring exists) → assert `abort_session` was called
    - Use Hypothesis to generate random valid dimension pairs (positive floats) for the start route
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (MonitoringService is never called - this proves the bug exists)
  - Document counterexamples: e.g., "POST start with width=5.0, length=2.0 → monitoring_repo.create() called directly, MonitoringService.start_session() never invoked"
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.3, 1.4, 1.5_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Dimension Validation and Non-Existent Entity Handling
  - **IMPORTANT**: Follow observation-first methodology
  - Observe behavior on UNFIXED code for non-buggy inputs (inputs that never reach MonitoringService):
    - Observe: POST start with width=0 → validation error displayed on setup screen
    - Observe: POST start with width=-1 → validation error displayed on setup screen
    - Observe: POST start with width="abc" → validation error displayed on setup screen
    - Observe: POST start for non-existent module → redirect to `/invernaderos?error=Módulo+no+encontrado`
    - Observe: POST abort for non-existent monitoring → redirect to `/invernaderos?error=Monitoreo+no+encontrado`
  - Write property-based tests using Hypothesis:
    - Generate invalid dimensions (zero, negative, non-numeric strings) → assert response is validation error template (status 200 with error messages), MonitoringService never called
    - Generate non-existent module IDs → assert redirect to greenhouses with error
    - Generate non-existent monitoring IDs for abort → assert redirect to greenhouses with error
  - Verify tests PASS on UNFIXED code (confirms baseline behavior to preserve)
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.5_

- [x] 3. Refactor `monitoring_start` route to delegate to MonitoringService

  - [x] 3.1 Implement the monitoring_start refactoring
    - Add imports: `ActiveSessionError` from `src.application.services.monitoring_service`, `create_frame_source` from `src.application.services.frame_source_factory`, `get_log_service` from `app.dependencies`
    - Remove duplicated active-session check (manual iteration over `monitoring_repo.get_by_module()` with `active_statuses` set)
    - Remove direct `monitoring_repo.create()` call
    - Construct dependencies: `frame_source` via `create_frame_source()`, `inference_runner` via `SnapshotInferenceRunner` (from app.state models or factory), `db_session` from request-scoped session, `log_service` from app state
    - If `create_frame_source()` returns None → render Spanish error "La cámara no está disponible. Verifica la conexión y vuelve a intentar." on setup template
    - If models unavailable for inference_runner → render Spanish error "No se pudieron cargar los modelos de inferencia. Verifica que los archivos estén en su lugar." on setup template
    - Call `MonitoringService.start_session()` with `module_id`, `width`, `length`, `notes`, `frame_source`, `inference_runner`, `db_session`, `log_service`
    - Catch `ActiveSessionError` → redirect to `/monitoreos/{existing_monitoring_id}/ejecucion`
    - Catch general exceptions → render user-friendly Spanish error on setup template
    - Keep dimension validation BEFORE any service call (unchanged)
    - Keep auto-save of confirmed dimensions to module record (unchanged)
    - _Bug_Condition: isBugCondition(input) where input.route = "POST /modulos/{id}/monitoreo/iniciar" AND dimensions valid_
    - _Expected_Behavior: MonitoringService.start_session() called with correct dependencies, worker spawned, redirect to execution screen_
    - _Preservation: Dimension validation, non-existent module redirect, auto-save dimensions — all unchanged_
    - _Requirements: 2.1, 2.2, 2.4, 2.5, 3.1, 3.2, 3.6, 3.7_

  - [x] 3.2 Implement the monitoring_abort refactoring
    - Replace direct `monitoring_repo.update_status(id, "aborted")` with `MonitoringService.abort_session(id)`
    - Import `MonitoringNotFoundError`, `InvalidTransitionError` from `src.application.services.monitoring_service`
    - Handle `MonitoringNotFoundError` → redirect to `/invernaderos?error=Monitoreo+no+encontrado`
    - Handle `InvalidTransitionError` → redirect gracefully to module detail page
    - Keep route code thin — delegate lifecycle management to MonitoringService
    - _Bug_Condition: isBugCondition(input) where input.route = "POST /monitoreos/{id}/abortar" AND monitoring exists_
    - _Expected_Behavior: MonitoringService.abort_session() called, worker signaled, partial metrics computed_
    - _Preservation: Non-existent monitoring redirect unchanged_
    - _Requirements: 2.3, 3.4, 3.5_

  - [x] 3.3 Add `get_inference_runner(request)` helper in `app/dependencies.py` (if needed)
    - Factory/helper that constructs `SnapshotInferenceRunner` with loaded models from `app.state`
    - If models are already cached at app startup in `app.state`, retrieve them directly
    - If not cached, document the performance cost of on-demand loading (~2-3s on RPi)
    - Ensure ARM64 compatibility (CPU-only, no CUDA)
    - _Requirements: 2.5_

  - [x] 3.4 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Start/Abort Routes Delegate to MonitoringService
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior (MonitoringService is called)
    - When this test passes, it confirms the expected behavior is satisfied
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed — service is now called)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 3.5 Verify preservation tests still pass
    - **Property 2: Preservation** - Dimension Validation and Non-Existent Entity Handling
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions in validation or error handling)
    - Confirm all tests still pass after fix (no regressions)
    - _Requirements: 3.1, 3.2, 3.5_

- [x] 4. Checkpoint - Ensure all tests pass
  - Run full test suite: `pytest tests/ -v`
  - Ensure Property 1 (bug condition) passes after fix
  - Ensure Property 2 (preservation) passes after fix
  - Ensure no other existing tests are broken
  - Ask the user if questions arise

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2"] },
    { "id": 1, "tasks": ["3.1", "3.2", "3.3"] },
    { "id": 2, "tasks": ["3.4", "3.5"] },
    { "id": 3, "tasks": ["4"] }
  ]
}
```

Tasks 1 and 2 can run in parallel (wave 0). Tasks 3.1, 3.2, 3.3 depend on 1 and 2 (wave 1). Tasks 3.4, 3.5 depend on 3.1-3.3 (wave 2). Task 4 depends on all prior tasks (wave 3).

## Notes

- Do NOT modify `MonitoringService`, `MonitoringWorker`, or `video_inspection_runner.py`
- `get_monitoring_service(request)` already exists in `app/dependencies.py`
- `create_frame_source()` factory already exists in `src/application/services/frame_source_factory.py`
- Tests use Hypothesis for property-based testing (already a project dependency — see `.hypothesis/` directory)
- Keep route code thin — delegate all lifecycle logic to `MonitoringService`
- Spanish error messages for the farmer (see UX steering for message standards)
- Must work on both PC (graceful camera/model unavailability) and Raspberry Pi (picamera2 + real models)
