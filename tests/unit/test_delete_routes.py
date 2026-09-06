"""Route tests for entity deletion (Spec 021, Task 6.2).

Covers the three destructive UI endpoints added/updated in Task 6.1 in
``app/routes/agricultural_ui.py``:

- POST /monitoreos/{id}/eliminar (monitoring_delete)
- POST /modulos/{id}/eliminar     (module_delete)
- POST /invernaderos/{id}/eliminar (greenhouse_delete)

These routes delegate to ``DeletionService`` and return immediately after the
local delete. Because the routes obtain their collaborators via plain function
calls (``get_deletion_service(request)``, ``get_monitoring_repository(request)``,
``get_module_repository(request)``) rather than FastAPI ``Depends``, those
collaborators are replaced here by patching the names imported into the route
module. Authentication IS a ``Depends`` and is overridden via
``app.dependency_overrides`` following the pattern in ``test_sync_ui.py`` /
``test_sync_api.py``.

Verifies (Req 2.4, 2.5, 2.6, 2.7):
- Delegation: a successful POST calls the fake DeletionService exactly once with
  the correct id and returns a redirect to the correct parent view.
- Safe error / no deletion on backend rejection: when the fake DeletionService
  raises a domain ``DeletionError`` subtype, the route redirects back with a
  Spanish ``?error=`` message and does NOT crash (no 500). The rejected delete
  performs no additional side-effect (the fake did not succeed).
- Confirmation / visible action: the monitoring report GET renders the visible
  "Eliminar" action whose confirmation form posts to the eliminar route.

No real DB writes, no network, no hardware. All collaborators are import fakes.
"""

from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import (
    require_current_user_html,
    require_current_user_api,
    get_current_user_optional,
)
from src.application.services.deletion_service import (
    DeletionError,
    MonitoringStateNotDeletableError,
    MonitoringWorkerActiveError,
    ContainerDescendantNotDeletableError,
    ContainerDescendantWorkerActiveError,
    DeletionOutboxRegistrationError,
    LocalCascadeFailedError,
)


_fake_user = SimpleNamespace(
    id=1, full_name="Test Operator", email="test@test.com", role="operator"
)


@pytest.fixture(autouse=True)
def _auth_overrides():
    """Override auth for all tests (save/restore to avoid leaking overrides)."""
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[require_current_user_html] = lambda: _fake_user
    app.dependency_overrides[require_current_user_api] = lambda: _fake_user
    app.dependency_overrides[get_current_user_optional] = lambda: _fake_user
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeDeletionService:
    """Records delegation and optionally raises a domain DeletionError.

    ``calls`` records ``(method, id)`` tuples so a test can assert the route
    delegated exactly once with the correct id (delegation + immediate return).
    When ``raises`` is set, the corresponding delete method raises it BEFORE
    recording success, mirroring a backend rejection where NO deletion happens.
    """

    def __init__(self, raises: Exception | None = None):
        self.raises = raises
        self.calls: list[tuple[str, int]] = []
        self.succeeded: list[tuple[str, int]] = []

    def _handle(self, method: str, entity_id: int):
        self.calls.append((method, entity_id))
        if self.raises is not None:
            raise self.raises
        self.succeeded.append((method, entity_id))
        return SimpleNamespace(entity_type=method, entity_local_id=entity_id)

    def delete_monitoring(self, monitoring_id: int):
        return self._handle("monitoring", monitoring_id)

    def delete_module(self, module_id: int):
        return self._handle("module", module_id)

    def delete_greenhouse(self, greenhouse_id: int):
        return self._handle("greenhouse", greenhouse_id)


class FakeMonitoringRepo:
    """Returns a monitoring-like object (or None) for get_by_id."""

    def __init__(self, monitoring: object | None):
        self._monitoring = monitoring

    def get_by_id(self, monitoring_id: int):
        return self._monitoring


class FakeModuleRepo:
    """Returns a module-like object (or None) for get_by_id."""

    def __init__(self, module: object | None):
        self._module = module

    def get_by_id(self, module_id: int):
        return self._module


def _fake_monitoring(monitoring_id: int = 10, module_id: int = 5, status: str = "completed"):
    return SimpleNamespace(
        id=monitoring_id,
        module_id=module_id,
        status=status,
        started_at=datetime(2024, 1, 15, 9, 30, 0),
    )


def _fake_module(module_id: int = 5, greenhouse_id: int = 3):
    return SimpleNamespace(id=module_id, greenhouse_id=greenhouse_id, name="Módulo 1")


MODULE = "app.routes.agricultural_ui"


@contextmanager
def _client_no_redirect():
    """TestClient that does NOT auto-follow redirects (so we can assert 303)."""
    with TestClient(app, follow_redirects=False) as client:
        yield client


# ---------------------------------------------------------------------------
# Delegation: successful POST delegates once and redirects to parent view
# ---------------------------------------------------------------------------


