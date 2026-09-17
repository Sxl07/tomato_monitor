"""Per-module temporal series for the analytical dashboard (Spec 024, Block 5).

Pure functions. Each series belongs to ONE module and uses that module's real
monitoring dates. Series are never merged into an artificial greenhouse-wide
historical total; in "all" scope the dashboard shows one independent series per
module.

Three series builders:
- ``evolution_series``: fruits detected (``total_tomatoes``) per valid monitoring.
- ``health_series``: percentage healthy per valid monitoring.
- ``maturity_index_series``: ordinal maturity index per valid monitoring, only
  for observations with real coverage (``coverage_known``); fallback-only
  observations are skipped (they have no index).
"""

from __future__ import annotations

from typing import Callable, Optional

from src.application.dtos.dashboard_analytics_dtos import (
    ModuleScopeData,
    ModuleSeries,
    ModuleValidMonitoring,
    ScopeData,
    SeriesPoint,
)
from src.application.services.maturity_index import maturity_index


def _date_label(m: ModuleValidMonitoring) -> str:
    """Short dd/mm label from the monitoring start date."""
    return m.started_at.strftime("%d/%m")


def _ordered(module: ModuleScopeData) -> list[ModuleValidMonitoring]:
    return sorted(module.valid_monitorings, key=lambda m: m.started_at)


def _build_series(
    scope: ScopeData,
    value_fn: Callable[[ModuleValidMonitoring], Optional[float]],
    note_fn: Callable[[ModuleValidMonitoring], Optional[str]] = lambda m: None,
) -> list[ModuleSeries]:
    """Build one ModuleSeries per module in the scope.

    A point is emitted only when ``value_fn`` returns a non-None value, so
    observations without the relevant datum (e.g. no maturity index) are
    skipped. Modules with no resulting points still yield an (empty) series so
    the UI can show the module's legend consistently.
    """
    series: list[ModuleSeries] = []
    for module in scope.modules:
        points: list[SeriesPoint] = []
        for m in _ordered(module):
            value = value_fn(m)
            if value is None:
                continue
            points.append(
                SeriesPoint(
                    started_at=m.started_at,
                    label=_date_label(m),
                    value=float(value),
                    note=note_fn(m),
                )
            )
        series.append(
            ModuleSeries(
                module_id=module.module_id,
                module_name=module.module_name,
                points=points,
            )
        )
    return series


def evolution_series(scope: ScopeData) -> list[ModuleSeries]:
    """Fruits detected (total_tomatoes) per valid monitoring, per module."""
    return _build_series(scope, value_fn=lambda m: float(m.total_tomatoes))


def health_series(scope: ScopeData) -> list[ModuleSeries]:
    """Percentage healthy per valid monitoring, per module."""

    def pct_healthy(m: ModuleValidMonitoring) -> Optional[float]:
        if m.total_tomatoes <= 0:
            return None
        return m.healthy_count / m.total_tomatoes * 100.0

    return _build_series(scope, value_fn=pct_healthy)


def maturity_index_series(scope: ScopeData) -> list[ModuleSeries]:
    """Ordinal maturity index per valid monitoring, per module.

    Only observations with real coverage produce a point; fallback-only
    observations are skipped (no index).
    """

    def mi(m: ModuleValidMonitoring) -> Optional[float]:
        if not m.coverage_known or m.maturity_counts is None:
            return None
        return maturity_index(m.maturity_counts)

    def note(m: ModuleValidMonitoring) -> Optional[str]:
        # Flag partial coverage so the UI can annotate the point.
        if not m.coverage_known or m.maturity_counts is None:
            return None
        covered = m.maturity_counts.covered
        if m.total_tomatoes and covered < m.total_tomatoes:
            return f"cobertura {covered}/{m.total_tomatoes}"
        return None

    return _build_series(scope, value_fn=mi, note_fn=note)
