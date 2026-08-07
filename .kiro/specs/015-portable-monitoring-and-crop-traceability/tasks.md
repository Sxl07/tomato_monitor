# Implementation Plan: Portable Monitoring and Crop Traceability

## Overview

Esta spec transforma Tomato Monitor de un sistema con orientación robótica a una plataforma portátil de monitoreo visual y trazabilidad agrícola para tomate cherry en invernadero.

El operario usa una Raspberry Pi 5 con pantalla táctil como dispositivo portátil. Recorre manualmente el cultivo, inicia sesión, revisa indicadores, registra actividades agrícolas, ejecuta monitoreos visuales capture-first y exporta/sincroniza datos e imágenes.

**No se toca:** capture-first pipeline, deferred analysis, thermal monitoring, monitoring state machine, vision infrastructure, camera infrastructure.

**No se introduce:** robot chassis, motors, BTS7960, GPIO movement, autonomous navigation.

## Tasks

- [x] 1. Audit and align scope language
  - [x] 1.1 Search active specs, steering, templates and docs for robot/autonomous/chassis/motor references
  - [x] 1.2 Classify references as historical (acceptable), active (requires update), or removable
  - [x] 1.3 Update steering/product.md to describe portable monitoring platform
  - [x] 1.4 Update steering/architecture.md to remove RobotOrchestrator and motor adapter sections
  - [x] 1.5 Update steering/thesis-context.md to reflect portable scope and current phase
  - [x] 1.6 Update steering/ux-design.md to add portrait mode and remove robot traversal language
  - [x] 1.7 Update steering/data-model.md to add new entities (User, ActivityType, ActivityLog, ExportPackage)
  - [x] 1.8 Audit legacy robot/movement-related domain interfaces and produce a removal plan only if they are confirmed unused by tests and current pipeline
  - [x] 1.9 Run architecture boundary tests to confirm nothing breaks
- [x] 2. Define data model foundation
  - [x] 2.1 Add User domain entity (src/domain/entities/user.py)
  - [x] 2.2 Add ActivityType domain entity (src/domain/entities/activity_type.py)
  - [x] 2.3 Add ActivityLog domain entity (src/domain/entities/activity_log.py)
  - [x] 2.4 Add ExportPackage domain entity (src/domain/entities/export_package.py)
  - [x] 2.5 Add monitoring_frequency_days field to Module entity
  - [x] 2.6 Add created_by_user_id and sync_status fields to Monitoring entity
  - [x] 2.7 Add SyncStatus value object or enum (src/domain/value_objects/sync_status.py)
  - [x] 2.8 Add UserModel persistence model (src/infrastructure/persistence/models/user_model.py)
  - [x] 2.9 Add ActivityTypeModel persistence model
  - [x] 2.10 Add ActivityLogModel persistence model
  - [x] 2.11 Add ExportPackageModel persistence model
  - [x] 2.12 Extend ModuleModel with monitoring_frequency_days column
  - [x] 2.13 Extend MonitoringModel with created_by_user_id and sync_status columns
  - [x] 2.14 Add repository interfaces (user, activity_type, activity_log, export_package)
  - [x] 2.15 Add SQL repository implementations
  - [x] 2.16 Seed initial activity type catalog in database initialization
  - [x] 2.17 Add unit tests for new entities and repositories
  - [x] 2.18 Run full test suite to confirm no regressions
- [x] 3. Implement local offline authentication foundation
  - [x] 3.1 Add password hashing service (src/application/services/auth_service.py)
  - [x] 3.2 Add login/logout routes (app/routes/auth.py)
  - [x] 3.3 Add login template (app/templates/auth/login.html)
  - [x] 3.4 Add secure local session/cookie handling after dependency compatibility review
  - [x] 3.5 Add current_user dependency for route injection
  - [x] 3.6 Protect operational routes (dashboard, monitoring, activities, export) with auth dependency
  - [x] 3.7 Add test fixtures for authenticated requests (conftest.py override)
  - [x] 3.8 Validate offline login with existing local users
  - [x] 3.9 Add seed script or init_db hook for initial admin user
  - [x] 3.10 Run full test suite to confirm existing tests still pass
