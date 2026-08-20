# Requirements Document

## Introduction

El sistema Tomato Monitor evoluciona de un enfoque robótico/autónomo a una **plataforma portátil de monitoreo visual y trazabilidad agrícola** para tomate cherry en invernadero.

El operario usa una Raspberry Pi 5 con cámara y pantalla táctil DSI 7" como dispositivo portátil. Recorre manualmente el cultivo, inicia sesión local, revisa indicadores en el dashboard, identifica módulos pendientes de monitoreo, registra labores agrícolas, realiza monitoreo visual manual (capture-first), revisa reportes y exporta/sincroniza datos e imágenes.

### Cambio de alcance

- **Eliminado:** Robot autónomo, chasis, motores, BTS7960, GPIO de movimiento, navegación automática, RobotOrchestrator.
- **Incorporado:** Login local offline, dashboard contextual, frecuencia de monitoreo por módulo, alertas operativas, bitácora de actividades agrícolas, historial combinado, exportación ZIP, sincronización manual provider-agnostic, UI responsive vertical.

### Decisiones cerradas

1. No habrá jornada ni turno laboral.
2. Habrá login local offline para usuarios ya registrados.
3. El registro de usuarios queda orientado a operación online o sincronización futura.
4. La trazabilidad se logrará con usuario + fecha/hora + módulo + actividad/monitoreo.
5. La frecuencia de monitoreo será por módulo.
6. Las actividades agrícolas se seleccionan desde un catálogo definido desde backend.
7. El catálogo de actividades no será editable desde UI en esta versión.
8. No se define aún proveedor remoto.
9. La sincronización será manual.
10. La exportación ZIP de datos e imágenes será el primer mecanismo obligatorio.
11. Drive/base remota quedan como integración futura o adaptador configurable.
12. El dashboard será general, con contexto de invernadero/cultivo y acceso a detalle por módulo.
13. Las alertas serán operativas y trazables, no recomendaciones agronómicas avanzadas.
14. La interfaz debe ser responsive, priorizando Raspberry vertical.
15. El monitoreo visual seguirá usando capture-first: snapshots primero → finalizar captura → liberar cámara → análisis diferido → reporte.

### Componentes estables (no romper)

Los siguientes componentes deben mantenerse estables durante toda la implementación:

- `src/application/services/capture_worker.py`
- `src/application/services/snapshot_analysis_service.py`
- `src/application/services/monitoring_service.py`
- `src/application/services/monitoring_runtime_registry.py`
- `src/domain/value_objects/monitoring_status.py`
- `app/static/js/monitoring.js`
- `app/templates/agricultural/monitoring_execution.html`
- `app/templates/agricultural/monitoring_report.html`
- `src/infrastructure/vision/*`
- `src/infrastructure/camera/*`
- `src/infrastructure/monitoring/thermal_monitor.py`

## Glossary

| Term | Definition |
|---|---|
| Operario | Usuario autenticado que transporta el dispositivo y ejecuta actividades en invernadero |
| Monitoreo visual portátil | Sesión capture-first donde el operario recorre manualmente un módulo con la Raspberry Pi |
| Bitácora agrícola | Registro de actividades/labores agrícolas por módulo con trazabilidad de usuario y fecha |
| Catálogo de actividades | Lista predefinida de tipos de actividad agrícola, gestionada desde backend |
| Frecuencia de monitoreo | Intervalo en días entre monitoreos sucesivos de un módulo |
| Alerta operativa | Notificación sobre estado pendiente/vencido de una tarea operativa (monitoreo, exportación) |
| Exportación ZIP | Empaquetado local de datos estructurados e imágenes para respaldo o transferencia |
| Sincronización manual | Proceso iniciado por el operario para subir datos/imágenes a un destino remoto |
| Provider-agnostic | Diseño de adaptadores sin acoplar a proveedor específico (Drive, S3, Supabase) |
| Dashboard contextual | Pantalla resumen con indicadores basados en datos reales del sistema |

## Requirements

### Requirement 1: Portable monitoring scope

The system scope SHALL describe a portable embedded monitoring device operated manually by a human, not an autonomous robot.

#### Acceptance Criteria

- WHEN the system scope is described THEN it SHALL describe a portable embedded monitoring device, not an autonomous robot.
- WHEN the operator performs monitoring THEN the operator SHALL physically move the device through the greenhouse/module.
- WHEN documentation or UI text refers to monitoring THEN it SHALL use manual/portable traversal language.
- WHEN implementation tasks are planned THEN they SHALL NOT introduce robot chassis, motors, BTS7960, GPIO movement, or autonomous navigation.

### Requirement 2: Preserve capture-first monitoring pipeline

The existing capture-first monitoring pipeline SHALL remain functional and unmodified in its core logic.

#### Acceptance Criteria

