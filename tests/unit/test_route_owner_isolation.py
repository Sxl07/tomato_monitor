"""Route-level local multiuser isolation tests (Spec 022).

Exercises the REAL ownership guards in app/routes/agricultural_ui.py through
the FastAPI app: user B must not be able to open/edit/delete/operate on
user A's greenhouse / module / monitoring, and B's dashboard/greenhouse list
must not contain A's hierarchy.

The auth dependency is overridden to return user B; A and B (and A's data) are
seeded directly in the app's SQLite DB.
"""

import uuid

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import (
    get_current_user_optional,
    require_current_user_html,
    require_current_user_api,
)
from src.domain.entities.user import User


def _seed_user(session, uid, email):
    from src.infrastructure.persistence.models import UserModel

    existing = session.query(UserModel).filter(UserModel.id == uid).first()
    if existing is None:
        session.add(UserModel(
            id=uid,
            full_name=f"User {uid}",
            email=email,
            password_hash="pbkdf2_sha256$260000$aaaa$bbbb",
            role="operator",
            is_active=True,
        ))
        session.commit()


@pytest.fixture
def isolation_ctx():
    """Seed users A(1) and B(2) + A's greenhouse/module/monitoring.

    Returns a dict of ids and a TestClient authenticated AS USER B.
    """
    from src.infrastructure.persistence.models.greenhouse_model import GreenhouseModel
    from src.infrastructure.persistence.models.module_model import ModuleModel
    from src.infrastructure.persistence.models.monitoring_model import MonitoringModel

    tag = uuid.uuid4().hex[:8]
    user_b = User(
        id=2, full_name="User B", email=f"b_{tag}@example.com",
        password_hash="x", role="operator",
    )

    async def _as_b(request: Request):
        return user_b

    app.dependency_overrides[get_current_user_optional] = _as_b
    app.dependency_overrides[require_current_user_html] = _as_b
    app.dependency_overrides[require_current_user_api] = _as_b

    # Enter the client first so the app lifespan initializes app.state.db_manager.
    with TestClient(app) as client:
        app.state.supabase_config = None
        db_manager = app.state.db_manager
        session = db_manager.get_session()
        try:
            _seed_user(session, 1, f"a_{tag}@example.com")
            _seed_user(session, 2, f"b_{tag}@example.com")

            gh_a = GreenhouseModel(owner_user_id=1, name=f"A-{tag}")
            session.add(gh_a)
            session.flush()
            mod_a = ModuleModel(greenhouse_id=gh_a.id, name=f"ModA-{tag}")
            session.add(mod_a)
            session.flush()
            mon_a = MonitoringModel(
                module_id=mod_a.id, status="completed",
                width_m=1.0, length_m=1.0,
            )
            session.add(mon_a)
            session.flush()

            # B's own greenhouse (to verify B sees only its own).
            gh_b = GreenhouseModel(owner_user_id=2, name=f"B-{tag}")
            session.add(gh_b)
            session.flush()

            ids = {
                "gh_a": gh_a.id, "mod_a": mod_a.id, "mon_a": mon_a.id,
                "gh_b": gh_b.id, "tag": tag,
            }
            session.commit()
        finally:
            session.close()

        yield client, ids

    app.dependency_overrides.pop(get_current_user_optional, None)
    app.dependency_overrides.pop(require_current_user_html, None)
    app.dependency_overrides.pop(require_current_user_api, None)


def _redirects_to_greenhouses(resp):
    """A guard rejection redirects (303) back to the greenhouse list."""
    assert resp.status_code in (302, 303)
    assert "/invernaderos" in resp.headers.get("location", "")


class TestRouteOwnerIsolation:
    def test_b_cannot_open_a_greenhouse(self, isolation_ctx):
        client, ids = isolation_ctx
        resp = client.get(f"/invernaderos/{ids['gh_a']}", follow_redirects=False)
        _redirects_to_greenhouses(resp)

    def test_b_cannot_edit_a_greenhouse(self, isolation_ctx):
        client, ids = isolation_ctx
        resp = client.post(
            f"/invernaderos/{ids['gh_a']}/editar",
            data={"name": "Hacked", "location": ""},
            follow_redirects=False,
        )
        _redirects_to_greenhouses(resp)

    def test_b_cannot_delete_a_greenhouse(self, isolation_ctx):
        client, ids = isolation_ctx
        resp = client.post(
            f"/invernaderos/{ids['gh_a']}/eliminar", follow_redirects=False
        )
        _redirects_to_greenhouses(resp)

    def test_b_cannot_open_a_module(self, isolation_ctx):
        client, ids = isolation_ctx
        resp = client.get(f"/modulos/{ids['mod_a']}", follow_redirects=False)
        _redirects_to_greenhouses(resp)

    def test_b_cannot_delete_a_module(self, isolation_ctx):
        client, ids = isolation_ctx
        resp = client.post(
            f"/modulos/{ids['mod_a']}/eliminar", follow_redirects=False
        )
        _redirects_to_greenhouses(resp)

    def test_b_cannot_create_module_under_a_greenhouse(self, isolation_ctx):
        client, ids = isolation_ctx
        resp = client.post(
            f"/invernaderos/{ids['gh_a']}/modulos/crear",
            data={"name": "X", "crop_type": "Tomate Cherry"},
            follow_redirects=False,
        )
        _redirects_to_greenhouses(resp)

    def test_b_cannot_start_monitoring_under_a_module(self, isolation_ctx):
        client, ids = isolation_ctx
        resp = client.post(
            f"/modulos/{ids['mod_a']}/monitoreo/iniciar",
            data={"width_m": "", "length_m": "", "notes": ""},
            follow_redirects=False,
        )
        _redirects_to_greenhouses(resp)

    def test_b_cannot_open_a_monitoring_report(self, isolation_ctx):
        client, ids = isolation_ctx
        resp = client.get(
            f"/monitoreos/{ids['mon_a']}/reporte", follow_redirects=False
        )
        _redirects_to_greenhouses(resp)

    def test_b_cannot_delete_a_monitoring(self, isolation_ctx):
        client, ids = isolation_ctx
        resp = client.post(
            f"/monitoreos/{ids['mon_a']}/eliminar", follow_redirects=False
        )
        _redirects_to_greenhouses(resp)

    def test_b_cannot_abort_a_monitoring(self, isolation_ctx):
        client, ids = isolation_ctx
        resp = client.post(
            f"/monitoreos/{ids['mon_a']}/abortar", follow_redirects=False
        )
        _redirects_to_greenhouses(resp)

    def test_b_cannot_register_activity_under_a_module(self, isolation_ctx):
        client, ids = isolation_ctx
        resp = client.get(
            f"/modulos/{ids['mod_a']}/actividades/registrar",
            follow_redirects=False,
        )
        _redirects_to_greenhouses(resp)

    def test_b_greenhouse_list_excludes_a(self, isolation_ctx):
        client, ids = isolation_ctx
        resp = client.get("/invernaderos")
        assert resp.status_code == 200
        assert f"A-{ids['tag']}" not in resp.text
        assert f"B-{ids['tag']}" in resp.text

    def test_b_dashboard_excludes_a(self, isolation_ctx):
        client, ids = isolation_ctx
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert f"A-{ids['tag']}" not in resp.text
