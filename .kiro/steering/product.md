# Product Steering - Tomato Monitor

## What it is

Portable agricultural visual monitoring and crop traceability system for cherry tomatoes in greenhouses, deployed on Raspberry Pi 5. The authenticated operator physically carries the device through greenhouse modules, captures snapshots using the live camera, runs deferred local inference (detection, health classification, maturity estimation), registers agricultural activities, and produces traceable monitoring reports organized by greenhouse, module, and session.

## Scope adjustment (Spec 015)

The system scope was deliberately adjusted from a robot-oriented approach to a portable embedded platform. There is no autonomous robot, chassis, motors, BTS7960, GPIO movement, or autonomous navigation. The operator manually transports the Raspberry Pi device through the greenhouse. This is a deliberate product decision, not a failed implementation.

## Purpose

- Provide farmers with actionable data: tomato count, maturity distribution (USDA scale), and health status per monitoring session
- Enable agricultural traceability: user + timestamp + module + activity/monitoring
- Evaluate the technical feasibility of embedded computer vision inference on edge hardware
- Generate reproducible experimental evidence for a thesis project
- Deliver a functional precision agriculture tool for controlled greenhouse environments
- Support data export and future remote synchronization

## Users

| User | Context |
|---|---|
| Operator (primary) | Authenticated farmer who carries the Raspberry Pi 5 through the greenhouse, using the DSI 7" touchscreen |
| Thesis team | Develops, tests, benchmarks, and documents the system |
| Academic evaluators | Review architecture, results, methodology, and documentation |
| Precision agriculture researchers | Reference the system as a technical case study |

## Data hierarchy

The operator organizes crops using a three-level hierarchy with traceability:

```
Greenhouse (Invernadero)
  └── Module (Módulo)
        ├── Monitoring (Monitoreo visual)
        │     └── Snapshots + Inference Results + Aggregated Metrics
        └── Activity Log (Bitácora agrícola)
              └── Activity Type + User + Timestamp + Notes
```

- **Greenhouse:** Physical infrastructure container (e.g., "Invernadero Experimental 1")
- **Module:** Rectangular, delimited area within a greenhouse with a specific crop (e.g., "Módulo 1 — Tomate Cherry, 5×2 m")
- **Monitoring:** A single portable monitoring session where the operator manually traverses the module, capturing snapshots for deferred inference
- **Activity Log:** Record of agricultural activities (irrigation, pruning, harvesting, etc.) performed on a module, with user and timestamp traceability

## Current capabilities (validated)

- Greenhouse / Module / Monitoring data hierarchy with full CRUD (SQLite + SQLAlchemy)
- Capture-first workflow: CaptureWorker captures raw snapshots without inference
- Deferred analysis: SnapshotAnalysisService processes snapshots after capture completes
- State machine with ANALYZING state for deferred inference phase
- Live camera preview before starting monitoring
- Detectron2 / RetinaNet detection running on RPi 5 CPU
- ResNet-18 health classification (healthy / unhealthy)
- Maturity estimation via GrabCut + HSV/CIELab colorimetry (6-stage USDA scale)
- Scene Gate (ORB + HSV histogram) with time-based cooldown/timeout
- FastAPI web interface accessible on local network and touchscreen
- SQLite persistence with hierarchical data model (Greenhouse → Module → Monitoring → Snapshot → Result → Metrics)
- Historical monitoring consultation per module
- Monitoring report with annotated snapshots, health/maturity metrics
- Thermal protection: pause during high temperature, visibility in UI
- Second monitoring without restarting the application (camera release validated)
- Raspberry Pi AI Camera connected and validated at hardware level
- Execution profiles (edge/full) selectable via environment variable

## Target capabilities (Spec 015)

- Local offline authentication for registered operators
- Contextual dashboard with real system data indicators
- Module-level monitoring frequency with overdue alerts
- Operational alerts (pending monitoring, pending exports, analysis errors)
- Agricultural activity log with backend-defined catalog
- Combined history (monitorings + activities) per module
- ZIP export for data and images
- Manual provider-agnostic synchronization foundation
- Responsive UI for Raspberry Pi in vertical (portrait) orientation
- Traceability: user + timestamp + module + activity/monitoring

## Primary output

The primary output is **NOT** an annotated video. The primary output is:

- **An agricultural monitoring report** containing:
  - Total tomatoes detected in the monitored module
  - Percentage distribution by USDA maturity stage (green, breaker, turning, pink, light_red, red)
  - Percentage of healthy vs. unhealthy tomatoes
  - Associated snapshots with detection overlays
  - Timestamp, operator, and module metadata for traceability

- **A traceable activity log** with:
  - Agricultural activities registered by the operator
  - User, module, activity type, timestamp, and optional product/quantity

## Priorities

1. Functional portable monitoring on Raspberry Pi 5 with live camera (capture-first)
2. Agricultural traceability (user + activity + timestamp + module)
3. Agricultural metrics useful for the operator (count, maturity %, health %)
4. Stable data persistence with historical consultation
5. Contextual dashboard with real data indicators
6. Data export and synchronization readiness
7. Reproducible evidence for thesis (benchmarks, ADRs, documented decisions)
8. Clean architecture and traceability of changes
9. Optimization based on measurement, not assumption

## Non-goals (current phase)

- Not a commercial or production-grade product
- Not processing video files as the primary workflow (video mode retained only for benchmarking/testing)
- Not replacing Detectron2 without measured evidence justifying the change
- Not using AI Camera NPU for inference until explicitly validated
- Not generating annotated video as the primary output
- Not implementing autonomous navigation, robot chassis, motors, or GPIO movement
- Not implementing a RobotOrchestrator or any autonomous traversal logic
- Not implementing BTS7960 motor drivers or any motor control hardware
- Not implementing advanced agronomic recommendations (irrigation, pest management) without model evidence
- Not implementing mandatory work-shift/jornada management
- Not hardcoding a specific remote sync provider (Drive, S3, Supabase)
- Not editing the activity type catalog from the UI in this version

## Relationship with legacy video pipeline

The original video processing pipeline (VideoInspectionRunner, annotated video generation, per-frame CSV reports) was the initial proof-of-concept that validated the vision components on Raspberry Pi 5. It remains available as:

- A benchmark/testing tool for measuring pipeline performance (Spec 001)
- Historical reference for the evolution of the system
- Fallback mode for development and debugging on PC without camera

The production workflow uses live camera + snapshot capture + per-snapshot deferred inference + aggregated report.
