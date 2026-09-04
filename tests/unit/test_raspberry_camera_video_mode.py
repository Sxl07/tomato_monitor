"""Tests for RaspberryCameraFrameSource camera_mode (Spec 019, Task 4).

Covers cases A-J with a fake Picamera2 (no hardware required):
    A. default STILL uses create_still_configuration, not video.
    B. explicit STILL same as default.
    C. VIDEO uses create_video_configuration with main size + FrameDurationLimits.
    D. VIDEO does not call create_still_configuration.
    E. invalid fps in VIDEO mode -> explicit error before touching the camera.
    F. invalid camera_mode -> explicit error.
    G. read() path unchanged (RGB -> BGR, no sleeps/throttling added).
    H. single-camera lifecycle: one lock, one Picamera2, one start; reuse on
       second read; stop/close/lock-release on release() — for STILL and VIDEO.
    I. release() idempotent.
    J. importability without picamera2 in application/domain (covered by the
       existing test_imports suite; here we assert the module degrades cleanly
       when picamera2 is unavailable).

The frame source imports ``Picamera2`` at module import (guarded by try/except).
In this environment picamera2 is absent, so we patch the module symbols
``Picamera2`` and ``PICAMERA2_AVAILABLE`` to drive the configuration paths.
"""

import threading
from unittest.mock import patch

import numpy as np
import pytest

import src.infrastructure.camera.raspberry_camera_frame_source as rcfs
from src.infrastructure.camera.raspberry_camera_frame_source import (
    RaspberryCameraFrameSource,
)


W = 640
H = 480


# --------------------------------------------------------------------------- #
# Fake libcamera enums (PC/CI has no real libcamera). These stand in for
# libcamera.controls.* so the VIDEO configuration can be built and inspected
# without depending on the real library. Sentinel values are compared by
# identity in the assertions below.
# --------------------------------------------------------------------------- #

class _FakeAeConstraintModeEnum:
    Highlight = "AeConstraintMode.Highlight"


class _FakeAeExposureModeEnum:
    Normal = "AeExposureMode.Normal"
    Short = "AeExposureMode.Short"


class _FakeAwbModeEnum:
    Auto = "AwbMode.Auto"


class _FakeLibcameraControls:
    AeConstraintModeEnum = _FakeAeConstraintModeEnum
    AeExposureModeEnum = _FakeAeExposureModeEnum
    AwbModeEnum = _FakeAwbModeEnum


class _FakeCamera:
    """Fake Picamera2 recording configuration/lifecycle calls."""

    def __init__(self):
        self.still_calls = []
        self.video_calls = []
        self.configured = []
        self.started = 0
        self.stopped = 0
        self.closed = 0
        self._frame = np.zeros((H, W, 3), dtype=np.uint8)  # RGB frame

    def create_still_configuration(self, **kwargs):
        cfg = {"kind": "still", **kwargs}
        self.still_calls.append(kwargs)
        return cfg

    def create_video_configuration(self, **kwargs):
        cfg = {"kind": "video", **kwargs}
        self.video_calls.append(kwargs)
        return cfg

    def configure(self, config):
        self.configured.append(config)

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def close(self):
        self.closed += 1

    def capture_array(self):
        # picamera2 returns RGB; make a distinctive gradient so BGR swap is visible.
        frame = np.zeros((H, W, 3), dtype=np.uint8)
        frame[..., 0] = 10  # R
        frame[..., 1] = 20  # G
        frame[..., 2] = 30  # B
        return frame


class _CameraFactory:
    def __init__(self):
        self.count = 0
        self.instances = []

    def __call__(self):
        self.count += 1
        cam = _FakeCamera()
        self.instances.append(cam)
        return cam


@pytest.fixture(autouse=True)
def reset_camera_lock():
    """Ensure the module lock is free before/after each test."""
    # Best-effort release in case a prior failure left it held.
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
    """Patch the module Picamera2 symbol + availability with a counting factory."""
    factory = _CameraFactory()
    monkeypatch.setattr(rcfs, "PICAMERA2_AVAILABLE", True)
    monkeypatch.setattr(rcfs, "Picamera2", factory, raising=False)
    # VIDEO mode needs the libcamera exposure/AWB enums; provide a fake so the
    # configuration can be built and inspected without the real library.
    monkeypatch.setattr(rcfs, "libcamera_controls", _FakeLibcameraControls, raising=False)
    # Avoid real settling sleeps slowing the tests.
    monkeypatch.setattr(rcfs.time, "sleep", lambda *_a, **_k: None)
    return factory


