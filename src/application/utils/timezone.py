"""Centralized timezone conversion utilities for Tomato Monitor.

Convention:
- PERSISTENCE: UTC (naive datetimes in DB are interpreted as UTC)
- DISPLAY: America/Bogota
- USER INPUT: America/Bogota -> convert to UTC before persisting

Colombia (America/Bogota) has NO daylight saving time.
Offset is always UTC-5.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BOGOTA_TZ = ZoneInfo("America/Bogota")


def utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


def to_bogota(dt: "datetime | None") -> "datetime | None":
    """Convert UTC datetime (naive or aware) to America/Bogota.

    Naive datetimes are assumed to be UTC.
    Returns None if input is None.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BOGOTA_TZ)


def bogota_to_utc(dt: "datetime | None") -> "datetime | None":
    """Convert a Bogota-local datetime to UTC naive (for persistence).

    Input is assumed to be America/Bogota local time.
    Returns naive UTC datetime suitable for SQLite storage.
    Returns None if input is None.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Assume it's Bogota local time
        dt = dt.replace(tzinfo=BOGOTA_TZ)
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.replace(tzinfo=None)  # Store as naive UTC


def format_bogota(dt: "datetime | None", fmt: str = "%d/%m/%Y %H:%M") -> str:
    """Convert UTC datetime to Bogota and format as string.

    Returns empty string if input is None.
    """
    local = to_bogota(dt)
    if local is None:
        return ""
    return local.strftime(fmt)


def iso_utc(dt: "datetime | None") -> str:
    """Format datetime as ISO-8601 with explicit UTC 'Z' indicator.

    Guarantees that 'Z' means real UTC:
    - Naive datetimes: assumed to already be UTC, stamped with Z.
    - Aware datetimes: converted to UTC via astimezone() FIRST, then Z.
    Returns empty string if input is None.
    """
    if dt is None:
        return ""
    if dt.tzinfo is None:
        # Assume naive = UTC
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        # CONVERT to UTC regardless of source timezone
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")