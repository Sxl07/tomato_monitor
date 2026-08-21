# Bugfix Requirements Document

## Introduction

Este documento describe las correcciones identificadas durante la validación física del sistema Tomato Monitor en Raspberry Pi 5 con Raspberry Pi AI Camera (IMX500), pantalla DSI 7" en orientación portrait (480×800) y ejecución local/offline.

La validación confirmó que la arquitectura funciona correctamente: 145/145 tests pasan en Raspberry Pi (tras instalar httpx manualmente), el pipeline capture-first opera end-to-end, y la plataforma base (dashboard, CRUD, monitoreo, actividades, exportación, sincronización) funciona. Las correcciones aquí documentadas son ajustes puntuales nacidos de la prueba real en hardware, no rediseños ni funcionalidades nuevas.

**Branch:** `feature/015-portable-monitoring-scope`

**Restricción crítica:** NO romper el pipeline capture-first, FSM, camera ownership, thermal monitoring ni vision infrastructure.

---

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the dashboard is displayed on Raspberry Pi in portrait mode THEN the system shows a redundant "Acceso rápido" section with links to "Ver invernaderos", "Iniciar monitoreo", "Exportar datos", "Sincronización" that duplicates the bottom navigation bar

1.2 WHEN the UI is rendered on Raspberry Pi OS THEN many emojis used as functional iconography (🏠🌱📦🔄⚠️📷💧🧪🍅✂️🍃🌿🧹👁️🔍📝📋🗑✎🌡️) render as squares due to missing/incompatible emoji fonts. Emojis appear in: `base_agricultural.html` bottom nav, `dashboard.html` alerts, `module_detail.html` header actions, `greenhouse_detail.html` header actions, `greenhouse_list.html` alerts, `monitoring_execution.html` thermal/status icons, `export_detail.html` warnings, `error.html`, `login.html` branding, and `history_service.py` activity/monitoring icons.

1.3 WHEN the UI is displayed in portrait orientation (480×800) THEN some controls are too close to screen edges with insufficient lateral separation, risking mis-taps and clipped content

1.4 WHEN an operator starts a new monitoring THEN the system requires both `width_m` and `length_m` as mandatory fields, preventing monitoring start even though dimensions are not needed for vision inference. The mandatory constraint exists across all layers: domain entity (`Monitoring.width_m: float`), ORM model (`MonitoringModel.width_m nullable=False`), validator (`validate_dimensions()` rejects empty), route (`Form(...)` requires value), template (`required` attribute), MonitoringService.start_session signature, and the physical SQLite schema (columns created as NOT NULL).

1.5 WHEN the EDGE profile is used for analysis on Raspberry Pi THEN `analysis_thermal_pause_threshold=72°C` causes excessive pausing because the SoC routinely operates at 72-76°C during inference, making 14-snapshot analysis take several minutes with repeated thermal pauses

1.6 WHEN activities or monitorings are displayed in certain views THEN timestamps stored as UTC naive appear without timezone conversion, showing (e.g.) 02:19 instead of the actual local time ~21:17 America/Bogota. Affected locations include: `dashboard.html` (last_monitoring.started_at, activity.occurred_at), `module_detail.html` (history items date_display/time_display), `activity_list.html` (occurred_at), `export_list.html` (created_at), `export_detail.html` (created_at, completed_at), `sync_status.html` (last_export.created_at), `history_service.py` (strftime on monitoring.started_at and log.occurred_at), `context_builders.py` (_format_date_spanish, _format_time).

1.7 WHEN LogService records events using `datetime.utcnow()` THEN the serialized timestamp lacks timezone indicator (no 'Z' suffix or offset), making it ambiguous whether the value is UTC or local time in the frontend. The `monitoring_log` API endpoint uses `entry.timestamp.isoformat()` which for naive datetimes produces no timezone suffix. The `since` parameter is parsed with `datetime.fromisoformat()` which may mix naive/aware comparisons in `get_entries()`.

1.8 WHEN the module detail page shows the combined history section THEN the title "Historial del módulo" does not clearly indicate that it includes both monitorings AND agricultural activities

1.9 WHEN the export screen describes the export action THEN the text does not make it clear that it generates a full local backup of all data and images stored on the device

