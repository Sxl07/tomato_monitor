"""Tests for VideoReaderPort / OpenCvVideoReader (Spec 019, Task 2.3).

Covers the mandatory cases A-H:
    A. Lifecycle open -> available -> metadata/read -> release, and double release.
    B. Single VideoCapture: exactly one cv2.VideoCapture constructed per cycle.
    C. is_available() does not reopen after release().
    D. metadata() does not consume frames.
    E. metadata() reports correct fps/total_frames/width/height.
    F. EOF -> read() == (False, None) without exception.
    G. missing/corrupt file -> no usable capture, is_available False, no raw
       OpenCV exception, release() safe.
    H. invalid metadata (fps <= 0, width/height <= 0) -> explicit policy.

Real-cv2 cases use a tiny synthetic video written with cv2.VideoWriter. Cases B/C
and H use a fake cv2.VideoCapture via monkeypatch to isolate handle-construction
counting and metadata policy without depending on codec behavior.
"""

from pathlib import Path

import numpy as np
import pytest

import cv2

import src.infrastructure.camera.opencv_video_reader as reader_mod
from src.infrastructure.camera.opencv_video_reader import OpenCvVideoReader
from src.application.interfaces.video_reader_port import (
    VideoMetadata,
    VideoReaderError,
)


VIDEO_W = 32
VIDEO_H = 24
VIDEO_FPS = 30.0
VIDEO_FRAMES = 5


@pytest.fixture
def synthetic_video(tmp_path):
    """Write a tiny real .mp4 and return its absolute path (skip if codec fails)."""
    path = tmp_path / "synthetic.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, VIDEO_FPS, (VIDEO_W, VIDEO_H))
    if not writer.isOpened():
        writer.release()
        pytest.skip("mp4v VideoWriter unavailable in this environment")
    for i in range(VIDEO_FRAMES):
        frame = np.full((VIDEO_H, VIDEO_W, 3), i * 20, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    if not path.exists() or path.stat().st_size == 0:
        pytest.skip("synthetic video could not be written")
    return path


# --------------------------------------------------------------------------- #
# Fake cv2.VideoCapture infrastructure (for construction counting + metadata)
# --------------------------------------------------------------------------- #

class _FakeCapture:
    def __init__(self, *, opened=True, fps=VIDEO_FPS, width=VIDEO_W,
                 height=VIDEO_H, total=VIDEO_FRAMES,
                 raise_on_read=False, raise_on_get_after_open=False):
        self._opened = opened
        self._fps = fps
        self._width = width
        self._height = height
        self._total = total
        self._pos = 0
        self.released = 0
        self._raise_on_read = raise_on_read
        self._raise_on_get_after_open = raise_on_get_after_open
        # When simulating a get() failure, allow open()'s metadata validation to
        # pass first, then start raising on subsequent get() calls.
        self._get_calls = 0

    def isOpened(self):
        return self._opened

    def get(self, prop):
        self._get_calls += 1
        if self._raise_on_get_after_open and self._get_calls > 4:
            # First 4 get() calls (fps/count/w/h during open validation) succeed;
            # a later metadata() call triggers the simulated backend failure.
            raise RuntimeError("simulated cv2.get failure")
        if prop == cv2.CAP_PROP_FPS:
            return float(self._fps)
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return float(self._total)
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._width)
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._height)
        return 0.0

    def read(self):
        if self._raise_on_read:
            raise RuntimeError("simulated cv2.read failure")
        if self._pos >= self._total:
            return False, None
        frame = np.full((self._height, self._width, 3), self._pos, dtype=np.uint8)
        self._pos += 1
        return True, frame

    def release(self):
        self.released += 1
        self._opened = False


class _CaptureFactory:
    """Records how many cv2.VideoCapture instances get constructed."""

    def __init__(self, capture_kwargs=None):
        self.count = 0
        self.instances = []
        self._capture_kwargs = capture_kwargs or {}

    def __call__(self, path):
        self.count += 1
        cap = _FakeCapture(**self._capture_kwargs)
        self.instances.append(cap)
        return cap


@pytest.fixture
def fake_capture(monkeypatch):
    """Patch cv2.VideoCapture with a counting factory; return the factory."""
    factory = _CaptureFactory()
    monkeypatch.setattr(reader_mod.cv2, "VideoCapture", factory)
    return factory


# --------------------------------------------------------------------------- #
# A. Lifecycle
# --------------------------------------------------------------------------- #

class TestLifecycle:
    def test_open_available_metadata_read_release(self, synthetic_video):
        reader = OpenCvVideoReader(synthetic_video.name, allowed_base=synthetic_video.parent)
        assert reader.is_available() is False  # before open

        reader.open()
        assert reader.is_available() is True

        meta = reader.metadata()
        assert isinstance(meta, VideoMetadata)

        ok, frame = reader.read()
        assert ok is True
        assert frame is not None

        reader.release()
        assert reader.is_available() is False

    def test_double_release_is_safe(self, synthetic_video):
        reader = OpenCvVideoReader(synthetic_video.name, allowed_base=synthetic_video.parent)
        reader.open()
        reader.release()
        reader.release()  # must not raise
        assert reader.is_available() is False


