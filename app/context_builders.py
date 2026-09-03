"""Pure context builder functions for the agricultural UI.

These functions transform already-fetched repository data (SQLAlchemy model instances)
into structured context objects suitable for Jinja2 templates. They contain no database
access — all data must be pre-fetched and passed as arguments.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.application.utils.timezone import to_bogota
from src.infrastructure.security.path_sanitizer import validate_safe_path, PathTraversalError
from src.infrastructure.config.settings import OUTPUTS_DIR

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Spanish month abbreviations (avoids locale dependency on Raspberry Pi)
# ---------------------------------------------------------------------------

_SPANISH_MONTHS = {
    1: "Ene",
    2: "Feb",
    3: "Mar",
    4: "Abr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Ago",
    9: "Sep",
    10: "Oct",
    11: "Nov",
    12: "Dic",
}


# ---------------------------------------------------------------------------
# Context dataclasses
# ---------------------------------------------------------------------------


@dataclass
class GreenhouseCardContext:
    """Context for rendering a greenhouse card on the list screen."""

    id: int
    name: str
    module_count: int
    last_monitoring_date: Optional[str]  # Formatted: "12 Jun 2025"


@dataclass
class ModuleCardContext:
    """Context for rendering a module card on the greenhouse detail screen."""

    id: int
    name: str
    crop_type: str
    dimensions: Optional[str]  # "5.0 × 2.0 m" or None
    last_monitoring_date: Optional[str]


@dataclass
class MonitoringHistoryItem:
    """Context for rendering a monitoring entry in the history list."""

    id: int
    date: str  # "12 Jun 2025"
    time: str  # "14:30"
    total_tomatoes: int
    pct_healthy: float  # 0-100
    status: str  # "completed", "aborted"


@dataclass
class ReportMetrics:
    """Context for rendering the monitoring report metric cards."""

    total_tomatoes: int
    healthy_count: int
    unhealthy_count: int
    pct_healthy: float
    pct_unhealthy: float
    maturity_stages: dict[str, float]  # stage_name → percentage
    snapshots_with_detections: int


@dataclass
class SnapshotThumbnail:
    """Context for rendering a snapshot in the gallery grid."""

    monitoring_id: int
    frame_index: int
    image_url: str  # "/snapshots/{monitoring_id}/snapshots/snapshot_{idx}.jpg"
    has_detections: bool


@dataclass
class ActiveMonitoringContext:
    """Context for rendering an active monitoring card on the module detail screen."""

    id: int
    status: str
    status_label: str
    started_at_display: str
    execution_url: str


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _format_date_spanish(dt: datetime) -> str:
    """Format a datetime as 'DD Mon YYYY' with Spanish month abbreviation.

    Converts UTC to America/Bogota before formatting.
    Example: datetime(2025, 6, 12, 5, 0) UTC → "12 Jun 2025" (Bogota)
    """
    local_dt = to_bogota(dt)
    if local_dt is None:
        return ""
    month_abbr = _SPANISH_MONTHS.get(local_dt.month, "???")
    return f"{local_dt.day} {month_abbr} {local_dt.year}"


def _format_time(dt: datetime) -> str:
    """Format a datetime's time component as 'HH:MM'.

    Converts UTC to America/Bogota before formatting.
    Example: datetime(..., hour=5, minute=30) UTC → "00:30" (Bogota)
    """
    local_dt = to_bogota(dt)
    if local_dt is None:
        return ""
    return f"{local_dt.hour:02d}:{local_dt.minute:02d}"


def _get_last_monitoring_date(monitorings: list) -> Optional[str]:
    """Find the most recent completed monitoring and format its started_at date.

    Args:
        monitorings: List of MonitoringModel instances (or objects with
            `status` and `started_at` attributes).

    Returns:
        Formatted date string (e.g., "12 Jun 2025") for the most recent
        completed monitoring, or None if no completed monitorings exist.
    """
    completed = [m for m in monitorings if m.status == "completed"]
    if not completed:
        return None

    most_recent = max(completed, key=lambda m: m.started_at)
    return _format_date_spanish(most_recent.started_at)


# Status labels for active monitoring card (Spanish, farmer-friendly).
# Video-first wording: running = Grabando (Recording), analyzing = Procesando
# (Processing). See Spec 019 Task 14.2.
_ACTIVE_STATUS_LABELS = {
    "initializing": "Inicializando",
    "running": "Grabando",
    "paused": "Pausado",
    "finishing": "Finalizando",
    "analyzing": "Procesando video",
}

# Active statuses for the module detail card
_ACTIVE_MONITORING_STATUSES = {"initializing", "running", "paused", "finishing", "analyzing"}


def build_active_monitoring_context(
    monitorings: list,
) -> Optional[ActiveMonitoringContext]:
    """Build context for the active monitoring card on module detail.

    Returns a context object for the most recent active (non-terminal) monitoring,
    or None if no active monitoring exists.

    Args:
        monitorings: List of monitoring objects with status and started_at attributes.

    Returns:
        ActiveMonitoringContext or None.
    """
    active = [m for m in monitorings if m.status in _ACTIVE_MONITORING_STATUSES]
    if not active:
        return None

    # Pick the most recent if multiple exist (shouldn't normally happen)
    most_recent = max(active, key=lambda m: m.started_at)

    status_label = _ACTIVE_STATUS_LABELS.get(most_recent.status, most_recent.status)
    started_at_display = (
        f"{_format_date_spanish(most_recent.started_at)} — "
        f"{_format_time(most_recent.started_at)}"
    )

    return ActiveMonitoringContext(
        id=most_recent.id,
        status=most_recent.status,
        status_label=status_label,
        started_at_display=started_at_display,
        execution_url=f"/monitoreos/{most_recent.id}/ejecucion",
    )


# ---------------------------------------------------------------------------
# Context builder functions
# ---------------------------------------------------------------------------


def build_greenhouse_cards(
    greenhouses: list,
    modules_by_gh: dict[int, list],
    monitorings_by_module: dict[int, list],
) -> list[GreenhouseCardContext]:
    """Build context cards for the greenhouse list screen.

    Args:
        greenhouses: List of GreenhouseModel instances.
        modules_by_gh: Mapping of greenhouse_id → list of ModuleModel instances.
        monitorings_by_module: Mapping of module_id → list of MonitoringModel instances.

    Returns:
        One GreenhouseCardContext per greenhouse with module count and
        last monitoring date across all its modules.
    """
    cards: list[GreenhouseCardContext] = []

    for gh in greenhouses:
        modules = modules_by_gh.get(gh.id, [])
        module_count = len(modules)

        # Collect all monitorings across all modules for this greenhouse
        all_monitorings: list = []
        for module in modules:
            all_monitorings.extend(monitorings_by_module.get(module.id, []))

        last_date = _get_last_monitoring_date(all_monitorings)

        cards.append(
            GreenhouseCardContext(
                id=gh.id,
                name=gh.name,
                module_count=module_count,
                last_monitoring_date=last_date,
            )
        )

    return cards


def build_module_cards(
    modules: list,
    monitorings_by_module: dict[int, list],
) -> list[ModuleCardContext]:
    """Build context cards for the greenhouse detail screen.

    Args:
        modules: List of ModuleModel instances.
        monitorings_by_module: Mapping of module_id → list of MonitoringModel instances.

    Returns:
        One ModuleCardContext per module with formatted dimensions and
        last monitoring date.
    """
    cards: list[ModuleCardContext] = []

    for module in modules:
        # Format dimensions as "{width} × {length} m" when both are set
        if module.width_m is not None and module.length_m is not None:
            dimensions = f"{module.width_m} × {module.length_m} m"
        else:
            dimensions = None

        monitorings = monitorings_by_module.get(module.id, [])
        last_date = _get_last_monitoring_date(monitorings)

        cards.append(
            ModuleCardContext(
                id=module.id,
                name=module.name,
                crop_type=module.crop_type,
                dimensions=dimensions,
                last_monitoring_date=last_date,
            )
        )

    return cards


def build_monitoring_history(
    monitorings: list,
    metrics_by_monitoring: dict[int, object],
) -> list[MonitoringHistoryItem]:
    """Build context items for the monitoring history list.

    Only includes monitorings with terminal status (completed, aborted, or error).
    Sorted by date descending (most recent first).

    Args:
        monitorings: List of MonitoringModel instances.
        metrics_by_monitoring: Mapping of monitoring_id → MonitoringMetricsModel instance.

    Returns:
        List of MonitoringHistoryItem sorted by started_at descending.
    """
    terminal_statuses = {"completed", "aborted", "error"}
    terminal_monitorings = [m for m in monitorings if m.status in terminal_statuses]

    # Sort by started_at descending (most recent first)
    terminal_monitorings.sort(key=lambda m: m.started_at, reverse=True)

    items: list[MonitoringHistoryItem] = []

    for monitoring in terminal_monitorings:
        metrics = metrics_by_monitoring.get(monitoring.id)
        total_tomatoes = monitoring.total_detections
        pct_healthy = metrics.pct_healthy if metrics else 0.0

        items.append(
            MonitoringHistoryItem(
                id=monitoring.id,
                date=_format_date_spanish(monitoring.started_at),
                time=_format_time(monitoring.started_at),
                total_tomatoes=total_tomatoes,
                pct_healthy=pct_healthy,
                status=monitoring.status,
            )
        )

    return items


def validate_dimensions(
    width: float, length: float
) -> tuple[bool, Optional[str]]:
    """Validate module dimensions for monitoring setup.

    Args:
        width: Module width in meters.
        length: Module length in meters.

    Returns:
        Tuple of (is_valid, error_message).
        (True, None) if both dimensions are greater than zero.
        (False, error_message) otherwise, with message in Spanish.
    """
    if width <= 0 and length <= 0:
        return (False, "El ancho y el largo deben ser mayores a cero.")
    if width <= 0:
        return (False, "El ancho debe ser mayor a cero.")
    if length <= 0:
        return (False, "El largo debe ser mayor a cero.")
    return (True, None)


def build_report_metrics(metrics: object) -> ReportMetrics:
    """Build context for the monitoring report metric cards.

    Maps MonitoringMetricsModel fields to a ReportMetrics dataclass,
    preserving exact numeric values.

    Args:
        metrics: A MonitoringMetricsModel instance with all percentage
            and count fields.

    Returns:
        ReportMetrics dataclass with all fields mapped directly.
    """
    maturity_stages: dict[str, float] = {
        "green": metrics.pct_green,
        "breaker": metrics.pct_breaker,
        "turning": metrics.pct_turning,
        "pink": metrics.pct_pink,
        "light_red": metrics.pct_light_red,
        "red": metrics.pct_red,
    }

    return ReportMetrics(
        total_tomatoes=metrics.total_tomatoes,
        healthy_count=metrics.healthy_count,
        unhealthy_count=metrics.unhealthy_count,
        pct_healthy=metrics.pct_healthy,
        pct_unhealthy=metrics.pct_unhealthy,
        maturity_stages=maturity_stages,
        snapshots_with_detections=metrics.snapshots_with_detections,
    )


def build_snapshot_gallery(
    snapshots: list,
    monitoring_id: int,
) -> list[SnapshotThumbnail]:
    """Build context for the snapshot gallery grid.

    Prefers annotated snapshots when present on disk, falls back to raw
    snapshots. Only includes snapshots where has_detections is True and
    whose image_path passes path traversal validation against OUTPUTS_DIR.

    Args:
        snapshots: List of SnapshotModel instances.
        monitoring_id: The monitoring ID for constructing image URLs.

    Returns:
        List of SnapshotThumbnail for snapshots with detections and valid paths,
        sorted by frame_index ascending.
    """
    from pathlib import Path as _Path

    thumbnails: list[SnapshotThumbnail] = []
    monitoring_dir = OUTPUTS_DIR / "monitorings" / str(monitoring_id)

    for snapshot in snapshots:
        if not snapshot.has_detections:
            continue

        # Validate that image_path resolves within OUTPUTS_DIR
        try:
            validate_safe_path(snapshot.image_path, OUTPUTS_DIR)
        except PathTraversalError:
            logger.warning(
                "Skipping snapshot %d — image_path failed path validation",
                snapshot.id if hasattr(snapshot, "id") and snapshot.id else snapshot.frame_index,
            )
            continue

        # Build filename with 6-digit padding
        filename = f"snapshot_{snapshot.frame_index:06d}.jpg"

        # Check if annotated snapshot exists on disk
        annotated_path = monitoring_dir / "annotated_snapshots" / filename
        if annotated_path.exists():
            image_url = f"/snapshots/{monitoring_id}/annotated_snapshots/{filename}"
        else:
            # Fallback to raw snapshot
            image_url = f"/snapshots/{monitoring_id}/snapshots/raw/{filename}"

        thumbnails.append(
            SnapshotThumbnail(
                monitoring_id=monitoring_id,
                frame_index=snapshot.frame_index,
                image_url=image_url,
                has_detections=True,
            )
        )

    # Sort by frame_index ascending for stable gallery ordering
    thumbnails.sort(key=lambda t: t.frame_index)

    return thumbnails
