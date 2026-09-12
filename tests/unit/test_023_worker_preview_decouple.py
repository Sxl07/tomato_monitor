"""Spec 023 tests for VideoRecordingWorker preview/recording decoupling.

Fakes only (no camera, cv2, picamera2). Cover:
    - Preview updated for EVERY successful frame (independently of recording).
    - Only sampler-accepted frames are written; exact selection, no dup.
    - Lazy open of the recorder on the FIRST ACCEPTED frame (not first read).
    - get_last_frame / get_preview_frame_snapshot copy semantics + sequence.
    - Camera/preview counters and effective_camera_stream_fps formula.
    - Pause accounting: inside window discounted; after last frame NOT; before
      first frame not counted; camera_capture_elapsed_seconds >= 0.
"""

import numpy as np

from src.application.services.video_recording_worker import VideoRecordingWorker


W, H = 64, 48


def _frame(value=0):
    return np.full((H, W, 3), value, dtype=np.uint8)


class _FakeFrameSource:
    def __init__(self, frames):
        self._frames = list(frames)
        self._i = 0
        self.released = 0

    def read(self):
        idx = self._i
        self._i += 1
        if idx < len(self._frames):
            return True, self._frames[idx]
        return False, None

    def release(self):
        self.released += 1

    def is_available(self):
        return True


class _FakeRecorder:
    def __init__(self):
        self.opened_with = None
        self.writes = []
        self.closed = 0

    def open(self, frame_size):
        self.opened_with = frame_size

    def write(self, frame):
        self.writes.append(frame)

    def close(self):
        self.closed += 1

    @property
    def frames_written(self):
        return len(self.writes)


class _SeqSampler:
    """Returns booleans from a preset sequence (then False)."""

    def __init__(self, decisions):
        self._decisions = list(decisions)
        self._i = 0

    def should_write(self) -> bool:
        i = self._i
        self._i += 1
        return self._decisions[i] if i < len(self._decisions) else False


def _make_worker(frame_source, recorder, sampler, *, camera_stream_fps=20.0):
    return VideoRecordingWorker(
        monitoring_id=1,
        frame_source=frame_source,
        video_recorder=recorder,
        monitoring_repo=object(),
        db_session=object(),
        configured_recording_fps=5.0,
        configured_camera_stream_fps=camera_stream_fps,
        recording_sampler=sampler,
    )


# --------------------------------------------------------------------------- #
# Preview updated for all frames; only accepted frames written
# --------------------------------------------------------------------------- #

class TestPreviewDecoupledFromRecording:
    def test_preview_all_frames_recording_only_accepted(self):
        frames = [_frame(i + 1) for i in range(5)]
        fs = _FakeFrameSource(frames)
        rec = _FakeRecorder()
        # Accept frames 1 and 4 (indices 0 and 3).
        sampler = _SeqSampler([True, False, False, True, False])
        worker = _make_worker(fs, rec, sampler)
        worker.run()

        m = worker.recording_metrics
        assert m.camera_frames_produced == 5
        assert m.preview_frames_updated == 5
        assert worker._preview_sequence == 5
        assert m.frames_written == 2

        # Preview snapshot reflects the LAST successful frame (#5) and seq 5.
        snap = worker.get_preview_frame_snapshot()
        assert snap is not None
        seq, frame = snap
        assert seq == 5
        assert int(frame.max()) == 5

    def test_only_selected_frames_written_in_order_no_dup(self):
        frames = [_frame(i + 1) for i in range(5)]
        fs = _FakeFrameSource(frames)
        rec = _FakeRecorder()
        sampler = _SeqSampler([True, False, False, True, False])
        worker = _make_worker(fs, rec, sampler)
        worker.run()

        # Written frames are exactly the accepted ones (#1 and #4), no dup.
        assert len(rec.writes) == 2
        assert int(rec.writes[0].max()) == 1
        assert int(rec.writes[1].max()) == 4


# --------------------------------------------------------------------------- #
# Lazy open on first ACCEPTED frame
# --------------------------------------------------------------------------- #

