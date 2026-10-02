"""Unit tests for sync routes (GET /sincronizacion, POST /sincronizacion/local)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _seed_test_user(authenticated_client):
    """Ensure user with id=1 exists in DB for FK constraints."""
    from app.main import app

    db_manager = app.state.db_manager
    session = db_manager.get_session()
    try:
        from src.infrastructure.persistence.models import UserModel
        existing = session.query(UserModel).filter(UserModel.id == 1).first()
        if existing is None:
            user_model = UserModel(
                id=1,
                full_name="Test Operator",
                email="test@example.com",
                password_hash="pbkdf2_sha256$260000$aaaa$bbbb",
                role="operator",
                is_active=True,
            )
            session.add(user_model)
            session.commit()
        else:
            session.close()
    except Exception:
        session.rollback()
        session.close()


class TestSyncStatusPage:
    """Tests for GET /sincronizacion."""

    def test_sync_page_returns_200(self, authenticated_client):
        """GET /sincronizacion returns 200 with sync status page."""
        response = authenticated_client.get("/sincronizacion")
        assert response.status_code == 200


    def test_sync_page_shows_counts(self, authenticated_client):
        """Sync page shows pending/exported/synced counts."""
        response = authenticated_client.get("/sincronizacion")
        assert response.status_code == 200
        assert "Pendientes" in response.text
        assert "Exportados" in response.text
        assert "Sincronizados" in response.text

    def test_local_zip_counts_follow_current_user_hierarchy(
        self, authenticated_client, monkeypatch
    ):
        """Only the current user's monitoring and activity reach ZIP status."""
        from app.routes import agricultural_ui

        greenhouse_repo = MagicMock()
        greenhouse_repo.get_all_by_owner.return_value = [SimpleNamespace(id=10)]
        module_repo = MagicMock()
        module_repo.get_by_greenhouse.return_value = [SimpleNamespace(id=20)]
        monitoring_repo = MagicMock()
        monitoring_repo.get_by_module.return_value = [
            SimpleNamespace(sync_status="pending")
        ]
        activity_repo = MagicMock()
        activity_repo.list_by_module.return_value = [
            SimpleNamespace(sync_status="exported")
        ]

        monkeypatch.setattr(
            agricultural_ui, "get_greenhouse_repository", lambda request: greenhouse_repo
        )
        monkeypatch.setattr(
            agricultural_ui, "get_module_repository", lambda request: module_repo
        )
        monkeypatch.setattr(
            agricultural_ui, "get_monitoring_repository", lambda request: monitoring_repo
        )
        monkeypatch.setattr(
            agricultural_ui, "get_activity_log_repository", lambda request: activity_repo
        )

        response = authenticated_client.get("/sincronizacion")

        assert response.status_code == 200
        assert "Monitoreos:</strong> 1 pendiente(s), 0 exportado(s)" in response.text
        assert "Actividades:</strong> 0 pendiente(s), 1 exportado(s)" in response.text
        greenhouse_repo.get_all_by_owner.assert_called_once_with(1)
        module_repo.get_by_greenhouse.assert_called_once_with(10)
        monitoring_repo.get_by_module.assert_called_once_with(20)
        activity_repo.list_by_module.assert_called_once_with(20)
        monitoring_repo.list_all.assert_not_called()
        activity_repo.list_all.assert_not_called()

    def test_sync_page_shows_local_explanation(self, authenticated_client):
        """Sync page explains this is local ZIP sync."""
        response = authenticated_client.get("/sincronizacion")
        assert response.status_code == 200
        assert "paquete ZIP local" in response.text

    def test_sync_page_requires_auth(self):
        """GET /sincronizacion redirects to login without auth."""
        from app.main import app
        from fastapi.testclient import TestClient

        saved = dict(app.dependency_overrides)
        app.dependency_overrides.clear()
        try:
            with TestClient(app) as client:
                response = client.get("/sincronizacion", follow_redirects=False)
                assert response.status_code == 302
                assert "/login" in response.headers.get("location", "")
        finally:
            app.dependency_overrides.update(saved)

    def test_sync_page_no_cloud_terms(self, authenticated_client, monkeypatch):
        """Sync page does not mention cloud providers when Supabase is not configured."""
        from app.main import app
        # Ensure Supabase is not configured for this test
        app.state.supabase_config = None
        response = authenticated_client.get("/sincronizacion")
        text_lower = response.text.lower()
        cloud_terms = ["google drive", "oauth", "amazon s3", "supabase", "dropbox"]
        for term in cloud_terms:
            assert term not in text_lower, f"Found cloud term '{term}' in sync page"


class TestSyncLocalTrigger:
    """Tests for POST /sincronizacion/local."""

    def test_post_redirects_when_no_pending(self, authenticated_client):
        """POST /sincronizacion/local redirects with error when no pending records."""
        response = authenticated_client.post(
            "/sincronizacion/local", follow_redirects=False
        )
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "/sincronizacion" in location

    def test_post_requires_auth(self):
        """POST /sincronizacion/local redirects to login without auth."""
        from app.main import app
        from fastapi.testclient import TestClient

        saved = dict(app.dependency_overrides)
        app.dependency_overrides.clear()
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/sincronizacion/local", follow_redirects=False
                )
                assert response.status_code == 302
                assert "/login" in response.headers.get("location", "")
        finally:
            app.dependency_overrides.update(saved)

    def test_post_with_pending_records_triggers_export(self, authenticated_client):
        """POST /sincronizacion/local with pending records creates export and redirects."""
        from app.main import app
        from src.infrastructure.persistence.models import (
            GreenhouseModel,
            ModuleModel,
            MonitoringModel,
        )
        from datetime import datetime, timezone
        import uuid

        db_manager = app.state.db_manager
        session = db_manager.get_session()
        try:
            # Create greenhouse and module with unique names. The greenhouse must
            # be owned by the authenticated user (test_user.id == 1); otherwise
            # get_all_by_owner(user.id) correctly excludes it and there are no
            # pending records to sync.
            gh_name = f"SyncTest GH {uuid.uuid4().hex[:8]}"
            gh = GreenhouseModel(name=gh_name, location="Test", owner_user_id=1)
            session.add(gh)
            session.flush()

            mod = ModuleModel(
                greenhouse_id=gh.id,
                name="Module 1",
                crop_type="Tomate Cherry",
            )
            session.add(mod)
            session.flush()

            # Create a monitoring with pending sync_status
            monitoring = MonitoringModel(
                module_id=mod.id,
                status="completed",
                started_at=datetime.now(timezone.utc),
                width_m=2.0,
                length_m=5.0,
                sync_status="pending",
            )
            session.add(monitoring)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        # Trigger sync
        response = authenticated_client.post(
            "/sincronizacion/local", follow_redirects=False
        )
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "/sincronizacion" in location
        # Success redirect uses ?success= parameter; error uses ?error=
        assert "success=" in location, (
            f"Expected success redirect, got: {location}"
        )


class TestDashboardSyncLink:
    """Test that dashboard has sync quick link."""

    def test_dashboard_has_sync_link(self, authenticated_client):
        """Dashboard quick links section includes sync link."""
        response = authenticated_client.get("/dashboard")
        assert response.status_code == 200
        assert "/sincronizacion" in response.text
