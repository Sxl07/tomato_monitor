"""Tests for VideoRecordingWorker (Spec 019, Task 4.3).

Covers cases A-P using fakes only (no camera, no Picamera2, no cv2, no Detectron2):
    A. First frame opens recorder with (w, h) and is also written; 3 frames -> 3 writes.
    B. All N valid frames written in order, none dropped.
    C. No throttle: normal recording path performs zero sleeps.
    D. Cooperative pause: waits, responds to finalize/abort, cleanup preserved.
    E. finalize_event -> exit_reason "finalize" + close + release.
    F. abort_event -> exit_reason "abort" + cleanup.
    G. (False, None) -> exit_reason "frame_source_exhausted" + cleanup.
    H. read() raises -> exit_reason "error" + cleanup.
    I. recorder.open raises -> exit_reason "error" + frame source released.
    J. recorder.write raises -> exit_reason "error" + release; only real writes counted.
    K. recorder.close raises -> frame_source.release() still runs.
    L. release_resources idempotent.
    M. get_last_frame returns a copy (mutation isolation) + None before first frame.
    N. Metrics with deterministic clock.
    O. No inference/Scene Gate/cv2 references (static boundary).
    P. Importability without cv2/torch/detectron2/picamera2.
"""

from pathlib import Path

import numpy as np
import pytest

import src.application.services.video_recording_worker as worker_mod
from src.application.services.video_recording_worker import (
    RecordingMetrics,
    VideoRecordingWorker,
)


W = 64
H = 48


def _frame(w=W, h=H, value=0):
    return np.full((h, w, 3), value, dtype=np.uint8)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class _FakeFrameSource:
    """Yields a fixed list of (success, frame) results, then (False, None)."""

    def __init__(self, frames=None, read_raises_at=None):
        # frames: list of numpy frames to return as (True, frame).
        self._frames = list(frames or [])
        self._i = 0
        self._read_raises_at = read_raises_at  # index at which read() raises
        self.released = 0

    def read(self):
        idx = self._i
        self._i += 1
        if self._read_raises_at is not None and idx == self._read_raises_at:
            raise RuntimeError("simulated read failure")
        if idx < len(self._frames):
            return True, self._frames[idx]
        return False, None

    def release(self):
        self.released += 1

    def is_available(self):
        return True


class _FakeRecorder:
    """Records open/write/close calls; configurable failures."""

    def __init__(self, *, open_raises=False, write_raises_at=None,
                 close_raises=False, codec_used="mp4v"):
        self.opened_with = None
        self.writes = []
        self.closed = 0
        self._frames_written = 0
        self._open_raises = open_raises
        self._write_raises_at = write_raises_at
        self._close_raises = close_raises
        self.codec_used = codec_used

    def open(self, frame_size):
        if self._open_raises:
            raise RuntimeError("simulated open failure")
        self.opened_with = frame_size

    def write(self, frame):
        idx = len(self.writes)
        if self._write_raises_at is not None and idx == self._write_raises_at:
            raise RuntimeError("simulated write failure")
        self.writes.append(frame)
        self._frames_written += 1

    def close(self):
        self.closed += 1
        if self._close_raises:
            raise RuntimeError("simulated close failure")

    @property
    def frames_written(self):
        return self._frames_written


class _FakeThermalMonitor:
    def __init__(self, peak=0.0):
        self.started = 0
        self.stopped = 0
        self.peak_temperature = peak

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


class _AlwaysWriteSampler:
    """Sampler that accepts every frame, preserving the Spec 019 'write every
    frame' contracts these historical tests were written against."""

    def should_write(self) -> bool:
        return True


def _make_worker(frame_source, recorder, *, fps=10.0, thermal=None, sampler=None):
    return VideoRecordingWorker(
        monitoring_id=1,
        frame_source=frame_source,
        video_recorder=recorder,
        monitoring_repo=object(),
        db_session=object(),
        configured_recording_fps=fps,
        configured_camera_stream_fps=20.0,
        recording_sampler=sampler if sampler is not None else _AlwaysWriteSampler(),
        thermal_monitor=thermal,
    )


