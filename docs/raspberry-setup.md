# Raspberry Pi Setup — Tomato Monitor

## Overview

This document describes the steps required to reproduce the Tomato Monitor environment on Raspberry Pi 5. It covers system dependencies, Python environment setup, Detectron2 installation, model validation, and known issues.

All steps assume Raspberry Pi OS / Debian Bookworm (64-bit) and an active internet connection.

---

## 1. Hardware requirements

- Raspberry Pi 5 (8 GB RAM recommended)
- Official Raspberry Pi 27W power supply
- Official case with active cooling fan (mandatory for inference workloads)
- MicroSD with sufficient space (≥ 32 GB recommended)
- Monitor, keyboard, and network access during initial setup

Active cooling is not optional for inference workloads. Without the fan, sustained CPU load will cause thermal throttling and non-representative benchmark results.

---

## 2. System dependencies

Install the following system packages before setting up the Python environment:

```bash
sudo apt update && sudo apt upgrade -y

sudo apt install -y \
  git \
  python3-pip \
  python3-venv \
  python3-dev \
  build-essential \
  cmake \
  pkg-config \
  htop \
  tree \
  curl \
  wget \
  libgl1 \
  libglib2.0-0 \
  libjpeg-dev \
  libpng-dev \
  libopenblas-dev
```

### Note: `libatlas-base-dev`

`libatlas-base-dev` is not available in Debian Bookworm for ARM64. It was tested and discarded. `libopenblas-dev` serves as the numeric BLAS backend and is sufficient for PyTorch and NumPy operations.

---

## 3. Python virtual environment

Create and activate a virtual environment in the project root:

```bash
cd ~/tomato_monitor
python3 -m venv .venv
source .venv/bin/activate
```

Verify Python version (3.10 or later required):

```bash
python --version
```

---

## 4. PyTorch and TorchVision

PyTorch must be installed before Detectron2. Install using the version validated for ARM64:

```bash
pip install --upgrade pip
pip install torch torchvision
```

> **Note:** PyTorch 2.x wheels for ARM64/aarch64 may not be available on PyPI for all versions. If installation fails, check the [PyTorch official install page](https://pytorch.org/get-started/locally/) for ARM-compatible options, or use a prebuilt wheel from a trusted source. Pin the version once validated and record it in `requirements-raspberry.txt`.

Verify installation:

```bash
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
```

Expected output: version string, then `False` (no CUDA on Raspberry Pi).

---

## 5. Project dependencies

Install the remaining project dependencies:

```bash
pip install -r requirements.txt
```

If `requirements-raspberry.txt` is populated with ARM64-validated versions, prefer it:

```bash
pip install -r requirements-raspberry.txt
```

> **Status:** `requirements-raspberry.txt` is currently empty pending formal version pinning after the baseline benchmark. See `docs/thesis-notes/current-state.md`.

---

## 6. Detectron2 installation

Detectron2 has no precompiled wheels for ARM64. It must be compiled from source on the device.

```bash
pip install --no-build-isolation 'git+https://github.com/facebookresearch/detectron2.git'
```

This process compiles Detectron2 directly on the Raspberry Pi and takes approximately 30–60 minutes. An active internet connection is required.

### Known issues

- The installed version corresponds to the latest commit at the time of installation. If reproducibility is critical, pin the commit hash:
  ```bash
  pip install --no-build-isolation 'git+https://github.com/facebookresearch/detectron2.git@{COMMIT_HASH}'
  ```
- The exact commit hash used in the current environment has not been recorded. This is documented as a known limitation in `docs/thesis-notes/current-state.md`.
- NumPy 2.x has breaking changes relative to 1.x. Detectron2 was originally developed against NumPy 1.x. Monitor for silent numerical inconsistencies.

---

## 7. Model files

Models are not included in the repository. Place the following files in the correct locations before running the pipeline:

| Model | Path | Architecture |
|---|---|---|
| Detector | `models/modelo_d2/model.pth` | RetinaNet R-50-FPN, 1 class |
| Health classifier | `models/health_model/model.pth` | ResNet-18 fine-tuned, 2 classes |

---

## 8. Environment validation

### 8.1 Validate imports

```bash
python -c "import torch; import torchvision; import cv2; import detectron2; print('All imports OK')"
```

### 8.2 Start FastAPI

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Access from the local network at `http://<raspberry-ip>:8000`. Verify the main page loads and the configuration form is visible.

### 8.3 Run smoke tests

Individual component smoke tests are available in `scripts/`. Run them one at a time:

```bash
python scripts/smoke_test_detector.py
python scripts/smoke_test_health.py
python scripts/smoke_test_maturity.py
python scripts/smoke_test_capture.py
```

> Do not run all smoke tests back to back without monitoring temperature. Use `vcgencmd measure_temp` or `htop` to check thermal status between tests.

### 8.4 Monitor temperature

```bash
vcgencmd measure_temp
```

Or use `htop` for continuous CPU and memory monitoring during inference:

```bash
htop
```

---

## 9. Validated state

The following components have been validated on Raspberry Pi 5 as of June 2026:

| Component | Status |
|---|---|
| FastAPI startup | ✅ Validated |
| Detectron2 import | ✅ Validated |
| RetinaNet model load (CPU) | ✅ Validated |
| ResNet-18 model load (CPU) | ✅ Validated |
| Full pipeline execution | ✅ Validated (detection + health + maturity + snapshots + annotated video) |
| RAM usage (peak) | ~2.1 GB |
| Formal FPS benchmark | ⏳ Pending — see `docs/benchmarks/raspberry-baseline.md` |
| `requirements-raspberry.txt` with pinned versions | ⏳ Pending |

---

## 10. Known issues and limitations

| Issue | Description | Status |
|---|---|---|
| `requirements-raspberry.txt` empty | No pinned ARM64-validated dependency versions | Pending — Spec 001 |
| Detectron2 commit not pinned | Reproducibility risk if repository is updated | Pending documentation |
| Model metadata missing | Training date, dataset, and validation metrics not documented | Pending — Spec 004 |
| GrabCut on very small crops | May produce empty mask; fallback to centered ellipse is active | Known, monitored |
| Models reload on each HTTP request | `PipelineService` is instantiated per request; no model singleton | Known — Spec 005 |

---

## Related Documents

- `docs/hardware.md` — full hardware inventory and validation status
- `docs/thesis-notes/current-state.md` — detailed technical state of the project
- `docs/decisions/ADR-001` — rationale for keeping Detectron2 on Raspberry Pi
- `docs/benchmarks/raspberry-baseline.md` — baseline performance results (pending)
