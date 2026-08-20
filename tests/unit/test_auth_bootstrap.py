"""Unit tests for the bootstrap admin user functionality."""

import os
from unittest.mock import patch, MagicMock

import pytest

from src.application.services.auth_service import maybe_bootstrap_admin, AuthService
from src.domain.entities.user import User


class TestBootstrapAdmin:
    """Tests for maybe_bootstrap_admin function."""

    def test_no_env_vars_returns_none(self):
        """If env vars are not set, do nothing."""
        with patch.dict(os.environ, {
            "TOMATO_MONITOR_BOOTSTRAP_ADMIN_EMAIL": "",
            "TOMATO_MONITOR_BOOTSTRAP_ADMIN_PASSWORD": "",
        }):
            mock_repo = MagicMock()
            result = maybe_bootstrap_admin(mock_repo)
            assert result is None
            mock_repo.create.assert_not_called()

    def test_creates_admin_when_no_users_exist(self):
        """Creates admin user when database is empty."""
        with patch.dict(os.environ, {
            "TOMATO_MONITOR_BOOTSTRAP_ADMIN_EMAIL": "admin@test.com",
            "TOMATO_MONITOR_BOOTSTRAP_ADMIN_PASSWORD": "securepass",
            "TOMATO_MONITOR_BOOTSTRAP_ADMIN_NAME": "Admin Test",
        }):
            mock_repo = MagicMock()
            mock_repo.list_active.return_value = []
            mock_repo.get_by_email.return_value = None

            created_user = User(
                id=1,
                full_name="Admin Test",
                email="admin@test.com",
                password_hash="pbkdf2_sha256$260000$salt$hash",
                role="admin",
            )
            mock_repo.create.return_value = created_user

            result = maybe_bootstrap_admin(mock_repo)
            assert result is not None
            assert result.email == "admin@test.com"
            assert result.role == "admin"

            # Verify create was called with correct user
            call_args = mock_repo.create.call_args[0][0]
            assert call_args.full_name == "Admin Test"
            assert call_args.email == "admin@test.com"
            assert call_args.role == "admin"
            # Password should be hashed, not plain
            assert call_args.password_hash.startswith("pbkdf2_sha256$")

    def test_does_not_create_when_users_exist(self):
        """Does not create admin if active users already exist."""
        with patch.dict(os.environ, {
            "TOMATO_MONITOR_BOOTSTRAP_ADMIN_EMAIL": "admin@test.com",
            "TOMATO_MONITOR_BOOTSTRAP_ADMIN_PASSWORD": "securepass",
        }):
            existing_user = MagicMock()
            mock_repo = MagicMock()
            mock_repo.list_active.return_value = [existing_user]

            result = maybe_bootstrap_admin(mock_repo)
            assert result is None
            mock_repo.create.assert_not_called()

    def test_does_not_create_when_email_already_exists(self):
        """Does not create admin if the email already exists (even deactivated)."""
        with patch.dict(os.environ, {
            "TOMATO_MONITOR_BOOTSTRAP_ADMIN_EMAIL": "admin@test.com",
            "TOMATO_MONITOR_BOOTSTRAP_ADMIN_PASSWORD": "securepass",
        }):
            mock_repo = MagicMock()
            mock_repo.list_active.return_value = []
            # Email exists but is deactivated
            mock_repo.get_by_email.return_value = MagicMock()

            result = maybe_bootstrap_admin(mock_repo)
            assert result is None
            mock_repo.create.assert_not_called()
