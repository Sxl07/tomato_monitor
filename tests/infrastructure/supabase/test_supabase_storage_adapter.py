"""Unit tests for SupabaseStorageAdapter using httpx.MockTransport.

Validates:
- build_snapshot_path produces exact deterministic paths
- Property: paths are deterministic (Hypothesis)
- Upload sends correct POST with headers, x-upsert, binary body
- Re-upload uses same path (idempotent)
- Local file missing/directory/read error → STORAGE_ERROR without network
- Error classification: CONNECTIVITY, REMOTE_UNAVAILABLE, RLS_DENIED, STORAGE_ERROR, UNKNOWN
- Constructor does not make network calls
- No JWT persistence, no secret leak
- Local file preserved after upload

Spec 017 — Supabase Remote Sync.
Requirements: 14, 15.2, 15.3, 17, 24.2
"""

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from hypothesis import given, strategies as st

from src.application.interfaces.remote_storage_port import RemoteUploadResult
from src.infrastructure.supabase.supabase_config import SupabaseConfig
from src.infrastructure.supabase.supabase_storage_adapter import SupabaseStorageAdapter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DUMMY_URL = "https://example.supabase.co"
_DUMMY_KEY = "dummy-publishable-key"
_DUMMY_JWT = "dummy-jwt"
_DUMMY_UUID = "11111111-2222-4333-8444-555555555555"
_REMOTE_PATH = f"monitorings/{_DUMMY_UUID}/raw/snapshot_000007.jpg"
_FILE_CONTENT = b"fake-jpeg-bytes"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    return SupabaseConfig(url=_DUMMY_URL, publishable_key=_DUMMY_KEY, storage_bucket="dummy-bucket")


@pytest.fixture
def local_file(tmp_path):
    f = tmp_path / "snapshot.jpg"
    f.write_bytes(_FILE_CONTENT)
    return f


def _make_adapter(config, handler):
    return SupabaseStorageAdapter(config=config, transport=httpx.MockTransport(handler))


# ===========================================================================
# build_snapshot_path
# ===========================================================================


class TestBuildSnapshotPath:
    """build_snapshot_path produces exact deterministic paths."""

    def test_raw(self):
        path = SupabaseStorageAdapter.build_snapshot_path(_DUMMY_UUID, 7, "raw")
        assert path == "monitorings/11111111-2222-4333-8444-555555555555/raw/snapshot_000007.jpg"

    def test_annotated(self):
        path = SupabaseStorageAdapter.build_snapshot_path(_DUMMY_UUID, 7, "annotated")
        assert path == "monitorings/11111111-2222-4333-8444-555555555555/annotated/snapshot_000007.jpg"

    def test_raw_and_annotated_differ(self):
        raw = SupabaseStorageAdapter.build_snapshot_path(_DUMMY_UUID, 7, "raw")
        ann = SupabaseStorageAdapter.build_snapshot_path(_DUMMY_UUID, 7, "annotated")
        assert raw != ann

    def test_frame_index_zero(self):
        path = SupabaseStorageAdapter.build_snapshot_path(_DUMMY_UUID, 0, "raw")
        assert "snapshot_000000.jpg" in path

    def test_frame_index_999999(self):
        path = SupabaseStorageAdapter.build_snapshot_path(_DUMMY_UUID, 999999, "raw")
        assert "snapshot_999999.jpg" in path

    def test_deterministic(self):
        p1 = SupabaseStorageAdapter.build_snapshot_path(_DUMMY_UUID, 7, "raw")
        p2 = SupabaseStorageAdapter.build_snapshot_path(_DUMMY_UUID, 7, "raw")
        assert p1 == p2

    @given(
        uuid_val=st.uuids().map(str),
        frame_index=st.integers(min_value=0, max_value=2_147_483_647),
        snapshot_type=st.sampled_from(["raw", "annotated"]),
    )
    def test_property_deterministic(self, uuid_val, frame_index, snapshot_type):
        expected = f"monitorings/{uuid_val}/{snapshot_type}/snapshot_{frame_index:06d}.jpg"
        result = SupabaseStorageAdapter.build_snapshot_path(uuid_val, frame_index, snapshot_type)
        assert result == expected
        # Second call same result
        assert SupabaseStorageAdapter.build_snapshot_path(uuid_val, frame_index, snapshot_type) == result


# ===========================================================================
# Upload success
# ===========================================================================


class TestUploadSuccess:
    """Successful upload returns object_path."""

    def test_success_201(self, config, local_file):
        def handler(req):
            return httpx.Response(201, json={"Key": "dummy"})

        adapter = _make_adapter(config, handler)
        result = adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)

        assert result.success is True
        assert result.object_path == _REMOTE_PATH
        assert result.error_type is None

    def test_success_204_no_body(self, config, local_file):
        def handler(req):
            return httpx.Response(204, content=b"")

        adapter = _make_adapter(config, handler)
        result = adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)

        assert result.success is True
        assert result.object_path == _REMOTE_PATH

    def test_returns_remote_upload_result(self, config, local_file):
        def handler(req):
            return httpx.Response(200, json={})

        adapter = _make_adapter(config, handler)
        result = adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)
        assert isinstance(result, RemoteUploadResult)


