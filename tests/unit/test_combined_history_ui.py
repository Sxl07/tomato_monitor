"""TestClient integration tests for combined history on module detail.

Validates that the module detail page renders combined history items
and preserves existing navigation buttons.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest


_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class TestTemplateContent:
    """Static template validation for combined history section."""

    @pytest.fixture(scope="class")
    def template(self):
        return (
            _PROJECT_ROOT
            / "app"
            / "templates"
            / "agricultural"
            / "module_detail.html"
        ).read_text("utf-8")

    def test_shows_historial_del_modulo(self, template):
        assert "Historial del módulo" in template

    def test_iterates_combined_history(self, template):
        assert "combined_history" in template

    def test_shows_item_icon(self, template):
        assert "item.icon" in template

    def test_shows_item_title(self, template):
        assert "item.title" in template

    def test_shows_item_subtitle(self, template):
        assert "item.subtitle" in template

    def test_shows_badge(self, template):
        assert "item.badge_class" in template
        assert "item.badge_label" in template

    def test_shows_item_url_conditionally(self, template):
        assert "item.url" in template

    def test_shows_action_label(self, template):
        assert "item.action_label" in template

    def test_empty_state_message(self, template):
        assert "No hay registros históricos para este módulo." in template

    def test_registrar_actividad_button(self, template):
        assert "Registrar actividad" in template

    def test_ver_bitacora_button(self, template):
        assert "Ver bitácora" in template

    def test_iniciar_nuevo_monitoreo_button(self, template):
        assert "Iniciar Nuevo Monitoreo" in template

    def test_confirm_dialog_included(self, template):
        assert "confirm_dialog.html" in template


class TestModuleDetailRoute:
    """TestClient tests for GET /modulos/{id} with combined history."""

    @pytest.fixture(autouse=True)
    def _patch_repos(self):
        """Patch repository functions to return controlled data."""
        from unittest.mock import MagicMock, patch
        from types import SimpleNamespace
        from src.domain.entities.activity_type import ActivityType

        module = SimpleNamespace(
            id=1,
            greenhouse_id=1,
            name="Módulo Test",
            crop_type="Tomate Cherry",
            width_m=5.0,
            length_m=2.0,
            monitoring_frequency_days=7,
        )
        monitoring = SimpleNamespace(
            id=10,
            module_id=1,
            status="completed",
            started_at=datetime(2025, 7, 10, 14, 30),
            total_snapshots=5,
            total_detections=20,
            completed_at=datetime(2025, 7, 10, 15, 0),
        )
        at = ActivityType(code="riego", name="Riego", category="mantenimiento")
        at_obj = SimpleNamespace(id=1, code="riego", name="Riego", category="mantenimiento")

        activity_log = SimpleNamespace(
            id=1,
            module_id=1,
            activity_type_id=1,
            user_id=1,
            occurred_at=datetime(2025, 7, 11, 9, 0),
            product_name=None,
            quantity=None,
            unit=None,
            notes="Riego matutino",
        )

        mock_module_repo = MagicMock()
        mock_module_repo.get_by_id.return_value = module

        mock_monitoring_repo = MagicMock()
        mock_monitoring_repo.get_by_module.return_value = [monitoring]

        mock_metrics_repo = MagicMock()
        mock_metrics_repo.get_by_monitoring.return_value = None

        mock_activity_log_repo = MagicMock()
        mock_activity_log_repo.list_by_module.return_value = [activity_log]

        mock_activity_type_repo = MagicMock()
        mock_activity_type_repo.list_all.return_value = [at_obj]

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=mock_monitoring_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_metrics_repository", return_value=mock_metrics_repo), \
             patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=mock_activity_log_repo), \
             patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=mock_activity_type_repo):
            yield

    def test_page_loads_with_combined_history(self, authenticated_client):
        response = authenticated_client.get("/modulos/1")
        assert response.status_code == 200
        assert "Historial del módulo" in response.text

    def test_shows_monitoring_in_history(self, authenticated_client):
        response = authenticated_client.get("/modulos/1")
        assert "Monitoreo visual" in response.text

    def test_shows_activity_in_history(self, authenticated_client):
        response = authenticated_client.get("/modulos/1")
        assert "Riego" in response.text

    def test_shows_ver_reporte_for_completed(self, authenticated_client):
        response = authenticated_client.get("/modulos/1")
        assert "Ver reporte" in response.text

    def test_preserves_registrar_actividad(self, authenticated_client):
        response = authenticated_client.get("/modulos/1")
        assert "Registrar actividad" in response.text

    def test_preserves_ver_bitacora(self, authenticated_client):
        response = authenticated_client.get("/modulos/1")
        assert "Ver bitácora" in response.text

    def test_preserves_iniciar_monitoreo(self, authenticated_client):
        response = authenticated_client.get("/modulos/1")
        assert "Iniciar Nuevo Monitoreo" in response.text

    def test_auth_required(self):
        """Unauthenticated requests redirect to login."""
        from app.main import app
        from fastapi.testclient import TestClient

        previous_overrides = dict(app.dependency_overrides)
        try:
            app.dependency_overrides.clear()
            with TestClient(app) as client:
                response = client.get("/modulos/1", follow_redirects=False)
                assert response.status_code == 302
                assert "/login" in response.headers.get("location", "")
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous_overrides)


# ---------------------------------------------------------------------------
# Additional combined history validation tests
# ---------------------------------------------------------------------------


class TestCombinedHistoryLinks:
    """Validate specific link rendering for monitoring states."""

    @pytest.fixture(autouse=True)
    def _patch_repos_completed(self):
        from unittest.mock import MagicMock, patch
        from types import SimpleNamespace

        self._module = SimpleNamespace(
            id=1, greenhouse_id=1, name="Módulo Test",
            crop_type="Tomate Cherry", width_m=5.0, length_m=2.0,
            monitoring_frequency_days=7,
        )
        self._mock_module_repo = MagicMock()
        self._mock_module_repo.get_by_id.return_value = self._module
        self._mock_metrics_repo = MagicMock()
        self._mock_metrics_repo.get_by_monitoring.return_value = None
        self._mock_activity_log_repo = MagicMock()
        self._mock_activity_log_repo.list_by_module.return_value = []
        self._mock_activity_type_repo = MagicMock()
        self._mock_activity_type_repo.list_all.return_value = []
        self._patches = [
            patch("app.routes.agricultural_ui.get_module_repository", return_value=self._mock_module_repo),
            patch("app.routes.agricultural_ui.get_monitoring_metrics_repository", return_value=self._mock_metrics_repo),
            patch("app.routes.agricultural_ui.get_activity_log_repository", return_value=self._mock_activity_log_repo),
            patch("app.routes.agricultural_ui.get_activity_type_repository", return_value=self._mock_activity_type_repo),
        ]
        for p in self._patches:
            p.start()
        yield
        for p in self._patches:
            p.stop()

    def test_completed_monitoring_has_report_link(self, authenticated_client):
        """Completed monitoring shows 'Ver reporte' with correct URL."""
        from unittest.mock import MagicMock, patch
        from types import SimpleNamespace

        monitoring = SimpleNamespace(
            id=10, module_id=1, status="completed",
            started_at=datetime(2025, 7, 10, 14, 30),
            total_snapshots=5, total_detections=20,
            completed_at=datetime(2025, 7, 10, 15, 0),
        )
        mock_monitoring_repo = MagicMock()
        mock_monitoring_repo.get_by_module.return_value = [monitoring]

        with patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=mock_monitoring_repo):
            response = authenticated_client.get("/modulos/1")

        assert response.status_code == 200
        assert "Ver reporte" in response.text
        assert "/monitoreos/10/reporte" in response.text

    def test_active_monitoring_has_execution_link(self, authenticated_client):
        """Analyzing monitoring shows 'Ver ejecución' with correct URL."""
        from unittest.mock import MagicMock, patch
        from types import SimpleNamespace

        monitoring = SimpleNamespace(
            id=20, module_id=1, status="analyzing",
            started_at=datetime(2025, 7, 12, 10, 0),
            total_snapshots=8, total_detections=0,
            completed_at=None,
        )
        mock_monitoring_repo = MagicMock()
        mock_monitoring_repo.get_by_module.return_value = [monitoring]

        with patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=mock_monitoring_repo):
            response = authenticated_client.get("/modulos/1")

        assert response.status_code == 200
        assert "Ver ejecución" in response.text
        assert "/monitoreos/20/ejecucion" in response.text

    def test_activity_more_recent_appears_before_monitoring(self, authenticated_client):
        """Activity with more recent occurred_at renders before older monitoring."""
        from unittest.mock import MagicMock, patch
        from types import SimpleNamespace

        monitoring = SimpleNamespace(
            id=10, module_id=1, status="completed",
            started_at=datetime(2025, 7, 5, 14, 0),
            total_snapshots=3, total_detections=10,
            completed_at=datetime(2025, 7, 5, 15, 0),
        )
        activity_log = SimpleNamespace(
            id=1, module_id=1, activity_type_id=1, user_id=1,
            occurred_at=datetime(2025, 7, 8, 9, 0),
            product_name=None, quantity=None, unit=None, notes=None,
        )
        at_obj = SimpleNamespace(id=1, code="riego", name="Riego", category="mantenimiento")

        mock_monitoring_repo = MagicMock()
        mock_monitoring_repo.get_by_module.return_value = [monitoring]
        self._mock_activity_log_repo.list_by_module.return_value = [activity_log]
        self._mock_activity_type_repo.list_all.return_value = [at_obj]

        with patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=mock_monitoring_repo):
            response = authenticated_client.get("/modulos/1")

        assert response.status_code == 200
        # Activity (Riego, July 8) should appear before monitoring (July 5)
        riego_pos = response.text.index("Riego")
        monitoreo_pos = response.text.index("Monitoreo visual")
        assert riego_pos < monitoreo_pos

    def test_empty_combined_history_state(self, authenticated_client):
        """No monitorings and no activities shows empty state."""
        from unittest.mock import MagicMock, patch

        mock_monitoring_repo = MagicMock()
        mock_monitoring_repo.get_by_module.return_value = []
        self._mock_activity_log_repo.list_by_module.return_value = []

        with patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=mock_monitoring_repo):
            response = authenticated_client.get("/modulos/1")

        assert response.status_code == 200
        assert "No hay registros históricos para este módulo." in response.text

    def test_no_export_or_sync_terms_visible(self, authenticated_client):
        """Module detail does not show export/sync functionality text."""
        from unittest.mock import MagicMock, patch

        mock_monitoring_repo = MagicMock()
        mock_monitoring_repo.get_by_module.return_value = []

        with patch("app.routes.agricultural_ui.get_monitoring_repository", return_value=mock_monitoring_repo):
            response = authenticated_client.get("/modulos/1")

        assert response.status_code == 200
        text_lower = response.text.lower()
        assert "exportservice" not in text_lower
        assert "zipfile" not in text_lower
        assert "sincronizar" not in text_lower
        assert "sync remoto" not in text_lower
