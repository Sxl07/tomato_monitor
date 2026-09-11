"""Unit tests for SupabaseRemoteReadAdapter using httpx.MockTransport.

Validates (no real network):
- Successful GET returns rows.
- Request carries apikey + Bearer JWT auth headers.
- greenhouses can be scoped by owner_user_id (owner_user_id=eq. present).
- Child tables (e.g. monitorings) receive NO owner_user_id filter.
- Real filters translate to field=eq.value equality.
- Disallowed table fails closed with UNKNOWN and NO HTTP request made.
- Empty response -> success with rows == [].
- 401 / 403 -> RLS_DENIED; 5xx -> REMOTE_UNAVAILABLE.
- Network error / timeout -> CONNECTIVITY.
- Multi-page pagination concatenates each row exactly once.
- A failing later page -> success=False (no partial success).

Spec 022 — Recovery Metadata Core (block D1).
"""

from urllib.parse import parse_qs

import httpx
import pytest

from src.application.interfaces.remote_read_port import RemoteQueryResult
from src.infrastructure.supabase.supabase_config import SupabaseConfig
from src.infrastructure.supabase.supabase_read_adapter import SupabaseRemoteReadAdapter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DUMMY_URL = "https://example.supabase.co"
_DUMMY_KEY = "dummy-publishable-key"
_DUMMY_JWT = "dummy-jwt"
_OWNER_UUID = "99999999-8888-4777-8666-555555555555"
_PAGE_SIZE = 1000


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    return SupabaseConfig(
        url=_DUMMY_URL,
        publishable_key=_DUMMY_KEY,
        storage_bucket="dummy-bucket",
    )


def _make_adapter(config, handler):
    return SupabaseRemoteReadAdapter(
        config=config, transport=httpx.MockTransport(handler)
    )


def _query_params(request: httpx.Request) -> dict:
    """Return the request query string parsed into a {key: [values]} dict."""
    return parse_qs(request.url.query.decode())


# ===========================================================================
# Success + headers
# ===========================================================================


class TestReadSuccess:
    def test_success_returns_rows(self, config):
        rows = [{"id": "a"}, {"id": "b"}]

        def handler(req):
            return httpx.Response(200, json=rows)

        adapter = _make_adapter(config, handler)
        result = adapter.fetch_by_owner(_DUMMY_JWT, "greenhouses", _OWNER_UUID)

        assert isinstance(result, RemoteQueryResult)
        assert result.success is True
        assert result.rows == rows
        assert result.error_type is None

    def test_request_carries_auth_headers(self, config):
        captured = {}

        def handler(req):
            captured["apikey"] = req.headers.get("apikey")
            captured["authorization"] = req.headers.get("authorization")
            return httpx.Response(200, json=[])

        adapter = _make_adapter(config, handler)
        adapter.fetch_by_owner(_DUMMY_JWT, "greenhouses", _OWNER_UUID)

        assert captured["apikey"] == _DUMMY_KEY
        assert captured["authorization"] == f"Bearer {_DUMMY_JWT}"

    def test_empty_response_is_success_with_no_rows(self, config):
        def handler(req):
            return httpx.Response(200, json=[])

        adapter = _make_adapter(config, handler)
        result = adapter.fetch_by_owner(_DUMMY_JWT, "monitorings", _OWNER_UUID)

        assert result.success is True
        assert result.rows == []


# ===========================================================================
# Owner scoping + filters
# ===========================================================================


class TestScopingAndFilters:
    def test_greenhouses_scoped_by_owner(self, config):
        captured = {}

        def handler(req):
            captured["params"] = _query_params(req)
            return httpx.Response(200, json=[])

        adapter = _make_adapter(config, handler)
        adapter.fetch_by_owner(_DUMMY_JWT, "greenhouses", _OWNER_UUID)

        params = captured["params"]
        assert params["owner_user_id"] == [f"eq.{_OWNER_UUID}"]

    def test_child_table_has_no_owner_filter(self, config):
        captured = {}

        def handler(req):
            captured["params"] = _query_params(req)
            return httpx.Response(200, json=[])

        adapter = _make_adapter(config, handler)
        adapter.fetch_by_owner(_DUMMY_JWT, "monitorings", _OWNER_UUID)

        params = captured["params"]
        assert "owner_user_id" not in params

    def test_filters_translate_to_equality(self, config):
        captured = {}

        def handler(req):
            captured["params"] = _query_params(req)
            return httpx.Response(200, json=[])

        adapter = _make_adapter(config, handler)
        adapter.fetch_by_owner(
            _DUMMY_JWT,
            "modules",
            _OWNER_UUID,
            filters={"greenhouse_id": "gh-uuid-1"},
        )

        params = captured["params"]
        assert params["greenhouse_id"] == ["eq.gh-uuid-1"]
        # Child table: still no owner filter even with a real filter present.
        assert "owner_user_id" not in params


