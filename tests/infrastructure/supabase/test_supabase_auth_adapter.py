"""Unit tests for SupabaseAuthAdapter using httpx.MockTransport.

Validates:
- sign_up sends correct URL, payload, headers (no role, no profiles POST)
- sign_in sends correct URL, query, payload, headers (no health check)
- Success parsing: user_id, access_token, email, full_name
- sign_in requires access_token; sign_up allows it absent
- Missing/malformed user, user_metadata, user.id → UNKNOWN
- Invalid JSON 2xx → UNKNOWN
- Error classification: CONNECTIVITY, REMOTE_UNAVAILABLE, INVALID_CREDENTIALS,
  EMAIL_EXISTS, RATE_LIMITED, AUTH_FORBIDDEN, UNKNOWN
- INVALID_CREDENTIALS is body-based, not status-code-based
- EMAIL_EXISTS uses explicit indicators only
- 422 generic → UNKNOWN (not EMAIL_EXISTS)
- EMAIL_EXISTS not used for sign_in
- Error messages do not leak secrets
- No create_profile method exists
- No health check request
- No second request to /profiles
- All HTTP via MockTransport (no real network)

Spec 017 — Supabase Remote Sync.
"""

import json

import httpx
import pytest

from src.application.interfaces.remote_auth_port import RemoteAuthResult
from src.infrastructure.supabase.supabase_auth_adapter import SupabaseAuthAdapter
from src.infrastructure.supabase.supabase_config import SupabaseConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DUMMY_URL = "https://example.supabase.co"
_DUMMY_KEY = "dummy-publishable-key"
_DUMMY_UUID = "11111111-2222-4333-8444-555555555555"


@pytest.fixture
def config():
    return SupabaseConfig(
        url=_DUMMY_URL,
        publishable_key=_DUMMY_KEY,
        storage_bucket="dummy-bucket",
    )


def _make_adapter(config, handler):
    """Create adapter with MockTransport from a handler function."""
    transport = httpx.MockTransport(handler)
    return SupabaseAuthAdapter(config=config, transport=transport)


def _success_response(
    access_token="dummy-jwt",
    user_id=_DUMMY_UUID,
    email="user@example.com",
    full_name="Test User",
    include_token=True,
    user_metadata_override=None,
):
    """Build a standard success JSON body."""
    user = {"id": user_id, "email": email}
    if user_metadata_override is not None:
        user["user_metadata"] = user_metadata_override
    elif full_name is not None:
        user["user_metadata"] = {"full_name": full_name}
    body = {"user": user}
    if include_token and access_token:
        body["access_token"] = access_token
    return body


# ===========================================================================
# sign_up request validation
# ===========================================================================


