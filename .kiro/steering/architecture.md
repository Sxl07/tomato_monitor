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
