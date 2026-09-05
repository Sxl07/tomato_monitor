"""Tests for VideoRecorder (Spec 019, Task 3.2).

Covers cases A-N:
    A. Constructor: no writer created, counters zeroed, arg validation.
    B. open success: single writer, codec/fps/frame_size passed correctly.
    C. codec fallback: first writer released, second selected, no dangling writers.
    D. all codecs fail: all failed writers released, VideoRecorderError, none kept.
    E. write before open -> error.
    F. write success -> writer receives the frame, frames_written increments.
    G. dimension mismatch -> error, no write, counter unchanged.
    H. invalid frame (None / bad shape) -> explicit error.
    I. backend write exception -> wrapped, chained, counter unchanged.
    J. close idempotent -> release once, second close safe.
    K. backend release exception -> VideoRecorderError, reference cleared.
    L. validate valid (real small mp4 when the environment allows).
    M. validate invalid (missing / empty / corrupt / cannot open / read fails).
    N. validate always releases the temporary capture.

Fallback/error cases use fake cv2.VideoWriter/VideoCapture via monkeypatch to
isolate handle lifecycle from codec availability.
"""

from pathlib import Path

import numpy as np
import pytest

import cv2

import src.infrastructure.camera.video_recorder as recorder_mod
from src.infrastructure.camera.video_recorder import (
    VideoRecorder,
    VideoRecorderError,
)


FPS = 30.0
W = 64
H = 48


def _frame(w=W, h=H, value=0):
    return np.full((h, w, 3), value, dtype=np.uint8)


# --------------------------------------------------------------------------- #
# Fake writer/capture infrastructure
# --------------------------------------------------------------------------- #

class _FakeWriter:
    def __init__(self, *, opened=True, raise_on_write=False, raise_on_release=False,
                 raise_on_isopened=False):
        self._opened = opened
        self._raise_on_write = raise_on_write
        self._raise_on_release = raise_on_release
        self._raise_on_isopened = raise_on_isopened
        self.released = 0
        self.written = []

    def isOpened(self):
        if self._raise_on_isopened:
            raise RuntimeError("simulated isOpened failure")
        return self._opened

    def write(self, frame):
        if self._raise_on_write:
            raise RuntimeError("simulated cv2.write failure")
        self.written.append(frame)

    def release(self):
        self.released += 1
        if self._raise_on_release:
            raise RuntimeError("simulated cv2.release failure")


class _WriterFactory:
    """Creates fake writers per codec, recording construction order/args.

    ``open_map`` maps codec-string -> whether that writer reports isOpened().
    """

    def __init__(self, open_map, writer_kwargs=None):
        self._open_map = open_map
        self._writer_kwargs = writer_kwargs or {}
        self.constructed = []  # list of (path, fourcc, fps, size, writer)

    def __call__(self, path, fourcc, fps, size):
        codec = _decode_fourcc(fourcc)
        opened = self._open_map.get(codec, True)
        writer = _FakeWriter(opened=opened, **self._writer_kwargs)
        self.constructed.append(
            {"path": path, "fourcc": fourcc, "codec": codec, "fps": fps,
             "size": size, "writer": writer}
        )
        return writer


# We encode the codec string into the fourcc int so the factory can recover it.
def _fake_fourcc(*codec_chars):
    codec = "".join(codec_chars)
    return _CODEC_TO_INT.setdefault(codec, len(_CODEC_TO_INT) + 1)


_CODEC_TO_INT: dict[str, int] = {}


def _decode_fourcc(fourcc_int):
    for codec, val in _CODEC_TO_INT.items():
        if val == fourcc_int:
            return codec
    return "?"


@pytest.fixture
def patch_fourcc(monkeypatch):
    monkeypatch.setattr(recorder_mod.cv2, "VideoWriter_fourcc", _fake_fourcc)


# --------------------------------------------------------------------------- #
# A. Constructor
# --------------------------------------------------------------------------- #

class TestConstructor:
    def test_no_writer_created_and_counters_zero(self, tmp_path):
        rec = VideoRecorder("out.mp4", fps=FPS, allowed_base=tmp_path)
        assert rec.frames_written == 0
        assert rec.codec_used == ""
        assert rec._writer is None

    def test_fps_zero_rejected(self, tmp_path):
        with pytest.raises(VideoRecorderError):
            VideoRecorder("out.mp4", fps=0.0, allowed_base=tmp_path)

    def test_fps_negative_rejected(self, tmp_path):
        with pytest.raises(VideoRecorderError):
            VideoRecorder("out.mp4", fps=-1.0, allowed_base=tmp_path)

    def test_empty_codec_list_rejected(self, tmp_path):
        with pytest.raises(VideoRecorderError):
            VideoRecorder("out.mp4", fps=FPS, codec_candidates=(), allowed_base=tmp_path)


