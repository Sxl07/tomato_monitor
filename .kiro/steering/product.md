# Product Steering - Tomato Monitor

## What it is

Agricultural visual monitoring system for cherry tomatoes deployed on Raspberry Pi 5. The robot traverses greenhouse modules using a live camera, captures snapshots when relevant changes are detected, runs inference on those images (detection, health classification, maturity estimation), and produces agricultural metric reports organized by greenhouse, module, and monitoring session.

## Purpose

- Provide farmers with actionable data: tomato count, maturity distribution (USDA scale), and health status per monitoring session
- Evaluate the technical feasibility of real-time computer vision inference on edge hardware
- Generate reproducible experimental evidence for a thesis project
- Deliver a functional precision agriculture tool for controlled greenhouse environments

## Users

| User | Context |
|---|---|
| Farmer (primary) | Operates the touchscreen interface on the Raspberry Pi 5 DSI 7" display inside the greenhouse |
| Thesis team | Develops, tests, benchmarks, and documents the system |
| Academic evaluators | Review architecture, results, methodology, and documentation |
| Precision agriculture researchers | Reference the system as a technical case study |

## Data hierarchy

The farmer organizes crops using a three-level hierarchy:

```
Greenhouse (Invernadero)
  └── Module (Módulo)
        └── Monitoring (Monitoreo)
              └── Snapshots + Inference Results + Aggregated Metrics
```

- **Greenhouse:** Physical infrastructure container (e.g., "Invernadero Experimental 1")
- **Module:** Rectangular, delimited area within a greenhouse with a specific crop (e.g., "Módulo 1 — Tomate Cherry, 5×2 m")
- **Monitoring:** A single robot traversal session with date, time, captured snapshots, inference results, and aggregated agricultural metrics

## Current capabilities (validated)

- Detectron2 / RetinaNet detection running on RPi 5 CPU
- ResNet-18 health classification (healthy / unhealthy)
- Maturity estimation via GrabCut + HSV/CIELab colorimetry (6-stage USDA scale)
- Scene Gate (ORB + HSV histogram) as change detection mechanism
- FastAPI web interface accessible on local network and touchscreen
- Local persistence (currently CSV + filesystem; evolving to SQLite)
- Raspberry Pi AI Camera connected and validated at hardware level

## Target capabilities (next phases)

- Live camera stream with intelligent snapshot capture (change detection)
- Inference per snapshot (not per video frame)
- Agricultural metric reports: total count, % by maturity stage, % healthy/unhealthy
- SQLite persistence with hierarchical data model (Greenhouse → Module → Monitoring → Snapshot → Result)
- Historical monitoring consultation per module
- Touchscreen-optimized UI for the farmer
- Robot traversal integration with configurable module dimensions

## Primary output

The primary output is **NOT** an annotated video. The primary output is:

- **An agricultural monitoring report** containing:
  - Total tomatoes detected in the monitored module
  - Percentage distribution by USDA maturity stage (green, breaker, turning, pink, light_red, red)
  - Percentage of healthy vs. unhealthy tomatoes
  - Associated snapshots with detection overlays
  - Timestamp and module metadata for traceability

## Priorities

1. Functional real-time monitoring on Raspberry Pi 5 with live camera
2. Agricultural metrics useful for the farmer (count, maturity %, health %)
3. Stable data persistence with historical consultation
4. Reproducible evidence for thesis (benchmarks, ADRs, documented decisions)
5. Clean architecture and traceability of changes
6. Optimization based on measurement, not assumption

## Non-goals (current phase)

- Not a commercial or production-grade product
- Not processing video files as the primary workflow (video mode retained only for benchmarking/testing)
- Not replacing Detectron2 without measured evidence justifying the change
- Not using AI Camera NPU for inference until explicitly validated
- Not implementing cloud persistence, remote sync, or multi-user authentication
- Not controlling robot motors directly from the application (separate subsystem)
- Not generating annotated video as the primary output

## Relationship with legacy video pipeline

The original video processing pipeline (VideoInspectionRunner, annotated video generation, per-frame CSV reports) was the initial proof-of-concept that validated the vision components on Raspberry Pi 5. It remains available as:

- A benchmark/testing tool for measuring pipeline performance (Spec 001)
- Historical reference for the evolution of the system
- Fallback mode for development and debugging on PC without camera

The production workflow uses live camera + snapshot capture + per-snapshot inference + aggregated report.