# --------------------------------------------------------------------------- #
# A / B. STILL configuration
# --------------------------------------------------------------------------- #

class TestStillConfiguration:
    def test_default_uses_still_configuration(self, fake_picamera):
        src = RaspberryCameraFrameSource(width=W, height=H, fps=5)
        ok, _ = src.read()
        assert ok is True
        cam = fake_picamera.instances[0]
        assert cam.still_calls == [{"main": {"size": (W, H)}}]
        assert cam.video_calls == []
        src.release()

    def test_explicit_still_same_as_default(self, fake_picamera):
        src = RaspberryCameraFrameSource(width=W, height=H, fps=5, camera_mode="still")
        src.read()
        cam = fake_picamera.instances[0]
        assert cam.still_calls == [{"main": {"size": (W, H)}}]
        assert cam.video_calls == []
        src.release()


# --------------------------------------------------------------------------- #
# C / D. VIDEO configuration
# --------------------------------------------------------------------------- #

class TestVideoConfiguration:
    def test_video_uses_video_configuration_fps10(self, fake_picamera):
        src = RaspberryCameraFrameSource(width=W, height=H, fps=10, camera_mode="video")
        src.read()
        cam = fake_picamera.instances[0]
        assert len(cam.video_calls) == 1
        call = cam.video_calls[0]
        assert call["main"] == {"size": (W, H)}
        assert call["controls"]["FrameDurationLimits"] == (100000, 100000)
        src.release()

    def test_video_fps5_frame_duration(self, fake_picamera):
        src = RaspberryCameraFrameSource(width=W, height=H, fps=5, camera_mode="video")
        src.read()
        cam = fake_picamera.instances[0]
        assert cam.video_calls[0]["controls"]["FrameDurationLimits"] == (200000, 200000)
        src.release()

    def test_video_does_not_call_still_configuration(self, fake_picamera):
        src = RaspberryCameraFrameSource(width=W, height=H, fps=10, camera_mode="video")
        src.read()
        cam = fake_picamera.instances[0]
        assert cam.still_calls == []
        src.release()

    def test_video_applies_exposure_and_awb_controls(self, fake_picamera):
        # VIDEO must add the IMX500-validated Highlight + EV -0.7 auto-exposure
        # and Auto AWB controls as siblings of FrameDurationLimits, keeping the
        # frame duration intact.
        src = RaspberryCameraFrameSource(width=W, height=H, fps=5, camera_mode="video")
        src.read()
        cam = fake_picamera.instances[0]
        controls = cam.video_calls[0]["controls"]

        # FrameDurationLimits unchanged (5 FPS -> 200000).
        assert controls["FrameDurationLimits"] == (200000, 200000)
        # Auto-exposure / AWB tuning.
        assert controls["AeEnable"] is True
        assert controls["AeConstraintMode"] is _FakeAeConstraintModeEnum.Highlight
        assert controls["AeExposureMode"] is _FakeAeExposureModeEnum.Normal
        assert controls["ExposureValue"] == -0.7
        assert controls["AwbEnable"] is True
        assert controls["AwbMode"] is _FakeAwbModeEnum.Auto
        src.release()

    def test_video_does_not_set_manual_exposure_or_gains(self, fake_picamera):
        # The camera must keep deciding ExposureTime / AnalogueGain / ColourGains
        # automatically — none of these may be present in controls.
        src = RaspberryCameraFrameSource(width=W, height=H, fps=5, camera_mode="video")
        src.read()
        cam = fake_picamera.instances[0]
        controls = cam.video_calls[0]["controls"]

        assert "ExposureTime" not in controls
        assert "AnalogueGain" not in controls
        assert "ColourGains" not in controls
        src.release()

    def test_video_exposure_mode_is_not_short(self, fake_picamera):
        src = RaspberryCameraFrameSource(width=W, height=H, fps=5, camera_mode="video")
        src.read()
        cam = fake_picamera.instances[0]
        controls = cam.video_calls[0]["controls"]
        assert controls["AeExposureMode"] is not _FakeAeExposureModeEnum.Short
        src.release()

    def test_video_without_libcamera_fails_explicitly(self, fake_picamera, monkeypatch):
        # If libcamera enums are unavailable, VIDEO must fail explicitly rather
        # than build a config without the exposure controls.
        monkeypatch.setattr(rcfs, "libcamera_controls", None, raising=False)
        src = RaspberryCameraFrameSource(width=W, height=H, fps=5, camera_mode="video")
        ok, frame = src.read()
        # read() wraps start failures and returns (False, None); the camera is
        # cleaned up and the lock released.
        assert ok is False
        assert frame is None
        assert rcfs.is_camera_locked() is False


