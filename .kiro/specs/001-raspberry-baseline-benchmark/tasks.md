# Tasks - Raspberry Baseline Benchmark

## Phase 1: Preparation

- [x] Verify that `scripts/benchmarks/` directory does not yet exist and create it.
- [x] Verify that `docs/benchmarks/benchmark-template.md` exists and review its fields.
- [x] Verify that `data/videos/video_02.mp4` is accessible on Raspberry Pi 5.
- [x] Confirm Raspberry Pi 5 has active cooling installed and fan is running.
- [x] Confirm all models are in place: `models/modelo_d2/model.pth` and `models/health_model/model.pth`.
- [x] Record current Python version, torch version, and OpenCV version on RPi.

## Phase 2: Model load benchmark

- [x] Create `scripts/benchmarks/bench_model_load.py`.
- [x] Script must: time Detectron2 model load from cold start.
- [x] Script must: time ResNet-18 health model load from cold start.
- [x] Script must: print results to stdout in a structured format.
- [x] Script must: not run inference — load only.
- [x] Script must: record start temperature before load and end temperature after load.
- [x] Run script on Raspberry Pi 5 and record results.

## Phase 3: Single inference benchmark

- [x] Create `scripts/benchmarks/bench_single_inference.py`.
- [x] Script must: load both models once at startup (not reload per frame).
- [x] Script must: run detector on a single frame from `data/videos/video_02.mp4`.
- [x] Script must: measure and report preprocessing time, detector inference time, postprocessing time, and total time.
- [x] Script must: optionally run health classifier on one crop and report its inference time.
- [x] Script must: not save snapshots, video, or crops during the benchmark.
- [x] Run script on Raspberry Pi 5 and record results.

## Phase 4: Full pipeline benchmark

- [x] Create `scripts/benchmarks/bench_full_video.py`.
- [x] Script must: process `data/videos/video_02.mp4` using the existing pipeline (via `VideoInspectionRunner` or equivalent).
- [x] Script must: measure total runtime, frames processed, average FPS, and effective FPS.
- [x] Script must: record peak RAM usage (using `psutil` or equivalent).
- [x] Script must: record initial temperature, peak temperature, and final temperature.
- [x] Script must: count detector runs vs. optical flow frames (Scene Gate effectiveness).
- [x] Script must: count unique tracks detected.
- [x] Script must: allow disabling annotated video and snapshots via parameter to isolate pure pipeline cost.
- [x] Script must: print a structured summary at the end.
- [x] Script must: not modify pipeline logic — benchmark only.
- [x] Run script on Raspberry Pi 5 with active cooling and record results.

## Phase 5: System monitor utility

- [x] Create `scripts/benchmarks/system_monitor.py`.
- [x] Script must: provide a helper to read CPU usage, RAM usage, and temperature on demand.
- [x] Script must: be importable by other benchmark scripts (not a standalone only).
- [x] Use standard library (`subprocess` for `vcgencmd`) or `psutil` if already available.
- [x] Do not add new dependencies unless `psutil` is already in `requirements.txt`.

## Phase 6: Results documentation

- [x] Fill `docs/benchmarks/raspberry-baseline.md` using `docs/benchmarks/benchmark-template.md`.
- [x] Record: test date, device, OS, Python version, git commit, and branch.
- [x] Record: torch version, torchvision version, OpenCV version, Detectron2 version/commit.
- [x] Record: all metrics from Phase 2, Phase 3, and Phase 4.
- [x] Record: temperature at start, peak, and end for each benchmark.
- [x] Record: errors or anomalies observed.
- [x] Write brief observations and conclusions based on measured data only.
- [x] Do not invent or estimate values — only record measured results.

## Phase 7: Requirements file

- [x] Run `pip freeze` on the validated Raspberry Pi environment.
- [x] Update `requirements-raspberry.txt` with the exact versions from `pip freeze` output.
- [x] Note the Detectron2 commit hash if determinable.
- [x] Commit `requirements-raspberry.txt` to version control.

## Phase 8: Decision review

- [x] Review `docs/decisions/ADR-001` — update Evidence section with quantitative benchmark data.
- [x] Review `docs/decisions/ADR-003` — confirm or update the optimization thresholds table with formally measured baseline values.
- [x] If results indicate that Detectron2 is clearly not viable for real-time use, propose an ADR for next steps (do not implement yet).

## Completion Criteria

- [x] `scripts/benchmarks/` directory exists with at least three scripts.
- [x] Model load times are measured and recorded.
- [x] Single inference time is measured and recorded.
- [x] Full pipeline FPS is measured and recorded.
- [x] RAM peak is measured and recorded.
- [x] Temperature behavior is measured and recorded.
- [x] `docs/benchmarks/raspberry-baseline.md` is filled with real measured values.
- [x] `requirements-raspberry.txt` has pinned versions.
- [x] ADR-001 and ADR-003 are updated with measured evidence.
- [x] No pipeline logic was modified during this spec.