class TestSignUpRequest:
    """sign_up sends correct URL, payload, and headers."""

    def test_url_is_signup(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == f"{_DUMMY_URL}/auth/v1/signup"
            return httpx.Response(200, json=_success_response())

        adapter = _make_adapter(config, handler)
        adapter.sign_up("user@example.com", "pass123", "Test User")

    def test_payload_contains_email_password_data(self, config):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json=_success_response())

        adapter = _make_adapter(config, handler)
        adapter.sign_up("user@example.com", "StrongPassword123!", "Test User")

        assert captured["email"] == "user@example.com"
        assert captured["password"] == "StrongPassword123!"
        assert captured["data"]["full_name"] == "Test User"

    def test_payload_does_not_contain_role(self, config):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json=_success_response())

        adapter = _make_adapter(config, handler)
        adapter.sign_up("u@e.com", "p", "N")
        assert "role" not in captured
        assert "is_active" not in captured
        assert "remote_user_id" not in captured

    def test_headers_contain_apikey(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["apikey"] == _DUMMY_KEY
            assert "application/json" in request.headers["content-type"]
            return httpx.Response(200, json=_success_response())

        adapter = _make_adapter(config, handler)
        adapter.sign_up("u@e.com", "p", "N")

    def test_no_service_role_in_headers(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            for key, value in request.headers.items():
                assert "service_role" not in value.lower()
            return httpx.Response(200, json=_success_response())

        adapter = _make_adapter(config, handler)
        adapter.sign_up("u@e.com", "p", "N")

    def test_no_profiles_request(self, config):
        requests_made = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests_made.append(str(request.url))
            return httpx.Response(200, json=_success_response())

        adapter = _make_adapter(config, handler)
        adapter.sign_up("u@e.com", "p", "N")

        assert len(requests_made) == 1
        assert "/auth/v1/signup" in requests_made[0]
        assert "/rest/v1/profiles" not in requests_made[0]


# ===========================================================================
# sign_up success
# ===========================================================================


class TestSignUpSuccess:
    """sign_up parses successful responses correctly."""

    def test_success_with_token(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_success_response())

        adapter = _make_adapter(config, handler)
        result = adapter.sign_up("u@e.com", "p", "Test User")

        assert result.success is True
        assert result.user_id == _DUMMY_UUID
        assert result.access_token == "dummy-jwt"
        assert result.email == "user@example.com"
        assert result.full_name == "Test User"
        assert result.error_type is None

    def test_success_without_token(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_success_response(include_token=False))

        adapter = _make_adapter(config, handler)
        result = adapter.sign_up("u@e.com", "p", "Test User")

        assert result.success is True
        assert result.user_id == _DUMMY_UUID
        assert result.access_token is None
        assert result.full_name == "Test User"


# ===========================================================================
# sign_in request validation
# ===========================================================================


class TestSignInRequest:
    """sign_in sends correct URL, query, payload, and headers."""

    def test_url_and_query(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            assert "/auth/v1/token" in url
            assert "grant_type=password" in url
            return httpx.Response(200, json=_success_response())

        adapter = _make_adapter(config, handler)
        adapter.sign_in("u@e.com", "p")

    def test_payload(self, config):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json=_success_response())

        adapter = _make_adapter(config, handler)
        adapter.sign_in("user@example.com", "StrongPassword123!")

        assert captured["email"] == "user@example.com"
        assert captured["password"] == "StrongPassword123!"
        assert "data" not in captured

    def test_headers(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["apikey"] == _DUMMY_KEY
            return httpx.Response(200, json=_success_response())

        adapter = _make_adapter(config, handler)
        adapter.sign_in("u@e.com", "p")

    def test_no_health_check(self, config):
        requests_made = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests_made.append(str(request.url))
            return httpx.Response(200, json=_success_response())

        adapter = _make_adapter(config, handler)
        adapter.sign_in("u@e.com", "p")

        assert len(requests_made) == 1
        assert "/auth/v1/token" in requests_made[0]
        assert "/health" not in requests_made[0]


# ===========================================================================
# sign_in success
# ===========================================================================


class TestSignInSuccess:
    """sign_in parses successful responses correctly."""

    def test_full_success(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_success_response())

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "p")

        assert result.success is True
        assert result.user_id == _DUMMY_UUID
        assert result.access_token == "dummy-jwt"
        assert result.email == "user@example.com"
        assert result.full_name == "Test User"

    def test_missing_access_token_fails(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_success_response(include_token=False))

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "p")

        assert result.success is False
        assert result.error_type == "UNKNOWN"


# ===========================================================================
# full_name handling
# ===========================================================================


class TestFullNameHandling:
    """full_name absent is valid; malformed user_metadata is UNKNOWN."""

    def test_full_name_absent(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            body = {"access_token": "jwt", "user": {"id": _DUMMY_UUID, "email": "u@e.com"}}
            return httpx.Response(200, json=body)

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "p")
        assert result.success is True
        assert result.full_name is None

    def test_user_metadata_none(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            body = {"access_token": "jwt", "user": {"id": _DUMMY_UUID, "user_metadata": None}}
            return httpx.Response(200, json=body)

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "p")
        assert result.success is True
        assert result.full_name is None

    @pytest.mark.parametrize("bad_metadata", ["invalid", [], 123])
    def test_user_metadata_malformed(self, config, bad_metadata):
        def handler(request: httpx.Request) -> httpx.Response:
            body = {"access_token": "jwt", "user": {"id": _DUMMY_UUID, "user_metadata": bad_metadata}}
            return httpx.Response(200, json=body)

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "p")
        assert result.success is False
        assert result.error_type == "UNKNOWN"


