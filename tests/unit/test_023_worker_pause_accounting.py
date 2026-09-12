"""Spec 023 pause-accounting tests for VideoRecordingWorker.

Only MEASURES pause time; does not change pause behavior. Verifies the three
approved scenarios with a deterministic fake monotonic clock:
    A. frame -> pause -> resume -> next frame  => pause discounted.
    B. frame -> pause -> finalize (no next frame) => pause NOT discounted.
    C. pause before first frame => not counted.
And that camera_capture_elapsed_seconds >= 0 always.

The worker's cooperative pause loop checks pause_event/thermal_pause_event and
sleeps in 0.1 s steps. We drive events from the frame source's read() so the
loop enters/exits deterministically, and we replace time.monotonic with a fake
clock and time.sleep with a no-op to keep the test fast and deterministic.
"""

import numpy as np

import src.application.services.video_recording_worker as worker_mod
from src.application.services.video_recording_worker import VideoRecordingWorker


W, H = 32, 24


def _frame(value=0):
    return np.full((H, W, 3), value, dtype=np.uint8)


class _AlwaysWriteSampler:
    def should_write(self) -> bool:
        return True


class _Clock:
    """Fake monotonic clock advanced explicitly by the test/frame source."""

    def __init__(self, start=0.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt):
        self.t += dt


class _ScriptedFrameSource:
    """Runs a script of steps before each read to drive events/clock.

    ``steps`` is a list where each entry is a callable(worker, clock) executed
    just before returning the next frame (or exhaustion). This lets a test open
    a pause window (set event + advance clock) precisely between frames.
    """

    def __init__(self, frames, steps=None):
        self._frames = list(frames)
        self._steps = list(steps or [])
        self._i = 0
        self.released = 0
        self.worker = None
        self.clock = None

    def read(self):
        idx = self._i
        self._i += 1
        if idx < len(self._steps) and self._steps[idx] is not None:
            self._steps[idx](self.worker, self.clock)
        if idx < len(self._frames):
            return True, self._frames[idx]
        return False, None

    def release(self):
        self.released += 1

    def is_available(self):
        return True


def _run(frames, steps, clock, *, finalize_after=None):
    fs = _ScriptedFrameSource(frames, steps)
    worker = VideoRecordingWorker(
        monitoring_id=1,
        frame_source=fs,
        video_recorder=_Recorder(),
        monitoring_repo=object(),
        db_session=object(),
        configured_recording_fps=5.0,
        configured_camera_stream_fps=20.0,
        recording_sampler=_AlwaysWriteSampler(),
    )
    fs.worker = worker
    fs.clock = clock
    worker.run()
    return worker


class _Recorder:
    def __init__(self):
        self.writes = []

    def open(self, frame_size):
        pass

    def write(self, frame):
        self.writes.append(frame)

    def close(self):
        pass

    @property
    def frames_written(self):
        return len(self.writes)


def _patch_time(monkeypatch, clock):
    monkeypatch.setattr(worker_mod.time, "monotonic", clock)
    monkeypatch.setattr(worker_mod.time, "sleep", lambda *_a, **_k: None)


