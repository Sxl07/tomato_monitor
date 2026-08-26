"""SupabaseAuthAdapter: infrastructure adapter for Supabase Auth REST API.

Implements RemoteAuthPort using httpx synchronous HTTP calls against
the Supabase GoTrue Auth endpoints. Classifies errors into the standard
error_type taxonomy defined in the port contract.

This adapter:
- Does NOT persist tokens, sessions, or user records locally.
- Does NOT call /rest/v1/profiles (profile creation is via Supabase trigger).
- Does NOT perform health checks before auth attempts.
- Does NOT log secrets (passwords, tokens, keys).
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from src.application.interfaces.remote_auth_port import RemoteAuthResult
from src.infrastructure.supabase.supabase_config import SupabaseConfig


# ---------------------------------------------------------------------------
# Error classification helpers
# ---------------------------------------------------------------------------

_INVALID_CREDENTIALS_INDICATORS = (
    "invalid_credentials",
    "invalid login credentials",
    "invalid email or password",
)


def _safe_json(response: httpx.Response) -> Optional[dict]:
    """Attempt to parse response body as JSON. Return None on failure."""
    try:
        data = response.json()
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _extract_error_message(payload: Optional[dict]) -> Optional[str]:
    """Extract a safe error message from a Supabase Auth error response."""
    if payload is None:
        return None
    # Supabase Auth uses various fields for the error message
    for key in ("msg", "message", "error_description", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_error_code(payload: Optional[dict]) -> Optional[str]:
    """Extract the error code/identifier from a Supabase Auth error response."""
    if payload is None:
        return None
    for key in ("error_code", "code", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value.lower()
    return None


def _is_invalid_credentials(payload: Optional[dict]) -> bool:
    """Determine if the error body indicates invalid credentials."""
    if payload is None:
        return False
    error_code = _extract_error_code(payload)
    if error_code:
        for indicator in _INVALID_CREDENTIALS_INDICATORS:
            if indicator in error_code:
                return True
    # Also check message fields
    msg = _extract_error_message(payload)
    if msg:
        msg_lower = msg.lower()
        for indicator in _INVALID_CREDENTIALS_INDICATORS:
            if indicator in msg_lower:
                return True
    return False


def _is_email_exists(payload: Optional[dict]) -> bool:
    """Determine if the error body indicates email already registered."""
    if payload is None:
        return False
    # Check explicit error codes
    error_code = _extract_error_code(payload)
    if error_code:
        if error_code in (
            "email_exists",
            "user_already_exists",
            "user_already_registered",
            "email_already_exists",
        ):
            return True
    # Check message fields for explicit registration indicators
    msg = _extract_error_message(payload)
    if msg:
        msg_lower = msg.lower()
        if "already registered" in msg_lower:
            return True
        if "already been registered" in msg_lower:
            return True
        if "user already registered" in msg_lower:
            return True
    return False


def _classify_error(
    response: httpx.Response, operation: str
) -> RemoteAuthResult:
    """Classify an HTTP error response into the standard error taxonomy."""
    status = response.status_code
    payload = _safe_json(response)
    error_msg = _extract_error_message(payload) or f"HTTP {status}"

    # 5xx → REMOTE_UNAVAILABLE
    if status in (500, 502, 503):
        return RemoteAuthResult(
            success=False,
            error_type="REMOTE_UNAVAILABLE",
            error_message=error_msg,
        )

    # 429 → RATE_LIMITED
    if status == 429:
        return RemoteAuthResult(
            success=False,
            error_type="RATE_LIMITED",
            error_message=error_msg,
        )

    # 403 → AUTH_FORBIDDEN
    if status == 403:
        return RemoteAuthResult(
            success=False,
            error_type="AUTH_FORBIDDEN",
            error_message=error_msg,
        )

    # 422 in signup → EMAIL_EXISTS (if body confirms)
    if status == 422 and operation == "sign_up":
        if _is_email_exists(payload):
            return RemoteAuthResult(
                success=False,
                error_type="EMAIL_EXISTS",
                error_message=error_msg,
            )

    # Body-based credential check (400, 401, or other)
    if _is_invalid_credentials(payload):
        return RemoteAuthResult(
            success=False,
            error_type="INVALID_CREDENTIALS",
            error_message=error_msg,
        )

    # EMAIL_EXISTS can also appear as 400 in some Supabase versions
    if operation == "sign_up" and _is_email_exists(payload):
        return RemoteAuthResult(
            success=False,
            error_type="EMAIL_EXISTS",
            error_message=error_msg,
        )

    # Default fallback
    return RemoteAuthResult(
        success=False,
        error_type="UNKNOWN",
        error_message=error_msg,
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class SupabaseAuthAdapter:
    """Infrastructure adapter implementing RemoteAuthPort via Supabase Auth API.

    Uses httpx synchronous client with optional transport injection for testing.
    """

    def __init__(
        self,
        config: SupabaseConfig,
        timeout_seconds: float = 10.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        """Initialize the adapter.

        Args:
            config: Supabase configuration with auth_url and publishable_key.
            timeout_seconds: HTTP timeout for each request.
            transport: Optional httpx transport for testing (MockTransport).
        """
        self._config = config
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def _build_client(self) -> httpx.Client:
        """Create a configured httpx Client."""
        kwargs: dict[str, Any] = {
            "timeout": self._timeout_seconds,
            "headers": {
                "apikey": self._config.publishable_key,
                "Content-Type": "application/json",
            },
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def sign_up(
        self,
        email: str,
        password: str,
        full_name: str,
    ) -> RemoteAuthResult:
        """Register a new user via Supabase Auth /signup endpoint."""
        url = f"{self._config.auth_url}/signup"
        payload = {
            "email": email,
            "password": password,
            "data": {
                "full_name": full_name,
            },
        }

        try:
            with self._build_client() as client:
                response = client.post(url, json=payload)
        except httpx.TimeoutException:
            return RemoteAuthResult(
                success=False,
                error_type="CONNECTIVITY",
                error_message="Request timed out",
            )
        except httpx.RequestError as exc:
            return RemoteAuthResult(
                success=False,
                error_type="CONNECTIVITY",
                error_message=f"Connection failed: {type(exc).__name__}",
            )

        if not response.is_success:
            return _classify_error(response, "sign_up")

        return self._parse_auth_response(response, "sign_up")

    def sign_in(
        self,
        email: str,
        password: str,
    ) -> RemoteAuthResult:
        """Authenticate via Supabase Auth /token?grant_type=password endpoint."""
        url = f"{self._config.auth_url}/token"
        params = {"grant_type": "password"}
        payload = {
            "email": email,
            "password": password,
        }

        try:
            with self._build_client() as client:
                response = client.post(url, json=payload, params=params)
        except httpx.TimeoutException:
            return RemoteAuthResult(
                success=False,
                error_type="CONNECTIVITY",
                error_message="Request timed out",
            )
        except httpx.RequestError as exc:
            return RemoteAuthResult(
                success=False,
                error_type="CONNECTIVITY",
                error_message=f"Connection failed: {type(exc).__name__}",
            )

        if not response.is_success:
            return _classify_error(response, "sign_in")

        return self._parse_auth_response(response, "sign_in")

    def _parse_auth_response(
        self, response: httpx.Response, operation: str
    ) -> RemoteAuthResult:
        """Parse a successful Auth response into RemoteAuthResult.

        Args:
            response: The HTTP response with 2xx status.
            operation: "sign_up" or "sign_in" — determines access_token requirement.
        """
        data = _safe_json(response)
        if data is None:
            return RemoteAuthResult(
                success=False,
                error_type="UNKNOWN",
                error_message="Invalid JSON in response body",
            )

        user = data.get("user")
        if not isinstance(user, dict):
            return RemoteAuthResult(
                success=False,
                error_type="UNKNOWN",
                error_message="Response missing user identity",
            )

        # Validate user.id is a non-empty string
        user_id = user.get("id")
        if not isinstance(user_id, str) or not user_id:
            return RemoteAuthResult(
                success=False,
                error_type="UNKNOWN",
                error_message="Response missing user identity",
            )

        # Validate access_token for sign_in (required)
        access_token = data.get("access_token")
        if operation == "sign_in":
            if not isinstance(access_token, str) or not access_token:
                return RemoteAuthResult(
                    success=False,
                    error_type="UNKNOWN",
                    error_message="Response missing access token",
                )

        # Defensive parsing of user_metadata
        user_metadata = user.get("user_metadata")
        if user_metadata is None:
            full_name = None
        elif not isinstance(user_metadata, dict):
            return RemoteAuthResult(
                success=False,
                error_type="UNKNOWN",
                error_message="Invalid user metadata in response",
            )
        else:
            full_name = user_metadata.get("full_name")

        # Normalize access_token to None if not a valid string
        if not isinstance(access_token, str) or not access_token:
            access_token = None

        return RemoteAuthResult(
            success=True,
            user_id=user_id,
            access_token=access_token,
            email=user.get("email"),
            full_name=full_name,
        )
