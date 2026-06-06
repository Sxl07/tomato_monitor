# Structure Steering - Tomato Monitor

## Mapa de directorios

```
tomato_monitor/
├── app/                        # Presentación: FastAPI, rutas, templates, CSS
│   ├── main.py                 # Entry point: registra routers
│   ├── dependencies.py         # Inyección de dependencias (manual, por request)
│   └── routes/                 # ui.py | pipeline.py | sessions.py
├── src/
│   ├── domain/                 # Entidades, value objects, políticas, interfaces de repos
│   ├── application/            # DTOs, servicios de app, casos de uso
│   └── infrastructure/
│       ├── config/             # settings.py (rutas/device/modelos), thresholds.py
│       ├── vision/             # Motor de visión: detector, tracker, health, maturity, gate
│       └── persistence/
│           ├── local/          # CSV + filesystem (activo)
│           └── cloud/          # Stub PostgreSQL (no activo)
├── legacy/                     # Pipeline anterior migrado — solo referencia, no importar
├── models/
│   ├── modelo_d2/model.pth     # RetinaNet R-50-FPN (detección)
│   └── health_model/model.pth  # ResNet-18 (sanidad)
├── data/
│   ├── images/                 # Imágenes de referencia
│   └── videos/                 # Videos de entrada (video_02.mp4)
├── outputs/
│   ├── experiments/            # Sesiones con reports/, snapshots/, annotated_video/
│   └── meta/sessions.csv       # Registro de sesiones
├── scripts/                    # Smoke tests y scripts de benchmark/utilidad
├── docs/
│   ├── decisions/              # ADR-001, ADR-002, ADR-003
│   ├── benchmarks/             # benchmark-template.md, raspberry-baseline.md
│   └── thesis-notes/           # current-state.md, next-steps.md
└── .kiro/
    ├── steering/               # Steering files (este directorio)
    └── specs/                  # 001, 002, 003, 004
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
