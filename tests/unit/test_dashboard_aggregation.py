"""Tests for dashboard_aggregation (Spec 024, Block 4)."""

from datetime import datetime

import pytest

from src.application.dtos.dashboard_analytics_dtos import (
    MaturityCounts,
    MaturityDistributionFallback,
    ModuleScopeData,
    ModuleValidMonitoring,
    ScopeData,
)
from src.application.services import dashboard_aggregation as agg


def _mvm(mid, day, total, healthy, unhealthy, counts=None, fallback=None):
    return ModuleValidMonitoring(
        monitoring_id=mid,
        started_at=datetime(2025, 6, day),
        total_tomatoes=total,
        healthy_count=healthy,
        unhealthy_count=unhealthy,
        maturity_counts=counts,
        maturity_fallback=fallback,
    )


def _module_scope(module):
    return ScopeData(
        greenhouse_id=1,
        greenhouse_name="GH1",
        scope_kind="module",
        selected_module_id=module.module_id,
        modules=[module],
    )


class TestLastAndPrevValid:
    def test_last_valid_is_most_recent(self):
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 8, 2), _mvm(2, 5, 20, 15, 5)])
        assert agg.last_valid(m).monitoring_id == 2

    def test_prev_valid(self):
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 8, 2), _mvm(2, 5, 20, 15, 5)])
        assert agg.prev_valid(m).monitoring_id == 1

    def test_none_when_empty(self):
        m = ModuleScopeData(1, "M1", [])
        assert agg.last_valid(m) is None
        assert agg.prev_valid(m) is None


class TestModuleKpis:
    def test_fruits_uses_last_valid_not_historical_sum(self):
        # Historical sum would be 10+20+30=60; last valid is 30.
        m = ModuleScopeData(1, "M1", [
            _mvm(1, 1, 10, 8, 2),
            _mvm(2, 5, 20, 15, 5),
            _mvm(3, 9, 30, 25, 5, counts=MaturityCounts(green=30)),
        ])
        kpi = agg.build_kpis(_module_scope(m))
        assert kpi.fruits_detected == 30  # NOT 60
        assert kpi.fruits_delta == 30 - 20

    def test_single_monitoring_no_delta(self):
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 8, 2)])
        kpi = agg.build_kpis(_module_scope(m))
        assert kpi.fruits_detected == 10
        assert kpi.fruits_delta is None

    def test_empty_module(self):
        m = ModuleScopeData(1, "M1", [])
        kpi = agg.build_kpis(_module_scope(m))
        assert kpi.fruits_detected is None
        assert kpi.predominant_maturity == []

    def test_health_0_100(self):
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 0, 10)])
        kpi = agg.build_kpis(_module_scope(m))
        assert kpi.health_pct_healthy == pytest.approx(0.0)
        assert kpi.health_pct_unhealthy == pytest.approx(100.0)

    def test_health_100_0(self):
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 10, 0)])
        kpi = agg.build_kpis(_module_scope(m))
        assert kpi.health_pct_healthy == pytest.approx(100.0)
        assert kpi.health_pct_unhealthy == pytest.approx(0.0)

    def test_health_sum_below_100_unknown(self):
        # 10 total, 6 healthy, 2 unhealthy -> 2 unknown -> other 20%
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 6, 2)])
        kpi = agg.build_kpis(_module_scope(m))
        assert kpi.health_pct_healthy == pytest.approx(60.0)
        assert kpi.health_pct_unhealthy == pytest.approx(20.0)
        assert kpi.health_other_pct == pytest.approx(20.0)

    def test_maturity_partial_coverage_kpi(self):
        counts = MaturityCounts(green=2, breaker=1, turning=3, pink=2, light_red=1, red=1)
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 16, 12, 4, counts=counts)])
        kpi = agg.build_kpis(_module_scope(m))
        assert kpi.maturity_covered == 10
        assert kpi.maturity_total == 16
        assert kpi.predominant_maturity == ["turning"]
        assert kpi.harvestable_share == pytest.approx(0.2)
        assert kpi.coverage_known is True

    def test_maturity_fallback_only(self):
        fb = MaturityDistributionFallback(pct_green=100.0)
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 8, 2, fallback=fb)])
        kpi = agg.build_kpis(_module_scope(m))
        assert kpi.coverage_known is False
        assert kpi.maturity_covered is None
        assert kpi.predominant_maturity == []
        assert kpi.harvestable_share is None

    def test_maturity_not_classifiable(self):
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 8, 2, counts=MaturityCounts())])
        kpi = agg.build_kpis(_module_scope(m))
        assert kpi.maturity_covered == 0
        assert kpi.predominant_maturity == []


