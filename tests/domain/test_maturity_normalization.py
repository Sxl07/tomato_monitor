"""Regression tests for maturity normalization for persistence.

A non-estimable maturity estimate (usda_stage="unknown", or any value outside
the six valid USDA stages) must collapse to (None, None) so that persistence
never stores anything the remote Supabase constraint would reject. The six
valid stages must pass through unchanged.
"""

from __future__ import annotations

import pytest

from src.domain.entities.maturity_assessment import (
    VALID_USDA_STAGES,
    normalize_maturity_for_persistence,
)


VALID_STAGES = ["green", "breaker", "turning", "pink", "light_red", "red"]


class TestValidStagesPassThrough:
    @pytest.mark.parametrize("stage", VALID_STAGES)
    def test_valid_stage_and_percent_unchanged(self, stage):
        assert normalize_maturity_for_persistence(stage, 42.0) == (stage, 42.0)

    def test_valid_set_matches_expected(self):
        assert VALID_USDA_STAGES == frozenset(VALID_STAGES)


class TestNonEstimableCollapsesToNull:
    def test_unknown_becomes_none(self):
        assert normalize_maturity_for_persistence("unknown", 0.0) == (None, None)

    def test_none_stage_becomes_none(self):
        assert normalize_maturity_for_persistence(None, 90.0) == (None, None)

    def test_unexpected_label_becomes_none(self):
        assert normalize_maturity_for_persistence("ripe", 100.0) == (None, None)

    def test_empty_string_becomes_none(self):
        assert normalize_maturity_for_persistence("", 10.0) == (None, None)