1.10 WHEN completed exports are reviewed THEN some show `images_count=0` despite subsequently existing monitorings with snapshots. **Root cause not yet confirmed.** Possible explanations: (A) The export was generated BEFORE the monitorings with snapshots existed (correct behavior — 0 images). (B) A path inconsistency between ExportService image search and actual disk files. ExportService reconstructs filenames as `snapshot_{frame_index:06d}.jpg` and searches in `outputs/monitorings/{id}/annotated_snapshots/` and `outputs/monitorings/{id}/snapshots/raw/`. CaptureWorker stores `image_path=outputs/monitorings/{id}/snapshots/raw/snapshot_{snapshot_count:06d}.jpg` and assigns `frame_index=snapshot_count`. The `Snapshot.image_path` field is the authoritative source of where the file actually was written. The implementation should prefer `Snapshot.image_path` for robustness and add diagnostic tests to verify behavior in all cases.

1.11 WHEN the Python process restarts during export generation THEN ExportPackage records remain permanently in "generating" status with no reconciliation mechanism at startup

1.12 WHEN the synchronization UI is displayed THEN it does not make it explicit that no remote provider is configured and the system operates in local-only mode. Additionally, sync state meanings (pending, exported, synced) are not explained to the operator.

1.13 WHEN tests requiring FastAPI TestClient are executed THEN they fail because `httpx` is not declared in the project's dependency files (`requirements.txt` nor `requirements-raspberry.txt`), requiring manual installation

1.14 WHEN the operator enters a manual date/time in the activity form THEN that input is interpreted and stored as-is without timezone conversion. If the operator enters 21:17 (local Colombia time), it may be persisted and later re-displayed incorrectly because the system assumes stored datetimes are UTC.

### Expected Behavior (Correct)

2.1 WHEN the dashboard is displayed THEN the system SHALL NOT show the "Acceso rápido" section, relying solely on the bottom navigation bar for module access

2.2 WHEN the UI is rendered on any platform (including Raspberry Pi OS without emoji fonts) THEN the system SHALL use offline SVG-based icons instead of Unicode emojis for all functional iconography across ALL affected templates and services. A systematic sweep SHALL replace all emojis used as functional icons (not simple typographic chars like "+" where universally supported).

2.3 WHEN the UI is displayed in portrait orientation (480×800) THEN all interactive controls SHALL maintain at least 20px lateral separation from screen edges without causing horizontal overflow. Overflow SHALL be prevented by proper container sizing (box-sizing, max-width: 100%, min-width: 0) rather than overflow-x: hidden which masks problems.

2.4 WHEN an operator starts a new monitoring THEN dimensions (`width_m` and `length_m`) SHALL be optional: both empty is valid, both filled is valid, only one filled SHALL produce a validation error. This change SHALL be applied end-to-end including: domain entity, ORM model, physical SQLite schema (migration for existing databases), validator, MonitoringService.start_session, route, template, all mappers/converters, test fixtures, and any serializer/context that assumes float. Existing monitoring records with non-null dimensions SHALL be preserved.

2.5 WHEN the EDGE profile is used for analysis THEN `analysis_thermal_pause_threshold` SHALL be 78.0°C (pause at >= 78°C) and `analysis_thermal_resume_threshold` SHALL be 72.0°C

2.6 WHEN timestamps are displayed to the operator THEN the system SHALL convert UTC timestamps to America/Bogota timezone using a centralized utility. The utility SHALL provide bidirectional conversion: UTC→Bogota for display, Bogota→UTC for persistence of user input. All timestamp formatting in the presentation layer SHALL use a single source of truth (centralized utility or Jinja filter) without duplicating timezone logic.

2.7 WHEN LogService serializes timestamps THEN it SHALL use `datetime.now(timezone.utc)` (timezone-aware) and serialize with explicit UTC indicator. The `since` parameter comparison in `get_entries()` and the API endpoint SHALL normalize both sides to avoid naive/aware datetime mixing. The frontend SHALL receive ISO-8601 with 'Z' or '+00:00'.

2.8 WHEN the module detail page shows the combined history section THEN the title SHALL be "Historial general" with secondary text indicating it shows both monitorings and agricultural activities

2.9 WHEN the export screen describes the export action THEN the text SHALL clearly indicate it generates a complete local backup of all data and images stored on the device