- WHEN a monitoring session starts THEN the system SHALL capture snapshots before deferred inference.
- WHEN capture is finalized THEN the system SHALL release the camera before analysis.
- WHEN analysis starts THEN the system SHALL process stored snapshots.
- WHEN analysis is running THEN the system SHALL expose progress (X/Y snapshots).
- WHEN analysis completes THEN the system SHALL generate a report from processed snapshots.
- WHEN the portable scope is implemented THEN existing capture-first tests SHALL continue passing.

### Requirement 3: Local offline login for registered users

Registered operators SHALL be able to log in locally without internet access.

#### Acceptance Criteria

- WHEN a registered operator has a local account THEN they SHALL be able to log in without internet.
- WHEN credentials are stored locally THEN passwords SHALL NOT be stored in plain text.
- WHEN a user performs a traceable action (monitoring, activity, export) THEN the system SHALL record the user_id and timestamp.
- WHEN the system is offline THEN login SHALL continue working for previously registered active users.
- WHEN an unauthenticated user accesses protected screens THEN they SHALL be redirected to login.

### Requirement 4: Online-oriented registration and future user sync

User registration SHALL be designed for online-capable operation with future remote synchronization.

#### Acceptance Criteria

- WHEN a new user is registered THEN the system SHOULD treat registration as an online-capable operation.
- WHEN the remote provider is not configured THEN the system SHALL NOT hardcode Supabase, Drive, S3, or any specific provider.
- WHEN user registration is implemented initially THEN it MAY be prototyped locally with sync_status for future remote backup.
- WHEN remote sync is added later THEN it SHALL use provider-agnostic adapters.

### Requirement 5: Module-level monitoring frequency

Each module SHALL support a configurable monitoring frequency in days.

#### Acceptance Criteria

- WHEN a module is configured THEN it SHALL support a monitoring_frequency_days field.
- WHEN a module has a last monitoring date THEN the system SHALL calculate next expected monitoring date.
- WHEN a module exceeds its monitoring frequency THEN the dashboard/alerts SHALL show it as pending or overdue.
- WHEN no frequency is explicitly defined THEN the system SHALL use a configurable default value, initially 7 days for the tomato cherry prototype.

### Requirement 6: Operational alerts and pending tasks

The system SHALL display operational alerts based on real system state data.

#### Acceptance Criteria

- WHEN modules are pending monitoring THEN the system SHALL display operational alerts.
- WHEN data or images are pending export/sync THEN the system SHALL display operational alerts.
- WHEN temperature or analysis pause events occur THEN the system SHOULD surface those as operational alerts.
- WHEN alerts are shown THEN they SHALL NOT claim unsupported agronomic diagnoses (irrigation deficit, pest detection, yield projection) unless backed by implemented model evidence.
- WHEN an alert condition is no longer true THEN computed alerts SHALL no longer appear.
- WHEN persisted alerts are introduced in a future iteration THEN their status SHALL support pending/resolved/error states.

### Requirement 7: Agricultural activity log

Operators SHALL be able to register agricultural activities per module from a backend-defined catalog.

#### Acceptance Criteria

- WHEN an operator registers a crop activity THEN the system SHALL store: module_id, activity_type_id, user_id, timestamp, notes, and optional product/quantity/unit.
- WHEN activity type is selected THEN it SHALL come from a backend-defined catalog.
- WHEN the UI is used THEN operators SHALL NOT create new activity types in this version.
- WHEN activities are shown in history THEN they SHALL be distinguishable from monitoring events.

Initial activity type catalog:

| Code | Name | Category |
|---|---|---|
| riego | Riego | mantenimiento |
| fertilizacion | Fertilización | nutrición |
| fitosanitario | Aplicación fitosanitaria | protección |
| monitoreo_plagas | Monitoreo de plagas | vigilancia |
| monitoreo_enfermedades | Monitoreo de enfermedades | vigilancia |
| poda | Poda | manejo_planta |
| deshoje | Deshoje | manejo_planta |
| tutorado | Tutorado / amarre | manejo_planta |
| limpieza | Limpieza del módulo | mantenimiento |
| cosecha | Cosecha | producción |
| inspeccion_visual | Inspección visual | vigilancia |
| observacion_general | Observación general | general |

### Requirement 8: Contextual dashboard

The dashboard SHALL show contextual information based exclusively on real system data.

#### Acceptance Criteria

- WHEN the dashboard is displayed THEN it SHALL show greenhouse and crop context.
- WHEN metrics are displayed THEN each metric SHALL be based on existing persisted system data.
- WHEN module monitoring frequency exists THEN the dashboard SHALL show pending/overdue modules.
- WHEN export/sync statuses exist THEN the dashboard SHALL show pending data/images.
- WHEN visual health/maturity metrics are unavailable for a module THEN the dashboard SHALL NOT invent unsupported agronomic indicators.

