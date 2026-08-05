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

- `src/infrastructure/vision/` — motor de visión: detector, tracker, clasificador, madurez, scene gate, cropper, orchestrator, runner
- `src/infrastructure/persistence/local/` — repositorios CSV + sistema de archivos
- `src/infrastructure/persistence/cloud/` — stub preparado, no activo
- `src/infrastructure/config/` — `settings.py` (rutas, device, modelos) y `thresholds.py` (umbrales operacionales)

## Entidades del dominio

`FruitDetection`, `HealthAssessment`, `MaturityAssessment`, `InspectionResult`, `InspectionSession`

## Value objects (inmutables)

`BoundingBox`, `FrameReference`, `ModelMetadata`

## Políticas de dominio

- `DeduplicationPolicy` — decide si un track debe reusarse o reprocesarse según área y estado previo
- `InspectionPolicy` — decide si ejecutar salud y/o madurez según tamaño de crop y score

## Interfaces de repositorios (puertos)

`SessionRepository`, `InspectionRepository`, `ArtifactRepository` — definidas en `src/domain/repositories/`

## Inyección de dependencias

Manual, en `app/dependencies.py`. Las instancias se crean por llamada. Sin contenedor DI. Las instancias de `PipelineService` se recrean en cada request (incluye carga de modelos — ver riesgo documentado en `docs/thesis-notes/current-state.md`).

## Reglas de arquitectura

- La lógica de negocio (políticas, entidades) no importa FastAPI, OpenCV, PyTorch ni Detectron2.
- Los módulos de visión en `src/infrastructure/vision/` pueden importar torch, cv2, detectron2 y numpy.
- No crear dependencias circulares entre capas.
- No mover lógica de dominio a la capa de presentación.
- No usar `legacy/` como fuente de imports en código activo; es solo referencia.
- Toda nueva capacidad de visión se integra en `src/infrastructure/vision/`, no en `legacy/`.

## Reglas para hardware y orquestación robótica

### 1. Single Camera Owner

- Solo un componente puede abrir físicamente la cámara durante una sesión de monitoreo.
- `CaptureWorker` es el owner exclusivo durante la fase de captura.
- Un servicio de navegación visual (`VisualNavigationService`) NO debe abrir la cámara directamente.
- La navegación visual consume frames, snapshots o metadatos proporcionados por el owner de cámara (buffer compartido o snapshots guardados).
- Esta regla previene conflictos con Picamera2 que solo permite una apertura concurrente.

### 2. RobotOrchestrator por encima de MonitoringService

- El futuro `RobotOrchestrator` coordina preflight, movimiento del robot y monitoreo.
- No reemplaza `MonitoringService` — lo invoca.
- No reemplaza `CaptureWorker` — MonitoringService sigue creándolo.
- No reemplaza `SnapshotAnalysisService` — el flujo diferido permanece intacto.
- No mezcla lógica GPIO/motores con lógica de negocio de monitoreo.
- El orquestador tiene su propia máquina de estados, ortogonal a `MonitoringState`.

### 3. Hardware via ports/adapters

- Motores, batería, sensores de hardware y navegación deben tener interfaces abstractas (ABCs) en `src/domain/interfaces/` o `src/application/`.
- Las implementaciones concretas viven en `src/infrastructure/robot/`.
- Antes de implementar un adapter real (GPIO, BTS7960, etc.), debe existir un adapter `NoOp` o `Simulated` que permita desarrollo y testing sin hardware.
- Los adapters NoOp logean llamadas sin efecto. Los Simulated mantienen estado virtual (posición, batería).

### 4. Integración progresiva de hardware

El hardware se integra en fases estrictas:

1. **Simulación**: Adapters simulados, RobotOrchestrator funcional end-to-end sin hardware real. Tests automatizados.
2. **Scripts aislados GPIO**: Validación de señales y motores en `scripts/hardware/`, sin el sistema completo.
3. **Adapter concreto**: Implementación de `BTS7960MotorController` (u otro) implementando el ABC de dominio.
4. **Integración**: Conexión del adapter real con el flujo de monitoreo completo.

Cada fase requiere su propia validación antes de avanzar a la siguiente.