2.10 WHEN ExportService searches for snapshot images THEN it SHALL prefer `Snapshot.image_path` as the authoritative source for locating raw images (supporting relative paths safely, with path traversal protection). For annotated images, it SHALL derive the path consistently from the raw filename. Diagnostic tests SHALL cover: (A) export before snapshots exist → images_count=0 correct, (B) snapshots exist with files on disk → included, (C) raw only → included, (D) annotated only → included, (E) both raw+annotated → both included with correct counts, (F) metadata exists but file missing → files_missing + warning in manifest.

2.11 WHEN the application starts THEN the system SHALL reconcile any ExportPackage records stuck in "generating" status by transitioning them to "error" with a message indicating process interruption

2.12 WHEN the synchronization UI is displayed and no remote provider is configured THEN the system SHALL show: (A) an explicit "Modo local" indicator, (B) explanation of states: pending = not yet included in local backup, exported = included in a local ZIP package, synced = reserved for future remote synchronization, (C) a note that LocalZipSyncAdapter never marks records as "synced"

2.13 WHEN the project's dependency files are used to install packages THEN `httpx` SHALL be declared as a test dependency. The relationship between `requirements.txt`, `requirements-raspberry.txt`, and the new test requirements file SHALL be documented with a clear reproducible procedure for running tests on Raspberry Pi without manual pip install commands.

2.14 WHEN the operator enters a manual date/time in the activity registration form THEN that input SHALL be interpreted as America/Bogota local time and converted to UTC before persistence. When displayed back, the stored UTC SHALL convert to America/Bogota showing the same time the operator originally entered.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN the capture-first pipeline is used (CaptureWorker → finalize → SnapshotAnalysisService) THEN the system SHALL CONTINUE TO capture snapshots, finalize capture, release camera, run deferred analysis, and generate reports without modification

3.2 WHEN the bottom navigation bar is displayed THEN it SHALL CONTINUE TO provide access to Dashboard, Módulos, Exportar, and Sync screens

3.3 WHEN monitoring dimensions are both provided with valid positive values THEN the system SHALL CONTINUE TO accept them and persist them in the Monitoring record

3.4 WHEN the FULL profile is active THEN its thermal thresholds (pause=78°C, resume=72°C) SHALL CONTINUE TO be unchanged

3.5 WHEN the monitoring state machine transitions occur THEN states (INITIALIZING → RUNNING → ANALYZING → COMPLETED) SHALL CONTINUE TO follow existing FSM rules

3.6 WHEN the existing 145 tests are run THEN they SHALL CONTINUE TO pass (with httpx now properly declared)

3.7 WHEN export packages complete successfully THEN they SHALL CONTINUE TO record status "completed" with file_path, file_size, and manifest

3.8 WHEN agricultural activities are registered THEN they SHALL CONTINUE TO record user_id, module_id, activity_type_id, occurred_at, and optional fields

3.9 WHEN the thermal monitor detects temperatures at or above 78°C in EDGE profile (after fix) THEN the system SHALL CONTINUE TO pause analysis to protect the hardware

3.10 WHEN alerts are computed THEN they SHALL CONTINUE TO be based on real system state (overdue modules, pending exports, analysis errors)

3.11 WHEN existing monitoring records in SQLite have non-null width_m/length_m THEN those values SHALL be preserved after migration without data loss

---

## Bug Condition Derivation

### Bug Condition Functions

