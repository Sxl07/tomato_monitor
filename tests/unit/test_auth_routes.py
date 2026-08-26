"""Unit tests for authentication routes (login, logout)."""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import get_current_user_optional
from src.application.services.auth_service import AuthService, SESSION_COOKIE_NAME
from src.application.services.hybrid_auth_service import LoginResult
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


def _mock_hybrid_service(login_result: LoginResult):
    """Create a MagicMock HybridAuthService that returns the given LoginResult."""
    mock_svc = MagicMock()
    mock_svc.login.return_value = login_result
    return mock_svc


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
        mock_svc = _mock_hybrid_service(LoginResult(
            success=False,
            auth_method="none",
            error_message="Credenciales incorrectas. Verifica tu email y contraseña.",
        ))
        with patch("app.routes.auth.get_hybrid_auth_service", return_value=mock_svc):
            response = client.post(
                "/login",
                data={"email": "nobody@example.com", "password": "wrong"},
                follow_redirects=False,
            )
        assert response.status_code == 401
        assert "Credenciales incorrectas" in response.text
        mock_svc.login.assert_called_once_with("nobody@example.com", "wrong")

    def test_successful_login_sets_cookie_and_redirects(self, client):
        """Successful login sets session cookie and redirects."""
        mock_user = MagicMock()
        mock_user.id = 1

        mock_svc = _mock_hybrid_service(LoginResult(
            success=True,
            user=mock_user,
            auth_method="local",
        ))
        with patch("app.routes.auth.get_hybrid_auth_service", return_value=mock_svc):
            response = client.post(
                "/login",
                data={"email": "logintest@example.com", "password": "testpass123"},
                follow_redirects=False,
            )

        assert response.status_code == 302
        assert "/invernaderos" in response.headers["location"]
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
        mock_user = MagicMock()
        mock_user.id = 1

        mock_svc = _mock_hybrid_service(LoginResult(
            success=True,
            user=mock_user,
            auth_method="local",
        ))
        with patch("app.routes.auth.get_hybrid_auth_service", return_value=mock_svc):
            response = client.post(
                "/login",
                data={"email": "test@example.com", "password": "testpass123", "next": "/modulos/5"},
                follow_redirects=False,
            )

        assert response.status_code == 302
        assert "/modulos/5" in response.headers["location"]

    def test_open_redirect_blocked_in_next(self, client):
        """Open redirect blocked (next=//evil.com → redirects to /invernaderos)."""
        mock_user = MagicMock()
        mock_user.id = 1

        mock_svc = _mock_hybrid_service(LoginResult(
            success=True,
            user=mock_user,
            auth_method="local",
        ))
        with patch("app.routes.auth.get_hybrid_auth_service", return_value=mock_svc):
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


# ===========================================================================
# Task 8.2 — Register route tests
# ===========================================================================


class TestGetRegistroConfigured:
    """GET /registro with Supabase configured shows form."""

    def test_renders_form(self, client):
        with patch("app.routes.auth.get_supabase_config", return_value="config-object"):
            response = client.get("/registro", follow_redirects=False)
        assert response.status_code == 200
        assert "Crear cuenta" in response.text
        assert 'action="/registro"' in response.text
        assert 'name="full_name"' in response.text
        assert 'name="email"' in response.text
        assert 'name="password"' in response.text
        assert 'name="confirm_password"' in response.text


class TestGetRegistroNoConfig:
    """GET /registro without Supabase config shows unavailable."""

    def test_shows_unavailable(self, client):
        with patch("app.routes.auth.get_supabase_config", return_value=None):
            response = client.get("/registro", follow_redirects=False)
        assert response.status_code == 200
        assert "Registro no disponible" in response.text
        assert 'action="/registro"' not in response.text


class TestLoginRegistrationLink:
    """GET /login shows/hides registration link based on config."""

    def test_link_shown_when_configured(self, client):
        with patch("app.routes.auth.get_supabase_config", return_value="config-object"):
            response = client.get("/login", follow_redirects=False)
        assert response.status_code == 200
        assert "/registro" in response.text

    def test_link_hidden_when_not_configured(self, client):
        with patch("app.routes.auth.get_supabase_config", return_value=None):
            response = client.get("/login", follow_redirects=False)
        assert response.status_code == 200
        assert "Crear cuenta" not in response.text


class TestPostRegistroPasswordMismatch:
    """POST /registro with mismatched passwords rejects without calling service."""

    def test_password_mismatch(self, client):
        mock_svc = MagicMock()
        with patch("app.routes.auth.get_supabase_config", return_value="config"):
            with patch("app.routes.auth.get_hybrid_auth_service", return_value=mock_svc):
                response = client.post(
                    "/registro",
                    data={
                        "full_name": "Test User",
                        "email": "test@example.com",
                        "password": "abc123",
                        "confirm_password": "xyz789",
                    },
                    follow_redirects=False,
                )
        assert response.status_code == 400
        assert "no coinciden" in response.text
        mock_svc.register.assert_not_called()
        # Name/email preserved, passwords not
        assert 'value="Test User"' in response.text
        assert 'value="test@example.com"' in response.text


