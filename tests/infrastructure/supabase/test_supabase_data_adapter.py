"""Unit tests for SupabaseDataAdapter using httpx.MockTransport.

Validates:
- Upsert sends correct POST with on_conflict=id, Prefer, auth headers
- Payload transmitted without transformation
- Success returns data["id"] as remote_id
- Same UUID preserved across retries (idempotency)
- Error classification: CONNECTIVITY, REMOTE_UNAVAILABLE, RLS_DENIED, UNKNOWN
- Missing/invalid id rejected without network call
- Constructor does not make network calls
- No JWT/key leak in error messages
- No token persistence on adapter

Spec 017 — Supabase Remote Sync.
Requirements: 10.3, 15.1, 15.4, 17.1, 18.8, 24.2
"""

import json

import httpx
import pytest

from src.application.interfaces.remote_data_port import RemoteUpsertResult
from src.infrastructure.supabase.supabase_config import SupabaseConfig
from src.infrastructure.supabase.supabase_data_adapter import SupabaseDataAdapter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DUMMY_URL = "https://example.supabase.co"
_DUMMY_KEY = "dummy-publishable-key"
_DUMMY_JWT = "dummy-jwt"
_DUMMY_UUID = "11111111-2222-4333-8444-555555555555"
_PAYLOAD = {"id": _DUMMY_UUID, "name": "Greenhouse Test"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    return SupabaseConfig(url=_DUMMY_URL, publishable_key=_DUMMY_KEY, storage_bucket="dummy-bucket")


def _make_adapter(config, handler):
    return SupabaseDataAdapter(config=config, transport=httpx.MockTransport(handler))


# ===========================================================================
# Success
# ===========================================================================


class TestUpsertSuccess:
    """Successful upsert returns remote_id from payload."""

    def test_result_on_201(self, config):
        def handler(req):
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        result = adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)

        assert result.success is True
        assert result.remote_id == _DUMMY_UUID
        assert result.error_type is None

    def test_result_on_204_no_body(self, config):
        def handler(req):
            return httpx.Response(204, content=b"")

        adapter = _make_adapter(config, handler)
        result = adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)

        assert result.success is True
        assert result.remote_id == _DUMMY_UUID

    def test_returns_remote_upsert_result(self, config):
        def handler(req):
            return httpx.Response(200, json={})

        adapter = _make_adapter(config, handler)
        result = adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)
        assert isinstance(result, RemoteUpsertResult)


# ===========================================================================
# Request validation
# ===========================================================================


class TestRequestFormat:
    """Upsert sends correct method, URL, headers, params, and body."""

    def test_method_is_post(self, config):
        captured = {}

        def handler(req):
            captured["method"] = req.method
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)
        assert captured["method"] == "POST"

    def test_url_path(self, config):
        captured = {}

        def handler(req):
            captured["url"] = req.url
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)
        assert captured["url"].path == "/rest/v1/greenhouses"

    def test_on_conflict_param(self, config):
        captured = {}

        def handler(req):
            captured["params"] = dict(req.url.params)
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)
        assert captured["params"]["on_conflict"] == "id"

    def test_prefer_header(self, config):
        captured = {}

        def handler(req):
            captured["prefer"] = req.headers.get("prefer")
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)
        assert captured["prefer"] == "resolution=merge-duplicates"

    def test_authorization_header(self, config):
        captured = {}

        def handler(req):
            captured["auth"] = req.headers.get("authorization")
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)
        assert captured["auth"] == f"Bearer {_DUMMY_JWT}"

    def test_apikey_header(self, config):
        captured = {}

        def handler(req):
            captured["apikey"] = req.headers.get("apikey")
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)
        assert captured["apikey"] == _DUMMY_KEY

    def test_payload_transmitted_exactly(self, config):
        captured = {}

        def handler(req):
            captured["body"] = json.loads(req.content)
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)
        assert captured["body"] == _PAYLOAD
        assert captured["body"]["id"] == _DUMMY_UUID


# ===========================================================================
# Idempotency: same UUID in retry
# ===========================================================================


class TestIdempotency:
    """Same UUID is transmitted on retries; modified payload still keeps UUID."""

    def test_same_uuid_in_two_calls(self, config):
        bodies = []

        def handler(req):
            bodies.append(json.loads(req.content))
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)
        adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)

        assert len(bodies) == 2
        assert bodies[0]["id"] == _DUMMY_UUID
        assert bodies[1]["id"] == _DUMMY_UUID

    def test_modified_payload_keeps_uuid(self, config):
        bodies = []

        def handler(req):
            bodies.append(json.loads(req.content))
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        adapter.upsert(_DUMMY_JWT, "greenhouses", {"id": _DUMMY_UUID, "name": "Name A"})
        adapter.upsert(_DUMMY_JWT, "greenhouses", {"id": _DUMMY_UUID, "name": "Name B"})

        assert bodies[0]["id"] == bodies[1]["id"] == _DUMMY_UUID
        assert bodies[0]["name"] == "Name A"
        assert bodies[1]["name"] == "Name B"


# ===========================================================================
# CONNECTIVITY
# ===========================================================================