class TestPauseAccounting:
    def test_A_pause_between_frames_discounted(self, monkeypatch):
        clock = _Clock(100.0)

        # A pause window between frame1 and frame2. frame1's step SETS the pause
        # event so the NEXT loop iteration's pause-block observes it. The fake
        # sleep (used by the cooperative wait loop) advances the clock 2.0s and
        # clears the event, so the worker measures a 2.0s pause that lies inside
        # the [first_frame, last_frame] window and is discounted.
        holder = {}

        def sleep_and_resume(*_a, **_k):
            clock.advance(2.0)
            w = holder.get("worker")
            if w is not None:
                w.pause_event.clear()

        monkeypatch.setattr(worker_mod.time, "monotonic", clock)
        monkeypatch.setattr(worker_mod.time, "sleep", sleep_and_resume)

        def step_frame1(w, c):
            holder["worker"] = w
            c.t = 100.0
            w.pause_event.set()  # observed at the next loop iteration

        def step_frame2(w, c):
            c.advance(0.05)  # frame2 arrives 0.05s after the pause resumes

        worker = _run(
            [_frame(1), _frame(2)],
            [step_frame1, step_frame2, None],
            clock,
        )
        m = worker.recording_metrics
        # first=100.0; pause 2.0s; frame2 at 102.05 -> elapsed = 0.05.
        assert m.camera_frames_produced == 2
        assert abs(m.accumulated_pause_seconds - 2.0) < 1e-6
        assert abs(m.camera_capture_elapsed_seconds - 0.05) < 1e-6
        assert m.camera_capture_elapsed_seconds >= 0.0

    def test_B_pause_after_last_frame_not_discounted(self, monkeypatch):
        clock = _Clock(100.0)

        # frame1 at 100.0, frame2 at 100.05. frame2's step SETS the pause event
        # so the NEXT loop iteration ENTERS the cooperative pause loop for real.
        # The fake sleep advances the clock 5.0s, clears the pause and sets
        # finalize -> the worker exits WITHOUT another successful frame, so the
        # pending pause (after the last frame) is never consolidated.
        holder = {}

        def sleep_finalize(*_a, **_k):
            clock.advance(5.0)
            w = holder["worker"]
            w.pause_event.clear()
            w.finalize_event.set()

        monkeypatch.setattr(worker_mod.time, "monotonic", clock)
        monkeypatch.setattr(worker_mod.time, "sleep", sleep_finalize)

        def step_frame1(w, c):
            holder["worker"] = w
            c.t = 100.0

        def step_frame2(w, c):
            c.advance(0.05)
            w.pause_event.set()  # observed at the next iteration's pause block

        worker = _run(
            [_frame(1), _frame(2)],
            [step_frame1, step_frame2, None],
            clock,
        )
        m = worker.recording_metrics
        # The 5.0s pause is entered for real but no further frame arrives ->
        # pending, never consolidated. accumulated stays 0; window ends at frame2.
        assert m.camera_frames_produced == 2
        assert abs(m.accumulated_pause_seconds - 0.0) < 1e-6
        assert abs(m.camera_capture_elapsed_seconds - 0.05) < 1e-6
        assert m.camera_capture_elapsed_seconds >= 0.0

    def test_C_pause_before_first_frame_not_counted(self, monkeypatch):
        clock = _Clock(100.0)

        # The pause is active BEFORE run() (before any frame). The worker's pause
        # block does NOT record a pause start while _first_camera_frame_time is
        # None, so this pre-first-frame pause must not be counted. The fake sleep
        # advances the clock and clears the pause so the first frame can arrive.
        holder = {}

        def sleep_resume(*_a, **_k):
            clock.advance(3.0)
            holder["worker"].pause_event.clear()

        monkeypatch.setattr(worker_mod.time, "monotonic", clock)
        monkeypatch.setattr(worker_mod.time, "sleep", sleep_resume)

        def step_frame1(w, c):
            # First frame arrives AFTER the pre-first-frame pause resumed. The
            # clock is monotonic: it never goes backwards. sleep_resume already
            # advanced it to ~103.0, so the first frame baseline is ~103.0.
            pass

        def step_frame2(w, c):
            c.advance(0.05)

        fs = _ScriptedFrameSource([_frame(1), _frame(2)], [step_frame1, step_frame2, None])
        worker = VideoRecordingWorker(
            monitoring_id=1,
            frame_source=fs,
            video_recorder=_Recorder(),
            monitoring_repo=object(),
            db_session=object(),
            configured_recording_fps=5.0,
            configured_camera_stream_fps=20.0,
            recording_sampler=_AlwaysWriteSampler(),
        )
        fs.worker = worker
        fs.clock = clock
        holder["worker"] = worker
        worker.pause_event.set()  # active BEFORE the first frame / before run()
        worker.run()

        m = worker.recording_metrics
        # Pre-first-frame pause not counted; window is [100.0, 100.05].
        assert m.camera_frames_produced == 2
        assert abs(m.accumulated_pause_seconds - 0.0) < 1e-6
        assert abs(m.camera_capture_elapsed_seconds - 0.05) < 1e-6
        assert m.camera_capture_elapsed_seconds >= 0.0