Dashboard MVP indicators (all from real data):

- Active greenhouses and modules count
- Modules pending monitoring (overdue by frequency)
- Last monitoring date (system-wide or per greenhouse)
- Monitorings completed this week
- Total snapshots captured in last monitoring
- Tomatoes detected in latest completed monitoring
- Recent agricultural activities (last 5–10)
- Pending exports/sync count
- Device thermal status if available from last monitoring

### Requirement 9: Combined history and reports

Module detail SHALL show a combined timeline of monitoring and agricultural activity history.

#### Acceptance Criteria

- WHEN a module detail page is opened THEN it SHOULD show both monitoring history and agricultural activity history.
- WHEN history is displayed THEN events SHALL be ordered by timestamp (most recent first).
- WHEN a monitoring report exists THEN it SHALL remain accessible from the timeline.
- WHEN activity records exist THEN they SHALL be visible in the module history.
- WHEN the report uses snapshots THEN annotated snapshots SHALL be preferred; raw snapshots SHALL be fallback.

### Requirement 10: ZIP export for data and images

The system SHALL support generating a ZIP export package with structured data and images.

#### Acceptance Criteria

- WHEN the operator requests export THEN the system SHALL generate a ZIP package.
- WHEN a ZIP package is generated THEN it SHALL include structured metadata and available images.
- WHEN annotated snapshots are available THEN they SHALL be included.
- WHEN raw snapshots are available THEN they SHALL be included as fallback.
- WHEN export completes THEN the system SHALL record export status (completed, file_path, timestamp).
- WHEN export fails THEN the system SHALL preserve error details and allow retry.

Suggested ZIP contents:

```
export_{timestamp}/
├── metadata.json          (device, export date, user, scope)
├── greenhouses.json       (greenhouse data)
├── modules.json           (module data with frequency)
├── monitorings.json       (monitoring records with metrics)
├── activities.json        (activity log records)
├── sync_manifest.json     (checksums, record counts)
├── reports/
│   └── monitoring_{id}_report.json
├── metrics/
│   └── monitoring_{id}_pipeline_metrics.json
├── snapshots/
│   ├── raw/
│   └── annotated/
```

### Requirement 11: Manual provider-agnostic synchronization

Synchronization SHALL be triggered manually and designed without coupling to a specific remote provider.

#### Acceptance Criteria

- WHEN synchronization is requested THEN it SHALL be triggered manually by the operator.
- WHEN remote provider is not configured THEN the system SHALL still support local ZIP export as the primary mechanism.
- WHEN provider adapters are introduced THEN they SHALL follow a provider-agnostic interface.
- WHEN data/images are pending sync THEN the system SHALL show pending/error/synced status per record.
- WHEN sync fails THEN the system SHALL allow retry without corrupting local data.

### Requirement 12: Raspberry vertical responsive UI

The UI SHALL be usable on the Raspberry Pi touchscreen in vertical (portrait) orientation.

#### Acceptance Criteria

- WHEN the app runs on Raspberry Pi touchscreen in vertical orientation (480×800) THEN layouts SHALL remain usable.
- WHEN forms are displayed THEN they SHALL avoid excessive horizontal width and support virtual keyboard usage.
- WHEN navigation is displayed on narrow portrait screens THEN it SHOULD use compact navigation (bottom nav or hamburger menu).
- WHEN buttons are used on touchscreen THEN they SHALL maintain 44×44 px minimum touch targets.
- WHEN desktop or landscape mode is used THEN the existing wider layout MAY remain available.

### Requirement 13: Security and data protection baseline

Authentication and data handling SHALL follow basic security practices.

#### Acceptance Criteria

- WHEN passwords are stored THEN they SHALL be hashed with a secure algorithm (bcrypt, argon2, or equivalent).
- WHEN sessions are used THEN unauthenticated users SHALL NOT access protected operational screens.
- WHEN data is exported THEN the package SHOULD avoid exposing unnecessary sensitive data (password hashes excluded from export).
- WHEN remote provider is not selected THEN secrets SHALL NOT be hardcoded in source files.
- WHEN sync/export configuration exists THEN credentials SHALL be stored outside source code (.env or secure storage).

### Requirement 14: Thesis alignment

Project documentation SHALL describe the system as a portable embedded crop monitoring and traceability platform.

#### Acceptance Criteria

- WHEN project documentation is updated THEN it SHALL describe the system as a portable embedded crop monitoring and traceability platform.
- WHEN limitations are documented THEN lack of autonomous locomotion SHALL be described as a deliberate scope decision, not a failed implementation.
- WHEN objectives are rewritten THEN they SHALL align with: portable monitoring, embedded vision, agricultural traceability, and export/synchronization.
- WHEN evidence is produced THEN it SHALL support the portable product scope (capture-first benchmarks, export validation, traceability records).
