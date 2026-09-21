"""Spec 020, Task 6.4 — POST /monitoreos/{id}/iniciar-analisis route tests.

Verifies:
    - the confirmation field is OPTIONAL (Form(False)): a POST WITHOUT the field
      does NOT return 422; instead the app rejects it (controlled Spanish error)
      and redirects (303) to the execution screen keeping ready_for_analysis;
    - with power_source_confirmed=true and an accepted start, redirects (303) to
      the execution screen;
    - a preflight rejection redirects (303) with an actionable ?error= (Spanish);
    - the confirmation is per-request (no persistence): the route simply forwards
      the flag to the service each time.

No real camera/DB/inference: the MonitoringService is mocked; auth is overridden.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.agricultural_ui import router
from app.dependencies import require_current_user_html
from src.application.services.monitoring_service import (
    PowerSourceNotConfirmedError,
    AnalysisPreflightFailedError,
)
from src.domain.value_objects.monitoring_status import MonitoringState


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_current_user_html] = lambda: SimpleNamespace(
        id=1, full_name="Op", email="op@test.com", role="operator"
    )
    mock_db_manager = MagicMock()
    mock_db_manager.get_session.return_value = MagicMock()
    app.state.db_manager = mock_db_manager
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _owned_monitoring():
    """Resolve ownership for the authenticated test user.

    These tests validate the iniciar-analisis delegation, not ownership. The
    route first calls ``_monitoring_owned_by_user`` (module -> greenhouse ->
    owner); mock it to return the requested monitoring so the route proceeds to
    the behavior under test. Ownership is covered by test_route_owner_isolation.
    """
    def _owned(request, monitoring_id, user):
        return SimpleNamespace(id=monitoring_id, module_id=1, status="ready_for_analysis")

    with patch(
        "app.routes.agricultural_ui._monitoring_owned_by_user", side_effect=_owned
    ):
        yield


def test_missing_confirmation_field_is_not_422(client):
    """Absent field must NOT trigger FastAPI 422; the app handles it (303 redirect)."""
    svc = MagicMock()
    svc.start_deferred_analysis.side_effect = PowerSourceNotConfirmedError()
    with patch("app.routes.agricultural_ui.get_monitoring_service", return_value=svc):
        # POST with NO form fields at all.
        resp = client.post("/monitoreos/7/iniciar-analisis", follow_redirects=False)
    assert resp.status_code == 303
    # The service was called with power_source_confirmed=False (default).
    args, kwargs = svc.start_deferred_analysis.call_args
    assert args[0] == 7
    assert args[1] is False
    # Redirect carries an actionable Spanish error.
    location = unquote(resp.headers["location"])
    assert "/monitoreos/7/ejecucion" in location
    assert "fuente de energía" in location


def test_confirmed_true_accepted_redirects_to_execution(client):
    svc = MagicMock()
    svc.start_deferred_analysis.return_value = SimpleNamespace(
        id=7, status=MonitoringState.ANALYZING.value
    )
    with patch("app.routes.agricultural_ui.get_monitoring_service", return_value=svc):
        resp = client.post(
            "/monitoreos/7/iniciar-analisis",
            data={"power_source_confirmed": "true"},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/monitoreos/7/ejecucion"
    args, _ = svc.start_deferred_analysis.call_args
    assert args[0] == 7 and args[1] is True


def test_preflight_failure_redirects_with_actionable_error(client):
    svc = MagicMock()
    svc.start_deferred_analysis.side_effect = AnalysisPreflightFailedError(
        "temperature_high",
        "Temperatura alta (85.0°C). Espera a que el dispositivo se enfríe "
        "antes de iniciar el análisis.",
    )
    with patch("app.routes.agricultural_ui.get_monitoring_service", return_value=svc):
        resp = client.post(
            "/monitoreos/7/iniciar-analisis",
            data={"power_source_confirmed": "true"},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    location = unquote(resp.headers["location"])
    assert "/monitoreos/7/ejecucion" in location
    assert "Temperatura alta" in location


def test_confirmation_is_per_request_not_persisted(client):
    """Each POST forwards its own flag; the route holds no state between calls."""
    svc = MagicMock()
    svc.start_deferred_analysis.side_effect = PowerSourceNotConfirmedError()
    with patch("app.routes.agricultural_ui.get_monitoring_service", return_value=svc):
        client.post("/monitoreos/7/iniciar-analisis", follow_redirects=False)
        # Second call WITH confirmation must forward True independently.
        svc.start_deferred_analysis.side_effect = None
        svc.start_deferred_analysis.return_value = SimpleNamespace(
            id=7, status=MonitoringState.ANALYZING.value
        )
        client.post(
            "/monitoreos/7/iniciar-analisis",
            data={"power_source_confirmed": "true"},
            follow_redirects=False,
        )
    calls = svc.start_deferred_analysis.call_args_list
    assert calls[0][0][1] is False
    assert calls[1][0][1] is True
