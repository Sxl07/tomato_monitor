"""UI tests for the manual cloud recovery controls (Spec 022, block F).

Two layers, both offline:

1. Rendered-HTML assertions for /sincronizacion (Supabase configured): the
   Recovery button, descriptive text, confirmation modal, and password input
   are present, and no email field is requested for recovery.

2. Static-asset assertions for app/static/js/cloud_recovery.js: it POSTs to
   /api/recovery/trigger with only a password, clears the password, disables
   controls during the request, renders success/partial/error summaries,
   reloads only after the user dismisses a result with recovered data, and
   never polls or references /api/recovery/status.

No JS runtime is exercised; the controller logic is asserted against the file
content, mirroring the project's HTML-string test style.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

# Mock heavy ML dependencies not available in the test environment.
_MOCK_MODULES = [
    "torch", "torch.nn", "torch.nn.functional", "torch.utils",
    "torch.utils.data", "torch.cuda", "torch.hub",
    "torchvision", "torchvision.transforms", "torchvision.transforms.functional",
    "torchvision.models", "torchvision.ops",
    "detectron2", "detectron2.config", "detectron2.engine",
    "detectron2.modeling", "detectron2.data", "detectron2.structures",
    "detectron2.utils", "detectron2.utils.logger",
    "detectron2.checkpoint", "detectron2.engine.defaults",
    "PIL", "PIL.Image",
    "picamera2",
]
for _mod_name in _MOCK_MODULES:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import (
    get_activity_log_repository,
    get_export_package_repository,
    get_monitoring_repository,
    get_current_user_optional,
    require_current_user_html,
)


_JS_PATH = Path("app/static/js/cloud_recovery.js")


_fake_user = SimpleNamespace(
    id=1, full_name="Test Operator", email="test@test.com", role="operator"
)


def _make_supabase_config():
    from src.infrastructure.supabase.supabase_config import SupabaseConfig

    return SupabaseConfig(
        url="https://test.supabase.co",
        publishable_key="test-key",
        storage_bucket="test-bucket",
    )


@pytest.fixture
def rendered_html():
    """Render /sincronizacion with Supabase configured and empty repos."""
    async def _html_user(request=None):
        return _fake_user

    mono_repo = MagicMock()
    mono_repo.list_all.return_value = []
    act_repo = MagicMock()
    act_repo.list_all.return_value = []
    exp_repo = MagicMock()
    exp_repo.list_by_user.return_value = []

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[require_current_user_html] = _html_user
    app.dependency_overrides[get_current_user_optional] = _html_user
    app.dependency_overrides[get_monitoring_repository] = lambda: mono_repo
    app.dependency_overrides[get_activity_log_repository] = lambda: act_repo
    app.dependency_overrides[get_export_package_repository] = lambda: exp_repo
    try:
        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            resp = client.get("/sincronizacion")
        assert resp.status_code == 200
        return resp.text
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.fixture(scope="module")
def js_source():
    return _JS_PATH.read_text(encoding="utf-8")


# ===========================================================================
# Rendered HTML: button, description, modal, password input
# ===========================================================================


class TestRecoveryMarkup:
    def test_recovery_button_rendered(self, rendered_html):
        assert 'id="cloud-recovery-btn"' in rendered_html
        assert "Recuperar datos desde la nube" in rendered_html

    def test_descriptive_text_present(self, rendered_html):
        # Direction nube -> dispositivo, no-overwrite wording.
        assert "datos históricos disponibles" in rendered_html
        assert "no serán sobrescritos" in rendered_html
        # Forbidden wording must NOT appear.
        low = rendered_html.lower()
        assert "restaurar backup completo" not in low
        assert "sobrescribir datos" not in low
        assert "sincronización bidireccional" not in low
        assert "descargar toda la cuenta" not in low

    def test_modal_exists(self, rendered_html):
        assert 'id="cloud-recovery-modal"' in rendered_html
        assert 'role="dialog"' in rendered_html
        assert 'aria-modal="true"' in rendered_html

    def test_password_input_type_password_with_label(self, rendered_html):
        assert 'id="recovery-password"' in rendered_html
        assert 'type="password"' in rendered_html
        # Label associated with the input.
        assert 'for="recovery-password"' in rendered_html

    def test_no_email_field_requested(self, rendered_html):
        # Recovery must not ask for an email; backend uses the session user.
        assert 'id="recovery-email"' not in rendered_html
        assert 'name="email"' not in rendered_html

    def test_modal_has_text_buttons(self, rendered_html):
        assert "Cancelar" in rendered_html
        assert 'id="cloud-recovery-confirm-btn"' in rendered_html
        assert 'id="cloud-recovery-cancel-btn"' in rendered_html

    def test_recovery_and_sync_coexist(self, rendered_html):
        # Sync button preserved; recovery added; distinct labels.
        assert 'id="remote-sync-btn"' in rendered_html
        assert "Sincronizar ahora" in rendered_html
        assert 'id="cloud-recovery-btn"' in rendered_html

    def test_script_included(self, rendered_html):
        assert "/static/js/cloud_recovery.js" in rendered_html


# ===========================================================================
# Static JS behavior assertions
# ===========================================================================


class TestRecoveryJs:
    def test_posts_to_recovery_trigger(self, js_source):
        assert '"/api/recovery/trigger"' in js_source
        assert '"POST"' in js_source

    def test_body_contains_only_password(self, js_source):
        assert "JSON.stringify({ password: password })" in js_source
        # Never send email/user id/remote id.
        assert "email" not in js_source
        assert "remote_user_id" not in js_source

    def test_no_recovery_status_endpoint(self, js_source):
        assert "/api/recovery/status" not in js_source

    def test_no_polling(self, js_source):
        assert "setInterval" not in js_source
        assert "pollRecovery" not in js_source

    def test_password_cleared(self, js_source):
        # A dedicated clear function assigning "" and used in finally/cancel.
        assert 'passwordInput.value = ""' in js_source
        assert "clearPassword" in js_source
        assert ".finally(" in js_source

    def test_disables_controls_during_request(self, js_source):
        assert "setBusy(true)" in js_source
        assert "setBusy(false)" in js_source
        # Recovery button disabled during request.
        assert "recoveryBtn.disabled = busy" in js_source

    def test_no_direct_sync_disable_clobber(self, js_source):
        # The dangerous direct pattern must NOT exist; ownership helper instead.
        assert "syncBtn.disabled = busy" not in js_source

    def test_ownership_marks_sync_disable(self, js_source):
        # Recovery owns its Sync disable via dataset marker.
        assert "setSyncBlockedByRecovery" in js_source
        assert "syncBtn.dataset.disabledByRecovery" in js_source
        # Only disables Sync when not already disabled.
        assert "if (!syncBtn.disabled)" in js_source
        # Only re-enables the disable it owns.
        assert 'syncBtn.dataset.disabledByRecovery === "true"' in js_source

    def test_loading_text(self, js_source):
        assert "Recuperando datos..." in js_source

    def test_success_summary_counters(self, js_source):
        assert "entities_recovered" in js_source
        assert "entities_reused" in js_source
        assert "images_downloaded" in js_source

    def test_partial_success_warning(self, js_source):
        assert "Recuperación completada parcialmente" in js_source
        assert "alert alert-warning" in js_source
        # Partial success driven by success === false on HTTP 200.
        assert "data.success === false" in js_source

    def test_error_handling_by_status(self, js_source):
        assert "mapErrorDetail" in js_source
        assert "result.data.detail" in js_source
        # Network/fetch failure branch.
        assert "No se pudo conectar con el servicio de recuperación." in js_source

    def test_reload_only_after_dismiss_with_recovered_data(self, js_source):
        # Reload guarded by entities_recovered > 0 and pendingReload flag.
        assert "data.entities_recovered > 0" in js_source
        assert "pendingReload" in js_source
        assert "window.location.reload()" in js_source
        assert "handleSuccess" in js_source

    def test_result_has_explicit_accept_button(self, js_source):
        # Every result renders an explicit Aceptar button (type=button).
        assert 'id="cloud-recovery-result-accept"' in js_source
        assert ">Aceptar<" in js_source
        assert 'type="button"' in js_source
        assert "acceptResult" in js_source

    def test_accept_button_drives_reload(self, js_source):
        # acceptResult reloads only when pendingReload; otherwise just dismiss.
        assert "function acceptResult" in js_source
        assert "if (pendingReload)" in js_source
        assert "hideResult()" in js_source

    def test_no_generic_click_reload_on_result(self, js_source):
        # The old generic-click-to-reload listener on resultEl must be gone.
        assert 'resultEl.addEventListener("click"' not in js_source


# ===========================================================================
# Sync JS integration: sync blocks recovery with ownership + status reconcile
# ===========================================================================


_SYNC_JS_PATH = Path("app/static/js/remote_sync.js")


@pytest.fixture(scope="module")
def sync_js_source():
    return _SYNC_JS_PATH.read_text(encoding="utf-8")


class TestSyncBlocksRecoveryJs:
    def test_sync_knows_recovery_button(self, sync_js_source):
        assert 'getElementById("cloud-recovery-btn")' in sync_js_source

    def test_sync_blocks_recovery_on_start(self, sync_js_source):
        assert "setRecoveryBlockedBySync(true)" in sync_js_source

    def test_sync_releases_recovery_on_finish_and_failure(self, sync_js_source):
        # Released in both the resolved and the catch branches.
        assert sync_js_source.count("setRecoveryBlockedBySync(false)") >= 2

    def test_sync_ownership_marker(self, sync_js_source):
        assert "recoveryBtn.dataset.disabledBySync" in sync_js_source
        assert "if (!recoveryBtn.disabled)" in sync_js_source
        assert 'recoveryBtn.dataset.disabledBySync === "true"' in sync_js_source

    def test_render_status_reconciles_recovery_with_is_syncing(self, sync_js_source):
        # renderStatus uses data.is_syncing to reconcile Recovery availability.
        assert "setRecoveryBlockedBySync(data.is_syncing === true)" in sync_js_source

    def test_sync_does_not_use_recovery_status_endpoint(self, sync_js_source):
        assert "/api/recovery/status" not in sync_js_source
