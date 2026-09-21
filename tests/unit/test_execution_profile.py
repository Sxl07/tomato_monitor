"""Unit tests for ExecutionProfile and TOMATO_MONITOR_PROFILE env var selection.

Tests verify:
- Both profiles instantiate with all required fields.
- EDGE_PROFILE and FULL_PROFILE differ per their configured values (EDGE is
  more conservative on most axes; camera resolution is a deliberate exception).
- Environment variable selects the correct profile.
- Invalid env var defaults to edge with warning.

Validates: Requirements 4.11, 4.12, 4.13
"""

import importlib
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock torch (not available on Windows dev machine) before importing settings.
_MOCK_MODULES = [
    "torch", "torch.nn", "torch.nn.functional", "torch.utils",
    "torch.utils.data", "torch.cuda", "torch.hub",
    "torchvision", "torchvision.transforms", "torchvision.transforms.functional",
    "torchvision.models", "torchvision.ops",
]
for _mod_name in _MOCK_MODULES:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()


def _reload_settings(env_value=None):
    """Reload settings module with a specific env var value.

    Uses patch.dict to set/unset TOMATO_MONITOR_PROFILE and then reloads
    the settings module so that profile selection logic runs fresh.
    """
    env = {}
    if env_value is not None:
        env["TOMATO_MONITOR_PROFILE"] = env_value
    else:
        # Ensure the var is not set
        env["TOMATO_MONITOR_PROFILE"] = ""

    with patch.dict(os.environ, env, clear=False):
        import src.infrastructure.config.settings as settings_module
        importlib.reload(settings_module)
        return settings_module


class TestExecutionProfileStructure:
    """Verify both profiles have all required fields with valid types."""

    def test_edge_profile_has_all_fields(self):
        from src.infrastructure.config.settings import EDGE_PROFILE

        assert EDGE_PROFILE.name == "edge"
        assert isinstance(EDGE_PROFILE.camera_width, int)
        assert isinstance(EDGE_PROFILE.camera_height, int)
        assert isinstance(EDGE_PROFILE.camera_fps, int)
        assert isinstance(EDGE_PROFILE.capture_loop_fps, float)
        assert isinstance(EDGE_PROFILE.min_seconds_between_snapshots, float)
        assert isinstance(EDGE_PROFILE.max_seconds_without_snapshot, float)
        assert isinstance(EDGE_PROFILE.gate_resolution, tuple)
        assert len(EDGE_PROFILE.gate_resolution) == 2
        assert isinstance(EDGE_PROFILE.inference_input_width, int)
        assert isinstance(EDGE_PROFILE.inference_input_height, int)
        assert isinstance(EDGE_PROFILE.skip_maturity, bool)
        assert isinstance(EDGE_PROFILE.detection_score_threshold, float)
        assert isinstance(EDGE_PROFILE.scene_gate_cooldown_frames, int)
        assert isinstance(EDGE_PROFILE.scene_gate_timeout_frames, int)
        assert isinstance(EDGE_PROFILE.thermal_poll_interval_seconds, float)
        assert isinstance(EDGE_PROFILE.memory_warning_rss_mb, int)

    def test_full_profile_has_all_fields(self):
        from src.infrastructure.config.settings import FULL_PROFILE

        assert FULL_PROFILE.name == "full"
        assert isinstance(FULL_PROFILE.camera_width, int)
        assert isinstance(FULL_PROFILE.camera_height, int)
        assert isinstance(FULL_PROFILE.camera_fps, int)
        assert isinstance(FULL_PROFILE.capture_loop_fps, float)
        assert isinstance(FULL_PROFILE.min_seconds_between_snapshots, float)
        assert isinstance(FULL_PROFILE.max_seconds_without_snapshot, float)
        assert isinstance(FULL_PROFILE.gate_resolution, tuple)
        assert isinstance(FULL_PROFILE.inference_input_width, int)
        assert isinstance(FULL_PROFILE.inference_input_height, int)
        assert isinstance(FULL_PROFILE.skip_maturity, bool)
        assert isinstance(FULL_PROFILE.detection_score_threshold, float)


