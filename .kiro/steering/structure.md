# Structure Steering - Tomato Monitor

## Mapa de directorios

```
tomato_monitor/
├── app/                        # Presentación: FastAPI, rutas, templates, CSS
│   ├── main.py                 # Entry point: registra routers
│   ├── dependencies.py         # Inyección de dependencias (manual, por request)
│   ├── context_builders.py     # Builders de contexto para templates
│   └── routes/                 # agricultural_ui.py | monitoring.py | monitoring_api.py | ui.py | pipeline.py | sessions.py
├── src/
│   ├── domain/                 # Entidades, value objects, políticas, interfaces de repos
│   │   ├── entities/           # monitoring.py, snapshot.py, greenhouse.py, module.py, etc.
│   │   ├── interfaces/         # frame_source.py (+ legacy: robot_movement_service.py, decision_service.py — pending removal)
│   │   ├── repositories/       # ABCs: monitoring, snapshot, inspection_result, metrics, module, greenhouse
│   │   └── value_objects/      # monitoring_status.py (state machine con ANALYZING)
│   ├── application/            # DTOs, servicios de app, casos de uso
│   │   ├── services/           # monitoring_service.py, capture_worker.py, snapshot_analysis_service.py, monitoring_runtime_registry.py, pipeline_metrics.py, camera_service.py, model_service.py, log_service.py
│   │   └── dtos/               # monitoring_dtos.py
│   └── infrastructure/
│       ├── config/             # settings.py (perfiles edge/full), thresholds.py
│       ├── vision/             # Motor de visión: detector, tracker, health, maturity, gate, orchestrator, runner, annotation_renderer
│       ├── camera/             # Frame sources: opencv, raspberry (Picamera2), video_file
│       ├── monitoring/         # thermal_monitor.py
│       ├── security/           # path_sanitizer.py
│       └── persistence/
│           ├── repositories/   # SQLAlchemy implementations
│           ├── local/          # CSV + filesystem (legacy benchmark mode)
│           └── cloud/          # Stub PostgreSQL (no activo)
├── tests/                      # Suite pytest (~806 tests)
│   ├── unit/                   # Tests unitarios (no requieren hardware)
│   ├── domain/                 # Tests de entidades y value objects
│   ├── infrastructure/         # Tests de persistencia y repos
│   ├── application/            # Tests de servicios de aplicación
│   ├── properties/             # Tests property-based (Hypothesis)
│   ├── conftest.py             # Fixtures compartidas (in-memory SQLite)
│   └── test_imports.py         # Verifica importabilidad de módulos
├── legacy/                     # Pipeline anterior migrado — solo referencia, no importar
├── models/
│   ├── modelo_d2/model.pth     # RetinaNet R-50-FPN (detección)
│   └── health_model/model.pth  # ResNet-18 (sanidad)
├── data/
│   ├── images/                 # Imágenes de referencia
│   ├── videos/                 # Videos de entrada (video_02.mp4)
│   └── tomato_monitor.db       # SQLite (no versionado)
├── outputs/                    # NO versionado — generado en runtime
│   └── monitorings/{id}/       # snapshots/raw/, annotated_snapshots/, crops/, reports/
├── scripts/                    # Smoke tests y scripts de benchmark/utilidad
├── docs/
│   ├── decisions/              # ADR-001, ADR-002, ADR-003
│   ├── benchmarks/             # benchmark-template.md, raspberry-baseline.md
│   └── thesis-notes/           # current-state.md, next-steps.md, limitations.md
└── .kiro/
    ├── steering/               # Steering files (este directorio)
    └── specs/                  # 001..010 + bugfix specs
```

### Fuera del alcance activo (no crear)

Los siguientes directorios fueron propuestos para un enfoque robótico anterior y no forman parte de la arquitectura activa. No crear estos directorios.

```
src/infrastructure/robot/       # No implementar — alcance robótico descartado
scripts/hardware/               # No implementar — no hay hardware de movimiento
```

## Reglas de organización

- El código activo del pipeline vive en `src/infrastructure/vision/`; no en `legacy/`
- Los scripts experimentales, smoke tests y benchmarks van en `scripts/`
- Los resultados de benchmark se documentan en `docs/benchmarks/`, nunca solo en `outputs/`
- Los nuevos ADRs siguen el formato `docs/decisions/ADR-NNN-titulo.md`
- `app/routes/` solo contiene rutas HTTP; la lógica va en `src/application/`
- `legacy/` no se importa desde ningún módulo activo; es solo referencia histórica
- `outputs/` no se versiona en Git (está en `.gitignore`)
- Los modelos `.pth` no se versionan en Git salvo decisión explícita

## Convenciones de nombres

- Módulos de visión: `snake_case.py` descriptivo del componente (`detectron_detector.py`, `capture_gate.py`)
- Entidades de dominio: `snake_case.py` con nombre del concepto (`fruit_detection.py`)
- Repositorios: prefijo tecnología + concepto (`csv_session_repository.py`)
- Specs: prefijo numérico + kebab-case (`001-raspberry-baseline-benchmark`)
