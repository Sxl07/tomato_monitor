# Bugfix Requirements Document

## Introduction

The agricultural UI route `POST /modulos/{id}/monitoreo/iniciar` bypasses `MonitoringService.start_session()` by calling `monitoring_repo.create()` directly. This creates monitoring sessions in the database without spawning a background worker, leaving sessions permanently stuck in "initializing" status with no capture or inference occurring. Similarly, the abort route `POST /monitoreos/{id}/abortar` bypasses `MonitoringService.abort_session()`, calling `monitoring_repo.update_status()` directly, which fails to signal the worker to stop and skips partial metrics computation. The active-session check is also duplicated in the UI instead of relying on the service's `ActiveSessionError` enforcement.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the user submits the monitoring start form THEN the system creates a Monitoring entity via `monitoring_repo.create()` directly without spawning a background worker, leaving the session stuck in "initializing" status indefinitely

1.2 WHEN the user submits the monitoring start form AND a module already has an active session THEN the system displays a static error message "Este módulo ya tiene un monitoreo activo" instead of redirecting the user to the active session's execution screen

1.3 WHEN the user clicks abort on an active monitoring THEN the system updates the status to "aborted" via `monitoring_repo.update_status()` directly without signaling the background worker to stop and without computing partial metrics

1.4 WHEN the user starts a monitoring THEN the system performs its own active-session validation by iterating through `monitoring_repo.get_by_module()` results, duplicating logic already implemented in `MonitoringService`

1.5 WHEN the user starts a monitoring THEN the system does not construct or pass the required dependencies (`frame_source`, `inference_runner`, `db_session`, `log_service`) needed for `MonitoringService.start_session()` to spawn the capture worker

### Expected Behavior (Correct)

2.1 WHEN the user submits the monitoring start form THEN the system SHALL delegate to `MonitoringService.start_session()` which creates the session AND spawns a background worker that transitions the session to "running" and begins capture/inference

2.2 WHEN the user submits the monitoring start form AND a module already has an active session THEN the system SHALL catch `ActiveSessionError` and redirect the user to the existing active monitoring's execution screen (`/monitoreos/{existing_id}/ejecucion`)

2.3 WHEN the user clicks abort on an active monitoring THEN the system SHALL delegate to `MonitoringService.abort_session()` which signals the worker to stop, computes partial metrics, and transitions the session to "aborted"

2.4 WHEN the user starts a monitoring THEN the system SHALL rely on `MonitoringService` to enforce the one-active-session-per-module invariant via `ActiveSessionError`, removing duplicated validation from the UI route

2.5 WHEN the user starts a monitoring THEN the system SHALL construct and inject all required dependencies (`frame_source` from `FrameSourceFactory`, `inference_runner` as `SnapshotInferenceRunner`, `db_session` from the request-scoped session, `log_service` from app state) into the `MonitoringService.start_session()` call

### Unchanged Behavior (Regression Prevention)

3.1 WHEN the user submits the monitoring start form with invalid dimensions (zero, negative, or non-numeric) THEN the system SHALL CONTINUE TO display validation errors on the setup screen without creating any session

3.2 WHEN the user starts a monitoring for a module that does not exist THEN the system SHALL CONTINUE TO redirect to the greenhouses page with an error message

3.3 WHEN the user views the monitoring execution screen THEN the system SHALL CONTINUE TO display the snapshot-driven execution view (last snapshot + log + counters), not video streaming

3.4 WHEN the monitoring completes or is aborted THEN the system SHALL CONTINUE TO redirect to the module detail screen

3.5 WHEN the user accesses abort for a non-existent monitoring THEN the system SHALL CONTINUE TO redirect to the greenhouses page with an error message

3.6 WHEN a hardware error occurs during monitoring start (camera unavailable, disk space low, model load failure) THEN the system SHALL CONTINUE TO display a user-friendly error message in Spanish on the setup screen

3.7 WHEN the user starts a monitoring THEN the system SHALL CONTINUE TO auto-save the confirmed dimensions to the module record

---

## Bug Condition

```pascal
FUNCTION isBugCondition(X)
  INPUT: X of type MonitoringStartRequest
  OUTPUT: boolean
  
  // The bug triggers whenever the UI route handles monitoring start or abort,
  // because it always bypasses MonitoringService regardless of input values.
  RETURN X.route = "POST /modulos/{id}/monitoreo/iniciar" 
         OR X.route = "POST /monitoreos/{id}/abortar"
END FUNCTION
```

## Fix Checking Property

```pascal
// Property: Fix Checking — Start delegates to service
FOR ALL X WHERE isBugCondition(X) AND X.route = "POST /modulos/{id}/monitoreo/iniciar" DO
  result ← monitoring_start'(X)
  ASSERT MonitoringService.start_session was called
  ASSERT result.monitoring has an associated background worker
  ASSERT result.monitoring.status transitions from "initializing" to "running"
END FOR

// Property: Fix Checking — Active session redirect
FOR ALL X WHERE isBugCondition(X) AND X.route = "POST /modulos/{id}/monitoreo/iniciar" 
  AND module_has_active_session(X.module_id) DO
  result ← monitoring_start'(X)
  ASSERT result is RedirectResponse to "/monitoreos/{active_id}/ejecucion"
END FOR

// Property: Fix Checking — Abort delegates to service
FOR ALL X WHERE isBugCondition(X) AND X.route = "POST /monitoreos/{id}/abortar" DO
  result ← monitoring_abort'(X)
  ASSERT MonitoringService.abort_session was called
  ASSERT worker received abort signal
  ASSERT partial metrics were computed
END FOR
```

## Preservation Checking Property

```pascal
// Property: Preservation — Non-buggy routes behave identically
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
END FOR
```
