"""Handoff route tests for monitoring_start / setup GET (Spec 023, Task 8.3).

Deterministic, isolated tests using TestClient + patches. They assert the
SERVER-SIDE handoff ordering (suspend_for_handoff BEFORE create_frame_source
BEFORE start_session), the handoff-False short-circuit, resume_after_failed_handoff
on failures, and enable_preview on the setup GET. New isolated tests — they do
NOT rely on the pre-existing MagicMock/Python-3.14 route tests.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import agricultural_ui
from app.dependencies import require_current_user_html


def _client_with_manager(manager):
    app = FastAPI()
    app.include_router(agricultural_ui.router)
    app.state.live_preview_manager = manager
    app.state.db_manager = MagicMock()
    app.state.log_service = MagicMock()
    app.dependency_overrides[require_current_user_html] = lambda: SimpleNamespace(id=1)
    # Register the redirect exception handler so _AuthRedirectException (unused
    # here) would not 500; not strictly needed but harmless.
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False)


_SENTINEL = object()


def _common_patches(events, *, module=None, start_session=None,
                     create_frame_source_result=_SENTINEL):
    module = module or SimpleNamespace(id=1, name="M", width_m=None, length_m=None)
    # Default frame source is a MagicMock so .release() works on failure paths.
    fs_result = (
        MagicMock() if create_frame_source_result is _SENTINEL
        else create_frame_source_result
    )

    def _fake_create_fs(**kwargs):
        events.append("create")
        return fs_result

    monitoring_service = MagicMock()
    if start_session is not None:
        monitoring_service.start_session.side_effect = start_session
    else:
        def _ok(**kwargs):
            events.append("start")
            return SimpleNamespace(id=42)
        monitoring_service.start_session.side_effect = _ok

    # create_frame_source and get_log_service are LOCAL imports inside
    # monitoring_start, so patch them at their source modules.
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        with patch.multiple(
            "app.routes.agricultural_ui",
            _module_owned_by_user=MagicMock(return_value=module),
            get_module_repository=MagicMock(return_value=MagicMock()),
            get_monitoring_service=MagicMock(return_value=monitoring_service),
            ModelService=MagicMock(return_value=MagicMock(
                check_availability=MagicMock(
                    return_value=SimpleNamespace(value="available")
                )
            )),
            validate_optional_dimensions=MagicMock(return_value=(None, None)),
            validate_notes=MagicMock(return_value=""),
        ), patch(
            "src.application.services.frame_source_factory.create_frame_source",
            side_effect=_fake_create_fs,
        ), patch(
            "app.dependencies.get_log_service", return_value=MagicMock()
        ), patch(
            "app.dependencies._get_request_session", return_value=MagicMock()
        ):
            yield

    return _ctx(), monitoring_service


class _Manager:
    def __init__(self, *, suspend_result=True):
        self.events = None
        self._suspend_result = suspend_result
        self.resumed = 0
        self.enabled = 0

    def bind(self, events):
        self.events = events

    def suspend_for_handoff(self, timeout=5.0):
        if self.events is not None:
            self.events.append("suspend")
        return self._suspend_result

    def resume_after_failed_handoff(self):
        self.resumed += 1

    def enable_preview(self):
        self.enabled += 1


class TestHandoffOrdering:
    def test_suspend_before_create_before_start(self):
        events = []
        manager = _Manager(suspend_result=True)
        manager.bind(events)
        client = _client_with_manager(manager)
        patches, _svc = _common_patches(events)
        with patches:
            resp = client.post(
                "/modulos/1/monitoreo/iniciar",
                data={"width_m": "", "length_m": "", "notes": ""},
            )
        # Redirect to execution on success.
        assert resp.status_code == 303
        assert events == ["suspend", "create", "start"]

    def test_handoff_false_short_circuits(self):
        events = []
        manager = _Manager(suspend_result=False)
        manager.bind(events)
        client = _client_with_manager(manager)
        patches, svc = _common_patches(events)
        with patches:
            resp = client.post(
                "/modulos/1/monitoreo/iniciar",
                data={"width_m": "", "length_m": "", "notes": ""},
            )
        # No camera created, no session started.
        assert "create" not in events
        assert not svc.start_session.called
        assert resp.status_code == 200  # re-rendered setup with error

    def test_frame_source_none_resumes_preview(self):
        events = []
        manager = _Manager(suspend_result=True)
        manager.bind(events)
        client = _client_with_manager(manager)
        patches, svc = _common_patches(
            events, create_frame_source_result=None
        )
        with patches:
            resp = client.post(
                "/modulos/1/monitoreo/iniciar",
                data={"width_m": "", "length_m": "", "notes": ""},
            )
        assert manager.resumed == 1  # A.2: resume when frame source is None
        assert not svc.start_session.called
        assert resp.status_code == 200

    def test_start_failure_resumes_preview(self):
        events = []
        manager = _Manager(suspend_result=True)
        manager.bind(events)
        client = _client_with_manager(manager)

        def _boom(**kwargs):
            raise RuntimeError("start failed")

        patches, svc = _common_patches(events, start_session=_boom)
        with patches:
            resp = client.post(
                "/modulos/1/monitoreo/iniciar",
                data={"width_m": "", "length_m": "", "notes": ""},
            )
        assert manager.resumed == 1
        assert resp.status_code == 200


class TestSetupGetEnablesPreview:
    def test_setup_get_calls_enable_preview(self):
        manager = _Manager()
        client = _client_with_manager(manager)
        module = SimpleNamespace(id=1, name="M", width_m=None, length_m=None)
        with patch(
            "app.routes.agricultural_ui._module_owned_by_user", return_value=module
        ), patch(
            "app.routes.agricultural_ui.ModelService",
            return_value=MagicMock(check_availability=MagicMock(
                return_value=SimpleNamespace(value="available")
            )),
        ):
            resp = client.get("/modulos/1/monitoreo/nuevo")
        assert resp.status_code == 200
        assert manager.enabled == 1
