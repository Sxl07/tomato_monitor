"""Tests for the physical camera cadence and configurable lock timeout
(Spec 023, Task 2). No real camera / picamera2 / libcamera required.

Covers:
    - _frame_duration_us(20) == 50_000 (video-mode FrameDurationLimits source).
    - Video-mode configuration passes FrameDurationLimits derived from fps.
    - Still-mode configuration does NOT impose FrameDurationLimits from fps.
    - Existing fps validation preserved (video + fps<=0 -> ValueError).
    - camera_lock_timeout_seconds default is 15.0.
    - camera_lock_timeout_seconds is the value used in _camera_lock.acquire.
    - create_frame_source propagates camera_lock_timeout_seconds to the
      Raspberry backend.
"""

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.infrastructure.camera.raspberry_camera_frame_source import (
    RaspberryCameraFrameSource,
)


class _FakeCam:
    """Captures the config dict returned by create_*_configuration."""

    def __init__(self):
        self.video_kwargs = None
        self.still_kwargs = None

    def create_video_configuration(self, **kwargs):
        self.video_kwargs = kwargs
        return {"_kind": "video", **kwargs}

    def create_still_configuration(self, **kwargs):
        self.still_kwargs = kwargs
        return {"_kind": "still", **kwargs}


class TestFrameDurationMath:
    def test_frame_duration_20fps_is_50000_us(self):
        assert RaspberryCameraFrameSource._frame_duration_us(20) == 50_000

    def test_frame_duration_5fps_is_200000_us(self):
        assert RaspberryCameraFrameSource._frame_duration_us(5) == 200_000

    def test_frame_duration_invalid_raises(self):
        with pytest.raises(ValueError):
            RaspberryCameraFrameSource._frame_duration_us(0)


class TestVideoModeConfiguration:
    def test_video_mode_sets_frame_duration_limits_from_fps(self):
        # Provide fake libcamera controls so the VIDEO branch can build.
        fake_controls = SimpleNamespace(
            AeConstraintModeEnum=SimpleNamespace(Highlight=object()),
            AeExposureModeEnum=SimpleNamespace(Normal=object()),
            AwbModeEnum=SimpleNamespace(Auto=object()),
        )
        src = RaspberryCameraFrameSource(
            width=960, height=720, fps=20, camera_mode="video"
        )
        cam = _FakeCam()
        with patch(
            "src.infrastructure.camera.raspberry_camera_frame_source.libcamera_controls",
            fake_controls,
        ):
            config = src._build_persistent_configuration(cam)

        assert config["_kind"] == "video"
        limits = cam.video_kwargs["controls"]["FrameDurationLimits"]
        # 20 FPS -> 50_000 us, applied as (min, max).
        assert limits == (50_000, 50_000)
        assert cam.video_kwargs["main"]["size"] == (960, 720)


class TestStillModeConfiguration:
    def test_still_mode_does_not_impose_frame_duration_from_fps(self):
        src = RaspberryCameraFrameSource(
            width=640, height=480, fps=20, camera_mode="still"
        )
        cam = _FakeCam()
        config = src._build_persistent_configuration(cam)

        assert config["_kind"] == "still"
        # Legacy still config: only main size, no controls / FrameDurationLimits.
        assert cam.still_kwargs == {"main": {"size": (640, 480)}}
        assert "controls" not in cam.still_kwargs


class TestFpsValidationPreserved:
    def test_video_mode_fps_zero_raises(self):
        with pytest.raises(ValueError):
            RaspberryCameraFrameSource(camera_mode="video", fps=0)

    def test_video_mode_fps_negative_raises(self):
        with pytest.raises(ValueError):
            RaspberryCameraFrameSource(camera_mode="video", fps=-5)

    def test_still_mode_allows_any_fps(self):
        # Still mode does not impose the fps>0 constraint (legacy behavior).
        src = RaspberryCameraFrameSource(camera_mode="still", fps=0)
        assert src is not None


class TestCameraLockTimeout:
    def test_default_timeout_is_15(self):
        src = RaspberryCameraFrameSource()
        assert src._camera_lock_timeout_seconds == 15.0

    def test_configurable_timeout_stored(self):
        src = RaspberryCameraFrameSource(camera_lock_timeout_seconds=0.5)
        assert src._camera_lock_timeout_seconds == 0.5

    def test_read_uses_configured_timeout_in_acquire(self):
        # Force PICAMERA2_AVAILABLE True and stub the module-level lock so we can
        # assert the timeout passed to acquire, without any real camera.
        src = RaspberryCameraFrameSource(
            camera_mode="video", fps=20, camera_lock_timeout_seconds=0.75
        )

        captured = {}

        class _FakeLock:
            def acquire(self, timeout=None):
                captured["timeout"] = timeout
                return False  # fail fast: read() returns (False, None), no camera

        mod = "src.infrastructure.camera.raspberry_camera_frame_source"
        with patch(f"{mod}.PICAMERA2_AVAILABLE", True), patch(
            f"{mod}._camera_lock", _FakeLock()
        ):
            ok, frame = src.read()

        assert ok is False and frame is None
        assert captured["timeout"] == 0.75


class TestFactoryPropagatesLockTimeout:
    def test_create_frame_source_forwards_lock_timeout(self):
        captured = {}

        class _FakeRaspberrySource:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        fake_module = SimpleNamespace(
            RaspberryCameraFrameSource=_FakeRaspberrySource,
            PICAMERA2_AVAILABLE=True,
        )

        from src.application.services import frame_source_factory

        with patch.dict(
            sys.modules,
            {"src.infrastructure.camera.raspberry_camera_frame_source": fake_module},
        ):
            src = frame_source_factory.create_frame_source(
                width=960,
                height=720,
                fps=20,
                camera_mode="video",
                camera_lock_timeout_seconds=1.0,
            )

        assert isinstance(src, _FakeRaspberrySource)
        assert captured["camera_lock_timeout_seconds"] == 1.0
        assert captured["camera_mode"] == "video"
        assert captured["fps"] == 20

    def test_create_frame_source_omits_lock_timeout_when_not_given(self):
        # Backward compatibility: when not provided, the kwarg is not forced,
        # so the RaspberryCameraFrameSource default (15.0) applies.
        captured = {}

        class _FakeRaspberrySource:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        fake_module = SimpleNamespace(
            RaspberryCameraFrameSource=_FakeRaspberrySource,
            PICAMERA2_AVAILABLE=True,
        )

        from src.application.services import frame_source_factory

        with patch.dict(
            sys.modules,
            {"src.infrastructure.camera.raspberry_camera_frame_source": fake_module},
        ):
            frame_source_factory.create_frame_source(
                width=640, height=480, fps=5, camera_mode="video"
            )

        assert "camera_lock_timeout_seconds" not in captured
