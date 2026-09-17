"""Tests for AnalyticsService (Spec 024, Block 8)."""

from datetime import datetime

import pytest

from src.application.dtos.dashboard_analytics_dtos import (
    HarvestResult,
    HarvestSummaryAll,
    MaturityCounts,
    MaturityDistributionFallback,
    ModuleScopeData,
    ModuleValidMonitoring,
    ScopeData,
)
from src.application.services.analytics_service import AnalyticsService


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
    return ScopeData(1, "GH1", "module", module.module_id, [module])


def _all_scope(modules):
    return ScopeData(1, "GH1", "all", None, modules)


@pytest.fixture
def service():
    return AnalyticsService()


class TestModuleScope:
    def test_module_real_counts(self, service):
        counts = MaturityCounts(green=5, red=5)
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 8, 2, counts=counts)])
        ctx = service.build_analytics(_module_scope(m))
        assert ctx.maturity_coverage_known is True
        assert ctx.maturity_current_counts.covered == 10
        assert ctx.maturity_current_fallback is None
        assert isinstance(ctx.harvest, HarvestResult)
        assert len(ctx.evolution_series) == 1

    def test_module_fallback(self, service):
        fb = MaturityDistributionFallback(pct_green=100.0)
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 8, 2, fallback=fb)])
        ctx = service.build_analytics(_module_scope(m))
        assert ctx.maturity_coverage_known is False
        assert ctx.maturity_current_counts is None
        assert ctx.maturity_current_fallback is fb

    def test_module_no_maturity(self, service):
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 8, 2)])
        ctx = service.build_analytics(_module_scope(m))
        assert ctx.maturity_coverage_known is False
        assert ctx.maturity_current_counts is None
        assert ctx.maturity_current_fallback is None

    def test_module_harvest_is_harvest_result(self, service):
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 8, 2, counts=MaturityCounts(green=10))])
        ctx = service.build_analytics(_module_scope(m))
        assert isinstance(ctx.harvest, HarvestResult)


class TestAllScope:
    def test_all_fully_known(self, service):
        m1 = ModuleScopeData(1, "M1", [_mvm(1, 1, 5, 5, 0, counts=MaturityCounts(green=5))])
        m2 = ModuleScopeData(2, "M2", [_mvm(2, 1, 5, 5, 0, counts=MaturityCounts(red=5))])
        ctx = service.build_analytics(_all_scope([m1, m2]))
        assert ctx.maturity_coverage_known is True
        assert ctx.maturity_current_counts.covered == 10
        assert isinstance(ctx.harvest, HarvestSummaryAll)
        assert len(ctx.evolution_series) == 2  # one series per module

    def test_all_mixed_real_and_fallback_not_known(self, service):
        m1 = ModuleScopeData(1, "M1", [_mvm(1, 1, 5, 5, 0, counts=MaturityCounts(green=5))])
        fb = MaturityDistributionFallback(pct_green=100.0)
        m2 = ModuleScopeData(2, "M2", [_mvm(2, 1, 5, 5, 0, fallback=fb)])
        ctx = service.build_analytics(_all_scope([m1, m2]))
        assert ctx.maturity_coverage_known is False
        assert ctx.maturity_current_counts is None
        # Never merges a fallback into a fake aggregate.
        assert ctx.maturity_current_fallback is None

    def test_all_harvest_summary(self, service):
        m1 = ModuleScopeData(1, "M1", [_mvm(1, 1, 5, 5, 0, counts=MaturityCounts(red=5))])
        ctx = service.build_analytics(_all_scope([m1]))
        assert isinstance(ctx.harvest, HarvestSummaryAll)


class TestCurrentDistribution:
    """§3: maturity_current_distribution presentation data (three states)."""

    def test_real_counts_distribution_computed_by_service(self, service):
        # covered=10: green 2, red 8 -> 20% / 80% over covered subset
        counts = MaturityCounts(green=2, red=8)
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 8, 2, counts=counts)])
        ctx = service.build_analytics(_module_scope(m))
        dist = ctx.maturity_current_distribution
        assert dist is not None
        assert dist["green"] == pytest.approx(20.0)
        assert dist["red"] == pytest.approx(80.0)
        assert sum(dist.values()) == pytest.approx(100.0)

    def test_fallback_distribution_uses_original_pcts(self, service):
        fb = MaturityDistributionFallback(pct_green=30.0, pct_red=70.0)
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 8, 2, fallback=fb)])
        ctx = service.build_analytics(_module_scope(m))
        dist = ctx.maturity_current_distribution
        assert dist is not None
        assert dist["green"] == pytest.approx(30.0)
        assert dist["red"] == pytest.approx(70.0)

    def test_no_maturity_distribution_none(self, service):
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 8, 2)])
        ctx = service.build_analytics(_module_scope(m))
        assert ctx.maturity_current_distribution is None


class TestEmptyScope:
    def test_empty_module_scope(self, service):
        scope = ScopeData(1, "GH1", "module", None, [])
        ctx = service.build_analytics(scope)
        assert ctx.maturity_coverage_known is False
        assert ctx.kpis.fruits_detected is None
        assert isinstance(ctx.harvest, HarvestResult)
        assert ctx.harvest.status == "insufficient"

    def test_empty_all_scope(self, service):
        scope = ScopeData(1, "GH1", "all", None, [])
        ctx = service.build_analytics(scope)
        assert ctx.maturity_coverage_known is False
        assert isinstance(ctx.harvest, HarvestSummaryAll)
        assert ctx.evolution_series == []
