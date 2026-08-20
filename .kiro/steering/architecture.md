# Architecture Steering - Tomato Monitor

## Estilo arquitectónico

Clean Architecture con cuatro capas ordenadas por dependencia (de afuera hacia adentro):

```
Presentación → Aplicación → Dominio
Infraestructura → Dominio
```

La capa de Dominio no depende de ninguna otra. La Infraestructura implementa las interfaces definidas en Dominio.

## Capas y ubicaciones

| Capa | Directorio | Responsabilidad |
|---|---|---|
| Presentación | `app/` | FastAPI, rutas, templates Jinja2, formularios |
| Aplicación | `src/application/` | DTOs, servicios de aplicación, casos de uso |
| Dominio | `src/domain/` | Entidades, value objects, políticas de negocio, interfaces de repositorios |
| Infraestructura | `src/infrastructure/` | Visión, persistencia, configuración |

## Componentes de infraestructura

- `src/infrastructure/vision/` — pipeline de visión por computador: detector, tracker, clasificador, madurez, scene gate, cropper, orchestrator, runner, annotation_renderer
- `src/infrastructure/persistence/local/` — repositorios CSV + sistema de archivos (legacy benchmark mode)
- `src/infrastructure/persistence/models/` — modelos SQLAlchemy ORM
- `src/infrastructure/persistence/repositories/` — implementaciones SQL de repositorios
- `src/infrastructure/persistence/cloud/` — stub preparado, no activo
- `src/infrastructure/config/` — `settings.py` (rutas, device, modelos) y `thresholds.py` (umbrales operacionales)
- `src/infrastructure/camera/` — frame sources: opencv, raspberry (Picamera2), video_file
- `src/infrastructure/monitoring/` — thermal_monitor.py

## Entidades del dominio

### Vision pipeline (existentes)

`FruitDetection`, `HealthAssessment`, `MaturityAssessment`, `InspectionResult`, `InspectionSession`

### Agricultural data model (existentes)

`Greenhouse`, `Module`, `Monitoring`, `Snapshot`, `MonitoringMetrics`

### Traceability and operations (Spec 015 — objetivo)

`User`, `ActivityType`, `ActivityLog`, `ExportPackage`, `OperationalAlert` (calculada)

## Value objects (inmutables)

`BoundingBox`, `FrameReference`, `ModelMetadata`, `MonitoringStatus` (FSM)

Spec 015 objetivo: `SyncStatus`

## Políticas de dominio

- `DeduplicationPolicy` — decide si un track debe reusarse o reprocesarse según área y estado previo
- `InspectionPolicy` — decide si ejecutar salud y/o madurez según tamaño de crop y score

## Interfaces de repositorios (puertos)

Existentes: `SessionRepository`, `InspectionRepository`, `ArtifactRepository`, `GreenhouseRepository`, `ModuleRepository`, `MonitoringRepository`, `SnapshotRepository`, `InspectionResultRepository`, `MonitoringMetricsRepository`

Spec 015 objetivo: `UserRepository`, `ActivityTypeRepository`, `ActivityLogRepository`, `ExportPackageRepository`

## Inyección de dependencias

Manual, en `app/dependencies.py`. Las instancias se crean por llamada. Sin contenedor DI. Las instancias de `PipelineService` se recrean en cada request (incluye carga de modelos — ver riesgo documentado en `docs/thesis-notes/current-state.md`).

## Reglas de arquitectura

- La lógica de negocio (políticas, entidades) no importa FastAPI, OpenCV, PyTorch ni Detectron2.
- Los módulos de visión en `src/infrastructure/vision/` pueden importar torch, cv2, detectron2 y numpy.
- No crear dependencias circulares entre capas.
- No mover lógica de dominio a la capa de presentación.
- No usar `legacy/` como fuente de imports en código activo; es solo referencia.
- Toda nueva capacidad de visión se integra en `src/infrastructure/vision/`, no en `legacy/`.
- Rutas en `app/routes/` no contienen lógica de negocio; delegan a servicios en `src/application/`.

## Reglas para el pipeline de monitoreo portátil

### 1. Single Camera Owner

- Solo un componente puede abrir físicamente la cámara durante una sesión de monitoreo.
- `CaptureWorker` es el owner exclusivo durante la fase de captura.
- Ningún otro servicio debe abrir la cámara si CaptureWorker está activo.
- Esta regla previene conflictos con Picamera2 que solo permite una apertura concurrente.

### 2. Capture-first pipeline (estable, no reescribir)

El flujo de producción actual es:

```
Operario recorre módulo con Raspberry → CaptureWorker captura snapshots (sin inferencia)
  → Operario finaliza captura → cámara se libera
    → SnapshotAnalysisService procesa snapshots diferidos (detection + health + maturity + tracking)
      → Reporte con métricas y snapshots anotados
```

Componentes estables:
- `CaptureWorker` — loop de captura, Scene Gate, snapshot saving
- `SnapshotAnalysisService` — análisis diferido, tracking cross-snapshot, thermal pause
- `MonitoringService` — lifecycle orchestration (start, finalize_capture, run_analysis, complete)
- `MonitoringRuntimeRegistry` — registry de workers/threads, finalization claims
- `MonitoringState` FSM — INITIALIZING → RUNNING → ANALYZING → COMPLETED

### 3. Servicios de aplicación objetivo (Spec 015)

| Servicio | Responsabilidad |
|---|---|
| `AuthService` | Hashing, login, sesión local offline |
| `AlertService` | Cálculo de alertas operativas a partir del estado del sistema |
| `ActivityService` | Registro y consulta de actividades agrícolas |
| `ExportService` | Generación de paquetes ZIP con datos e imágenes |
| `SyncService` | Orquestación de sincronización manual provider-agnostic |
| `DashboardContextBuilder` | Construcción de indicadores para el dashboard |

### 4. Autenticación (Spec 015)

- Implementada como dependency inyectable (`Depends()`), no como middleware global.
- Rutas protegidas requieren usuario autenticado.
- Rutas públicas: login, static, health.
- Tests existentes no se rompen: fixture mock user en conftest.py.

## Fuera de alcance activo

Los siguientes componentes fueron planificados para un enfoque robótico anterior pero están **fuera del alcance activo** del proyecto:

- `RobotOrchestrator` — no se implementará
- Motor adapters (BTS7960, NoOp, Simulated) — no se implementarán
- `src/infrastructure/robot/` — directorio no existe y no se creará
- GPIO movement scripts — no se implementarán
- Autonomous navigation — no se implementará
- Battery monitor — no se implementará
- Robot safety controller — no se implementará

### Legacy domain interfaces (pendientes de auditoría)

Los siguientes archivos existen en `src/domain/interfaces/` pero no son referenciados por código activo ni tests:

- `robot_movement_service.py` — interfaz abstracta para movimiento de robot (no usada)
- `decision_service.py` — interfaz abstracta para decisiones de movimiento (no usada)

Estos archivos serán evaluados para remoción en una task futura, solo después de confirmar que no tienen dependencias activas.

## Reglas para Kiro

- Cuando se diseñen nuevas capacidades, respetar la separación de capas.
- No introducir RobotOrchestrator, motor adapters, GPIO movement, ni autonomous navigation.
- No tocar CaptureWorker, SnapshotAnalysisService, MonitoringService ni MonitoringState salvo bugs críticos.
- Nuevos servicios de aplicación (auth, alerts, activities, export, sync) van en `src/application/services/`.
- Nuevas entidades van en `src/domain/entities/`.
- Nuevos modelos de persistencia van en `src/infrastructure/persistence/models/`.
- Auth se implementa como Depends(), no middleware global, para proteger tests existentes.
