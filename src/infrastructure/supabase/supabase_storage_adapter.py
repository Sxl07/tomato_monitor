"""SupabaseStorageAdapter: infrastructure adapter for Supabase Storage uploads.

Implements RemoteStoragePort using httpx synchronous HTTP calls against
the Supabase Storage REST API. Uploads files with x-upsert=true for
idempotent retry behavior.

This adapter:
- Does NOT generate UUIDs or filenames (receives deterministic remote_path).
- Does NOT persist tokens or maintain state between calls.
- Does NOT delete, move, or modify local files.
- Does NOT create signed/public URLs.
- Does NOT retry internally (retry policy is in RemoteSyncService).
- Does NOT log secrets (JWT, keys, file content).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import httpx

from src.application.interfaces.remote_storage_port import RemoteUploadResult
from src.infrastructure.supabase.supabase_config import SupabaseConfig


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------


def _safe_json(response: httpx.Response) -> Optional[dict]:
    """Attempt to parse response body as JSON dict. Return None on failure."""
    try:
        data = response.json()
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _extract_error_message(response: httpx.Response) -> str:
    """Extract a safe error message from Storage error response."""
    payload = _safe_json(response)
    if payload is not None:
        for key in ("message", "msg", "error", "details"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return f"HTTP {response.status_code}"


def _classify_error(response: httpx.Response) -> RemoteUploadResult:
    """Classify an HTTP error response into the Storage error taxonomy."""
    status = response.status_code
    error_msg = _extract_error_message(response)

    # 5xx → REMOTE_UNAVAILABLE
    if status in (500, 502, 503):
        return RemoteUploadResult(
            success=False,
            error_type="REMOTE_UNAVAILABLE",
            error_message=error_msg,
        )

    # 403 → RLS_DENIED
    if status == 403:
        return RemoteUploadResult(
            success=False,
            error_type="RLS_DENIED",
            error_message=error_msg,
        )

    # Other 4xx that are clearly storage-specific
    if status in (404, 409, 413, 422):
        return RemoteUploadResult(
            success=False,
            error_type="STORAGE_ERROR",
            error_message=error_msg,
        )

    # Default fallback
    return RemoteUploadResult(
        success=False,
        error_type="UNKNOWN",
        error_message=error_msg,
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class SupabaseStorageAdapter:
    """Infrastructure adapter implementing RemoteStoragePort via Supabase Storage.

    Uses httpx synchronous client with optional transport injection for testing.
    Each upload operation creates a short-lived HTTP client.
    """

    def __init__(
        self,
        config: SupabaseConfig,
        timeout_seconds: float = 60.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        """Initialize the adapter.

        Args:
            config: Supabase configuration with storage_url, storage_bucket,
                    and publishable_key.
            timeout_seconds: HTTP timeout for each upload request.
            transport: Optional httpx transport for testing (MockTransport).
        """
        self._config = config
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    @staticmethod
    def build_snapshot_path(
        remote_monitoring_uuid: str,
        frame_index: int,
        snapshot_type: str,
    ) -> str:
        """Build deterministic remote path for a snapshot image.

        Args:
            remote_monitoring_uuid: Remote UUID of the monitoring session.
            frame_index: Zero-based frame sequence number.
            snapshot_type: "raw" or "annotated".

        Returns:
            Path relative to the storage bucket, e.g.:
            monitorings/<uuid>/raw/snapshot_000007.jpg
        """
        return f"monitorings/{remote_monitoring_uuid}/{snapshot_type}/snapshot_{frame_index:06d}.jpg"

    def upload_file(
        self,
        access_token: str,
        local_file_path: str,
        remote_path: str,
    ) -> RemoteUploadResult:
        """Upload a local file to Supabase Storage.

        Uses x-upsert=true for idempotent overwrites on retry.
        Content-Type is image/jpeg (all snapshots in this system are JPEG).

        Args:
            access_token: Ephemeral JWT for authenticating the request.
            local_file_path: Absolute or relative path to the local file.
            remote_path: Destination path within the configured bucket.

        Returns:
            RemoteUploadResult with success and object_path on success,
            or error classification on failure.
        """
        # Pre-check: local file must exist
        local_path = Path(local_file_path)
        if not local_path.is_file():
            return RemoteUploadResult(
                success=False,
                error_type="STORAGE_ERROR",
                error_message="Local file not found",
            )

        url = (
            f"{self._config.storage_url}/object/"
            f"{self._config.storage_bucket}/{remote_path}"
        )

        try:
            with open(local_file_path, "rb") as file_handle:
                with self._build_client(access_token) as client:
                    response = client.post(url, content=file_handle)
        except httpx.TimeoutException:
            return RemoteUploadResult(
                success=False,
                error_type="CONNECTIVITY",
                error_message="Request timed out",
            )
        except httpx.RequestError as exc:
            return RemoteUploadResult(
                success=False,
                error_type="CONNECTIVITY",
                error_message=f"Connection failed: {type(exc).__name__}",
            )
        except OSError:
            return RemoteUploadResult(
                success=False,
                error_type="STORAGE_ERROR",
                error_message="Failed to read local file",
            )

        if not response.is_success:
            return _classify_error(response)

        return RemoteUploadResult(
            success=True,
            object_path=remote_path,
        )

    def _build_client(self, access_token: str) -> httpx.Client:
        """Create a configured httpx Client for a single upload."""
        kwargs: dict[str, Any] = {
            "timeout": self._timeout_seconds,
            "headers": {
                "apikey": self._config.publishable_key,
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "image/jpeg",
                "x-upsert": "true",
            },
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)