```pascal
FUNCTION isBugCondition_Issue1(X)
  INPUT: X of type DashboardRenderRequest
  OUTPUT: boolean
  RETURN X.page = "dashboard"
  // Always triggers — the redundant section is always present
END FUNCTION

FUNCTION isBugCondition_Issue2(X)
  INPUT: X of type UIRenderRequest
  OUTPUT: boolean
  RETURN X.platform_has_emoji_font = False
  // Triggers on Raspberry Pi OS without emoji font packages
END FUNCTION

FUNCTION isBugCondition_Issue3(X)
  INPUT: X of type ViewportContext
  OUTPUT: boolean
  RETURN X.orientation = "portrait" AND X.width <= 480
END FUNCTION

FUNCTION isBugCondition_Issue4(X)
  INPUT: X of type MonitoringStartInput
  OUTPUT: boolean
  RETURN X.width_m = empty AND X.length_m = empty
  // Both empty should be valid but currently rejected across all layers
END FUNCTION

FUNCTION isBugCondition_Issue5(X)
  INPUT: X of type AnalysisExecution
  OUTPUT: boolean
  RETURN X.profile = "edge" AND X.soc_temperature >= 72.0 AND X.soc_temperature < 78.0
  // Normal operating range that currently triggers excessive pausing
END FUNCTION

FUNCTION isBugCondition_Issue6(X)
  INPUT: X of type TimestampDisplay
  OUTPUT: boolean
  RETURN X.stored_timezone = None AND X.display_requires_local = True
  // UTC naive timestamps displayed without conversion in ANY view
END FUNCTION

FUNCTION isBugCondition_Issue7(X)
  INPUT: X of type LogEntrySerialization
  OUTPUT: boolean
  RETURN X.source = "LogService" AND X.timezone_indicator = None
  // Also: since comparison mixes naive/aware datetimes
END FUNCTION

FUNCTION isBugCondition_Issue10(X)
  INPUT: X of type ExportImageSearch
  OUTPUT: boolean
  RETURN X.snapshot_exists_on_disk = True AND X.export_includes_image = False
  // Robustness: ExportService should use Snapshot.image_path as authoritative source
END FUNCTION

FUNCTION isBugCondition_Issue11(X)
  INPUT: X of type ApplicationStartup
  OUTPUT: boolean
  RETURN EXISTS(ExportPackage WHERE status = "generating")
  // Orphan records from interrupted process
END FUNCTION

FUNCTION isBugCondition_Issue14(X)
  INPUT: X of type ActivityFormSubmission
  OUTPUT: boolean
  RETURN X.manual_datetime != None AND X.timezone_conversion = None
  // User enters local time but system stores without UTC conversion
END FUNCTION
```

### Property Specifications

```pascal
// Property: Fix Checking — Issue 5 (Thermal thresholds)
FOR ALL X WHERE isBugCondition_Issue5(X) DO
  result ← EDGE_PROFILE.analysis_thermal_pause_threshold
  ASSERT result = 78.0
  // Pause only at >= 78°C in EDGE profile
END FOR

// Property: Fix Checking — Issue 4 (Optional dimensions)
FOR ALL X WHERE isBugCondition_Issue4(X) DO
  result ← validateOptionalDimensions'(X)
  ASSERT result.is_valid = True
  // Both empty is accepted
END FOR

// Property: Fix Checking — Issue 4 (SQLite migration)
FOR ALL existing_monitoring_row WHERE width_m IS NOT NULL DO
  result ← migration'(existing_monitoring_row)
  ASSERT result.width_m = existing_monitoring_row.width_m
  ASSERT result.length_m = existing_monitoring_row.length_m
  // Existing data preserved after schema migration
END FOR

// Property: Fix Checking — Issue 10 (Export image robustness)
FOR ALL snapshot WHERE snapshot.image_path exists on disk DO
  result ← ExportService'.includeImage(snapshot)
  ASSERT result.included = True
  ASSERT images_count > 0
END FOR

// Property: Fix Checking — Issue 10 (Export before snapshots — NOT a bug)
FOR ALL export WHERE export.created_at < earliest_snapshot.created_at DO
  ASSERT export.images_count = 0 IS CORRECT
END FOR

// Property: Fix Checking — Issue 11 (Orphan exports)
FOR ALL X WHERE isBugCondition_Issue11(X) DO
  result ← reconcileOrphanExports'(X)
  ASSERT ALL(ExportPackage WHERE previous_status = "generating").new_status = "error"
END FOR

// Property: Fix Checking — Issue 14 (Activity timezone)
FOR ALL X WHERE isBugCondition_Issue14(X) DO
  stored ← persistActivity'(X)
  displayed ← to_bogota(stored.occurred_at)
  ASSERT displayed = X.manual_datetime
  // Round-trip: user enters 21:17 → stored as 02:17 UTC → displayed as 21:17
END FOR
```

### Preservation Goal

```pascal
// Property: Preservation Checking
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
  // All non-buggy inputs produce identical behavior before and after fixes
END FOR

// Specific preservation: pipeline integrity
FOR ALL monitoring_session DO
  ASSERT capture_first_pipeline'(monitoring_session) = capture_first_pipeline(monitoring_session)
END FOR

// Specific preservation: existing tests
FOR ALL test IN existing_test_suite DO
  ASSERT test.result' = PASS
END FOR

// Specific preservation: existing data
FOR ALL row IN existing_sqlite_tables DO
  ASSERT row.data' = row.data
  // No data loss from schema migration
END FOR
```
