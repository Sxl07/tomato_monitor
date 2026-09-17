"""Unit tests for the dashboard UI route (/dashboard)."""

import sys
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from datetime import datetime

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


def _mock_greenhouse(id=1, name="Invernadero 1"):
    gh = MagicMock()
    gh.id = id
    gh.name = name
    return gh


def _mock_module(id=1, gh_id=1, name="Módulo 1"):
    m = MagicMock()
    m.id = id
    m.greenhouse_id = gh_id
    m.name = name
    m.crop_type = "Tomate Cherry"
    m.monitoring_frequency_days = 7
    return m


def _mock_monitoring(id=1, status="completed", started_at=None, snapshots=5, detections=20):
    mon = MagicMock()
    mon.id = id
    mon.status = status
    mon.started_at = started_at or datetime(2025, 6, 18, 10, 30)
    mon.total_snapshots = snapshots
    mon.total_detections = detections
    return mon


def _mock_activity_log(id=1, type_id=1, occurred_at=None):
    log = MagicMock()
    log.id = id
    log.activity_type_id = type_id
    log.module_id = 1
    log.occurred_at = occurred_at or datetime(2025, 6, 18, 9, 0)
    return log


def _mock_activity_type(id=1, code="riego", name="Riego", category="mantenimiento"):
    at = MagicMock()
    at.id = id
    at.code = code
    at.name = name
    at.category = category
    return at


def _setup_mocks(greenhouses=None, modules=None, monitorings_by_module=None,
                 activity_logs=None, activity_types=None, export_packages=None):
    """Create mock repos for the dashboard route.

    Spec 024: the route uses owner-scoped reads (get_all_by_owner /
    list_by_user) and the analytics scope builder issues bulk metrics/inspection
    reads. Mocks reflect the real method names the route calls.
    """
    gh_repo = MagicMock()
    # Route uses get_all_by_owner(user.id) (Spec 022 isolation), not get_all.
    gh_repo.get_all_by_owner.return_value = greenhouses or []
    gh_repo.get_all.return_value = greenhouses or []

    module_repo = MagicMock()
    # get_by_greenhouse returns modules for a specific greenhouse
    if modules:
        module_repo.get_by_greenhouse.return_value = modules
    else:
        module_repo.get_by_greenhouse.return_value = []

    monitoring_repo = MagicMock()
    if monitorings_by_module:
        monitoring_repo.get_by_module.side_effect = lambda mid: monitorings_by_module.get(mid, [])
    else:
        monitoring_repo.get_by_module.return_value = []

    activity_log_repo = MagicMock()
    activity_log_repo.list_recent.return_value = activity_logs or []
    activity_log_repo.list_by_module.return_value = []

    activity_type_repo = MagicMock()
    activity_type_repo.list_all.return_value = activity_types or []

    export_repo = MagicMock()
    # Route uses list_by_user(user.id) (Spec 022 isolation), not list_pending.
    export_repo.list_by_user.return_value = export_packages or []
    export_repo.list_pending.return_value = export_packages or []

    # Spec 024 analytics: bulk repos return empty dicts by default so the
    # analytics scope builder produces empty/insufficient analytics without
    # touching the database.
    metrics_repo = MagicMock()
    metrics_repo.get_by_monitoring_ids.return_value = {}
    inspection_repo = MagicMock()
    inspection_repo.get_by_monitoring_ids.return_value = {}

    return (
        gh_repo, module_repo, monitoring_repo, activity_log_repo,
        activity_type_repo, export_repo, metrics_repo, inspection_repo,
    )


# ---------------------------------------------------------------------------
# GET /dashboard tests
# ---------------------------------------------------------------------------