# --------------------------------------------------------------------------- #
# A. First frame
# --------------------------------------------------------------------------- #

class TestFirstFrame:
    def test_open_once_with_dims_and_first_frame_written(self):
        frames = [_frame(value=1), _frame(value=2), _frame(value=3)]
        fs = _FakeFrameSource(frames)
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec)
        worker.run()

        assert rec.opened_with == (W, H)  # (width, height)
        assert len(rec.writes) == 3
        # First frame was written (not skipped).
        assert rec.writes[0] is frames[0]


# --------------------------------------------------------------------------- #
# B. All frames written in order
# --------------------------------------------------------------------------- #

class TestAllFrames:
    def test_all_frames_written_in_order(self):
        frames = [_frame(value=i) for i in range(7)]
        fs = _FakeFrameSource(frames)
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec)
        worker.run()

        assert len(rec.writes) == 7
        for written, original in zip(rec.writes, frames):
            assert written is original


# --------------------------------------------------------------------------- #
# C. No throttle
# --------------------------------------------------------------------------- #

class TestNoThrottle:
    def test_no_sleep_in_normal_recording(self, monkeypatch):
        frames = [_frame(value=i) for i in range(5)]
        fs = _FakeFrameSource(frames)
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec, fps=10.0)

        sleeps = []
        monkeypatch.setattr(worker_mod.time, "sleep", lambda *a, **k: sleeps.append(a))
        worker.run()
        assert sleeps == []  # zero throttle sleeps in the normal path


# --------------------------------------------------------------------------- #
# D. Cooperative pause
# --------------------------------------------------------------------------- #

class TestCooperativePause:
    def test_pause_then_finalize(self, monkeypatch):
        frames = [_frame(value=i) for i in range(3)]
        fs = _FakeFrameSource(frames)
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec)

        worker.pause_event.set()

        # When the worker sleeps in the pause loop, finalize to break out.
        def fake_sleep(_secs):
            worker.finalize_event.set()

        monkeypatch.setattr(worker_mod.time, "sleep", fake_sleep)
        worker.run()

        # It paused (slept) then finalized without recording anything.
        assert worker.recording_metrics.exit_reason == "finalize"
        assert rec.closed == 1
        assert fs.released == 1

    def test_thermal_pause_then_abort(self, monkeypatch):
        fs = _FakeFrameSource([_frame()])
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec)
        worker.thermal_pause_event.set()

        def fake_sleep(_secs):
            worker.abort_event.set()

        monkeypatch.setattr(worker_mod.time, "sleep", fake_sleep)
        worker.run()
        assert worker.recording_metrics.exit_reason == "abort"
        assert fs.released == 1


# --------------------------------------------------------------------------- #
# E. Finalize
# --------------------------------------------------------------------------- #

class TestFinalize:
    def test_finalize_before_frames(self):
        fs = _FakeFrameSource([_frame() for _ in range(3)])
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec)
        worker.finalize_event.set()  # set before run
        worker.run()
        assert worker.recording_metrics.exit_reason == "finalize"
        assert rec.closed == 1
        assert fs.released == 1


# --------------------------------------------------------------------------- #
# complete_event backward-compatible alias
# --------------------------------------------------------------------------- #

class TestCompleteEventAlias:
    def test_complete_event_is_finalize_event(self):
        fs = _FakeFrameSource([_frame()])
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec)
        assert worker.complete_event is worker.finalize_event

    def test_setting_complete_sets_finalize(self):
        fs = _FakeFrameSource([_frame()])
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec)
        worker.complete_event.set()
        assert worker.finalize_event.is_set()

    def test_setting_finalize_sets_complete(self):
        fs = _FakeFrameSource([_frame()])
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec)
        worker.finalize_event.set()
        assert worker.complete_event.is_set()


# --------------------------------------------------------------------------- #
# F. Abort
# --------------------------------------------------------------------------- #

