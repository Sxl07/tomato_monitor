"""Unit tests for agricultural activity log UI routes."""

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
from src.domain.entities.activity_type import ActivityType

_fake_user = SimpleNamespace(id=1, full_name="Test Operator", email="test@test.com", role="operator")


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


def _mock_module(module_id=1, gh_id=1):
    m = MagicMock()
    m.id = module_id
    m.greenhouse_id = gh_id
    m.name = "Módulo Test"
    m.crop_type = "Tomate Cherry"
    m.width_m = 5.0
    m.length_m = 2.0
    m.monitoring_frequency_days = 7
    return m


def _owned_gh_repo(gh_id=1, owner_user_id=1):
    """Greenhouse repo mock honoring the productive ownership contract.

    The routes resolve module ownership via module -> greenhouse -> owner using
    ``get_by_id_for_owner(greenhouse_id, user.id)``. This mock returns the
    greenhouse only for the matching owner (explicit ownership, not truthiness),
    so the authenticated fixture user (id=1) owns the module's greenhouse.
    """
    gh = SimpleNamespace(id=gh_id, owner_user_id=owner_user_id, name="Invernadero Test")

    def _get_by_id_for_owner(greenhouse_id, uid):
        if uid == owner_user_id and greenhouse_id == gh_id:
            return gh
        return None

    repo = MagicMock()
    repo.get_by_id_for_owner.side_effect = _get_by_id_for_owner
    return repo


def _mock_activity_types():
    types = []
    at1 = ActivityType(code="riego", name="Riego", category="mantenimiento",
                       requires_product=False, allows_quantity=True, default_unit="L")
    at1.id = 1
    types.append(at1)
    at2 = ActivityType(code="fertilizacion", name="Fertilización", category="nutrición",
                       requires_product=True, allows_quantity=True, default_unit="kg")
    at2.id = 2
    types.append(at2)
    return types


# ---------------------------------------------------------------------------
# GET activity form tests
# ---------------------------------------------------------------------------


class TestActivityFormGet:
    """Tests for GET /modulos/{id}/actividades/registrar."""

    def test_renders_form_with_activity_types(self):
        """GET form renders with activity type options."""
        mock_module_repo = MagicMock()
        mock_module_repo.get_by_id.return_value = _mock_module()

        mock_type_repo = MagicMock()
        mock_type_repo.list_active.return_value = _mock_activity_types()

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo), \
             patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=_owned_gh_repo()), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=mock_type_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/modulos/1/actividades/registrar")

        assert response.status_code == 200
        assert "Riego" in response.text
        assert "Fertilización" in response.text
        assert "Guardar actividad" in response.text
        assert 'name="activity_type_id"' in response.text

    def test_form_contains_dynamic_field_markers(self):
        """Form contains data attributes and field IDs for dynamic JS."""
        mock_module_repo = MagicMock()
        mock_module_repo.get_by_id.return_value = _mock_module()

        mock_type_repo = MagicMock()
        mock_type_repo.list_active.return_value = _mock_activity_types()

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo), \
             patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=_owned_gh_repo()), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=mock_type_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/modulos/1/actividades/registrar")

        assert response.status_code == 200
        assert "data-requires-product" in response.text
        assert "data-allows-quantity" in response.text
        assert "data-default-unit" in response.text
        assert 'id="product-field"' in response.text
        assert 'id="quantity-field"' in response.text
        assert 'id="unit-field"' in response.text
        assert "addEventListener" in response.text

    def test_form_module_not_found_redirects(self):
        """GET form for non-existent module redirects."""
        mock_module_repo = MagicMock()
        mock_module_repo.get_by_id.return_value = None

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo):
            client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
            response = client.get("/modulos/999/actividades/registrar")

        assert response.status_code == 303


# ---------------------------------------------------------------------------
# POST activity form tests
# ---------------------------------------------------------------------------


