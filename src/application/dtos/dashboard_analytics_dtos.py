"""Context objects (DTOs) for the analytical dashboard (Spec 024).

These are plain, immutable-where-appropriate dataclasses used to carry
pre-computed analytics from the application layer to the presentation layer.

They contain NO business logic and NO database access. The aggregation rules,
maturity index, trend and harvest window are computed by the analytics services
(``maturity_stats``, ``maturity_index``, ``analytics_service``,
``harvest_estimator``) and materialized into these structures so the template
only has to render them.

Maturity semantics (Spec 024):
- When ``InspectionResult`` rows exist for a monitoring, maturity is expressed
  as REAL counts (``MaturityCounts``) with a known coverage. Everything derives
  from these: distribution, predominant, maturity index, harvestable share, and
  participation in the trend / harvest estimation.
- When only ``MonitoringMetrics.pct_*`` are available (no ``InspectionResult``),
  maturity is a VISUAL-ONLY fallback (``MaturityDistributionFallback``) with
  ``coverage_known = False``: no counts, no coverage, no maturity index, no
  harvestable share, and it does NOT participate in the trend / harvest window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


# Fixed phenological order (green -> red). Used everywhere a deterministic
# ordering of maturity stages is required (e.g. predominant tie-breaking).
STAGE_ORDER: tuple[str, ...] = (
    "green",
    "breaker",
    "turning",
    "pink",
    "light_red",
    "red",
)


@dataclass(frozen=True)
class MaturityCounts:
    """Real per-stage maturity counts derived from ``InspectionResult``.

    ``covered`` is the number of detections with a non-null ``maturity_stage``.
    This is the source of truth for distribution, predominant, maturity index
    and harvestable share.
    """

    green: int = 0
    breaker: int = 0
    turning: int = 0
    pink: int = 0
    light_red: int = 0
    red: int = 0

    @property
    def covered(self) -> int:
        return (
            self.green
            + self.breaker
            + self.turning
            + self.pink
            + self.light_red
            + self.red
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "green": self.green,
            "breaker": self.breaker,
            "turning": self.turning,
            "pink": self.pink,
            "light_red": self.light_red,
            "red": self.red,
        }


@dataclass(frozen=True)
class MaturityDistributionFallback:
    """Visual-only maturity distribution from ``MonitoringMetrics.pct_*``.

    Used ONLY when no ``InspectionResult`` rows are available for the monitoring.
    ``coverage_known`` is always False for this structure — it does NOT represent
    real counts or coverage and must not feed the maturity index, harvestable
    share, trend or harvest window.
    """

    pct_green: float = 0.0
    pct_breaker: float = 0.0
    pct_turning: float = 0.0
    pct_pink: float = 0.0
    pct_light_red: float = 0.0
    pct_red: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "green": self.pct_green,
            "breaker": self.pct_breaker,
            "turning": self.pct_turning,
            "pink": self.pct_pink,
            "light_red": self.pct_light_red,
            "red": self.pct_red,
        }


@dataclass
class ModuleValidMonitoring:
    """A single valid monitoring of a module, with metrics and maturity.

    A monitoring is "valid" (built only by the route after confirming metrics
    exist) when it is ``completed``, has ``started_at`` and has
    ``MonitoringMetrics``. Maturity is either real counts (``maturity_counts``)
    or a visual-only fallback (``maturity_fallback``); never both meaningful at
    once. ``coverage_known`` is True iff real counts are present.
    """

    monitoring_id: int
    started_at: datetime
    total_tomatoes: int
    healthy_count: int
    unhealthy_count: int
    # Source of truth for maturity when available.
    maturity_counts: Optional[MaturityCounts] = None
    # Visual-only fallback when there are no InspectionResult rows.
    maturity_fallback: Optional[MaturityDistributionFallback] = None

    def __post_init__(self) -> None:
        # Invariant: real counts and visual-only fallback are mutually
        # exclusive. Having both is an ambiguous state and is rejected.
        if self.maturity_counts is not None and self.maturity_fallback is not None:
            raise ValueError(
                "ModuleValidMonitoring cannot have both maturity_counts and "
                "maturity_fallback set (ambiguous maturity state)."
            )

    @property
    def coverage_known(self) -> bool:
        return self.maturity_counts is not None

    @property
    def maturity_covered(self) -> Optional[int]:
        if self.maturity_counts is None:
            return None
        return self.maturity_counts.covered

    @property
    def coverage_ratio(self) -> Optional[float]:
        """maturity_covered / total_tomatoes, or None when unknown.

        None when there are no real counts (fallback-only) or when
        ``total_tomatoes`` is 0. A None coverage_ratio means the observation
        does NOT participate in the WLS trend / harvest estimation.
        """
        if self.maturity_counts is None or not self.total_tomatoes:
            return None
        return self.maturity_counts.covered / self.total_tomatoes


@dataclass
class ModuleScopeData:
    """All valid monitorings of one module, ordered ascending by started_at."""

    module_id: int
    module_name: str
    valid_monitorings: list[ModuleValidMonitoring] = field(default_factory=list)


@dataclass
class ScopeData:
    """Resolved analytics scope: a greenhouse plus one or all of its modules.

    ``scope_kind`` is "module" (``modules`` has exactly one entry, the selected
    module) or "all" (``modules`` has every module of the greenhouse that has at
    least one valid monitoring is included by the aggregation rules).
    """

    greenhouse_id: int
    greenhouse_name: str
    scope_kind: str  # "module" | "all"
    selected_module_id: Optional[int] = None
    modules: list[ModuleScopeData] = field(default_factory=list)


@dataclass
class SeriesPoint:
    """A single point in a per-module temporal series."""

    started_at: datetime
    label: str
    value: float
    note: Optional[str] = None


@dataclass
class ModuleSeries:
    """A temporal series belonging to a single module (real dates).

    In "all" scope, each module contributes its own independent series; series
    are NEVER merged into an artificial greenhouse-wide historical total.
    """

    module_id: int
    module_name: str
    points: list[SeriesPoint] = field(default_factory=list)


@dataclass
class KpiBlock:
    """The four contextual KPIs plus secondary information.

    Field order is arranged so that all non-default fields precede defaulted
    ones (avoids ``TypeError: non-default argument follows default argument``).
    """

    # --- required / always-present shape (may hold None) ---
    last_monitoring_date: Optional[datetime]
    fruits_detected: Optional[int]
    fruits_delta: Optional[int]  # only meaningful in "module" scope
    health_pct_healthy: Optional[float]
    health_pct_unhealthy: Optional[float]
    health_other_pct: Optional[float]  # 100 - healthy - unhealthy, if > 0
    predominant_maturity: list[str]  # [] none, [x] single, [x, y] tie ("Mixto")
    maturity_covered: Optional[int]
    maturity_total: Optional[int]  # total_tomatoes of the current scope
    harvestable_share: Optional[float]  # (light_red + red) / maturity_covered
    # --- defaulted secondary fields ---
    coverage_known: bool = True  # False if maturity is a visual-only fallback
    contributing_modules: Optional[int] = None  # only in "all" scope
    freshness_from: Optional[datetime] = None  # min started_at of last-valid set
    freshness_to: Optional[datetime] = None  # max started_at of last-valid set


@dataclass
class HarvestResult:
    """Per-module harvest estimation outcome.

    ``status`` is one of: "target_reached", "window", "insufficient",
    "not_estimable". Data support is communicated with objective evidence
    (``n_observations``, ``mean_coverage_ratio``) rather than confidence
    categories.
    """

    status: str
    window_start: Optional[date] = None
    window_end: Optional[date] = None
    is_degenerate_window: bool = False  # s == 0 -> point estimate, not a guarantee
    n_observations: int = 0
    mean_coverage_ratio: Optional[float] = None
    mi_last: Optional[float] = None
    slope_per_day: Optional[float] = None
    reason: Optional[str] = None  # inconsistency detail (fit projected the past)
    message: str = ""


@dataclass
class HarvestSummaryAll:
    """Per-module harvest summary for the "all" scope (no single date)."""

    target_reached: list[str] = field(default_factory=list)
    # (module_name, window_start, window_end)
    upcoming: list[tuple[str, date, date]] = field(default_factory=list)
    insufficient: list[str] = field(default_factory=list)
    not_estimable: list[str] = field(default_factory=list)


@dataclass
class AnalyticsContext:
    """Full analytics context passed to the dashboard template.

    Current maturity is represented explicitly as EITHER real counts OR a
    visual-only fallback OR neither (no maturity data at all), so the template
    never has to infer business rules:

    - ``maturity_coverage_known == True``  -> use ``maturity_current_counts``
      (real; distribution, predominant, index, harvestable share available).
    - ``maturity_coverage_known == False`` and ``maturity_current_fallback`` set
      -> visual-only distribution ("Cobertura no disponible"); no index / no
      harvestable share.
    - both maturity fields None -> "Sin datos de madurez".
    """

    kpis: KpiBlock
    # HarvestResult in "module" scope, HarvestSummaryAll in "all" scope.
    harvest: "HarvestResult | HarvestSummaryAll"
    # Default is the semantically safe "coverage unknown" state.
    maturity_coverage_known: bool = False
    maturity_current_counts: Optional[MaturityCounts] = None
    maturity_current_fallback: Optional[MaturityDistributionFallback] = None
    evolution_series: list[ModuleSeries] = field(default_factory=list)
    health_series: list[ModuleSeries] = field(default_factory=list)
    maturity_index_series: list[ModuleSeries] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Real counts and fallback are mutually exclusive.
        if (
            self.maturity_current_counts is not None
            and self.maturity_current_fallback is not None
        ):
            raise ValueError(
                "AnalyticsContext cannot have both maturity_current_counts and "
                "maturity_current_fallback set."
            )
        # coverage_known True requires real counts (covered may be 0).
        if self.maturity_coverage_known and self.maturity_current_counts is None:
            raise ValueError(
                "maturity_coverage_known=True requires maturity_current_counts."
            )
        # A visual-only fallback implies coverage is NOT known.
        if self.maturity_current_fallback is not None and self.maturity_coverage_known:
            raise ValueError(
                "maturity_current_fallback requires maturity_coverage_known=False."
            )
