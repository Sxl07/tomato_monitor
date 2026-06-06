# Tasks - Thesis and Testing Documentation

## Phase 1: Documentation structure

- [x] Create `docs/` directory.
- [x] Create `docs/project-overview.md`.
- [x] Create `docs/hardware.md`.
- [x] Create `docs/raspberry-setup.md`.
- [x] Create `docs/architecture.md`.
- [x] Create `docs/benchmarks/`.
- [x] Create `docs/decisions/`.
- [x] Create `docs/thesis-notes/`.

## Phase 2: Project overview

- [x] Document the general objective of Tomato Monitor.
- [x] Document the current scope.
- [x] Document current application status.
- [x] Document current Raspberry status.
- [x] Document major project constraints.
- [x] Document current next steps.

## Phase 3: Hardware documentation

- [x] List Raspberry Pi 5.
- [x] List official 27W power supply.
- [x] List official case with fan.
- [x] List Raspberry Pi AI Camera.
- [x] List DSI 7-inch touchscreen.
- [x] List microSD.
- [x] Document validated hardware status.
- [x] Document active cooling as mandatory for heavy tests.

## Phase 4: Raspberry setup documentation

- [x] Document system package installation.
- [x] Document Python virtual environment setup.
- [x] Document dependency installation order.
- [x] Document `libatlas-base-dev` issue and replacement with `libopenblas-dev`.
- [x] Document PyTorch and torchvision requirement before Detectron2.
- [x] Document Detectron2 installation with `--no-build-isolation`.
- [x] Document API startup validation.
- [x] Document model inference validation.

## Phase 5: Architecture documentation

- [x] Document `app/` responsibility.
- [x] Document `src/application/` responsibility.
- [x] Document `src/domain/` responsibility.
- [x] Document `src/infrastructure/` responsibility.
- [x] Document vision pipeline components.
- [x] Document local persistence approach.
- [x] Document current offline video orientation.
- [x] Document future live camera direction.

## Phase 6: Benchmark documentation

- [x] Create `docs/benchmarks/benchmark-template.md`.
- [x] Create `docs/benchmarks/raspberry-baseline.md` (structure ready, results pending).
- [x] Include fields for CPU, RAM, temperature, FPS, load time, inference time, and observations.
- [ ] Fill `docs/benchmarks/raspberry-baseline.md` with actual measured results (blocked on Spec 001 execution).
- [ ] Add conclusions and next actions based on real benchmark data.

## Phase 7: ADRs

- [x] Create ADR-001 for using Detectron2 initially on Raspberry for baseline.
- [x] Create ADR-002 for AI Camera strategy.
- [x] Create ADR-003 for benchmark-before-refactor approach.
- [x] Link ADRs to relevant specs.
- [ ] Update ADR-001 with quantitative benchmark evidence after Spec 001 is executed.
- [ ] Update ADR-003 thresholds with formally measured values after baseline benchmark.

## Phase 8: Testing documentation

- [x] Document how to validate imports (in `docs/raspberry-setup.md`).
- [x] Document how to start FastAPI (in `docs/raspberry-setup.md`).
- [x] Document how to run smoke tests (in `docs/raspberry-setup.md`).
- [x] Document temperature monitoring procedure (in `docs/raspberry-setup.md`).
- [ ] Document how to run benchmark scripts once created (blocked on Spec 001).
- [ ] Document how to record and interpret benchmark results.
- [ ] Document what not to run automatically on Raspberry (formalize current informal rules).

## Phase 9: Limitations documentation

- [x] Create `docs/thesis-notes/limitations.md`.
- [x] Document hardware limitations (CPU-only, thermal throttling, SD card I/O).
- [x] Document software limitations (Detectron2 reproducibility, missing requirements pin, no test suite).
- [x] Document pipeline limitations (offline only, Scene Gate not formally validated, GrabCut edge cases).
- [x] Document academic scope limitations (single reference video, no ground truth for mAP).
- [ ] Update limitations after baseline benchmark reveals additional findings.

## Phase 10: Review and cleanup

- [x] Check that documentation does not include secrets.
- [x] Check that documentation does not invent results.
- [ ] Check that paths are portable where possible.
- [ ] Check that README links to key docs.
- [ ] Check that Kiro steering files do not duplicate long documentation.
- [ ] Commit documentation separately from code changes.

## Completion Criteria

- [x] Documentation structure exists.
- [x] Raspberry setup can be reproduced from docs.
- [x] Current project state is documented.
- [x] Benchmark template exists.
- [x] Initial ADRs exist (ADR-001, ADR-002, ADR-003).
- [x] Architecture is formally documented.
- [x] Limitations are documented.
- [ ] Benchmark results are filled with real data (depends on Spec 001).
- [ ] ADRs updated with measured evidence (depends on Spec 001).
- [ ] Testing documentation complete (depends on Spec 001 benchmark scripts).