# --------------------------------------------------------------------------- #
# E / F. validation
# --------------------------------------------------------------------------- #

class TestValidation:
    def test_video_fps_zero_rejected(self):
        with pytest.raises(ValueError):
            RaspberryCameraFrameSource(width=W, height=H, fps=0, camera_mode="video")

    def test_video_fps_negative_rejected(self):
        with pytest.raises(ValueError):
            RaspberryCameraFrameSource(width=W, height=H, fps=-3, camera_mode="video")

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValueError):
            RaspberryCameraFrameSource(width=W, height=H, fps=5, camera_mode="banana")

    def test_still_fps_zero_allowed_for_backward_compat(self, fake_picamera):
        # STILL mode does not impose a cadence; fps<=0 must not break existing callers.
        src = RaspberryCameraFrameSource(width=W, height=H, fps=0, camera_mode="still")
        ok, _ = src.read()
        assert ok is True
        src.release()

    def test_frame_duration_us_rounding(self):
        assert RaspberryCameraFrameSource._frame_duration_us(10) == 100000
        assert RaspberryCameraFrameSource._frame_duration_us(5) == 200000
        assert RaspberryCameraFrameSource._frame_duration_us(30) == 33333  # round(33333.33)


# --------------------------------------------------------------------------- #
# G. read() path unchanged (RGB -> BGR, no throttling)
# --------------------------------------------------------------------------- #

class TestReadPath:
    def test_read_converts_rgb_to_bgr(self, fake_picamera):
        src = RaspberryCameraFrameSource(width=W, height=H, fps=10, camera_mode="video")
        ok, frame_bgr = src.read()
        assert ok is True
        # Fake camera returns R=10, G=20, B=30 (RGB). After RGB->BGR swap the
        # channel order becomes B=30, G=20, R=10.
        assert int(frame_bgr[0, 0, 0]) == 30  # B
        assert int(frame_bgr[0, 0, 1]) == 20  # G
        assert int(frame_bgr[0, 0, 2]) == 10  # R
        src.release()

    def test_video_four_channel_frame_becomes_bgr3(self, monkeypatch):
        """VIDEO main stream may deliver 4 channels (e.g. XBGR8888/RGBX).

        The current read() does cv2.cvtColor(frame, COLOR_RGB2BGR), which for a
        4-channel input yields a 3-channel BGR frame. This guards that the VIDEO
        path keeps feeding the VideoRecorder 3-channel BGR frames. No production
        pixel-format is fixed here; the real format is verified later on the RPi
        via picam2.camera_configuration()["main"].
        """
        class _FourChannelCamera(_FakeCamera):
            def capture_array(self):
                # RGBX: R=10, G=20, B=30, X=255
                frame = np.zeros((H, W, 4), dtype=np.uint8)
                frame[..., 0] = 10
                frame[..., 1] = 20
                frame[..., 2] = 30
                frame[..., 3] = 255
                return frame

        class _FourChannelFactory:
            def __init__(self):
                self.instances = []

            def __call__(self):
                cam = _FourChannelCamera()
                self.instances.append(cam)
                return cam

        factory = _FourChannelFactory()
        monkeypatch.setattr(rcfs, "PICAMERA2_AVAILABLE", True)
        monkeypatch.setattr(rcfs, "Picamera2", factory, raising=False)
        # VIDEO mode requires the libcamera exposure/AWB enums.
        monkeypatch.setattr(rcfs, "libcamera_controls", _FakeLibcameraControls, raising=False)
        monkeypatch.setattr(rcfs.time, "sleep", lambda *_a, **_k: None)

        src = RaspberryCameraFrameSource(width=W, height=H, fps=10, camera_mode="video")
        ok, frame_bgr = src.read()
        assert ok is True
        assert frame_bgr.shape == (H, W, 3)  # 4ch -> 3ch
        assert int(frame_bgr[0, 0, 0]) == 30  # B
        assert int(frame_bgr[0, 0, 1]) == 20  # G
        assert int(frame_bgr[0, 0, 2]) == 10  # R
        src.release()

    def test_no_sleep_calls_in_read(self, fake_picamera, monkeypatch):
        # read() itself must not introduce throttling sleeps. _start_camera has a
        # single stabilization sleep; we count sleeps during the SECOND read
        # (camera already started) to prove read() adds none.
        src = RaspberryCameraFrameSource(width=W, height=H, fps=10, camera_mode="video")
        src.read()  # first read: starts camera
        sleep_calls = []
        monkeypatch.setattr(rcfs.time, "sleep", lambda *a, **k: sleep_calls.append(a))
        src.read()  # second read: no start, no throttle
        assert sleep_calls == []
        src.release()


