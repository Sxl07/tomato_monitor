"""SupabaseDataAdapter: infrastructure adapter for Supabase PostgREST upsert.

Implements RemoteDataPort using httpx synchronous HTTP calls against
the Supabase PostgREST endpoint. Performs idempotent upserts using
pre-generated UUIDs with ON CONFLICT resolution.

This adapter:
- Does NOT generate UUIDs (receives them in data["id"]).
- Does NOT persist tokens or maintain state between calls.
- Does NOT know about specific entity types or SQLAlchemy models.
- Does NOT retry internally (retry policy is in RemoteSyncService).
- Does NOT log secrets (JWT, keys, full payloads).
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from src.application.interfaces.remote_data_port import RemoteUpsertResult
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
    """Extract a safe error message from PostgREST error response."""
    payload = _safe_json(response)
    if payload is not None:
        for key in ("message", "msg", "details", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return f"HTTP {response.status_code}"


def _classify_error(response: httpx.Response) -> RemoteUpsertResult:
    """Classify an HTTP error response into the Data error taxonomy."""
    status = response.status_code
    error_msg = _extract_error_message(response)

    # 5xx → REMOTE_UNAVAILABLE
    if status in (500, 502, 503):
        return RemoteUpsertResult(
            success=False,
            error_type="REMOTE_UNAVAILABLE",
            error_message=error_msg,
        )

    # 403 → RLS_DENIED
    if status == 403:
        return RemoteUpsertResult(
            success=False,
            error_type="RLS_DENIED",
            error_message=error_msg,
        )

    # All other errors
    return RemoteUpsertResult(
        success=False,
        error_type="UNKNOWN",
        error_message=error_msg,
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class SupabaseDataAdapter:
    """Infrastructure adapter implementing RemoteDataPort via Supabase PostgREST.

    Uses httpx synchronous client with optional transport injection for testing.
    Each upsert operation creates a short-lived HTTP client.
    """

    def __init__(
        self,
        config: SupabaseConfig,
        timeout_seconds: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        """Initialize the adapter.

        Args:
            config: Supabase configuration with rest_url and publishable_key.
            timeout_seconds: HTTP timeout for each request.
            transport: Optional httpx transport for testing (MockTransport).
        """
        self._config = config
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def _build_client(self, access_token: str) -> httpx.Client:
        """Create a configured httpx Client with auth headers."""
        kwargs: dict[str, Any] = {
            "timeout": self._timeout_seconds,
            "headers": {
                "apikey": self._config.publishable_key,
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates",
            },
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def upsert(
        self,
        access_token: str,
        table: str,
        data: dict,
    ) -> RemoteUpsertResult:
        """Upsert a record to Supabase PostgREST.

        Sends POST with on_conflict=id and Prefer: resolution=merge-duplicates
        for idempotent upsert behavior.

        Args:
            access_token: Ephemeral JWT for authenticating the request.
            table: Target table name (e.g., "greenhouses", "monitorings").
            data: Record data dict. Must contain "id" with pre-generated UUID.

        Returns:
            RemoteUpsertResult with success and remote_id on success,
            or error classification on failure.
        """
        # Validate id presence before network call
        record_id = data.get("id")
        if not isinstance(record_id, str) or not record_id:
            return RemoteUpsertResult(
                success=False,
                error_type="UNKNOWN",
                error_message="Payload missing required 'id' field",
            )

        url = f"{self._config.rest_url}/{table}"
        params = {"on_conflict": "id"}

        try:
            with self._build_client(access_token) as client:
                response = client.post(url, json=data, params=params)
        except httpx.TimeoutException:
            return RemoteUpsertResult(
                success=False,
                error_type="CONNECTIVITY",
                error_message="Request timed out",
            )
        except httpx.RequestError as exc:
            return RemoteUpsertResult(
                success=False,
                error_type="CONNECTIVITY",
                error_message=f"Connection failed: {type(exc).__name__}",
            )

        if not response.is_success:
            return _classify_error(response)

        return RemoteUpsertResult(
            success=True,
            remote_id=record_id,
        )