- [x] 4. Add module monitoring frequency and operational alerts
  - [x] 4.1 Add monitoring_frequency_days to module create/edit forms
  - [x] 4.2 Implement next_monitoring_due calculation based on frequency and last monitoring date
  - [x] 4.3 Add AlertService (src/application/services/alert_service.py) with compute_alerts()
  - [x] 4.4 Implement alert types: monitoring_overdue, monitoring_pending, export_pending, analysis_error
  - [x] 4.5 Add OperationalAlert dataclass (src/domain/entities/operational_alert.py)
  - [x] 4.6 Add alerts section to dashboard and/or standalone alerts page
  - [x] 4.7 Add unit tests for frequency calculation and alert computation
  - [x] 4.8 Run full test suite
- [ ] 5. Implement agricultural activity log
  - [ ] 5.1 Add activity creation route (POST /modulos/{id}/actividades/registrar)
  - [ ] 5.2 Add activity form template (app/templates/agricultural/activity_form.html)
  - [ ] 5.3 Add activity list route and template by module
  - [ ] 5.4 Implement form logic: dynamic fields based on activity type (requires_product, allows_quantity)
  - [ ] 5.5 Record user_id and occurred_at on every activity
  - [ ] 5.6 Add validation rules (required fields, positive quantity, valid type)
  - [ ] 5.7 Add activity_service.py for creation and listing logic
  - [ ] 5.8 Add unit tests for activity creation, validation, and listing
  - [ ] 5.9 Run full test suite
- [ ] 6. Build contextual dashboard
  - [ ] 6.1 Add dashboard route (GET /dashboard)
  - [ ] 6.2 Add dashboard context builder (app/context_builders.py or new service)
  - [ ] 6.3 Add dashboard template (app/templates/agricultural/dashboard.html)
  - [ ] 6.4 Implement indicators: greenhouse/module counts, pending/overdue modules
  - [ ] 6.5 Implement indicators: last monitoring, monitorings this week, snapshots, detections
  - [ ] 6.6 Implement indicators: recent activities, pending exports
  - [ ] 6.7 Redirect home (/) to dashboard when authenticated
  - [ ] 6.8 Ensure no unsupported agronomic metrics are shown
  - [ ] 6.9 Add unit tests for dashboard context building
  - [ ] 6.10 Run full test suite
- [ ] 7. Add combined history and report access
  - [ ] 7.1 Build combined timeline query (monitorings + activities by module, ordered by date)
  - [ ] 7.2 Add history section to module_detail.html template
  - [ ] 7.3 Add timeline item rendering (icons, colors, links to reports)
  - [ ] 7.4 Ensure monitoring reports remain accessible via existing routes
  - [ ] 7.5 Add unit tests for timeline ordering and content
  - [ ] 7.6 Run full test suite
- [ ] 8. Implement ZIP export package
  - [ ] 8.1 Add ExportService (src/application/services/export_service.py)
  - [ ] 8.2 Implement metadata and manifest JSON generation
  - [ ] 8.3 Implement incremental snapshot copying (raw + annotated) without full RAM load
  - [ ] 8.4 Implement monitoring reports and pipeline metrics inclusion
  - [ ] 8.5 Add export route (POST /exportar, GET /exportar/{id}/descargar)
  - [ ] 8.6 Add export UI (trigger button, progress/status, download link)
  - [ ] 8.7 Track ExportPackage status in database (pending → generating → completed/error)
  - [ ] 8.8 Add unit tests with temporary files and small fixture data
  - [ ] 8.9 Run full test suite