# --------------------------------------------------------------------------- #
# B. Single VideoCapture
# --------------------------------------------------------------------------- #

class TestSingleVideoCapture:
    def test_exactly_one_capture_across_cycle(self, fake_capture):
        reader = OpenCvVideoReader("outputs/monitorings/1/video/monitoring.mp4")
        reader.open()
        reader.is_available()
        reader.metadata()
        reader.metadata()
        reader.read()
        reader.read()
        reader.is_available()
        reader.release()

        assert fake_capture.count == 1


# --------------------------------------------------------------------------- #
# C. is_available does not reopen
# --------------------------------------------------------------------------- #

class TestIsAvailableNoReopen:
    def test_is_available_false_after_release_and_no_new_capture(self, fake_capture):
        reader = OpenCvVideoReader("outputs/monitorings/1/video/monitoring.mp4")
        reader.open()
        assert reader.is_available() is True
        reader.release()

        assert reader.is_available() is False
        assert reader.is_available() is False  # repeated checks
        assert fake_capture.count == 1  # never reopened


# --------------------------------------------------------------------------- #
# D. metadata does not consume frames
# --------------------------------------------------------------------------- #

class TestMetadataDoesNotConsume:
    def test_metadata_before_read_keeps_frame_zero(self, synthetic_video):
        reader = OpenCvVideoReader(synthetic_video.name, allowed_base=synthetic_video.parent)
        reader.open()
        # Query metadata multiple times, then read.
        reader.metadata()
        reader.metadata()
        ok, first = reader.read()
        assert ok is True
        # The first read must still be frame 0. Our synthetic frames are filled
        # with value = i*20, so frame 0 is all zeros.
        assert first is not None
        assert int(first.max()) == 0
        reader.release()


# --------------------------------------------------------------------------- #
# E. metadata correct
# --------------------------------------------------------------------------- #

class TestMetadataCorrect:
    def test_metadata_values(self, synthetic_video):
        reader = OpenCvVideoReader(synthetic_video.name, allowed_base=synthetic_video.parent)
        reader.open()
        meta = reader.metadata()
        assert meta.width == VIDEO_W
        assert meta.height == VIDEO_H
        assert meta.fps == pytest.approx(VIDEO_FPS, rel=0.05)
        # total_frames may vary slightly by container; allow a small tolerance.
        assert meta.total_frames == pytest.approx(VIDEO_FRAMES, abs=1)
        reader.release()

    def test_metadata_via_fake_exact(self, fake_capture):
        reader = OpenCvVideoReader("outputs/v/monitoring.mp4")
        reader.open()
        meta = reader.metadata()
        assert meta == VideoMetadata(
            fps=VIDEO_FPS, total_frames=VIDEO_FRAMES, width=VIDEO_W, height=VIDEO_H
        )
        reader.release()

    def test_metadata_before_open_raises_explicit_error(self, synthetic_video):
        reader = OpenCvVideoReader(synthetic_video.name, allowed_base=synthetic_video.parent)
        with pytest.raises(VideoReaderError):
            reader.metadata()


# --------------------------------------------------------------------------- #
# F. EOF
# --------------------------------------------------------------------------- #

class TestEof:
    def test_read_past_end_returns_false_none(self, synthetic_video):
        reader = OpenCvVideoReader(synthetic_video.name, allowed_base=synthetic_video.parent)
        reader.open()
        # Drain all frames.
        count = 0
        while True:
            ok, frame = reader.read()
            if not ok:
                assert frame is None
                break
            count += 1
            assert count <= VIDEO_FRAMES + 2  # guard against infinite loop
        # Further reads keep returning EOF without exception.
        assert reader.read() == (False, None)
        reader.release()


# --------------------------------------------------------------------------- #
# G. missing/corrupt file
# --------------------------------------------------------------------------- #

class TestMissingOrCorrupt:
    def test_missing_file_stays_unavailable(self, tmp_path):
        missing = tmp_path / "does_not_exist.mp4"
        reader = OpenCvVideoReader(str(missing))
        reader.open()  # must not raise
        assert reader.is_available() is False
        reader.release()  # safe

    def test_corrupt_file_stays_unavailable(self, tmp_path):
        corrupt = tmp_path / "corrupt.mp4"
        corrupt.write_bytes(b"not-a-real-video")
        reader = OpenCvVideoReader(str(corrupt))
        reader.open()  # must not raise
        assert reader.is_available() is False
        reader.release()

    def test_capture_not_opened_via_fake(self, monkeypatch):
        factory = _CaptureFactory(capture_kwargs={"opened": False})
        monkeypatch.setattr(reader_mod.cv2, "VideoCapture", factory)
        reader = OpenCvVideoReader("outputs/v/monitoring.mp4")
        reader.open()
        assert reader.is_available() is False
        # The unopened capture was released and not retained.
        assert factory.instances[0].released == 1


