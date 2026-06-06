# Design - Thesis and Testing Documentation

## Overview

This spec defines the documentation structure for Tomato Monitor. The goal is to keep the project understandable, reproducible, and aligned with thesis requirements.

Documentation should serve three audiences:

1. The development team.
2. Kiro as an AI technical assistant.
3. Thesis evaluators or technical reviewers.

## Documentation Structure

Recommended structure:

```text
docs/
├── project-overview.md
├── hardware.md
├── raspberry-setup.md
├── architecture.md
├── camera-live-integration.md
├── decisions/
│   ├── ADR-001-detectron2-on-raspberry.md
│   ├── ADR-002-ai-camera-strategy.md
│   └── ADR-003-edge-benchmark-first.md
├── benchmarks/
│   ├── benchmark-template.md
│   ├── raspberry-baseline.md
│   └── raspberry-optimized.md
└── thesis-notes/
    ├── current-state.md
    ├── next-steps.md
    └── limitations.md

Document Responsibilities
docs/project-overview.md

Purpose:

Explain what Tomato Monitor is.
Define objective and scope.
Summarize current state.
Explain main components.
docs/hardware.md

Purpose:

List hardware used.
Document Raspberry Pi 5.
Document AI Camera.
Document power supply, screen, cooling, and other components.
Track hardware validation status.
docs/raspberry-setup.md

Purpose:

Explain Raspberry setup.
List system packages.
Explain virtual environment setup.
Document Python dependencies.
Document Detectron2 installation.
Record known issues such as unavailable libatlas-base-dev.
docs/architecture.md

Purpose:

Explain architecture layers.
Describe FastAPI app layer.
Describe application services.
Describe domain layer.
Describe infrastructure layer.
Describe vision pipeline.
Include data flow diagrams later if useful.
docs/benchmarks/benchmark-template.md

Purpose:

Provide a standard test report format.
Ensure all benchmark results include hardware, software, configuration, and metrics.
docs/benchmarks/raspberry-baseline.md

Purpose:

Record baseline Raspberry results.
Include API validation, model loading, inference, RAM, CPU, temperature, and observations.
docs/decisions/

Purpose:

Store Architecture Decision Records.
Explain why important decisions were made.
Preserve thesis evidence and technical reasoning.
docs/thesis-notes/

Purpose:

Keep thesis-oriented summaries.
Track current state.
Track next steps.
Track limitations and possible future work.
ADR Template

Every ADR should follow this structure:

# ADR-XXX: Title

## Status

Proposed | Accepted | Superseded

## Date

YYYY-MM-DD

## Context

Explain the technical situation and constraints.

## Decision

Explain the decision made.

## Consequences

### Positive

- ...

### Negative

- ...

## Evidence

List benchmark data, tests, observations, or references.

## Related Specs

- ...
Benchmark Report Template

Benchmark documentation should include:

Test name.
Date.
Device.
OS.
Python version.
Git commit.
Branch.
Hardware.
Cooling.
Dependencies.
Input data.
Configuration.
Metrics.
Observations.
Conclusion.
Next actions.
Documentation Rules
Do not invent benchmark values.
Distinguish between measured facts and hypotheses.
Use clear technical language.
Keep claims traceable.
Update documentation when specs produce relevant changes.
Use ADRs for decisions, not casual notes.
Keep long thesis explanations in docs/thesis-notes/, not steering files.
Testing Documentation Strategy

Testing documentation should separate:

Lightweight validation

Examples:

Import checks.
FastAPI startup.
Camera availability.
Configuration loading.
Benchmark tests

Examples:

Model load time.
Single-frame inference.
Full video pipeline.
Continuous processing.
Manual Raspberry tests

Examples:

Temperature monitoring.
CPU/RAM observation.
Camera validation.
Cooling validation.
Security Considerations

Documentation must not include:

Secrets.
Tokens.
Private credentials.
Personal network information.
Unnecessary absolute local paths.
Sensitive configuration files.
Maintenance Strategy

Documentation should be updated in the same branch as the feature or benchmark it describes. Documentation-only changes should use docs: commits.

Benchmark result changes should use bench: or docs: commits depending on repository convention.