# ===========================================================================
# Fail closed for disallowed tables
# ===========================================================================


class TestDisallowedTable:
    def test_disallowed_table_fails_closed_without_http(self, config):
        called = {"count": 0}

        def handler(req):
            called["count"] += 1
            return httpx.Response(200, json=[])

        adapter = _make_adapter(config, handler)
        result = adapter.fetch_by_owner(_DUMMY_JWT, "users", _OWNER_UUID)

        assert result.success is False
        assert result.error_type == "UNKNOWN"
        assert called["count"] == 0


# ===========================================================================
# Error classification
# ===========================================================================


class TestErrorClassification:
    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_errors_map_to_rls_denied(self, config, status):
        def handler(req):
            return httpx.Response(status, json={"message": "denied"})

        adapter = _make_adapter(config, handler)
        result = adapter.fetch_by_owner(_DUMMY_JWT, "greenhouses", _OWNER_UUID)

        assert result.success is False
        assert result.error_type == "RLS_DENIED"

    @pytest.mark.parametrize("status", [500, 502, 503])
    def test_server_errors_map_to_remote_unavailable(self, config, status):
        def handler(req):
            return httpx.Response(status, json={"message": "boom"})

        adapter = _make_adapter(config, handler)
        result = adapter.fetch_by_owner(_DUMMY_JWT, "greenhouses", _OWNER_UUID)

        assert result.success is False
        assert result.error_type == "REMOTE_UNAVAILABLE"

    def test_other_error_maps_to_unknown(self, config):
        def handler(req):
            return httpx.Response(418, json={"message": "teapot"})

        adapter = _make_adapter(config, handler)
        result = adapter.fetch_by_owner(_DUMMY_JWT, "greenhouses", _OWNER_UUID)

        assert result.success is False
        assert result.error_type == "UNKNOWN"

    def test_timeout_maps_to_connectivity(self, config):
        def handler(req):
            raise httpx.TimeoutException("timed out", request=req)

        adapter = _make_adapter(config, handler)
        result = adapter.fetch_by_owner(_DUMMY_JWT, "greenhouses", _OWNER_UUID)

        assert result.success is False
        assert result.error_type == "CONNECTIVITY"

    def test_network_error_maps_to_connectivity(self, config):
        def handler(req):
            raise httpx.ConnectError("no route", request=req)

        adapter = _make_adapter(config, handler)
        result = adapter.fetch_by_owner(_DUMMY_JWT, "greenhouses", _OWNER_UUID)

        assert result.success is False
        assert result.error_type == "CONNECTIVITY"

    def test_error_message_does_not_leak_secrets(self, config):
        def handler(req):
            return httpx.Response(403, json={"message": "row-level denied"})

        adapter = _make_adapter(config, handler)
        result = adapter.fetch_by_owner(_DUMMY_JWT, "greenhouses", _OWNER_UUID)

        assert result.error_message is not None
        assert _DUMMY_JWT not in result.error_message
        assert _DUMMY_KEY not in result.error_message


# ===========================================================================
# Pagination
# ===========================================================================


class TestPagination:
    def test_multi_page_concatenates_each_row_once(self, config):
        # First window returns a full page; second window returns a short page.
        first_page = [{"id": f"first-{i}"} for i in range(_PAGE_SIZE)]
        second_page = [{"id": "second-0"}, {"id": "second-1"}]
        calls = {"count": 0}

        def handler(req):
            calls["count"] += 1
            rng = req.headers.get("range")
            if rng == f"0-{_PAGE_SIZE - 1}":
                return httpx.Response(200, json=first_page)
            if rng == f"{_PAGE_SIZE}-{2 * _PAGE_SIZE - 1}":
                return httpx.Response(200, json=second_page)
            raise AssertionError(f"unexpected Range header: {rng}")

        adapter = _make_adapter(config, handler)
        result = adapter.fetch_by_owner(_DUMMY_JWT, "snapshots", _OWNER_UUID)

        assert result.success is True
        assert calls["count"] == 2
        # Exactly page_size + 2 rows, each id unique (no duplicates).
        assert len(result.rows) == _PAGE_SIZE + 2
        ids = [r["id"] for r in result.rows]
        assert len(ids) == len(set(ids))

    def test_failing_later_page_yields_no_partial_success(self, config):
        first_page = [{"id": f"first-{i}"} for i in range(_PAGE_SIZE)]

        def handler(req):
            rng = req.headers.get("range")
            if rng == f"0-{_PAGE_SIZE - 1}":
                return httpx.Response(200, json=first_page)
            # Second window fails with a server error.
            return httpx.Response(503, json={"message": "unavailable"})

        adapter = _make_adapter(config, handler)
        result = adapter.fetch_by_owner(_DUMMY_JWT, "snapshots", _OWNER_UUID)

        assert result.success is False
        assert result.error_type == "REMOTE_UNAVAILABLE"
        # No partial dataset leaked on failure.
        assert result.rows == []
