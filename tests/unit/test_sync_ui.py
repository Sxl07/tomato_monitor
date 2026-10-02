"""UI tests for remote sync integration (Task 14.4).

Covers:
A. Without Supabase: /sincronizacion renders local ZIP section, no remote form.
B. With Supabase: /sincronizacion shows remote section and sync button.
C. Dashboard: remote pending badge appears when Supabase configured.
D. Portrait: sync button meets minimum touch target (48px via inline style).
"""

from html.parser import HTMLParser
from pathlib import Path
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


class _SyncPageStructure(HTMLParser):
    """Record the rendered controls and their enclosing native disclosures."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.elements = []
        self.summaries = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        disclosures = tuple(
            item[1].get("id") for item in self.stack if item[0] == "details"
        )
        self.elements.append((tag, attributes, disclosures))
        if tag not in {"area", "br", "hr", "img", "input", "link", "meta"}:
            self.stack.append((tag, attributes))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.stack and self.stack[-1][0] == "summary":
            self.summaries.append(data.strip())

    def by_id(self, element_id):
        return next(
            (index, tag, attrs, disclosures)
            for index, (tag, attrs, disclosures) in enumerate(self.elements)
            if attrs.get("id") == element_id
        )


def _pending_zip_status():
    return {
        "total_pending": 1, "total_exported": 0, "total_synced": 0,
        "monitorings_pending": 1, "monitorings_exported": 0,
        "monitorings_synced": 0, "activities_pending": 0,
        "activities_exported": 0, "activities_synced": 0,
    }


class TestSyncPageLayout:
    """The remote action stays visible while secondary controls collapse."""

    def test_remote_first_and_existing_controls_preserved(self):
        from src.application.services.sync_service import SyncService

        with patch.object(SyncService, "compute_sync_status", return_value=_pending_zip_status()):
            with TestClient(app) as client:
                app.state.supabase_config = _make_supabase_config()
                response = client.get("/sincronizacion")

        assert response.status_code == 200
        page = _SyncPageStructure()
        page.feed(response.text)

        status_index, _, _, status_details = page.by_id("remote-sync-status")
        form_index, form_tag, _, form_details = page.by_id("remote-sync-form")
        button_index, _, button, button_details = page.by_id("remote-sync-btn")
        local_index, _, local, _ = page.by_id("local-export-section")
        recovery_index, _, recovery, _ = page.by_id("cloud-recovery-section")
        _, _, help_details, _ = page.by_id("sync-state-help")

        assert status_details == form_details == button_details == ()
        assert form_tag == "div"  # Remote sync remains JS-driven, not an HTML form.
        assert button["type"] == "button"
        assert status_index < form_index < button_index < local_index < recovery_index
        assert "open" not in local and "open" not in recovery
        assert "open" not in help_details
        assert {"Exportación local", "Recuperación desde la nube", "Información sobre estados"} <= set(page.summaries)

        local_forms = [attrs for tag, attrs, disclosures in page.elements
                       if tag == "form" and attrs.get("action") == "/sincronizacion/local"
                       and disclosures == ("local-export-section",)]
        assert len(local_forms) == 1
        assert local_forms[0]["method"] == "post"
        assert sum(tag == "form" and attrs.get("action") == "/sincronizacion/local"
                   for tag, attrs, _ in page.elements) == 1
        form_actions = [attrs["action"] for tag, attrs, _ in page.elements
                        if tag == "form" and "action" in attrs]
        assert len(form_actions) == len(set(form_actions))

        _, _, remote_password, remote_password_details = page.by_id("sync-password")
        assert remote_password_details == ()
        assert remote_password["type"] == "password"
        assert "name" not in remote_password
        assert "value" not in remote_password
        remote_toggles = [attrs for tag, attrs, _ in page.elements
                          if tag == "button" and attrs.get("aria-controls") == "sync-password"]
        assert len(remote_toggles) == 1
        assert remote_toggles[0]["type"] == "button"
        assert remote_toggles[0]["aria-pressed"] == "false"
        assert remote_toggles[0]["data-password-toggle"] is None
        _, _, recovery_password, recovery_password_details = page.by_id("recovery-password")
        assert recovery_password["name"] == "password"
        assert recovery_password["type"] == "password"
        assert "value" not in recovery_password
        recovery_toggles = [attrs for tag, attrs, _ in page.elements
                            if tag == "button" and attrs.get("aria-controls") == "recovery-password"]
        assert len(recovery_toggles) == 1
        assert recovery_toggles[0]["type"] == "button"
        assert recovery_toggles[0]["aria-pressed"] == "false"
        assert recovery_toggles[0]["data-password-toggle"] is None
        assert recovery_password_details == ()  # Modal remains outside closed details.
        assert sum(tag == "form" and attrs.get("id") == "cloud-recovery-form"
                   for tag, attrs, _ in page.elements) == 1
        assert "method" not in page.by_id("cloud-recovery-form")[2]
        assert "action" not in page.by_id("cloud-recovery-form")[2]

        for element_id in (
            "remote-sync-progress", "remote-sync-phase", "remote-sync-counter",
            "remote-sync-result", "cloud-recovery-btn", "cloud-recovery-result",
            "cloud-recovery-modal", "cloud-recovery-form",
            "cloud-recovery-cancel-btn", "cloud-recovery-confirm-btn",
        ):
            page.by_id(element_id)
        assert page.by_id("cloud-recovery-btn")[3] == ("cloud-recovery-section",)
        assert page.by_id("cloud-recovery-modal")[3] == ()
        assert '/static/js/remote_sync.js' in response.text
        assert '/static/js/cloud_recovery.js' in response.text
        assert '/static/js/password_visibility.js' in response.text
        static_dir = Path(__file__).resolve().parents[2] / "app" / "static" / "js"
        sync_js = (static_dir / "remote_sync.js").read_text(encoding="utf-8")
        recovery_js = (static_dir / "cloud_recovery.js").read_text(encoding="utf-8")
        assert 'fetch("/api/sync/status"' in sync_js
        assert all(f"data.{field}_count" in sync_js
                   for field in ("pending", "synced", "error"))
        assert 'fetch("/api/sync/trigger"' in sync_js
        assert 'fetch("/api/recovery/trigger"' in recovery_js
        assert 'method: "POST"' in sync_js and 'method: "POST"' in recovery_js

    def test_local_mode_opens_its_status_and_export(self):
        with TestClient(app) as client:
            app.state.supabase_config = None
            response = client.get("/sincronizacion")

        assert response.status_code == 200
        page = _SyncPageStructure()
        page.feed(response.text)
        _, _, local, _ = page.by_id("local-export-section")
        assert "open" in local
        assert "remote-sync-btn" not in response.text

    def test_local_export_error_opens_disclosure(self):
        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            response = client.get("/sincronizacion?error=Error+de+exportación")

        assert response.status_code == 200
        page = _SyncPageStructure()
        page.feed(response.text)
        assert "open" in page.by_id("local-export-section")[2]
        assert "Error de exportación" in response.text


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

    def test_password_controls_fit_portrait_width(self):
        """The eye controls sit inside full-width password inputs."""
        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            response = client.get("/sincronizacion")

        assert response.status_code == 200
        assert response.text.count('class="password-field"') == 2
        assert response.text.count('class="password-icon-eye"') == 2
        assert response.text.count('class="password-icon-eye-off"') == 2
        css = Path("app/static/css/agricultural.css").read_text(encoding="utf-8")
        assert "position: relative" in css
        assert ".password-field .form-input" in css
        assert "padding-right: 56px" in css
        assert '.password-toggle[aria-pressed="true"] .password-icon-eye-off' in css
        assert "width: 44px" in css


def test_remote_sync_password_cleared_only_after_success():
    source = Path("app/static/js/remote_sync.js").read_text(encoding="utf-8")
    trigger = source.split("function triggerSync()", 1)[1].split("// --- Init ---", 1)[0]

    assert 'passwordInput.value = ""' in source
    assert trigger.count("clearPassword()") == 1
    assert "if (d.success) clearPassword();" in trigger
    assert "clearPassword()" not in trigger.rsplit(".catch(function () {", 1)[-1]


def test_password_visibility_script_has_no_storage():
    js_dir = Path("app/static/js")
    source = (js_dir / "password_visibility.js").read_text(encoding="utf-8")
    assert 'document.querySelectorAll("[data-password-toggle]")' in source
    assert 'button.getAttribute("aria-controls")' in source
    assert 'input.type = visible ? "text" : "password"' in source
    assert 'button.setAttribute("aria-pressed"' in source
    assert 'button.setAttribute("aria-label"' in source
    assert "button.textContent" not in source
    for name in ("password_visibility.js", "remote_sync.js", "cloud_recovery.js"):
        script = (js_dir / name).read_text(encoding="utf-8")
        assert all(storage not in script for storage in (
            "localStorage", "sessionStorage", "document.cookie", "indexedDB"
        ))
