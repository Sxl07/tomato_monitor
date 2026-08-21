# Implementation Plan

## Overview

Correcciones puntuales identificadas durante la validación física en Raspberry Pi 5 (pantalla DSI 7", portrait 480×800, offline). 14 ajustes mínimos agrupados por dependencia y afinidad lógica.

**Orden de ejecución:**
- Fix 13 (httpx) primero — habilita la ejecución de tests
- Fix 6+14 (timezone utility bidireccional) antes de fixes que la usan
- Fix 4 (optional dimensions + SQLite migration) es end-to-end
- Fix 5 (thermal) es independiente y simple
- Fix 2 (icon system) es el cambio más grande
- Fix 10 y 11 (export) se agrupan
- Fixes de template/texto (1, 8, 9, 12) se agrupan
- Validación final

**Restricción crítica:** NO romper capture-first pipeline, FSM, camera ownership, thermal monitoring ni vision infrastructure.

## Task Dependency Graph

```json
{
  "waves": [
    {
      "name": "Wave 1 — Exploration and Preservation Tests",
      "tasks": [1, 2],
      "description": "Write bug condition and preservation tests BEFORE any fix"
    },
    {
      "name": "Wave 2 — Enable Test Infrastructure",
      "tasks": [3],
      "description": "httpx dependency enables test execution",
      "dependsOn": [1, 2]
    },
    {
      "name": "Wave 3 — Timezone Foundation + Activity Input",
      "tasks": [4],
      "description": "Centralized bidirectional timezone utility + Jinja filter + activity form fix",
      "dependsOn": [3]
    },
    {
      "name": "Wave 4 — Independent Fixes",
      "tasks": [5, 6, 7, 8, 9],
      "description": "EDGE thresholds, template text, optional dimensions, SVG icons, export fixes",
      "dependsOn": [4]
    },
    {
      "name": "Wave 5 — Verification and Spec Update",
      "tasks": [10, 11],
      "description": "Verify all fixes, run full test suite, update Spec 015",
      "dependsOn": [5, 6, 7, 8, 9]
    }
  ]
}
```

## Tasks

- [x] 1. Write bug condition exploration tests
  - **Property 1: Bug Condition** — Post-Raspberry Stabilization Defects
  - **CRITICAL**: These tests encode EXPECTED behavior — they will FAIL on unfixed code (confirming bugs exist)
  - **DO NOT attempt to fix the tests or the code when they fail**
  - Tests to write (file: `tests/properties/test_016_bug_conditions.py`):
    - Test EDGE profile `analysis_thermal_pause_threshold == 78.0` (currently 72.0 — will FAIL)
    - Test LogService timestamp has `tzinfo` (currently naive — will FAIL)
    - Test `to_bogota()` utility converts UTC naive to America/Bogota (function doesn't exist yet — will FAIL/ERROR)
    - Test `bogota_to_utc()` converts local 21:17 to UTC 02:17 (function doesn't exist — will FAIL/ERROR)
    - Test orphan export reconciliation transitions "generating" → "error" (no mechanism — will FAIL)
    - Test dashboard response does NOT contain "Acceso rápido" (currently present — will FAIL)
    - Test `validate_optional_dimensions("", "")` returns valid (None, None) (function doesn't exist — will FAIL/ERROR)
    - Test SQLite migration preserves existing monitoring data when changing nullable (will FAIL/ERROR)
    - Test history_service `_ACTIVITY_ICONS` does NOT contain emoji Unicode characters (currently does — will FAIL)
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests FAIL (this is correct — proves bugs exist)
  - Document counterexamples found
  - _Requirements: 1.1, 1.2, 1.4, 1.5, 1.6, 1.7, 1.10, 1.11, 1.14_

- [x] 2. Write preservation property tests (BEFORE implementing fixes)
  - **Property 2: Preservation** — Pipeline and Configuration Integrity
  - Observe behavior on UNFIXED code for non-buggy inputs:
    - Observe: FULL profile thresholds are pause=78.0, resume=72.0
    - Observe: Monitoring with width=5.0, length=3.0 is accepted and persisted
    - Observe: Monitoring FSM transitions follow INITIALIZING → RUNNING → ANALYZING → COMPLETED
    - Observe: ExportPackage with status="completed" retains correct metadata
    - Observe: Bottom navigation bar renders with 4 navigation items
    - Observe: CaptureWorker assigns frame_index=snapshot_count (path naming consistent)
  - Write property-based tests (file: `tests/properties/test_016_preservation.py`):
    - Property: FULL profile thermal thresholds remain unchanged (pause=78, resume=72)
    - Property: For all valid positive (width, length) pairs, dimension validation still accepts them
    - Property: Monitoring status transitions follow allowed FSM paths
    - Property: Completed exports retain file_path, file_size, and manifest
    - Property: CaptureWorker frame_index == snapshot_count (naming is consistent)
  - Verify tests PASS on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (confirms baseline behavior to preserve)
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.11_

- [x] 3. Fix httpx test dependency (Fix 13)

  - [x] 3.1 Create `requirements-test.txt` as pure overlay (NO `-r` reference)
    - Content: pytest>=8.0.0 + hypothesis>=6.100.0 + httpx>=0.27.0
    - Header comments: explain overlay usage with -r requirements.txt OR -r requirements-raspberry.txt
    - NOT: `-r requirements.txt` (would conflict with requirements-raspberry.txt on RPi)
    - _Requirements: 2.13_

  - [x] 3.2 Add SQLAlchemy to requirements-raspberry.txt and fix Python version docs
    - Add `SQLAlchemy==2.0.41` to `requirements-raspberry.txt` (currently missing, app needs it in runtime)
    - Fix Python version comment: validated environment uses Python 3.13.5, not 3.11
    - Review entire header — do not maintain Bookworm/3.11 as "currently validated" if no longer accurate
    - May add note distinguishing "baseline original" vs "post-Spec 015 validated environment"
    - _Requirements: 2.13_

  - [x] 3.3 Document reproducible test procedure
    - PC: `pip install -r requirements.txt -r requirements-test.txt`
    - Raspberry: `pip install -r requirements-raspberry.txt -r requirements-test.txt`
    - No manual httpx installation needed
    - _Requirements: 2.13_

  - [x] 3.4 Verify test suite runs without manual httpx installation
    - Run `python -m pytest --co -q` to confirm test collection works
    - _Requirements: 3.6_

- [x] 4. Implement centralized timezone utility — bidirectional (Fixes 6, 7, 14)

  - [x] 4.1 Create `src/application/utils/__init__.py` and `src/application/utils/timezone.py`
    - Implement: `utc_now()`, `to_bogota(dt)`, `bogota_to_utc(dt)`, `format_bogota(dt, fmt)`, `iso_utc(dt)`
    - Use `zoneinfo.ZoneInfo("America/Bogota")` — Python stdlib, no external deps
    - Handle: None input, naive UTC, aware UTC, naive Bogota
    - _Requirements: 2.6, 2.14_

  - [x] 4.2 Create Jinja filter module and register on template instances
    - NEW file: `src/application/utils/jinja_filters.py`
    - Implement `filter_to_bogota(dt, fmt)` and `register_filters(templates_instance)`
    - Register on ALL relevant Jinja2Templates instances: `agricultural_ui.py`, `auth.py`, and any other router rendering timestamps
    - Note: the app has NO global `app.jinja_env` — each router creates its own `Jinja2Templates` instance
    - Call `register_filters(templates)` after creating each instance
    - Add test confirming the filter is available and works in rendered templates
    - _Requirements: 2.6_

  - [x] 4.3 Apply timezone conversion in `src/application/services/history_service.py`
    - Use `format_bogota()` for `date_display` and `time_display` in both monitoring and activity items
    - _Requirements: 2.6_

  - [x] 4.4 Apply timezone conversion in `app/context_builders.py`
    - Update `_format_date_spanish()` and `_format_time()` to use `to_bogota()` before formatting
    - _Requirements: 2.6_

  - [x] 4.5 Apply Jinja filter in templates that call `.strftime()` directly
    - `app/templates/agricultural/dashboard.html` — last_monitoring.started_at, activity.occurred_at
    - `app/templates/agricultural/activity_list.html` — item.occurred_at
    - `app/templates/agricultural/export_list.html` — pkg.created_at
    - `app/templates/agricultural/export_detail.html` — package.created_at, package.completed_at
    - `app/templates/agricultural/sync_status.html` — last_export.created_at
    - Replace direct `.strftime(...)` with `| to_bogota` filter
    - _Requirements: 2.6_

  - [x] 4.6 Fix LogService ISO-8601 with 'Z' suffix (Fix 7)
    - File: `src/application/services/log_service.py`
    - Replace `datetime.utcnow()` with `datetime.now(timezone.utc)` in `add_entry()`
    - File: `src/application/services/log_service.py` — `get_entries(since=...)`
    - Normalize: if `since` is naive, assume UTC and make aware before comparing
    - File: `app/routes/monitoring_api.py`
    - Serialize with explicit 'Z': `entry.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")`
    - Parse `since`: if naive after fromisoformat, make aware as UTC
    - _Requirements: 2.7_

  - [x] 4.7 Fix activity form timezone conversion (Fix 14)
    - File: `app/routes/agricultural_ui.py` — function `activity_create()`
    - When user provides date/time: interpret as America/Bogota, convert to UTC via `bogota_to_utc()`
    - When user leaves date/time empty: use `utc_now().replace(tzinfo=None)` as before
    - _Requirements: 2.14_

  - [x] 4.8 Add unit tests for timezone utility
    - Test `to_bogota`: None → None, naive UTC → Bogota (-5h), aware UTC → Bogota
    - Test `bogota_to_utc`: local 21:17 → UTC 02:17 next day
    - Test round-trip: enter 21:17 local → persist as UTC → display as 21:17
    - Test `format_bogota`: various formats
    - Test `iso_utc`: naive UTC → includes 'Z', aware UTC → includes 'Z', aware Bogota → converts to UTC then 'Z'
    - Test Colombia has no DST (offset always -5h)
    - Test LogService entry has tzinfo after fix
    - Test LogService `get_entries(since=naive)` works without crash
    - Test LogService `get_entries(since=aware)` works correctly
    - Test LogService API serialization includes 'Z'
    - Test Jinja filter is registered and works in rendered template
    - _Requirements: 2.6, 2.7, 2.14_

- [x] 5. EDGE thermal threshold adjustment (Fix 5)

  - [x] 5.1 Update `src/infrastructure/config/settings.py`
    - Change EDGE: `analysis_thermal_pause_threshold=72.0` → `78.0`
    - Change EDGE: `analysis_thermal_resume_threshold=65.0` → `72.0`
    - Document: "pause at >= 78°C" (code uses >= comparison)
    - _Requirements: 2.5, 3.4, 3.9_

  - [x] 5.2 Add test verifying both profiles
    - Assert EDGE: pause=78.0, resume=72.0
    - Assert FULL: pause=78.0, resume=72.0 (unchanged)
    - _Requirements: 2.5, 3.4_

- [x] 6. Template text, UI clarity, and CSS fixes (Fixes 1, 3, 8, 9, 12)

  - [x] 6.1 Remove "Acceso rápido" section from dashboard (Fix 1)
    - File: `app/templates/agricultural/dashboard.html`
    - Delete the quick links section entirely
    - _Requirements: 2.1, 3.2_

  - [x] 6.2 Update history section title (Fix 8)
    - File: `app/templates/agricultural/module_detail.html`
    - "Historial del módulo" → "Historial general"
    - Add secondary: "Monitoreos y actividades agrícolas registradas en este módulo"
    - _Requirements: 2.8_

  - [x] 6.3 Clarify export description (Fix 9)
    - File: `app/templates/agricultural/export_list.html`
    - → "Genera un respaldo ZIP con los datos e imágenes almacenados actualmente en este dispositivo."
    - _Requirements: 2.9_

  - [x] 6.4 Add "Modo local" indicator + state explanations to sync UI (Fix 12)
    - File: `app/templates/agricultural/sync_status.html`
    - Add banner: "Modo local — No hay proveedor remoto configurado"
    - Add state legend: Pendiente / Exportado / Sincronizado with descriptions
    - _Requirements: 2.12_

  - [x] 6.5 CSS safe margins for portrait — NO overflow-x: hidden (Fix 3)
    - File: `app/static/css/agricultural.css`
    - Add 20px padding-left/right on `.main-content` and `.app-header` in portrait
    - Use `max-width: 100%` and `box-sizing: border-box` for cards/panels/alerts
    - Do NOT use `overflow-x: hidden` globally — correct individual components if they overflow
    - Keep bottom navigation full-bleed
    - _Requirements: 2.3_

  - [x] 6.6 Verify template changes
    - Run endpoint tests to confirm:
    - Dashboard does NOT contain "Acceso rápido"
    - Module detail contains "Historial general"
    - Export list contains "respaldo ZIP"
    - Sync status contains "Modo local"
    - _Requirements: 2.1, 2.8, 2.9, 2.12_

- [x] 7. Optional dimensions — end-to-end with SQLite migration (Fix 4)

  - [x] 7.1 Implement SQLite migration for nullable dimensions
    - File: `src/infrastructure/persistence/database.py`
    - Add `_migrate_dimensions_nullable(engine)` function
    - Strategy: PRAGMA table_info → detect NOT NULL → commit open txn → raw DBAPI: FK OFF → VERIFY FK==0 → BEGIN → CREATE monitorings_new → INSERT explicit cols → DROP old → RENAME new → foreign_key_check → if violations ROLLBACK+ERROR → else COMMIT → FK ON → VERIFY FK==1
    - MUST use raw DBAPI connection (conn.connection.dbapi_connection) for reliable PRAGMA outside autobegin
    - MUST verify FK OFF/ON via PRAGMA read immediately after setting
    - MUST run foreign_key_check BEFORE commit (allows rollback on failure)
    - MUST use explicit column list in INSERT (not SELECT *)
    - Call from `init_db()` BEFORE `_migrate_add_columns()`
    - Idempotent: if notnull flag already 0 → no-op
    - Fresh install: create_all makes nullable directly, migration detects notnull=0 → no-op
    - _Requirements: 2.4, 3.11_

  - [x] 7.2 Update domain entity
    - File: `src/domain/entities/monitoring.py`
    - `width_m: float` → `width_m: Optional[float] = None`
    - `length_m: float` → `length_m: Optional[float] = None`
    - _Requirements: 2.4_

  - [x] 7.3 Update persistence model
    - File: `src/infrastructure/persistence/models/monitoring_model.py`
    - `nullable=False` → `nullable=True` for width_m and length_m
    - _Requirements: 2.4_

  - [x] 7.4 Add `validate_optional_dimensions()` to validators
    - File: `src/application/validators.py`
    - Both empty → (None, None) valid
    - Both filled positive → validate as before
    - One filled one empty → ValidationError: "Ingresa ambas dimensiones o deja ambos campos vacíos."
    - Negative or invalid → existing error message
    - _Requirements: 2.4, 3.3_

  - [x] 7.5 Update MonitoringService.start_session signature
    - File: `src/application/services/monitoring_service.py`
    - Change: `width_m: float` → `width_m: Optional[float]`
    - Change: `length_m: float` → `length_m: Optional[float]`
    - Pass Optional values to Monitoring entity constructor
    - Do NOT update module dimensions when monitoring dimensions are None
    - _Requirements: 2.4_

  - [x] 7.6 Update monitoring start route — FastAPI Form signature
    - File: `app/routes/agricultural_ui.py`
    - Change Form declarations from `Form(...)` (required) to `Form("")` (optional with empty default)
    - Example: `width_m: str = Form("")` and `length_m: str = Form("")`
    - This ensures POST without fields or with empty values does NOT trigger FastAPI 422
    - Replace `validate_dimensions()` with `validate_optional_dimensions()`
    - Pass (None, None) or (width, length) to MonitoringService
    - Preserve existing behavior: if module already has dimensions, prefill form
    - _Requirements: 2.4_

  - [x] 7.7 Update monitoring setup template
    - File: `app/templates/agricultural/monitoring_setup.html`
    - Remove `required` attribute from width/length inputs
    - Add hint: "Opcional — ingresa ambas dimensiones o deja ambos campos vacíos"
    - Display "No configuradas" when monitoring/module has no dimensions
    - _Requirements: 2.4_

  - [x] 7.8 Review and update all dependent code
    - Check and fix: mappers/converters between entity↔model
    - Check and fix: test fixtures/factories that construct Monitoring
    - Check and fix: context_builders or serializers that assume float for dimensions
    - Check and fix: monitoring_report.html or any template displaying dimensions
    - Ensure type hints reflect `Optional[float]` throughout
    - _Requirements: 2.4_

  - [x] 7.9 Add comprehensive dimension tests
    - SQLite migration test:
      - Create DB with NOT NULL schema (old schema)
      - Insert monitoring + associated snapshot (FK) + associated monitoring_metrics (FK)
      - Run `init_db()` (triggers migration)
      - Verify monitoring data preserved
      - Verify snapshot.monitoring_id still points correctly
      - Verify monitoring_metrics.monitoring_id still points correctly
      - PRAGMA foreign_key_check → must return empty
      - PRAGMA foreign_keys == 1 after migration
      - Create new Monitoring with width_m=None, length_m=None → success
      - Run `init_db()` AGAIN → idempotent, no error
    - Validator: both empty → valid, both filled → valid, one only → error, negative → error
    - Property-based: random (width_str, length_str) pairs follow rules
    - Integration: POST monitoring start with empty dimensions → success (303 redirect, NOT 422)
    - Integration: POST with valid dimensions → success
    - Integration: POST with only width → validation error (NOT FastAPI 422)
    - Integration: POST without width_m/length_m fields at all → success (treated as empty)
    - _Requirements: 2.4, 3.3, 3.11_

- [x] 8. SVG icon system — global sweep (Fix 2)

  - [x] 8.1 Create icon macro partial
    - NEW file: `app/templates/agricultural/partials/icons.html`
    - Jinja2 macro: `{% macro icon(name, size=24) %}...{% endmacro %}`
    - Define inline SVGs for: home, plant, package, sync, camera, water, lab, tomato, scissors, leaf, vine, broom, eye, search, note, clipboard, warning, thermometer, edit, delete, error
    - All: viewBox, currentColor, aria-hidden="true"
    - _Requirements: 2.2_

  - [x] 8.2 Replace emojis in bottom navigation
    - File: `app/templates/base_agricultural.html`
    - Import icon macro: `{% from "agricultural/partials/icons.html" import icon %}`
    - Replace 🏠🌱📦🔄 with `{{ icon('home') }}` etc.
    - _Requirements: 2.2, 3.2_

  - [x] 8.3 Replace emojis in ALL other templates (global sweep)
    - `dashboard.html` — ⚠️ in alerts header → `{{ icon('warning') }}`
    - `module_detail.html` — ✎🗑 actions → `{{ icon('edit') }}` `{{ icon('delete') }}`
    - `greenhouse_detail.html` — ✎🗑 → same
    - `greenhouse_list.html` — ⚠️ → `{{ icon('warning') }}`
    - `monitoring_execution.html` — 🌡️⚠️ → `{{ icon('thermometer') }}` `{{ icon('warning') }}`
    - `export_detail.html` — ⚠️ → `{{ icon('warning') }}`
    - `error.html` — ⚠️ → `{{ icon('error') }}`
    - `login.html` — 🍅 branding → `{{ icon('tomato') }}` or plain text "Tomato Monitor"
    - _Requirements: 2.2_

  - [x] 8.4 Replace emojis in history_service.py
    - File: `src/application/services/history_service.py`
    - Change `_ACTIVITY_ICONS` values to semantic icon_key strings:
      - riego → "watering", fertilizacion → "fertilization", fitosanitario → "phytosanitary"
      - cosecha → "harvest", poda → "pruning", deshoje → "defoliation"
      - tutorado → "trellising", limpieza → "cleaning", inspeccion_visual → "inspection"
      - monitoreo_plagas → "pest_monitoring", monitoreo_enfermedades → "pest_monitoring"
      - observacion_general → "observation"
    - Monitoring icon: "monitoring"
    - Fallback: "note"
    - Rename field from "icon" to "icon_key" in returned dicts
    - _Requirements: 2.2_

  - [x] 8.5 Update module_detail template for icon_key rendering
    - File: `app/templates/agricultural/module_detail.html`
    - Use icon macro with `item.icon_key` instead of rendering `{{ item.icon }}` as text
    - _Requirements: 2.2_

  - [x] 8.6 Add emoji sweep test
    - Test: scan all `.html` template files for known emoji Unicode ranges
    - Test: `history_service._ACTIVITY_ICONS` values contain no emoji chars
    - Test: bottom nav HTML contains `<svg` elements, NOT emoji characters
    - This prevents accidental reintroduction of emojis
    - _Requirements: 2.2_

- [x] 9. Export fixes — image path robustness and orphan reconciliation (Fixes 10, 11)

  - [x] 9.1 Improve export image path resolution (Fix 10) — robustness, NOT bug fix claim
    - File: `src/application/services/export_service.py`
    - Prefer `snapshot.image_path` as authoritative source for raw images
    - Use `src.infrastructure.security.path_sanitizer.validate_safe_path()` — NO str.startswith()
    - Normalize: strip "outputs/" prefix from image_path, then validate_safe_path(relative, OUTPUTS_DIR.resolve())
    - Derive annotated path from raw filename, validated within annotated_snapshots dir
    - Keep existing fallback behavior for snapshots without image_path
    - File: `src/infrastructure/security/path_sanitizer.py`
    - Strengthen containment check: replace `str(candidate).startswith(str(resolved_base))` with `candidate.relative_to(resolved_base)` (Path semantics, not string prefix)
    - Add test: sibling prefix attack (base=`outputs`, malicious=`outputs_evil/file.jpg`) → REJECTED
    - Add test: path traversal `../../../etc/passwd` → rejected
    - Add test: null bytes → rejected
    - Add test: valid relative path → accepted
    - _Requirements: 2.10_

  - [x] 9.2 Add orphan export reconciliation at startup (Fix 11)
    - File: `app/main.py` — inside `lifespan()` after `init_db()`
    - Use existing `list_pending()` to find "generating" records
    - Transition to "error" via `update(id, {...})` with descriptive message
    - Do NOT delete the records — preserve traceability
    - Set `completed_at` to current time
    - _Requirements: 2.11_

  - [x] 9.3 Add diagnostic tests for export images
    - Test A: export before any snapshots exist → images_count=0 (CORRECT behavior)
    - Test B: snapshots with files on disk → included, images_count > 0
    - Test C: only raw exists → included
    - Test D: only annotated exists → included
    - Test E: both raw + annotated → both included, counts coherent
    - Test F: metadata in DB but file missing → files_missing + warning in manifest
    - Do NOT assert "this was a bug" — document observed behavior
    - _Requirements: 2.10_

  - [x] 9.4 Add orphan reconciliation tests
    - Test: 0 orphans → nothing changes
    - Test: 1 orphan generating → transitioned to "error" with message
    - Test: multiple orphans → all transitioned
    - Test: completed exports NOT affected
    - Test: after startup reconciliation, no "generating" records exist
    - _Requirements: 2.11_

- [x] 10. Fix implementation verification

  - [x] 10.1 Re-run bug condition exploration tests (from task 1)
    - Run `tests/properties/test_016_bug_conditions.py`
    - **EXPECTED OUTCOME**: All tests now PASS (confirms bugs are fixed)
    - If any fail: investigate and fix before proceeding
    - _Requirements: 2.1, 2.2, 2.4, 2.5, 2.6, 2.7, 2.10, 2.11, 2.14_

  - [x] 10.2 Re-run preservation tests (from task 2)
    - Run `tests/properties/test_016_preservation.py`
    - **EXPECTED OUTCOME**: All tests still PASS (confirms no regressions)
    - _Requirements: 3.1, 3.3, 3.4, 3.5, 3.7, 3.11_

- [x] 11. Checkpoint — Full validation and Spec 015 update

  - [x] 11.1 Run full test suite
    - Execute `python -m pytest -q`
    - ALL existing tests + new tests must pass
    - _Requirements: 3.6_

  - [x] 11.2 Run architecture boundary tests
    - Execute `python -m pytest tests/unit/test_architecture_boundaries.py -v`
    - Confirm no layer violations from new `src/application/utils/` module
    - _Requirements: 3.1_

  - [x] 11.3 Run capture-first pipeline tests
    - Execute `python -m pytest tests/ -k "capture_worker or snapshot_analysis or monitoring_service" -v`
    - Confirm pipeline integrity preserved
    - _Requirements: 3.1, 3.5_

  - [x] 11.4 Update Spec 015 tasks.md with Wave 12 stabilization section
    - File: `.kiro/specs/015-portable-monitoring-and-crop-traceability/tasks.md`
    - Append new section: "12. Post-Raspberry hardware validation stabilization (Spec 016)"
    - List all fixes applied with brief description
    - Reference Spec 016 for detailed requirements/design
    - Note: born from physical validation on RPi 5 + AI Camera + DSI 7" + portrait
    - Mark subtasks as completed only when verified
    - _Requirements: 2.1-2.14_

  - [x] 11.5 Report final test results
    - Total tests, passed, failed, skipped
    - Do NOT declare complete if any related test is red
    - _Requirements: 3.6_

## Notes

- Tasks 1 and 2 (exploration and preservation tests) MUST run before implementation begins.
- Task 3 (httpx) must be first implementation task — it unblocks test execution.
- Task 4 (timezone) must precede tasks that use `to_bogota()`/`bogota_to_utc()`.
- Tasks 5-9 are independent of each other and can be done in any order after task 4.
- Fix 2 (SVG icons) is the largest single change — global sweep across ALL templates + Python service.
- Fix 4 (optional dimensions) is the most complex — touches all layers + requires SQLite migration.
- The SQLite migration uses table recreation (the only way to change column constraints in SQLite).
- No new tables — only modifications to existing code, schema, and values.
- Capture-first pipeline, FSM, and camera ownership MUST remain untouched.
- Do NOT claim Issue 10 is a confirmed bug — treat as robustness improvement pending diagnostic evidence.
- `overflow-x: hidden` must NOT be used globally to mask layout problems.
- Colombia timezone (America/Bogota) has no DST — offset is always UTC-5.