class TestDashboardRoute:
    """Tests for the GET /dashboard endpoint."""

    def test_dashboard_returns_200(self):
        """GET /dashboard returns 200 when authenticated."""
        gh_repo, module_repo, monitoring_repo, log_repo, type_repo, export_repo, metrics_repo, inspection_repo = _setup_mocks()

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=gh_repo), \
             patch("app.routes.agricultural_ui.get_module_repository", return_value=module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=monitoring_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=log_repo), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=type_repo), \
             patch("app.routes.agricultural_ui.get_export_package_repository", return_value=export_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_metrics_repository", return_value=metrics_repo), \
             patch("app.routes.agricultural_ui.get_inspection_result_repository", return_value=inspection_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/dashboard")

        assert response.status_code == 200

    def test_dashboard_shows_metric_cards(self):
        """Dashboard renders indicator values."""
        gh = _mock_greenhouse()
        mod = _mock_module()
        mon = _mock_monitoring(snapshots=10, detections=45)

        gh_repo, module_repo, monitoring_repo, log_repo, type_repo, export_repo, metrics_repo, inspection_repo = _setup_mocks(
            greenhouses=[gh],
            modules=[mod],
            monitorings_by_module={mod.id: [mon]},
        )

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=gh_repo), \
             patch("app.routes.agricultural_ui.get_module_repository", return_value=module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=monitoring_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=log_repo), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=type_repo), \
             patch("app.routes.agricultural_ui.get_export_package_repository", return_value=export_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_metrics_repository", return_value=metrics_repo), \
             patch("app.routes.agricultural_ui.get_inspection_result_repository", return_value=inspection_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/dashboard")

        assert response.status_code == 200
        assert "Invernaderos" in response.text
        assert "Módulos" in response.text
        assert "Tomates detectados" in response.text

    def test_dashboard_shows_alerts_section(self):
        """Dashboard shows alerts when modules are pending."""
        mod = _mock_module()
        gh = _mock_greenhouse()

        gh_repo, module_repo, monitoring_repo, log_repo, type_repo, export_repo, metrics_repo, inspection_repo = _setup_mocks(
            greenhouses=[gh],
            modules=[mod],
            monitorings_by_module={mod.id: []},  # No monitorings → pending alert
        )

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=gh_repo), \
             patch("app.routes.agricultural_ui.get_module_repository", return_value=module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=monitoring_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=log_repo), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=type_repo), \
             patch("app.routes.agricultural_ui.get_export_package_repository", return_value=export_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_metrics_repository", return_value=metrics_repo), \
             patch("app.routes.agricultural_ui.get_inspection_result_repository", return_value=inspection_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/dashboard")

        assert response.status_code == 200
        assert "pendiente" in response.text.lower()

    def test_dashboard_shows_recent_activities(self):
        """Dashboard shows recent activity entries.

        Spec 024/022: the route gathers activities by descending the user's
        modules via activity_log_repo.list_by_module(module_id), so the mock
        must provide a greenhouse + module and wire list_by_module.
        """
        gh = _mock_greenhouse()
        mod = _mock_module()
        log = _mock_activity_log()
        at = _mock_activity_type()

        gh_repo, module_repo, monitoring_repo, log_repo, type_repo, export_repo, metrics_repo, inspection_repo = _setup_mocks(
            greenhouses=[gh],
            modules=[mod],
            activity_logs=[log],
            activity_types=[at],
        )
        # Route reads activities per module.
        log_repo.list_by_module.side_effect = lambda mid: [log] if mid == mod.id else []

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=gh_repo), \
             patch("app.routes.agricultural_ui.get_module_repository", return_value=module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=monitoring_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=log_repo), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=type_repo), \
             patch("app.routes.agricultural_ui.get_export_package_repository", return_value=export_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_metrics_repository", return_value=metrics_repo), \
             patch("app.routes.agricultural_ui.get_inspection_result_repository", return_value=inspection_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/dashboard")

        assert response.status_code == 200
        assert "Riego" in response.text
        assert "Actividades recientes" in response.text

    def test_dashboard_empty_state(self):
        """Dashboard renders correctly with no data at all."""
        gh_repo, module_repo, monitoring_repo, log_repo, type_repo, export_repo, metrics_repo, inspection_repo = _setup_mocks()

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=gh_repo), \
             patch("app.routes.agricultural_ui.get_module_repository", return_value=module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=monitoring_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=log_repo), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=type_repo), \
             patch("app.routes.agricultural_ui.get_export_package_repository", return_value=export_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_metrics_repository", return_value=metrics_repo), \
             patch("app.routes.agricultural_ui.get_inspection_result_repository", return_value=inspection_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/dashboard")

        assert response.status_code == 200
        assert "No hay monitoreos registrados" in response.text
        assert "No hay actividades recientes" in response.text

    def test_dashboard_shows_last_monitoring_info(self):
        """Dashboard shows last monitoring details when available."""
        mon = _mock_monitoring(snapshots=8, detections=30, started_at=datetime(2025, 6, 18, 14, 30))
        mod = _mock_module()
        gh = _mock_greenhouse()

        gh_repo, module_repo, monitoring_repo, log_repo, type_repo, export_repo, metrics_repo, inspection_repo = _setup_mocks(
            greenhouses=[gh],
            modules=[mod],
            monitorings_by_module={mod.id: [mon]},
        )

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=gh_repo), \
             patch("app.routes.agricultural_ui.get_module_repository", return_value=module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=monitoring_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=log_repo), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=type_repo), \
             patch("app.routes.agricultural_ui.get_export_package_repository", return_value=export_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_metrics_repository", return_value=metrics_repo), \
             patch("app.routes.agricultural_ui.get_inspection_result_repository", return_value=inspection_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/dashboard")

        assert response.status_code == 200
        assert "Último monitoreo" in response.text
        assert "18/06/2025" in response.text