class TestMonitoringDeleteDelegation:
    def test_success_delegates_once_and_redirects_to_module(self):
        fake_service = FakeDeletionService()
        monitoring = _fake_monitoring(monitoring_id=10, module_id=5)
        with patch(f"{MODULE}.get_deletion_service", return_value=fake_service), patch(
            f"{MODULE}.get_monitoring_repository",
            return_value=FakeMonitoringRepo(monitoring),
        ):
            with _client_no_redirect() as client:
                resp = client.post("/monitoreos/10/eliminar")

        assert resp.status_code in (302, 303)
        assert resp.headers["location"] == "/modulos/5"
        # Delegation: called exactly once with the correct id, and it succeeded.
        assert fake_service.calls == [("monitoring", 10)]
        assert fake_service.succeeded == [("monitoring", 10)]

    def test_not_found_redirects_safely_without_calling_service(self):
        """Missing monitoring: route redirects safely, service is never called."""
        fake_service = FakeDeletionService()
        with patch(f"{MODULE}.get_deletion_service", return_value=fake_service), patch(
            f"{MODULE}.get_monitoring_repository",
            return_value=FakeMonitoringRepo(None),
        ):
            with _client_no_redirect() as client:
                resp = client.post("/monitoreos/999/eliminar")

        assert resp.status_code in (302, 303)
        assert resp.headers["location"] == "/invernaderos?error=Monitoreo+no+encontrado"
        assert fake_service.calls == []


class TestModuleDeleteDelegation:
    def test_success_delegates_once_and_redirects_to_greenhouse(self):
        fake_service = FakeDeletionService()
        module = _fake_module(module_id=5, greenhouse_id=3)
        with patch(f"{MODULE}.get_deletion_service", return_value=fake_service), patch(
            f"{MODULE}.get_module_repository",
            return_value=FakeModuleRepo(module),
        ):
            with _client_no_redirect() as client:
                resp = client.post("/modulos/5/eliminar")

        assert resp.status_code in (302, 303)
        assert resp.headers["location"] == "/invernaderos/3"
        assert fake_service.calls == [("module", 5)]
        assert fake_service.succeeded == [("module", 5)]

    def test_not_found_redirects_safely_without_calling_service(self):
        fake_service = FakeDeletionService()
        with patch(f"{MODULE}.get_deletion_service", return_value=fake_service), patch(
            f"{MODULE}.get_module_repository",
            return_value=FakeModuleRepo(None),
        ):
            with _client_no_redirect() as client:
                resp = client.post("/modulos/999/eliminar")

        assert resp.status_code in (302, 303)
        # The Location header percent-encodes the non-ASCII "ó" in "Módulo".
        location = resp.headers["location"]
        assert location in (
            "/invernaderos?error=Módulo+no+encontrado",
            "/invernaderos?error=M%C3%B3dulo+no+encontrado",
        )
        assert fake_service.calls == []


class TestGreenhouseDeleteDelegation:
    def test_success_delegates_once_and_redirects_to_greenhouse_list(self):
        fake_service = FakeDeletionService()
        with patch(f"{MODULE}.get_deletion_service", return_value=fake_service):
            with _client_no_redirect() as client:
                resp = client.post("/invernaderos/7/eliminar")

        assert resp.status_code in (302, 303)
        assert resp.headers["location"] == "/invernaderos"
        assert fake_service.calls == [("greenhouse", 7)]
        assert fake_service.succeeded == [("greenhouse", 7)]


# ---------------------------------------------------------------------------
# Safe error: backend rejection redirects back with ?error= and no 500
# ---------------------------------------------------------------------------


class TestMonitoringDeleteRejection:
    @pytest.mark.parametrize(
        "error",
        [
            MonitoringStateNotDeletableError(10, "running"),
            MonitoringWorkerActiveError(10),
            DeletionOutboxRegistrationError("monitoring", 10),
            LocalCascadeFailedError("monitoring", 10),
        ],
    )
    def test_rejection_redirects_back_to_report_with_error(self, error: DeletionError):
        fake_service = FakeDeletionService(raises=error)
        monitoring = _fake_monitoring(monitoring_id=10, module_id=5)
        with patch(f"{MODULE}.get_deletion_service", return_value=fake_service), patch(
            f"{MODULE}.get_monitoring_repository",
            return_value=FakeMonitoringRepo(monitoring),
        ):
            with _client_no_redirect() as client:
                resp = client.post("/monitoreos/10/eliminar")

        # No crash: it is a redirect back to the report, not a 500.
        assert resp.status_code in (302, 303)
        location = resp.headers["location"]
        assert location.startswith("/monitoreos/10/reporte?error=")
        # Service was called but did NOT succeed (no additional side-effect).
        assert fake_service.calls == [("monitoring", 10)]
        assert fake_service.succeeded == []


