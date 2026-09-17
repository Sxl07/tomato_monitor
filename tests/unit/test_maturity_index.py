"""Tests for maturity_index (Spec 024, Block 3)."""

import pytest

from src.application.dtos.dashboard_analytics_dtos import MaturityCounts
from src.application.services import maturity_stats
from src.application.services.maturity_index import maturity_index


def test_all_green_is_zero():
    assert maturity_index(MaturityCounts(green=10)) == pytest.approx(0.0)


def test_all_red_is_one():
    assert maturity_index(MaturityCounts(red=7)) == pytest.approx(1.0)


def test_design_example_044():
    # design.md §6.4: (2,1,3,2,1,1), covered=10 -> MI=0.44
    c = MaturityCounts(green=2, breaker=1, turning=3, pink=2, light_red=1, red=1)
    assert maturity_index(c) == pytest.approx(0.44)


def test_index_and_harvestable_share_are_distinct():
    # Same example: MI=0.44 but harvestable_share=0.20
    c = MaturityCounts(green=2, breaker=1, turning=3, pink=2, light_red=1, red=1)
    assert maturity_index(c) == pytest.approx(0.44)
    assert maturity_stats.harvestable_share(c) == pytest.approx(0.20)
    assert maturity_index(c) != maturity_stats.harvestable_share(c)


def test_zero_coverage_is_none():
    assert maturity_index(MaturityCounts()) is None
