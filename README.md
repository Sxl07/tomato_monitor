# 🍅 Tomato Monitor

**Sistema de visión por computador para detección, tracking, evaluación de sanidad y estimación de madurez de tomates cherry en video.**

Tomato Monitor es una aplicación construida con **Python**, **FastAPI**, **PyTorch** y **OpenCV** que procesa videos de cultivos de tomate cherry para detectar frutos individuales, rastrearlos entre frames, clasificar su estado sanitario (sano / enfermo) y estimar su grado de madurez según la escala USDA.

---

## Tabla de Contenidos

- [Características Principales](#características-principales)
- [Arquitectura del Proyecto](#arquitectura-del-proyecto)
- [Estructura de Directorios](#estructura-de-directorios)
- [Pipeline de Procesamiento](#pipeline-de-procesamiento)
- [Modelo de Dominio](#modelo-de-dominio)
- [Capa de Aplicación](#capa-de-aplicación)
- [Capa de Infraestructura](#capa-de-infraestructura)
- [Interfaz Web (FastAPI)](#interfaz-web-fastapi)
- [Flujo de Datos](#flujo-de-datos)
- [Tecnologías Utilizadas](#tecnologías-utilizadas)
- [Instalación y Configuración](#instalación-y-configuración)
- [Uso](#uso)
- [Endpoints de la API](#endpoints-de-la-api)
- [Configuración de Umbrales](#configuración-de-umbrales)
- [Código Legacy](#código-legacy)
- [Scripts de Prueba](#scripts-de-prueba)
- [Licencia](#licencia)

---

## Características Principales

- **Detección de tomates** con Detectron2 (RetinaNet R-50-FPN)
- **Tracking multi-objeto** por IoU y distancia de centroide
- **Clasificación de sanidad** con ResNet-18 fine-tuned (sano / enfermo)
- **Estimación de madurez** por colorimetría HSV + espacio CIELab (escala USDA: green → breaker → turning → pink → light_red → red)
- **Scene Gate inteligente** con ORB features + histograma HSV para optimizar cuándo ejecutar el detector
- **Propagación por Optical Flow** (Lucas-Kanade) para mantener tracks en frames sin detección
- **Deduplicación** para evitar re-procesar tracks ya analizados
- **Interfaz web** con FastAPI + Jinja2 para configurar y ejecutar el pipeline
- **Persistencia local** en CSV + sistema de archivos para sesiones y artefactos
- **Generación de reportes** (summary, per_frame, per_detection) en CSV
- **Snapshots y video anotado** con bounding boxes, IDs de track y métricas

---

## Arquitectura del Proyecto

El proyecto sigue una **arquitectura limpia (Clean Architecture)** con separación clara en capas:

```mermaid
graph TB
    subgraph "Capa de Presentación"
        UI["🌐 FastAPI App<br/>(app/)"]
        Templates["📄 Jinja2 Templates"]
        Static["🎨 CSS Estático"]
    end

    subgraph "Capa de Aplicación"
        Services["⚙️ Services<br/>PipelineService<br/>SessionService"]
        UseCases["📋 Use Cases<br/>RunVideoInspection<br/>GetSessionDetail<br/>ListSessions"]
        DTOs["📦 DTOs<br/>InspectionRequest<br/>InspectionSummary<br/>SessionDetail"]
    end

    subgraph "Capa de Dominio"
        Entities["🏛️ Entities<br/>FruitDetection<br/>HealthAssessment<br/>MaturityAssessment<br/>InspectionResult<br/>InspectionSession"]
        ValueObjects["💎 Value Objects<br/>BoundingBox<br/>FrameReference<br/>ModelMetadata"]
        DomainServices["🔧 Domain Services<br/>DeduplicationPolicy<br/>InspectionPolicy"]
        Repositories["📚 Repository Interfaces<br/>SessionRepository<br/>InspectionRepository<br/>ArtifactRepository"]
    end

    subgraph "Capa de Infraestructura"
        Vision["👁️ Vision<br/>Detectron Detector<br/>ResNet Health Classifier<br/>Maturity Colorimetry<br/>Optical Flow Tracker<br/>Pipeline Orchestrator"]
        Persistence["💾 Persistence<br/>CsvSessionRepository<br/>CsvInspectionRepository<br/>FileArtifactRepository"]
        Config["⚙️ Config<br/>Settings<br/>Thresholds"]
    end

    UI --> Services
    UI --> Templates
    UI --> Static
    Services --> UseCases
    UseCases --> DTOs
    UseCases --> Repositories
    Services --> Repositories
    DomainServices --> Entities
    Entities --> ValueObjects
    Vision --> DomainServices
    Vision --> Config
    Persistence -.->|implementa| Repositories

    style UI fill:#4CAF50,color:#fff
    style Entities fill:#2196F3,color:#fff
    style Vision fill:#FF9800,color:#fff
    style Persistence fill:#9C27B0,color:#fff
```

---

## Estructura de Directorios

```
tomato_monitor/
├── app/                            # Capa de presentación (FastAPI)
│   ├── main.py                     # Punto de entrada de la aplicación
│   ├── dependencies.py             # Inyección de dependencias
│   ├── routes/
│   │   ├── ui.py                   # Ruta principal (GET /)
│   │   ├── pipeline.py             # Ruta para ejecutar pipeline (POST /pipeline/run)
│   │   └── sessions.py             # Rutas de sesiones (consulta, eliminación + snapshots)
│   ├── templates/
│   │   ├── base.html               # Template base con header
│   │   ├── index.html              # Página principal con formulario
│   │   └── session_detail.html     # Detalle de sesión con métricas y snapshots
│   └── static/
│       └── styles.css              # Estilos de la interfaz
│
├── src/                            # Código fuente principal (Clean Architecture)
│   ├── domain/                     # Capa de dominio
│   │   ├── entities/               # Entidades del negocio
│   │   │   ├── fruit_detection.py
│   │   │   ├── health_assessment.py
│   │   │   ├── maturity_assessment.py
│   │   │   ├── inspection_result.py
│   │   │   └── inspection_session.py
│   │   ├── value_objects/          # Objetos de valor (inmutables)
│   │   │   ├── bounding_box.py
│   │   │   ├── frame_reference.py
│   │   │   └── model_metadata.py
│   │   ├── services/              # Servicios de dominio (reglas de negocio)
│   │   │   ├── deduplication_policy.py
│   │   │   └── inspection_policy.py
│   │   └── repositories/          # Interfaces de repositorios (puertos)
│   │       ├── session_repository.py
│   │       ├── inspection_repository.py
│   │       └── artifact_repository.py
│   │
│   ├── application/                # Capa de aplicación
│   │   ├── dto/                    # Data Transfer Objects
│   │   │   ├── inspection_request.py
│   │   │   ├── inspection_summary.py
│   │   │   └── session_detail.py
│   │   ├── services/              # Servicios de aplicación
│   │   │   ├── pipeline_service.py
│   │   │   └── session_service.py
│   │   └── use_cases/             # Casos de uso
│   │       ├── run_video_inspection.py
│   │       ├── get_session_detail.py
│   │       └── list_sessions.py
│   │
│   └── infrastructure/            # Capa de infraestructura
│       ├── config/                # Configuración global
│       │   ├── settings.py        # Rutas, device, modelos
│       │   └── thresholds.py      # Umbrales de detección y análisis
│       ├── persistence/           # Implementaciones de repositorios
│       │   ├── local/             # Persistencia local (CSV + archivos)
│       │   │   ├── csv_session_repository.py
│       │   │   ├── csv_inspection_repository.py
│       │   │   ├── file_artifact_repository.py
│       │   │   └── file_utils.py
│       │   └── cloud/             # Persistencia cloud futura (estructura preparada, no activa aún)
│       │       └── postgres_session_repository.py
│       └── vision/                # Motor de visión por computador
│           ├── detectron_detector.py       # Detector con Detectron2
│           ├── resnet_health_classifier.py # Clasificador de sanidad
│           ├── maturity_estimator.py       # Estimador de madurez
│           ├── maturity_colorimetry.py     # Colorimetría CIELab/HSV
│           ├── tracker_adapter.py          # Tracker multi-objeto
│           ├── visual_tracker.py           # Optical Flow (Lucas-Kanade)
│           ├── capture_gate.py             # Scene Gate (ORB + histograma)
│           ├── cropper.py                  # Recorte y validación de crops
│           ├── pipeline_orchestrator.py    # Orquestador del pipeline
│           └── video_inspection_runner.py  # Runner principal del video
│
├── legacy/                         # Código legacy (versión anterior)
│   ├── config/
│   ├── pipeline_core/
│   └── storage/
│
├── scripts/                        # Scripts de prueba / smoke tests
│   ├── compare_video_strategies.py
│   ├── smoke_test_capture.py
│   ├── smoke_test_detector.py
│   ├── smoke_test_health.py
│   └── smoke_test_maturity.py
│
├── models/                         # Modelos entrenados (.pth)
│   ├── health_model/
│   │   └── model.pth               # ResNet-18 para clasificación de sanidad
│   └── modelo_d2/
│       └── model.pth               # Modelo Detectron2 para detección de tomates
│
├── data/                           # Datos de entrada
│   ├── images/                     # Imágenes de referencia
│   └── videos/                     # Videos a procesar
│
├── outputs/                        # Resultados generados
│   ├── experiments/                # Sesiones de experimentos
│   │   └── <session_name>/
│   │       ├── reports/            # CSVs de reportes
│   │       ├── detection_snapshots/
│   │       │   ├── raw_frames/
│   │       │   ├── annotated_frames/
│   │       │   └── crops/
│   │       └── annotated_video/
│   └── meta/
│       └── sessions.csv            # Registro de sesiones
│
├── requirements.txt                # Dependencias de Python
└── .gitignore
```

---

## Pipeline de Procesamiento

El pipeline procesa cada frame del video a través de múltiples etapas:

```mermaid
flowchart TD
    A["📹 Video de entrada"] --> B["Frame N"]
    B --> C{"¿Es primer frame<br/>o Scene Gate activado?"}

    C -->|"Sí (detector_ran=True)"| D["🔍 Detectron2<br/>Detección de tomates"]
    C -->|"No (detector_ran=False)"| E["🔄 Optical Flow<br/>Propagación de tracks"]

    D --> F["📊 Tracker<br/>Asignación de IDs"]
    F --> G{"¿Track nuevo o<br/>mejor vista?"}

    G -->|"Sí"| H["✂️ Crop + Expand"]
    G -->|"No (reusar)"| I["♻️ Reusar resultado<br/>anterior del track"]

    H --> J{"¿Crop válido?"}
    J -->|"Sí"| K["🏥 ResNet-18<br/>Clasificación Sanidad"]
    J -->|"No (muy pequeño)"| M["⏭️ Skip análisis"]

    K --> L{"¿Sano y score alto?"}
    L -->|"Sí"| N["🎨 Colorimetría<br/>Estimación Madurez"]
    L -->|"No"| O["Solo salud registrada"]

    N --> P["📝 Resultado completo<br/>por detección"]
    O --> P
    I --> P
    M --> P
    E --> P

    P --> Q["📊 Métricas por frame"]
    Q --> R{"¿Guardar snapshots?"}
    R -->|"Sí"| S["💾 Raw + Annotated<br/>+ Crops"]
    R -->|"No"| T["Continuar"]
    S --> T

    T --> U{"¿Más frames?"}
    U -->|"Sí"| B
    U -->|"No"| V["📋 Resumen final<br/>+ CSVs de reporte"]

    style D fill:#FF5722,color:#fff
    style K fill:#4CAF50,color:#fff
    style N fill:#FFC107,color:#000
    style E fill:#2196F3,color:#fff
```

---

## Modelo de Dominio

```mermaid
classDiagram
    class FruitDetection {
        +str session_id
        +FrameReference frame_ref
        +int detection_id
        +int track_id
        +BoundingBox bbox
        +float detection_score
        +int class_id
        +bool is_new_track
        +int track_hits
        +bool reused_previous_result
        +bool propagated
        +is_reliable(min_score) bool
        +to_record_dict() dict
    }

    class HealthAssessment {
        +str label
        +float confidence
        +float prob_healthy
        +float prob_unhealthy
        +is_healthy() bool
        +to_dict() dict
    }

    class MaturityAssessment {
        +str usda_stage
        +float maturity_percent
        +float confidence
        +float occlusion_ratio
        +float visible_fruit_ratio
        +str warning
        +is_valid_for_reporting() bool
        +to_dict() dict
    }

    class InspectionResult {
        +FruitDetection detection
        +HealthAssessment health_assessment
        +MaturityAssessment maturity_assessment
        +has_health() bool
        +has_maturity() bool
        +to_record_dict() dict
    }

    class InspectionSession {
        +str session_id
        +str strategy_name
        +str source_video
        +datetime started_at
        +str status
        +datetime completed_at
        +dict parameters
        +mark_running()
        +mark_completed()
        +mark_failed()
        +is_finished() bool
    }

    class BoundingBox {
        <<frozen>>
        +int x1
        +int y1
        +int x2
        +int y2
        +width() int
        +height() int
        +area() int
        +center() tuple
        +is_valid() bool
    }

    class FrameReference {
        <<frozen>>
        +str source_name
        +int frame_index
        +display_name() str
    }

    class ModelMetadata {
        <<frozen>>
        +str model_name
        +str model_version
        +str model_path
        +str device
        +dict extra_info
    }

    InspectionResult --> FruitDetection
    InspectionResult --> HealthAssessment
    InspectionResult --> MaturityAssessment
    FruitDetection --> BoundingBox
    FruitDetection --> FrameReference
```

---

## Capa de Aplicación

### Casos de Uso

```mermaid
flowchart LR
    subgraph "Use Cases"
        UC1["RunVideoInspection"]
        UC2["GetSessionDetail"]
        UC3["ListSessions"]
    end

    subgraph "Services"
        PS["PipelineService"]
        SS["SessionService"]
    end

    subgraph "DTOs"
        IR["InspectionRequest"]
        IS["InspectionSummaryDTO"]
        SD["SessionDetailDTO"]
    end

    PS --> UC1
    SS --> UC2
    SS --> UC3

    UC1 --> IR
    UC1 --> IS
    UC2 --> SD
    UC3 -.-> InspectionSession

    style UC1 fill:#E91E63,color:#fff
    style UC2 fill:#3F51B5,color:#fff
    style UC3 fill:#009688,color:#fff
```

| Caso de Uso | Responsabilidad |
|---|---|
| `RunVideoInspection` | Ejecuta el pipeline completo de inspección de video y genera artefactos |
| `GetSessionDetail` | Obtiene el detalle completo de una sesión existente con sus métricas |
| `ListSessions` | Lista las sesiones más recientes ordenadas por fecha |

### DTOs

| DTO | Campos principales |
|---|---|
| `InspectionRequest` | `strategy_name`, `video_index`, `min_frames_between_detections`, `max_frames_without_detection`, `use_scene_gate`, opciones de guardado |
| `InspectionSummaryDTO` | `total_frames`, `detector_runs`, `effective_fps`, `unique_tracks_detected`, conteos de health/maturity, razones de captura |
| `SessionDetailDTO` | `session_name`, `summary`, rutas de archivos, `snapshot_files`, `annotated_videos` |

---

## Capa de Infraestructura

### Componentes de Visión

```mermaid
flowchart TB
    subgraph "Pipeline Orchestrator"
        PO["pipeline_orchestrator.py"]
    end

    subgraph "Detección"
        DET["detectron_detector.py<br/>RetinaNet R-50-FPN<br/>Detectron2"]
    end

    subgraph "Tracking"
        TRK["tracker_adapter.py<br/>SimpleTracker<br/>(IoU + distancia)"]
        VT["visual_tracker.py<br/>OpticalFlowVisualTracker<br/>(Lucas-Kanade)"]
    end

    subgraph "Análisis"
        HC["resnet_health_classifier.py<br/>ResNet-18 fine-tuned<br/>(sano / enfermo)"]
        ME["maturity_estimator.py<br/>GrabCut segmentation"]
        MC["maturity_colorimetry.py<br/>HSV + CIELab<br/>(escala USDA)"]
    end

    subgraph "Captura"
        CG["capture_gate.py<br/>Scene Gate<br/>(ORB + Histograma HSV)"]
        CR["cropper.py<br/>Expand + Clamp + Validate"]
    end

    PO --> DET
    PO --> TRK
    PO --> HC
    PO --> ME
    ME --> MC
    PO --> CG
    PO --> CR
    PO --> VT

    style DET fill:#F44336,color:#fff
    style HC fill:#4CAF50,color:#fff
    style MC fill:#FF9800,color:#fff
    style VT fill:#2196F3,color:#fff
    style CG fill:#9C27B0,color:#fff
```

### Scene Gate — Decisión de Captura

```mermaid
flowchart TD
    A["Frame actual"] --> B["Preprocessing<br/>(crop centro 60%, resize 320x320, blur)"]
    B --> C["ORB Features<br/>(500 keypoints)"]
    B --> D["HSV Histogram<br/>(50x60 bins)"]

    C --> E{"matches < 35?<br/>(escena cambió)"}
    D --> F{"hist_diff > 0.38?<br/>(color cambió)"}

    E --> G{"cooldown OK?<br/>(≥ 18 frames)"}
    F --> G

    G -->|"Ambos true"| H["✅ Ejecutar detector"]
    G -->|"Alguno false"| I{"timeout?<br/>(≥ 45 frames)"}
    I -->|"Sí"| H
    I -->|"No"| J["⏭️ Skip detector<br/>(usar Optical Flow)"]

    style H fill:#4CAF50,color:#fff
    style J fill:#FF9800,color:#fff
```

### Estimación de Madurez — Escala USDA

```mermaid
flowchart LR
    subgraph "Segmentación"
        S1["GrabCut<br/>(máscara elíptica)"]
        S2["Largest Centered<br/>Component"]
    end

    subgraph "Colorimetría"
        C1["HSV Analysis<br/>(hue channel)"]
        C2["CIELab Stats<br/>(a*, b*)"]
    end

    subgraph "Clasificación USDA"
        U1["🟢 Green<br/>(≥90% verde)"]
        U2["🟡 Breaker<br/>(≥60% verde)"]
        U3["🟠 Turning<br/>(≥30% verde)"]
        U4["🩷 Pink<br/>(<60% rojo)"]
        U5["🔴 Light Red<br/>(<90% rojo)"]
        U6["🔴 Red<br/>(≥90% rojo)"]
    end

    S1 --> S2
    S2 --> C1
    S2 --> C2
    C1 --> U1
    C1 --> U2
    C1 --> U3
    C1 --> U4
    C1 --> U5
    C1 --> U6
```

### Políticas de Dominio

```mermaid
flowchart TD
    subgraph "DeduplicationPolicy"
        D1{"¿Track nuevo?"}
        D1 -->|"Sí"| D2["Procesar<br/>(new_track)"]
        D1 -->|"No"| D3{"¿Ya procesado?"}
        D3 -->|"No"| D4["Procesar<br/>(not_processed_yet)"]
        D3 -->|"Sí"| D5{"¿Mejor vista?<br/>(area > best_area)"}
        D5 -->|"Sí"| D6["Re-procesar<br/>(better_view)"]
        D5 -->|"No"| D7["♻️ Reusar resultado<br/>anterior"]
    end

    subgraph "InspectionPolicy"
        I1{"¿Crop grande?"}
        I1 -->|"No"| I2["Skip todo<br/>(crop_too_small)"]
        I1 -->|"Sí"| I3["✅ Ejecutar Health"]
        I3 --> I4{"¿Score ≥ umbral<br/>madurez?"}
        I4 -->|"No"| I5["Solo Health"]
        I4 -->|"Sí"| I6{"¿Solo para sanos?"}
        I6 -->|"Sí, y es sano"| I7["✅ Health + Maturity"]
        I6 -->|"No es sano"| I5
        I6 -->|"No aplica"| I7
    end

    style D7 fill:#9C27B0,color:#fff
    style I7 fill:#4CAF50,color:#fff
```

---

## Interfaz Web (FastAPI)

```mermaid
flowchart LR
    subgraph "Rutas"
        R1["GET /<br/>Página principal"]
        R2["POST /pipeline/run<br/>Ejecutar pipeline"]
        R3["GET /sessions/<br/>Listar sesiones (JSON)"]
        R4["GET /sessions/{name}<br/>Detalle de sesión"]
        R5["POST /sessions/{name}/delete<br/>Eliminar sesión"]
        R6["GET /sessions/{name}/snapshot/{file}<br/>Servir snapshot"]
    end

    subgraph "Templates"
        T1["base.html"]
        T2["index.html"]
        T3["session_detail.html"]
    end

    R1 --> T2
    R2 --> T2
    R4 --> T3
    T2 --> T1
    T3 --> T1

    style R1 fill:#4CAF50,color:#fff
    style R2 fill:#FF5722,color:#fff
    style R4 fill:#2196F3,color:#fff
```

---

## Flujo de Datos

```mermaid
sequenceDiagram
    actor User as Usuario
    participant UI as FastAPI UI
    participant PS as PipelineService
    participant UC as RunVideoInspection
    participant VIR as VideoInspectionRunner
    participant PO as PipelineOrchestrator
    participant DET as Detectron2
    participant TRK as Tracker
    participant HC as HealthClassifier
    participant ME as MaturityEstimator
    participant AR as ArtifactRepository

    User->>UI: POST /pipeline/run (config)
    UI->>PS: run_video_inspection(request)
    PS->>UC: execute(request, runner)

    UC->>VIR: run_video_inspection(params)
    VIR->>PO: build_pipeline_components()
    PO-->>VIR: PipelineComponents

    loop Para cada frame del video
        VIR->>VIR: should_run_detector? (Scene Gate)

        alt Detector activado
            VIR->>PO: process_frame(frame)
            PO->>DET: run_detection(frame)
            DET-->>PO: detections[]
            PO->>TRK: update(detections)
            TRK-->>PO: tracked_detections[]

            loop Para cada detección
                PO->>PO: DeduplicationPolicy.should_reuse?
                alt Procesar nuevo
                    PO->>HC: predict_health(crop)
                    HC-->>PO: {label, confidence}
                    PO->>ME: estimate_maturity(crop)
                    ME-->>PO: {usda_stage, maturity%}
                end
            end
            PO-->>VIR: frame_result
        else Optical Flow
            VIR->>VIR: propagate(frame)
            VIR-->>VIR: propagated_detections
        end

        VIR->>VIR: draw_annotations(frame)
        VIR->>AR: save_snapshots, save_video
    end

    VIR->>VIR: summarize_results()
    VIR-->>UC: summary_dict
    UC->>AR: create_session_dirs()
    UC-->>PS: RunVideoInspectionResult
    PS-->>UI: result
    UI-->>User: Página con métricas + links
```

---

## Tecnologías Utilizadas

| Categoría | Tecnología | Uso |
|---|---|---|
| **Framework Web** | FastAPI + Uvicorn | API REST y servidor de la aplicación web |
| **Templates** | Jinja2 | Renderizado de vistas HTML |
| **Deep Learning** | PyTorch + TorchVision | Modelo ResNet-18 para clasificación de sanidad |
| **Detección de Objetos** | Detectron2 | RetinaNet R-50-FPN para detección de tomates |
| **Visión por Computador** | OpenCV + NumPy | Procesamiento de video, Optical Flow, GrabCut, ORB, histogramas y operaciones numéricas |
| **Persistencia** | CSV + Sistema de archivos | Almacenamiento de sesiones, reportes y artefactos |
| **Lenguaje** | Python 3.10+ | Todo el proyecto |

---

## Instalación y Configuración

### Requisitos Previos

- **Python 3.10** o superior
- **pip** para gestión de paquetes
- (Opcional) Entorno virtual (`venv`)

### Pasos

1. **Clonar el repositorio:**

```bash
git clone https://github.com/Sxl07/tomato_monitor.git
cd tomato_monitor
```

2. **Crear y activar un entorno virtual:**

```bash
python -m venv .venv
source .venv/bin/activate    # Linux/macOS
# .venv\Scripts\activate     # Windows
```

3. **Instalar dependencias:**

```bash
pip install -r requirements.txt
```

4. **Verificar los modelos:**

Asegúrate de que los archivos de modelos existen:

```
models/health_model/model.pth    # Modelo ResNet-18 de sanidad
models/modelo_d2/model.pth       # Modelo Detectron2 de detección (no incluido por defecto)
```

5. **Colocar videos de entrada:**

```
data/videos/video_02.mp4         # Video(s) de cultivo de tomate cherry
```
### Nota sobre dispositivo de ejecución

En el estado actual del proyecto, la ejecución se recomienda en **CPU** para mantener estabilidad y coherencia con el entorno objetivo en **Raspberry Pi**. Aunque la infraestructura permite configuración de dispositivo, la validación principal del sistema se está realizando en CPU.

---

## Uso

### Iniciar el servidor

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Acceder a: `http://localhost:8000`

### Desde la interfaz web

1. Abrir el navegador en `http://localhost:8000`
2. Configurar los parámetros del pipeline en el formulario
3. Hacer clic en **"Ejecutar pipeline"**
4. Ver los resultados: métricas de resumen, archivos generados
5. Navegar a las sesiones individuales para ver snapshots anotados y videos

---

## Endpoints de la API

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/` | Página principal con formulario de configuración |
| `POST` | `/pipeline/run` | Ejecutar el pipeline con los parámetros dados |
| `GET` | `/sessions/` | Lista de sesiones recientes (JSON) |
| `GET` | `/sessions/{name}` | Detalle de una sesión con métricas y snapshots |
| `POST` | `/sessions/{name}/delete` | Eliminar una sesión y sus artefactos |
| `GET` | `/sessions/{name}/snapshot/{file}` | Servir imagen de snapshot anotado |

---

## Configuración de Umbrales

Los umbrales son configurables en `src/infrastructure/config/thresholds.py`:

| Parámetro | Valor | Descripción |
|---|---|---|
| `DETECTION_SCORE_THRESHOLD` | 0.80 | Umbral mínimo de confianza para detección |
| `HEALTH_B_THRESHOLD` | 0.70 | Umbral para clasificar como "unhealthy" |
| `HEALTH_IMAGE_SIZE` | 224 | Tamaño de imagen para el modelo de sanidad |
| `CROP_EXPAND_RATIO` | 0.08 | Ratio de expansión del bounding box |
| `MIN_CROP_WIDTH` / `MIN_CROP_HEIGHT` | 20 | Tamaño mínimo de crop para análisis |
| `MATURITY_MIN_DET_SCORE` | 0.80 | Score mínimo para ejecutar estimación de madurez |
| `MIN_FRAMES_BETWEEN_CAPTURES` | 18 | Cooldown mínimo entre ejecuciones del detector |
| `MAX_FRAMES_WITHOUT_CAPTURE` | 45 | Forzar detección si no se ha ejecutado en N frames |
| `ORB_MIN_MATCH_COUNT` | 35 | Umbral de features ORB para detectar cambio de escena |
| `HSV_HIST_DIFF_THRESHOLD` | 0.38 | Umbral de diferencia de histograma HSV |

---

## Código Legacy

El directorio `legacy/` contiene la **versión anterior** del pipeline antes de la migración a Clean Architecture. Se mantiene como referencia y para compatibilidad:

```mermaid
flowchart LR
    subgraph "Legacy (legacy/)"
        LC["config/<br/>settings.py<br/>thresholds.py"]
        LP["pipeline_core/<br/>orchestrator.py<br/>detector.py<br/>health.py<br/>maturity.py<br/>tracker.py<br/>visual_tracker.py<br/>cropper.py<br/>capture_logic.py"]
        LS["storage/<br/>csv_store.py<br/>file_store.py"]
    end

    subgraph "Actual (src/)"
        SC["infrastructure/config/"]
        SV["infrastructure/vision/"]
        SP["infrastructure/persistence/"]
        SD["domain/"]
        SA["application/"]
    end

    LP -.->|"migrado a"| SV
    LP -.->|"reglas extraídas a"| SD
    LS -.->|"migrado a"| SP
    LC -.->|"migrado a"| SC

    style LP fill:#757575,color:#fff
    style SV fill:#4CAF50,color:#fff
```

---

## Scripts de Prueba

En el directorio `scripts/` se encuentran smoke tests para validar componentes individuales:

| Script | Propósito |
|---|---|
| `smoke_test_detector.py` | Prueba rápida del detector Detectron2 |
| `smoke_test_health.py` | Prueba del clasificador de sanidad ResNet-18 |
| `smoke_test_maturity.py` | Prueba del estimador de madurez |
| `smoke_test_capture.py` | Prueba del Scene Gate (ORB + histograma) |
| `compare_video_strategies.py` | Comparación de diferentes estrategias de procesamiento |

---

## Licencia

Este proyecto es de uso académico / personal. Consulta con los autores para más información.
