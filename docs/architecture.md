# Architecture — Tomato Monitor

## Overview

Tomato Monitor follows Clean Architecture principles with four ordered layers. Dependencies flow inward: outer layers depend on inner layers, never the reverse. The Domain layer has no external dependencies.

```
Presentation → Application → Domain
Infrastructure → Domain
```

This separation allows the business logic (domain) and application rules (use cases) to remain independent of frameworks, databases, and hardware-specific components.

---

## Layer Map

| Layer | Directory | Responsibility |
|---|---|---|
| Presentation | `app/` | FastAPI application, HTTP routes, Jinja2 templates, static files |
| Application | `src/application/` | DTOs, application services, use cases |
| Domain | `src/domain/` | Entities, value objects, domain policies, repository interfaces |
| Infrastructure | `src/infrastructure/` | Vision pipeline, persistence, configuration |

---

## Presentation Layer — `app/`

Entry point: `app/main.py` registers the three routers and mounts static files.

Dependency injection is handled manually in `app/dependencies.py`. Instances are created per request. This means models are reloaded on each pipeline invocation from the UI — a known performance risk documented in `docs/thesis-notes/current-state.md`.

### Routes

| File | Method | Route | Description |
|---|---|---|---|
| `ui.py` | GET | `/` | Main page with pipeline configuration form |
| `pipeline.py` | POST | `/pipeline/run` | Execute vision pipeline with given parameters |
| `sessions.py` | GET | `/sessions/` | List recent sessions (JSON) |
| `sessions.py` | GET | `/sessions/{name}` | Session detail with metrics and snapshots |
| `sessions.py` | POST | `/sessions/{name}/delete` | Delete session and artifacts |
| `sessions.py` | GET | `/sessions/{name}/snapshot/{file}` | Serve annotated snapshot image |

### Templates

| File | Purpose |
|---|---|
| `base.html` | Base layout with shared header |
| `index.html` | Main form for pipeline configuration and execution |
| `session_detail.html` | Session results: metrics, snapshots, annotated video links |

---

## Application Layer — `src/application/`

Contains the orchestration logic that connects the presentation layer to domain and infrastructure, without containing business rules itself.

### Use Cases

| Use Case | Responsibility |
|---|---|
| `RunVideoInspection` | Executes the full video inspection pipeline and produces artifacts |
| `GetSessionDetail` | Retrieves complete session data including metrics and file paths |
| `ListSessions` | Returns recent sessions ordered by date |

### Application Services

| Service | Responsibility |
|---|---|
| `PipelineService` | Orchestrates pipeline execution; delegates to use cases |
| `SessionService` | Delegates session retrieval and listing to use cases |

### DTOs

| DTO | Key Fields |
|---|---|
| `InspectionRequest` | `strategy_name`, `video_index`, `min_frames_between_detections`, `max_frames_without_detection`, `use_scene_gate`, output options |
| `InspectionSummaryDTO` | `total_frames`, `detector_runs`, `effective_fps`, `unique_tracks_detected`, health/maturity counts, capture reasons |
| `SessionDetailDTO` | `session_name`, `summary`, file paths, `snapshot_files`, `annotated_videos` |

---

## Domain Layer — `src/domain/`

The domain layer contains pure business concepts. It has no dependency on FastAPI, PyTorch, OpenCV, Detectron2, or any file system API.

### Entities

| Entity | Description |
|---|---|
| `FruitDetection` | Represents a single detected tomato in a frame: bbox, track ID, detection score, propagation flag |
| `HealthAssessment` | Health classification result: label (healthy/unhealthy), confidence, class probabilities |
| `MaturityAssessment` | Maturity estimation result: USDA stage, maturity percentage, occlusion ratio, warnings |
| `InspectionResult` | Aggregates one detection with its health and maturity assessments |
| `InspectionSession` | Session lifecycle: session ID, source video, status, timestamps, parameters |

### Value Objects (immutable, frozen)

| Value Object | Description |
|---|---|
| `BoundingBox` | Pixel coordinates (x1, y1, x2, y2) with geometry helpers (area, center, validity) |
| `FrameReference` | Source name + frame index; identifies a specific frame within a video |
| `ModelMetadata` | Model name, version, path, device, and extra info |

### Domain Policies

| Policy | Description |
|---|---|
| `DeduplicationPolicy` | Decides whether a track should be reprocessed or reuse a previous result, based on track history and crop area |
| `InspectionPolicy` | Decides whether to run health and/or maturity analysis based on crop size and detection score |

### Repository Interfaces (ports)

Defined in `src/domain/repositories/`. These are abstract interfaces that infrastructure implements.

| Interface | Description |
|---|---|
| `SessionRepository` | Store and retrieve inspection sessions |
| `InspectionRepository` | Store and retrieve per-frame detection results |
| `ArtifactRepository` | Manage output files: snapshots, crops, annotated video, CSV reports |

---

## Infrastructure Layer — `src/infrastructure/`

Implements the interfaces defined in the domain layer. Contains all framework-specific, hardware-specific, and I/O-specific code.

### Vision Pipeline — `src/infrastructure/vision/`

All vision components live here. They may depend on `torch`, `cv2`, `detectron2`, and `numpy`.

