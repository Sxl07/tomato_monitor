"""MonitoringStatus value object with state machine transitions.

Implements the monitoring lifecycle as a finite state machine where
transitions are validated against a predefined set of valid moves.
Terminal states (completed, aborted, error) reject all transitions.
"""

from enum import Enum

from src.domain.exceptions import InvalidTransitionError


class MonitoringState(str, Enum):
    """Valid states for a monitoring session."""

    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHING = "finishing"
    COMPLETED = "completed"
    ABORTED = "aborted"
    ERROR = "error"


class MonitoringStatus:
    """Value object representing monitoring status with validated transitions.

    The state machine enforces the following transitions:
        - initializing → running, error
        - running → paused, finishing, aborted, error
        - paused → running, aborted, error
        - finishing → completed, error
        - completed, aborted, error → (no transitions allowed)
    """

    VALID_TRANSITIONS: dict[MonitoringState, set[MonitoringState]] = {
        MonitoringState.INITIALIZING: {
            MonitoringState.RUNNING,
            MonitoringState.ERROR,
        },
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
        MonitoringState.FINISHING: {
            MonitoringState.COMPLETED,
            MonitoringState.ERROR,
        },
        MonitoringState.COMPLETED: set(),
        MonitoringState.ABORTED: set(),
        MonitoringState.ERROR: set(),
    }

    TERMINAL_STATES: set[MonitoringState] = {
        MonitoringState.COMPLETED,
        MonitoringState.ABORTED,
        MonitoringState.ERROR,
    }

    def __init__(self, state: MonitoringState) -> None:
        self._state = state

    @property
    def state(self) -> MonitoringState:
        """Current monitoring state."""
        return self._state

    def transition_to(self, target: MonitoringState) -> "MonitoringStatus":
        """Attempt to transition to the target state.

        Args:
            target: The desired next state.

        Returns:
            A new MonitoringStatus instance with the target state.

        Raises:
            InvalidTransitionError: If the transition is not allowed
                (either from a terminal state or not in valid transitions).
        """
        allowed = self.VALID_TRANSITIONS.get(self._state, set())

        if self.is_terminal():
            raise InvalidTransitionError(
                current_state=self._state.value,
                target_state=target.value,
                allowed_transitions=[],
            )

        if target not in allowed:
            raise InvalidTransitionError(
                current_state=self._state.value,
                target_state=target.value,
                allowed_transitions=[s.value for s in allowed],
            )

        return MonitoringStatus(target)

    def is_terminal(self) -> bool:
        """Check if the current state is a terminal state."""
        return self._state in self.TERMINAL_STATES

    def allowed_transitions(self) -> set[MonitoringState]:
        """Return the set of states reachable from the current state."""
        return self.VALID_TRANSITIONS.get(self._state, set())

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MonitoringStatus):
            return NotImplemented
        return self._state == other._state

    def __hash__(self) -> int:
        return hash(self._state)

    def __repr__(self) -> str:
        return f"MonitoringStatus(state={self._state.value!r})"
