"""HybridAuthService: online/offline authentication orchestration.

Coordinates remote Supabase Auth with local PBKDF2 cache to provide:
- Online login via remote provider with local cache refresh.
- Offline fallback only when remote is unreachable (CONNECTIVITY/REMOTE_UNAVAILABLE).
- First-login provisioning of local User from remote auth.
- Online-only registration.
- Identity conflict detection (no silent UUID replacement).
- Protection against auth downgrade (no fallback on INVALID_CREDENTIALS).

This service belongs to the application layer and depends only on
abstractions (ports/interfaces). It does NOT import infrastructure,
httpx, SQLAlchemy, or FastAPI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from src.application.interfaces.remote_auth_port import RemoteAuthPort, RemoteAuthResult
from src.application.services.auth_service import AuthService
from src.domain.entities.user import User
from src.domain.repositories.user_repository import UserRepository

_logger = logging.getLogger(__name__)

# Error types that allow local fallback (remote unreachable).
_FALLBACK_ALLOWED = frozenset({"CONNECTIVITY", "REMOTE_UNAVAILABLE"})


def _utcnow() -> datetime:
    """Return current UTC time as timezone-naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class LoginResult:
    """Result of a hybrid login or registration attempt.

    Attributes:
        success: Whether authentication/registration succeeded.
        user: Authenticated User entity on success, None on failure.
        auth_method: "remote", "local", or "none" (failure).
        error_message: User-facing error message on failure.
        requires_internet: True if the operation needs internet to succeed.
    """

    success: bool
    user: Optional[User] = None
    auth_method: str = "local"
    error_message: Optional[str] = None
    requires_internet: bool = False