class TestConnectivity:
    """Transport errors → CONNECTIVITY."""

    def test_connect_error(self, config):
        def handler(req):
            raise httpx.ConnectError("refused")

        adapter = _make_adapter(config, handler)
        result = adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)
        assert result.success is False
        assert result.error_type == "CONNECTIVITY"
        assert result.remote_id is None

    def test_read_timeout(self, config):
        def handler(req):
            raise httpx.ReadTimeout("timed out")

        adapter = _make_adapter(config, handler)
        result = adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)
        assert result.success is False
        assert result.error_type == "CONNECTIVITY"

    def test_connect_timeout(self, config):
        def handler(req):
            raise httpx.ConnectTimeout("connect timed out")

        adapter = _make_adapter(config, handler)
        result = adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)
        assert result.success is False
        assert result.error_type == "CONNECTIVITY"


# ===========================================================================
# REMOTE_UNAVAILABLE
# ===========================================================================


class TestRemoteUnavailable:
    """HTTP 500/502/503 → REMOTE_UNAVAILABLE."""

    @pytest.mark.parametrize("status", [500, 502, 503])
    def test_5xx(self, config, status):
        def handler(req):
            return httpx.Response(status, json={"message": "Server error"})

        adapter = _make_adapter(config, handler)
        result = adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)
        assert result.error_type == "REMOTE_UNAVAILABLE"

    def test_504_is_unknown(self, config):
        def handler(req):
            return httpx.Response(504, json={"message": "Gateway Timeout"})

        adapter = _make_adapter(config, handler)
        result = adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)
        assert result.error_type == "UNKNOWN"


# ===========================================================================
# RLS_DENIED
# ===========================================================================


class TestRlsDenied:
    """HTTP 403 → RLS_DENIED (not AUTH_FORBIDDEN)."""

    def test_403(self, config):
        def handler(req):
            return httpx.Response(403, json={"code": "42501", "message": "permission denied"})

        adapter = _make_adapter(config, handler)
        result = adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)
        assert result.success is False
        assert result.error_type == "RLS_DENIED"
        assert result.error_type != "AUTH_FORBIDDEN"


# ===========================================================================
# UNKNOWN (other HTTP errors)
# ===========================================================================


class TestUnknownErrors:
    """Other HTTP errors → UNKNOWN."""

    @pytest.mark.parametrize("status", [400, 401, 404, 409, 422, 429])
    def test_various_codes(self, config, status):
        def handler(req):
            return httpx.Response(status, json={"message": "Some error"})

        adapter = _make_adapter(config, handler)
        result = adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)
        assert result.error_type == "UNKNOWN"


# ===========================================================================
# Error message parsing
# ===========================================================================


class TestErrorMessage:
    """Error message extracted from JSON or fallback."""

    def test_json_message(self, config):
        def handler(req):
            return httpx.Response(400, json={"message": "Invalid payload"})

        adapter = _make_adapter(config, handler)
        result = adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)
        assert result.error_message == "Invalid payload"

    def test_non_json_fallback(self, config):
        def handler(req):
            return httpx.Response(400, content=b"not-json", headers={"content-type": "text/plain"})

        adapter = _make_adapter(config, handler)
        result = adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)
        assert result.error_message == "HTTP 400"


# ===========================================================================
# Missing/invalid id
# ===========================================================================


class TestMissingId:
    """Missing or invalid id rejects without network."""

    def test_no_id_key(self, config):
        calls = [0]

        def handler(req):
            calls[0] += 1
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        result = adapter.upsert(_DUMMY_JWT, "greenhouses", {"name": "No ID"})

        assert result.success is False
        assert result.error_type == "UNKNOWN"
        assert calls[0] == 0

    @pytest.mark.parametrize("bad_id", [None, 123, {}, [], ""])
    def test_invalid_id_types(self, config, bad_id):
        calls = [0]

        def handler(req):
            calls[0] += 1
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        result = adapter.upsert(_DUMMY_JWT, "greenhouses", {"id": bad_id, "name": "X"})

        assert result.success is False
        assert result.error_type == "UNKNOWN"
        assert calls[0] == 0


# ===========================================================================
# Constructor does not make network
# ===========================================================================


class TestConstructorNoNetwork:
    """Instantiation does not invoke transport."""

    def test_no_calls_on_init(self, config):
        calls = [0]

        def handler(req):
            calls[0] += 1
            return httpx.Response(200, json={})

        _make_adapter(config, handler)
        assert calls[0] == 0


# ===========================================================================
# No secret leak
# ===========================================================================


class TestNoSecretLeak:
    """Error messages do not contain JWT or publishable key."""

    def test_connectivity_no_jwt_leak(self, config):
        def handler(req):
            raise httpx.ConnectError("refused")

        secret_config = SupabaseConfig(
            url=_DUMMY_URL, publishable_key="TOP_SECRET_TEST_KEY", storage_bucket="b"
        )
        adapter = _make_adapter(secret_config, handler)
        result = adapter.upsert("TOP_SECRET_TEST_JWT", "greenhouses", _PAYLOAD)

        assert "TOP_SECRET_TEST_JWT" not in (result.error_message or "")
        assert "TOP_SECRET_TEST_KEY" not in (result.error_message or "")


# ===========================================================================
# No token persistence
# ===========================================================================


class TestNoTokenPersistence:
    """Adapter does not store access_token as attribute."""

    def test_no_access_token_attr(self, config):
        def handler(req):
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        adapter.upsert(_DUMMY_JWT, "greenhouses", _PAYLOAD)
        assert not hasattr(adapter, "_access_token")
