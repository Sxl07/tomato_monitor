# Monitoring Session Flow Fix — Bugfix Design

## Overview

The UI routes for starting and aborting monitoring sessions bypass `MonitoringService` and interact directly with repositories. This means `monitoring_start` never spawns a background worker (leaving sessions stuck in "initializing"), duplicates the active-session invariant check, and shows a dead error instead of redirecting to the active session. Similarly, `monitoring_abort` never signals the worker or computes partial metrics. The fix delegates both routes to the existing `MonitoringService` methods, which already implement correct lifecycle management.

## Glossary

- **Bug_Condition (C)**: The UI route handles monitoring start/abort by calling repositories directly, bypassing `MonitoringService`
- **Property (P)**: Routes delegate to `MonitoringService.start_session()` and `MonitoringService.abort_session()`, which spawn workers, enforce invariants, and compute metrics
- **Preservation**: Input validation, dimension auto-save, error rendering, non-existent module/monitoring redirects, and all non-monitoring routes must remain unchanged
- **`MonitoringService`**: Application service in `src/application/services/monitoring_service.py` that orchestrates monitoring lifecycle (create → worker → metrics → finalize)
- **`ActiveSessionError`**: Domain exception raised by `MonitoringService.start_session()` when a module already has a non-terminal session; carries `existing_monitoring_id`
- **`FrameSource`**: Interface for camera backends; obtained from `create_frame_source()` factory
- **`SnapshotInferenceRunner`**: Facade over detector + health classifier + maturity estimator; requires pre-loaded models
- **`MonitoringWorker`**: Background thread that runs capture loop + inference, communicating via `threading.Event` signals

## Bug Details

### Bug Condition

The bug manifests whenever the user submits the monitoring start form or clicks abort on an active monitoring. The `monitoring_start` route creates monitoring entities via `monitoring_repo.create()` directly, never calling `MonitoringService.start_session()`, so no background worker is spawned. The `monitoring_abort` route calls `monitoring_repo.update_status(id, "aborted")` directly, never calling `MonitoringService.abort_session()`, so the worker is never signaled and partial metrics are never computed.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type HTTPRequest
  OUTPUT: boolean
  
  RETURN (input.method = "POST" AND input.path MATCHES "/modulos/{id}/monitoreo/iniciar"
          AND input.form_data passes dimension validation)
         OR (input.method = "POST" AND input.path MATCHES "/monitoreos/{id}/abortar"
             AND monitoring exists)