class TestPostRegistroNoConfig:
    """POST /registro without config rejects."""

    def test_rejects(self, client):
        with patch("app.routes.auth.get_supabase_config", return_value=None):
            response = client.post(
                "/registro",
                data={
                    "full_name": "Test",
                    "email": "t@t.com",
                    "password": "p",
                    "confirm_password": "p",
                },
                follow_redirects=False,
            )
        assert response.status_code == 400
        assert "no disponible" in response.text
        assert SESSION_COOKIE_NAME not in response.cookies


class TestPostRegistroSuccess:
    """POST /registro success creates cookie and shows message."""

    def test_success(self, client):
        mock_user = MagicMock()
        mock_user.id = 123
        mock_user.remote_user_id = "uuid-remote"

        mock_svc = MagicMock()
        mock_svc.register.return_value = LoginResult(
            success=True,
            user=mock_user,
            auth_method="remote",
        )

        mock_auth = MagicMock()
        mock_auth.create_session_token.return_value = "test-token"

        with patch("app.routes.auth.get_supabase_config", return_value="config"):
            with patch("app.routes.auth.get_hybrid_auth_service", return_value=mock_svc):
                with patch("app.routes.auth.get_auth_service", return_value=mock_auth):
                    response = client.post(
                        "/registro",
                        data={
                            "full_name": "New User",
                            "email": "new@example.com",
                            "password": "StrongPass123!",
                            "confirm_password": "StrongPass123!",
                        },
                        follow_redirects=False,
                    )

        assert response.status_code == 200
        assert "Tu cuenta fue creada" in response.text
        assert "sin Internet" in response.text
        assert SESSION_COOKIE_NAME in response.cookies
        assert "/invernaderos" in response.text  # Continuar link
        assert 'action="/registro"' not in response.text  # form hidden

        mock_svc.register.assert_called_once_with("new@example.com", "StrongPass123!", "New User")
        mock_auth.create_session_token.assert_called_once_with(123)  # local integer ID


class TestPostRegistroEmailExists:
    """POST /registro EMAIL_EXISTS shows error."""

    def test_email_exists(self, client):
        mock_svc = MagicMock()
        mock_svc.register.return_value = LoginResult(
            success=False,
            auth_method="none",
            error_message="El email ya está registrado.",
        )

        with patch("app.routes.auth.get_supabase_config", return_value="config"):
            with patch("app.routes.auth.get_hybrid_auth_service", return_value=mock_svc):
                response = client.post(
                    "/registro",
                    data={
                        "full_name": "Test",
                        "email": "exists@example.com",
                        "password": "pass123",
                        "confirm_password": "pass123",
                    },
                    follow_redirects=False,
                )

        assert response.status_code == 400
        assert "ya está registrado" in response.text
        assert SESSION_COOKIE_NAME not in response.cookies


class TestPostRegistroConnectivity:
    """POST /registro CONNECTIVITY shows error."""

    def test_connectivity(self, client):
        mock_svc = MagicMock()
        mock_svc.register.return_value = LoginResult(
            success=False,
            auth_method="none",
            error_message="Se requiere conexión a Internet para crear una cuenta.",
            requires_internet=True,
        )

        with patch("app.routes.auth.get_supabase_config", return_value="config"):
            with patch("app.routes.auth.get_hybrid_auth_service", return_value=mock_svc):
                response = client.post(
                    "/registro",
                    data={
                        "full_name": "Test",
                        "email": "t@t.com",
                        "password": "p",
                        "confirm_password": "p",
                    },
                    follow_redirects=False,
                )

        assert response.status_code == 400
        assert "conexión a Internet" in response.text
        assert SESSION_COOKIE_NAME not in response.cookies


class TestPostRegistroSuccessWithoutUser:
    """POST /registro success=True but user=None → safe failure."""

    def test_safe_failure(self, client):
        mock_svc = MagicMock()
        mock_svc.register.return_value = LoginResult(
            success=True,
            user=None,
            auth_method="remote",
        )

        with patch("app.routes.auth.get_supabase_config", return_value="config"):
            with patch("app.routes.auth.get_hybrid_auth_service", return_value=mock_svc):
                response = client.post(
                    "/registro",
                    data={
                        "full_name": "Test",
                        "email": "t@t.com",
                        "password": "p",
                        "confirm_password": "p",
                    },
                    follow_redirects=False,
                )

        assert response.status_code == 400
        assert SESSION_COOKIE_NAME not in response.cookies


class TestPostRegistroPasswordNotReflected:
    """Password sentinel never appears as value in error response."""

    def test_no_password_in_response(self, client):
        mock_svc = MagicMock()
        mock_svc.register.return_value = LoginResult(
            success=False,
            auth_method="none",
            error_message="Some error",
        )

        with patch("app.routes.auth.get_supabase_config", return_value="config"):
            with patch("app.routes.auth.get_hybrid_auth_service", return_value=mock_svc):
                response = client.post(
                    "/registro",
                    data={
                        "full_name": "Test",
                        "email": "t@t.com",
                        "password": "SUPER_SECRET_REGISTER_PASSWORD",
                        "confirm_password": "SUPER_SECRET_REGISTER_PASSWORD",
                    },
                    follow_redirects=False,
                )

        assert 'value="SUPER_SECRET_REGISTER_PASSWORD"' not in response.text