# ---------------------------------------------------------------------------
# Home redirect tests
# ---------------------------------------------------------------------------


class TestHomeRedirect:
    """Tests for GET / redirect behavior."""

    def test_home_redirects_to_dashboard(self):
        """GET / redirects to /dashboard when authenticated."""
        gh_repo, module_repo, monitoring_repo, log_repo, type_repo, export_repo, metrics_repo, inspection_repo = _setup_mocks()

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=gh_repo), \
             patch("app.routes.agricultural_ui.get_module_repository", return_value=module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=monitoring_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=log_repo), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=type_repo), \
             patch("app.routes.agricultural_ui.get_export_package_repository", return_value=export_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_metrics_repository", return_value=metrics_repo), \
             patch("app.routes.agricultural_ui.get_inspection_result_repository", return_value=inspection_repo):
            client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
            response = client.get("/")

        assert response.status_code == 302
        assert "/dashboard" in response.headers["location"]


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


class TestDashboardAuth:
    """Tests for dashboard authentication requirements."""

    def test_dashboard_without_auth_redirects_to_login(self):
        """GET /dashboard without auth redirects to login."""
        app.dependency_overrides.pop(require_current_user_html, None)

        client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
        response = client.get("/dashboard")

        assert response.status_code in (302, 303)
        assert "/login" in response.headers.get("location", "")

    def test_home_without_auth_redirects_to_login(self):
        """GET / without auth redirects to login."""
        app.dependency_overrides.pop(require_current_user_html, None)

        client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
        response = client.get("/")

        assert response.status_code in (302, 303)
        assert "/login" in response.headers.get("location", "")


# ---------------------------------------------------------------------------
# Prohibited content tests
# ---------------------------------------------------------------------------


class TestDashboardNoProhibitedMetrics:
    """Ensure no unsupported agronomic metrics appear in the dashboard."""

    _PROHIBITED_TERMS = [
        "salud del cultivo",
        "déficit hídrico",
        "plaga detectada",
        "Tuta absoluta",
        "rendimiento proyectado",
        "eficiencia hídrica",
        "riego recomendado",
    ]

    def test_no_prohibited_metrics_empty_dashboard(self):
        """Empty dashboard does not contain any prohibited metric terms."""
        gh_repo, module_repo, monitoring_repo, log_repo, type_repo, export_repo, metrics_repo, inspection_repo = _setup_mocks()

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=gh_repo), \
             patch("app.routes.agricultural_ui.get_module_repository", return_value=module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=monitoring_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=log_repo), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=type_repo), \
             patch("app.routes.agricultural_ui.get_export_package_repository", return_value=export_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_metrics_repository", return_value=metrics_repo), \
             patch("app.routes.agricultural_ui.get_inspection_result_repository", return_value=inspection_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/dashboard")

        text_lower = response.text.lower()
        for term in self._PROHIBITED_TERMS:
            assert term.lower() not in text_lower, f"Prohibited term '{term}' found in dashboard"

    def test_no_prohibited_metrics_with_data(self):
        """Dashboard with data does not contain any prohibited metric terms."""
        gh = _mock_greenhouse()
        mod = _mock_module()
        mon = _mock_monitoring(snapshots=10, detections=45)

        gh_repo, module_repo, monitoring_repo, log_repo, type_repo, export_repo, metrics_repo, inspection_repo = _setup_mocks(
            greenhouses=[gh],
            modules=[mod],
            monitorings_by_module={mod.id: [mon]},
        )

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=gh_repo), \
             patch("app.routes.agricultural_ui.get_module_repository", return_value=module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=monitoring_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=log_repo), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=type_repo), \
             patch("app.routes.agricultural_ui.get_export_package_repository", return_value=export_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_metrics_repository", return_value=metrics_repo), \
             patch("app.routes.agricultural_ui.get_inspection_result_repository", return_value=inspection_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/dashboard")

        text_lower = response.text.lower()
        for term in self._PROHIBITED_TERMS:
            assert term.lower() not in text_lower, f"Prohibited term '{term}' found in dashboard"


