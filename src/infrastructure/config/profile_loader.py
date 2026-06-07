"""Profile loader for execution profiles.

Provides functions to load named profiles with optional overrides
and to generate configuration summaries for traceability.
"""
from __future__ import annotations

import dataclasses
from typing import Any

from src.infrastructure.config.settings import (
    EDGE_PROFILE,
    FULL_PROFILE,
    ExecutionProfile,
)

_PROFILES = {
    "edge": EDGE_PROFILE,
    "full": FULL_PROFILE,
}


def load_profile(profile_name: str, **overrides: Any) -> ExecutionProfile:
    """Load a named profile, optionally overriding individual parameters.

    Args:
        profile_name: "edge" or "full"
        **overrides: keyword args matching ExecutionProfile fields

    Returns:
        ExecutionProfile instance with overrides applied.

    Raises:
        ValueError: if profile_name is not recognized.
    """
    if profile_name not in _PROFILES:
        valid = ", ".join(sorted(_PROFILES.keys()))
        raise ValueError(
            f"Unknown profile '{profile_name}'. Valid profiles: {valid}"
        )

    base = _PROFILES[profile_name]

    if overrides:
        return dataclasses.replace(base, **overrides)

    return base


def get_config_summary(profile: ExecutionProfile) -> dict[str, Any]:
    """Return a JSON-serializable dict of all profile fields.

    Used for monitoring session metadata and benchmark reports.
    """
    return dataclasses.asdict(profile)
