"""Property-based test: undervoltage bit semantics (Spec 020).

Testing framework: pytest + hypothesis
Minimum examples: 100 per property

# Feature: 020-deferred-manual-analysis-workflow, Property 4
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from src.infrastructure.monitoring.undervoltage_probe import (
    UndervoltagePresenceProbe,
    UndervoltageStatus,
    UNDERVOLTAGE_NOW_BIT,
)


@settings(max_examples=300)
@given(mask=st.integers(min_value=0, max_value=0xFFFFFFFF))
def test_property_4_present_iff_bit0(mask):
    """Property 4: PRESENT iff bit 0 set; bit 16 (historical) never causes PRESENT.

    # Feature: 020-deferred-manual-analysis-workflow, Property 4
    """
    status = UndervoltagePresenceProbe.interpret(mask)
    if mask & UNDERVOLTAGE_NOW_BIT:
        assert status == UndervoltageStatus.PRESENT
    else:
        assert status == UndervoltageStatus.ABSENT


@settings(max_examples=50)
@given(dummy=st.integers())
def test_property_4_none_is_unavailable_never_present(dummy):
    """A None reading is always UNAVAILABLE and never PRESENT.

    # Feature: 020-deferred-manual-analysis-workflow, Property 4
    """
    status = UndervoltagePresenceProbe.interpret(None)
    assert status == UndervoltageStatus.UNAVAILABLE
    assert status != UndervoltageStatus.PRESENT