class TestAbort:
    def test_abort_before_frames(self):
        fs = _FakeFrameSource([_frame() for _ in range(3)])
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec)
        worker.abort_event.set()
        worker.run()
        assert worker.recording_metrics.exit_reason == "abort"
        assert rec.closed == 1
        assert fs.released == 1


# --------------------------------------------------------------------------- #
# G. Frame source exhausted
# --------------------------------------------------------------------------- #

class TestFrameSourceExhausted:
    def test_exhausted_returns_false_none(self):
        fs = _FakeFrameSource([])  # immediately (False, None)
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec)
        worker.run()
        assert worker.recording_metrics.exit_reason == "frame_source_exhausted"
        assert fs.released == 1
        # No frames -> recorder never opened, close still called (idempotent).
        assert rec.closed == 1


# --------------------------------------------------------------------------- #
# H. FrameSource exception
# --------------------------------------------------------------------------- #

class TestFrameSourceException:
    def test_read_raises_is_error(self):
        fs = _FakeFrameSource([_frame(), _frame()], read_raises_at=0)
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec)
        worker.run()
        assert worker.recording_metrics.exit_reason == "error"
        assert worker.error_reason is not None
        assert fs.released == 1


# --------------------------------------------------------------------------- #
# I. recorder.open exception
# --------------------------------------------------------------------------- #

class TestOpenException:
    def test_open_raises_is_error_and_releases_source(self):
        fs = _FakeFrameSource([_frame()])
        rec = _FakeRecorder(open_raises=True)
        worker = _make_worker(fs, rec)
        worker.run()
        assert worker.recording_metrics.exit_reason == "error"
        assert fs.released == 1
        assert rec.writes == []  # nothing written


# --------------------------------------------------------------------------- #
# J. recorder.write exception
# --------------------------------------------------------------------------- #

class TestWriteException:
    def test_write_raises_is_error_counts_only_real_writes(self):
        frames = [_frame(value=0), _frame(value=1), _frame(value=2)]
        fs = _FakeFrameSource(frames)
        rec = _FakeRecorder(write_raises_at=1)  # first write ok, second raises
        worker = _make_worker(fs, rec)
        worker.run()
        assert worker.recording_metrics.exit_reason == "error"
        assert fs.released == 1
        # Only the first write succeeded and was counted.
        assert rec.frames_written == 1
        assert worker.recording_metrics.frames_written == 1


# --------------------------------------------------------------------------- #
# K. recorder.close exception must not block camera release
# --------------------------------------------------------------------------- #

class TestCloseException:
    def test_close_raises_still_releases_source(self):
        fs = _FakeFrameSource([_frame() for _ in range(2)])
        rec = _FakeRecorder(close_raises=True)
        worker = _make_worker(fs, rec)
        worker.run()
        # close() raised internally, but the camera was still released.
        assert rec.closed == 1
        assert fs.released == 1


# --------------------------------------------------------------------------- #
# L. release_resources idempotent
# --------------------------------------------------------------------------- #

class TestReleaseIdempotent:
    def test_double_release(self):
        fs = _FakeFrameSource([_frame()])
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec)
        worker.release_resources()
        worker.release_resources()  # must not raise, must not double-run
        assert rec.closed == 1
        assert fs.released == 1

    def test_release_before_run_is_safe(self):
        fs = _FakeFrameSource([_frame()])
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec)
        worker.release_resources()
        assert rec.closed == 1
        assert fs.released == 1


# --------------------------------------------------------------------------- #
# M. get_last_frame copy semantics
# --------------------------------------------------------------------------- #

class TestGetLastFrame:
    def test_none_before_first_frame(self):
        fs = _FakeFrameSource([])
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec)
        assert worker.get_last_frame() is None

    def test_returns_copy_not_internal_reference(self):
        fs = _FakeFrameSource([_frame(value=5)])
        rec = _FakeRecorder()
        worker = _make_worker(fs, rec)
        worker.run()

        returned = worker.get_last_frame()
        assert returned is not None
        returned[...] = 200  # mutate the returned copy

        again = worker.get_last_frame()
        assert int(again.max()) == 5  # internal buffer unaffected by the mutation