class TestEdgeVsFullProfileDifferences:
    """Verify the configured differences between EDGE_PROFILE and FULL_PROFILE.

    EDGE is more conservative on most axes (fps, gate resolution, timing), but
    camera resolution is a deliberate exception: EDGE capture is set to 960x720
    for the Spec 019 coverage/recall experiment. Assertions below reflect the
    real configured values, not a blanket "EDGE is smaller everywhere" rule.
    """

    def test_edge_camera_resolution(self):
        # Controlled experiment (Spec 019): EDGE capture raised to 960x720 to
        # study coverage/recall on Raspberry Pi. This validates the current
        # configured resolution — it is NOT a permanent EDGE>=FULL policy.
        from src.infrastructure.config.settings import EDGE_PROFILE

        assert EDGE_PROFILE.camera_width == 960
        assert EDGE_PROFILE.camera_height == 720

    def test_edge_lower_camera_fps(self):
        from src.infrastructure.config.settings import EDGE_PROFILE, FULL_PROFILE

        assert EDGE_PROFILE.camera_fps < FULL_PROFILE.camera_fps

    def test_edge_lower_capture_loop_fps(self):
        """Edge capture_loop_fps is same as full (both use fast capture-first)."""
        from src.infrastructure.config.settings import EDGE_PROFILE, FULL_PROFILE

        assert EDGE_PROFILE.capture_loop_fps <= FULL_PROFILE.capture_loop_fps

    def test_edge_higher_min_seconds_between_snapshots(self):
        """Edge min_seconds_between_snapshots >= full (same or more conservative)."""
        from src.infrastructure.config.settings import EDGE_PROFILE, FULL_PROFILE

        assert EDGE_PROFILE.min_seconds_between_snapshots >= FULL_PROFILE.min_seconds_between_snapshots

    def test_edge_higher_max_seconds_without_snapshot(self):
        """Edge max_seconds_without_snapshot >= full (same or more conservative)."""
        from src.infrastructure.config.settings import EDGE_PROFILE, FULL_PROFILE

        assert EDGE_PROFILE.max_seconds_without_snapshot >= FULL_PROFILE.max_seconds_without_snapshot

    def test_edge_smaller_gate_resolution(self):
        """Edge gate_resolution <= full (same or smaller for speed)."""
        from src.infrastructure.config.settings import EDGE_PROFILE, FULL_PROFILE

        edge_gate_pixels = EDGE_PROFILE.gate_resolution[0] * EDGE_PROFILE.gate_resolution[1]
        full_gate_pixels = FULL_PROFILE.gate_resolution[0] * FULL_PROFILE.gate_resolution[1]
        assert edge_gate_pixels <= full_gate_pixels

    def test_edge_skip_maturity_enabled(self):
        from src.infrastructure.config.settings import EDGE_PROFILE, FULL_PROFILE

        assert EDGE_PROFILE.skip_maturity is True
        assert FULL_PROFILE.skip_maturity is False

    def test_detection_thresholds(self):
        # EDGE=0.475 is the frozen final V6 operating point (selected from
        # validation/deployment-development data before the held-out test set);
        # FULL keeps the legacy 0.80. This validates the configured values,
        # not an EDGE>=FULL policy.
        from src.infrastructure.config.settings import EDGE_PROFILE, FULL_PROFILE

        assert EDGE_PROFILE.detection_score_threshold == 0.475
        assert FULL_PROFILE.detection_score_threshold == 0.80

    def test_edge_lower_thermal_warning(self):
        from src.infrastructure.config.settings import EDGE_PROFILE, FULL_PROFILE

        assert EDGE_PROFILE.thermal_warning_temp < FULL_PROFILE.thermal_warning_temp

    def test_edge_smaller_inference_input(self):
        from src.infrastructure.config.settings import EDGE_PROFILE, FULL_PROFILE

        edge_inf = EDGE_PROFILE.inference_input_width * EDGE_PROFILE.inference_input_height
        full_inf = FULL_PROFILE.inference_input_width * FULL_PROFILE.inference_input_height
        assert edge_inf < full_inf


