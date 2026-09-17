"""AnalyticsService — orchestrates the analytical dashboard (Spec 024, Block 8).

Pure application service: NO repositories, NO database access. It receives a
fully materialized ``ScopeData`` (built by the route from bulk repository reads)
and composes an ``AnalyticsContext`` for the presentation layer.

Responsibilities:
- KPIs (via dashboard_aggregation.build_kpis).
- Per-module series (evolution, health, maturity index).
- Current maturity representation: real counts / visual-only fallback / none,
  honoring the coverage_known semantics (Fix A/B).
- Harvest estimation: per module in "module" scope, per-module summary in "all".
"""

from __future__ import annotations

from typing import Optional

from src.application.dtos.dashboard_analytics_dtos import (
    AnalyticsContext,
    MaturityCounts,
    MaturityDistributionFallback,
    ModuleScopeData,
    ScopeData,
)
from src.application.services import dashboard_aggregation as aggregation
from src.application.services import dashboard_series as series_builder
from src.application.services import harvest_estimator


class AnalyticsService:
    """Builds the analytics context from pre-fetched scope data (no DB)."""

    def build_analytics(self, scope: ScopeData) -> AnalyticsContext:
        kpis = aggregation.build_kpis(scope)

        evolution = series_builder.evolution_series(scope)
        health = series_builder.health_series(scope)
        maturity_index = series_builder.maturity_index_series(scope)

        coverage_known, counts, fallback = self._current_maturity(scope)

        if scope.scope_kind == "module":
            module = scope.modules[0] if scope.modules else None
            harvest = (
                harvest_estimator.estimate(module)
                if module is not None
                else harvest_estimator.estimate(ModuleScopeData(0, "", []))
            )
        else:
            harvest = harvest_estimator.estimate_all(scope.modules)

        return AnalyticsContext(
            kpis=kpis,
            harvest=harvest,
            maturity_coverage_known=coverage_known,
            maturity_current_counts=counts,
            maturity_current_fallback=fallback,
            evolution_series=evolution,
            health_series=health,
            maturity_index_series=maturity_index,
        )

    # ------------------------------------------------------------------
    # Current maturity representation
    # ------------------------------------------------------------------

    def _current_maturity(
        self, scope: ScopeData
    ) -> tuple[bool, Optional[MaturityCounts], Optional[MaturityDistributionFallback]]:
        """Resolve the current-maturity triple (coverage_known, counts, fallback).

        MODULE scope:
            - last-valid has real counts -> (True, counts, None)
            - last-valid is fallback-only -> (False, None, fallback)
            - no valid / no maturity      -> (False, None, None)

        ALL scope:
            - EVERY contributing last-valid has real counts -> (True, aggregate, None)
            - any mix of real / fallback / no-data          -> (False, None, None)
              (never invent a partial aggregate; do not merge different modules'
               fallbacks into a fake aggregate)
        """
        if scope.scope_kind == "module":
            module = scope.modules[0] if scope.modules else None
            if module is None:
                return False, None, None
            lv = aggregation.last_valid(module)
            if lv is None:
                return False, None, None
            if lv.coverage_known and lv.maturity_counts is not None:
                return True, lv.maturity_counts, None
            if lv.maturity_fallback is not None:
                return False, None, lv.maturity_fallback
            return False, None, None

        # ALL scope
        counts = aggregation.aggregate_current_maturity_counts(scope)
        if counts is not None:
            # aggregate_current_maturity_counts only returns non-None when ALL
            # contributing modules had real counts.
            return True, counts, None
        return False, None, None