# --------------------------------------------------------------------------- #
# B. open success
# --------------------------------------------------------------------------- #

class TestOpenSuccess:
    def test_open_selects_first_codec(self, tmp_path, monkeypatch, patch_fourcc):
        factory = _WriterFactory(open_map={"mp4v": True, "avc1": True})
        monkeypatch.setattr(recorder_mod.cv2, "VideoWriter", factory)

        out = tmp_path / "sub" / "out.mp4"
        rec = VideoRecorder("sub/out.mp4", fps=FPS, codec_candidates=("mp4v", "avc1"),
                            allowed_base=tmp_path)
        rec.open(frame_size=(W, H))

        # Exactly one writer constructed and active.
        assert len(factory.constructed) == 1
        entry = factory.constructed[0]
        assert entry["codec"] == "mp4v"
        assert entry["fps"] == FPS
        assert entry["size"] == (W, H)
        assert rec.codec_used == "mp4v"
        assert rec._writer is entry["writer"]
        assert entry["writer"].released == 0
        # Parent directory created.
        assert out.parent.exists()

    def test_open_rejects_nonpositive_dims(self, tmp_path, monkeypatch, patch_fourcc):
        factory = _WriterFactory(open_map={"mp4v": True})
        monkeypatch.setattr(recorder_mod.cv2, "VideoWriter", factory)
        rec = VideoRecorder("out.mp4", fps=FPS, allowed_base=tmp_path)
        with pytest.raises(VideoRecorderError):
            rec.open(frame_size=(0, H))
        with pytest.raises(VideoRecorderError):
            rec.open(frame_size=(W, 0))
        assert len(factory.constructed) == 0


# --------------------------------------------------------------------------- #
# C. codec fallback
# --------------------------------------------------------------------------- #

class TestCodecFallback:
    def test_fallback_to_second_codec(self, tmp_path, monkeypatch, patch_fourcc):
        factory = _WriterFactory(open_map={"mp4v": False, "avc1": True})
        monkeypatch.setattr(recorder_mod.cv2, "VideoWriter", factory)

        rec = VideoRecorder("out.mp4", fps=FPS,
                            codec_candidates=("mp4v", "avc1"), allowed_base=tmp_path)
        rec.open(frame_size=(W, H))

        assert len(factory.constructed) == 2
        first = factory.constructed[0]["writer"]
        second = factory.constructed[1]["writer"]
        # First (failed) writer released; second retained.
        assert first.released == 1
        assert second.released == 0
        assert rec.codec_used == "avc1"
        assert rec._writer is second


# --------------------------------------------------------------------------- #
# Hardened fallback: isOpened() / release() backend exceptions
# --------------------------------------------------------------------------- #

class TestFallbackHardening:
    def test_isopened_exception_aborts_open(self, tmp_path, monkeypatch, patch_fourcc):
        # First codec's writer raises in isOpened(); open() must abort (not try
        # the next codec), attempt cleanup, and raise a wrapped error.
        factory = _WriterFactory(
            open_map={"mp4v": True, "avc1": True},
            writer_kwargs={"raise_on_isopened": True},
        )
        monkeypatch.setattr(recorder_mod.cv2, "VideoWriter", factory)

        rec = VideoRecorder("out.mp4", fps=FPS,
                            codec_candidates=("mp4v", "avc1"), allowed_base=tmp_path)
        with pytest.raises(VideoRecorderError) as exc_info:
            rec.open(frame_size=(W, H))

        # Backend cause preserved, not surfaced raw.
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        # No writer retained.
        assert rec._writer is None
        assert rec.codec_used == ""
        # Only the FIRST codec was constructed (no second attempt).
        assert len(factory.constructed) == 1
        assert factory.constructed[0]["codec"] == "mp4v"
        # Best-effort cleanup was attempted on the problematic writer.
        assert factory.constructed[0]["writer"].released == 1

    def test_fallback_release_exception_aborts_open(self, tmp_path, monkeypatch, patch_fourcc):
        # First codec does NOT open and its release() raises. open() must abort
        # without constructing the second codec's writer.
        factory = _WriterFactory(
            open_map={"mp4v": False, "avc1": True},
            writer_kwargs={"raise_on_release": True},
        )
        monkeypatch.setattr(recorder_mod.cv2, "VideoWriter", factory)

        rec = VideoRecorder("out.mp4", fps=FPS,
                            codec_candidates=("mp4v", "avc1"), allowed_base=tmp_path)
        with pytest.raises(VideoRecorderError) as exc_info:
            rec.open(frame_size=(W, H))

        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert rec._writer is None
        assert rec.codec_used == ""
        # Only the FIRST writer was constructed; the second codec was NOT tried,
        # because cleanup of the first was uncertain (single-writer guarantee).
        assert len(factory.constructed) == 1
        assert factory.constructed[0]["codec"] == "mp4v"
        assert factory.constructed[0]["writer"].released == 1


