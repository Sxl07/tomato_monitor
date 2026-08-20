"""Unit tests for AuthService: password hashing, verification, and session tokens."""

import time
from unittest.mock import MagicMock, patch

import pytest

from src.application.services.auth_service import AuthService


class TestPasswordHashing:
    """Tests for hash_password and verify_password."""

    def test_hash_produces_valid_format(self):
        auth = AuthService()
        hashed = auth.hash_password("mypassword123")
        parts = hashed.split("$")
        assert len(parts) == 4
        assert parts[0] == "pbkdf2_sha256"
        assert int(parts[1]) == 260000
        assert len(parts[2]) == 32  # 16-byte salt as hex
        assert len(parts[3]) == 64  # sha256 output as hex

    def test_hash_is_unique_per_call(self):
        auth = AuthService()
        h1 = auth.hash_password("samepassword")
        h2 = auth.hash_password("samepassword")
        # Different salts → different hashes
        assert h1 != h2

    def test_verify_correct_password(self):
        auth = AuthService()
        hashed = auth.hash_password("correcthorse")
        assert auth.verify_password("correcthorse", hashed) is True

    def test_verify_wrong_password(self):
        auth = AuthService()
        hashed = auth.hash_password("correcthorse")
        assert auth.verify_password("wrongpassword", hashed) is False

    def test_verify_empty_password_returns_false(self):
        auth = AuthService()
        hashed = auth.hash_password("something")
        assert auth.verify_password("", hashed) is False

    def test_verify_empty_hash_returns_false(self):
        auth = AuthService()
        assert auth.verify_password("something", "") is False

    def test_verify_malformed_hash_returns_false(self):
        auth = AuthService()
        assert auth.verify_password("pass", "not_a_valid_hash") is False
        assert auth.verify_password("pass", "a$b$c") is False
        assert auth.verify_password("pass", "wrong_algo$260000$salt$hash") is False

    def test_hash_empty_password_raises(self):
        auth = AuthService()
        with pytest.raises(ValueError, match="password must not be empty"):
            auth.hash_password("")


class TestSessionTokens:
    """Tests for create_session_token and verify_session_token."""

    def test_create_and_verify_token(self):
        auth = AuthService()
        token = auth.create_session_token(user_id=42)
        assert "." in token
        user_id = auth.verify_session_token(token)
        assert user_id == 42

    def test_expired_token_returns_none(self):
        auth = AuthService()
        # Patch time to create an already-expired token
        with patch("src.application.services.auth_service.time.time", return_value=1000):
            token = auth.create_session_token(user_id=7)

        # Now verify with current time (way past expiration)
        result = auth.verify_session_token(token)
        assert result is None

    def test_tampered_token_returns_none(self):
        auth = AuthService()
        token = auth.create_session_token(user_id=42)
        # Tamper with the payload
        tampered = "dGFtcGVyZWQ" + token[10:]
        result = auth.verify_session_token(tampered)
        assert result is None

    def test_empty_token_returns_none(self):
        auth = AuthService()
        assert auth.verify_session_token("") is None
        assert auth.verify_session_token(None) is None

    def test_no_dot_token_returns_none(self):
        auth = AuthService()
        assert auth.verify_session_token("nodothere") is None


class TestAuthenticate:
    """Tests for authenticate method."""

    def test_authenticate_valid_user(self):
        auth = AuthService()
        hashed = auth.hash_password("secret123")

        mock_user = MagicMock()
        mock_user.is_active = True
        mock_user.password_hash = hashed

        mock_repo = MagicMock()
        mock_repo.get_by_email.return_value = mock_user

        result = auth.authenticate("user@example.com", "secret123", mock_repo)
        assert result == mock_user

    def test_authenticate_wrong_password(self):
        auth = AuthService()
        hashed = auth.hash_password("secret123")

        mock_user = MagicMock()
        mock_user.is_active = True
        mock_user.password_hash = hashed

        mock_repo = MagicMock()
        mock_repo.get_by_email.return_value = mock_user

        result = auth.authenticate("user@example.com", "wrongpass", mock_repo)
        assert result is None

    def test_authenticate_user_not_found(self):
        auth = AuthService()
        mock_repo = MagicMock()
        mock_repo.get_by_email.return_value = None

        result = auth.authenticate("nobody@example.com", "pass", mock_repo)
        assert result is None

    def test_authenticate_inactive_user(self):
        auth = AuthService()
        hashed = auth.hash_password("secret123")

        mock_user = MagicMock()
        mock_user.is_active = False
        mock_user.password_hash = hashed

        mock_repo = MagicMock()
        mock_repo.get_by_email.return_value = mock_user

        result = auth.authenticate("user@example.com", "secret123", mock_repo)
        assert result is None
