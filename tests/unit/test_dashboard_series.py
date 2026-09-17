"""Tests for dashboard_series (Spec 024, Block 5)."""

from datetime import datetime

import pytest

from src.application.dtos.dashboard_analytics_dtos import (
    MaturityCounts,
    MaturityDistributionFallback,
    ModuleScopeData,
    ModuleValidMonitoring,
    ScopeData,
)
from src.application.services import dashboard_series as series


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


class TestEvolutionSeries:
    def test_single_module_points_ordered(self):
        m = ModuleScopeData(1, "M1", [_mvm(2, 9, 30, 20, 10), _mvm(1, 1, 10, 8, 2)])
        result = series.evolution_series(_module_scope(m))
        assert len(result) == 1
        vals = [p.value for p in result[0].points]
        assert vals == [10.0, 30.0]  # ordered by date

    def test_empty_module_empty_series(self):
        m = ModuleScopeData(1, "M1", [])
        result = series.evolution_series(_module_scope(m))
        assert len(result) == 1 and result[0].points == []

    def test_single_point(self):
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 8, 2)])
        result = series.evolution_series(_module_scope(m))
        assert len(result[0].points) == 1

    def test_all_scope_independent_series_per_module(self):
        m1 = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 8, 2), _mvm(2, 5, 12, 10, 2)])
        m2 = ModuleScopeData(2, "M2", [_mvm(3, 3, 20, 18, 2)])
        result = series.evolution_series(_all_scope([m1, m2]))
        assert len(result) == 2  # one series per module, NOT a merged total
        by_id = {s.module_id: s for s in result}
        assert [p.value for p in by_id[1].points] == [10.0, 12.0]
        assert [p.value for p in by_id[2].points] == [20.0]
        # each module keeps its own real dates
        assert by_id[1].points[0].started_at == datetime(2025, 6, 1)
        assert by_id[2].points[0].started_at == datetime(2025, 6, 3)

    def test_irregular_gaps_keep_real_dates(self):
        m = ModuleScopeData(1, "M1", [
            _mvm(1, 1, 10, 8, 2), _mvm(2, 4, 12, 10, 2), _mvm(3, 24, 30, 25, 5),
        ])
        result = series.evolution_series(_module_scope(m))
        dates = [p.started_at.day for p in result[0].points]
        assert dates == [1, 4, 24]


class TestHealthSeries:
    def test_pct_healthy_per_monitoring(self):
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 10, 5, 5), _mvm(2, 5, 10, 8, 2)])
        result = series.health_series(_module_scope(m))
        assert [p.value for p in result[0].points] == [pytest.approx(50.0), pytest.approx(80.0)]

    def test_zero_total_skipped(self):
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 0, 0, 0)])
        result = series.health_series(_module_scope(m))
        assert result[0].points == []


class TestMaturityIndexSeries:
    def test_index_per_monitoring_real_coverage(self):
        m = ModuleScopeData(1, "M1", [
            _mvm(1, 1, 10, 8, 2, counts=MaturityCounts(green=10)),
            _mvm(2, 5, 10, 8, 2, counts=MaturityCounts(red=10)),
        ])
        result = series.maturity_index_series(_module_scope(m))
        assert [p.value for p in result[0].points] == [pytest.approx(0.0), pytest.approx(1.0)]

    def test_fallback_only_skipped(self):
        m = ModuleScopeData(1, "M1", [
            _mvm(1, 1, 10, 8, 2, fallback=MaturityDistributionFallback(pct_green=100.0)),
            _mvm(2, 5, 10, 8, 2, counts=MaturityCounts(red=10)),
        ])
        result = series.maturity_index_series(_module_scope(m))
        # only the second (real coverage) yields a point
        assert len(result[0].points) == 1
        assert result[0].points[0].value == pytest.approx(1.0)

    def test_partial_coverage_note(self):
        # covered 4 of 16 -> note present
        counts = MaturityCounts(green=2, red=2)
        m = ModuleScopeData(1, "M1", [_mvm(1, 1, 16, 12, 4, counts=counts)])
        result = series.maturity_index_series(_module_scope(m))
        assert result[0].points[0].note == "cobertura 4/16"
