"""Unit tests for time-based snapshot decision logic.

Tests verify:
- Time-based cooldown blocks premature snapshots.
- Time-based timeout forces snapshot with reason "timeout".
- First-wins logic (time OR frame threshold triggers).
- First frame always captures with reason "first_frame".

These test the pure decision logic that will be integrated into MonitoringWorker.
The logic is extracted here as pure functions for testability.
"""

import pytest


def compute_snapshot_decision(
    *,
    is_first_frame: bool,
    scene_gate_triggered: bool,
    elapsed_since_last_snapshot: float,
    frames_since_last_capture: int,
    min_seconds_between_snapshots: float,
    max_seconds_without_snapshot: float,
    cooldown_frames: int,
    timeout_frames: int,
) -> tuple[bool, str]:
    """Pure function implementing the combined time+frame snapshot decision.

    Returns (should_capture, snapshot_reason).

    Logic:
    - First frame: always capture → "first_frame"
    - Time-based timeout OR frame-based timeout: force → "timeout"
    - Time-based cooldown AND frame-based cooldown both met + scene change: → "scene_change"
    - Otherwise: don't capture → ""
    """
    if is_first_frame:
        return (True, "first_frame")

    # Timeout: either time-based or frame-based (first wins)
    time_timeout = elapsed_since_last_snapshot >= max_seconds_without_snapshot
    frame_timeout = frames_since_last_capture >= timeout_frames
    if time_timeout or frame_timeout:
        return (True, "timeout")

    # Cooldown check: both must be satisfied
    time_cooldown_ok = elapsed_since_last_snapshot >= min_seconds_between_snapshots
    frame_cooldown_ok = frames_since_last_capture >= cooldown_frames

    # Either cooldown being met is enough (first wins for enabling)
    cooldown_passed = time_cooldown_ok or frame_cooldown_ok

    if cooldown_passed and scene_gate_triggered:
        return (True, "scene_change")

    return (False, "")


class TestFirstFrame:
    """First frame always captures regardless of other params."""

    def test_first_frame_always_captures(self):
        should, reason = compute_snapshot_decision(
            is_first_frame=True,
            scene_gate_triggered=False,
            elapsed_since_last_snapshot=0.0,
            frames_since_last_capture=0,
            min_seconds_between_snapshots=8.0,
            max_seconds_without_snapshot=30.0,
            cooldown_frames=18,
            timeout_frames=45,
        )
        assert should is True
        assert reason == "first_frame"

    def test_first_frame_ignores_cooldown(self):
        should, reason = compute_snapshot_decision(
            is_first_frame=True,
            scene_gate_triggered=False,
            elapsed_since_last_snapshot=0.0,
            frames_since_last_capture=0,
            min_seconds_between_snapshots=999.0,
            max_seconds_without_snapshot=999.0,
            cooldown_frames=999,
            timeout_frames=999,
        )
        assert should is True
        assert reason == "first_frame"


class TestTimeBasedCooldown:
    """Time-based cooldown blocks premature snapshots."""

    def test_cooldown_not_met_blocks_scene_change(self):
        should, reason = compute_snapshot_decision(
            is_first_frame=False,
            scene_gate_triggered=True,
            elapsed_since_last_snapshot=2.0,  # < 8.0
            frames_since_last_capture=5,  # < 18
            min_seconds_between_snapshots=8.0,
            max_seconds_without_snapshot=30.0,
            cooldown_frames=18,
            timeout_frames=45,
        )
        assert should is False

    def test_time_cooldown_met_allows_scene_change(self):
        should, reason = compute_snapshot_decision(
            is_first_frame=False,
            scene_gate_triggered=True,
            elapsed_since_last_snapshot=10.0,  # >= 8.0
            frames_since_last_capture=5,  # < 18 frames, but time cooldown met
            min_seconds_between_snapshots=8.0,
            max_seconds_without_snapshot=30.0,
            cooldown_frames=18,
            timeout_frames=45,
        )
        assert should is True
        assert reason == "scene_change"

    def test_frame_cooldown_met_allows_scene_change(self):
        should, reason = compute_snapshot_decision(
            is_first_frame=False,
            scene_gate_triggered=True,
            elapsed_since_last_snapshot=2.0,  # < 8.0 time
            frames_since_last_capture=20,  # >= 18 frames
            min_seconds_between_snapshots=8.0,
            max_seconds_without_snapshot=30.0,
            cooldown_frames=18,
            timeout_frames=45,
        )
        assert should is True
        assert reason == "scene_change"


class TestTimeBasedTimeout:
    """Time-based timeout forces snapshot with reason "timeout"."""

    def test_time_timeout_forces_capture(self):
        should, reason = compute_snapshot_decision(
            is_first_frame=False,
            scene_gate_triggered=False,
            elapsed_since_last_snapshot=35.0,  # >= 30.0
            frames_since_last_capture=10,  # < 45 frames
            min_seconds_between_snapshots=8.0,
            max_seconds_without_snapshot=30.0,
            cooldown_frames=18,
            timeout_frames=45,
        )
        assert should is True
        assert reason == "timeout"

    def test_frame_timeout_forces_capture(self):
        should, reason = compute_snapshot_decision(
            is_first_frame=False,
            scene_gate_triggered=False,
            elapsed_since_last_snapshot=5.0,  # < 30.0 time
            frames_since_last_capture=50,  # >= 45 frames
            min_seconds_between_snapshots=8.0,
            max_seconds_without_snapshot=30.0,
            cooldown_frames=18,
            timeout_frames=45,
        )
        assert should is True
        assert reason == "timeout"


class TestFirstWinsRule:
    """Whichever threshold (time or frame) is reached first triggers."""

    def test_time_wins_over_frames_for_timeout(self):
        should, reason = compute_snapshot_decision(
            is_first_frame=False,
            scene_gate_triggered=False,
            elapsed_since_last_snapshot=31.0,  # time timeout met
            frames_since_last_capture=10,  # frame timeout NOT met
            min_seconds_between_snapshots=8.0,
            max_seconds_without_snapshot=30.0,
            cooldown_frames=18,
            timeout_frames=45,
        )
        assert should is True
        assert reason == "timeout"

    def test_frames_wins_over_time_for_timeout(self):
        should, reason = compute_snapshot_decision(
            is_first_frame=False,
            scene_gate_triggered=False,
            elapsed_since_last_snapshot=5.0,  # time timeout NOT met
            frames_since_last_capture=46,  # frame timeout met
            min_seconds_between_snapshots=8.0,
            max_seconds_without_snapshot=30.0,
            cooldown_frames=18,
            timeout_frames=45,
        )
        assert should is True
        assert reason == "timeout"

    def test_no_trigger_when_nothing_met(self):
        should, reason = compute_snapshot_decision(
            is_first_frame=False,
            scene_gate_triggered=False,
            elapsed_since_last_snapshot=5.0,
            frames_since_last_capture=10,
            min_seconds_between_snapshots=8.0,
            max_seconds_without_snapshot=30.0,
            cooldown_frames=18,
            timeout_frames=45,
        )
        assert should is False
        assert reason == ""

    def test_scene_change_without_any_cooldown_met(self):
        should, reason = compute_snapshot_decision(
            is_first_frame=False,
            scene_gate_triggered=True,
            elapsed_since_last_snapshot=1.0,  # < 8.0
            frames_since_last_capture=2,  # < 18
            min_seconds_between_snapshots=8.0,
            max_seconds_without_snapshot=30.0,
            cooldown_frames=18,
            timeout_frames=45,
        )
        assert should is False