# --------------------------------------------------------------------------- #
# H. invalid metadata policy
# --------------------------------------------------------------------------- #

class TestInvalidMetadataPolicy:
    def test_fps_zero_rejected(self, monkeypatch):
        factory = _CaptureFactory(capture_kwargs={"fps": 0.0})
        monkeypatch.setattr(reader_mod.cv2, "VideoCapture", factory)
        reader = OpenCvVideoReader("outputs/v/monitoring.mp4")
        reader.open()
        assert reader.is_available() is False  # invalid fps -> unavailable
        assert factory.instances[0].released == 1
        # metadata() on the now-unavailable reader raises explicitly.
        with pytest.raises(VideoReaderError):
            reader.metadata()

    def test_fps_negative_rejected(self, monkeypatch):
        factory = _CaptureFactory(capture_kwargs={"fps": -5.0})
        monkeypatch.setattr(reader_mod.cv2, "VideoCapture", factory)
        reader = OpenCvVideoReader("outputs/v/monitoring.mp4")
        reader.open()
        assert reader.is_available() is False

    def test_zero_width_rejected(self, monkeypatch):
        factory = _CaptureFactory(capture_kwargs={"width": 0})
        monkeypatch.setattr(reader_mod.cv2, "VideoCapture", factory)
        reader = OpenCvVideoReader("outputs/v/monitoring.mp4")
        reader.open()
        assert reader.is_available() is False

    def test_zero_height_rejected(self, monkeypatch):
        factory = _CaptureFactory(capture_kwargs={"height": 0})
        monkeypatch.setattr(reader_mod.cv2, "VideoCapture", factory)
        reader = OpenCvVideoReader("outputs/v/monitoring.mp4")
        reader.open()
        assert reader.is_available() is False

    def test_valid_metadata_accepted(self, fake_capture):
        reader = OpenCvVideoReader("outputs/v/monitoring.mp4")
        reader.open()
        assert reader.is_available() is True
        reader.release()


# --------------------------------------------------------------------------- #
# Error contract: unavailable reader raises; backend errors are wrapped
# --------------------------------------------------------------------------- #

class TestReadErrorContract:
    def test_read_before_open_raises(self, synthetic_video):
        reader = OpenCvVideoReader(synthetic_video.name, allowed_base=synthetic_video.parent)
        with pytest.raises(VideoReaderError):
            reader.read()

    def test_read_after_release_raises(self, synthetic_video):
        reader = OpenCvVideoReader(synthetic_video.name, allowed_base=synthetic_video.parent)
        reader.open()
        reader.release()
        with pytest.raises(VideoReaderError):
            reader.read()

    def test_metadata_after_release_raises(self, synthetic_video):
        reader = OpenCvVideoReader(synthetic_video.name, allowed_base=synthetic_video.parent)
        reader.open()
        reader.release()
        with pytest.raises(VideoReaderError):
            reader.metadata()

    def test_eof_still_returns_false_none_not_error(self, synthetic_video):
        """EOF of an OPEN reader is (False, None), not a VideoReaderError."""
        reader = OpenCvVideoReader(synthetic_video.name, allowed_base=synthetic_video.parent)
        reader.open()
        while True:
            ok, _ = reader.read()
            if not ok:
                break
        assert reader.read() == (False, None)  # EOF, not an exception
        reader.release()

    def test_backend_read_exception_is_wrapped(self, monkeypatch):
        factory = _CaptureFactory(capture_kwargs={"raise_on_read": True})
        monkeypatch.setattr(reader_mod.cv2, "VideoCapture", factory)
        reader = OpenCvVideoReader("outputs/v/monitoring.mp4")
        reader.open()
        assert reader.is_available() is True  # opened fine; read will fail
        with pytest.raises(VideoReaderError) as exc_info:
            reader.read()
        # Original backend error preserved via chaining, not surfaced raw.
        assert isinstance(exc_info.value.__cause__, RuntimeError)

    def test_backend_metadata_exception_is_wrapped(self, monkeypatch):
        factory = _CaptureFactory(capture_kwargs={"raise_on_get_after_open": True})
        monkeypatch.setattr(reader_mod.cv2, "VideoCapture", factory)
        reader = OpenCvVideoReader("outputs/v/monitoring.mp4")
        reader.open()  # first 4 get() calls succeed -> reader available
        assert reader.is_available() is True
        with pytest.raises(VideoReaderError) as exc_info:
            reader.metadata()
        assert isinstance(exc_info.value.__cause__, RuntimeError)


# --------------------------------------------------------------------------- #
# Path sanitizer integration (minimal; Task 12 owns exhaustive coverage)
# --------------------------------------------------------------------------- #

class TestPathSanitizerIntegration:
    def test_relative_traversal_path_rejected_stays_unavailable(self, fake_capture):
        # A relative path escaping the base is rejected by validate_safe_path;
        # open() must not construct a capture and must stay unavailable.
        reader = OpenCvVideoReader("../../etc/passwd")
        reader.open()
        assert reader.is_available() is False
        assert fake_capture.count == 0  # never reached cv2.VideoCapture
