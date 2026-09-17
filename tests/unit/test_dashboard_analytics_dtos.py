"""Tests for Spec 024 analytics DTOs (Block 1).

Verifies every dataclass imports and instantiates, KpiBlock has no default-order
error, and AnalyticsContext can represent real counts, fallback, and no-data.
"""

from datetime import date, datetime

import pytest

from src.application.dtos.dashboard_analytics_dtos import (
    STAGE_ORDER,
    AnalyticsContext,
    HarvestResult,
    HarvestSummaryAll,
    KpiBlock,
    MaturityCounts,
    MaturityDistributionFallback,
    ModuleScopeData,
    ModuleSeries,
    ModuleValidMonitoring,
    ScopeData,
    SeriesPoint,
)


def test_stage_order_is_phenological():
    assert STAGE_ORDER == (
        "green",
        "breaker",
        "turning",
        "pink",
        "light_red",
        "red",
    )


def test_maturity_counts_covered_and_dict():
    c = MaturityCounts(green=2, breaker=1, turning=3, pink=2, light_red=1, red=1)
    assert c.covered == 10
    assert c.as_dict()["turning"] == 3
    assert MaturityCounts().covered == 0


def test_fallback_distribution_as_dict():
    f = MaturityDistributionFallback(pct_green=50.0, pct_red=50.0)
    d = f.as_dict()
    assert d["green"] == 50.0 and d["red"] == 50.0 and d["pink"] == 0.0


class TestModuleValidMonitoring:
    def test_coverage_known_true_with_counts(self):
        m = ModuleValidMonitoring(
            monitoring_id=1,
            started_at=datetime(2025, 6, 1),
            total_tomatoes=16,
            healthy_count=12,
            unhealthy_count=4,
            maturity_counts=MaturityCounts(green=10),
        )
        assert m.coverage_known is True
        assert m.maturity_covered == 10
        assert m.coverage_ratio == 10 / 16

    def test_coverage_ratio_none_when_fallback_only(self):
        m = ModuleValidMonitoring(
            monitoring_id=1,
            started_at=datetime(2025, 6, 1),
            total_tomatoes=16,
            healthy_count=12,
            unhealthy_count=4,
            maturity_fallback=MaturityDistributionFallback(pct_green=100.0),
        )
        assert m.coverage_known is False
        assert m.maturity_covered is None
        assert m.coverage_ratio is None

    def test_coverage_ratio_none_when_zero_total(self):
        m = ModuleValidMonitoring(
            monitoring_id=1,
            started_at=datetime(2025, 6, 1),
            total_tomatoes=0,
            healthy_count=0,
            unhealthy_count=0,
            maturity_counts=MaturityCounts(),
        )
        assert m.coverage_ratio is None


def test_kpiblock_instantiates_without_default_order_error():
    # If field order were wrong this import/instantiation would raise TypeError.
    kpi = KpiBlock(
        last_monitoring_date=None,
        fruits_detected=None,
        fruits_delta=None,
        health_pct_healthy=None,
        health_pct_unhealthy=None,
        health_other_pct=None,
        predominant_maturity=[],
        maturity_covered=None,
        maturity_total=None,
        harvestable_share=None,
    )
    assert kpi.coverage_known is True
    assert kpi.contributing_modules is None
    assert kpi.freshness_from is None and kpi.freshness_to is None


def test_scope_and_series_dataclasses():
    sp = SeriesPoint(started_at=datetime(2025, 6, 1), label="01/06", value=5.0)
    ms = ModuleSeries(module_id=1, module_name="M1", points=[sp])
    msd = ModuleScopeData(module_id=1, module_name="M1")
    scope = ScopeData(
        greenhouse_id=1,
        greenhouse_name="GH1",
        scope_kind="module",
        selected_module_id=1,
        modules=[msd],
    )
    assert ms.points[0].value == 5.0
    assert scope.modules[0].valid_monitorings == []


def test_harvest_result_and_summary():
    hr = HarvestResult(status="insufficient")
    assert hr.n_observations == 0 and hr.is_degenerate_window is False
    summ = HarvestSummaryAll()
    assert summ.target_reached == [] and summ.upcoming == []