# --------------------------------------------------------------------------- #
# D. all codecs fail
# --------------------------------------------------------------------------- #

class TestAllCodecsFail:
    def test_all_fail_raises_and_keeps_no_writer(self, tmp_path, monkeypatch, patch_fourcc):
        factory = _WriterFactory(open_map={"mp4v": False, "avc1": False})
        monkeypatch.setattr(recorder_mod.cv2, "VideoWriter", factory)

        rec = VideoRecorder("out.mp4", fps=FPS,
                            codec_candidates=("mp4v", "avc1"), allowed_base=tmp_path)
        with pytest.raises(VideoRecorderError):
            rec.open(frame_size=(W, H))

        # All failed writers released; none retained.
        assert all(c["writer"].released == 1 for c in factory.constructed)
        assert rec._writer is None
        assert rec.codec_used == ""


# --------------------------------------------------------------------------- #
# E. write before open
# --------------------------------------------------------------------------- #

class TestWriteBeforeOpen:
    def test_write_before_open_raises(self, tmp_path):
        rec = VideoRecorder("out.mp4", fps=FPS, allowed_base=tmp_path)
        with pytest.raises(VideoRecorderError):
            rec.write(_frame())


# --------------------------------------------------------------------------- #
# F. write success
# --------------------------------------------------------------------------- #

class TestWriteSuccess:
    def test_write_passes_frame_and_increments(self, tmp_path, monkeypatch, patch_fourcc):
        factory = _WriterFactory(open_map={"mp4v": True})
        monkeypatch.setattr(recorder_mod.cv2, "VideoWriter", factory)
        rec = VideoRecorder("out.mp4", fps=FPS, codec_candidates=("mp4v",), allowed_base=tmp_path)
        rec.open(frame_size=(W, H))

        f0 = _frame(value=1)
        f1 = _frame(value=2)
        rec.write(f0)
        rec.write(f1)

        writer = factory.constructed[0]["writer"]
        assert writer.written[0] is f0
        assert writer.written[1] is f1
        assert rec.frames_written == 2


# --------------------------------------------------------------------------- #
# G. dimension mismatch
# --------------------------------------------------------------------------- #

class TestDimensionMismatch:
    def test_mismatch_raises_no_write_no_count(self, tmp_path, monkeypatch, patch_fourcc):
        factory = _WriterFactory(open_map={"mp4v": True})
        monkeypatch.setattr(recorder_mod.cv2, "VideoWriter", factory)
        rec = VideoRecorder("out.mp4", fps=FPS, codec_candidates=("mp4v",), allowed_base=tmp_path)
        rec.open(frame_size=(W, H))  # (640,480)-style: width=W, height=H

        wrong = _frame(w=W, h=H - 12)  # different height
        with pytest.raises(VideoRecorderError):
            rec.write(wrong)

        writer = factory.constructed[0]["writer"]
        assert writer.written == []  # no write happened
        assert rec.frames_written == 0

    def test_mismatch_width_raises(self, tmp_path, monkeypatch, patch_fourcc):
        factory = _WriterFactory(open_map={"mp4v": True})
        monkeypatch.setattr(recorder_mod.cv2, "VideoWriter", factory)
        rec = VideoRecorder("out.mp4", fps=FPS, codec_candidates=("mp4v",), allowed_base=tmp_path)
        rec.open(frame_size=(W, H))
        with pytest.raises(VideoRecorderError):
            rec.write(_frame(w=W + 8, h=H))
        assert rec.frames_written == 0


# --------------------------------------------------------------------------- #
# H. invalid frame
# --------------------------------------------------------------------------- #

