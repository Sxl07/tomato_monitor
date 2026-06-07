"""Unit tests for MonitoringStatus state machine.

Exhaustive testing of all valid/invalid transitions, terminal state behavior,
error transitions, is_terminal(), and allowed_transitions().
"""

import pytest

from src.domain.exceptions import InvalidTransitionError
from src.domain.value_objects.monitoring_status import MonitoringState, MonitoringStatus


# ---------------------------------------------------------------------------
# Fixtures and constants
# ---------------------------------------------------------------------------

ALL_STATES = list(MonitoringState)

TERMINAL_STATES = [
    MonitoringState.COMPLETED,
    MonitoringState.ABORTED,
    MonitoringState.ERROR,
]

NON_TERMINAL_STATES = [
    MonitoringState.INITIALIZING,
    MonitoringState.RUNNING,
    MonitoringState.PAUSED,
    MonitoringState.FINISHING,
]

# Expected valid transitions per state (source of truth from design).
EXPECTED_TRANSITIONS: dict[MonitoringState, set[MonitoringState]] = {
    MonitoringState.INITIALIZING: {MonitoringState.RUNNING, MonitoringState.ERROR},
    MonitoringState.RUNNING: {
        MonitoringState.PAUSED,
        MonitoringState.FINISHING,
        MonitoringState.ABORTED,
        MonitoringState.ERROR,
    },
    MonitoringState.PAUSED: {
        MonitoringState.RUNNING,
        MonitoringState.ABORTED,
        MonitoringState.ERROR,
    },
    MonitoringState.FINISHING: {MonitoringState.COMPLETED, MonitoringState.ERROR},
    MonitoringState.COMPLETED: set(),
    MonitoringState.ABORTED: set(),
    MonitoringState.ERROR: set(),
}


# ---------------------------------------------------------------------------
# Test: all valid transitions succeed
# ---------------------------------------------------------------------------


class TestValidTransitions:
    """Verify that every valid (current, target) pair produces a new status."""

    @pytest.mark.parametrize(
        "current,target",
        [
            (current, target)
            for current, targets in EXPECTED_TRANSITIONS.items()
            for target in targets
        ],
        ids=lambda pair: f"{pair}" if isinstance(pair, MonitoringState) else None,
    )
    def test_valid_transition_succeeds(self, current: MonitoringState, target: MonitoringState):
        status = MonitoringStatus(current)
        result = status.transition_to(target)

        assert isinstance(result, MonitoringStatus)
        assert result.state == target

    @pytest.mark.parametrize(
        "current,target",
        [
            (current, target)
            for current, targets in EXPECTED_TRANSITIONS.items()
            for target in targets
        ],
    )
    def test_valid_transition_returns_new_instance(
        self, current: MonitoringState, target: MonitoringState
    ):
        """transition_to returns a NEW MonitoringStatus, not mutating the original."""
        status = MonitoringStatus(current)
        result = status.transition_to(target)

        assert result is not status
        assert status.state == current  # Original unchanged


# ---------------------------------------------------------------------------
# Test: all invalid transitions raise InvalidTransitionError
# ---------------------------------------------------------------------------


class TestInvalidTransitions:
    """Verify that every invalid (current, target) pair raises InvalidTransitionError."""

    @pytest.mark.parametrize(
        "current,target",
        [
            (current, target)
            for current in ALL_STATES
            for target in ALL_STATES
            if target not in EXPECTED_TRANSITIONS[current]
        ],
    )
    def test_invalid_transition_raises(self, current: MonitoringState, target: MonitoringState):
        status = MonitoringStatus(current)

        with pytest.raises(InvalidTransitionError) as exc_info:
            status.transition_to(target)

        err = exc_info.value
        assert err.current_state == current.value
        assert err.target_state == target.value

    @pytest.mark.parametrize(
        "current,target",
        [
            (current, target)
            for current in NON_TERMINAL_STATES
            for target in ALL_STATES
            if target not in EXPECTED_TRANSITIONS[current]
        ],
    )
    def test_invalid_transition_reports_allowed(
        self, current: MonitoringState, target: MonitoringState
    ):
        """InvalidTransitionError includes the list of allowed transitions."""
        status = MonitoringStatus(current)

        with pytest.raises(InvalidTransitionError) as exc_info:
            status.transition_to(target)

        err = exc_info.value
        expected_allowed = sorted(s.value for s in EXPECTED_TRANSITIONS[current])
        assert sorted(err.allowed_transitions) == expected_allowed


# ---------------------------------------------------------------------------
# Test: terminal states reject ALL transitions
# ---------------------------------------------------------------------------


class TestTerminalStates:
    """Terminal states (completed, aborted, error) reject ALL transition attempts."""

    @pytest.mark.parametrize("terminal", TERMINAL_STATES)
    @pytest.mark.parametrize("target", ALL_STATES)
    def test_terminal_rejects_all_targets(
        self, terminal: MonitoringState, target: MonitoringState
    ):
        status = MonitoringStatus(terminal)

        with pytest.raises(InvalidTransitionError) as exc_info:
            status.transition_to(target)

        err = exc_info.value
        assert err.current_state == terminal.value
        assert err.target_state == target.value
        assert err.allowed_transitions == []


# ---------------------------------------------------------------------------
# Test: error transition from all non-terminal states succeeds
# ---------------------------------------------------------------------------


class TestErrorTransition:
    """Every non-terminal state can transition to ERROR."""

    @pytest.mark.parametrize("state", NON_TERMINAL_STATES)
    def test_non_terminal_to_error_succeeds(self, state: MonitoringState):
        status = MonitoringStatus(state)
        result = status.transition_to(MonitoringState.ERROR)

        assert result.state == MonitoringState.ERROR


# ---------------------------------------------------------------------------
# Test: is_terminal() correctness
# ---------------------------------------------------------------------------


class TestIsTerminal:
    """is_terminal() returns True for terminal states, False otherwise."""

    @pytest.mark.parametrize("state", TERMINAL_STATES)
    def test_terminal_state_returns_true(self, state: MonitoringState):
        status = MonitoringStatus(state)
        assert status.is_terminal() is True

    @pytest.mark.parametrize("state", NON_TERMINAL_STATES)
    def test_non_terminal_state_returns_false(self, state: MonitoringState):
        status = MonitoringStatus(state)
        assert status.is_terminal() is False


# ---------------------------------------------------------------------------
# Test: allowed_transitions() correctness
# ---------------------------------------------------------------------------


class TestAllowedTransitions:
    """allowed_transitions() returns the correct set for each state."""

    @pytest.mark.parametrize("state", ALL_STATES)
    def test_allowed_transitions_matches_expected(self, state: MonitoringState):
        status = MonitoringStatus(state)
        result = status.allowed_transitions()

        assert result == EXPECTED_TRANSITIONS[state]

    @pytest.mark.parametrize("state", TERMINAL_STATES)
    def test_terminal_has_empty_allowed(self, state: MonitoringState):
        status = MonitoringStatus(state)
        assert status.allowed_transitions() == set()