class TestAnalyticsContextThreeMaturityStates:
    def _base_kpi(self):
        return KpiBlock(
            last_monitoring_date=None,
            fruits_detected=None,
            fruits_delta=None,
            health_pct_healthy=None,
            health_pct_unhealthy=None,
            health_other_pct=None,
            predominant_maturity=[],
            maturity_covered=None,
            maturity_total=None,
            harvestable_share=None,
        )

    def test_real_counts_state(self):
        ctx = AnalyticsContext(
            kpis=self._base_kpi(),
            harvest=HarvestResult(status="insufficient"),
            maturity_coverage_known=True,
            maturity_current_counts=MaturityCounts(green=5, red=5),
        )
        assert ctx.maturity_coverage_known is True
        assert ctx.maturity_current_counts.covered == 10
        assert ctx.maturity_current_fallback is None

    def test_fallback_state(self):
        ctx = AnalyticsContext(
            kpis=self._base_kpi(),
            harvest=HarvestResult(status="insufficient"),
            maturity_coverage_known=False,
            maturity_current_fallback=MaturityDistributionFallback(pct_green=100.0),
        )
        assert ctx.maturity_coverage_known is False
        assert ctx.maturity_current_counts is None
        assert ctx.maturity_current_fallback is not None

    def test_no_maturity_state(self):
        ctx = AnalyticsContext(
            kpis=self._base_kpi(),
            harvest=HarvestResult(status="insufficient"),
            maturity_coverage_known=False,
        )
        assert ctx.maturity_current_counts is None
        assert ctx.maturity_current_fallback is None

    def test_default_coverage_known_is_false(self):
        ctx = AnalyticsContext(
            kpis=self._base_kpi(),
            harvest=HarvestResult(status="insufficient"),
        )
        assert ctx.maturity_coverage_known is False


class TestDtoInvariants:
    """Fix B: reject ambiguous / inconsistent maturity states."""

    def test_module_valid_monitoring_rejects_both_maturity(self):
        with pytest.raises(ValueError):
            ModuleValidMonitoring(
                monitoring_id=1,
                started_at=datetime(2025, 6, 1),
                total_tomatoes=10,
                healthy_count=8,
                unhealthy_count=2,
                maturity_counts=MaturityCounts(green=5),
                maturity_fallback=MaturityDistributionFallback(pct_green=100.0),
            )

    def _kpi(self):
        return KpiBlock(
            last_monitoring_date=None,
            fruits_detected=None,
            fruits_delta=None,
            health_pct_healthy=None,
            health_pct_unhealthy=None,
            health_other_pct=None,
            predominant_maturity=[],
            maturity_covered=None,
            maturity_total=None,
            harvestable_share=None,
        )

    def test_context_rejects_both_maturity(self):
        with pytest.raises(ValueError):
            AnalyticsContext(
                kpis=self._kpi(),
                harvest=HarvestResult(status="insufficient"),
                maturity_current_counts=MaturityCounts(green=5),
                maturity_current_fallback=MaturityDistributionFallback(pct_green=100.0),
            )

    def test_context_coverage_known_requires_counts(self):
        with pytest.raises(ValueError):
            AnalyticsContext(
                kpis=self._kpi(),
                harvest=HarvestResult(status="insufficient"),
                maturity_coverage_known=True,  # but no counts
            )

    def test_context_fallback_requires_coverage_unknown(self):
        with pytest.raises(ValueError):
            AnalyticsContext(
                kpis=self._kpi(),
                harvest=HarvestResult(status="insufficient"),
                maturity_coverage_known=True,
                maturity_current_counts=MaturityCounts(green=5),
                maturity_current_fallback=MaturityDistributionFallback(pct_green=100.0),
            )

    def test_context_coverage_known_with_counts_covered_zero_ok(self):
        # Real counts with covered==0 is a valid coverage_known=True state.
        ctx = AnalyticsContext(
            kpis=self._kpi(),
            harvest=HarvestResult(status="insufficient"),
            maturity_coverage_known=True,
            maturity_current_counts=MaturityCounts(),
        )
        assert ctx.maturity_coverage_known is True
        assert ctx.maturity_current_counts.covered == 0
