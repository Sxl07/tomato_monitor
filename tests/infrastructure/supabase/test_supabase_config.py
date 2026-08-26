"""Unit tests for SupabaseConfig and load_supabase_config().

Validates:
- Offline-only mode (no env vars) returns None without warnings.
- Valid HTTPS configuration returns a properly populated SupabaseConfig.
- Trailing slash normalization prevents double slashes in derived URLs.
- Whitespace is trimmed from all input values.
- Default and custom storage bucket behavior.
- Partial configuration (URL without key, key without URL) is rejected.
- HTTPS scheme is required; HTTP and other schemes are rejected.
- SUPABASE_SERVICE_ROLE_KEY presence disables integration.
- No secret values are ever logged.

Requirements: 1.1–1.7, 18.1, 18.2, 23.1, 24.5
"""

import logging

import pytest

from src.infrastructure.supabase.supabase_config import (
    SupabaseConfig,
    load_supabase_config,
)

# --- Constants for dummy values (never real credentials) ---
_DUMMY_URL = "https://example.supabase.co"
_DUMMY_KEY = "dummy-publishable-key"
_DUMMY_ADMIN_KEY = "dummy-admin-key"
_DUMMY_BUCKET = "my-custom-bucket"


# --- Fixtures ---


@pytest.fixture(autouse=True)
def _clean_supabase_env(monkeypatch):
    """Ensure all Supabase env vars are absent before each test."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_STORAGE_BUCKET", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)


# ===========================================================================
# 5.1 Offline-only
# ===========================================================================


class TestOfflineOnly:
    """When no Supabase vars are set, system operates offline without errors."""

    def test_returns_none(self):
        result = load_supabase_config()
        assert result is None

    def test_no_warnings_or_errors(self, caplog):
        with caplog.at_level(logging.DEBUG):
            load_supabase_config()
        assert caplog.text == ""


# ===========================================================================
# 5.2 Valid configuration
# ===========================================================================


class TestValidConfig:
    """Valid HTTPS URL + publishable key produces a proper SupabaseConfig."""

    def test_returns_config(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", _DUMMY_URL)
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)

        cfg = load_supabase_config()

        assert cfg is not None
        assert cfg.url == "https://example.supabase.co"
        assert cfg.publishable_key == "dummy-publishable-key"
        assert cfg.storage_bucket == "tomato-monitor-snapshots"
        assert cfg.is_configured is True

    def test_auth_url(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", _DUMMY_URL)
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)

        cfg = load_supabase_config()
        assert cfg.auth_url == "https://example.supabase.co/auth/v1"

    def test_rest_url(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", _DUMMY_URL)
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)

        cfg = load_supabase_config()
        assert cfg.rest_url == "https://example.supabase.co/rest/v1"

    def test_storage_url(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", _DUMMY_URL)
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)

        cfg = load_supabase_config()
        assert cfg.storage_url == "https://example.supabase.co/storage/v1"


# ===========================================================================
# 5.3 Trailing slash normalization
# ===========================================================================


class TestTrailingSlash:
    """Trailing slash on URL is stripped to prevent double slashes."""

    def test_url_normalized(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co/")
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)

        cfg = load_supabase_config()
        assert cfg.url == "https://example.supabase.co"

    def test_derived_urls_no_double_slash(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co/")
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)

        cfg = load_supabase_config()
        assert "//" not in cfg.auth_url.split("://")[1]
        assert "//" not in cfg.rest_url.split("://")[1]
        assert "//" not in cfg.storage_url.split("://")[1]


# ===========================================================================
# 5.4 Whitespace normalization
# ===========================================================================


class TestWhitespaceNormalization:
    """Leading/trailing whitespace is stripped from all input values."""

    def test_url_trimmed(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "  https://example.supabase.co/  ")
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "  dummy-key  ")
        monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", "  custom-bucket  ")

        cfg = load_supabase_config()
        assert cfg.url == "https://example.supabase.co"
        assert cfg.publishable_key == "dummy-key"
        assert cfg.storage_bucket == "custom-bucket"


# ===========================================================================
# 5.5 Bucket default
# ===========================================================================


class TestBucketDefault:
    """Default bucket is used when SUPABASE_STORAGE_BUCKET is absent or empty."""

    def test_absent(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", _DUMMY_URL)
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)

        cfg = load_supabase_config()
        assert cfg.storage_bucket == "tomato-monitor-snapshots"

    def test_empty_string(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", _DUMMY_URL)
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)
        monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", "")

        cfg = load_supabase_config()
        assert cfg.storage_bucket == "tomato-monitor-snapshots"

    def test_whitespace_only(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", _DUMMY_URL)
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)
        monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", "   ")

        cfg = load_supabase_config()
        assert cfg.storage_bucket == "tomato-monitor-snapshots"


# ===========================================================================
# 5.6 Bucket custom
# ===========================================================================


class TestBucketCustom:
    """Custom bucket value is preserved when provided."""

    def test_custom_bucket(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", _DUMMY_URL)
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)
        monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", _DUMMY_BUCKET)

        cfg = load_supabase_config()
        assert cfg.storage_bucket == "my-custom-bucket"


# ===========================================================================
# 5.7 URL without key (partial)
# ===========================================================================


class TestPartialUrlOnly:
    """URL present but key missing disables integration with warning."""

    def test_returns_none(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", _DUMMY_URL)

        result = load_supabase_config()
        assert result is None

    def test_logs_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("SUPABASE_URL", _DUMMY_URL)

        with caplog.at_level(logging.WARNING):
            load_supabase_config()

        assert "SUPABASE_PUBLISHABLE_KEY" in caplog.text
        assert any(r.levelno == logging.WARNING for r in caplog.records)


# ===========================================================================
# 5.8 Key without URL (partial)
# ===========================================================================


class TestPartialKeyOnly:
    """Key present but URL missing disables integration with warning."""

    def test_returns_none(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)

        result = load_supabase_config()
        assert result is None

    def test_logs_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)

        with caplog.at_level(logging.WARNING):
            load_supabase_config()

        assert "SUPABASE_URL" in caplog.text
        assert any(r.levelno == logging.WARNING for r in caplog.records)


# ===========================================================================
# 5.9 URL without scheme
# ===========================================================================


class TestUrlNoScheme:
    """URL without scheme is rejected."""

    def test_returns_none(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "example.supabase.co")
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)

        result = load_supabase_config()
        assert result is None

    def test_logs_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("SUPABASE_URL", "example.supabase.co")
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)

        with caplog.at_level(logging.WARNING):
            load_supabase_config()

        assert any(r.levelno == logging.WARNING for r in caplog.records)


# ===========================================================================
# 5.10 URL without host
# ===========================================================================


class TestUrlNoHost:
    """URL with scheme but no host is rejected."""

    def test_returns_none(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://")
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)

        result = load_supabase_config()
        assert result is None

    def test_logs_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("SUPABASE_URL", "https://")
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)

        with caplog.at_level(logging.WARNING):
            load_supabase_config()

        assert any(r.levelno == logging.WARNING for r in caplog.records)


# ===========================================================================
# 5.11 HTTP rejected
# ===========================================================================


class TestHttpRejected:
    """HTTP scheme is rejected — only HTTPS is allowed."""

    def test_returns_none(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "http://example.supabase.co")
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)

        result = load_supabase_config()
        assert result is None

    def test_logs_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("SUPABASE_URL", "http://example.supabase.co")
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)

        with caplog.at_level(logging.WARNING):
            load_supabase_config()

        assert any(r.levelno == logging.WARNING for r in caplog.records)
        assert "https" in caplog.text.lower()

    def test_warning_does_not_contain_key(self, monkeypatch, caplog):
        monkeypatch.setenv("SUPABASE_URL", "http://example.supabase.co")
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "SECRET_KEY_VALUE")

        with caplog.at_level(logging.WARNING):
            load_supabase_config()

        assert "SECRET_KEY_VALUE" not in caplog.text


# ===========================================================================
# 5.12 Unsupported scheme
# ===========================================================================


class TestUnsupportedScheme:
    """Non-HTTP/HTTPS schemes are rejected."""

    def test_ftp_returns_none(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "ftp://example.supabase.co")
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)

        result = load_supabase_config()
        assert result is None

    def test_ftp_logs_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("SUPABASE_URL", "ftp://example.supabase.co")
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)

        with caplog.at_level(logging.WARNING):
            load_supabase_config()

        assert any(r.levelno == logging.WARNING for r in caplog.records)


# ===========================================================================
# 5.13 Service role detected
# ===========================================================================


class TestServiceRoleDetected:
    """SUPABASE_SERVICE_ROLE_KEY presence disables integration."""

    def test_returns_none(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", _DUMMY_URL)
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", _DUMMY_ADMIN_KEY)

        result = load_supabase_config()
        assert result is None

    def test_logs_error(self, monkeypatch, caplog):
        monkeypatch.setenv("SUPABASE_URL", _DUMMY_URL)
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", _DUMMY_ADMIN_KEY)

        with caplog.at_level(logging.ERROR):
            load_supabase_config()

        assert any(r.levelno == logging.ERROR for r in caplog.records)

    def test_key_value_not_in_logs(self, monkeypatch, caplog):
        monkeypatch.setenv("SUPABASE_URL", _DUMMY_URL)
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", _DUMMY_ADMIN_KEY)

        with caplog.at_level(logging.DEBUG):
            load_supabase_config()

        assert _DUMMY_ADMIN_KEY not in caplog.text


# ===========================================================================
# 5.14 Service role whitespace only
# ===========================================================================


class TestServiceRoleWhitespace:
    """Service role key that is only whitespace does not block config."""

    def test_whitespace_service_role_allows_config(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", _DUMMY_URL)
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _DUMMY_KEY)
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "   ")

        cfg = load_supabase_config()
        assert cfg is not None
        assert cfg.is_configured is True


# ===========================================================================
# 5.15 Publishable key empty/whitespace
# ===========================================================================


class TestPublishableKeyEmptyOrWhitespace:
    """Empty or whitespace-only publishable key is treated as absent."""

    def test_empty_key_returns_none(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", _DUMMY_URL)
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "")

        result = load_supabase_config()
        assert result is None

    def test_whitespace_key_returns_none(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", _DUMMY_URL)
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "   ")

        result = load_supabase_config()
        assert result is None

    def test_logs_warning_for_partial(self, monkeypatch, caplog):
        monkeypatch.setenv("SUPABASE_URL", _DUMMY_URL)
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "   ")

        with caplog.at_level(logging.WARNING):
            load_supabase_config()

        assert any(r.levelno == logging.WARNING for r in caplog.records)


# ===========================================================================
# 5.16 is_configured property
# ===========================================================================


class TestIsConfigured:
    """Test is_configured property on directly instantiated objects."""

    def test_valid_config_is_configured(self):
        cfg = SupabaseConfig(
            url="https://example.supabase.co",
            publishable_key="dummy",
            storage_bucket="bucket",
        )
        assert cfg.is_configured is True

    def test_empty_key_is_not_configured(self):
        cfg = SupabaseConfig(
            url="https://example.supabase.co",
            publishable_key="",
            storage_bucket="bucket",
        )
        assert cfg.is_configured is False

    def test_empty_url_is_not_configured(self):
        cfg = SupabaseConfig(
            url="",
            publishable_key="dummy",
            storage_bucket="bucket",
        )
        assert cfg.is_configured is False


# ===========================================================================
# 6. No leak of credentials in logs
# ===========================================================================


class TestNoCredentialLeak:
    """Verify that secret values never appear in log output."""

    def test_publishable_key_not_in_partial_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("SUPABASE_URL", _DUMMY_URL)
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "TOP_SECRET_PUBLISHABLE_VALUE")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "TOP_SECRET_ADMIN_VALUE")

        with caplog.at_level(logging.DEBUG):
            load_supabase_config()

        assert "TOP_SECRET_PUBLISHABLE_VALUE" not in caplog.text
        assert "TOP_SECRET_ADMIN_VALUE" not in caplog.text

    def test_key_not_in_http_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("SUPABASE_URL", "http://example.supabase.co")
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "TOP_SECRET_PUBLISHABLE_VALUE")

        with caplog.at_level(logging.DEBUG):
            load_supabase_config()

        assert "TOP_SECRET_PUBLISHABLE_VALUE" not in caplog.text

    def test_key_not_in_invalid_url_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("SUPABASE_URL", "not-a-url")
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "TOP_SECRET_PUBLISHABLE_VALUE")

        with caplog.at_level(logging.DEBUG):
            load_supabase_config()

        assert "TOP_SECRET_PUBLISHABLE_VALUE" not in caplog.text


# ===========================================================================
# Frozen dataclass verification
# ===========================================================================


class TestFrozenDataclass:
    """SupabaseConfig is immutable (frozen)."""

    def test_cannot_modify_url(self):
        cfg = SupabaseConfig(
            url="https://example.supabase.co",
            publishable_key="dummy",
            storage_bucket="bucket",
        )
        with pytest.raises(AttributeError):
            cfg.url = "https://other.supabase.co"  # type: ignore[misc]

    def test_cannot_modify_key(self):
        cfg = SupabaseConfig(
            url="https://example.supabase.co",
            publishable_key="dummy",
            storage_bucket="bucket",
        )
        with pytest.raises(AttributeError):
            cfg.publishable_key = "other"  # type: ignore[misc]