# --------------------------------------------------------------------------- #
# N. Metrics
# --------------------------------------------------------------------------- #

class TestMetrics:
    def test_metrics_with_deterministic_clock(self, monkeypatch):
        # 20 frames written; duration forced to 4.0s -> effective 5 fps.
        frames = [_frame(value=i % 5) for i in range(20)]
        fs = _FakeFrameSource(frames)
        rec = _FakeRecorder(codec_used="mp4v")
        thermal = _FakeThermalMonitor(peak=61.5)
        worker = _make_worker(fs, rec, fps=10.0, thermal=thermal)

        # Deterministic monotonic: start=100.0, end=104.0.
        times = iter([100.0] + [104.0] * 50)
        monkeypatch.setattr(worker_mod.time, "monotonic", lambda: next(times))
        worker.run()

        m = worker.recording_metrics
        assert isinstance(m, RecordingMetrics)
        assert m.configured_recording_fps == 10.0
        assert m.container_fps == 10.0
        assert m.frames_written == 20
        assert m.recording_duration_seconds == 4.0
        assert m.effective_recording_fps == 5.0
        assert m.deviation_between_configured_and_effective_fps == 5.0
        assert m.recorded_width == W
        assert m.recorded_height == H
        assert m.codec_used == "mp4v"
        assert m.peak_temperature_c == 61.5
        assert m.exit_reason == "frame_source_exhausted"


# --------------------------------------------------------------------------- #
# O. No inference / Scene Gate / cv2 (static boundary)
# --------------------------------------------------------------------------- #

class TestNoInferenceBoundary:
    """Static checks that the worker does not IMPORT or CALL inference/cv2.

    The forbidden terms may legitimately appear in the module docstring (which
    documents what the worker deliberately does NOT do). To avoid false
    positives, the checks parse the AST and inspect import names and referenced
    identifiers/attributes — not raw docstring prose.
    """

    @pytest.fixture(scope="class")
    def source(self):
        path = (
            Path(__file__).resolve().parents[2]
            / "src" / "application" / "services" / "video_recording_worker.py"
        )
        return path.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    def code_identifiers(self, source):
        """Return the set of import names + referenced Name/Attribute identifiers."""
        import ast

        tree = ast.parse(source)
        names: set[str] = set()
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    modules.add(node.module)
                for alias in node.names:
                    names.add(alias.name)
            elif isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
        return {"names": names, "modules": modules}

    @pytest.mark.parametrize("forbidden", [
        "process_frame",
        "build_pipeline_components",
        "should_capture_new_image",
        "should_run_detector_by_scene_change",
        "SnapshotAnalysisService",
    ])
    def test_no_inference_symbol_referenced(self, code_identifiers, forbidden):
        assert forbidden not in code_identifiers["names"], (
            f"worker must not reference {forbidden} in code"
        )

    @pytest.mark.parametrize("forbidden_module", [
        "detectron2",
        "cv2",
        "torch",
        "picamera2",
    ])
    def test_no_forbidden_module_imported(self, code_identifiers, forbidden_module):
        for mod in code_identifiers["modules"]:
            assert not (mod == forbidden_module or mod.startswith(forbidden_module + ".")), (
                f"worker must not import {forbidden_module} (found {mod})"
            )

    def test_no_cv2_import_line(self, source):
        for line in source.splitlines():
            s = line.strip()
            if s.startswith("import ") or s.startswith("from "):
                assert "import cv2" not in s and "from cv2" not in s


# --------------------------------------------------------------------------- #
# P. Importability without heavy backends
# --------------------------------------------------------------------------- #

class TestImportability:
    def test_module_has_no_heavy_backends_loaded(self):
        mod = worker_mod
        assert getattr(mod, "cv2", None) is None
        assert getattr(mod, "torch", None) is None
        # VideoRecorder is only a TYPE_CHECKING import; not present at runtime.
        assert getattr(mod, "VideoRecorder", None) is None

    def test_importable_symbols(self):
        assert hasattr(worker_mod, "VideoRecordingWorker")
        assert hasattr(worker_mod, "RecordingMetrics")
