"""Property-based tests for the READY_FOR_ANALYSIS FSM state (Spec 020).

Testing framework: pytest + hypothesis
Minimum examples: 100 per property

# Feature: 020-deferred-manual-analysis-workflow, Property 1
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.domain.exceptions import InvalidTransitionError
from src.domain.value_objects.monitoring_status import MonitoringState, MonitoringStatus

ALLOWED_FROM_READY = {
    MonitoringState.ANALYZING,
    MonitoringState.ABORTED,
    MonitoringState.ERROR,
}


@settings(max_examples=200)
@given(target=st.sampled_from(list(MonitoringState)))
def test_property_1_ready_for_analysis_only_transitions_to_allowed(target):
    """Property 1: ready_for_analysis is non-terminal and transitions ONLY to
    {analyzing, aborted, error}; every other target raises InvalidTransitionError.

    # Feature: 020-deferred-manual-analysis-workflow, Property 1
    """
    status = MonitoringStatus(MonitoringState.READY_FOR_ANALYSIS)

    assert status.is_terminal() is False
    assert status.allowed_transitions() == ALLOWED_FROM_READY

    if target in ALLOWED_FROM_READY:
        result = status.transition_to(target)
        assert result.state == target
        # Original instance is not mutated.
        assert status.state == MonitoringState.READY_FOR_ANALYSIS
    else:
        with pytest.raises(InvalidTransitionError) as exc_info:
            status.transition_to(target)
        assert exc_info.value.current_state == MonitoringState.READY_FOR_ANALYSIS.value
        assert exc_info.value.target_state == target.value
