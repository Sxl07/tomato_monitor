"""Tests for harvest_estimator (Spec 024, Block 6)."""

from datetime import datetime

import pytest

from src.application.dtos.dashboard_analytics_dtos import (
    MaturityCounts,
    MaturityDistributionFallback,
    ModuleScopeData,
    ModuleValidMonitoring,
)
from src.application.services import harvest_estimator as he
from src.application.services.harvest_estimator import (
    MI_TARGET,
    MIN_OBS,
    _Observation,
    _wls,
    estimate,
)


# ---------------------------------------------------------------------------
# Helpers to build observations with a CONTROLLED maturity index and coverage.
#
# maturity_index(counts) = Σ count[s]*v(s) / covered, v in {0,.2,.4,.6,.8,1}.
# To get MI = m over `covered` fruits, we split between green (v=0) and red
# (v=1): red = round(m*covered), green = covered - red -> MI = red/covered.
# coverage_ratio = covered / total_tomatoes = weight.
# ---------------------------------------------------------------------------


def _mvm_with_mi(mid, day, mi, total, covered=None):
    """Build a monitoring whose maturity index equals `mi` exactly.

    Uses covered = 20 by default so any MI that is a multiple of 0.05 is exactly
    representable via green (v=0) / red (v=1) split: red = mi*covered.
    coverage_ratio = covered / total = the WLS weight.
    """
    if covered is None:
        covered = 20
    red = round(mi * covered)
    green = covered - red
    counts = MaturityCounts(green=green, red=red)
    # sanity: exact MI representable
    assert red / covered == pytest.approx(mi), (mi, covered, red)
    return ModuleValidMonitoring(
        monitoring_id=mid,
        started_at=datetime(2025, 6, day),
        total_tomatoes=total,
        healthy_count=total,
        unhealthy_count=0,
        maturity_counts=counts,
    )


def _module(monitorings):
    return ModuleScopeData(1, "M1", monitorings)


# ---------------------------------------------------------------------------
# WLS math — exact design.md §9.6 example via internal _wls
# ---------------------------------------------------------------------------


