"""Unit tests for module monitoring_frequency_days in UI forms and alerts display."""

import sys
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

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
from app.dependencies import require_current_user_html, require_current_user_api, get_current_user_optional

_fake_user = SimpleNamespace(id=1, full_name="Test", email="test@test.com", role="operator")


async def _override_html(request=None):
    return _fake_user


async def _override_api(request=None):
    return _fake_user


@pytest.fixture(autouse=True)
def _auth_overrides():
    """Override auth for all tests with proper cleanup."""
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[require_current_user_html] = _override_html
    app.dependency_overrides[require_current_user_api] = _override_api
    app.dependency_overrides[get_current_user_optional] = _override_html
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def _mock_greenhouse(gh_id=1):
    gh = MagicMock()
    gh.id = gh_id
    gh.name = "Invernadero Test"
    gh.location = None
    return gh


def _mock_module(module_id=1, gh_id=1, frequency=14):
    m = MagicMock()
    m.id = module_id
    m.greenhouse_id = gh_id
    m.name = "Módulo Test"
    m.crop_type = "Tomate Cherry"
    m.width_m = 5.0
    m.length_m = 2.0
    m.monitoring_frequency_days = frequency
    return m


# ---------------------------------------------------------------------------
# Form field presence tests
# ---------------------------------------------------------------------------


class TestModuleFormFrequencyField:
    """Tests that the module form contains the monitoring_frequency_days field."""

    def test_create_form_contains_frequency_field(self):
        """GET module create form includes monitoring_frequency_days input."""
        mock_gh_repo = MagicMock()
        mock_gh_repo.get_by_id.return_value = _mock_greenhouse()

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=mock_gh_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/invernaderos/1/modulos/crear")

        assert response.status_code == 200
        assert 'name="monitoring_frequency_days"' in response.text
        assert "Frecuencia de monitoreo" in response.text

    def test_create_form_shows_default_7(self):
        """Create form defaults monitoring_frequency_days to 7."""
        mock_gh_repo = MagicMock()
        mock_gh_repo.get_by_id.return_value = _mock_greenhouse()

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=mock_gh_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/invernaderos/1/modulos/crear")

        assert response.status_code == 200
        # The input value should be "7"
        assert 'value="7"' in response.text or "value=\"7\"" in response.text

    def test_edit_form_shows_existing_value(self):
        """GET module edit form shows existing monitoring_frequency_days."""
        mock_module_repo = MagicMock()
        mock_module_repo.get_by_id.return_value = _mock_module(frequency=14)

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/modulos/1/editar")

        assert response.status_code == 200
        assert 'value="14"' in response.text or "value=\"14\"" in response.text


# ---------------------------------------------------------------------------
# Form submission tests
# ---------------------------------------------------------------------------


class TestModuleFormFrequencySubmission:
    """Tests for POST create/edit with monitoring_frequency_days."""

    def test_create_with_valid_frequency(self):
        """POST create with monitoring_frequency_days=14 succeeds."""
        mock_gh_repo = MagicMock()
        mock_gh_repo.get_by_id.return_value = _mock_greenhouse()
        mock_module_repo = MagicMock()
        created_module = _mock_module(module_id=5, frequency=14)
        mock_module_repo.create.return_value = created_module

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=mock_gh_repo), \
             patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo):
            client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
            response = client.post(
                "/invernaderos/1/modulos/crear",
                data={
                    "name": "Módulo Nuevo",
                    "crop_type": "Tomate Cherry",
                    "width_m": "5.0",
                    "length_m": "2.0",
                    "monitoring_frequency_days": "14",
                },
            )

        assert response.status_code == 303
        # Verify the module was created with monitoring_frequency_days
        call_args = mock_module_repo.create.call_args
        module_arg = call_args[1].get("module") or call_args[0][1]
        assert module_arg.monitoring_frequency_days == 14

    def test_create_with_zero_frequency_shows_error(self):
        """POST create with monitoring_frequency_days=0 returns template with error."""
        mock_gh_repo = MagicMock()
        mock_gh_repo.get_by_id.return_value = _mock_greenhouse()

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=mock_gh_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/invernaderos/1/modulos/crear",
                data={
                    "name": "Módulo Nuevo",
                    "crop_type": "Tomate Cherry",
                    "width_m": "5.0",
                    "length_m": "2.0",
                    "monitoring_frequency_days": "0",
                },
            )

        assert response.status_code == 200
        assert "frecuencia" in response.text.lower() or "positivo" in response.text.lower()

    def test_create_with_negative_frequency_shows_error(self):
        """POST create with monitoring_frequency_days=-5 returns error."""
        mock_gh_repo = MagicMock()
        mock_gh_repo.get_by_id.return_value = _mock_greenhouse()

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=mock_gh_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/invernaderos/1/modulos/crear",
                data={
                    "name": "Módulo Nuevo",
                    "crop_type": "Tomate Cherry",
                    "width_m": "5.0",
                    "length_m": "2.0",
                    "monitoring_frequency_days": "-5",
                },
            )

        assert response.status_code == 200
        assert "frecuencia" in response.text.lower() or "positivo" in response.text.lower()

    def test_create_with_non_numeric_frequency_shows_error(self):
        """POST create with monitoring_frequency_days=abc returns error."""
        mock_gh_repo = MagicMock()
        mock_gh_repo.get_by_id.return_value = _mock_greenhouse()

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=mock_gh_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/invernaderos/1/modulos/crear",
                data={
                    "name": "Módulo Nuevo",
                    "crop_type": "Tomate Cherry",
                    "width_m": "5.0",
                    "length_m": "2.0",
                    "monitoring_frequency_days": "abc",
                },
            )

        assert response.status_code == 200
        assert "frecuencia" in response.text.lower() or "positivo" in response.text.lower()

    def test_edit_with_valid_frequency(self):
        """POST edit with monitoring_frequency_days=21 calls update."""
        mock_module_repo = MagicMock()
        existing = _mock_module(frequency=14)
        mock_module_repo.get_by_id.return_value = existing
        mock_module_repo.update.return_value = existing

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo):
            client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
            response = client.post(
                "/modulos/1/editar",
                data={
                    "name": "Módulo Test",
                    "crop_type": "Tomate Cherry",
                    "width_m": "5.0",
                    "length_m": "2.0",
                    "monitoring_frequency_days": "21",
                },
            )

        assert response.status_code == 303
        # Verify update was called with monitoring_frequency_days
        update_call = mock_module_repo.update.call_args
        fields = update_call[0][1] if len(update_call[0]) > 1 else update_call[1].get("fields", {})
        assert fields.get("monitoring_frequency_days") == 21


