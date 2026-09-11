"""SupabaseRemoteReadAdapter: infrastructure adapter for Supabase PostgREST reads.

Implements RemoteReadPort using httpx synchronous HTTP calls against the
Supabase PostgREST endpoint. Performs owner-scoped, paginated reads used by
the recovery flow (Spec 022, block D1).

This adapter:
- Fails closed for tables outside a fixed whitelist (no HTTP request made).
- Applies caller ``filters`` as PostgREST equality only (no operator syntax).
- Scopes ``greenhouses`` by owner_user_id as defense-in-depth; child tables
  rely on Row-Level Security via the JWT (no owner filter added).
- Paginates deterministically by id with the PostgREST Range header and never
  returns a partial dataset as success: any failing page fails the whole read.
- Does NOT retry internally (retry policy belongs to higher layers).
- Does NOT persist or log secrets (JWT, publishable key, full response body).
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from src.application.interfaces.remote_read_port import RemoteQueryResult
from src.infrastructure.supabase.supabase_config import SupabaseConfig


# Tables the recovery flow is allowed to read. Any other table fails closed.
_ALLOWED_TABLES = frozenset(
    {
        "greenhouses",
        "modules",
        "monitorings",
        "monitoring_metrics",
        "snapshots",
        "inspection_results",
        "activity_logs",
    }
)

# PostgREST page size for Range-based pagination.
_PAGE_SIZE = 1000


def _safe_json_list(response: httpx.Response) -> Optional[list]:
    """Attempt to parse the response body as a JSON list. Return None on failure."""
    try:
        data = response.json()
    except Exception:
        return None
    if isinstance(data, list):
        return data
    return None


def _extract_error_message(response: httpx.Response) -> str:
    """Extract a short, safe error message; never expose the full body."""
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        for key in ("message", "msg", "details", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return f"HTTP {response.status_code}"


def _classify_error(response: httpx.Response) -> RemoteQueryResult:
    """Classify an HTTP error response into the Read error taxonomy."""
    status = response.status_code
    error_msg = _extract_error_message(response)

    # 5xx → REMOTE_UNAVAILABLE
    if status in (500, 502, 503):
        return RemoteQueryResult(
            success=False,
            error_type="REMOTE_UNAVAILABLE",
            error_message=error_msg,
        )

    # 401/403 → RLS_DENIED
    if status in (401, 403):
        return RemoteQueryResult(
            success=False,
            error_type="RLS_DENIED",
            error_message=error_msg,
        )

    # Any other non-2xx
    return RemoteQueryResult(
        success=False,
        error_type="UNKNOWN",
        error_message=error_msg,
    )


class SupabaseRemoteReadAdapter:
    """Infrastructure adapter implementing RemoteReadPort via Supabase PostgREST.

    Uses an httpx synchronous client with optional transport injection for
    testing. Each read operation creates a short-lived HTTP client.
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
            },
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def _build_params(
        self,
        table: str,
        owner_user_id: str,
        filters: Optional[dict],
    ) -> dict[str, str]:
        """Build PostgREST query params: select, deterministic order, equality filters.

        Caller-provided ``filters`` are applied as plain equality only
        (``field=eq.value``); operator syntax is not accepted. For the
        ``greenhouses`` table only, an ``owner_user_id=eq.{owner_user_id}``
        filter is added as defense-in-depth; child tables rely on RLS.
        """
        params: dict[str, str] = {
            "select": "*",
            # Deterministic ordering so successive Range windows are disjoint.
            "order": "id.asc",
        }

        if filters:
            for key, value in filters.items():
                params[key] = f"eq.{value}"

        # Defense-in-depth owner scoping for the only owner-bearing table.
        if table == "greenhouses" and owner_user_id:
            params["owner_user_id"] = f"eq.{owner_user_id}"

        return params

    def fetch_by_owner(
        self,
        access_token: str,
        table: str,
        owner_user_id: str,
        filters: Optional[dict] = None,
    ) -> RemoteQueryResult:
        """Read records of ``table`` visible to the given owner (paginated).

        See RemoteReadPort for the full contract. Fails closed for tables
        outside the whitelist without any HTTP request. Paginates by id using
        the PostgREST Range header and never returns a partial dataset as
        success: any failing page fails the entire read.
        """
        # Fail closed for disallowed tables — no network request.
        if table not in _ALLOWED_TABLES:
            return RemoteQueryResult(
                success=False,
                error_type="UNKNOWN",
                error_message="Table is not allowed for recovery reads",
            )

        url = f"{self._config.rest_url}/{table}"
        params = self._build_params(table, owner_user_id, filters)

        rows: list[dict] = []
        offset = 0

        try:
            with self._build_client(access_token) as client:
                while True:
                    range_start = offset
                    range_end = offset + _PAGE_SIZE - 1
                    headers = {"Range": f"{range_start}-{range_end}"}

                    response = client.get(url, params=params, headers=headers)

                    if not response.is_success:
                        # A failing page fails the whole read (no partial data).
                        return _classify_error(response)

                    page = _safe_json_list(response)
                    if page is None:
                        return RemoteQueryResult(
                            success=False,
                            error_type="UNKNOWN",
                            error_message="Unexpected response body format",
                        )

                    rows.extend(page)

                    # A short page (fewer than a full window) means we are done.
                    if len(page) < _PAGE_SIZE:
                        break

                    offset += _PAGE_SIZE
        except httpx.TimeoutException:
            return RemoteQueryResult(
                success=False,
                error_type="CONNECTIVITY",
                error_message="Request timed out",
            )
        except httpx.RequestError as exc:
            return RemoteQueryResult(
                success=False,
                error_type="CONNECTIVITY",
                error_message=f"Connection failed: {type(exc).__name__}",
            )

        return RemoteQueryResult(success=True, rows=rows)