# ===========================================================================
# Request format
# ===========================================================================


class TestRequestFormat:
    """Upload sends correct method, URL, headers, and body."""

    def test_method_post(self, config, local_file):
        captured = {}

        def handler(req):
            captured["method"] = req.method
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)
        assert captured["method"] == "POST"

    def test_endpoint_path(self, config, local_file):
        captured = {}

        def handler(req):
            captured["path"] = req.url.path
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)
        expected = f"/storage/v1/object/dummy-bucket/{_REMOTE_PATH}"
        assert captured["path"] == expected

    def test_authorization(self, config, local_file):
        captured = {}

        def handler(req):
            captured["auth"] = req.headers["authorization"]
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)
        assert captured["auth"] == f"Bearer {_DUMMY_JWT}"

    def test_apikey(self, config, local_file):
        captured = {}

        def handler(req):
            captured["apikey"] = req.headers["apikey"]
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)
        assert captured["apikey"] == _DUMMY_KEY

    def test_content_type(self, config, local_file):
        captured = {}

        def handler(req):
            captured["ct"] = req.headers["content-type"]
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)
        assert captured["ct"] == "image/jpeg"

    def test_x_upsert(self, config, local_file):
        captured = {}

        def handler(req):
            captured["upsert"] = req.headers["x-upsert"]
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)
        assert captured["upsert"] == "true"

    def test_body_binary(self, config, local_file):
        captured = {}

        def handler(req):
            captured["body"] = req.content
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)
        assert captured["body"] == _FILE_CONTENT


# ===========================================================================
# Idempotent re-upload
# ===========================================================================


class TestReupload:
    """Re-upload uses same path with x-upsert."""

    def test_same_path_twice(self, config, local_file):
        requests_made = []

        def handler(req):
            requests_made.append({"path": req.url.path, "upsert": req.headers["x-upsert"]})
            return httpx.Response(200, json={})

        adapter = _make_adapter(config, handler)
        adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)
        adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)

        assert len(requests_made) == 2
        assert requests_made[0]["path"] == requests_made[1]["path"]
        assert requests_made[0]["upsert"] == "true"
        assert requests_made[1]["upsert"] == "true"


# ===========================================================================
# Local file errors
# ===========================================================================


class TestLocalFileErrors:
    """Missing/directory/read errors → STORAGE_ERROR without network."""

    def test_file_not_found(self, config, tmp_path):
        calls = [0]

        def handler(req):
            calls[0] += 1
            return httpx.Response(200, json={})

        adapter = _make_adapter(config, handler)
        result = adapter.upload_file(_DUMMY_JWT, str(tmp_path / "nope.jpg"), _REMOTE_PATH)

        assert result.success is False
        assert result.error_type == "STORAGE_ERROR"
        assert result.object_path is None
        assert calls[0] == 0

    def test_directory_not_file(self, config, tmp_path):
        calls = [0]

        def handler(req):
            calls[0] += 1
            return httpx.Response(200, json={})

        adapter = _make_adapter(config, handler)
        result = adapter.upload_file(_DUMMY_JWT, str(tmp_path), _REMOTE_PATH)

        assert result.success is False
        assert result.error_type == "STORAGE_ERROR"
        assert calls[0] == 0

    def test_os_error_on_read(self, config, local_file):
        adapter = _make_adapter(config, lambda req: httpx.Response(200, json={}))

        with patch(
            "src.infrastructure.supabase.supabase_storage_adapter.open",
            side_effect=OSError("read failed"),
        ):
            result = adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)

        assert result.success is False
        assert result.error_type == "STORAGE_ERROR"
        # File still exists
        assert local_file.exists()


# ===========================================================================
# CONNECTIVITY
# ===========================================================================


class TestConnectivity:
    """Transport errors → CONNECTIVITY."""

    def test_connect_error(self, config, local_file):
        def handler(req):
            raise httpx.ConnectError("refused")

        adapter = _make_adapter(config, handler)
        result = adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)
        assert result.success is False
        assert result.error_type == "CONNECTIVITY"
        assert result.object_path is None

    def test_read_timeout(self, config, local_file):
        def handler(req):
            raise httpx.ReadTimeout("timed out")

        adapter = _make_adapter(config, handler)
        result = adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)
        assert result.error_type == "CONNECTIVITY"


# ===========================================================================
# REMOTE_UNAVAILABLE
# ===========================================================================


class TestRemoteUnavailable:
    """HTTP 500/502/503 → REMOTE_UNAVAILABLE."""

    @pytest.mark.parametrize("status", [500, 502, 503])
    def test_5xx(self, config, local_file, status):
        def handler(req):
            return httpx.Response(status, json={"message": "Server error"})

        adapter = _make_adapter(config, handler)
        result = adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)
        assert result.error_type == "REMOTE_UNAVAILABLE"

    def test_504_is_unknown(self, config, local_file):
        def handler(req):
            return httpx.Response(504, json={"message": "Gateway Timeout"})

        adapter = _make_adapter(config, handler)
        result = adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)
        assert result.error_type == "UNKNOWN"