class TestInvalidFrame:
    def test_none_frame_raises(self, tmp_path, monkeypatch, patch_fourcc):
        factory = _WriterFactory(open_map={"mp4v": True})
        monkeypatch.setattr(recorder_mod.cv2, "VideoWriter", factory)
        rec = VideoRecorder("out.mp4", fps=FPS, codec_candidates=("mp4v",), allowed_base=tmp_path)
        rec.open(frame_size=(W, H))
        with pytest.raises(VideoRecorderError):
            rec.write(None)
        assert rec.frames_written == 0

    def test_bad_shape_raises(self, tmp_path, monkeypatch, patch_fourcc):
        factory = _WriterFactory(open_map={"mp4v": True})
        monkeypatch.setattr(recorder_mod.cv2, "VideoWriter", factory)
        rec = VideoRecorder("out.mp4", fps=FPS, codec_candidates=("mp4v",), allowed_base=tmp_path)
        rec.open(frame_size=(W, H))
        bad = np.zeros((H,), dtype=np.uint8)  # 1-D, no width
        with pytest.raises(VideoRecorderError):
            rec.write(bad)
        assert rec.frames_written == 0


# --------------------------------------------------------------------------- #
# I. backend write exception
# --------------------------------------------------------------------------- #

class TestBackendWriteException:
    def test_backend_write_exception_wrapped(self, tmp_path, monkeypatch, patch_fourcc):
        factory = _WriterFactory(open_map={"mp4v": True},
                                 writer_kwargs={"raise_on_write": True})
        monkeypatch.setattr(recorder_mod.cv2, "VideoWriter", factory)
        rec = VideoRecorder("out.mp4", fps=FPS, codec_candidates=("mp4v",), allowed_base=tmp_path)
        rec.open(frame_size=(W, H))
        with pytest.raises(VideoRecorderError) as exc_info:
            rec.write(_frame())
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert rec.frames_written == 0  # not counted


# --------------------------------------------------------------------------- #
# J. close idempotent
# --------------------------------------------------------------------------- #

class TestCloseIdempotent:
    def test_close_releases_once_and_is_safe(self, tmp_path, monkeypatch, patch_fourcc):
        factory = _WriterFactory(open_map={"mp4v": True})
        monkeypatch.setattr(recorder_mod.cv2, "VideoWriter", factory)
        rec = VideoRecorder("out.mp4", fps=FPS, codec_candidates=("mp4v",), allowed_base=tmp_path)
        rec.open(frame_size=(W, H))
        writer = factory.constructed[0]["writer"]

        rec.close()
        rec.close()  # must not raise
        assert writer.released == 1
        assert rec._writer is None

    def test_close_without_open_is_safe(self, tmp_path):
        rec = VideoRecorder("out.mp4", fps=FPS, allowed_base=tmp_path)
        rec.close()  # no-op, no raise


# --------------------------------------------------------------------------- #
# K. backend release exception
# --------------------------------------------------------------------------- #

class TestBackendReleaseException:
    def test_release_exception_wrapped_reference_cleared(
        self, tmp_path, monkeypatch, patch_fourcc
    ):
        factory = _WriterFactory(open_map={"mp4v": True},
                                 writer_kwargs={"raise_on_release": True})
        monkeypatch.setattr(recorder_mod.cv2, "VideoWriter", factory)
        rec = VideoRecorder("out.mp4", fps=FPS, codec_candidates=("mp4v",), allowed_base=tmp_path)
        rec.open(frame_size=(W, H))

        with pytest.raises(VideoRecorderError) as exc_info:
            rec.close()
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        # Reference cleared despite the failure; a second close is safe.
        assert rec._writer is None
        rec.close()


# --------------------------------------------------------------------------- #
# L. validate valid (real small mp4)
# --------------------------------------------------------------------------- #

class TestValidateValid:
    def test_validate_true_for_real_video(self, tmp_path):
        out = tmp_path / "real.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(out), fourcc, FPS, (W, H))
        if not writer.isOpened():
            writer.release()
            pytest.skip("mp4v VideoWriter unavailable in this environment")
        for i in range(5):
            writer.write(_frame(value=i * 10))
        writer.release()
        if not out.exists() or out.stat().st_size == 0:
            pytest.skip("real video could not be written")

        rec = VideoRecorder("real.mp4", fps=FPS, allowed_base=tmp_path)
        assert rec.validate() is True


# --------------------------------------------------------------------------- #
# M. validate invalid
# --------------------------------------------------------------------------- #

