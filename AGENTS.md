# AGENTS.md — Tomato Monitor

This file defines rules and context that apply to all AI-assisted development on this project.
It is read by Kiro and any other AI agent working in this repository.

---

## Project context

Tomato Monitor is a computer vision thesis project targeting Raspberry Pi 5 (ARM64, CPU-only).
The pipeline detects cherry tomatoes, classifies health status, and estimates maturity using the USDA scale.
The application runs on FastAPI with a local filesystem persistence layer.

Development follows Spec Driven Development using `.kiro/specs/`. No major change is implemented without an approved spec.

Current phase: baseline benchmark on Raspberry Pi 5 (Spec 001).

---

## Language conventions

- Steering files, specs, ADRs, and thesis documentation are written in **Spanish**.
- Source code, comments, docstrings, commit messages, and this file are written in **English**.
- Do not mix languages within a single file unless there is an explicit reason.

---

## Software quality standards

All AI-assisted changes must follow:

- Clean Code principles.
- SOLID principles where appropriate, without over-engineering.
- Separation of concerns across layers (presentation, application, domain, infrastructure).
- Low coupling and high cohesion.
- Explicit error handling at entry points, file reads, model loads, and inference calls.
- Minimal dependencies — justify every new addition.
- Configurable behavior over hardcoded constants.
- Small, reviewable, reversible changes.

---

## Architecture rules

- `src/domain/` must not import FastAPI, PyTorch, OpenCV, or Detectron2.
- `src/application/` must not import FastAPI, PyTorch, OpenCV, or Detectron2.
- `src/infrastructure/vision/` may import torch, cv2, detectron2, and numpy.
- `app/routes/` must not contain business logic — delegate to `src/application/use_cases/`.
- `legacy/` must not be imported by any active module — it is reference only.
- Do not create circular dependencies between layers.

---

## Edge and hardware constraints

- Assume CPU execution by default. Do not assume CUDA. `DEVICE = "cpu"` everywhere.
- Do not assume the AI Camera accelerates Detectron2 inference automatically.
- Avoid long-running inference in hooks or automated processes.
- Prefer configurable runtime options over hardcoded behavior.
- Active cooling (official RPi fan) is required for sustained inference workloads.

---

## Security and information assurance

- Do not hardcode secrets, tokens, passwords, or private paths.
- Do not commit `.env` files or credentials.
- Do not expose unnecessary API endpoints.
- Validate all user inputs, file paths, and uploaded files.
- Avoid destructive file operations without explicit confirmation.
- Preserve the integrity of benchmark results — do not overwrite without control.
- Consider availability risks caused by CPU saturation, thermal throttling, or blocking operations.
- Protect path inputs against traversal attacks (e.g., `../` in user-supplied filenames).

---

## Benchmark-first optimization

- No component is replaced or refactored without a baseline benchmark justifying the change.
- Reference: `docs/decisions/ADR-003` and `docs/benchmarks/raspberry-baseline.md`.
- Every optimization proposal must state: metric target, expected impact, and comparison method.

---

## Spec-first development

- No large refactoring is applied without an approved spec in `.kiro/specs/`.
- Specs follow the format: `requirements.md` → `design.md` → `tasks.md`.
- Tasks are executed sequentially; build must pass between tasks.
- Decisions with architectural impact are documented as ADRs in `docs/decisions/`.

---

## Documentation standards

- Do not invent benchmark values, performance claims, or experimental results.
- Distinguish between informal observations ("observed") and formal measurements ("measured with X").
- ADRs document why a decision was made, not just what was decided.
- Benchmark results must include: date, device, OS, git commit, temperatures, and configuration.

---

## Testing rules

- Do not run heavy inference benchmarks automatically from hooks.
- Smoke tests live in `scripts/`.
- Benchmark scripts live in `scripts/benchmarks/`.
- Benchmark results are documented in `docs/benchmarks/`.
- When adding pure domain logic, suggest a lightweight unit test.

---

## What not to do (ever, without an explicit approved spec)

- Do not replace Detectron2.
- Do not integrate AI Camera inference.
- Do not modify model files.
- Do not add new Python dependencies without justification.
- Do not remove existing files without explaining why.
- Do not mix documentation changes with functional code changes in the same commit.
- Do not run the full pipeline benchmark automatically (it requires active cooling and manual monitoring).
