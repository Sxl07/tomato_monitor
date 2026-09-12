"""Tests for RecordingSampler (Spec 023, Task 3).

Pure, deterministic tests using an injectable fake clock. No time.sleep, no
randomness, no hardware. Verify the phase-preserving, no-burst temporal sampling
policy that keeps recorded video at ~recording_fps while the camera runs faster.
"""

import pytest

from src.application.services.recording_sampler import RecordingSampler


class _FakeClock:
    """Returns a preset sequence of monotonic timestamps, one per call."""

    def __init__(self, times):
        self._times = list(times)
        self._i = 0

    def __call__(self) -> float:
        # Each should_write() calls the clock exactly once; return the next
        # scheduled timestamp. Repeat the last one defensively if overrun.
        if self._i < len(self._times):
            t = self._times[self._i]
            self._i += 1
            return t
        return self._times[-1]


def _count_writes(recording_fps, times):
    """Feed each timestamp through the sampler; return how many were written."""
    clock = _FakeClock(times)
    sampler = RecordingSampler(recording_fps, clock=clock)
    return sum(1 for _ in times if sampler.should_write())


# --------------------------------------------------------------------------- #
# Case 1 — invalid fps
# --------------------------------------------------------------------------- #

class TestInvalidFps:
    def test_zero_raises(self):
        with pytest.raises(ValueError):
            RecordingSampler(0)

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            RecordingSampler(-1)


# --------------------------------------------------------------------------- #
# Case 2 — first frame is eligible
# --------------------------------------------------------------------------- #

class TestFirstFrame:
    def test_first_call_writes(self):
        sampler = RecordingSampler(5.0, clock=_FakeClock([100.0]))
        assert sampler.should_write() is True


# --------------------------------------------------------------------------- #
# Case 3 — EDGE: 20 FPS input, 5 FPS recording
# --------------------------------------------------------------------------- #

class TestEdge20to5:
    def test_five_writes_per_second(self):
        # 1 second of 20 FPS input = 20 frames at t = 0.00, 0.05, ..., 0.95.
        times = [round(i * 0.05, 4) for i in range(20)]
        writes = _count_writes(5.0, times)
        # First frame eligible + slots at ~0.2/0.4/0.6/0.8 -> 5 writes in 1 s.
        assert writes == 5


# --------------------------------------------------------------------------- #
# Case 4 — FULL: 20 FPS input, 10 FPS recording
# --------------------------------------------------------------------------- #

class TestFull20to10:
    def test_ten_writes_per_second(self):
        times = [round(i * 0.05, 4) for i in range(20)]
        writes = _count_writes(10.0, times)
        # First + every other frame -> 10 writes in 1 s.
        assert writes == 10


# --------------------------------------------------------------------------- #
# Case 5 — before the next slot
# --------------------------------------------------------------------------- #

class TestBeforeNextSlot:
    def test_sequence_5fps(self):
        # interval = 0.2; grid slots at 0.2, 0.4, ...
        times = [0.00, 0.05, 0.10, 0.15, 0.20]
        clock = _FakeClock(times)
        sampler = RecordingSampler(5.0, clock=clock)
        results = [sampler.should_write() for _ in times]
        assert results == [True, False, False, False, True]


# --------------------------------------------------------------------------- #
# Case 6 — large gap without burst
# --------------------------------------------------------------------------- #

class TestLargeGapNoBurst:
    def test_gap_produces_single_write_then_no_catchup(self):
        # target 5 FPS. t=0.0 writes; big gap to t=0.9 writes ONCE; the very next
        # call right after must NOT fire repeatedly to recover missed slots.
        times = [0.0, 0.9, 0.91, 0.92]
        clock = _FakeClock(times)
        sampler = RecordingSampler(5.0, clock=clock)
        results = [sampler.should_write() for _ in times]
        # 0.0 -> True; 0.9 -> True (one write only); 0.91/0.92 -> False (no burst).
        assert results == [True, True, False, False]

    def test_gap_writes_at_most_one_per_call(self):
        # Even a very long jump triggers exactly one write on that call.
        times = [0.0, 100.0]
        clock = _FakeClock(times)
        sampler = RecordingSampler(5.0, clock=clock)
        assert [sampler.should_write() for _ in times] == [True, True]


