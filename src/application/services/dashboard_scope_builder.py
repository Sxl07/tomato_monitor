"""Build analytics ScopeData from repositories with strict user isolation.

Spec 024, Block 9 support. This module resolves the analytics scope (which
greenhouse and which module/"all") and materializes a ``ScopeData`` using bulk
repository reads, while enforcing multiuser isolation (Spec 022):

- Greenhouses come ONLY from ``greenhouse_repo.get_all_by_owner(user_id)``.
- Modules come ONLY from ``module_repo.get_by_greenhouse(selected_gh_id)`` for a
  greenhouse the user owns.
- Monitoring ids fed to the bulk repos are derived EXCLUSIVELY by descending the
  owned greenhouse -> its modules -> their monitorings. A ``greenhouse_id`` or
  ``module_id`` from the querystring that does not belong to the user is treated
  as not-found and falls back to a safe default; its ids never reach the repos.

Valid monitoring (matches the glossary): status == "completed", started_at is
not None, and MonitoringMetrics exists. The order is mandatory:
candidates -> bulk metrics -> valid -> bulk inspection results.

This module has no HTTP/Jinja dependency; it takes repository objects (already
scoped/injected by the route) so it is unit-testable with fakes.
"""

from __future__ import annotations

from typing import Optional

from src.application.dtos.dashboard_analytics_dtos import (
    MaturityCounts,
    MaturityDistributionFallback,
    ModuleScopeData,
    ModuleValidMonitoring,
    ScopeData,
)
from src.application.services import maturity_stats

_SIX_PCT_ATTRS = (
    "pct_green",
    "pct_breaker",
    "pct_turning",
    "pct_pink",
    "pct_light_red",
    "pct_red",
)


def resolve_selected_greenhouse(greenhouses: list, greenhouse_id_param: Optional[str]):
    """Pick the selected greenhouse deterministically.

    - If ``greenhouse_id_param`` parses to an int that belongs to the user's
      greenhouses, use it.
    - Otherwise (missing, invalid, or another user's id) fall back to the
      greenhouse with the smallest id (``min(greenhouses, key=id)``).
    - Returns None only when the user has no greenhouses.
    """
    if not greenhouses:
        return None
    by_id = {gh.id: gh for gh in greenhouses}
    if greenhouse_id_param is not None:
        try:
            gid = int(greenhouse_id_param)
        except (TypeError, ValueError):
            gid = None
        if gid is not None and gid in by_id:
            return by_id[gid]
    return min(greenhouses, key=lambda gh: gh.id)


def resolve_module_scope(modules: list, module_id_param: Optional[str]):
    """Resolve the module scope: ("all", None) or ("module", module_id).

    A ``module_id`` that does not belong to ``modules`` (another user's/greenhouse's
    module, or invalid) falls back safely to "all".
    """
    if module_id_param is None or module_id_param == "" or module_id_param == "all":
        return "all", None
    try:
        mid = int(module_id_param)
    except (TypeError, ValueError):
        return "all", None
    owned_ids = {m.id for m in modules}
    if mid in owned_ids:
        return "module", mid
    return "all", None


def _has_useful_pcts(metrics) -> bool:
    """True if any of the six maturity percentages is > 0 (useful fallback)."""
    return any(getattr(metrics, attr, 0.0) > 0 for attr in _SIX_PCT_ATTRS)


def _build_maturity(monitoring_id, metrics, results_by_id):
    """Return (maturity_counts, maturity_fallback) for a valid monitoring.

    - Inspection result rows present -> real counts, no fallback.
    - No rows but useful pct_* (any > 0) -> visual-only fallback, no counts.
    - No rows and all pct_* == 0 -> (None, None): no maturity data.
    """
    rows = results_by_id.get(monitoring_id)
    if rows:
        return maturity_stats.counts_from_results(rows), None
    if metrics is not None and _has_useful_pcts(metrics):
        fallback = maturity_stats.fallback_from_pcts(
            metrics.pct_green,
            metrics.pct_breaker,
            metrics.pct_turning,
            metrics.pct_pink,
            metrics.pct_light_red,
            metrics.pct_red,
        )
        return None, fallback
    return None, None


def build_scope_data(
    *,
    greenhouses: list,
    module_repo,
    monitoring_repo,
    metrics_repo,
    inspection_repo,
    greenhouse_id_param: Optional[str],
    module_id_param: Optional[str],
) -> Optional[ScopeData]:
    """Materialize ScopeData for the analytics dashboard.

    ``greenhouses`` MUST already be scoped to the user (get_all_by_owner). All
    module/monitoring ids are derived by descending from the selected owned
    greenhouse, never from the querystring directly.

    Returns None when the user has no greenhouses.
    """
    selected_gh = resolve_selected_greenhouse(greenhouses, greenhouse_id_param)
    if selected_gh is None:
        return None

    modules = module_repo.get_by_greenhouse(selected_gh.id)
    scope_kind, selected_module_id = resolve_module_scope(modules, module_id_param)

    # Which modules participate in the scope.
    if scope_kind == "module":
        scoped_modules = [m for m in modules if m.id == selected_module_id]
    else:
        scoped_modules = list(modules)

    # 1-2. Candidate monitorings (completed + started_at), ids derived from
    # owned modules only.
    candidates_by_module: dict[int, list] = {}
    candidate_ids: list[int] = []
    for m in scoped_modules:
        candidates = [
            mon
            for mon in monitoring_repo.get_by_module(m.id)
            if getattr(mon, "status", None) == "completed" and mon.started_at is not None
        ]
        candidates_by_module[m.id] = candidates
        candidate_ids.extend(mon.id for mon in candidates)

    # 3. Bulk metrics over candidates.
    metrics_by_id = metrics_repo.get_by_monitoring_ids(candidate_ids)

    # 4-5. Valid = candidate whose id is present in metrics_by_id.
    valid_ids = [mid for mid in candidate_ids if mid in metrics_by_id]

    # 6. Bulk inspection results only for valid ids.
    results_by_id = inspection_repo.get_by_monitoring_ids(valid_ids)

    # 7-12. Build ModuleScopeData with valid monitorings and maturity.
    module_scopes: list[ModuleScopeData] = []
    for m in scoped_modules:
        valid_monitorings: list[ModuleValidMonitoring] = []
        for mon in candidates_by_module.get(m.id, []):
            metrics = metrics_by_id.get(mon.id)
            if metrics is None:
                continue  # not valid (no metrics)
            counts, fallback = _build_maturity(mon.id, metrics, results_by_id)
            valid_monitorings.append(
                ModuleValidMonitoring(
                    monitoring_id=mon.id,
                    started_at=mon.started_at,
                    total_tomatoes=metrics.total_tomatoes,
                    healthy_count=metrics.healthy_count,
                    unhealthy_count=metrics.unhealthy_count,
                    maturity_counts=counts,
                    maturity_fallback=fallback,
                )
            )
        valid_monitorings.sort(key=lambda v: v.started_at)
        module_scopes.append(
            ModuleScopeData(
                module_id=m.id,
                module_name=m.name,
                valid_monitorings=valid_monitorings,
            )
        )

    return ScopeData(
        greenhouse_id=selected_gh.id,
        greenhouse_name=selected_gh.name,
        scope_kind=scope_kind,
        selected_module_id=selected_module_id,
        modules=module_scopes,
    )