class TestValidateInvalid:
    def test_missing_file(self, tmp_path):
        rec = VideoRecorder("missing.mp4", fps=FPS, allowed_base=tmp_path)
        assert rec.validate() is False

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.mp4"
        p.write_bytes(b"")
        rec = VideoRecorder(p.name, fps=FPS, allowed_base=tmp_path)
        assert rec.validate() is False

    def test_corrupt_file(self, tmp_path):
        p = tmp_path / "corrupt.mp4"
        p.write_bytes(b"not-a-real-video-payload")
        rec = VideoRecorder(p.name, fps=FPS, allowed_base=tmp_path)
        assert rec.validate() is False

    def test_capture_cannot_open(self, tmp_path, monkeypatch):
        p = tmp_path / "present.mp4"
        p.write_bytes(b"\x00\x01\x02")  # exists + size > 0

        class _Cap:
            def __init__(self, path):
                self.released = 0

            def isOpened(self):
                return False

            def read(self):  # pragma: no cover - not reached
                return False, None

            def release(self):
                self.released += 1

        monkeypatch.setattr(recorder_mod.cv2, "VideoCapture", _Cap)
        rec = VideoRecorder(p.name, fps=FPS, allowed_base=tmp_path)
        assert rec.validate() is False

    def test_capture_opens_but_read_fails(self, tmp_path, monkeypatch):
        p = tmp_path / "present.mp4"
        p.write_bytes(b"\x00\x01\x02")

        class _Cap:
            def __init__(self, path):
                self.released = 0

            def isOpened(self):
                return True

            def read(self):
                return False, None  # opens but no frame

            def release(self):
                self.released += 1

        monkeypatch.setattr(recorder_mod.cv2, "VideoCapture", _Cap)
        rec = VideoRecorder(p.name, fps=FPS, allowed_base=tmp_path)
        assert rec.validate() is False

    def test_backend_read_raises_returns_false(self, tmp_path, monkeypatch):
        p = tmp_path / "present.mp4"
        p.write_bytes(b"\x00\x01\x02")

        class _Cap:
            def __init__(self, path):
                self.released = 0

            def isOpened(self):
                return True

            def read(self):
                raise RuntimeError("simulated cv2.read failure")

            def release(self):
                self.released += 1

        monkeypatch.setattr(recorder_mod.cv2, "VideoCapture", _Cap)
        rec = VideoRecorder(p.name, fps=FPS, allowed_base=tmp_path)
        assert rec.validate() is False  # never leaks the raw error


# --------------------------------------------------------------------------- #
# N. validate releases the temporary capture
# --------------------------------------------------------------------------- #

class TestValidateReleasesCapture:
    def _make_cap_class(self, *, opened=True, read_raises=False, read_ok=True):
        instances = []

        class _Cap:
            def __init__(self, path):
                self.released = 0
                instances.append(self)

            def isOpened(self):
                return opened

            def read(self):
                if read_raises:
                    raise RuntimeError("boom")
                if read_ok:
                    return True, _frame()
                return False, None

            def release(self):
                self.released += 1

        return _Cap, instances

    def test_capture_released_on_read_failure(self, tmp_path, monkeypatch):
        p = tmp_path / "present.mp4"
        p.write_bytes(b"\x00\x01\x02")
        cap_cls, instances = self._make_cap_class(read_ok=False)
        monkeypatch.setattr(recorder_mod.cv2, "VideoCapture", cap_cls)
        rec = VideoRecorder(p.name, fps=FPS, allowed_base=tmp_path)
        rec.validate()
        assert len(instances) == 1
        assert instances[0].released == 1

    def test_capture_released_on_backend_exception(self, tmp_path, monkeypatch):
        p = tmp_path / "present.mp4"
        p.write_bytes(b"\x00\x01\x02")
        cap_cls, instances = self._make_cap_class(read_raises=True)
        monkeypatch.setattr(recorder_mod.cv2, "VideoCapture", cap_cls)
        rec = VideoRecorder(p.name, fps=FPS, allowed_base=tmp_path)
        assert rec.validate() is False
        assert len(instances) == 1
        assert instances[0].released == 1

    def test_capture_released_on_success(self, tmp_path, monkeypatch):
        p = tmp_path / "present.mp4"
        p.write_bytes(b"\x00\x01\x02")
        cap_cls, instances = self._make_cap_class(read_ok=True)
        monkeypatch.setattr(recorder_mod.cv2, "VideoCapture", cap_cls)
        rec = VideoRecorder(p.name, fps=FPS, allowed_base=tmp_path)
        assert rec.validate() is True
        assert instances[0].released == 1
