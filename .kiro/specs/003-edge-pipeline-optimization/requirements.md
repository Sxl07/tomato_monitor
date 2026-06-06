# Requirements - Edge Pipeline Optimization

## Objective

Optimize the Tomato Monitor vision pipeline for Raspberry Pi 5 execution based on measured benchmark results, while preserving correctness, traceability, and thesis-quality documentation.

## Context

The current pipeline can run on Raspberry Pi 5, but it creates high CPU load and temperature increase. RAM usage reached approximately 2.1 GB during heavy testing, suggesting that the main bottleneck is CPU/temperature rather than memory.

The current pipeline includes Detectron2/RetinaNet detection, health classification, crops, snapshots, annotated video generation, CSV outputs, and offline video processing.

This spec must depend on benchmark evidence. Optimization should not be performed blindly.

## Functional Requirements

### RF-001: Configurable artifact generation

WHEN the pipeline runs in Raspberry or edge mode  
THE SYSTEM SHALL allow enabling or disabling heavy artifacts such as annotated video, snapshots, and crops.

### RF-002: Configurable input resolution

WHEN the pipeline processes video or camera frames  
THE SYSTEM SHALL allow using a lower processing resolution.

### RF-003: Configurable detection frequency

WHEN continuous processing is executed  
THE SYSTEM SHALL allow reducing detector execution frequency where technically appropriate.

### RF-004: Benchmark-driven optimization

WHEN an optimization is proposed  
THE SYSTEM SHALL reference benchmark data or a clearly defined metric target.

### RF-005: Edge execution profile

WHEN the user selects an edge profile  
THE SYSTEM SHALL use conservative defaults suitable for Raspberry Pi execution.

### RF-006: Preserve desktop/full mode

WHEN the user runs the application outside edge mode  
THE SYSTEM SHALL preserve the existing full processing behavior unless explicitly configured otherwise.

### RF-007: Result traceability

WHEN optimized pipeline results are generated  
THE SYSTEM SHALL record the configuration used to produce those results.

## Non-Functional Requirements

### RNF-001: Raspberry Pi compatibility

THE SYSTEM SHALL run on Raspberry Pi 5 using CPU by default.

### RNF-002: Thermal awareness

THE SYSTEM SHALL avoid encouraging long-running heavy workloads without temperature monitoring.

### RNF-003: Minimal invasive refactor

THE SYSTEM SHALL prefer configuration-based changes before large architectural rewrites.

### RNF-004: Maintainability

THE SYSTEM SHALL keep optimization logic readable, configurable, and documented.

### RNF-005: No premature detector migration

THE SYSTEM SHALL NOT replace Detectron2 unless benchmark evidence justifies it and a separate design decision is documented.

### RNF-006: Documentation

THE SYSTEM SHALL document all optimization decisions, benchmark results, and trade-offs.

## Acceptance Criteria

- An edge execution profile is defined.
- Heavy outputs can be disabled through configuration.
- Processing resolution can be configured.
- Optimization changes reference benchmark evidence.
- Desktop/full mode remains available.
- Results include configuration metadata.
- Relevant decisions are documented in ADRs.
- Raspberry performance is measured before and after optimization.

## Out of Scope

- Full rewrite of the vision pipeline.
- Training new models.
- Replacing Detectron2 without a separate decision.
- Implementing IMX500 inference on the AI Camera.
- Cloud offloading.