class TestActivityFormPost:
    """Tests for POST /modulos/{id}/actividades/registrar."""

    def test_valid_submission_redirects_to_list(self):
        """POST with valid data redirects to activity list."""
        mock_module_repo = MagicMock()
        mock_module_repo.get_by_id.return_value = _mock_module()

        at = _mock_activity_types()[0]  # Riego
        mock_type_repo = MagicMock()
        mock_type_repo.get_by_id.return_value = at
        mock_type_repo.list_active.return_value = _mock_activity_types()

        mock_log_repo = MagicMock()

        def _create(log):
            log.id = 1
            return log

        mock_log_repo.create.side_effect = _create

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo), \
             patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=_owned_gh_repo()), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=mock_type_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=mock_log_repo):
            client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
            response = client.post(
                "/modulos/1/actividades/registrar",
                data={
                    "activity_type_id": "1",
                    "occurred_at_date": "2025-06-15",
                    "occurred_at_time": "10:00",
                    "product_name": "",
                    "quantity": "5.0",
                    "unit": "L",
                    "notes": "Test irrigation",
                },
            )

        assert response.status_code == 303
        assert "/modulos/1/actividades" in response.headers["location"]

    def test_missing_required_product_shows_error(self):
        """POST with missing product for requires_product type shows error."""
        mock_module_repo = MagicMock()
        mock_module_repo.get_by_id.return_value = _mock_module()

        at = _mock_activity_types()[1]  # Fertilización (requires_product=True)
        mock_type_repo = MagicMock()
        mock_type_repo.get_by_id.return_value = at
        mock_type_repo.list_active.return_value = _mock_activity_types()

        mock_log_repo = MagicMock()

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo), \
             patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=_owned_gh_repo()), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=mock_type_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=mock_log_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/modulos/1/actividades/registrar",
                data={
                    "activity_type_id": "2",
                    "occurred_at_date": "",
                    "occurred_at_time": "",
                    "product_name": "",
                    "quantity": "",
                    "unit": "",
                    "notes": "",
                },
            )

        assert response.status_code == 200
        assert "producto" in response.text.lower() or "obligatorio" in response.text.lower()

    def test_negative_quantity_shows_error(self):
        """POST with negative quantity shows validation error."""
        mock_module_repo = MagicMock()
        mock_module_repo.get_by_id.return_value = _mock_module()

        at = _mock_activity_types()[0]  # Riego (allows_quantity=True)
        mock_type_repo = MagicMock()
        mock_type_repo.get_by_id.return_value = at
        mock_type_repo.list_active.return_value = _mock_activity_types()

        mock_log_repo = MagicMock()

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo), \
             patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=_owned_gh_repo()), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=mock_type_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=mock_log_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/modulos/1/actividades/registrar",
                data={
                    "activity_type_id": "1",
                    "occurred_at_date": "",
                    "occurred_at_time": "",
                    "product_name": "",
                    "quantity": "-5",
                    "unit": "L",
                    "notes": "",
                },
            )

        assert response.status_code == 200
        assert "cantidad" in response.text.lower() or "mayor" in response.text.lower()

    def test_valid_submission_preserves_user_id(self):
        """POST with valid data uses authenticated user.id in the created ActivityLog."""
        mock_module_repo = MagicMock()
        mock_module_repo.get_by_id.return_value = _mock_module()

        at = _mock_activity_types()[0]  # Riego
        mock_type_repo = MagicMock()
        mock_type_repo.get_by_id.return_value = at
        mock_type_repo.list_active.return_value = _mock_activity_types()

        mock_log_repo = MagicMock()
        created_logs = []

        def _create(log):
            created_logs.append(log)
            log.id = 1
            return log

        mock_log_repo.create.side_effect = _create

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo), \
             patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=_owned_gh_repo()), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=mock_type_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=mock_log_repo):
            client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
            response = client.post(
                "/modulos/1/actividades/registrar",
                data={
                    "activity_type_id": "1",
                    "occurred_at_date": "2025-06-15",
                    "occurred_at_time": "10:00",
                    "product_name": "",
                    "quantity": "5.0",
                    "unit": "L",
                    "notes": "",
                },
            )

        assert response.status_code == 303
        assert len(created_logs) == 1
        assert created_logs[0].user_id == 1  # _fake_user.id
        assert created_logs[0].module_id == 1
        assert created_logs[0].activity_type_id == 1
        assert created_logs[0].sync_status == "pending"
        # Verify occurred_at corresponds to the submitted date/time
        assert created_logs[0].occurred_at.year == 2025
        assert created_logs[0].occurred_at.month == 6
        assert created_logs[0].occurred_at.day == 15
        assert created_logs[0].occurred_at.hour == 15  # 10:00 Bogota = 15:00 UTC
        assert created_logs[0].occurred_at.minute == 0

    def test_allows_quantity_false_ignores_quantity_and_unit_from_ui(self):
        """POST with allows_quantity=False type ignores quantity and unit values."""
        mock_module_repo = MagicMock()
        mock_module_repo.get_by_id.return_value = _mock_module()

        at_no_qty = ActivityType(
            code="inspeccion_visual", name="Inspección visual", category="vigilancia",
            requires_product=False, allows_quantity=False, default_unit=None, is_active=True,
        )
        at_no_qty.id = 3

        mock_type_repo = MagicMock()
        mock_type_repo.get_by_id.return_value = at_no_qty
        mock_type_repo.list_active.return_value = [at_no_qty]

        mock_log_repo = MagicMock()
        created_logs = []

        def _create(log):
            created_logs.append(log)
            log.id = 1
            return log

        mock_log_repo.create.side_effect = _create

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo), \
             patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=_owned_gh_repo()), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=mock_type_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=mock_log_repo):
            client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
            response = client.post(
                "/modulos/1/actividades/registrar",
                data={
                    "activity_type_id": "3",
                    "occurred_at_date": "",
                    "occurred_at_time": "",
                    "product_name": "",
                    "quantity": "999",
                    "unit": "kg",
                    "notes": "",
                },
            )

        assert response.status_code == 303
        assert len(created_logs) == 1
        assert created_logs[0].quantity is None
        assert created_logs[0].unit is None
        assert created_logs[0].user_id == 1
        assert created_logs[0].sync_status == "pending"

    def test_inactive_type_shows_spanish_error(self):
        """POST with inactive activity type shows error in Spanish."""
        mock_module_repo = MagicMock()
        mock_module_repo.get_by_id.return_value = _mock_module()

        at = _mock_activity_types()[0]
        at_inactive = ActivityType(code="riego", name="Riego", category="mantenimiento",
                                   requires_product=False, allows_quantity=True, default_unit="L",
                                   is_active=False)
        at_inactive.id = 1
        mock_type_repo = MagicMock()
        mock_type_repo.get_by_id.return_value = at_inactive
        mock_type_repo.list_active.return_value = _mock_activity_types()

        mock_log_repo = MagicMock()

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo), \
             patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=_owned_gh_repo()), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=mock_type_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=mock_log_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/modulos/1/actividades/registrar",
                data={
                    "activity_type_id": "1",
                    "occurred_at_date": "",
                    "occurred_at_time": "",
                    "product_name": "",
                    "quantity": "",
                    "unit": "",
                    "notes": "",
                },
            )

        assert response.status_code == 200
        # Error message must be in Spanish
        assert "no encontrado" in response.text.lower() or "inactivo" in response.text.lower()


