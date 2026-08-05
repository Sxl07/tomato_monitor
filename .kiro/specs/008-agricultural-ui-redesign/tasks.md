# Implementation Plan: Agricultural UI Redesign

## Overview

Progressive construction of the farmer-facing web interface for the Tomato Monitor system. The implementation builds the UI layer from base layout/CSS first, then screens from simplest to most complex, followed by JavaScript interactivity, and finally automated tests. All code integrates with existing SQLAlchemy models and repositories from Spec 006 and the MonitoringService from Spec 007.

## Tasks

- [x] 1. Set up base layout, CSS, and static assets
  - [x] 1.1 Create the agricultural CSS file with design tokens and base styles
    - Create `app/static/css/agricultural.css` with all CSS custom properties (colors, typography, touch targets, layout)
    - Implement base component styles: `.app-header`, `.main-content`, `.back-button`, cards, buttons, forms, lists
    - Implement touch target sizing rules (min 44×44px, primary 60×48px, list items 56px height)
    - Implement scrollable regions with momentum scrolling
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 8.1, 8.2, 8.3, 8.4, 8.5, 9.1, 9.2, 9.3, 9.4, 10.1, 10.2, 10.3, 10.4, 10.5_

  - [x] 1.2 Create the base agricultural template
    - Create `app/templates/base_agricultural.html` with viewport meta (width=800, no user-scalable), CSS link, header block with back button support, main content block, and scripts block
    - Set `lang="es"` on the HTML element
    - _Requirements: 9.1, 11.1, 12.2, 14.1, 14.2_

  - [x] 1.3 Create partial templates for reusable components
    - Create `app/templates/agricultural/partials/confirm_dialog.html` — reusable confirmation dialog with customizable message, confirm (red) and cancel (neutral) buttons
    - Create `app/templates/agricultural/partials/empty_state.html` — reusable empty state with message and optional action button
    - Create `app/templates/agricultural/partials/metric_card.html` — large metric display card with label and value
    - Create `app/templates/agricultural/partials/maturity_bar.html` — horizontal color-coded USDA maturity distribution bar (6 stages)
    - _Requirements: 13.4, 6.3_

  - [x] 1.4 Create placeholder SVG for missing snapshot images
    - Create `app/static/images/placeholder-snapshot.svg` with a simple plant/image placeholder icon
    - _Requirements: 6.5_

- [x] 2. Implement route module and context builders
  - [x] 2.1 Create the agricultural UI router with basic structure
    - Create `app/routes/agricultural_ui.py` with FastAPI `APIRouter`
    - Register all endpoint stubs (GET/POST for greenhouses, modules, monitorings)
    - Import dependencies from `app/dependencies.py`
    - _Requirements: 12.1, 14.1_

  - [x] 2.2 Create context builder functions
    - Create `app/context_builders.py` with pure functions: `build_greenhouse_cards()`, `build_module_cards()`, `build_monitoring_history()`, `validate_dimensions()`, `build_report_metrics()`, `build_snapshot_gallery()`
    - Each function takes repository data and returns structured context dataclasses/dicts
    - Include `_get_last_monitoring_date()` helper
    - _Requirements: 1.2, 2.2, 3.4, 4.4, 6.2, 6.3, 6.4_

  - [x] 2.3 Register the agricultural router in the FastAPI app
    - Update `app/main.py` to include the agricultural UI router with prefix `/`
    - Add static file mount for snapshot images: `/snapshots` → `outputs/monitorings`
    - _Requirements: 14.1_

- [x] 3. Implement greenhouse screens (simplest CRUD)
  - [x] 3.1 Implement Greenhouse List Screen
    - Create `app/templates/agricultural/greenhouse_list.html` extending `base_agricultural.html`
    - Implement route handler `GET /invernaderos` using `build_greenhouse_cards()`
    - Implement `GET /` redirect to `/invernaderos`
    - Display cards with name, module count, last monitoring date
    - Display empty state when no greenhouses exist
    - Include "+" button for creation
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 11.1, 11.2, 17.1_

  - [x] 3.2 Implement Greenhouse Form (create and edit)
    - Create `app/templates/agricultural/greenhouse_form.html` with name and location fields, shared for create/edit modes
    - Implement route handlers: `GET /invernaderos/crear`, `POST /invernaderos/crear`, `GET /invernaderos/{id}/editar`, `POST /invernaderos/{id}/editar`
    - Server-side validation: name required, duplicate name check
    - Re-render form with errors on validation failure
    - _Requirements: 1.5, 11.3, 16.1_

  - [x] 3.3 Implement Greenhouse Detail Screen
    - Create `app/templates/agricultural/greenhouse_detail.html` extending `base_agricultural.html`
    - Implement route handler `GET /invernaderos/{id}` using `build_module_cards()`
    - Display greenhouse name header with edit/delete action icons
    - Display module cards with crop type, dimensions, last monitoring date
    - Display empty state when no modules exist
    - Implement `POST /invernaderos/{id}/eliminar` with confirmation dialog include
    - Handle 404 with redirect to `/invernaderos`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 12.2, 13.1, 17.2_