# ---------------------------------------------------------------------------
# Alerts empty state and last monitoring links
# ---------------------------------------------------------------------------


class TestDashboardAlertsEmptyState:
    """Tests for alerts section empty state."""

    def test_dashboard_shows_empty_alerts_state(self):
        """Dashboard without alerts shows 'No hay alertas operativas pendientes.'"""
        # Module with no frequency (Lechuga) and recent monitoring → no alerts
        mod = _mock_module()
        mod.crop_type = "Lechuga"
        mod.monitoring_frequency_days = None
        mon = _mock_monitoring(status="completed", started_at=datetime(2025, 6, 19, 10, 0))

        gh_repo, module_repo, monitoring_repo, log_repo, type_repo, export_repo, metrics_repo, inspection_repo = _setup_mocks(
            greenhouses=[_mock_greenhouse()],
            modules=[mod],
            monitorings_by_module={mod.id: [mon]},
        )

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=gh_repo), \
             patch("app.routes.agricultural_ui.get_module_repository", return_value=module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=monitoring_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=log_repo), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=type_repo), \
             patch("app.routes.agricultural_ui.get_export_package_repository", return_value=export_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_metrics_repository", return_value=metrics_repo), \
             patch("app.routes.agricultural_ui.get_inspection_result_repository", return_value=inspection_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/dashboard")

        assert response.status_code == 200
        assert "No hay alertas operativas pendientes" in response.text


class TestDashboardLastMonitoringLinks:
    """Tests for last monitoring action links."""

    def test_completed_monitoring_has_report_link(self):
        """Completed last monitoring shows 'Ver reporte' link."""
        mod = _mock_module()
        mon = _mock_monitoring(id=42, status="completed", started_at=datetime(2025, 6, 19, 10, 0))

        gh_repo, module_repo, monitoring_repo, log_repo, type_repo, export_repo, metrics_repo, inspection_repo = _setup_mocks(
            greenhouses=[_mock_greenhouse()],
            modules=[mod],
            monitorings_by_module={mod.id: [mon]},
        )

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=gh_repo), \
             patch("app.routes.agricultural_ui.get_module_repository", return_value=module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=monitoring_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=log_repo), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=type_repo), \
             patch("app.routes.agricultural_ui.get_export_package_repository", return_value=export_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_metrics_repository", return_value=metrics_repo), \
             patch("app.routes.agricultural_ui.get_inspection_result_repository", return_value=inspection_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/dashboard")

        assert response.status_code == 200
        assert "/monitoreos/42/reporte" in response.text
        assert "Ver reporte" in response.text

    def test_active_monitoring_has_execution_link(self):
        """Running/analyzing last monitoring shows 'Ver ejecución' link."""
        mod = _mock_module()
        mon = _mock_monitoring(id=55, status="analyzing", started_at=datetime(2025, 6, 19, 14, 0))

        gh_repo, module_repo, monitoring_repo, log_repo, type_repo, export_repo, metrics_repo, inspection_repo = _setup_mocks(
            greenhouses=[_mock_greenhouse()],
            modules=[mod],
            monitorings_by_module={mod.id: [mon]},
        )

        with patch("app.routes.agricultural_ui.get_greenhouse_repository", return_value=gh_repo), \
             patch("app.routes.agricultural_ui.get_module_repository", return_value=module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=monitoring_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=log_repo), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=type_repo), \
             patch("app.routes.agricultural_ui.get_export_package_repository", return_value=export_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_metrics_repository", return_value=metrics_repo), \
             patch("app.routes.agricultural_ui.get_inspection_result_repository", return_value=inspection_repo):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/dashboard")

        assert response.status_code == 200
        assert "/monitoreos/55/ejecucion" in response.text
        assert "Ver ejecución" in response.text