# ===========================================================================
# RLS_DENIED
# ===========================================================================


class TestRlsDenied:
    """HTTP 403 → RLS_DENIED."""

    def test_403(self, config, local_file):
        def handler(req):
            return httpx.Response(403, json={"message": "permission denied"})

        adapter = _make_adapter(config, handler)
        result = adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)
        assert result.error_type == "RLS_DENIED"
        assert result.error_type != "AUTH_FORBIDDEN"


# ===========================================================================
# STORAGE_ERROR HTTP
# ===========================================================================


class TestStorageErrorHttp:
    """HTTP 404/409/413/422 → STORAGE_ERROR."""

    @pytest.mark.parametrize("status", [404, 409, 413, 422])
    def test_storage_specific_codes(self, config, local_file, status):
        def handler(req):
            return httpx.Response(status, json={"message": "Storage issue"})

        adapter = _make_adapter(config, handler)
        result = adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)
        assert result.error_type == "STORAGE_ERROR"


# ===========================================================================
# UNKNOWN
# ===========================================================================


class TestUnknownErrors:
    """Other HTTP errors → UNKNOWN."""

    @pytest.mark.parametrize("status", [400, 401, 429])
    def test_various(self, config, local_file, status):
        def handler(req):
            return httpx.Response(status, json={"message": "Error"})

        adapter = _make_adapter(config, handler)
        result = adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)
        assert result.error_type == "UNKNOWN"


# ===========================================================================
# Error message parsing
# ===========================================================================


class TestErrorMessage:
    """Error message from JSON or fallback."""

    def test_json_message(self, config, local_file):
        def handler(req):
            return httpx.Response(404, json={"message": "Bucket/object error"})

        adapter = _make_adapter(config, handler)
        result = adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)
        assert result.error_message == "Bucket/object error"

    def test_non_json_fallback(self, config, local_file):
        def handler(req):
            return httpx.Response(400, content=b"not-json", headers={"content-type": "text/plain"})

        adapter = _make_adapter(config, handler)
        result = adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)
        assert result.error_message == "HTTP 400"


# ===========================================================================
# Constructor no network
# ===========================================================================


class TestConstructorNoNetwork:
    """Instantiation does not invoke transport."""

    def test_no_calls(self, config):
        calls = [0]

        def handler(req):
            calls[0] += 1
            return httpx.Response(200, json={})

        _make_adapter(config, handler)
        assert calls[0] == 0


# ===========================================================================
# No JWT persistence
# ===========================================================================


class TestNoJwtPersistence:
    """Adapter does not store access_token."""

    def test_no_token_attr(self, config, local_file):
        def handler(req):
            return httpx.Response(200, json={})

        adapter = _make_adapter(config, handler)
        adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)
        assert not hasattr(adapter, "_access_token")


# ===========================================================================
# No secret leak
# ===========================================================================


class TestNoSecretLeak:
    """Error messages do not contain JWT or key."""

    def test_connectivity_no_leak(self):
        secret_config = SupabaseConfig(
            url=_DUMMY_URL, publishable_key="TOP_SECRET_STORAGE_TEST_KEY", storage_bucket="b"
        )

        def handler(req):
            raise httpx.ConnectError("refused")

        adapter = _make_adapter(secret_config, handler)
        # Need a real file for this test
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"x")
            tmp = f.name
        try:
            result = adapter.upload_file("TOP_SECRET_STORAGE_TEST_JWT", tmp, _REMOTE_PATH)
        finally:
            os.unlink(tmp)

        assert "TOP_SECRET_STORAGE_TEST_JWT" not in (result.error_message or "")
        assert "TOP_SECRET_STORAGE_TEST_KEY" not in (result.error_message or "")


# ===========================================================================
# File preservation
# ===========================================================================


class TestFilePreservation:
    """Local file is never modified or deleted."""

    def test_file_intact_after_success(self, config, local_file):
        def handler(req):
            return httpx.Response(201, json={})

        adapter = _make_adapter(config, handler)
        adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)

        assert local_file.exists()
        assert local_file.read_bytes() == _FILE_CONTENT

    def test_file_intact_after_error(self, config, local_file):
        def handler(req):
            return httpx.Response(500, json={"message": "fail"})

        adapter = _make_adapter(config, handler)
        adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)

        assert local_file.exists()
        assert local_file.read_bytes() == _FILE_CONTENT


# ===========================================================================
# Object path is provider-relative
# ===========================================================================


class TestObjectPathRelative:
    """object_path does not contain URLs."""

    def test_no_http_prefix(self, config, local_file):
        def handler(req):
            return httpx.Response(200, json={})

        adapter = _make_adapter(config, handler)
        result = adapter.upload_file(_DUMMY_JWT, str(local_file), _REMOTE_PATH)

        assert not result.object_path.startswith("http://")
        assert not result.object_path.startswith("https://")
        assert result.object_path == _REMOTE_PATH
