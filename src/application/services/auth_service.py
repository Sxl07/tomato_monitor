"""Authentication service for local offline login.

Uses PBKDF2-SHA256 for password hashing and HMAC-SHA256 for session tokens.
No external dependencies required — stdlib only.
"""

import hashlib
import hmac
import json
import os
import secrets
import time
import base64
import logging
from typing import Optional

_logger = logging.getLogger(__name__)

# Configuration from environment
SESSION_SECRET = os.environ.get("TOMATO_MONITOR_SESSION_SECRET", "")
SESSION_COOKIE_NAME = os.environ.get("TOMATO_MONITOR_SESSION_COOKIE_NAME", "tomato_session")
SESSION_MAX_AGE = int(os.environ.get("TOMATO_MONITOR_SESSION_MAX_AGE_SECONDS", "86400"))  # 24h

# PBKDF2 parameters
_HASH_ALGORITHM = "sha256"
_HASH_ITERATIONS = 260000
_SALT_LENGTH = 16
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_hex(32)
    _logger.warning(
        "TOMATO_MONITOR_SESSION_SECRET not set. "
        "Using ephemeral secret — sessions will be lost on restart."
    )


class AuthService:
    """Handles password hashing, verification, and session token management."""

    def hash_password(self, plain_password: str) -> str:
        """Hash a password using PBKDF2-SHA256. Returns formatted hash string."""
        if not plain_password:
            raise ValueError("password must not be empty")
        salt = secrets.token_hex(_SALT_LENGTH)
        hash_bytes = hashlib.pbkdf2_hmac(
            _HASH_ALGORITHM,
            plain_password.encode("utf-8"),
            salt.encode("utf-8"),
            _HASH_ITERATIONS,
        )
        hash_hex = hash_bytes.hex()
        return f"pbkdf2_sha256${_HASH_ITERATIONS}${salt}${hash_hex}"

    def verify_password(self, plain_password: str, password_hash: str) -> bool:
        """Verify a password against a stored hash. Uses constant-time comparison."""
        if not plain_password or not password_hash:
            return False
        try:
            parts = password_hash.split("$")
            if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
                return False
            iterations = int(parts[1])
            salt = parts[2]
            stored_hash = parts[3]
            computed = hashlib.pbkdf2_hmac(
                _HASH_ALGORITHM,
                plain_password.encode("utf-8"),
                salt.encode("utf-8"),
                iterations,
            ).hex()
            return hmac.compare_digest(computed, stored_hash)
        except (ValueError, IndexError):
            return False

    def authenticate(self, email: str, password: str, user_repo) -> Optional["User"]:
        """Authenticate user by email/password. Returns User or None."""
        from src.domain.entities.user import User  # noqa: F401

        user = user_repo.get_by_email(email)
        if user is None:
            return None
        if not user.is_active:
            return None
        if not self.verify_password(password, user.password_hash):
            return None
        return user

    def create_session_token(self, user_id: int) -> str:
        """Create a signed session token containing user_id and expiration."""
        payload = json.dumps({"uid": user_id, "exp": int(time.time()) + SESSION_MAX_AGE})
        payload_b64 = base64.urlsafe_b64encode(payload.encode()).decode()
        signature = hmac.new(
            SESSION_SECRET.encode(),
            payload_b64.encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"{payload_b64}.{signature}"

    def verify_session_token(self, token: str) -> Optional[int]:
        """Verify token signature and expiration. Returns user_id or None."""
        if not token or "." not in token:
            return None
        try:
            payload_b64, signature = token.rsplit(".", 1)
            expected_sig = hmac.new(
                SESSION_SECRET.encode(),
                payload_b64.encode(),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(signature, expected_sig):
                return None
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            if payload.get("exp", 0) < time.time():
                return None
            return payload.get("uid")
        except (ValueError, KeyError, json.JSONDecodeError):
            return None


def maybe_bootstrap_admin(user_repo) -> Optional["User"]:
    """Create initial admin user from environment if no users exist.

    Reads TOMATO_MONITOR_BOOTSTRAP_ADMIN_EMAIL and TOMATO_MONITOR_BOOTSTRAP_ADMIN_PASSWORD.
    Only creates user if database has zero users. Idempotent.
    """
    from src.domain.entities.user import User

    admin_email = os.environ.get("TOMATO_MONITOR_BOOTSTRAP_ADMIN_EMAIL", "")
    admin_password = os.environ.get("TOMATO_MONITOR_BOOTSTRAP_ADMIN_PASSWORD", "")

    if not admin_email or not admin_password:
        return None

    # Only bootstrap if no users exist
    existing = user_repo.list_active()
    if existing:
        return None

    # Also check by email in case user was deactivated
    if user_repo.get_by_email(admin_email) is not None:
        return None

    admin_name = os.environ.get("TOMATO_MONITOR_BOOTSTRAP_ADMIN_NAME", "Administrador")
    auth = AuthService()
    hashed = auth.hash_password(admin_password)

    user = User(
        full_name=admin_name,
        email=admin_email,
        password_hash=hashed,
        role="admin",
    )
    created = user_repo.create(user)
    _logger.info(f"Bootstrap admin user created: {admin_email}")
    return created
