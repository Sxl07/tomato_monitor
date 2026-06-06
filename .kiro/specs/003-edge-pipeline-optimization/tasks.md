# Tasks - Edge Pipeline Optimization

## Phase 1: Baseline dependency

- [ ] Confirm that `001-raspberry-baseline-benchmark` has measurable results.
- [ ] Review baseline CPU, RAM, temperature, and runtime.
- [ ] Identify the top bottleneck from benchmark data.
- [ ] Do not implement optimizations without baseline evidence.

## Phase 2: Configuration review

- [ ] Review `src/infrastructure/config/settings.py`.
- [ ] Identify existing flags related to outputs, video, snapshots, crops, and processing resolution.
- [ ] Propose an `edge` profile and a `full` profile.
- [ ] Avoid hardcoded Raspberry-specific behavior.
- [ ] Document proposed configuration changes.

## Phase 3: Heavy output controls

- [ ] Locate annotated video generation.
- [ ] Locate snapshot generation.
- [ ] Locate crop saving.
- [ ] Add or reuse configuration flags to enable/disable each output type.
- [ ] Preserve current behavior in full mode.
- [ ] Set conservative defaults for edge mode.
- [ ] Update documentation.

## Phase 4: Resolution and frame control

- [ ] Identify where frames are resized or preprocessed.
- [ ] Add configurable processing resolution if missing.
- [ ] Add optional maximum frames per run for controlled tests.
- [ ] Add optional detector frequency control if technically safe.
- [ ] Document accuracy/performance trade-offs.

## Phase 5: Benchmark optimized variants

- [ ] Run baseline test with full outputs.
- [ ] Run test without annotated video.
- [ ] Run test without snapshots.
- [ ] Run test without crops.
- [ ] Run test at reduced resolution.
- [ ] Run test with reduced detector frequency if implemented.
- [ ] Record CPU, RAM, temperature, runtime, and quality observations.

## Phase 6: Documentation and decisions

- [ ] Update `docs/benchmarks/raspberry-baseline.md`.
- [ ] Create `docs/benchmarks/raspberry-optimized.md`.
- [ ] Create ADR for any default behavior change.
- [ ] Update `docs/architecture.md` if architecture changes.
- [ ] Update `README.md` with edge execution notes.

## Phase 7: Review and validation

- [ ] Verify desktop/full mode still works.
- [ ] Verify edge mode works on Raspberry Pi 5.
- [ ] Verify outputs are generated only when enabled.
- [ ] Verify no unexpected files are created.
- [ ] Verify configuration is documented.
- [ ] Review code quality and maintainability.

## Completion Criteria

- [ ] Edge profile exists or is clearly documented.
- [ ] Heavy outputs are configurable.
- [ ] At least one optimized benchmark is documented.
- [ ] Trade-offs are documented.
- [ ] Relevant ADRs are created.
- [ ] The system remains compatible with Raspberry Pi 5.