# ---------------------------------------------------------------------------
# Alerts display tests
# ---------------------------------------------------------------------------


class TestGreenhouseListAlerts:
    """Tests that greenhouse_list shows alerts when modules are overdue."""

    def test_greenhouse_list_shows_alerts_when_overdue(self):
        """Greenhouse list renders alerts section when AlertService returns alerts."""
        from datetime import datetime

        mock_gh_repo = MagicMock()
        gh = _mock_greenhouse()
        mock_gh_repo.get_all.return_value = [gh]

        mock_module_repo = MagicMock()
        module = _mock_module(frequency=7)
        mock_module_repo.get_by_greenhouse.return_value = [module]

        mock_monitoring_repo = MagicMock()
        # Last monitoring was 10 days ago → overdue
        old_monitoring = MagicMock()
        old_monitoring.id = 1
        old_monitoring.status = "completed"
        old_monitoring.started_at = datetime(2025, 1, 1)
        mock_monitoring_repo.get_by_module.return_value = [old_monitoring]

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=mock_gh_repo), \
             patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=mock_monitoring_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/invernaderos")

        assert response.status_code == 200
        assert "Alertas operativas" in response.text or "alerta" in response.text.lower()

    def test_greenhouse_list_no_unsupported_metrics(self):
        """Greenhouse list does not show unsupported agronomic metrics."""
        mock_gh_repo = MagicMock()
        mock_gh_repo.get_all.return_value = []
        mock_module_repo = MagicMock()
        mock_module_repo.get_by_greenhouse.return_value = []
        mock_monitoring_repo = MagicMock()
        mock_monitoring_repo.get_by_module.return_value = []

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=mock_gh_repo), \
             patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=mock_monitoring_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/invernaderos")

        assert response.status_code == 200
        text_lower = response.text.lower()
        assert "déficit hídrico" not in text_lower
        assert "rendimiento proyectado" not in text_lower
        assert "plaga detectada" not in text_lower
        assert "riego recomendado" not in text_lower


# ---------------------------------------------------------------------------
# Module detail frequency status tests
# ---------------------------------------------------------------------------


class TestModuleDetailFrequencyStatus:
    """Tests that module_detail shows monitoring frequency status."""

    def test_module_detail_shows_frequency(self):
        """Module detail page shows the monitoring frequency."""
        mock_module_repo = MagicMock()
        mock_module_repo.get_by_id.return_value = _mock_module(frequency=7)

        mock_monitoring_repo = MagicMock()
        mock_monitoring_repo.get_by_module.return_value = []

        mock_metrics_repo = MagicMock()
        mock_metrics_repo.get_by_monitoring.return_value = None

        mock_activity_log_repo = MagicMock()
        mock_activity_log_repo.list_by_module.return_value = []

        mock_activity_type_repo = MagicMock()
        mock_activity_type_repo.list_all.return_value = []

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=mock_monitoring_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_metrics_repository", return_value=mock_metrics_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=mock_activity_log_repo), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=mock_activity_type_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/modulos/1")

        assert response.status_code == 200
        # Should show frequency info
        assert "7" in response.text
        # Should show a status (pending since no monitorings)
        assert "Sin monitoreos" in response.text or "Pendiente" in response.text or "pendiente" in response.text
