"""Tests for POST /monitoreos/{id}/finalizar-captura endpoint.

Validates:
- Calls finalize_capture(id) exactly once on success
- Never calls abort_session or complete_session
- Redirects to /monitoreos/{id}/ejecucion for all valid outcomes
- Handles MonitoringNotFoundError with redirect to greenhouses
- Handles FinalizationInProgressError idempotently
- Handles InvalidTransitionError gracefully
- Only accepts POST (GET returns 405)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.agricultural_ui import router
from src.domain.exceptions import InvalidTransitionError


@pytest.fixture
def app():
    """Minimal FastAPI app with agricultural router."""
    from types import SimpleNamespace
    from app.dependencies import require_current_user_html

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[require_current_user_html] = lambda: SimpleNamespace(
        id=1, full_name="Test", email="test@test.com", role="operator"
    )
    return application


@pytest.fixture
def client(app):
    """TestClient that does NOT follow redirects."""
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False)


@pytest.fixture(autouse=True)
def _owned_monitoring():
    """Make the ownership check resolve for the authenticated test user.

    These tests validate the finalize-capture delegation, not ownership. The
    route first calls ``_monitoring_owned_by_user`` (module -> greenhouse ->
    owner). We mock that helper to return the requested monitoring so the route
    proceeds to the behavior under test. Ownership itself is covered by
    ``test_route_owner_isolation.py``.
    """
    from types import SimpleNamespace

    def _owned(request, monitoring_id, user):
        return SimpleNamespace(id=monitoring_id, module_id=1, status="analyzing")

    with patch(
        "app.routes.agricultural_ui._monitoring_owned_by_user", side_effect=_owned
    ):
        yield


# ---------------------------------------------------------------------------
# 1. POST success — analyzing
# ---------------------------------------------------------------------------


class TestFinalizeSuccess:
    """Successful finalize_capture returns 303 to execution screen."""

    def test_analyzing_result_redirects_to_ejecucion(self, client):
        mock_service = MagicMock()
        mock_monitoring = MagicMock(status="analyzing", id=7)
        mock_service.finalize_capture.return_value = mock_monitoring

        with patch(
            "app.routes.agricultural_ui.get_monitoring_service",
            return_value=mock_service,
        ):
            response = client.post("/monitoreos/7/finalizar-captura")

        assert response.status_code == 303
        assert response.headers["location"] == "/monitoreos/7/ejecucion"
        mock_service.finalize_capture.assert_called_once_with(7)

    def test_completed_result_redirects_to_ejecucion(self, client):
        mock_service = MagicMock()
        mock_monitoring = MagicMock(status="completed", id=7)
        mock_service.finalize_capture.return_value = mock_monitoring

        with patch(
            "app.routes.agricultural_ui.get_monitoring_service",
            return_value=mock_service,
        ):
            response = client.post("/monitoreos/7/finalizar-captura")

        assert response.status_code == 303
        assert response.headers["location"] == "/monitoreos/7/ejecucion"

    def test_error_result_redirects_to_ejecucion(self, client):
        mock_service = MagicMock()
        mock_monitoring = MagicMock(status="error", id=7)
        mock_service.finalize_capture.return_value = mock_monitoring

        with patch(
            "app.routes.agricultural_ui.get_monitoring_service",
            return_value=mock_service,
        ):
            response = client.post("/monitoreos/7/finalizar-captura")

        assert response.status_code == 303
        assert response.headers["location"] == "/monitoreos/7/ejecucion"


# ---------------------------------------------------------------------------
# 4. MonitoringNotFoundError
# ---------------------------------------------------------------------------


class TestFinalizeNotFound:
    """MonitoringNotFoundError redirects to greenhouses with error."""

    def test_not_found_redirects_to_invernaderos(self, client):
        from src.application.services.monitoring_service import MonitoringNotFoundError

        mock_service = MagicMock()
        mock_service.finalize_capture.side_effect = MonitoringNotFoundError(99)

        with patch(
            "app.routes.agricultural_ui.get_monitoring_service",
            return_value=mock_service,
        ):
            response = client.post("/monitoreos/99/finalizar-captura")

        assert response.status_code == 303
        assert "/invernaderos?error=Monitoreo+no+encontrado" in response.headers["location"]


# ---------------------------------------------------------------------------
# 5. FinalizationInProgressError — idempotent handling
# ---------------------------------------------------------------------------


class TestFinalizeAlreadyInProgress:
    """FinalizationInProgressError redirects to execution (not error)."""

    def test_already_finalizing_redirects_to_ejecucion(self, client):
        from src.application.services.monitoring_service import FinalizationInProgressError

        mock_service = MagicMock()
        mock_service.finalize_capture.side_effect = FinalizationInProgressError(5)

        with patch(
            "app.routes.agricultural_ui.get_monitoring_service",
            return_value=mock_service,
        ):
            response = client.post("/monitoreos/5/finalizar-captura")

        assert response.status_code == 303
        assert response.headers["location"] == "/monitoreos/5/ejecucion"
        # abort_session never called
        mock_service.abort_session.assert_not_called()


# ---------------------------------------------------------------------------
# 6. InvalidTransitionError — monitoring exists
# ---------------------------------------------------------------------------


class TestFinalizeInvalidTransition:
    """InvalidTransitionError handled gracefully."""

    def test_invalid_transition_existing_monitoring_redirects_to_ejecucion(self, client):
        mock_service = MagicMock()
        mock_service.finalize_capture.side_effect = InvalidTransitionError(
            current_state="analyzing",
            target_state="analyzing",
            allowed_transitions=["completed", "error"],
        )

        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = MagicMock(id=3, status="analyzing")

        with patch(
            "app.routes.agricultural_ui.get_monitoring_service",
            return_value=mock_service,
        ), patch(
            "app.routes.agricultural_ui.get_monitoring_repository",
            return_value=mock_repo,
        ):
            response = client.post("/monitoreos/3/finalizar-captura")

        assert response.status_code == 303
        assert response.headers["location"] == "/monitoreos/3/ejecucion"
        mock_service.abort_session.assert_not_called()

    def test_invalid_transition_monitoring_not_found_redirects_to_invernaderos(self, client):
        """InvalidTransitionError + repo returns None → not found redirect."""
        mock_service = MagicMock()
        mock_service.finalize_capture.side_effect = InvalidTransitionError(
            current_state="completed",
            target_state="analyzing",
            allowed_transitions=[],
        )

        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = None

        with patch(
            "app.routes.agricultural_ui.get_monitoring_service",
            return_value=mock_service,
        ), patch(
            "app.routes.agricultural_ui.get_monitoring_repository",
            return_value=mock_repo,
        ):
            response = client.post("/monitoreos/3/finalizar-captura")

        assert response.status_code == 303
        assert "/invernaderos?error=Monitoreo+no+encontrado" in response.headers["location"]


# ---------------------------------------------------------------------------
# 8. GET returns 405
# ---------------------------------------------------------------------------


class TestFinalizeMethodNotAllowed:
    """GET on the finalize endpoint returns 405."""

    def test_get_returns_405(self, client):
        response = client.get("/monitoreos/1/finalizar-captura")
        assert response.status_code == 405


# ---------------------------------------------------------------------------
# 9. Never calls abort_session or complete_session
# ---------------------------------------------------------------------------


class TestFinalizeNeverAbortsOrCompletes:
    """In all scenarios, abort_session and complete_session are never called."""

    def test_success_never_calls_abort_or_complete(self, client):
        mock_service = MagicMock()
        mock_service.finalize_capture.return_value = MagicMock(status="analyzing")

        with patch(
            "app.routes.agricultural_ui.get_monitoring_service",
            return_value=mock_service,
        ):
            client.post("/monitoreos/1/finalizar-captura")

        mock_service.abort_session.assert_not_called()
        mock_service.complete_session.assert_not_called()

    def test_not_found_never_calls_abort_or_complete(self, client):
        from src.application.services.monitoring_service import MonitoringNotFoundError

        mock_service = MagicMock()
        mock_service.finalize_capture.side_effect = MonitoringNotFoundError(1)

        with patch(
            "app.routes.agricultural_ui.get_monitoring_service",
            return_value=mock_service,
        ):
            client.post("/monitoreos/1/finalizar-captura")

        mock_service.abort_session.assert_not_called()
        mock_service.complete_session.assert_not_called()

    def test_invalid_transition_never_calls_abort_or_complete(self, client):
        mock_service = MagicMock()
        mock_service.finalize_capture.side_effect = InvalidTransitionError(
            current_state="error", target_state="analyzing", allowed_transitions=[]
        )
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = MagicMock(id=1)

        with patch(
            "app.routes.agricultural_ui.get_monitoring_service",
            return_value=mock_service,
        ), patch(
            "app.routes.agricultural_ui.get_monitoring_repository",
            return_value=mock_repo,
        ):
            client.post("/monitoreos/1/finalizar-captura")

        mock_service.abort_session.assert_not_called()
        mock_service.complete_session.assert_not_called()
