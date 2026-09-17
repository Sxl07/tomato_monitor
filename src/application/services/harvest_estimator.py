"""Harvest window estimator (Spec 024, Block 6).

Pure, deterministic, reproducible. No ML, no forecasting library, no external
data. Given a module's valid monitorings it estimates a harvest WINDOW (a date
range, never an exact guaranteed date) derived from the observed evolution of
the ordinal maturity index (MI), weighted by real maturity coverage.

Approved parameters (only two):
    MIN_OBS   = 3     # minimum usable observations to project a FUTURE trend
    MI_TARGET = 0.8   # operational convention: mean maturity level ~ Light Red

Usable observation: valid monitoring (completed + metrics + started_at) with
real coverage (``coverage_known`` and ``maturity_covered > 0``) and
``total_tomatoes > 0``. Fallback-only observations do NOT participate.

Trend: coverage-weighted least squares (WLS) of MI vs days.
    w_i = coverage_ratio_i ; x_i = days since first usable obs ; y_i = MI_i
    slope = Sxy_w / Sxx_w ; intercept = ȳ_w - slope * x̄_w

Crossing of MI_TARGET is computed on the FITTED line:
    t_target = (MI_TARGET - intercept) / slope ; days_until = t_target - x_last

Window from the empirical residual dispersion (weighted):
    r_i = y_i - (intercept + slope * x_i) ; s = sqrt(Σ w_i r_i^2 / Σ w_i)
    t_low  = (MI_TARGET - s - intercept) / slope
    t_high = (MI_TARGET + s - intercept) / slope

State priority (MANDATORY ORDER):
    1. no usable observation                     -> insufficient
    2. MI_last >= MI_TARGET                      -> target_reached (even n=1,2)
    3. n < MIN_OBS                               -> insufficient
    4. Sxx_w == 0 (no temporal spread)           -> not_estimable
    5. MI_last < MI_TARGET and slope <= 0        -> not_estimable
    6. MI_last < MI_TARGET and slope > 0:
         t_target > x_last                       -> window (normal)
         t_target <= x_last and t_high > x_last  -> window (starts "now")
         t_target <= x_last and t_high <= x_last -> not_estimable (inconsistency)

target_reached is NEVER decided by days_until <= 0 alone.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Optional

from src.application.dtos.dashboard_analytics_dtos import (
    HarvestResult,
    HarvestSummaryAll,
    ModuleScopeData,
    ModuleValidMonitoring,
)
from src.application.services.maturity_index import maturity_index

MIN_OBS = 3
MI_TARGET = 0.8

# Below this weighted residual std, the fit is treated as having no dispersion
# (perfect linear fit up to floating-point noise) -> degenerate point window.
_RESIDUAL_EPSILON = 1e-12


class _Observation:
    """A usable observation: day offset x, MI value y, coverage weight w."""

    __slots__ = ("started_at", "x", "y", "w")

    def __init__(self, started_at: datetime, x: float, y: float, w: float) -> None:
        self.started_at = started_at
        self.x = x
        self.y = y
        self.w = w


def _usable_observations(module: ModuleScopeData) -> list[_Observation]:
    """Extract usable observations, ordered by date, with x = days since first.

    Usable: real coverage (coverage_known, covered > 0), total_tomatoes > 0,
    and a computable maturity index.
    """
    candidates: list[tuple[datetime, float, float]] = []
    for m in sorted(module.valid_monitorings, key=lambda o: o.started_at):
        if not m.coverage_known or m.maturity_counts is None:
            continue
        if m.maturity_counts.covered <= 0 or m.total_tomatoes <= 0:
            continue
        mi = maturity_index(m.maturity_counts)
        if mi is None:
            continue
        cov_ratio = m.coverage_ratio
        if cov_ratio is None or cov_ratio <= 0:
            continue
        candidates.append((m.started_at, mi, cov_ratio))

    if not candidates:
        return []

    t0 = candidates[0][0]
    obs: list[_Observation] = []
    for started_at, mi, w in candidates:
        x = (started_at - t0).total_seconds() / 86400.0  # days, fractional
        obs.append(_Observation(started_at, x, mi, w))
    return obs


def _wls(obs: list[_Observation]) -> tuple[float, float, float, float]:
    """Coverage-weighted least squares.

    Returns (slope, intercept, Sxx_w, s) where s is the weighted residual
    standard deviation. When Sxx_w == 0, slope/intercept/s are 0.0 and the
    caller treats it as no-temporal-spread.
    """
    W = sum(o.w for o in obs)
    xbar = sum(o.w * o.x for o in obs) / W
    ybar = sum(o.w * o.y for o in obs) / W
    sxx = sum(o.w * (o.x - xbar) ** 2 for o in obs)
    sxy = sum(o.w * (o.x - xbar) * (o.y - ybar) for o in obs)

    if sxx == 0.0:
        return 0.0, 0.0, 0.0, 0.0

    slope = sxy / sxx
    intercept = ybar - slope * xbar
    ss_res = sum(o.w * (o.y - (intercept + slope * o.x)) ** 2 for o in obs)
    s = math.sqrt(ss_res / W) if W > 0 else 0.0
    # Numeric stability: a perfect linear fit yields residuals of ~1e-17 rather
    # than exactly 0.0 due to floating-point. Treat such negligible dispersion
    # as zero so the "degenerate window" (point estimate) case is detected
    # deterministically instead of producing a spuriously tiny band.
    if s < _RESIDUAL_EPSILON:
        s = 0.0
    return slope, intercept, sxx, s


def estimate(module: ModuleScopeData) -> HarvestResult:
    """Estimate the harvest window for a single module.

    Returns a HarvestResult; see module docstring for the state machine.
    """
    obs = _usable_observations(module)
    n = len(obs)

    # 1. No usable observation.
    if n == 0:
        return HarvestResult(
            status="insufficient",
            n_observations=0,
            message="Datos insuficientes: no hay monitoreos con madurez clasificable.",
        )

    mi_last = obs[-1].y
    x_last = obs[-1].x
    started_last = obs[-1].started_at
    mean_cov = sum(o.w for o in obs) / n

    # 2. Current level already reached the target (independent of MIN_OBS).
    if mi_last >= MI_TARGET:
        return HarvestResult(
            status="target_reached",
            n_observations=n,
            mean_coverage_ratio=mean_cov,
            mi_last=mi_last,
            message="Nivel medio de madurez objetivo alcanzado.",
        )

    # 3. Not enough observations to project a future trend.
    if n < MIN_OBS:
        return HarvestResult(
            status="insufficient",
            n_observations=n,
            mean_coverage_ratio=mean_cov,
            mi_last=mi_last,
            message=(
                f"Datos insuficientes: se requieren {MIN_OBS} monitoreos "
                f"utilizables (hay {n})."
            ),
        )

    slope, intercept, sxx, s = _wls(obs)

    # 4. No temporal spread.
    if sxx == 0.0:
        return HarvestResult(
            status="not_estimable",
            n_observations=n,
            mean_coverage_ratio=mean_cov,
            mi_last=mi_last,
            slope_per_day=None,
            reason="Sin dispersión temporal en los monitoreos utilizados.",
            message="No estimable: los monitoreos no tienen separación temporal.",
        )

    # 5. Below target with no upward progression.
    if slope <= 0:
        return HarvestResult(
            status="not_estimable",
            n_observations=n,
            mean_coverage_ratio=mean_cov,
            mi_last=mi_last,
            slope_per_day=slope,
            reason="Tendencia plana o regresiva; no proyecta hacia el objetivo.",
            message="No estimable: la madurez no muestra progresión hacia el objetivo.",
        )

    # 6. Below target, positive slope -> project crossing on the fitted line.
    t_target = (MI_TARGET - intercept) / slope
    t_low = (MI_TARGET - s - intercept) / slope
    t_high = (MI_TARGET + s - intercept) / slope
    is_degenerate = s == 0.0

    if t_target > x_last:
        # Normal window. Window start clamps to "now" if the lower band already
        # crosses at/before the last observation.
        days_low = max(0.0, t_low - x_last)
        days_high = t_high - x_last
        return HarvestResult(
            status="window",
            window_start=_offset_date(started_last, days_low),
            window_end=_offset_date(started_last, days_high),
            is_degenerate_window=is_degenerate,
            n_observations=n,
            mean_coverage_ratio=mean_cov,
            mi_last=mi_last,
            slope_per_day=slope,
            message=_window_message(n, mean_cov, is_degenerate),
        )

    # t_target <= x_last (fitted line crossed target in the past) but MI_last < target.
    if t_high > x_last:
        # Lower band still crosses in the future -> window that starts "now".
        days_high = t_high - x_last
        return HarvestResult(
            status="window",
            window_start=started_last.date(),
            window_end=_offset_date(started_last, days_high),
            is_degenerate_window=is_degenerate,
            n_observations=n,
            mean_coverage_ratio=mean_cov,
            mi_last=mi_last,
            slope_per_day=slope,
            message=_window_message(n, mean_cov, is_degenerate),
        )

    # Both crossings in the past while the last observation is still below target.
    return HarvestResult(
        status="not_estimable",
        n_observations=n,
        mean_coverage_ratio=mean_cov,
        mi_last=mi_last,
        slope_per_day=slope,
        reason=(
            "Inconsistencia entre el ajuste histórico y la observación actual: "
            "el modelo ajustado proyectaba el objetivo en el pasado, pero el "
            "último dato observado sigue por debajo."
        ),
        message="No estimable: el ajuste histórico es inconsistente con el último dato.",
    )


def estimate_all(modules: list[ModuleScopeData]) -> HarvestSummaryAll:
    """Per-module harvest summary for the "all" scope.

    Runs ``estimate`` per module and classifies each into one bucket. NEVER
    produces a single greenhouse-wide date. Deterministic order: modules are
    processed sorted by ``module_id`` so the output lists are stable regardless
    of input order.
    """
    summary = HarvestSummaryAll()
    for module in sorted(modules, key=lambda m: m.module_id):
        result = estimate(module)
        name = module.module_name
        if result.status == "target_reached":
            summary.target_reached.append(name)
        elif result.status == "window":
            summary.upcoming.append(
                (name, result.window_start, result.window_end)
            )
        elif result.status == "insufficient":
            summary.insufficient.append(name)
        else:  # not_estimable
            summary.not_estimable.append(name)
    return summary


def _offset_date(base: datetime, days: float) -> date:
    """Base date + fractional days, rounded to the nearest whole day."""
    return (base + timedelta(days=round(days))).date()


def _window_message(n: int, mean_cov: float, degenerate: bool) -> str:
    base = (
        f"Estimación basada en {n} monitoreos; "
        f"cobertura media de madurez: {round(mean_cov * 100)} %."
    )
    if degenerate:
        return (
            "Estimación central (sin dispersión observada en los monitoreos "
            f"utilizados). {base}"
        )
    return base