# ===========================================================================
# Malformed 2xx responses
# ===========================================================================


class TestMalformed2xxResponses:
    """Various malformed 2xx bodies return UNKNOWN without exceptions."""

    def test_user_absent(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"access_token": "jwt"})

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "p")
        assert result.success is False
        assert result.error_type == "UNKNOWN"

    @pytest.mark.parametrize("bad_user", ["invalid", [], None])
    def test_user_not_dict(self, config, bad_user):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"access_token": "jwt", "user": bad_user})

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "p")
        assert result.success is False
        assert result.error_type == "UNKNOWN"

    @pytest.mark.parametrize("bad_id", [None, "", 12345])
    def test_user_id_invalid(self, config, bad_id):
        def handler(request: httpx.Request) -> httpx.Response:
            body = {"access_token": "jwt", "user": {"id": bad_id}}
            return httpx.Response(200, json=body)

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "p")
        assert result.success is False
        assert result.error_type == "UNKNOWN"

    def test_invalid_json_body(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not-json", headers={"content-type": "text/plain"})

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "p")
        assert result.success is False
        assert result.error_type == "UNKNOWN"


# ===========================================================================
# INVALID_CREDENTIALS (body-based)
# ===========================================================================


class TestInvalidCredentials:
    """INVALID_CREDENTIALS requires body indicators, not just status."""

    def test_400_with_invalid_credentials_code(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={
                "error_code": "invalid_credentials",
                "msg": "Invalid login credentials",
            })

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "wrong")
        assert result.error_type == "INVALID_CREDENTIALS"

    def test_401_with_invalid_credentials_message(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={
                "error": "unauthorized",
                "message": "Invalid login credentials",
            })

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "wrong")
        assert result.error_type == "INVALID_CREDENTIALS"

    def test_400_generic_not_invalid_credentials(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={
                "error_code": "bad_request",
                "msg": "Malformed request",
            })

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "p")
        assert result.error_type == "UNKNOWN"

    def test_401_generic_not_invalid_credentials(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={
                "error_code": "some_other_error",
                "msg": "Other authentication problem",
            })

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "p")
        assert result.error_type == "UNKNOWN"


# ===========================================================================
# EMAIL_EXISTS
# ===========================================================================


class TestEmailExists:
    """EMAIL_EXISTS uses explicit indicators only."""

    def test_422_user_already_exists(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={
                "code": "user_already_exists",
                "message": "User already registered",
            })

        adapter = _make_adapter(config, handler)
        result = adapter.sign_up("u@e.com", "p", "N")
        assert result.error_type == "EMAIL_EXISTS"

    def test_422_email_exists_code(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={
                "code": "email_exists",
                "message": "Email already been registered",
            })

        adapter = _make_adapter(config, handler)
        result = adapter.sign_up("u@e.com", "p", "N")
        assert result.error_type == "EMAIL_EXISTS"

    def test_422_generic_not_email_exists(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={
                "code": "validation_failed",
                "message": "Some validation error",
            })

        adapter = _make_adapter(config, handler)
        result = adapter.sign_up("u@e.com", "p", "N")
        assert result.error_type == "UNKNOWN"

    def test_email_exists_not_used_for_sign_in(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={
                "code": "user_already_exists",
                "message": "User already registered",
            })

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "p")
        # sign_in should NOT classify as EMAIL_EXISTS
        assert result.error_type != "EMAIL_EXISTS"


