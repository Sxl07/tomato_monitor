"""Tests for maturity_stats (Spec 024, Block 2)."""

from dataclasses import dataclass
from typing import Optional

import pytest

from src.application.dtos.dashboard_analytics_dtos import MaturityCounts
from src.application.services import maturity_stats


@dataclass
class FakeResult:
    """Minimal stand-in for DetectionInspectionResult (only maturity_stage)."""

    maturity_stage: Optional[str] = None


def _results(**stage_counts) -> list[FakeResult]:
    out: list[FakeResult] = []
    for stage, n in stage_counts.items():
        out.extend(FakeResult(maturity_stage=stage) for _ in range(n))
    return out


class TestCountsFromResults:
    def test_counts_by_stage(self):
        results = _results(green=2, turning=3, red=1)
        c = maturity_stats.counts_from_results(results)
        assert c.green == 2 and c.turning == 3 and c.red == 1
        assert c.covered == 6

    def test_null_and_unknown_stages_are_uncovered(self):
        results = [
            FakeResult(maturity_stage="green"),
            FakeResult(maturity_stage=None),
            FakeResult(maturity_stage="not_a_stage"),
        ]
        c = maturity_stats.counts_from_results(results)
        assert c.covered == 1  # only the green one

    def test_partial_coverage_like_monitoring_55(self):
        # 16 fruits, only 10 classifiable -> covered == 10 (6 null)
        results = _results(
            green=2, breaker=1, turning=3, pink=2, light_red=1, red=1
        ) + [FakeResult(maturity_stage=None) for _ in range(6)]
        c = maturity_stats.counts_from_results(results)
        assert c.covered == 10

    def test_empty(self):
        assert maturity_stats.counts_from_results([]).covered == 0


class TestDistribution:
    def test_distribution_over_covered(self):
        c = MaturityCounts(green=5, red=5)
        d = maturity_stats.distribution_pcts(c)
        assert d["green"] == pytest.approx(50.0)
        assert d["red"] == pytest.approx(50.0)
        assert sum(d.values()) == pytest.approx(100.0)

    def test_distribution_zero_coverage(self):
        d = maturity_stats.distribution_pcts(MaturityCounts())
        assert all(v == 0.0 for v in d.values())


class TestPredominant:
    def test_single_predominant(self):
        c = MaturityCounts(green=2, turning=5, red=1)
        assert maturity_stats.predominant_stages(c) == ["turning"]

    def test_tie_returns_list_in_phenological_order(self):
        c = MaturityCounts(turning=3, pink=3)
        assert maturity_stats.predominant_stages(c) == ["turning", "pink"]

    def test_three_way_tie(self):
        c = MaturityCounts(green=2, pink=2, red=2)
        assert maturity_stats.predominant_stages(c) == ["green", "pink", "red"]

    def test_no_coverage_empty(self):
        assert maturity_stats.predominant_stages(MaturityCounts()) == []


class TestHarvestableShare:
    def test_share_denominator_is_covered(self):
        # covered=10; light_red+red = 2 -> 0.2, NOT over total_tomatoes
        c = MaturityCounts(green=2, breaker=1, turning=3, pink=2, light_red=1, red=1)
        assert maturity_stats.harvestable_share(c) == pytest.approx(0.2)

    def test_share_none_when_no_coverage(self):
        assert maturity_stats.harvestable_share(MaturityCounts()) is None

    def test_share_all_harvestable(self):
        c = MaturityCounts(light_red=3, red=7)
        assert maturity_stats.harvestable_share(c) == pytest.approx(1.0)


class TestAddCounts:
    def test_add(self):
        a = MaturityCounts(green=1, red=2)
        b = MaturityCounts(green=3, turning=4)
        s = maturity_stats.add_counts(a, b)
        assert s.green == 4 and s.turning == 4 and s.red == 2


class TestFallback:
    def test_fallback_builds_visual_distribution(self):
        f = maturity_stats.fallback_from_pcts(10, 0, 20, 0, 30, 40)
        assert f.pct_green == 10 and f.pct_red == 40
