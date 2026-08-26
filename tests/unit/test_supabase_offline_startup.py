"""Test: application starts and functions without Supabase configuration.

Validates Requirement 24.5: the system starts correctly and login works
when no Supabase environment variables are defined (offline-only mode).

Uses a temporary SQLite database — never touches data/tomato_monitor.db.
"""

import os
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.application.services.auth_service import AuthService, SESSION_COOKIE_NAME


@pytest.fixture
def offline_client(monkeypatch):
    """TestClient with Supabase env cleared and temp SQLite."""
    # Clear Supabase variables
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_STORAGE_BUCKET", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    # Prevent bootstrap side effects
    monkeypatch.delenv("TOMATO_MONITOR_BOOTSTRAP_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("TOMATO_MONITOR_BOOTSTRAP_ADMIN_PASSWORD", raising=False)

    import app.main
    from src.infrastructure.persistence.database import DatabaseManager

    OrigDM = DatabaseManager

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        temp_db = os.path.join(tmpdir, "offline_test.db")

        def TempDM(*args, **kwargs):
            return OrigDM(db_path=temp_db)

        with patch.object(app.main, "DatabaseManager", TempDM):
            with TestClient(app.main.app) as client:
                yield client, app.main.app, temp_db


class TestOfflineStartup:
    """Application starts without Supabase env variables (Requirement 24.5)."""

    def test_app_starts_without_supabase_config(self, offline_client):
        client, app_instance, _ = offline_client
        assert app_instance.state.supabase_config is None

    def test_login_page_accessible(self, offline_client):
        client, _, _ = offline_client
        response = client.get("/login", follow_redirects=False)
        assert response.status_code == 200

    def test_registro_shows_unavailable(self, offline_client):
        client, _, _ = offline_client
        response = client.get("/registro", follow_redirects=False)
        assert response.status_code == 200
        assert "Registro no disponible" in response.text


class TestLocalLoginWithoutSupabase:
    """Login via local PBKDF2 works in offline-only mode (Requirement 24.5)."""

    def test_local_login_creates_session(self, offline_client):
        client, app_instance, temp_db = offline_client

        # Create a real local user in the temp database
        from src.infrastructure.persistence.repositories.sql_user_repository import (
            SqlUserRepository,
        )
        from src.domain.entities.user import User

        db_manager = app_instance.state.db_manager
        session = db_manager.get_session()
        auth = AuthService()
        hashed = auth.hash_password("OfflineTestPass123!")
        repo = SqlUserRepository(session=session)
        repo.create(User(
            full_name="Offline Test User",
            email="offline@example.com",
            password_hash=hashed,
        ))
        session.commit()
        session.close()

        # Login without any Supabase
        response = client.post(
            "/login",
            data={"email": "offline@example.com", "password": "OfflineTestPass123!"},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert "/invernaderos" in response.headers["location"]
        assert SESSION_COOKIE_NAME in response.cookies


# ---------------------------------------------------------------------------
# Extended offline operation tests (Task 16.1)
# ---------------------------------------------------------------------------


@pytest.fixture
def authenticated_offline_client(offline_client):
    """Offline TestClient with a real local user authenticated via cookie."""
    client, app_instance, temp_db = offline_client

    from src.infrastructure.persistence.repositories.sql_user_repository import (
        SqlUserRepository,
    )
    from src.domain.entities.user import User

    db_manager = app_instance.state.db_manager
    session = db_manager.get_session()
    auth = AuthService()
    hashed = auth.hash_password("TestPass123!")
    repo = SqlUserRepository(session=session)
    repo.create(User(
        full_name="Offline Operator",
        email="operator@offline.local",
        password_hash=hashed,
    ))
    session.commit()
    session.close()

    # Login to get session cookie
    response = client.post(
        "/login",
        data={"email": "operator@offline.local", "password": "TestPass123!"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert SESSION_COOKIE_NAME in response.cookies

    yield client, app_instance


class TestOfflineOperation:
    """All core features work without Supabase (Task 16.1)."""

    def test_dashboard_responds_200(self, authenticated_offline_client):
        """Dashboard loads without Supabase."""
        client, _ = authenticated_offline_client
        response = client.get("/dashboard")
        assert response.status_code == 200
        assert "Invernaderos" in response.text

    def test_sync_page_responds_200_local_mode(self, authenticated_offline_client):
        """Sync page shows local mode without Supabase."""
        client, _ = authenticated_offline_client
        response = client.get("/sincronizacion")
        assert response.status_code == 200
        assert "Modo local" in response.text
        assert "Sincronización local" in response.text

    def test_export_page_responds_200(self, authenticated_offline_client):
        """Export page remains available offline."""
        client, _ = authenticated_offline_client
        response = client.get("/exportar")
        assert response.status_code == 200

    def test_monitoring_setup_accessible(self, authenticated_offline_client):
        """Monitoring setup page loads for a valid module (no camera/inference)."""
        client, app_instance = authenticated_offline_client

        # Create greenhouse + module for the monitoring setup route
        from src.infrastructure.persistence.repositories import (
            SqlGreenhouseRepository,
            SqlModuleRepository,
        )
        from src.domain.entities.greenhouse import Greenhouse
        from src.domain.entities.module import Module

        db_manager = app_instance.state.db_manager
        session = db_manager.get_session()
        gh_repo = SqlGreenhouseRepository(session=session)
        mod_repo = SqlModuleRepository(session=session)

        gh = gh_repo.create(Greenhouse(name="Test GH Offline"))
        mod = mod_repo.create(gh.id, Module(
            greenhouse_id=gh.id,
            name="Modulo 1",
            crop_type="Tomate Cherry",
        ))
        session.commit()
        module_id = mod.id
        session.close()

        response = client.get(f"/modulos/{module_id}/monitoreo/nuevo")
        assert response.status_code == 200

    def test_api_sync_status_configured_false(self, authenticated_offline_client):
        """GET /api/sync/status returns supabase_configured=false offline."""
        client, _ = authenticated_offline_client
        response = client.get("/api/sync/status")
        assert response.status_code == 200
        data = response.json()
        assert data["supabase_configured"] is False
        assert data["is_syncing"] is False

    def test_local_login_works_offline(self, authenticated_offline_client):
        """Confirm the fixture itself demonstrates working offline login."""
        client, _ = authenticated_offline_client
        # The fixture already logged in — verify dashboard is accessible
        response = client.get("/dashboard")
        assert response.status_code == 200