class TestAllScopeAggregation:
    def _all_scope(self, modules):
        return ScopeData(
            greenhouse_id=1,
            greenhouse_name="GH1",
            scope_kind="all",
            selected_module_id=None,
            modules=modules,
        )

    def test_fruits_sum_of_last_valid_per_module(self):
        m1 = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 8, 2), _mvm(2, 5, 15, 12, 3)])
        m2 = ModuleScopeData(2, "M2", [_mvm(3, 3, 20, 18, 2)])
        kpi = agg.build_kpis(self._all_scope([m1, m2]))
        # last valid: M1=15, M2=20 -> 35
        assert kpi.fruits_detected == 35
        assert kpi.contributing_modules == 2

    def test_health_weighted_by_counts_not_avg_of_pcts(self):
        # M1: 100 fruits, 90 healthy (90%). M2: 10 fruits, 1 healthy (10%).
        # Naive avg of pcts = 50%. Weighted = 91/110 = 82.7%.
        m1 = ModuleScopeData(1, "M1", [_mvm(1, 1, 100, 90, 10)])
        m2 = ModuleScopeData(2, "M2", [_mvm(2, 1, 10, 1, 9)])
        kpi = agg.build_kpis(self._all_scope([m1, m2]))
        assert kpi.health_pct_healthy == pytest.approx(91 / 110 * 100.0)
        assert kpi.health_pct_healthy != pytest.approx(50.0)

    def test_maturity_counts_summed_across_modules(self):
        m1 = ModuleScopeData(1, "M1", [_mvm(1, 1, 5, 5, 0, counts=MaturityCounts(green=5))])
        m2 = ModuleScopeData(2, "M2", [_mvm(2, 1, 5, 5, 0, counts=MaturityCounts(red=5))])
        kpi = agg.build_kpis(self._all_scope([m1, m2]))
        assert kpi.maturity_covered == 10
        # tie green vs red
        assert kpi.predominant_maturity == ["green", "red"]

    def test_module_without_valid_excluded(self):
        m1 = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 8, 2)])
        m2 = ModuleScopeData(2, "M2", [])  # no valid
        kpi = agg.build_kpis(self._all_scope([m1, m2]))
        assert kpi.contributing_modules == 1

    def test_freshness_range_and_last_monitoring(self):
        m1 = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 8, 2)])
        m2 = ModuleScopeData(2, "M2", [_mvm(2, 9, 20, 18, 2)])
        kpi = agg.build_kpis(self._all_scope([m1, m2]))
        assert kpi.freshness_from == datetime(2025, 6, 1)
        assert kpi.freshness_to == datetime(2025, 6, 9)
        assert kpi.last_monitoring_date == datetime(2025, 6, 9)

    def test_empty_all_scope(self):
        kpi = agg.build_kpis(self._all_scope([]))
        assert kpi.fruits_detected is None


class TestCoverageKnownSemantics:
    """Fix A: coverage_known means exact maturity coverage is known for the
    WHOLE presented set."""

    def _all_scope(self, modules):
        return ScopeData(1, "GH1", "all", None, modules)

    def test_empty_scope_coverage_unknown(self):
        kpi = agg.build_kpis(self._all_scope([]))
        assert kpi.coverage_known is False

    def test_module_real_counts_covered_zero_is_known(self):
        # MODULE scope: real counts with covered==0 is still coverage_known.
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 8, 2, counts=MaturityCounts())])
        kpi = agg.build_kpis(_module_scope(m))
        assert kpi.coverage_known is True
        assert kpi.maturity_covered == 0
        assert kpi.predominant_maturity == []
        assert kpi.harvestable_share is None

    def test_all_all_real_counts_known(self):
        m1 = ModuleScopeData(1, "M1", [_mvm(1, 1, 5, 5, 0, counts=MaturityCounts(green=5))])
        m2 = ModuleScopeData(2, "M2", [_mvm(2, 1, 5, 5, 0, counts=MaturityCounts(red=5))])
        kpi = agg.build_kpis(self._all_scope([m1, m2]))
        assert kpi.coverage_known is True
        assert kpi.maturity_covered == 10

    def test_all_all_real_counts_but_covered_zero(self):
        m1 = ModuleScopeData(1, "M1", [_mvm(1, 1, 5, 5, 0, counts=MaturityCounts())])
        m2 = ModuleScopeData(2, "M2", [_mvm(2, 1, 5, 5, 0, counts=MaturityCounts())])
        kpi = agg.build_kpis(self._all_scope([m1, m2]))
        assert kpi.coverage_known is True
        assert kpi.maturity_covered == 0
        assert kpi.predominant_maturity == []
        assert kpi.harvestable_share is None

    def test_all_real_plus_fallback_is_unknown(self):
        m1 = ModuleScopeData(1, "M1", [_mvm(1, 1, 5, 5, 0, counts=MaturityCounts(green=5))])
        fb = MaturityDistributionFallback(pct_green=100.0)
        m2 = ModuleScopeData(2, "M2", [_mvm(2, 1, 5, 5, 0, fallback=fb)])
        kpi = agg.build_kpis(self._all_scope([m1, m2]))
        assert kpi.coverage_known is False
        assert kpi.maturity_covered is None
        assert kpi.predominant_maturity == []
        assert kpi.harvestable_share is None

    def test_all_real_plus_no_maturity_data_is_unknown(self):
        # A module whose last-valid has neither real counts nor fallback.
        m1 = ModuleScopeData(1, "M1", [_mvm(1, 1, 5, 5, 0, counts=MaturityCounts(green=5))])
        m2 = ModuleScopeData(2, "M2", [_mvm(2, 1, 5, 5, 0)])  # no maturity at all
        kpi = agg.build_kpis(self._all_scope([m1, m2]))
        assert kpi.coverage_known is False
        assert kpi.maturity_covered is None
        assert kpi.predominant_maturity == []
        assert kpi.harvestable_share is None