- [ ] 9. Prepare provider-agnostic manual sync foundation
  - [ ] 9.1 Define RemoteSyncAdapter abstract interface
  - [ ] 9.2 Add SyncService (src/application/services/sync_service.py) with manual trigger
  - [ ] 9.3 Add sync_status tracking on Monitoring and ActivityLog records
  - [ ] 9.4 Add sync status UI (pending/exported/synced counts, manual trigger)
  - [ ] 9.5 Implement LocalZipExport as the initial concrete "sync" mechanism
  - [ ] 9.6 Keep remote provider adapters as stubs (not implemented)
  - [ ] 9.7 Add tests for sync status transitions and pending calculations
  - [ ] 9.8 Run full test suite
- [ ] 10. Adapt UI for Raspberry vertical usage
  - [ ] 10.1 Add @media queries for portrait orientation (max-width: 600px or orientation: portrait)
  - [ ] 10.2 Make dashboard cards stack in single column for portrait
  - [ ] 10.3 Add compact bottom navigation for portrait screens
  - [ ] 10.4 Ensure forms use full width and accommodate virtual keyboard
  - [ ] 10.5 Ensure monitoring execution flow remains usable in portrait
  - [ ] 10.6 Ensure touch targets remain ≥44×44px in portrait
  - [ ] 10.7 Test login, dashboard, activity form, and monitoring flow in 480×800 viewport
  - [ ] 10.8 Add CSS regression tests or visual verification notes
  - [ ] 10.9 Run full test suite
- [ ] 11. Final validation and documentation alignment
  - [ ] 11.1 Run targeted monitoring tests (capture_worker, snapshot_analysis, finalize route)
  - [ ] 11.2 Run auth, activity, dashboard, export, and alert tests
  - [ ] 11.3 Run architecture boundary tests
  - [ ] 11.4 Run full test suite (all 826+ tests must pass)
  - [ ] 11.5 Confirm no robot/chassis/motor/autonomous navigation implementation is active
  - [ ] 11.6 Verify steering files reflect portable scope
  - [ ] 11.7 Update thesis-context.md with new phase description
  - [ ] 11.8 Document scope adjustment rationale for thesis

## Definition of Done

- [ ] Active scope describes a portable embedded crop monitoring and traceability platform.
- [ ] Capture-first monitoring pipeline remains fully functional.
- [ ] Local offline login works for registered users.
- [ ] Module monitoring frequency supports operational alerts.
- [ ] Agricultural activity log exists with backend-defined catalog.
- [ ] Dashboard shows contextual, supported metrics (no invented indicators).
- [ ] Combined history links monitorings and activities per module.
- [ ] ZIP export exists for structured data and images.
- [ ] Manual sync foundation is provider-agnostic (local ZIP as first mechanism).
- [ ] UI is usable on Raspberry Pi vertical orientation (480×800).
- [ ] No active robot/chassis/motor/autonomous navigation implementation exists.
- [ ] All targeted and full tests pass.

## Explicitly Out of Scope

- Robot chassis, autonomous movement, motor control, BTS7960, GPIO movement scripts.
- RobotOrchestrator, autonomous navigation.
- Advanced agronomic recommendations (irrigation deficit, pest forecasting, yield projection).
- Automatic irrigation or fertilization decisions.
- Mandatory work-shift/jornada management.
- Provider-specific cloud integration as mandatory first delivery (Drive OAuth, S3, Supabase).
- Editing activity type catalog from UI.
- Real-time multi-user collaboration.
- Push notifications to external devices.

## Notes

- This spec is intentionally broad. Implementation MUST proceed task by task, wave by wave.
- Do NOT implement all tasks in one iteration.
- Protect existing capture-first monitoring pipeline at all costs.
- Favor local-first behavior: the Raspberry Pi may operate without internet.
- Avoid unsupported dashboard indicators — only show data backed by real system state.
- Auth implementation must not break existing tests; use injectable dependencies, not global middleware.
- New SQLAlchemy columns added to existing tables MUST be nullable or have defaults for migration safety.