- [x] 4. Checkpoint - Ensure greenhouse CRUD works
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement module screens
  - [x] 5.1 Implement Module Form (create and edit)
    - Create `app/templates/agricultural/module_form.html` with name, crop_type, width_m, length_m fields
    - Implement route handlers: `GET /invernaderos/{gh_id}/modulos/crear`, `POST /invernaderos/{gh_id}/modulos/crear`, `GET /modulos/{id}/editar`, `POST /modulos/{id}/editar`
    - Server-side validation: name required, duplicate name within greenhouse check
    - Implement `POST /modulos/{id}/eliminar` with cascade confirmation
    - _Requirements: 2.5, 13.2_

  - [x] 5.2 Implement Module Detail Screen
    - Create `app/templates/agricultural/module_detail.html` extending `base_agricultural.html`
    - Implement route handler `GET /modulos/{id}` using `build_monitoring_history()`
    - Display module name header with edit/delete action icons
    - Display info panel: crop type, dimensions (or "No configuradas")
    - Display large "Iniciar Nuevo Monitoreo" primary action button
    - Display monitoring history list (date, time, tomato count, % healthy)
    - Display empty state when no monitoring history
    - Handle 404 with redirect
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 11.2, 11.4, 17.3_

- [x] 6. Implement monitoring screens
  - [x] 6.1 Implement Monitoring Setup Screen
    - Create `app/templates/agricultural/monitoring_setup.html` extending `base_agricultural.html`
    - Implement route handler `GET /modulos/{id}/monitoreo/nuevo` pre-filling saved dimensions
    - Implement `POST /modulos/{id}/monitoreo/iniciar` with dimension validation and auto-save to module
    - Display "Nuevo Monitoreo — {Module Name}" header
    - Display numeric inputs for width/length, optional notes text area
    - Display "Continuar" (primary) and "Cancelar" buttons
    - Validate dimensions > 0 server-side, re-render with error on failure
    - On success: start monitoring via MonitoringService, redirect to execution screen
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 16.1, 16.3, 16.4, 17.4_

  - [x] 6.2 Implement Monitoring Execution Screen
    - Create `app/templates/agricultural/monitoring_execution.html` extending `base_agricultural.html`
    - Implement route handler `GET /monitoreos/{id}/ejecucion`
    - Display "Monitoreando — {Module Name}" header
    - Include status-dependent UI blocks: spinner for initializing, live counters for running, amber banner for paused, progress for finishing
    - Include "Detener Monitoreo" button with confirmation dialog
    - Implement `POST /monitoreos/{id}/abortar` handler
    - Include `monitoring.js` script block with monitoring ID passed to template
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10, 13.3, 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7, 17.5_

  - [x] 6.3 Implement Monitoring Report Screen
    - Create `app/templates/agricultural/monitoring_report.html` extending `base_agricultural.html`
    - Implement route handler `GET /monitoreos/{id}/reporte` using `build_report_metrics()` and `build_snapshot_gallery()`
    - Display "{Module Name} — {Date} {Time}" header
    - Display metric cards (total tomatoes, healthy count/%, unhealthy count/%)
    - Display maturity bar with 6 USDA stages color-coded
    - Display snapshot gallery with thumbnails (onerror fallback to placeholder)
    - Display "No se detectaron tomates" message when total is 0
    - Display "Volver al Módulo" navigation button
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 11.2, 11.4, 12.3, 17.6_

- [x] 7. Implement JavaScript interactivity
  - [x] 7.1 Create monitoring.js polling module
    - Create `app/static/js/monitoring.js` with `startMonitoringPolling(monitoringId, options)`, `stopMonitoringPolling()`, `showConfirmDialog(message, onConfirm, onCancel)`
    - Implement `pollStatus()`: fetch `/monitoring/{id}/status` every 2 seconds
    - Implement `updateExecutionUI(data)`: update DOM counters for snapshots and detections
    - Implement `handleStateTransition(newStatus)`: auto-redirect on "completed", show error banner on "error", show amber banner on "paused"
    - Implement error handling: show reconnection banner on network failure
    - _Requirements: 5.3, 5.9, 5.10, 14.2_