END FUNCTION
```

### Examples

- **Start session (happy path)**: User submits valid dimensions for module 1 → currently creates DB record with status "initializing" and redirects to execution screen, but no worker runs, session never transitions to "running", counters stay at 0
- **Start session (active session exists)**: User submits form for module that already has a running monitoring → currently displays static error "Este módulo ya tiene un monitoreo activo" instead of redirecting to `/monitoreos/{active_id}/ejecucion`
- **Abort session**: User clicks abort on monitoring 5 → currently sets status to "aborted" in DB but worker thread continues capturing frames, no partial metrics are computed
- **Start session (no camera)**: User submits form but no camera is available → currently would create a stuck session; correct behavior is for `MonitoringService` to fail fast with a descriptive error before creating the session

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Dimension validation (zero, negative, non-numeric values) must continue to display errors on the setup screen before any service call
- Notes validation and sanitization must remain unchanged
- Auto-save of confirmed dimensions to the module record must remain unchanged
- Non-existent module redirects to `/invernaderos?error=Módulo+no+encontrado`
- Non-existent monitoring redirects to `/invernaderos?error=Monitoreo+no+encontrado`
- Model availability check on the GET setup screen must remain unchanged
- All non-monitoring routes (greenhouses, modules CRUD) are completely unaffected
- The execution screen rendering (GET `/monitoreos/{id}/ejecucion`) remains unchanged
- Hardware/system error messages displayed in Spanish on the setup screen

**Scope:**
All inputs that do NOT reach the `MonitoringService.start_session()` or `MonitoringService.abort_session()` call should be completely unaffected by this fix. This includes:
- GET requests to any screen
- POST requests that fail dimension validation (rejected before service call)
- POST requests for non-existent modules/monitorings (rejected before service call)
- All greenhouse and module CRUD operations

## Hypothesized Root Cause

Based on the code analysis of `app/routes/agricultural_ui.py`:

1. **Direct Repository Usage in `monitoring_start`**: Lines create `Monitoring` entity and call `monitoring_repo.create(id, monitoring)` directly, bypassing `MonitoringService.start_session()`. The service was available via `get_monitoring_service(request)` but never invoked.

2. **Missing Dependency Construction**: `MonitoringService.start_session()` requires `frame_source`, `inference_runner`, `db_session`, and `log_service` — none of which are constructed in the current route. The route was written before these dependencies were wired.

3. **Duplicated Active-Session Logic**: The route manually iterates `monitoring_repo.get_by_module(id)` and checks `m.status in active_statuses`, duplicating the invariant that `MonitoringService` already enforces by raising `ActiveSessionError`.

4. **Direct Status Update in `monitoring_abort`**: The route calls `monitoring_repo.update_status(id, "aborted")` directly instead of `MonitoringService.abort_session(monitoring_id)`, which would signal the worker, compute partial metrics, and clean up resources.

5. **Missing ActiveSessionError Handling**: Since `MonitoringService` was never called, the route never had a reason to catch `ActiveSessionError` and extract `existing_monitoring_id` for the redirect.

## Correctness Properties

Property 1: Bug Condition - Start Route Delegates to MonitoringService

_For any_ valid POST request to `/modulos/{id}/monitoreo/iniciar` where dimension validation passes and the module exists, the fixed route SHALL delegate to `MonitoringService.start_session()` with correctly constructed `frame_source`, `inference_runner`, `db_session`, and `log_service` dependencies, resulting in a monitoring entity with an associated background worker that transitions from "initializing" to "running".

**Validates: Requirements 2.1, 2.5**

Property 2: Bug Condition - Active Session Redirects to Execution

_For any_ valid POST request to `/modulos/{id}/monitoreo/iniciar` where the module already has an active (non-terminal) session, the fixed route SHALL catch `ActiveSessionError` and return a redirect to `/monitoreos/{existing_monitoring_id}/ejecucion` instead of displaying a static error message.

**Validates: Requirements 2.2, 2.4**

Property 3: Bug Condition - Abort Route Delegates to MonitoringService

_For any_ valid POST request to `/monitoreos/{id}/abortar` where the monitoring exists and is in an abortable state, the fixed route SHALL delegate to `MonitoringService.abort_session(id)` which signals the worker to stop, computes partial metrics, and transitions status to "aborted".

**Validates: Requirements 2.3**

Property 4: Preservation - Input Validation Unchanged

_For any_ POST request to `/modulos/{id}/monitoreo/iniciar` where dimension validation fails (zero, negative, non-numeric) OR the module does not exist, the fixed route SHALL produce the same response as the original route (validation error template or redirect), never reaching `MonitoringService`.

**Validates: Requirements 3.1, 3.2, 3.6**

Property 5: Preservation - Non-Monitoring Routes Unchanged

_For any_ request that does NOT target `/modulos/{id}/monitoreo/iniciar` or `/monitoreos/{id}/abortar`, the fixed code SHALL produce exactly the same behavior as the original code, preserving all greenhouse CRUD, module CRUD, execution screen, and report screen functionality.

**Validates: Requirements 3.3, 3.4, 3.5**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `app/routes/agricultural_ui.py`

**Function**: `monitoring_start`

**Specific Changes**:
1. **Import `ActiveSessionError`**: Add import from `src.application.services.monitoring_service`
2. **Import dependencies**: Add imports for `create_frame_source`, `get_unavailability_reason` from `src.application.services.frame_source_factory`, and `get_log_service` from `app.dependencies`
3. **Remove duplicated active-session check**: Delete the manual iteration over `monitoring_repo.get_by_module(id)` and the `active_statuses` set
4. **Construct frame_source**: Call `create_frame_source()` — if None, render error "La cámara no está disponible..."
5. **Construct inference_runner**: Build `SnapshotInferenceRunner` from app-state cached models (or a factory). If models unavailable, render error
6. **Obtain db_session and log_service**: Get from request-scoped dependencies
7. **Call `MonitoringService.start_session()`**: Pass `module_id`, `width`, `length`, `validated_notes`, `frame_source`, `inference_runner`, `db_session`, `log_service`
8. **Catch `ActiveSessionError`**: Extract `existing_monitoring_id` and redirect to `/monitoreos/{existing_monitoring_id}/ejecucion`
9. **Catch `ParentNotFoundError`**: Redirect to `/invernaderos?error=Módulo+no+encontrado` (already handled but good as safety net)
10. **Catch general exceptions**: Map to user-friendly Spanish error messages on the setup template

**Function**: `monitoring_abort`

**Specific Changes**:
1. **Replace direct `update_status` call**: Use `get_monitoring_service(request).abort_session(id)` instead of `monitoring_repo.update_status(id, "aborted")`
2. **Handle `MonitoringNotFoundError`**: Redirect to `/invernaderos?error=Monitoreo+no+encontrado`
3. **Handle `InvalidTransitionError`**: If session is already in a terminal state, redirect gracefully to module detail

**File**: `app/dependencies.py` (possibly)

**Function**: New helper `get_inference_runner(request)` or inline construction

**Specific Changes**:
1. **Add factory for `SnapshotInferenceRunner`**: If models are cached at app startup in `app.state`, retrieve them. Otherwise, load on demand (documenting the performance cost)
2. **Consider lazy singleton pattern**: Since model loading is heavy (~2-3s on RPi), models should ideally be loaded once at app startup and stored on `app.state`. If not already done, add a startup event or use existing `ModelService` to check/load

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write integration tests using FastAPI TestClient that submit monitoring start forms and verify whether `MonitoringService.start_session()` is called. Mock `MonitoringService` to track invocations.

**Test Cases**:
1. **Start bypasses service**: Submit valid start form → assert `monitoring_repo.create()` is called directly without `MonitoringService.start_session()` (will demonstrate bug on unfixed code)
2. **No worker spawned**: Submit valid start form → assert no background thread is created (will demonstrate bug on unfixed code)
3. **Active session shows dead error**: Submit start form for module with active session → assert response contains static error text instead of redirect (will demonstrate bug on unfixed code)
4. **Abort bypasses service**: Submit abort request → assert `MonitoringService.abort_session()` is never called (will demonstrate bug on unfixed code)

**Expected Counterexamples**:
- `MonitoringService.start_session()` is never invoked on any start request
- No daemon thread with name pattern `monitoring-worker-*` is created
- Active session results in error template, not redirect

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) AND input.route = "start" DO
  result := monitoring_start_fixed(input)
  ASSERT MonitoringService.start_session was called with correct args
  ASSERT result.monitoring has background worker thread
  ASSERT result redirects to /monitoreos/{id}/ejecucion
END FOR

FOR ALL input WHERE isBugCondition(input) AND input.route = "start" 
  AND module_has_active_session(input.module_id) DO
  result := monitoring_start_fixed(input)
  ASSERT result is RedirectResponse to /monitoreos/{active_id}/ejecucion
END FOR

FOR ALL input WHERE isBugCondition(input) AND input.route = "abort" DO
  result := monitoring_abort_fixed(input)
  ASSERT MonitoringService.abort_session was called
  ASSERT worker received abort signal
  ASSERT partial metrics computed
  ASSERT result redirects to /modulos/{module_id}
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT F(input) = F'(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many form inputs with invalid dimensions to verify validation still rejects them
- It generates edge cases (empty strings, negative numbers, very large values) to verify the route still returns error templates
- It provides strong guarantees that dimension validation behavior is unchanged

**Test Plan**: Observe behavior on UNFIXED code first for invalid dimension inputs and non-existent modules, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Dimension Validation Preservation**: Verify invalid dimensions (zero, negative, non-numeric) continue to display validation errors on the setup screen without reaching the service
2. **Non-Existent Module Preservation**: Verify requests for non-existent module IDs continue to redirect to `/invernaderos?error=Módulo+no+encontrado`
3. **Non-Existent Monitoring Preservation**: Verify abort requests for non-existent monitoring IDs continue to redirect to `/invernaderos?error=Monitoreo+no+encontrado`
4. **Dimension Auto-Save Preservation**: Verify confirmed dimensions are still saved to the module record before starting the session

### Unit Tests

- Test that `monitoring_start` calls `MonitoringService.start_session()` with mocked dependencies
- Test that `ActiveSessionError` results in redirect to execution screen with correct monitoring ID
- Test that `monitoring_abort` calls `MonitoringService.abort_session()` with mocked service
- Test that `InvalidTransitionError` on abort results in graceful redirect
- Test that camera unavailability (`create_frame_source()` returns None) renders user-friendly error
- Test that model load failure renders user-friendly error in Spanish

### Property-Based Tests

- Generate random dimension strings (valid and invalid) and verify the route either validates+delegates or rejects+renders error consistently
- Generate random module IDs (existing and non-existing) and verify correct routing behavior
- Generate random monitoring states and verify abort handles each state correctly (abortable vs already terminal)

### Integration Tests

- Test full start flow with mocked `FrameSource` and `SnapshotInferenceRunner`: form submission → service call → redirect to execution screen
- Test active session redirect flow: start when active → catch `ActiveSessionError` → redirect with correct ID
- Test abort flow with mocked worker: abort request → service signals worker → redirect to module detail
- Test hardware error flow: `create_frame_source()` returns None → error displayed on setup screen