# ===========================================================================
# RATE_LIMITED
# ===========================================================================


class TestRateLimited:
    """HTTP 429 → RATE_LIMITED."""

    def test_429_sign_in(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"message": "Too many requests"})

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "p")
        assert result.error_type == "RATE_LIMITED"

    def test_429_sign_up(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"message": "Too many requests"})

        adapter = _make_adapter(config, handler)
        result = adapter.sign_up("u@e.com", "p", "N")
        assert result.error_type == "RATE_LIMITED"


# ===========================================================================
# AUTH_FORBIDDEN
# ===========================================================================


class TestAuthForbidden:
    """HTTP 403 → AUTH_FORBIDDEN (not RLS_DENIED)."""

    def test_403(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "Forbidden"})

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "p")
        assert result.error_type == "AUTH_FORBIDDEN"
        assert result.error_type != "RLS_DENIED"


# ===========================================================================
# REMOTE_UNAVAILABLE
# ===========================================================================


class TestRemoteUnavailable:
    """HTTP 500/502/503 → REMOTE_UNAVAILABLE."""

    @pytest.mark.parametrize("status", [500, 502, 503])
    def test_5xx(self, config, status):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"msg": "Server error"})

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "p")
        assert result.error_type == "REMOTE_UNAVAILABLE"

    def test_504_is_not_remote_unavailable(self, config):
        """504 is not in the explicit 500/502/503 list → falls to UNKNOWN."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(504, json={"msg": "Gateway Timeout"})

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "p")
        # Per Spec: only 500/502/503 are explicitly classified
        assert result.error_type == "UNKNOWN"


# ===========================================================================
# CONNECTIVITY
# ===========================================================================


class TestConnectivity:
    """Transport errors → CONNECTIVITY."""

    def test_connect_error(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "p")
        assert result.success is False
        assert result.error_type == "CONNECTIVITY"

    def test_timeout(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "p")
        assert result.success is False
        assert result.error_type == "CONNECTIVITY"

    def test_connect_timeout(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("connect timed out")

        adapter = _make_adapter(config, handler)
        result = adapter.sign_up("u@e.com", "p", "N")
        assert result.success is False
        assert result.error_type == "CONNECTIVITY"


# ===========================================================================
# Error message security
# ===========================================================================


class TestErrorMessageSecurity:
    """Error messages do not leak secrets."""

    def test_connectivity_no_password_leak(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "TOP_SECRET_PASSWORD_VALUE")
        assert "TOP_SECRET_PASSWORD_VALUE" not in (result.error_message or "")

    def test_error_response_no_key_leak(self, config):
        config_secret = SupabaseConfig(
            url=_DUMMY_URL,
            publishable_key="TOP_SECRET_PUBLISHABLE_VALUE",
            storage_bucket="b",
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"msg": "Internal error"})

        adapter = _make_adapter(config_secret, handler)
        result = adapter.sign_in("u@e.com", "p")
        assert "TOP_SECRET_PUBLISHABLE_VALUE" not in (result.error_message or "")


# ===========================================================================
# Structural: no create_profile
# ===========================================================================


class TestNoCreateProfile:
    """Adapter does not have a create_profile method."""

    def test_no_create_profile_attribute(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_success_response())

        adapter = _make_adapter(config, handler)
        assert not hasattr(adapter, "create_profile")


# ===========================================================================
# Contract: returns RemoteAuthResult
# ===========================================================================


class TestReturnsRemoteAuthResult:
    """All operations return RemoteAuthResult instances."""

    def test_sign_in_returns_result(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_success_response())

        adapter = _make_adapter(config, handler)
        result = adapter.sign_in("u@e.com", "p")
        assert isinstance(result, RemoteAuthResult)

    def test_sign_up_returns_result(self, config):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_success_response())

        adapter = _make_adapter(config, handler)
        result = adapter.sign_up("u@e.com", "p", "N")
        assert isinstance(result, RemoteAuthResult)