- [x] 8. Checkpoint - Ensure all screens render and navigation works
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Implement property-based tests for context builders
  - [ ]* 9.1 Write property test for greenhouse card assembly
    - **Property 1: Greenhouse card assembly preserves data**
    - Test `build_greenhouse_cards()` with generated lists of Greenhouse, Module, Monitoring entities
    - Verify: one card per greenhouse, correct name, module_count matches actual modules, last_monitoring_date is most recent completed monitoring or None
    - **Validates: Requirements 1.2**

  - [ ]* 9.2 Write property test for module card assembly
    - **Property 2: Module card assembly preserves data**
    - Test `build_module_cards()` with generated lists of Module, Monitoring entities
    - Verify: one card per module, correct crop_type, dimensions formatted as "{width} × {length} m" or None, last_monitoring_date correct
    - **Validates: Requirements 2.2**

  - [ ]* 9.3 Write property test for monitoring history assembly
    - **Property 3: Monitoring history assembly preserves data**
    - Test `build_monitoring_history()` with generated lists of Monitoring (completed/aborted), MonitoringMetrics
    - Verify: one entry per terminal-status monitoring, correct total_tomatoes and pct_healthy from metrics
    - **Validates: Requirements 3.4**

  - [ ]* 9.4 Write property test for dimension validation
    - **Property 4: Dimension validation correctness**
    - Test `validate_dimensions(width, length)` with generated float pairs (positive, zero, negative)
    - Verify: success iff both > 0, error otherwise
    - **Validates: Requirements 4.4, 4.5**

  - [ ]* 9.5 Write property test for report metrics assembly
    - **Property 6: Report metrics assembly round-trip**
    - Test `build_report_metrics()` with generated MonitoringMetrics entities
    - Verify: all count and percentage fields preserved with exact numeric equality
    - **Validates: Requirements 6.2, 6.3**

  - [ ]* 9.6 Write property test for snapshot gallery filtering
    - **Property 7: Snapshot gallery filters by detection presence**
    - Test `build_snapshot_gallery()` with generated lists of Snapshot entities (mixed has_detections)
    - Verify: only snapshots with has_detections==True included, image_url matches expected pattern
    - **Validates: Requirements 6.4**

- [x] 10. Implement integration tests for CRUD flows
  - [ ]* 10.1 Write integration tests for greenhouse CRUD
    - Test full create → read → update → delete flow using FastAPI TestClient with in-memory SQLite
    - Verify form validation errors re-render correctly
    - Verify cascade delete removes associated modules and monitorings
    - _Requirements: 1.2, 1.3, 1.5, 2.6, 13.1_

  - [ ]* 10.2 Write integration tests for module CRUD
    - Test full create → read → update → delete flow
    - Verify duplicate name validation within same greenhouse
    - Verify cascade delete removes associated monitorings
    - _Requirements: 2.2, 2.5, 5.1, 13.2_

  - [ ]* 10.3 Write integration tests for monitoring flows
    - Test monitoring setup → start → complete flow (verify redirect to report)
    - Test monitoring setup → start → abort flow (verify partial results saved)
    - Verify dimension auto-save to module record
    - Verify validation errors re-render setup form
    - _Requirements: 4.4, 4.5, 4.7, 5.5, 5.9, 13.3_

- [x] 11. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Integration tests verify end-to-end CRUD flows with database
- The existing `app/routes/ui.py` is preserved for developer/debugging use
- All user-facing text is in Spanish per Requirements 11.1
- Context builder functions are extracted into `app/context_builders.py` for testability

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.4"] },
    { "id": 1, "tasks": ["1.3", "2.1"] },
    { "id": 2, "tasks": ["2.2", "2.3"] },
    { "id": 3, "tasks": ["3.1", "3.2"] },
    { "id": 4, "tasks": ["3.3", "5.1"] },
    { "id": 5, "tasks": ["5.2", "6.1"] },
    { "id": 6, "tasks": ["6.2", "6.3"] },
    { "id": 7, "tasks": ["7.1"] },
    { "id": 8, "tasks": ["9.1", "9.2", "9.3", "9.4", "9.5", "9.6"] },
    { "id": 9, "tasks": ["10.1", "10.2", "10.3"] }
  ]
}
```
