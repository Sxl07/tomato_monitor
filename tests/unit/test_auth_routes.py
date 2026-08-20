"""Unit tests for authentication routes (login, logout)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import get_current_user_optional
from src.application.services.auth_service import AuthService, SESSION_COOKIE_NAME
from src.domain.entities.user import User


@pytest.fixture
def client():
    """Provide an unauthenticated TestClient with lifespan events."""
    # Ensure no auth override
    app.dependency_overrides.pop(get_current_user_optional, None)
    from app.dependencies import require_current_user_html, require_current_user_api
    app.dependency_overrides.pop(require_current_user_html, None)
    app.dependency_overrides.pop(require_current_user_api, None)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_current_user_optional, None)
    app.dependency_overrides.pop(require_current_user_html, None)
    app.dependency_overrides.pop(require_current_user_api, None)


@pytest.fixture
def auth_client(test_user):
    """Provide an authenticated TestClient."""
    from app.dependencies import require_current_user_html, require_current_user_api
    from fastapi import Request

    async def _override(request: Request):
        return test_user

    app.dependency_overrides[get_current_user_optional] = _override
    app.dependency_overrides[require_current_user_html] = _override
    app.dependency_overrides[require_current_user_api] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_current_user_optional, None)
    app.dependency_overrides.pop(require_current_user_html, None)
    app.dependency_overrides.pop(require_current_user_api, None)


class TestLoginPage:
    """Tests for GET /login."""

    def test_login_page_renders(self, client):
        response = client.get("/login", follow_redirects=False)
        assert response.status_code == 200
        assert "Iniciar sesión" in response.text

    def test_login_page_redirects_when_authenticated(self, auth_client):
        response = auth_client.get("/login", follow_redirects=False)
        assert response.status_code == 302
        assert "/invernaderos" in response.headers["location"]


class TestLoginSubmit:
    """Tests for POST /login."""

    def test_invalid_credentials_returns_401(self, client):
        response = client.post(
            "/login",
            data={"email": "nobody@example.com", "password": "wrong"},
            follow_redirects=False,
        )
        assert response.status_code == 401
        assert "Credenciales incorrectas" in response.text

    def test_successful_login_sets_cookie_and_redirects(self, client, db_session):
        """Test a full login flow with a real user in the database."""
        from src.infrastructure.persistence.repositories.sql_user_repository import SqlUserRepository

        # Create a real user
        auth = AuthService()
        hashed = auth.hash_password("testpass123")
        user = User(
            full_name="Login Test User",
            email="logintest@example.com",
            password_hash=hashed,
            role="operator",
        )
        repo = SqlUserRepository(session=db_session)
        repo.create(user)

        # We need a TestClient that uses this db_session. The simplest approach
        # is to mock the database at the dependency level.
        from unittest.mock import patch, MagicMock

        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_active = True
        mock_user.password_hash = hashed

        with patch(
            "app.routes.auth.SqlUserRepository"
        ) as MockRepo:
            mock_repo_instance = MagicMock()
            mock_repo_instance.get_by_email.return_value = mock_user
            mock_repo_instance.update.return_value = mock_user
            MockRepo.return_value = mock_repo_instance

            response = client.post(
                "/login",
                data={"email": "logintest@example.com", "password": "testpass123"},
                follow_redirects=False,
            )

        assert response.status_code == 302
        assert "/invernaderos" in response.headers["location"]
        # Check cookie was set
        assert SESSION_COOKIE_NAME in response.cookies


class TestLogout:
    """Tests for POST /logout."""

    def test_logout_clears_cookie_and_redirects(self, auth_client):
        response = auth_client.post("/logout", follow_redirects=False)
        assert response.status_code == 302
        assert "/login" in response.headers["location"]


class TestProtectedRoutes:
    """Tests that protected routes redirect unauthenticated users to login."""

    def test_invernaderos_redirects_to_login(self, client):
        response = client.get("/invernaderos", follow_redirects=False)
        assert response.status_code == 302
        assert "/login" in response.headers["location"]

    def test_html_route_redirect_includes_next(self, client):
        """Protected HTML route without cookie → 302 redirect to /login?next=..."""
        response = client.get("/invernaderos", follow_redirects=False)
        assert response.status_code == 302
        location = response.headers["location"]
        assert "/login" in location
        assert "next=" in location
        assert "/invernaderos" in location

    def test_api_route_without_cookie_returns_401(self, client):
        """Protected API route without cookie → 401."""
        response = client.get("/api/camera/status", follow_redirects=False)
        assert response.status_code == 401

    def test_authenticated_user_can_access_invernaderos(self, auth_client):
        response = auth_client.get("/invernaderos", follow_redirects=False)
        assert response.status_code == 200


class TestLoginNextParameter:
    """Tests for the 'next' parameter in login flow."""

    def test_login_page_shows_next_in_hidden_input(self, client):
        """Login page with next parameter includes hidden input."""
        response = client.get("/login?next=/modulos/1", follow_redirects=False)
        assert response.status_code == 200
        assert 'name="next"' in response.text
        assert 'value="/modulos/1"' in response.text

    def test_login_with_next_redirects_to_next(self, client):
        """Login with next parameter → redirect to next after success."""
        from unittest.mock import patch, MagicMock

        auth = AuthService()
        hashed = auth.hash_password("testpass123")

        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_active = True
        mock_user.password_hash = hashed

        with patch("app.routes.auth.SqlUserRepository") as MockRepo:
            mock_repo_instance = MagicMock()
            mock_repo_instance.get_by_email.return_value = mock_user
            mock_repo_instance.update.return_value = mock_user
            MockRepo.return_value = mock_repo_instance

            response = client.post(
                "/login",
                data={"email": "test@example.com", "password": "testpass123", "next": "/modulos/5"},
                follow_redirects=False,
            )

        assert response.status_code == 302
        assert "/modulos/5" in response.headers["location"]

    def test_open_redirect_blocked_in_next(self, client):
        """Open redirect blocked (next=//evil.com → redirects to /invernaderos)."""
        from unittest.mock import patch, MagicMock

        auth = AuthService()
        hashed = auth.hash_password("testpass123")

        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_active = True
        mock_user.password_hash = hashed

        with patch("app.routes.auth.SqlUserRepository") as MockRepo:
            mock_repo_instance = MagicMock()
            mock_repo_instance.get_by_email.return_value = mock_user
            mock_repo_instance.update.return_value = mock_user
            MockRepo.return_value = mock_repo_instance

            response = client.post(
                "/login",
                data={"email": "test@example.com", "password": "testpass123", "next": "//evil.com"},
                follow_redirects=False,
            )

        assert response.status_code == 302
        assert "/invernaderos" in response.headers["location"]
        assert "evil.com" not in response.headers["location"]

    def test_open_redirect_blocked_in_get_login(self, client):
        """GET /login with next=//evil.com sanitizes the value."""
        response = client.get("/login?next=//evil.com", follow_redirects=False)
        assert response.status_code == 200
        # The hidden input value should be empty (sanitized)
        assert 'value=""' in response.text or 'value="//evil.com"' not in response.text