class TestModuleDeleteRejection:
    @pytest.mark.parametrize(
        "error",
        [
            ContainerDescendantNotDeletableError("module", 5, 10, "running"),
            ContainerDescendantWorkerActiveError("module", 5, 10),
            DeletionOutboxRegistrationError("module", 5),
            LocalCascadeFailedError("module", 5),
        ],
    )
    def test_rejection_redirects_back_to_module_with_error(self, error: DeletionError):
        fake_service = FakeDeletionService(raises=error)
        module = _fake_module(module_id=5, greenhouse_id=3)
        with patch(f"{MODULE}.get_deletion_service", return_value=fake_service), patch(
            f"{MODULE}.get_module_repository",
            return_value=FakeModuleRepo(module),
        ):
            with _client_no_redirect() as client:
                resp = client.post("/modulos/5/eliminar")

        assert resp.status_code in (302, 303)
        location = resp.headers["location"]
        assert location.startswith("/modulos/5?error=")
        assert fake_service.calls == [("module", 5)]
        assert fake_service.succeeded == []


class TestGreenhouseDeleteRejection:
    @pytest.mark.parametrize(
        "error",
        [
            ContainerDescendantNotDeletableError("greenhouse", 7, 10, "analyzing"),
            ContainerDescendantWorkerActiveError("greenhouse", 7, 10),
            DeletionOutboxRegistrationError("greenhouse", 7),
            LocalCascadeFailedError("greenhouse", 7),
        ],
    )
    def test_rejection_redirects_back_to_greenhouse_with_error(self, error: DeletionError):
        fake_service = FakeDeletionService(raises=error)
        with patch(f"{MODULE}.get_deletion_service", return_value=fake_service):
            with _client_no_redirect() as client:
                resp = client.post("/invernaderos/7/eliminar")

        assert resp.status_code in (302, 303)
        location = resp.headers["location"]
        assert location.startswith("/invernaderos/7?error=")
        assert fake_service.calls == [("greenhouse", 7)]
        assert fake_service.succeeded == []


# ---------------------------------------------------------------------------
# Confirmation / visible action (Req 2.5, 2.6): the report GET renders the
# visible "Eliminar" action whose confirmation form posts to the eliminar route.
# ---------------------------------------------------------------------------


class TestVisibleDeleteAction:
    def test_report_renders_delete_confirmation_form_for_deletable_monitoring(self):
        """A deletable monitoring shows the 'Eliminar' action + confirmation form.

        Lightweight approach: render the report GET with fake repositories so no
        heavy dependency is loaded, then assert the confirmation form posts to
        the eliminar route and the visible action text is present.
        """
        monitoring = _fake_monitoring(monitoring_id=10, module_id=5, status="completed")
        module = _fake_module(module_id=5, greenhouse_id=3)

        empty_metrics_repo = SimpleNamespace(get_by_monitoring=lambda _id: None)
        empty_snapshot_repo = SimpleNamespace(get_by_monitoring=lambda _id: [])

        with patch(
            f"{MODULE}.get_monitoring_repository",
            return_value=FakeMonitoringRepo(monitoring),
        ), patch(
            f"{MODULE}.get_module_repository", return_value=FakeModuleRepo(module)
        ), patch(
            f"{MODULE}.get_monitoring_metrics_repository", return_value=empty_metrics_repo
        ), patch(
            f"{MODULE}.get_snapshot_repository", return_value=empty_snapshot_repo
        ):
            with TestClient(app) as client:
                resp = client.get("/monitoreos/10/reporte")

        assert resp.status_code == 200
        # Confirmation form posts to the eliminar route (no manual URL needed).
        assert 'action="/monitoreos/10/eliminar"' in resp.text
        # Visible "Eliminar" action present.
        assert "Eliminar monitoreo" in resp.text
        # Explicit confirmation is required before invoking the delete.
        assert "showConfirmDialog" in resp.text

    def test_report_hides_delete_action_for_non_deletable_monitoring(self):
        """A monitoring in a prohibited state does not show the delete form."""
        monitoring = _fake_monitoring(monitoring_id=11, module_id=5, status="running")
        module = _fake_module(module_id=5, greenhouse_id=3)

        empty_metrics_repo = SimpleNamespace(get_by_monitoring=lambda _id: None)
        empty_snapshot_repo = SimpleNamespace(get_by_monitoring=lambda _id: [])

        with patch(
            f"{MODULE}.get_monitoring_repository",
            return_value=FakeMonitoringRepo(monitoring),
        ), patch(
            f"{MODULE}.get_module_repository", return_value=FakeModuleRepo(module)
        ), patch(
            f"{MODULE}.get_monitoring_metrics_repository", return_value=empty_metrics_repo
        ), patch(
            f"{MODULE}.get_snapshot_repository", return_value=empty_snapshot_repo
        ):
            with TestClient(app) as client:
                resp = client.get("/monitoreos/11/reporte")

        assert resp.status_code == 200
        assert 'action="/monitoreos/11/eliminar"' not in resp.text