# --------------------------------------------------------------------------- #
# H. single camera lifecycle
# --------------------------------------------------------------------------- #

class TestSingleCameraLifecycle:
    @pytest.mark.parametrize("mode", ["still", "video"])
    def test_lifecycle_single_camera_and_lock(self, fake_picamera, mode):
        src = RaspberryCameraFrameSource(width=W, height=H, fps=10, camera_mode=mode)

        assert rcfs.is_camera_locked() is False
        # First read: lock acquired, one Picamera2, one start.
        ok1, _ = src.read()
        assert ok1 is True
        assert fake_picamera.count == 1
        assert fake_picamera.instances[0].started == 1
        assert rcfs.is_camera_locked() is True  # persistent lock held

        # Second read: same camera, no new instance, no new start.
        ok2, _ = src.read()
        assert ok2 is True
        assert fake_picamera.count == 1
        assert fake_picamera.instances[0].started == 1

        # Release: stop + close + lock released.
        src.release()
        cam = fake_picamera.instances[0]
        assert cam.stopped == 1
        assert cam.closed == 1
        assert rcfs.is_camera_locked() is False

    def test_video_mode_uses_same_module_lock(self, fake_picamera):
        # There must be exactly one lock object; no video-specific lock exists.
        assert isinstance(rcfs._camera_lock, type(threading.Lock()))
        src = RaspberryCameraFrameSource(width=W, height=H, fps=10, camera_mode="video")
        src.read()
        # While held, the module-level is_camera_locked reflects it.
        assert rcfs.is_camera_locked() is True
        src.release()
        assert rcfs.is_camera_locked() is False
        # No second lock OBJECT introduced at module level: the only
        # threading.Lock instance must be _camera_lock itself.
        lock_type = type(threading.Lock())
        lock_objects = [
            name for name, value in vars(rcfs).items()
            if isinstance(value, lock_type)
        ]
        assert lock_objects == ["_camera_lock"]


# --------------------------------------------------------------------------- #
# I. release idempotent
# --------------------------------------------------------------------------- #

class TestReleaseIdempotent:
    def test_double_release_safe(self, fake_picamera):
        src = RaspberryCameraFrameSource(width=W, height=H, fps=10, camera_mode="video")
        src.read()
        src.release()
        src.release()  # must not raise
        assert rcfs.is_camera_locked() is False


# --------------------------------------------------------------------------- #
# J. degrade cleanly without picamera2
# --------------------------------------------------------------------------- #

class TestNoPicamera:
    def test_read_returns_false_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(rcfs, "PICAMERA2_AVAILABLE", False)
        src = RaspberryCameraFrameSource(width=W, height=H, fps=10, camera_mode="video")
        ok, frame = src.read()
        assert ok is False
        assert frame is None
        assert rcfs.is_camera_locked() is False
