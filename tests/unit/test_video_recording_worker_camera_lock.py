"""End-to-end camera-lock integration for VideoRecordingWorker (Spec 019, Task 4).

Proves, without hardware, that the full chain releases the persistent camera:

    worker.run -> frame_source.read -> acquires _camera_lock -> recording
    -> finalize/error -> worker.finally -> release_resources
    -> frame_source.release -> _camera_lock free

Uses the REAL VideoRecordingWorker and REAL RaspberryCameraFrameSource with a
FAKE Picamera2 and a FAKE VideoRecorder. The worker itself never spawns a
thread; the TEST runs worker.run() in a thread only to observe the lock while
recording is in progress.
"""

import threading

import numpy as np
import pytest

import src.infrastructure.camera.raspberry_camera_frame_source as rcfs
from src.infrastructure.camera.raspberry_camera_frame_source import (
    RaspberryCameraFrameSource,
)
from src.application.services.video_recording_worker import VideoRecordingWorker


W = 64
H = 48


class _FakePicamera2:
    """Fake Picamera2 for the VIDEO configuration path, tracking lifecycle."""

    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.closed = 0

    def create_video_configuration(self, **kwargs):
        return {"kind": "video", **kwargs}

    def create_still_configuration(self, **kwargs):  # pragma: no cover - not used here
        return {"kind": "still", **kwargs}

    def configure(self, config):
        pass

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def close(self):
        self.closed += 1

    def capture_array(self):
        # RGB frame (3 channels); contents irrelevant.
        return np.zeros((H, W, 3), dtype=np.uint8)


class _Picamera2Factory:
    def __init__(self):
        self.count = 0
        self.instances = []

    def __call__(self):
        self.count += 1
        cam = _FakePicamera2()
        self.instances.append(cam)
        return cam


class _SignalingRecorder:
    """Fake VideoRecorder that signals after the first write; optional write error."""

    def __init__(self, first_write_event, *, write_raises_at=None):
        self._event = first_write_event
        self._write_raises_at = write_raises_at
        self.opened_with = None
        self.closed = 0
        self._frames_written = 0
        self.codec_used = "mp4v"

    def open(self, frame_size):
        self.opened_with = frame_size

    def write(self, frame):
        idx = self._frames_written
        if self._write_raises_at is not None and idx == self._write_raises_at:
            raise RuntimeError("simulated write failure")
        self._frames_written += 1
        self._event.set()  # a frame has been recorded

    def close(self):
        self.closed += 1

    @property
    def frames_written(self):
        return self._frames_written


@pytest.fixture(autouse=True)
def reset_camera_lock():
    """Ensure the module lock is free before and after each test."""
    if rcfs.is_camera_locked():
        try:
            rcfs._camera_lock.release()
        except RuntimeError:
            pass
    yield
    if rcfs.is_camera_locked():
        try:
            rcfs._camera_lock.release()
        except RuntimeError:
            pass


@pytest.fixture
def fake_picamera(monkeypatch):
    factory = _Picamera2Factory()
    monkeypatch.setattr(rcfs, "PICAMERA2_AVAILABLE", True)
    monkeypatch.setattr(rcfs, "Picamera2", factory, raising=False)
    # VIDEO mode requires the libcamera exposure/AWB enums; provide a minimal
    # fake so the video configuration builds without the real library (PC/CI).
    class _FakeLibcameraControls:
        class AeConstraintModeEnum:
            Highlight = "AeConstraintMode.Highlight"

        class AeExposureModeEnum:
            Normal = "AeExposureMode.Normal"

        class AwbModeEnum:
            Auto = "AwbMode.Auto"

    monkeypatch.setattr(rcfs, "libcamera_controls", _FakeLibcameraControls, raising=False)
    monkeypatch.setattr(rcfs.time, "sleep", lambda *_a, **_k: None)  # no settling delays
    return factory


class _AlwaysWriteSampler:
    def should_write(self) -> bool:
        return True


def _make_worker(frame_source, recorder):
    return VideoRecordingWorker(
        monitoring_id=1,
        frame_source=frame_source,
        video_recorder=recorder,
        monitoring_repo=object(),
        db_session=object(),
        configured_recording_fps=10.0,
        configured_camera_stream_fps=20.0,
        recording_sampler=_AlwaysWriteSampler(),
    )


def test_camera_lock_acquired_during_recording_and_released_on_finalize(fake_picamera):
    frame_source = RaspberryCameraFrameSource(
        width=W, height=H, fps=10, camera_mode="video"
    )
    first_write = threading.Event()
    recorder = _SignalingRecorder(first_write)
    worker = _make_worker(frame_source, recorder)

    assert rcfs.is_camera_locked() is False

    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        # Wait deterministically until at least one frame has been recorded.
        assert first_write.wait(timeout=5.0), "worker did not record a frame in time"

        # During recording the persistent camera lock is held and exactly one
        # Picamera2 instance exists.
        assert rcfs.is_camera_locked() is True
        assert fake_picamera.count == 1
    finally:
        worker.finalize_event.set()
        thread.join(timeout=5.0)

    assert thread.is_alive() is False
    assert worker.recording_metrics.exit_reason == "finalize"
    # Lock released end-to-end.
    assert rcfs.is_camera_locked() is False
    # Camera fully torn down.
    cam = fake_picamera.instances[0]
    assert cam.stopped == 1
    assert cam.closed == 1
    assert fake_picamera.count == 1


def test_camera_lock_released_on_write_error(fake_picamera):
    frame_source = RaspberryCameraFrameSource(
        width=W, height=H, fps=10, camera_mode="video"
    )
    # First write raises -> error path after the camera was acquired.
    recorder = _SignalingRecorder(threading.Event(), write_raises_at=0)
    worker = _make_worker(frame_source, recorder)

    assert rcfs.is_camera_locked() is False

    # Synchronous run is fine here: it errors quickly on the first write.
    worker.run()

    assert worker.recording_metrics.exit_reason == "error"
    # Requirement 1.19: camera lock released even on error.
    assert rcfs.is_camera_locked() is False
    assert fake_picamera.count == 1
    cam = fake_picamera.instances[0]
    assert cam.stopped == 1
    assert cam.closed == 1