class HybridAuthService:
    """Orchestrates hybrid online/offline authentication.

    When remote_auth is configured (not None), login attempts first go to
    the remote provider. Fallback to local hash is only allowed for
    CONNECTIVITY and REMOTE_UNAVAILABLE errors. All other remote rejections
    are final (no auth downgrade).
    """

    def __init__(
        self,
        auth_service: AuthService,
        user_repo: UserRepository,
        remote_auth: Optional[RemoteAuthPort],
    ) -> None:
        self._auth_service = auth_service
        self._user_repo = user_repo
        self._remote_auth = remote_auth

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def login(self, email: str, password: str) -> LoginResult:
        """Authenticate user via hybrid online/offline strategy."""
        if self._remote_auth is None:
            return self._local_login(email, password, requires_internet_if_missing=False)

        # Attempt remote auth directly (no health check)
        try:
            remote_result = self._remote_auth.sign_in(email, password)
        except Exception as exc:
            _logger.error(
                "Unexpected exception from RemoteAuthPort.sign_in: %s",
                type(exc).__name__,
            )
            return LoginResult(
                success=False,
                auth_method="none",
                error_message="Error inesperado de autenticación.",
            )

        if remote_result.success:
            return self._handle_remote_success(email, password, remote_result)

        # Classify remote failure
        error_type = remote_result.error_type

        if error_type in _FALLBACK_ALLOWED:
            _logger.warning(
                "Remote auth unavailable (error_type=%s); attempting local fallback",
                error_type,
            )
            return self._local_login(email, password, requires_internet_if_missing=True)

        # No fallback for any other error type
        _logger.warning(
            "Remote authentication rejected: error_type=%s", error_type
        )
        return self._translate_remote_error(error_type, remote_result.error_message)

    def register(self, email: str, password: str, full_name: str) -> LoginResult:
        """Register a new user via remote provider and cache locally."""
        if self._remote_auth is None:
            return LoginResult(
                success=False,
                auth_method="none",
                error_message="Registro no disponible sin conexión remota.",
                requires_internet=True,
            )

        # Validate full_name before proceeding
        if not full_name or not full_name.strip():
            return LoginResult(
                success=False,
                auth_method="none",
                error_message="El nombre es requerido para el registro.",
            )

        try:
            remote_result = self._remote_auth.sign_up(email, password, full_name)
        except Exception as exc:
            _logger.error(
                "Unexpected exception from RemoteAuthPort.sign_up: %s",
                type(exc).__name__,
            )
            return LoginResult(
                success=False,
                auth_method="none",
                error_message="Error inesperado durante el registro.",
            )

        if remote_result.success:
            result = self._handle_register_success(
                email, password, full_name, remote_result
            )
            if result.success:
                _logger.info(
                    "Remote registration succeeded and local cache updated"
                )
            return result

        # Classify remote failure
        error_type = remote_result.error_type

        if error_type == "EMAIL_EXISTS":
            _logger.warning("Remote registration failed: error_type=EMAIL_EXISTS")
            return LoginResult(
                success=False,
                auth_method="none",
                error_message="El email ya está registrado.",
            )

        if error_type in _FALLBACK_ALLOWED:
            _logger.warning(
                "Remote registration failed: error_type=%s", error_type
            )
            return LoginResult(
                success=False,
                auth_method="none",
                error_message="Se requiere conexión a Internet para crear una cuenta.",
                requires_internet=True,
            )

        if error_type == "RATE_LIMITED":
            _logger.warning("Remote registration failed: error_type=RATE_LIMITED")
            return LoginResult(
                success=False,
                auth_method="none",
                error_message="Demasiados intentos. Intenta nuevamente más tarde.",
            )

        # AUTH_FORBIDDEN, UNKNOWN, or unrecognized
        _logger.warning(
            "Remote registration failed: error_type=%s", error_type
        )
        return LoginResult(
            success=False,
            auth_method="none",
            error_message="No se pudo crear la cuenta.",
        )

    # ------------------------------------------------------------------
    # Private: remote success handlers
    # ------------------------------------------------------------------

    def _handle_remote_success(
        self, email: str, password: str, result: RemoteAuthResult
    ) -> LoginResult:
        """Handle successful remote sign_in: resolve or create local user."""
        # Defensive validation of remote identity
        if not isinstance(result.user_id, str) or not result.user_id:
            return LoginResult(
                success=False,
                auth_method="none",
                error_message="No se pudo validar la identidad remota.",
            )

        # Determine authenticated email
        authenticated_email = (
            result.email
            if isinstance(result.email, str) and result.email.strip()
            else email
        )

        return self._resolve_local_user(
            authenticated_email, password, result.user_id, result.full_name
        )

    def _handle_register_success(
        self,
        email: str,
        password: str,
        full_name: str,
        result: RemoteAuthResult,
    ) -> LoginResult:
        """Handle successful remote sign_up: create/update local user cache."""
        if not isinstance(result.user_id, str) or not result.user_id:
            return LoginResult(
                success=False,
                auth_method="none",
                error_message="No se pudo validar la identidad remota.",
            )

        authenticated_email = (
            result.email
            if isinstance(result.email, str) and result.email.strip()
            else email
        )

        # For register, use the explicit full_name provided by the form
        return self._resolve_local_user(
            authenticated_email, password, result.user_id, full_name
        )

    # ------------------------------------------------------------------
    # Private: identity resolution
    # ------------------------------------------------------------------

    def _resolve_local_user(
        self,
        authenticated_email: str,
        password: str,
        remote_user_id: str,
        full_name: Optional[str],
    ) -> LoginResult:
        """Resolve local user from remote auth success.

        Cases:
        A. No local user → create new.
        B. Local user with remote_user_id=None → associate.
        C. Local user with matching remote_user_id → refresh cache.
        D. Local user with different remote_user_id → IDENTITY_CONFLICT.
        """
        existing_user = self._user_repo.get_by_email(authenticated_email)

        if existing_user is None:
            # Case A: first login — create local cache
            return self._create_local_user(
                authenticated_email, password, remote_user_id, full_name
            )

        # Check if user is active
        if not existing_user.is_active:
            return LoginResult(
                success=False,
                auth_method="none",
                error_message="La cuenta está desactivada.",
            )

        # Case D: identity conflict check
        if (
            existing_user.remote_user_id is not None
            and existing_user.remote_user_id != remote_user_id
        ):
            _logger.error(
                "IDENTITY_CONFLICT: local remote identity differs from "
                "authenticated remote identity. Login rejected."
            )
            return LoginResult(
                success=False,
                auth_method="none",
                error_message="Conflicto de identidad. No se pudo vincular la cuenta.",
            )

        # Case B or C: associate or refresh
        user = self._update_local_cache(existing_user, password, remote_user_id)
        _logger.info("Authentication succeeded via remote provider")
        return LoginResult(
            success=True,
            user=user,
            auth_method="remote",
        )

    def _create_local_user(
        self,
        email: str,
        password: str,
        remote_user_id: str,
        full_name: Optional[str],
    ) -> LoginResult:
        """Create a new local User from remote auth success (Case A)."""
        # Determine display name: use full_name or fallback to email
        display_name = (
            full_name.strip()
            if isinstance(full_name, str) and full_name.strip()
            else email
        )

        password_hash = self._auth_service.hash_password(password)

        user = User(
            full_name=display_name,
            email=email,
            password_hash=password_hash,
            role="operator",
            is_active=True,
            remote_user_id=remote_user_id,
            sync_status="synced",
        )
        created = self._user_repo.create(user)

        # Update last_login_at
        updated = self._user_repo.update(created.id, {"last_login_at": _utcnow()})

        _logger.info("Authentication succeeded via remote provider (new local user created)")
        return LoginResult(
            success=True,
            user=updated,
            auth_method="remote",
        )

    def _update_local_cache(
        self, user: User, password: str, remote_user_id: str
    ) -> User:
        """Refresh local cache: hash, remote_user_id, sync_status, last_login_at."""
        new_hash = self._auth_service.hash_password(password)

        fields: dict = {
            "password_hash": new_hash,
            "sync_status": "synced",
            "last_login_at": _utcnow(),
        }

        # Associate remote_user_id if not yet set (Case B)
        if user.remote_user_id is None:
            fields["remote_user_id"] = remote_user_id

        return self._user_repo.update(user.id, fields)

    # ------------------------------------------------------------------
    # Private: local fallback
    # ------------------------------------------------------------------

    def _local_login(
        self, email: str, password: str, *, requires_internet_if_missing: bool
    ) -> LoginResult:
        """Attempt local-only authentication."""
        user = self._user_repo.get_by_email(email)

        if user is None:
            if requires_internet_if_missing:
                return LoginResult(
                    success=False,
                    auth_method="none",
                    error_message="Se requiere conexión a Internet para el primer inicio de sesión.",
                    requires_internet=True,
                )
            return LoginResult(
                success=False,
                auth_method="none",
                error_message="Credenciales incorrectas. Verifica tu email y contraseña.",
            )

        if not user.is_active:
            return LoginResult(
                success=False,
                auth_method="none",
                error_message="La cuenta está desactivada.",
            )

        if not self._auth_service.verify_password(password, user.password_hash):
            return LoginResult(
                success=False,
                auth_method="none",
                error_message="Credenciales incorrectas. Verifica tu email y contraseña.",
            )

        # Update last_login_at
        updated = self._user_repo.update(user.id, {"last_login_at": _utcnow()})

        if requires_internet_if_missing:
            _logger.info("Authentication succeeded via local fallback (offline)")
        else:
            _logger.info("Authentication succeeded via local credentials (offline-only mode)")

        return LoginResult(
            success=True,
            user=updated,
            auth_method="local",
        )

    # ------------------------------------------------------------------
    # Private: error translation
    # ------------------------------------------------------------------

    def _translate_remote_error(
        self, error_type: Optional[str], error_message: Optional[str]
    ) -> LoginResult:
        """Translate a remote auth error into a LoginResult failure."""
        if error_type == "INVALID_CREDENTIALS":
            return LoginResult(
                success=False,
                auth_method="none",
                error_message="Credenciales incorrectas. Verifica tu email y contraseña.",
            )

        if error_type == "RATE_LIMITED":
            return LoginResult(
                success=False,
                auth_method="none",
                error_message="Demasiados intentos. Intenta nuevamente más tarde.",
            )

        if error_type == "AUTH_FORBIDDEN":
            return LoginResult(
                success=False,
                auth_method="none",
                error_message="No fue posible validar la cuenta.",
            )

        # UNKNOWN or any unrecognized error_type
        return LoginResult(
            success=False,
            auth_method="none",
            error_message="No fue posible validar la cuenta.",
        )
