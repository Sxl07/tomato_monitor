# System Limitations — Tomato Monitor

**Last updated:** June 2026

This document records known technical limitations, design constraints, and unresolved issues in Tomato Monitor. It is intended to support honest academic reporting and to prevent incorrect assumptions during future development.

---

## 1. Hardware limitations

### 1.1 CPU-only inference on Raspberry Pi 5

The Raspberry Pi 5 has no GPU. All inference runs on CPU. This is a fundamental constraint that affects latency, throughput, and thermal behavior. The system was designed for this constraint and does not assume GPU availability.

**Impact:** FPS is significantly lower than on a desktop machine. Sustained inference causes thermal throttling if active cooling is not present.

**Mitigation:** Active cooling (official case with fan) is mandatory. Thermal monitoring is included in benchmark procedures.

### 1.2 Thermal throttling

Sustained CPU inference causes SoC temperature to rise. If the SoC reaches its thermal limit, the CPU frequency is reduced automatically (throttling), resulting in lower and non-representative performance measurements.

**Impact:** Benchmark results obtained without active cooling or without temperature monitoring may not be reproducible.

**Mitigation:** All benchmarks must be run with the official fan installed, and temperature must be recorded at start, peak, and end.

### 1.3 Storage speed constraints

The microSD card I/O speed is lower than NVMe or SSD. Saving annotated video, crops, and snapshots simultaneously adds I/O pressure.

**Impact:** Annotated video generation and bulk snapshot saving increase total processing time per session.

**Mitigation:** Edge execution profile disables these outputs by default (Spec 003).

---

## 2. Software limitations

### 2.1 Detectron2 installation not reproducible without commit pin

Detectron2 is installed from GitHub (`git+https://...`) without a pinned commit hash. If the repository is updated, reinstalling may produce a different version with different behavior.

**Impact:** Reduces reproducibility of the experimental environment.

**Mitigation:** Pin the commit hash in `requirements-raspberry.txt` after the baseline benchmark. Document the installed version in `docs/benchmarks/raspberry-baseline.md`.

### 2.2 `requirements-raspberry.txt` is empty

No ARM64-validated dependency versions have been formally recorded. The environment was built manually and the exact versions installed are not documented.

**Impact:** Reproducing the environment on a new device is not straightforward.

**Mitigation:** Generate and commit `requirements-raspberry.txt` using `pip freeze` after the validated environment is stable. Track this in Spec 001.

### 2.3 Model metadata not documented

The training dataset, training date, hyperparameters, and validation metrics for both models (`modelo_d2/model.pth` and `health_model/model.pth`) are not formally documented.

**Impact:** The models cannot be reproduced or independently validated from the repository.

**Mitigation:** Document model metadata as part of Spec 004 thesis documentation tasks.

### 2.4 No automated test suite

The project has no automated test suite. Only manual smoke tests exist in `scripts/`. Domain policies (`DeduplicationPolicy`, `InspectionPolicy`) are not unit-tested.

**Impact:** Regressions in business logic may go undetected. Confidence in correctness is limited to manual validation.

**Mitigation:** Spec 005 proposes adding lightweight unit tests for pure domain logic.

### 2.5 Model reload on every HTTP request

`PipelineService` is instantiated in `app/dependencies.py` on every HTTP request via `get_pipeline_service()`. Each call loads Detectron2 and ResNet-18 from disk.

**Impact:** Each execution from the web UI includes model load time. This inflates reported execution time if not accounted for in benchmarks. In practice, the benchmark scripts load models independently, so this does not affect benchmark accuracy — but it does affect user experience.

**Mitigation:** A model singleton or dependency lifetime scoping is a candidate improvement in Spec 005, pending benchmarked justification.

---

## 3. Pipeline limitations

### 3.1 Offline video processing only (current state)

The pipeline currently processes pre-recorded video files. Live camera capture from the Raspberry Pi AI Camera is not yet implemented.

**Impact:** Results are based on recorded footage, not real-time conditions.

**Mitigation:** Spec 002 implements live capture as the next milestone.

### 3.2 Scene Gate not validated under all conditions

The Scene Gate parameters (ORB threshold, HSV histogram threshold, cooldown, timeout) were tuned manually without a formal parameter sweep. Their performance across different video conditions (lighting changes, fast movement, occlusion) has not been benchmarked.

**Impact:** False positives (unnecessary detector runs) or false negatives (missed scene changes) may occur in untested conditions.

**Mitigation:** Document Scene Gate behavior during the baseline benchmark. Consider parameter sensitivity analysis as a future optimization task.

### 3.3 GrabCut on small crops may fail

GrabCut segmentation can return an empty or degenerate mask when the crop is small or lacks clear foreground/background contrast. A fallback to a centered ellipse mask is implemented but not formally tested across the full crop size distribution.

**Impact:** Maturity estimates for small or low-contrast tomatoes may be less accurate.

**Mitigation:** Monitor GrabCut failure rate during the baseline benchmark. Record error count per session.

### 3.4 Maturity estimation runs only on healthy fruits (by default)

`RUN_MATURITY_ONLY_FOR_HEALTHY = True` by default. Maturity is not estimated for fruits classified as unhealthy.

**Impact:** The USDA maturity distribution in reports reflects only healthy fruits. This is a design decision, not a defect, but it must be documented as a scope limitation.

**Mitigation:** This behavior is configurable. Document clearly in thesis results sections.

---

## 4. Academic scope limitations

### 4.1 Single video as reference benchmark

The baseline benchmark uses `data/videos/video_02.mp4` as the sole reference video. Performance results may not generalize to other video conditions, lighting environments, or tomato varieties.

**Impact:** Benchmark evidence is specific to this recording context.

**Mitigation:** Acknowledge this limitation explicitly in thesis results. Report conditions (resolution, lighting, number of frames, tomato density) alongside metrics.

### 4.2 No ground truth for detection evaluation

No annotated ground truth dataset exists for evaluating detection precision (mAP) on the benchmark video. Detection quality is assessed qualitatively through visual inspection of annotated frames.

**Impact:** mAP cannot be computed; only proxy metrics (detection count, track count, score distribution) are available from the pipeline output.

**Mitigation:** Acknowledge this limitation. If mAP evaluation is needed for the thesis, a ground truth annotation step would be required.

---

## Related Documents

- `docs/thesis-notes/current-state.md` — current technical state and identified debt
- `docs/thesis-notes/next-steps.md` — planned work and spec order
- `docs/decisions/ADR-001` — rationale for Detectron2 on Raspberry Pi
- `docs/decisions/ADR-003` — benchmark-first optimization strategy
- `docs/benchmarks/raspberry-baseline.md` — baseline results (pending)