# --------------------------------------------------------------------------- #
# Case 7 — phase preservation (observable via results, not private attrs)
# --------------------------------------------------------------------------- #

class TestPhasePreservation:
    def test_jittered_accept_does_not_shift_grid(self):
        # target 5 FPS, grid slots at 0.2, 0.4, 0.6, ...
        # A slot is accepted a bit LATE (jitter) at 0.23. If next_due were
        # anchored to now (0.23 + 0.2 = 0.43), then 0.41 would be rejected.
        # Phase-preserving anchors to the grid (next_due = 0.4), so 0.41 writes.
        times = [0.00, 0.23, 0.41]
        clock = _FakeClock(times)
        sampler = RecordingSampler(5.0, clock=clock)
        results = [sampler.should_write() for _ in times]
        # 0.00 -> True; 0.23 -> True (>= 0.2 slot); 0.41 -> True (>= 0.4 grid slot).
        assert results == [True, True, True]

    def test_now_plus_interval_would_reject_but_grid_accepts(self):
        # Contrast test making the difference explicit: with anchor-to-now the
        # third frame would be rejected; with grid anchoring it is accepted.
        times = [0.00, 0.23, 0.41]
        sampler = RecordingSampler(5.0, clock=_FakeClock(times))
        assert sampler.should_write() is True   # 0.00
        assert sampler.should_write() is True   # 0.23 (slot 0.2)
        assert sampler.should_write() is True   # 0.41 (grid slot 0.4)


# --------------------------------------------------------------------------- #
# Case 6b (long simulation) — no cumulative drift over 30-60 s with jitter
# --------------------------------------------------------------------------- #

class TestLongSimulationNoDrift:
    def _jittered_times(self, duration_s, base_fps=20.0):
        """Deterministic ~20 FPS stream with a repeatable jitter pattern.

        The nominal interval is 1/base_fps (0.05 s). We perturb each step with a
        fixed, repeating offset sequence (no randomness) so the mean rate stays
        ~20 FPS but individual intervals vary (e.g. ~19.2 / 20.4 / 18.9 FPS).
        """
        # Repeating multiplicative jitter on the 0.05 s step; mean ~= 1.0.
        jitter = [0.96, 1.02, 1.06, 0.94, 1.00, 1.04, 0.98, 1.00]
        base = 1.0 / base_fps
        times = []
        t = 0.0
        i = 0
        while t <= duration_s:
            times.append(round(t, 6))
            t += base * jitter[i % len(jitter)]
            i += 1
        return times

    @pytest.mark.parametrize("duration_s", [30.0, 60.0])
    def test_writes_track_target_without_drift(self, duration_s):
        times = self._jittered_times(duration_s, base_fps=20.0)
        recording_fps = 5.0
        writes = _count_writes(recording_fps, times)

        expected = duration_s * recording_fps  # 150 for 30 s, 300 for 60 s
        # Tolerance: a small constant band, NOT proportional to duration. If the
        # error were cumulative (as with next_due = now + interval), the 60 s run
        # would drift much further than the 30 s run. A fixed +/- band that both
        # durations satisfy demonstrates the error does not grow with time.
        assert abs(writes - expected) <= 3, (
            f"writes={writes} expected≈{expected} for {duration_s}s "
            f"(drift band exceeded)"
        )

    def test_error_does_not_grow_with_duration(self):
        recording_fps = 5.0
        err_30 = abs(
            _count_writes(recording_fps, self._jittered_times(30.0)) - 30.0 * 5.0
        )
        err_60 = abs(
            _count_writes(recording_fps, self._jittered_times(60.0)) - 60.0 * 5.0
        )
        # Cumulative drift would make err_60 roughly double err_30. Phase-
        # preserving keeps both within the same small band.
        assert err_60 <= err_30 + 2
