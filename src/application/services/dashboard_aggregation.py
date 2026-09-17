"""Aggregation rules for the analytical dashboard (Spec 024, Block 4).

Pure functions over the analytics DTOs. No DB, no hardware.

Key semantics:
- "Fruits detected" uses the LAST valid monitoring per module, never the
  historical sum (a fruit can appear in several monitorings).
- Health percentages are recomputed from aggregated COUNTS (weighted by real
  counts), never from a naive average of per-module percentages. ``other_pct``
  captures unknown-health labels when healthy + unhealthy < 100.
- Maturity aggregation sums REAL per-stage counts (only from observations with
  ``coverage_known``) and recomputes distribution / predominant / harvestable
  share from those counts.
- "Last monitoring" in "all" scope is the most recent valid monitoring across
  all modules.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from src.application.dtos.dashboard_analytics_dtos import (
    KpiBlock,
    MaturityCounts,
    ModuleScopeData,
    ModuleValidMonitoring,
    ScopeData,
)
from src.application.services import maturity_stats
from src.application.services.maturity_index import maturity_index


def last_valid(module: ModuleScopeData) -> Optional[ModuleValidMonitoring]:
    """Most recent valid monitoring of a module (max started_at), or None."""
    if not module.valid_monitorings:
        return None
    return max(module.valid_monitorings, key=lambda m: m.started_at)


def prev_valid(module: ModuleScopeData) -> Optional[ModuleValidMonitoring]:
    """Second-most-recent valid monitoring of a module, or None."""
    if len(module.valid_monitorings) < 2:
        return None
    ordered = sorted(module.valid_monitorings, key=lambda m: m.started_at)
    return ordered[-2]


def _health_pcts(
    healthy: int, unhealthy: int, total: int
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (pct_healthy, pct_unhealthy, other_pct) from aggregated counts.

    ``other_pct`` is the remainder (unknown-health labels) when > 0.05, else 0.
    All None when total is 0.
    """
    if total <= 0:
        return None, None, None
    pct_h = healthy / total * 100.0
    pct_u = unhealthy / total * 100.0
    other = 100.0 - pct_h - pct_u
    other_pct = other if other > 0.05 else 0.0
    return pct_h, pct_u, other_pct


def build_kpis(scope: ScopeData) -> KpiBlock:
    """Build the contextual KPI block for the given scope.

    Handles both "module" (single module) and "all" (aggregate across modules)
    scopes. Empty scopes yield a KPI block full of None / empty values.
    """
    if scope.scope_kind == "module":
        module = scope.modules[0] if scope.modules else None
        return _build_kpis_module(module)
    return _build_kpis_all(scope)


def _empty_kpis() -> KpiBlock:
    # No valid data present -> maturity coverage is not known.
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
        coverage_known=False,
    )


def _build_kpis_module(module: Optional[ModuleScopeData]) -> KpiBlock:
    if module is None:
        return _empty_kpis()
    lv = last_valid(module)
    if lv is None:
        return _empty_kpis()

    pv = prev_valid(module)
    fruits_delta = (
        lv.total_tomatoes - pv.total_tomatoes if pv is not None else None
    )

    pct_h, pct_u, other = _health_pcts(
        lv.healthy_count, lv.unhealthy_count, lv.total_tomatoes
    )

    # MODULE scope: coverage_known iff the last-valid monitoring has real
    # counts (even if covered == 0). Fallback / no-data -> False.
    coverage_known = lv.coverage_known
    if coverage_known and lv.maturity_counts is not None:
        counts = lv.maturity_counts
        predominant = maturity_stats.predominant_stages(counts)  # [] when covered==0
        maturity_covered = counts.covered  # may be 0
        share = maturity_stats.harvestable_share(counts)  # None when covered==0
    else:
        predominant = []
        maturity_covered = None
        share = None

    return KpiBlock(
        last_monitoring_date=lv.started_at,
        fruits_detected=lv.total_tomatoes,
        fruits_delta=fruits_delta,
        health_pct_healthy=pct_h,
        health_pct_unhealthy=pct_u,
        health_other_pct=other,
        predominant_maturity=predominant,
        maturity_covered=maturity_covered,
        maturity_total=lv.total_tomatoes,
        harvestable_share=share,
        coverage_known=coverage_known,
    )