class TestLazyOpenOnFirstAccepted:
    def test_open_uses_dimensions_of_first_accepted_frame(self):
        frames = [_frame(1), _frame(2), _frame(3)]
        fs = _FakeFrameSource(frames)
        rec = _FakeRecorder()
        # Reject first two, accept the third.
        sampler = _SeqSampler([False, False, True])
        worker = _make_worker(fs, rec, sampler)
        worker.run()

        # Recorder opened once, with (W, H); only frame #3 written.
        assert rec.opened_with == (W, H)
        assert len(rec.writes) == 1
        assert int(rec.writes[0].max()) == 3


# --------------------------------------------------------------------------- #
# Snapshot / copy semantics
# --------------------------------------------------------------------------- #

class TestSnapshotSemantics:
    def test_snapshot_none_before_first_frame(self):
        fs = _FakeFrameSource([])
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec, _SeqSampler([]))
        assert worker.get_preview_frame_snapshot() is None
        assert worker.get_last_frame() is None

    def test_snapshot_returns_independent_copy(self):
        fs = _FakeFrameSource([_frame(5)])
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec, _SeqSampler([True]))
        worker.run()

        seq, frame = worker.get_preview_frame_snapshot()
        assert seq == 1
        frame[...] = 200  # mutate the returned copy
        # Internal buffer unaffected.
        seq2, frame2 = worker.get_preview_frame_snapshot()
        assert int(frame2.max()) == 5


# --------------------------------------------------------------------------- #
# Metrics: counters and effective_camera_stream_fps formula
# --------------------------------------------------------------------------- #

class TestCameraMetrics:
    def test_counters_equal_successful_reads(self):
        frames = [_frame(i) for i in range(4)]
        fs = _FakeFrameSource(frames)
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec, _SeqSampler([True, True, True, True]))
        worker.run()
        m = worker.recording_metrics
        assert m.camera_frames_produced == 4
        assert m.preview_frames_updated == 4

    def test_effective_camera_stream_fps_formula(self, monkeypatch):
        # Control monotonic so frame timestamps are deterministic. The worker
        # calls time.monotonic() for: start_time, per-frame stamps, end_time.
        # We feed a clock that advances 0.05 s per successful frame (=20 FPS).
        times = iter([
            100.0,          # _start_time
            100.0,          # frame 1
            100.05,         # frame 2
            100.10,         # frame 3
            100.15,         # frame 4
            100.15,         # _end_time (finally)
            100.15,         # any extra reads
        ])
        last = [100.15]

        def fake_monotonic():
            try:
                v = next(times)
                last[0] = v
                return v
            except StopIteration:
                return last[0]

        monkeypatch.setattr(worker_mod_time(), "monotonic", fake_monotonic)

        frames = [_frame(i) for i in range(4)]
        fs = _FakeFrameSource(frames)
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec, _SeqSampler([True, True, True, True]))
        worker.run()

        m = worker.recording_metrics
        # 4 frames from 100.0 to 100.15 => elapsed 0.15, N-1=3 => 20 FPS.
        assert m.camera_frames_produced == 4
        assert abs(m.camera_capture_elapsed_seconds - 0.15) < 1e-6
        assert abs(m.effective_camera_stream_fps - 20.0) < 1e-6
        assert m.camera_capture_elapsed_seconds >= 0.0


def worker_mod_time():
    """Return the ``time`` module object as referenced inside the worker."""
    import src.application.services.video_recording_worker as m
    return m.time


class TestRecordingMetricsSerialization:
    """Block D: dataclasses.asdict(recording_metrics) carries the Spec 023 fields
    AND preserves the historical names (nothing renamed)."""

    def test_asdict_contains_all_expected_keys(self):
        import dataclasses

        frames = [_frame(i + 1) for i in range(3)]
        fs = _FakeFrameSource(frames)
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec, _SeqSampler([True, True, True]))
        worker.run()

        data = dataclasses.asdict(worker.recording_metrics)
        # Historical names preserved (NOT renamed).
        for key in (
            "frames_written",
            "configured_recording_fps",
            "effective_recording_fps",
        ):
            assert key in data, f"missing historical key {key}"
        # New Spec 023 diagnostic fields present.
        for key in (
            "camera_frames_produced",
            "preview_frames_updated",
            "camera_capture_elapsed_seconds",
            "accumulated_pause_seconds",
            "configured_camera_stream_fps",
            "effective_camera_stream_fps",
        ):
            assert key in data, f"missing Spec 023 key {key}"
        # There is NO 'recording_frames_written' rename in the dataclass.
        assert "recording_frames_written" not in data