class TestWlsDesignExample:
    def test_slope_intercept_and_s(self):
        obs = [
            _Observation(datetime(2025, 6, 1), 0.0, 0.30, 0.6),
            _Observation(datetime(2025, 6, 8), 7.0, 0.42, 0.8),
            _Observation(datetime(2025, 6, 15), 14.0, 0.55, 1.0),
            _Observation(datetime(2025, 6, 22), 21.0, 0.66, 0.9),
        ]
        slope, intercept, sxx, s = _wls(obs)
        assert slope == pytest.approx(0.017270, abs=1e-5)
        assert intercept == pytest.approx(0.301542, abs=1e-5)
        assert sxx == pytest.approx(182.933, abs=1e-2)
        assert s == pytest.approx(0.004496, abs=1e-5)

    def test_t_target_and_window(self):
        obs = [
            _Observation(datetime(2025, 6, 1), 0.0, 0.30, 0.6),
            _Observation(datetime(2025, 6, 8), 7.0, 0.42, 0.8),
            _Observation(datetime(2025, 6, 15), 14.0, 0.55, 1.0),
            _Observation(datetime(2025, 6, 22), 21.0, 0.66, 0.9),
        ]
        slope, intercept, sxx, s = _wls(obs)
        t_target = (MI_TARGET - intercept) / slope
        assert t_target == pytest.approx(28.862, abs=1e-2)
        x_last = 21.0
        assert (t_target - x_last) == pytest.approx(7.862, abs=1e-2)
        t_low = (MI_TARGET - s - intercept) / slope
        t_high = (MI_TARGET + s - intercept) / slope
        assert (t_low - x_last) == pytest.approx(7.601, abs=1e-2)
        assert (t_high - x_last) == pytest.approx(8.122, abs=1e-2)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class TestStatePriority:
    def test_no_usable_observation_insufficient(self):
        m = _module([])
        assert estimate(m).status == "insufficient"

    def test_fallback_only_is_not_usable(self):
        mvm = ModuleValidMonitoring(
            monitoring_id=1,
            started_at=datetime(2025, 6, 1),
            total_tomatoes=10,
            healthy_count=10,
            unhealthy_count=0,
            maturity_fallback=MaturityDistributionFallback(pct_green=100.0),
        )
        assert estimate(_module([mvm])).status == "insufficient"

    def test_target_reached_with_single_observation(self):
        # MI_last = 0.8 >= target, n=1 -> target_reached (NOT insufficient)
        m = _module([_mvm_with_mi(1, 1, 0.8, total=20)])
        r = estimate(m)
        assert r.status == "target_reached"
        assert r.n_observations == 1

    def test_target_reached_with_two_observations(self):
        m = _module([
            _mvm_with_mi(1, 1, 0.5, total=20),
            _mvm_with_mi(2, 5, 0.9, total=20),  # last >= 0.8
        ])
        r = estimate(m)
        assert r.status == "target_reached"
        assert r.n_observations == 2

    def test_one_obs_below_target_insufficient(self):
        m = _module([_mvm_with_mi(1, 1, 0.5, total=20)])
        assert estimate(m).status == "insufficient"

    def test_two_obs_below_target_insufficient(self):
        m = _module([
            _mvm_with_mi(1, 1, 0.4, total=20),
            _mvm_with_mi(2, 5, 0.5, total=20),
        ])
        assert estimate(m).status == "insufficient"

    def test_days_until_negative_but_mi_below_target_is_not_target_reached(self):
        # Early rise, then last observation dips below target. Whatever the
        # fitted line does, this MUST NOT be target_reached (MI_last=0.5 < 0.8).
        m = _module([
            _mvm_with_mi(1, 1, 0.7, total=20),
            _mvm_with_mi(2, 8, 0.75, total=20),
            _mvm_with_mi(3, 15, 0.5, total=20),
        ])
        r = estimate(m)
        assert r.status != "target_reached"

    def test_flat_trend_not_estimable(self):
        m = _module([
            _mvm_with_mi(1, 1, 0.5, total=20),
            _mvm_with_mi(2, 8, 0.5, total=20),
            _mvm_with_mi(3, 15, 0.5, total=20),
        ])
        r = estimate(m)
        assert r.status == "not_estimable"

    def test_regressive_trend_not_estimable(self):
        m = _module([
            _mvm_with_mi(1, 1, 0.6, total=20),
            _mvm_with_mi(2, 8, 0.5, total=20),
            _mvm_with_mi(3, 15, 0.4, total=20),
        ])
        r = estimate(m)
        assert r.status == "not_estimable"

    def test_same_date_no_spread_not_estimable(self):
        m = _module([
            _mvm_with_mi(1, 5, 0.3, total=20),
            _mvm_with_mi(2, 5, 0.5, total=20),
            _mvm_with_mi(3, 5, 0.6, total=20),
        ])
        r = estimate(m)
        assert r.status == "not_estimable"

    def test_normal_window(self):
        # rising toward target, last below target, fit crosses in the future
        m = _module([
            _mvm_with_mi(1, 1, 0.3, total=20),
            _mvm_with_mi(2, 8, 0.45, total=20),
            _mvm_with_mi(3, 15, 0.6, total=20),
        ])
        r = estimate(m)
        assert r.status == "window"
        assert r.window_start is not None and r.window_end is not None
        assert r.window_end >= r.window_start
        assert r.n_observations == 3
        assert r.mean_coverage_ratio == pytest.approx(1.0)

    def test_degenerate_window_marked(self):
        # Perfect linear fit + equal weights -> residuals ~ 0 -> s == 0.
        m = _module([
            _mvm_with_mi(1, 1, 0.3, total=20),
            _mvm_with_mi(2, 11, 0.5, total=20),
            _mvm_with_mi(3, 21, 0.7, total=20),
        ])
        r = estimate(m)
        assert r.status == "window"
        assert r.is_degenerate_window is True

    def test_coverage_weighting_influences_slope(self):
        # Same MI points; different coverage weights change the fitted slope.
        # Low-coverage early point (w=0.2) influences less than high-coverage.
        low_first = _module([
            _mvm_with_mi(1, 1, 0.3, total=50, covered=10),   # w=0.2
            _mvm_with_mi(2, 8, 0.5, total=20, covered=20),   # w=1.0
            _mvm_with_mi(3, 15, 0.6, total=20, covered=20),  # w=1.0
        ])
        obs = he._usable_observations(low_first)
        assert obs[0].w == pytest.approx(0.2)
        assert obs[1].w == pytest.approx(1.0)
        slope_weighted, *_ = he._wls(obs)
        # Compare with equal weights (all w=1) on the same (x, y) points.
        equal = [_Observation(o.started_at, o.x, o.y, 1.0) for o in obs]
        slope_equal, *_ = he._wls(equal)
        assert slope_weighted != pytest.approx(slope_equal)