def _build_kpis_all(scope: ScopeData) -> KpiBlock:
    # Collect the last valid monitoring of each module that has one.
    last_valids: list[ModuleValidMonitoring] = []
    for module in scope.modules:
        lv = last_valid(module)
        if lv is not None:
            last_valids.append(lv)

    if not last_valids:
        return _empty_kpis()

    # Fruits detected: sum of last-valid total_tomatoes per module.
    fruits_detected = sum(lv.total_tomatoes for lv in last_valids)

    # Health: aggregate counts, then recompute percentages.
    total = sum(lv.total_tomatoes for lv in last_valids)
    healthy = sum(lv.healthy_count for lv in last_valids)
    unhealthy = sum(lv.unhealthy_count for lv in last_valids)
    pct_h, pct_u, other = _health_pcts(healthy, unhealthy, total)

    # Maturity aggregate (Fix A): coverage_known is True ONLY when the exact
    # maturity coverage is known for the WHOLE presented set — i.e. EVERY
    # contributing last-valid monitoring has real counts. If any contributor
    # has a fallback distribution or no maturity data, the scope-wide maturity
    # aggregate would be misleading, so we do NOT present it.
    all_real = all(lv.coverage_known and lv.maturity_counts is not None for lv in last_valids)
    if all_real:
        agg = MaturityCounts()
        for lv in last_valids:
            agg = maturity_stats.add_counts(agg, lv.maturity_counts)
        coverage_known = True
        maturity_covered = agg.covered  # may be 0
        predominant = maturity_stats.predominant_stages(agg)  # [] when covered==0
        share = maturity_stats.harvestable_share(agg)  # None when covered==0
    else:
        coverage_known = False
        maturity_covered = None
        predominant = []
        share = None

    # Last monitoring across all modules (most recent started_at).
    last_dt: datetime = max(lv.started_at for lv in last_valids)

    # Freshness: min/max started_at of the last-valid set.
    freshness_from = min(lv.started_at for lv in last_valids)
    freshness_to = max(lv.started_at for lv in last_valids)

    return KpiBlock(
        last_monitoring_date=last_dt,
        fruits_detected=fruits_detected,
        fruits_delta=None,  # no aggregate delta in "all" scope
        health_pct_healthy=pct_h,
        health_pct_unhealthy=pct_u,
        health_other_pct=other,
        predominant_maturity=predominant,
        maturity_covered=maturity_covered,
        maturity_total=total,
        harvestable_share=share,
        coverage_known=coverage_known,
        contributing_modules=len(last_valids),
        freshness_from=freshness_from,
        freshness_to=freshness_to,
    )


def aggregate_current_maturity_counts(scope: ScopeData) -> Optional[MaturityCounts]:
    """Aggregate real maturity counts of the current scope (last valid per module).

    Returns the module's last-valid counts in "module" scope, the summed counts
    across modules in "all" scope, or None when there is no real coverage.
    """
    if scope.scope_kind == "module":
        module = scope.modules[0] if scope.modules else None
        if module is None:
            return None
        lv = last_valid(module)
        if lv is None or not lv.coverage_known:
            return None
        return lv.maturity_counts

    # All-scope: only aggregate when EVERY contributing module's last-valid has
    # real counts (Fix A). A mix of real / fallback / no-data yields None so the
    # caller marks coverage_known=False instead of inventing a partial aggregate.
    last_valids = [lv for lv in (last_valid(m) for m in scope.modules) if lv is not None]
    if not last_valids:
        return None
    if not all(lv.coverage_known and lv.maturity_counts is not None for lv in last_valids):
        return None
    agg = MaturityCounts()
    for lv in last_valids:
        agg = maturity_stats.add_counts(agg, lv.maturity_counts)
    return agg
