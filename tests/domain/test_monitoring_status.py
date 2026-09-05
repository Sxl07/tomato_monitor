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
    MonitoringState.READY_FOR_ANALYSIS,
    MonitoringState.ANALYZING,
]

# Expected valid transitions per state (source of truth from design).
EXPECTED_TRANSITIONS: dict[MonitoringState, set[MonitoringState]] = {
    MonitoringState.INITIALIZING: {MonitoringState.RUNNING, MonitoringState.ERROR},
    MonitoringState.RUNNING: {
        MonitoringState.PAUSED,
        MonitoringState.FINISHING,
        MonitoringState.ANALYZING,
        MonitoringState.READY_FOR_ANALYSIS,
        MonitoringState.COMPLETED,
        MonitoringState.ABORTED,
        MonitoringState.ERROR,
    },
    MonitoringState.PAUSED: {
        MonitoringState.RUNNING,
        MonitoringState.ABORTED,
        MonitoringState.ERROR,
    },
    MonitoringState.FINISHING: {MonitoringState.COMPLETED, MonitoringState.ERROR},
    MonitoringState.READY_FOR_ANALYSIS: {
        MonitoringState.ANALYZING,
        MonitoringState.ABORTED,
        MonitoringState.ERROR,
    },
    MonitoringState.ANALYZING: {MonitoringState.COMPLETED, MonitoringState.ERROR},
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


# ---------------------------------------------------------------------------
# Test: ANALYZING state — Spec 009 additive changes
# ---------------------------------------------------------------------------


class TestAnalyzingState:
    """Tests specific to the new ANALYZING state (Spec 009).

    Verifies that ANALYZING was added additively without breaking
    existing PAUSED and FINISHING transitions.
    """

    def test_running_to_analyzing_succeeds(self):
        """running → analyzing is a valid transition (finalize capture)."""
        status = MonitoringStatus(MonitoringState.RUNNING)
        result = status.transition_to(MonitoringState.ANALYZING)
        assert result.state == MonitoringState.ANALYZING

    def test_running_to_completed_succeeds(self):
        """running → completed is valid (0 snapshots case)."""
        status = MonitoringStatus(MonitoringState.RUNNING)
        result = status.transition_to(MonitoringState.COMPLETED)
        assert result.state == MonitoringState.COMPLETED

    def test_analyzing_to_completed_succeeds(self):
        """analyzing → completed is valid (analysis finished)."""
        status = MonitoringStatus(MonitoringState.ANALYZING)
        result = status.transition_to(MonitoringState.COMPLETED)
        assert result.state == MonitoringState.COMPLETED

    def test_analyzing_to_error_succeeds(self):
        """analyzing → error is valid (irrecoverable analysis failure)."""
        status = MonitoringStatus(MonitoringState.ANALYZING)
        result = status.transition_to(MonitoringState.ERROR)
        assert result.state == MonitoringState.ERROR

    def test_analyzing_to_aborted_raises(self):
        """analyzing → aborted is NOT valid (can't cancel during analysis)."""
        status = MonitoringStatus(MonitoringState.ANALYZING)
        with pytest.raises(InvalidTransitionError) as exc_info:
            status.transition_to(MonitoringState.ABORTED)
        assert exc_info.value.current_state == "analyzing"
        assert exc_info.value.target_state == "aborted"

    def test_analyzing_to_running_raises(self):
        """analyzing → running is NOT valid (no going back)."""
        status = MonitoringStatus(MonitoringState.ANALYZING)
        with pytest.raises(InvalidTransitionError):
            status.transition_to(MonitoringState.RUNNING)

    def test_analyzing_is_not_terminal(self):
        """ANALYZING is an active state, not terminal."""
        status = MonitoringStatus(MonitoringState.ANALYZING)
        assert status.is_terminal() is False

    def test_analyzing_allowed_transitions(self):
        """ANALYZING allows only completed and error."""
        status = MonitoringStatus(MonitoringState.ANALYZING)
        assert status.allowed_transitions() == {
            MonitoringState.COMPLETED,
            MonitoringState.ERROR,
        }

    # --- Backward compatibility: PAUSED and FINISHING still work ---

    def test_running_to_paused_still_works(self):
        """running → paused remains valid (backward compatibility)."""
        status = MonitoringStatus(MonitoringState.RUNNING)
        result = status.transition_to(MonitoringState.PAUSED)
        assert result.state == MonitoringState.PAUSED

    def test_running_to_finishing_still_works(self):
        """running → finishing remains valid (backward compatibility)."""
        status = MonitoringStatus(MonitoringState.RUNNING)
        result = status.transition_to(MonitoringState.FINISHING)
        assert result.state == MonitoringState.FINISHING

    def test_paused_to_running_still_works(self):
        """paused → running remains valid (backward compatibility)."""
        status = MonitoringStatus(MonitoringState.PAUSED)
        result = status.transition_to(MonitoringState.RUNNING)
        assert result.state == MonitoringState.RUNNING

    def test_finishing_to_completed_still_works(self):
        """finishing → completed remains valid (backward compatibility)."""
        status = MonitoringStatus(MonitoringState.FINISHING)
        result = status.transition_to(MonitoringState.COMPLETED)
        assert result.state == MonitoringState.COMPLETED

    def test_completed_to_analyzing_raises(self):
        """Terminal state completed cannot transition to analyzing."""
        status = MonitoringStatus(MonitoringState.COMPLETED)
        with pytest.raises(InvalidTransitionError):
            status.transition_to(MonitoringState.ANALYZING)


# ---------------------------------------------------------------------------
# Test: READY_FOR_ANALYSIS state — Spec 020 (deferred manual analysis)
# ---------------------------------------------------------------------------


class TestReadyForAnalysisState:
    """Tests for the new READY_FOR_ANALYSIS state (Spec 020).

    Video-first finalize transitions running -> ready_for_analysis (no auto
    analysis). ready_for_analysis is a non-terminal, active state that only
    transitions to analyzing (manual start), aborted (explicit cancel), or
    error. Added additively without breaking existing transitions.
    """

    def test_running_to_ready_for_analysis_succeeds(self):
        """running -> ready_for_analysis is valid (video-first finalize)."""
        status = MonitoringStatus(MonitoringState.RUNNING)
        result = status.transition_to(MonitoringState.READY_FOR_ANALYSIS)
        assert result.state == MonitoringState.READY_FOR_ANALYSIS

    def test_ready_for_analysis_to_analyzing_succeeds(self):
        """ready_for_analysis -> analyzing is valid (manual start)."""
        status = MonitoringStatus(MonitoringState.READY_FOR_ANALYSIS)
        result = status.transition_to(MonitoringState.ANALYZING)
        assert result.state == MonitoringState.ANALYZING

    def test_ready_for_analysis_to_aborted_succeeds(self):
        """ready_for_analysis -> aborted is valid (explicit cancellation)."""
        status = MonitoringStatus(MonitoringState.READY_FOR_ANALYSIS)
        result = status.transition_to(MonitoringState.ABORTED)
        assert result.state == MonitoringState.ABORTED

    def test_ready_for_analysis_to_error_succeeds(self):
        """ready_for_analysis -> error is valid (video missing/corrupt on start)."""
        status = MonitoringStatus(MonitoringState.READY_FOR_ANALYSIS)
        result = status.transition_to(MonitoringState.ERROR)
        assert result.state == MonitoringState.ERROR

    def test_ready_for_analysis_to_running_raises(self):
        """ready_for_analysis -> running is NOT valid."""
        status = MonitoringStatus(MonitoringState.READY_FOR_ANALYSIS)
        with pytest.raises(InvalidTransitionError):
            status.transition_to(MonitoringState.RUNNING)

    def test_ready_for_analysis_to_completed_raises(self):
        """ready_for_analysis -> completed is NOT a direct edge (must analyze)."""
        status = MonitoringStatus(MonitoringState.READY_FOR_ANALYSIS)
        with pytest.raises(InvalidTransitionError):
            status.transition_to(MonitoringState.COMPLETED)

    def test_ready_for_analysis_to_paused_raises(self):
        """ready_for_analysis -> paused is NOT valid (not a pause state)."""
        status = MonitoringStatus(MonitoringState.READY_FOR_ANALYSIS)
        with pytest.raises(InvalidTransitionError):
            status.transition_to(MonitoringState.PAUSED)

    def test_ready_for_analysis_is_not_terminal(self):
        """READY_FOR_ANALYSIS is an active, non-terminal state."""
        status = MonitoringStatus(MonitoringState.READY_FOR_ANALYSIS)
        assert status.is_terminal() is False

    def test_ready_for_analysis_allowed_transitions(self):
        """READY_FOR_ANALYSIS allows exactly {analyzing, aborted, error}."""
        status = MonitoringStatus(MonitoringState.READY_FOR_ANALYSIS)
        assert status.allowed_transitions() == {
            MonitoringState.ANALYZING,
            MonitoringState.ABORTED,
            MonitoringState.ERROR,
        }

    def test_running_to_analyzing_still_valid_for_legacy(self):
        """running -> analyzing remains valid (capture-first legacy + reprocess)."""
        status = MonitoringStatus(MonitoringState.RUNNING)
        result = status.transition_to(MonitoringState.ANALYZING)
        assert result.state == MonitoringState.ANALYZING

    def test_completed_to_analyzing_not_an_fsm_edge(self):
        """No completed -> analyzing FSM edge (reprocess uses controlled reset)."""
        status = MonitoringStatus(MonitoringState.COMPLETED)
        with pytest.raises(InvalidTransitionError):
            status.transition_to(MonitoringState.ANALYZING)

    def test_error_to_analyzing_not_an_fsm_edge(self):
        """No error -> analyzing FSM edge (reprocess uses controlled reset)."""
        status = MonitoringStatus(MonitoringState.ERROR)
        with pytest.raises(InvalidTransitionError):
            status.transition_to(MonitoringState.ANALYZING)