class TestProfileEnvVarSelection:
    """Verify TOMATO_MONITOR_PROFILE env var selects the correct profile."""

    def test_default_is_edge(self):
        settings = _reload_settings(env_value="")
        assert settings.ACTIVE_PROFILE.name == "edge"

    def test_edge_explicit(self):
        settings = _reload_settings(env_value="edge")
        assert settings.ACTIVE_PROFILE.name == "edge"

    def test_full_explicit(self):
        settings = _reload_settings(env_value="full")
        assert settings.ACTIVE_PROFILE.name == "full"

    def test_case_insensitive(self):
        settings = _reload_settings(env_value="FULL")
        assert settings.ACTIVE_PROFILE.name == "full"

    def test_with_whitespace(self):
        settings = _reload_settings(env_value="  edge  ")
        assert settings.ACTIVE_PROFILE.name == "edge"

    def test_empty_string_no_warning(self, caplog):
        """Empty string should default to edge WITHOUT a warning."""
        import logging
        with caplog.at_level(logging.WARNING):
            settings = _reload_settings(env_value="")
        assert settings.ACTIVE_PROFILE.name == "edge"
        assert "Invalid" not in caplog.text

    def test_whitespace_only_no_warning(self, caplog):
        """Whitespace-only should default to edge WITHOUT a warning."""
        import logging
        with caplog.at_level(logging.WARNING):
            settings = _reload_settings(env_value="   ")
        assert settings.ACTIVE_PROFILE.name == "edge"
        assert "Invalid" not in caplog.text

    def test_invalid_value_defaults_to_edge(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            settings = _reload_settings(env_value="invalid_value")
        assert settings.ACTIVE_PROFILE.name == "edge"
        assert "Invalid" in caplog.text or "invalid_value" in caplog.text

    def test_unset_variable_defaults_to_edge(self):
        # Remove the env var entirely
        env_backup = os.environ.pop("TOMATO_MONITOR_PROFILE", None)
        try:
            import src.infrastructure.config.settings as settings_module
            importlib.reload(settings_module)
            assert settings_module.ACTIVE_PROFILE.name == "edge"
        finally:
            if env_backup is not None:
                os.environ["TOMATO_MONITOR_PROFILE"] = env_backup


# ---------------------------------------------------------------------------
# 12B: Verify analysis parameters still present on profiles
# ---------------------------------------------------------------------------


class TestAnalysisParamsPresent:
    """Verify analysis_skip_maturity and inference dimensions are present."""

    def test_edge_has_analysis_skip_maturity(self):
        from src.infrastructure.config.settings import EDGE_PROFILE
        assert hasattr(EDGE_PROFILE, "analysis_skip_maturity")
        assert isinstance(EDGE_PROFILE.analysis_skip_maturity, bool)

    def test_full_has_analysis_skip_maturity(self):
        from src.infrastructure.config.settings import FULL_PROFILE
        assert hasattr(FULL_PROFILE, "analysis_skip_maturity")
        assert isinstance(FULL_PROFILE.analysis_skip_maturity, bool)

    def test_edge_has_inference_dimensions(self):
        from src.infrastructure.config.settings import EDGE_PROFILE
        assert hasattr(EDGE_PROFILE, "inference_input_width")
        assert hasattr(EDGE_PROFILE, "inference_input_height")
        assert EDGE_PROFILE.inference_input_width > 0
        assert EDGE_PROFILE.inference_input_height > 0

    def test_full_has_inference_dimensions(self):
        from src.infrastructure.config.settings import FULL_PROFILE
        assert hasattr(FULL_PROFILE, "inference_input_width")
        assert hasattr(FULL_PROFILE, "inference_input_height")
        assert FULL_PROFILE.inference_input_width > 0
        assert FULL_PROFILE.inference_input_height > 0

    def test_analysis_thermal_thresholds_present(self):
        from src.infrastructure.config.settings import EDGE_PROFILE, FULL_PROFILE
        assert hasattr(EDGE_PROFILE, "analysis_thermal_pause_threshold")
        assert hasattr(EDGE_PROFILE, "analysis_thermal_resume_threshold")
        assert hasattr(FULL_PROFILE, "analysis_thermal_pause_threshold")
        assert hasattr(FULL_PROFILE, "analysis_thermal_resume_threshold")