| Module | Role |
|---|---|
| `pipeline_orchestrator.py` | Coordinates detection → tracking → deduplication → health → maturity for each frame |
| `video_inspection_runner.py` | Iterates video frames, applies scene gate, writes artifacts |
| `detectron_detector.py` | Detectron2 / RetinaNet R-50-FPN; 1 class (`cherry_tomato`); CPU execution |
| `resnet_health_classifier.py` | ResNet-18 fine-tuned; 2 classes (`healthy` / `unhealthy`); CPU execution |
| `maturity_estimator.py` | GrabCut segmentation with elliptical prior mask; delegates colorimetry |
| `maturity_colorimetry.py` | HSV + CIELab analysis; 6-stage USDA scale |
| `tracker_adapter.py` | SimpleTracker: IoU ≥ 0.30 and centroid distance ≤ 120 px |
| `visual_tracker.py` | Lucas-Kanade Optical Flow; propagates tracks in frames where detector does not run |
| `capture_gate.py` | Scene Gate: ORB (500 keypoints) + HSV histogram (50×60 bins) |
| `cropper.py` | Bounding box expansion (ratio 0.08), frame clamping, minimum size validation |

#### Pipeline Flow per Frame

```
frame
  → Scene Gate decision
      YES (detector trigger) → Detectron2 → Tracker → DeduplicationPolicy
                                → crop → InspectionPolicy → Health → Maturity
      NO (optical flow)      → Lucas-Kanade propagation
  → annotation
  → artifact writing (conditional)
```

#### Scene Gate Parameters

| Parameter | Value | Meaning |
|---|---|---|
| Cooldown | ≥ 18 frames | Minimum frames between detector runs |
| Timeout | ≥ 45 frames | Force detector after this many frames without detection |
| ORB threshold | matches < 35 | Scene change detected |
| HSV histogram threshold | diff > 0.38 | Color change detected |
| Trigger condition | `cooldown_ok AND orb_changed AND hist_changed` OR timeout | |

### Configuration — `src/infrastructure/config/`

| File | Contents |
|---|---|
| `settings.py` | Output directories, model paths, device (`cpu`), video path |
| `thresholds.py` | Detection score threshold (0.80), health threshold (0.70), maturity score (0.80), crop minimum size (20 px), Scene Gate parameters |

### Persistence — `src/infrastructure/persistence/`

Active implementation uses local filesystem.

| Implementation | Location | Description |
|---|---|---|
| `CsvSessionRepository` | `persistence/local/` | Stores session metadata in `outputs/meta/sessions.csv` |
| `CsvInspectionRepository` | `persistence/local/` | Stores per-detection results in per-session CSV files |
| `FileArtifactRepository` | `persistence/local/` | Manages raw frames, annotated frames, crops, annotated video |
| `PostgresSessionRepository` | `persistence/cloud/` | Stub only — not active, not connected |

---

## Domain Model Diagram

```
InspectionResult
  ├── FruitDetection
  │     ├── BoundingBox (frozen)
  │     └── FrameReference (frozen)
  ├── HealthAssessment
  └── MaturityAssessment

InspectionSession
  └── (standalone lifecycle entity)

ModelMetadata (frozen)
  └── (used by vision components at load time)
```

---

## Dependency Rules (enforced by convention)

- `src/domain/` imports nothing outside the standard library.
- `src/application/` imports from `src/domain/` only.
- `src/infrastructure/` imports from `src/domain/` and may use external libraries.
- `app/` imports from `src/application/` and `src/infrastructure/config/`.
- `legacy/` is not imported by any active module.

---

## Known Architectural Risks

| Risk | Description | Reference |
|---|---|---|
| Model reload per request | `PipelineService` is instantiated on every HTTP request, causing model reload | `docs/thesis-notes/current-state.md` |
| No singleton model cache | No model caching mechanism; each pipeline call reloads Detectron2 and ResNet-18 | Spec 005 |
| No automated test suite | Only manual smoke tests exist in `scripts/`; domain policies are not unit-tested | Spec 005 |
| GrabCut on small crops | May return empty mask; fallback to centered ellipse is implemented but not formally tested | ADR-003 |
| Cloud persistence stub | `postgres_session_repository.py` exists but is not wired or tested | Not blocked |

---

## Output Structure

```
outputs/
├── experiments/
│   └── {session_name}/
│       ├── reports/
│       │   ├── summary.csv
│       │   ├── per_frame.csv
│       │   └── per_detection.csv
│       ├── detection_snapshots/
│       │   ├── raw_frames/
│       │   ├── annotated_frames/
│       │   └── crops/
│       └── annotated_video/
│           └── {video_name}_{session}.mp4
└── meta/
    └── sessions.csv
```

The `outputs/` directory is excluded from version control (`.gitignore`).

---

## Related Documents

- `README.md` — project overview with Mermaid diagrams of all layers
- `docs/decisions/ADR-001` — rationale for keeping Detectron2 during benchmarking phase
- `docs/decisions/ADR-002` — AI Camera integration strategy
- `docs/decisions/ADR-003` — benchmark-first optimization principle
- `docs/thesis-notes/current-state.md` — current technical debt and validation status
- `.kiro/steering/architecture.md` — architectural rules for Kiro
