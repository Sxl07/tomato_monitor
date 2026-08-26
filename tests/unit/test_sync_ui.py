"""UI tests for remote sync integration (Task 14.4).

Covers:
A. Without Supabase: /sincronizacion renders local ZIP section, no remote form.
B. With Supabase: /sincronizacion shows remote section and sync button.
C. Dashboard: remote pending badge appears when Supabase configured.
D. Portrait: sync button meets minimum touch target (48px via inline style).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import (
    require_current_user_html,
    require_current_user_api,
    get_current_user_optional,
)


_fake_user = SimpleNamespace(
    id=1, full_name="Test Operator", email="test@test.com", role="operator"
)


@pytest.fixture(autouse=True)
def _auth_overrides():
    """Override auth for all tests."""
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[require_current_user_html] = lambda: _fake_user
    app.dependency_overrides[require_current_user_api] = lambda: _fake_user
    app.dependency_overrides[get_current_user_optional] = lambda: _fake_user
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def _make_supabase_config():
    """Create a valid SupabaseConfig for test."""
    from src.infrastructure.supabase.supabase_config import SupabaseConfig
    return SupabaseConfig(
        url="https://test.supabase.co",
        publishable_key="pk_test_key",
        storage_bucket="test-bucket",
    )


# ---------------------------------------------------------------------------
# A. Without Supabase: local ZIP section present, no remote controls
# ---------------------------------------------------------------------------


class TestSyncPageWithoutSupabase:
    """When Supabase is NOT configured, only local sync is visible."""

    def test_local_section_present(self):
        """Local sync section and ZIP button area appear."""
        with TestClient(app) as client:
            app.state.supabase_config = None
            response = client.get("/sincronizacion")

        assert response.status_code == 200
        assert "Sincronización local" in response.text
        assert "paquete ZIP local" in response.text

    def test_no_remote_button(self):
        """Remote sync button does not appear without Supabase."""
        with TestClient(app) as client:
            app.state.supabase_config = None
            response = client.get("/sincronizacion")

        assert response.status_code == 200
        assert "Sincronizar ahora" not in response.text
        assert "remote-sync-btn" not in response.text

    def test_no_remote_section(self):
        """Remote sync section is not rendered."""
        with TestClient(app) as client:
            app.state.supabase_config = None
            response = client.get("/sincronizacion")

        assert response.status_code == 200
        assert "remote-sync-section" not in response.text

    def test_local_mode_banner_shown(self):
        """'Modo local' banner appears when no remote configured."""
        with TestClient(app) as client:
            app.state.supabase_config = None
            response = client.get("/sincronizacion")

        assert response.status_code == 200
        assert "Modo local" in response.text


# ---------------------------------------------------------------------------
# B. With Supabase: remote section and button appear
# ---------------------------------------------------------------------------


class TestSyncPageWithSupabase:
    """When Supabase IS configured, remote sync section appears."""

    def test_remote_section_rendered(self):
        """Remote sync section appears with Supabase configured."""
        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            response = client.get("/sincronizacion")

        assert response.status_code == 200
        assert "remote-sync-section" in response.text

    def test_sync_button_present(self):
        """'Sincronizar ahora' button appears."""
        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            response = client.get("/sincronizacion")

        assert response.status_code == 200
        assert "Sincronizar ahora" in response.text
        assert "remote-sync-btn" in response.text

    def test_password_input_present(self):
        """Password input field for remote auth appears."""
        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            response = client.get("/sincronizacion")

        assert response.status_code == 200
        assert 'type="password"' in response.text
        assert "sync-password" in response.text

    def test_local_section_still_present(self):
        """Local ZIP section remains when Supabase is configured."""
        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            response = client.get("/sincronizacion")

        assert response.status_code == 200
        assert "Sincronización local" in response.text
        assert "Generar paquete local" in response.text or "No hay registros pendientes" in response.text

    def test_local_mode_banner_hidden(self):
        """'Modo local' banner does NOT appear when Supabase configured."""
        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            response = client.get("/sincronizacion")

        assert response.status_code == 200
        assert "Modo local" not in response.text

    def test_remote_sync_js_loaded(self):
        """remote_sync.js script is loaded when Supabase configured."""
        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            response = client.get("/sincronizacion")

        assert response.status_code == 200
        assert "remote_sync.js" in response.text


# ---------------------------------------------------------------------------
# C. Dashboard: remote pending badge
# ---------------------------------------------------------------------------


class TestDashboardRemoteSyncBadge:
    """Dashboard shows remote sync badge when Supabase configured."""

    def test_badge_element_present_when_configured(self):
        """Badge HTML element exists when Supabase is configured."""
        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            response = client.get("/dashboard")

        assert response.status_code == 200
        assert "dashboard-remote-sync-badge" in response.text

    def test_badge_hidden_when_not_configured(self):
        """Badge element does not exist without Supabase."""
        with TestClient(app) as client:
            app.state.supabase_config = None
            response = client.get("/dashboard")

        assert response.status_code == 200
        assert "dashboard-remote-sync-badge" not in response.text

    def test_dashboard_still_works_without_supabase(self):
        """Dashboard loads normally without Supabase configuration."""
        with TestClient(app) as client:
            app.state.supabase_config = None
            response = client.get("/dashboard")

        assert response.status_code == 200
        assert "Invernaderos" in response.text


# ---------------------------------------------------------------------------
# D. Portrait: touch targets
# ---------------------------------------------------------------------------


class TestPortraitTouchTargets:
    """Sync button meets minimum touch target size."""

    def test_sync_button_min_height(self):
        """Remote sync button has min-height: 48px (>= 44px requirement)."""
        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            response = client.get("/sincronizacion")

        assert response.status_code == 200
        assert "min-height: 48px" in response.text

    def test_password_input_full_width(self):
        """Password input is full-width for portrait usability."""
        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            response = client.get("/sincronizacion")

        assert response.status_code == 200
        # Input has width: 100% for portrait-first layout
        assert 'width: 100%' in response.text
