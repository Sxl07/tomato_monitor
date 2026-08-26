"""Optional Supabase configuration for remote integration.

Reads environment variables to determine whether remote sync with
Supabase is enabled. When variables are absent the system operates
in offline-only mode without errors or warnings.

No network requests are performed by this module.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_DEFAULT_STORAGE_BUCKET = "tomato-monitor-snapshots"


@dataclass(frozen=True)
class SupabaseConfig:
    """Immutable configuration for Supabase remote integration.

    Attributes:
        url: Base Supabase project URL (normalized, no trailing slash).
        publishable_key: Supabase publishable key for authenticated requests.
        storage_bucket: Object storage bucket name for snapshot images.
    """

    url: str
    publishable_key: str
    storage_bucket: str

    @property
    def is_configured(self) -> bool:
        """Return True if the configuration has valid url and key."""
        return bool(self.url and self.publishable_key)

    @property
    def auth_url(self) -> str:
        """Supabase Auth endpoint URL."""
        return f"{self.url}/auth/v1"

    @property
    def rest_url(self) -> str:
        """Supabase PostgREST endpoint URL."""
        return f"{self.url}/rest/v1"

    @property
    def storage_url(self) -> str:
        """Supabase Storage endpoint URL."""
        return f"{self.url}/storage/v1"


def load_supabase_config() -> Optional[SupabaseConfig]:
    """Load Supabase configuration from environment variables.

    Reads:
        SUPABASE_URL: Base project URL (required for remote mode).
        SUPABASE_PUBLISHABLE_KEY: Supabase publishable key (required for
            remote mode).
        SUPABASE_STORAGE_BUCKET: Bucket name (optional, defaults to
            'tomato-monitor-snapshots').

    Detects and rejects:
        SUPABASE_SERVICE_ROLE_KEY: Administrative credentials are not
            allowed on edge devices.

    Returns:
        SupabaseConfig instance if fully configured, None otherwise.
        Returns None without error when variables are absent (offline-only).
    """
    # --- Reject administrative credentials ---
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if service_role_key:
        logger.error(
            "Administrative Supabase credentials are not allowed on this "
            "device; remote integration disabled."
        )
        return None

    # --- Read primary variables ---
    raw_url = os.environ.get("SUPABASE_URL", "").strip()
    raw_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip()

    # --- Offline-only: both absent ---
    if not raw_url and not raw_key:
        return None

    # --- Partial configuration ---
    if raw_url and not raw_key:
        logger.warning(
            "SUPABASE_URL is set but SUPABASE_PUBLISHABLE_KEY is missing; "
            "remote integration disabled."
        )
        return None

    if raw_key and not raw_url:
        logger.warning(
            "SUPABASE_PUBLISHABLE_KEY is set but SUPABASE_URL is missing; "
            "remote integration disabled."
        )
        return None

    # --- Validate URL ---
    parsed = urlparse(raw_url)
    if not parsed.scheme or not parsed.netloc:
        logger.warning(
            "SUPABASE_URL is not a valid URL (missing scheme or host); "
            "remote integration disabled."
        )
        return None

    if parsed.scheme != "https":
        logger.warning(
            "SUPABASE_URL requires https scheme (got '%s'); "
            "remote integration disabled.",
            parsed.scheme,
        )
        return None

    # --- Normalize URL (strip trailing slash) ---
    url = raw_url.rstrip("/")

    # --- Storage bucket ---
    raw_bucket = os.environ.get("SUPABASE_STORAGE_BUCKET", "").strip()
    storage_bucket = raw_bucket if raw_bucket else _DEFAULT_STORAGE_BUCKET

    return SupabaseConfig(
        url=url,
        publishable_key=raw_key,
        storage_bucket=storage_bucket,
    )
