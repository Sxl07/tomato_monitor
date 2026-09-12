"""Tests for the camera_stream_fps profile field and fail-fast cadence
validation (Spec 023, Task 1).

Covers:
    - EDGE = 20 / 5 and FULL = 20 / 10 (camera_stream_fps / recording_target_fps).
    - recording_target_fps values are NOT changed by Spec 023.
    - validate_profile_cadences: valid config accepted; fail-fast (ValueError)
      when recording_target_fps > camera_stream_fps, or either fps <= 0.

These are pure config tests: no camera, no hardware, no heavy backends.
"""

import dataclasses

import pytest

from src.infrastructure.config.settings import (
    EDGE_PROFILE,
    FULL_PROFILE,
    ExecutionProfile,
    validate_profile_cadences,
)


def _profile_with(**overrides) -> ExecutionProfile:
    """Build an ExecutionProfile from EDGE_PROFILE with field overrides.

    Uses dataclasses.replace so the test stays valid if new fields are added
    later, and never mutates the frozen originals.
    """
    return dataclasses.replace(EDGE_PROFILE, **overrides)


class TestCameraStreamFpsValues:
    def test_edge_camera_stream_fps(self):
        assert EDGE_PROFILE.camera_stream_fps == 20.0

    def test_full_camera_stream_fps(self):
        assert FULL_PROFILE.camera_stream_fps == 20.0

    def test_recording_target_fps_unchanged_edge(self):
        # Spec 023 must NOT alter the existing recording cadence.
        assert EDGE_PROFILE.recording_target_fps == 5.0

    def test_recording_target_fps_unchanged_full(self):
        assert FULL_PROFILE.recording_target_fps == 10.0

    def test_stream_fps_is_float(self):
        assert isinstance(EDGE_PROFILE.camera_stream_fps, float)
        assert isinstance(FULL_PROFILE.camera_stream_fps, float)

    def test_frozen_field_cannot_be_mutated(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            EDGE_PROFILE.camera_stream_fps = 99.0  # type: ignore[misc]


class TestValidateProfileCadences:
    def test_shipped_profiles_are_valid(self):
        # Must not raise for the profiles defined in code.
        validate_profile_cadences(EDGE_PROFILE)
        validate_profile_cadences(FULL_PROFILE)

    def test_valid_config_accepted(self):
        # recording_target_fps < camera_stream_fps
        validate_profile_cadences(
            _profile_with(camera_stream_fps=20.0, recording_target_fps=5.0)
        )

    def test_equal_cadences_accepted(self):
        # recording_target_fps == camera_stream_fps is allowed (<=).
        validate_profile_cadences(
            _profile_with(camera_stream_fps=10.0, recording_target_fps=10.0)
        )

    def test_recording_greater_than_stream_raises(self):
        with pytest.raises(ValueError):
            validate_profile_cadences(
                _profile_with(camera_stream_fps=5.0, recording_target_fps=10.0)
            )

    def test_camera_stream_fps_zero_raises(self):
        with pytest.raises(ValueError):
            validate_profile_cadences(
                _profile_with(camera_stream_fps=0.0, recording_target_fps=5.0)
            )

    def test_camera_stream_fps_negative_raises(self):
        with pytest.raises(ValueError):
            validate_profile_cadences(
                _profile_with(camera_stream_fps=-1.0, recording_target_fps=5.0)
            )

    def test_recording_target_fps_zero_raises(self):
        with pytest.raises(ValueError):
            validate_profile_cadences(
                _profile_with(camera_stream_fps=20.0, recording_target_fps=0.0)
            )

    def test_recording_target_fps_negative_raises(self):
        with pytest.raises(ValueError):
            validate_profile_cadences(
                _profile_with(camera_stream_fps=20.0, recording_target_fps=-3.0)
            )