class TestTargetInPastSubcases:
    """Fix D: end-to-end estimate() for both t_target <= x_last subcases."""

    def test_case1_window_from_now(self):
        # (1,0.5),(6,0.85),(11,0.75): slope>0, t_target=9<=x_last=10, t_high>10
        m = _module([
            _mvm_with_mi(1, 1, 0.5, total=20),
            _mvm_with_mi(2, 6, 0.85, total=20),
            _mvm_with_mi(3, 11, 0.75, total=20),
        ])
        r = estimate(m)
        assert r.status == "window"
        assert r.mi_last == pytest.approx(0.75)
        assert r.slope_per_day > 0
        # window starts "now" = date of last monitoring (2025-06-11)
        assert r.window_start == datetime(2025, 6, 11).date()
        assert r.window_end > r.window_start

    def test_case2_not_estimable_inconsistency(self):
        # slope>0, t_target<=x_last AND t_high<=x_last while mi_last<0.8.
        # Rising line above target early; last observation a low, low-weight dip.
        m = _module([
            _mvm_with_mi(1, 1, 0.6, total=100, covered=100),   # w=1.0
            _mvm_with_mi(2, 5, 0.8, total=100, covered=100),   # w=1.0
            _mvm_with_mi(3, 9, 0.95, total=100, covered=100),  # w=1.0
            _mvm_with_mi(4, 13, 0.7, total=100, covered=10),   # w=0.1, below target
        ])
        r = estimate(m)
        assert r.status == "not_estimable"
        assert r.mi_last == pytest.approx(0.7)
        assert r.slope_per_day > 0
        assert r.reason is not None
        assert "inconsistencia" in r.reason.lower()


class TestEstimateAll:
    """Fix C: per-module harvest summary, no greenhouse-wide date."""

    def test_four_modules_one_of_each_bucket(self):
        # M1 target_reached; M2 window; M3 insufficient; M4 not_estimable(flat)
        m_target = ModuleScopeData(1, "M1", [_mvm_with_mi(1, 1, 0.85, total=20)])
        m_window = ModuleScopeData(2, "M2", [
            _mvm_with_mi(2, 1, 0.3, total=20),
            _mvm_with_mi(3, 8, 0.45, total=20),
            _mvm_with_mi(4, 15, 0.6, total=20),
        ])
        m_insuf = ModuleScopeData(3, "M3", [_mvm_with_mi(5, 1, 0.4, total=20)])
        m_notest = ModuleScopeData(4, "M4", [
            _mvm_with_mi(6, 1, 0.5, total=20),
            _mvm_with_mi(7, 8, 0.5, total=20),
            _mvm_with_mi(8, 15, 0.5, total=20),
        ])
        summary = he.estimate_all([m_target, m_window, m_insuf, m_notest])
        assert summary.target_reached == ["M1"]
        assert summary.insufficient == ["M3"]
        assert summary.not_estimable == ["M4"]
        assert len(summary.upcoming) == 1
        name, ws, we = summary.upcoming[0]
        assert name == "M2" and we >= ws
        # No greenhouse-wide single date exists on the summary object.
        assert not hasattr(summary, "greenhouse_window")
        assert not hasattr(summary, "window_start")

    def test_deterministic_order_by_module_id(self):
        # Provide modules out of id order; buckets follow module_id order.
        m2 = ModuleScopeData(2, "B", [_mvm_with_mi(1, 1, 0.9, total=20)])
        m1 = ModuleScopeData(1, "A", [_mvm_with_mi(2, 1, 0.95, total=20)])
        summary = he.estimate_all([m2, m1])
        assert summary.target_reached == ["A", "B"]

    def test_empty_modules(self):
        summary = he.estimate_all([])
        assert summary.target_reached == []
        assert summary.upcoming == []
        assert summary.insufficient == []
        assert summary.not_estimable == []


class TestUsableObservationFiltering:
    def test_zero_coverage_excluded(self):
        mvm = ModuleValidMonitoring(
            monitoring_id=1,
            started_at=datetime(2025, 6, 1),
            total_tomatoes=10,
            healthy_count=10,
            unhealthy_count=0,
            maturity_counts=MaturityCounts(),  # covered 0
        )
        assert he._usable_observations(_module([mvm])) == []
