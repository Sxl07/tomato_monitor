"""Smoke test: verify app starts correctly with both execution profiles.

Run with: python scripts/smoke_test_profiles.py

Tests that TOMATO_MONITOR_PROFILE="edge" and TOMATO_MONITOR_PROFILE="full"
both produce a valid ACTIVE_PROFILE with all required fields.

Uses subprocess to test each profile value in isolation (env var is read
at module import time, so a fresh process is needed per value).

Validates: Requirements 4.11
"""

import os
import subprocess
import sys
import textwrap

# Ensure project root is in path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# The validation script that runs inside each subprocess
_VALIDATION_SCRIPT = textwrap.dedent("""\
    import os
    import sys
    import types

    sys.path.insert(0, os.environ["PROJECT_ROOT"])

    # Provide a minimal torch stub if torch is not installed (e.g., dev machine
    # without GPU dependencies). The profile logic does not use torch directly.
    if "torch" not in sys.modules:
        try:
            import torch  # noqa: F401
        except ImportError:
            sys.modules["torch"] = types.ModuleType("torch")

    from src.infrastructure.config.settings import ACTIVE_PROFILE, ExecutionProfile

    # Verify ACTIVE_PROFILE is an ExecutionProfile instance
    assert isinstance(ACTIVE_PROFILE, ExecutionProfile), (
        f"ACTIVE_PROFILE is not an ExecutionProfile: {type(ACTIVE_PROFILE)}"
    )

    # Verify the profile name matches the expected value
    expected_name = os.environ.get("EXPECTED_PROFILE_NAME")
    assert ACTIVE_PROFILE.name == expected_name, (
        f"Expected profile name '{expected_name}', got '{ACTIVE_PROFILE.name}'"
    )

    # Verify all required fields are present and have valid types
    required_fields = {
        "name": str,
        "camera_width": int,
        "camera_height": int,
        "camera_fps": int,
        "capture_loop_fps": float,
        "min_seconds_between_snapshots": float,
        "max_seconds_without_snapshot": float,
        "gate_resolution": tuple,
        "scene_gate_cooldown_frames": int,
        "scene_gate_timeout_frames": int,
        "scene_gate_orb_threshold": int,
        "scene_gate_hsv_threshold": float,
        "inference_input_width": int,
        "inference_input_height": int,
        "skip_maturity": bool,
        "detection_score_threshold": float,
        "run_maturity_only_for_healthy": bool,
        "thermal_poll_interval_seconds": float,
        "thermal_warning_temp": float,
        "thermal_critical_temp": float,
        "thermal_resume_temp": float,
        "memory_warning_rss_mb": int,
    }

    for field_name, field_type in required_fields.items():
        value = getattr(ACTIVE_PROFILE, field_name, None)
        assert value is not None, f"Field '{field_name}' is None"
        assert isinstance(value, field_type), (
            f"Field '{field_name}' expected {field_type.__name__}, "
            f"got {type(value).__name__}: {value}"
        )

    # Verify numeric fields are positive where expected
    positive_fields = [
        "camera_width", "camera_height", "camera_fps",
        "capture_loop_fps", "min_seconds_between_snapshots",
        "max_seconds_without_snapshot", "inference_input_width",
        "inference_input_height", "detection_score_threshold",
        "thermal_poll_interval_seconds", "thermal_warning_temp",
        "thermal_critical_temp", "thermal_resume_temp",
        "memory_warning_rss_mb",
    ]
    for field_name in positive_fields:
        value = getattr(ACTIVE_PROFILE, field_name)
        assert value > 0, f"Field '{field_name}' should be positive, got {value}"

    # Verify gate_resolution is a 2-tuple of positive ints
    gr = ACTIVE_PROFILE.gate_resolution
    assert len(gr) == 2, f"gate_resolution should have 2 elements, got {len(gr)}"
    assert all(isinstance(v, int) and v > 0 for v in gr), (
        f"gate_resolution elements should be positive ints, got {gr}"
    )

    print(f"PASS: profile='{ACTIVE_PROFILE.name}' validated successfully")
""")


def run_profile_check(profile_value: str, expected_name: str) -> bool:
    """Run validation script in a subprocess with the given profile env var."""
    env = os.environ.copy()
    env["TOMATO_MONITOR_PROFILE"] = profile_value
    env["EXPECTED_PROFILE_NAME"] = expected_name
    env["PROJECT_ROOT"] = PROJECT_ROOT

    result = subprocess.run(
        [sys.executable, "-c", _VALIDATION_SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode == 0:
        print(f"  ✅ TOMATO_MONITOR_PROFILE='{profile_value}' → {result.stdout.strip()}")
        return True
    else:
        print(f"  ❌ TOMATO_MONITOR_PROFILE='{profile_value}' → FAILED")
        if result.stdout.strip():
            print(f"     stdout: {result.stdout.strip()}")
        if result.stderr.strip():
            # Show only the assertion error, not full traceback
            stderr_lines = result.stderr.strip().split("\n")
            for line in stderr_lines:
                if "AssertionError" in line or "Error" in line:
                    print(f"     {line.strip()}")
                    break
            else:
                print(f"     stderr: {stderr_lines[-1].strip()}")
        return False


def main():
    print("=" * 60)
    print("Smoke Test: App starts with both profiles")
    print("=" * 60)
    print()

    all_passed = True

    # Test 1: TOMATO_MONITOR_PROFILE="edge" → selects edge profile
    print("[1/2] Testing TOMATO_MONITOR_PROFILE='edge'...")
    if not run_profile_check("edge", "edge"):
        all_passed = False

    print()

    # Test 2: TOMATO_MONITOR_PROFILE="full" → selects full profile
    print("[2/2] Testing TOMATO_MONITOR_PROFILE='full'...")
    if not run_profile_check("full", "full"):
        all_passed = False

    print()
    print("=" * 60)

    if all_passed:
        print("✅ SMOKE TEST PASSED: Both profiles start correctly.")
        sys.exit(0)
    else:
        print("❌ SMOKE TEST FAILED: One or more profiles failed validation.")
        sys.exit(1)


if __name__ == "__main__":
    main()