# ---------------------------------------------------------------------------
# GET activity list tests
# ---------------------------------------------------------------------------


class TestActivityListGet:
    """Tests for GET /modulos/{id}/actividades."""

    def test_shows_activities(self):
        """GET activity list shows activities for the module."""
        from datetime import datetime
        from src.domain.entities.activity_log import ActivityLog

        mock_module_repo = MagicMock()
        mock_module_repo.get_by_id.return_value = _mock_module()

        at = _mock_activity_types()[0]
        mock_type_repo = MagicMock()
        mock_type_repo.list_all.return_value = _mock_activity_types()

        log = ActivityLog(
            module_id=1, activity_type_id=1, user_id=1,
            product_name="Agua", quantity=10.0, unit="L",
            notes="Morning", occurred_at=datetime(2025, 6, 15, 8, 0, 0),
            sync_status="pending",
        )
        log.id = 1
        mock_log_repo = MagicMock()
        mock_log_repo.list_by_module.return_value = [log]

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo), \
             patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=_owned_gh_repo()), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=mock_type_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=mock_log_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/modulos/1/actividades")

        assert response.status_code == 200
        assert "Riego" in response.text
        assert "Agua" in response.text
        assert "15/06/2025" in response.text

    def test_empty_list_shows_empty_state(self):
        """GET activity list with no activities shows empty state."""
        mock_module_repo = MagicMock()
        mock_module_repo.get_by_id.return_value = _mock_module()

        mock_type_repo = MagicMock()
        mock_type_repo.list_all.return_value = []

        mock_log_repo = MagicMock()
        mock_log_repo.list_by_module.return_value = []

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo), \
             patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=_owned_gh_repo()), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=mock_type_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=mock_log_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/modulos/1/actividades")

        assert response.status_code == 200
        assert "No hay actividades registradas" in response.text


# ---------------------------------------------------------------------------
# Module detail links tests
# ---------------------------------------------------------------------------


class TestModuleDetailActivityLinks:
    """Tests that module_detail contains activity links."""

    def test_module_detail_contains_activity_links(self):
        """Module detail page has links to register activity and view log."""
        mock_module_repo = MagicMock()
        mock_module_repo.get_by_id.return_value = _mock_module()

        mock_monitoring_repo = MagicMock()
        mock_monitoring_repo.get_by_module.return_value = []

        mock_metrics_repo = MagicMock()
        mock_metrics_repo.get_by_monitoring.return_value = None

        mock_activity_log_repo = MagicMock()
        mock_activity_log_repo.list_by_module.return_value = []

        mock_activity_type_repo = MagicMock()
        mock_activity_type_repo.list_all.return_value = []

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo), \
             patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=_owned_gh_repo()), \
             patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=mock_monitoring_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_metrics_repository", return_value=mock_metrics_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=mock_activity_log_repo), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=mock_activity_type_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/modulos/1")

        assert response.status_code == 200
        assert "Registrar actividad" in response.text
        assert "Ver bitácora" in response.text
        assert "/modulos/1/actividades/registrar" in response.text
        assert "/modulos/1/actividades" in response.text


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


class TestActivityRoutesAuth:
    """Tests that activity routes require authentication."""

    def test_activity_list_without_auth_redirects(self):
        """GET activity list without auth redirects to login."""
        # Remove auth overrides
        from app.dependencies import _AuthRedirectException

        app.dependency_overrides.pop(require_current_user_html, None)

        # Simulate _AuthRedirectException being raised
        client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
        response = client.get("/modulos/1/actividades")

        # Without auth, should redirect to login (302 or 303)
        assert response.status_code in (302, 303)
        assert "/login" in response.headers.get("location", "")

    def test_activity_form_without_auth_redirects(self):
        """GET activity form without auth redirects to login."""
        app.dependency_overrides.pop(require_current_user_html, None)

        client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
        response = client.get("/modulos/1/actividades/registrar")

        assert response.status_code in (302, 303)
        assert "/login" in response.headers.get("location", "")
