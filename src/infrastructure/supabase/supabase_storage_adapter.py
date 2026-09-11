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

from src.application.interfaces.remote_download_port import RemoteDownloadResult
from src.application.interfaces.remote_storage_port import (
    RemoteStorageDeleteResult,
    RemoteUploadResult,
)
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


def _classify_delete_error(response: httpx.Response) -> RemoteStorageDeleteResult:
    """Classify an HTTP error response for a Storage delete operation.

    Not-found responses are handled by the caller as idempotent success and
    never reach this function. All classifications here are retryable errors
    (the object is not marked as removed).
    """
    status = response.status_code
    error_msg = _extract_error_message(response)

    # 5xx → REMOTE_UNAVAILABLE
    if status in (500, 502, 503):
        return RemoteStorageDeleteResult(
            success=False,
            error_type="REMOTE_UNAVAILABLE",
            error_message=error_msg,
        )

    # 401/403 → RLS_DENIED (permission / RLS)
    if status in (401, 403):
        return RemoteStorageDeleteResult(
            success=False,
            error_type="RLS_DENIED",
            error_message=error_msg,
        )

    # Other 4xx that are clearly storage-specific
    if status in (400, 409, 413, 422):
        return RemoteStorageDeleteResult(
            success=False,
            error_type="STORAGE_ERROR",
            error_message=error_msg,
        )

    # Default fallback
    return RemoteStorageDeleteResult(
        success=False,
        error_type="UNKNOWN",
        error_message=error_msg,
    )


def _classify_download_error(response: httpx.Response) -> RemoteDownloadResult:
    """Classify an HTTP error response for a Storage download (Spec 022, D3.2).

    Not-found (404) is a distinct outcome (a missing object, not a hard error)
    so the caller can count it as skipped. 401/403 map to RLS_DENIED, 5xx to
    REMOTE_UNAVAILABLE, and other storage-specific 4xx to STORAGE_ERROR.
    """
    status = response.status_code
    error_msg = _extract_error_message(response)

    # 404 → NOT_FOUND (object absent; not a hard failure)
    if status == 404:
        return RemoteDownloadResult(
            success=False,
            error_type="NOT_FOUND",
            error_message=error_msg,
        )

    # 401/403 → RLS_DENIED
    if status in (401, 403):
        return RemoteDownloadResult(
            success=False,
            error_type="RLS_DENIED",
            error_message=error_msg,
        )

    # 5xx → REMOTE_UNAVAILABLE
    if status in (500, 502, 503):
        return RemoteDownloadResult(
            success=False,
            error_type="REMOTE_UNAVAILABLE",
            error_message=error_msg,
        )

    # Any other 4xx (400/405/409/413/422/429/...) → STORAGE_ERROR
    if 400 <= status < 500:
        return RemoteDownloadResult(
            success=False,
            error_type="STORAGE_ERROR",
            error_message=error_msg,
        )

    # Default fallback (e.g. unexpected 2xx like 204, or other 5xx)
    return RemoteDownloadResult(
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

    def remove_object(
        self,
        access_token: str,
        path: str,
    ) -> RemoteStorageDeleteResult:
        """Delete a single object from Supabase Storage.

        Idempotent behavior: if the object does not exist remotely (404), the
        deletion is treated as success with already_absent=True. Any other
        failure is a retryable error and does NOT mark the object as removed.

        Args:
            access_token: Ephemeral JWT for authenticating the request.
            path: Object path within the configured bucket.

        Returns:
            RemoteStorageDeleteResult with success=True on deletion or when the
            object was already absent, or success=False with a retryable error
            classification on failure.
        """
        # Validate path presence before network call
        if not isinstance(path, str) or not path:
            return RemoteStorageDeleteResult(
                success=False,
                error_type="STORAGE_ERROR",
                error_message="Empty object path",
            )

        url = (
            f"{self._config.storage_url}/object/"
            f"{self._config.storage_bucket}/{path}"
        )

        try:
            with self._build_delete_client(access_token) as client:
                response = client.delete(url)
        except httpx.TimeoutException:
            return RemoteStorageDeleteResult(
                success=False,
                error_type="CONNECTIVITY",
                error_message="Request timed out",
            )
        except httpx.RequestError as exc:
            return RemoteStorageDeleteResult(
                success=False,
                error_type="CONNECTIVITY",
                error_message=f"Connection failed: {type(exc).__name__}",
            )

        # Idempotent: object already absent → success
        if response.status_code == 404:
            return RemoteStorageDeleteResult(
                success=True,
                already_absent=True,
            )

        if not response.is_success:
            return _classify_delete_error(response)

        return RemoteStorageDeleteResult(success=True)

    def download_object(
        self,
        access_token: str,
        remote_path: str,
    ) -> RemoteDownloadResult:
        """Download a single object from Supabase Storage (Spec 022, D3.2).

        Uses the authenticated Storage endpoint against a PRIVATE bucket. No
        signed/public URLs, no service_role, no retries, no local filesystem
        access. RLS/Storage policies are the access authority.

        Args:
            access_token: Ephemeral JWT for authenticating the request.
            remote_path: Object path within the configured bucket.

        Returns:
            RemoteDownloadResult with success=True and content (bytes) on HTTP
            200, or success=False with an error classification on failure.
        """
        if not isinstance(remote_path, str) or not remote_path:
            return RemoteDownloadResult(
                success=False,
                error_type="STORAGE_ERROR",
                error_message="Empty object path",
            )

        url = (
            f"{self._config.storage_url}/object/authenticated/"
            f"{self._config.storage_bucket}/{remote_path}"
        )

        try:
            with self._build_download_client(access_token) as client:
                response = client.get(url)
        except httpx.TimeoutException:
            return RemoteDownloadResult(
                success=False,
                error_type="CONNECTIVITY",
                error_message="Request timed out",
            )
        except httpx.RequestError as exc:
            return RemoteDownloadResult(
                success=False,
                error_type="CONNECTIVITY",
                error_message=f"Connection failed: {type(exc).__name__}",
            )

        # Only an exact HTTP 200 with a body is a successful download. Any other
        # status (including 201/204) is classified; a 204 must NOT be treated as
        # a successful empty file.
        if response.status_code == 200:
            return RemoteDownloadResult(
                success=True,
                content=response.content,
            )

        return _classify_download_error(response)

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

    def _build_delete_client(self, access_token: str) -> httpx.Client:
        """Create a configured httpx Client for a single object deletion.

        Unlike the upload client, this does not set upload-specific headers
        (Content-Type: image/jpeg, x-upsert).
        """
        kwargs: dict[str, Any] = {
            "timeout": self._timeout_seconds,
            "headers": {
                "apikey": self._config.publishable_key,
                "Authorization": f"Bearer {access_token}",
            },
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def _build_download_client(self, access_token: str) -> httpx.Client:
        """Create a configured httpx Client for a single object download.

        Only authentication headers are set (Bearer JWT + apikey); no
        upload-specific headers (Content-Type, x-upsert).
        """
        kwargs: dict[str, Any] = {
            "timeout": self._timeout_seconds,
            "headers": {
                "apikey": self._config.publishable_key,
                "Authorization": f"Bearer {access_token}",
            },
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)
