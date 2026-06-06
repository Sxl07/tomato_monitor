# Raspberry Pi Edge Steering

## Hardware target

- **Platform:** Raspberry Pi 5 (ARM64/aarch64)
- **RAM:** 8 GB (peak observed during full pipeline: ~2.1 GB)
- **Compute:** CPU only — `DEVICE = "cpu"`, `USE_CUDA = False`
- **Camera:** Raspberry Pi AI Camera (IMX500 + NPU) — live capture integration pending (Spec 002)
- **Cooling:** Official case with active fan — **mandatory for all inference workloads**
- **OS:** Raspberry Pi OS / Debian Bookworm 64-bit

---

## Validated state on RPi 5

| Component | Status |
|---|---|
| FastAPI startup | ✅ Validated |
| Detectron2 (RetinaNet) load and inference | ✅ Validated |
| ResNet-18 health classifier load and inference | ✅ Validated |
| Full pipeline (detection + health + maturity + snapshots + annotated video) | ✅ Validated |
| RAM peak (full pipeline) | ~2.1 GB — not the bottleneck |
| **Primary bottleneck** | **CPU / temperature** |
| Formal FPS measurement | ⏳ Pending — Spec 001 |

---

## Dependency installation notes (critical)

- `libatlas-base-dev` is not available on Bookworm ARM64; use `libopenblas-dev` instead.
- `opencv-python` may need to be replaced with `opencv-python-headless` in headless mode.
- Detectron2 has no ARM64 wheels; install from source:
  ```bash
  pip install --no-build-isolation 'git+https://github.com/facebookresearch/detectron2.git'
  ```
- `requirements-raspberry.txt` is pending completion with formally pinned versions (Spec 001, Phase 7).

---

## Performance rules

- **Measure before optimizing** — no component is replaced without a baseline benchmark (ADR-003).
- No annotated video by default in edge mode — it increases CPU and I/O load significantly.
- No mass snapshot or crop saving by default in edge mode.
- Keep heavy output generation configurable; do not hardcode it off.
- Prefer short video tests before long unattended runs.
- Record temperature at start, peak, and end for every benchmark.
- Separate test categories: model load, single inference, full pipeline.
- Active cooling is mandatory for runs longer than approximately 2 minutes of inference.

---

## Rules for Kiro

- Every proposed optimization must state its target metric and expected impact.
- Do not replace Detectron2 without comparative benchmark evidence.
- Do not integrate the AI Camera as an inference accelerator without explicit validation.
- Do not assume that reducing one bottleneck will eliminate thermal issues.
- If an edge-specific option is added, it must be configurable — not hardcoded.
- Every optimization change must be benchmarked before and after.

---

## Reference metrics (pending formal measurement)

| Metric | Informal observation | Formal source |
|---|---|---|
| RAM — full pipeline | ~2.1 GB | `docs/benchmarks/raspberry-baseline.md` |
| FPS — full pipeline | not yet measured | `docs/benchmarks/raspberry-baseline.md` |
| Peak temperature | elevated | `docs/benchmarks/raspberry-baseline.md` |
| Peak CPU usage | high sustained | `docs/benchmarks/raspberry-baseline.md` |
| Detector load time | not yet measured | `docs/benchmarks/raspberry-baseline.md` |
| Single inference time | not yet measured | `docs/benchmarks/raspberry-baseline.md` |

---

## Related documents

- `docs/hardware.md` — full hardware inventory
- `docs/raspberry-setup.md` — installation and validation steps
- `docs/decisions/ADR-001` — rationale for keeping Detectron2 on RPi
- `docs/decisions/ADR-003` — benchmark-first optimization strategy
- `docs/benchmarks/raspberry-baseline.md` — baseline results (pending)
- `.kiro/specs/001-raspberry-baseline-benchmark/` — benchmark spec
- `.kiro/specs/003-edge-pipeline-optimization/` — optimization spec (depends on Spec 001)
