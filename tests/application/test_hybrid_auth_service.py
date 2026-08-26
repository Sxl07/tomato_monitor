"""Tests for HybridAuthService — Property 1: No auth downgrade.

Demonstrates that remote rejections (INVALID_CREDENTIALS, RATE_LIMITED,
AUTH_FORBIDDEN, UNKNOWN) NEVER trigger local fallback, even when a valid
local password hash exists.

Evidence is provided via spy counters on:
- RemoteAuthPort.sign_in_calls (must be 1)
- UserRepository.get_by_email_calls (must be 0 for rejections)
- AuthService.verify_password_calls (must be 0 for rejections)
- UserRepository.create_calls (must be 0)
- UserRepository.update_calls (must be 0)

Spec 017 — Supabase Remote Sync.
Requirements: 2.1, 2.5, 3.1, 3.3, 23.3
"""

from typing import Optional

import pytest

from src.application.interfaces.remote_auth_port import RemoteAuthResult
from src.application.services.auth_service import AuthService
from src.application.services.hybrid_auth_service import HybridAuthService, LoginResult
from src.domain.entities.user import User
from src.domain.repositories.user_repository import UserRepository


# ---------------------------------------------------------------------------
# Fakes / Spies
# ---------------------------------------------------------------------------


class FakeRemoteAuthPort:
    """Fake RemoteAuthPort that returns configurable sign_in/sign_up results."""

    def __init__(
        self,
        sign_in_result: Optional[RemoteAuthResult] = None,
        sign_up_result: Optional[RemoteAuthResult] = None,
    ) -> None:
        self._sign_in_result = sign_in_result
        self._sign_up_result = sign_up_result
        self.sign_in_calls = 0
        self.sign_up_calls = 0

    def sign_in(self, email: str, password: str) -> RemoteAuthResult:
        self.sign_in_calls += 1
        if self._sign_in_result is not None:
            return self._sign_in_result
        return RemoteAuthResult(success=False, error_type="UNKNOWN")

    def sign_up(self, email: str, password: str, full_name: str) -> RemoteAuthResult:
        self.sign_up_calls += 1
        if self._sign_up_result is not None:
            return self._sign_up_result
        return RemoteAuthResult(success=False, error_type="UNKNOWN")


class SpyAuthService(AuthService):
    """AuthService with verify_password call counting."""

    def __init__(self) -> None:
        super().__init__()
        self.verify_password_calls = 0

    def verify_password(self, plain_password: str, password_hash: str) -> bool:
        self.verify_password_calls += 1
        return super().verify_password(plain_password, password_hash)


class SpyUserRepository(UserRepository):
    """In-memory UserRepository with call counting."""

    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._next_id = 1
        self.get_by_email_calls = 0
        self.create_calls = 0
        self.update_calls = 0

    def seed_user(self, user: User) -> User:
        """Add a user directly (not counted as create call)."""
        user.id = self._next_id
        self._next_id += 1
        self._users[user.email] = user
        return user

    def create(self, user: User) -> User:
        self.create_calls += 1
        user.id = self._next_id
        self._next_id += 1
        self._users[user.email] = user
        return user

    def get_by_email(self, email: str) -> Optional[User]:
        self.get_by_email_calls += 1
        return self._users.get(email)

    def get_by_id(self, id: int) -> Optional[User]:
        for u in self._users.values():
            if u.id == id:
                return u
        return None

    def update(self, id: int, fields: dict) -> User:
        self.update_calls += 1
        for u in self._users.values():
            if u.id == id:
                for k, v in fields.items():
                    setattr(u, k, v)
                return u
        raise ValueError(f"User {id} not found")

    def list_active(self) -> list[User]:
        return [u for u in self._users.values() if u.is_active]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Real AuthService instance for generating valid hashes
_real_auth = AuthService()
_VALID_PASSWORD = "local-valid-password"
_VALID_HASH = _real_auth.hash_password(_VALID_PASSWORD)


@pytest.fixture
def spy_auth():
    return SpyAuthService()


@pytest.fixture
def repo_with_valid_user():
    """Repository containing a user with a valid local hash."""
    repo = SpyUserRepository()
    repo.seed_user(User(
        full_name="Test User",
        email="user@example.com",
        password_hash=_VALID_HASH,
        role="operator",
        is_active=True,
        remote_user_id="existing-uuid-aaa",
    ))
    return repo


# ===========================================================================
# Test: INVALID_CREDENTIALS — basic rejection
# ===========================================================================


class TestInvalidCredentialsNoFallback:
    """INVALID_CREDENTIALS from remote NEVER triggers local fallback."""

    def test_rejects_login(self, spy_auth):
        remote = FakeRemoteAuthPort(RemoteAuthResult(
            success=False,
            error_type="INVALID_CREDENTIALS",
            error_message="Invalid login credentials",
        ))
        repo = SpyUserRepository()
        svc = HybridAuthService(spy_auth, repo, remote_auth=remote)

        result = svc.login("user@example.com", "wrong-password")

        assert result.success is False
        assert result.user is None
        assert result.auth_method == "none"
        assert result.requires_internet is False

    def test_remote_called_exactly_once(self, spy_auth):
        remote = FakeRemoteAuthPort(RemoteAuthResult(
            success=False,
            error_type="INVALID_CREDENTIALS",
            error_message="Invalid login credentials",
        ))
        repo = SpyUserRepository()
        svc = HybridAuthService(spy_auth, repo, remote_auth=remote)

        svc.login("user@example.com", "wrong-password")

        assert remote.sign_in_calls == 1

    def test_no_local_operations(self, spy_auth):
        remote = FakeRemoteAuthPort(RemoteAuthResult(
            success=False,
            error_type="INVALID_CREDENTIALS",
            error_message="Invalid login credentials",
        ))
        repo = SpyUserRepository()
        svc = HybridAuthService(spy_auth, repo, remote_auth=remote)

        svc.login("user@example.com", "wrong-password")

        assert repo.get_by_email_calls == 0
        assert spy_auth.verify_password_calls == 0
        assert repo.create_calls == 0
        assert repo.update_calls == 0


# ===========================================================================
# Test: INVALID_CREDENTIALS with VALID local hash — the critical test
# ===========================================================================


class TestInvalidCredentialsWithValidLocalHash:
    """Even when local hash would succeed, INVALID_CREDENTIALS rejects."""

    def test_rejects_despite_valid_hash(self, spy_auth, repo_with_valid_user):
        """The password IS correct locally, but remote said INVALID_CREDENTIALS."""
        remote = FakeRemoteAuthPort(RemoteAuthResult(
            success=False,
            error_type="INVALID_CREDENTIALS",
            error_message="Invalid login credentials",
        ))
        svc = HybridAuthService(spy_auth, repo_with_valid_user, remote_auth=remote)

        # Use the password that WOULD pass locally
        result = svc.login("user@example.com", _VALID_PASSWORD)

        assert result.success is False
        assert result.user is None
        assert result.auth_method == "none"

    def test_no_local_verification_attempted(self, spy_auth, repo_with_valid_user):
        """verify_password and get_by_email are never called."""
        remote = FakeRemoteAuthPort(RemoteAuthResult(
            success=False,
            error_type="INVALID_CREDENTIALS",
            error_message="Invalid login credentials",
        ))
        svc = HybridAuthService(spy_auth, repo_with_valid_user, remote_auth=remote)

        svc.login("user@example.com", _VALID_PASSWORD)

        assert repo_with_valid_user.get_by_email_calls == 0
        assert spy_auth.verify_password_calls == 0

    def test_no_persistence_changes(self, spy_auth, repo_with_valid_user):
        """No create or update occurs."""
        remote = FakeRemoteAuthPort(RemoteAuthResult(
            success=False,
            error_type="INVALID_CREDENTIALS",
            error_message="Invalid login credentials",
        ))
        svc = HybridAuthService(spy_auth, repo_with_valid_user, remote_auth=remote)

        svc.login("user@example.com", _VALID_PASSWORD)

        assert repo_with_valid_user.create_calls == 0
        assert repo_with_valid_user.update_calls == 0

    def test_password_hash_unchanged(self, spy_auth, repo_with_valid_user):
        """The stored hash must not be modified."""
        remote = FakeRemoteAuthPort(RemoteAuthResult(
            success=False,
            error_type="INVALID_CREDENTIALS",
            error_message="Invalid login credentials",
        ))
        svc = HybridAuthService(spy_auth, repo_with_valid_user, remote_auth=remote)

        svc.login("user@example.com", _VALID_PASSWORD)

        user = repo_with_valid_user.get_by_email("user@example.com")
        # Reset counter since we just called get_by_email for verification
        assert user.password_hash == _VALID_HASH

    def test_remote_user_id_unchanged(self, spy_auth, repo_with_valid_user):
        """remote_user_id must not be modified."""
        remote = FakeRemoteAuthPort(RemoteAuthResult(
            success=False,
            error_type="INVALID_CREDENTIALS",
            error_message="Invalid login credentials",
        ))
        svc = HybridAuthService(spy_auth, repo_with_valid_user, remote_auth=remote)

        svc.login("user@example.com", _VALID_PASSWORD)

        user = repo_with_valid_user.get_by_email("user@example.com")
        assert user.remote_user_id == "existing-uuid-aaa"


# ===========================================================================
# Test: RATE_LIMITED — no fallback
# ===========================================================================


class TestRateLimitedNoFallback:
    """RATE_LIMITED with valid local hash does NOT trigger fallback."""

    def test_rejects_despite_valid_hash(self, spy_auth, repo_with_valid_user):
        remote = FakeRemoteAuthPort(RemoteAuthResult(
            success=False,
            error_type="RATE_LIMITED",
            error_message="Too many requests",
        ))
        svc = HybridAuthService(spy_auth, repo_with_valid_user, remote_auth=remote)

        result = svc.login("user@example.com", _VALID_PASSWORD)

        assert result.success is False
        assert result.auth_method == "none"
        assert result.requires_internet is False

    def test_no_local_operations(self, spy_auth, repo_with_valid_user):
        remote = FakeRemoteAuthPort(RemoteAuthResult(
            success=False,
            error_type="RATE_LIMITED",
            error_message="Too many requests",
        ))
        svc = HybridAuthService(spy_auth, repo_with_valid_user, remote_auth=remote)

        svc.login("user@example.com", _VALID_PASSWORD)

        assert remote.sign_in_calls == 1
        assert repo_with_valid_user.get_by_email_calls == 0
        assert spy_auth.verify_password_calls == 0
        assert repo_with_valid_user.create_calls == 0
        assert repo_with_valid_user.update_calls == 0


# ===========================================================================
# Test: AUTH_FORBIDDEN — no fallback
# ===========================================================================


class TestAuthForbiddenNoFallback:
    """AUTH_FORBIDDEN with valid local hash does NOT trigger fallback."""

    def test_rejects_despite_valid_hash(self, spy_auth, repo_with_valid_user):
        remote = FakeRemoteAuthPort(RemoteAuthResult(
            success=False,
            error_type="AUTH_FORBIDDEN",
            error_message="Forbidden",
        ))
        svc = HybridAuthService(spy_auth, repo_with_valid_user, remote_auth=remote)

        result = svc.login("user@example.com", _VALID_PASSWORD)

        assert result.success is False
        assert result.auth_method == "none"
        assert result.requires_internet is False

    def test_no_local_operations(self, spy_auth, repo_with_valid_user):
        remote = FakeRemoteAuthPort(RemoteAuthResult(
            success=False,
            error_type="AUTH_FORBIDDEN",
            error_message="Forbidden",
        ))
        svc = HybridAuthService(spy_auth, repo_with_valid_user, remote_auth=remote)

        svc.login("user@example.com", _VALID_PASSWORD)

        assert remote.sign_in_calls == 1
        assert repo_with_valid_user.get_by_email_calls == 0
        assert spy_auth.verify_password_calls == 0
        assert repo_with_valid_user.create_calls == 0
        assert repo_with_valid_user.update_calls == 0


# ===========================================================================
# Test: UNKNOWN — fail-closed, no fallback
# ===========================================================================


class TestUnknownNoFallback:
    """UNKNOWN error with valid local hash does NOT trigger fallback."""

    def test_rejects_despite_valid_hash(self, spy_auth, repo_with_valid_user):
        remote = FakeRemoteAuthPort(RemoteAuthResult(
            success=False,
            error_type="UNKNOWN",
            error_message="Something went wrong",
        ))
        svc = HybridAuthService(spy_auth, repo_with_valid_user, remote_auth=remote)

        result = svc.login("user@example.com", _VALID_PASSWORD)

        assert result.success is False
        assert result.auth_method == "none"

    def test_no_local_operations(self, spy_auth, repo_with_valid_user):
        remote = FakeRemoteAuthPort(RemoteAuthResult(
            success=False,
            error_type="UNKNOWN",
            error_message="Something went wrong",
        ))
        svc = HybridAuthService(spy_auth, repo_with_valid_user, remote_auth=remote)

        svc.login("user@example.com", _VALID_PASSWORD)

        assert remote.sign_in_calls == 1
        assert repo_with_valid_user.get_by_email_calls == 0
        assert spy_auth.verify_password_calls == 0
        assert repo_with_valid_user.create_calls == 0
        assert repo_with_valid_user.update_calls == 0


# ===========================================================================
# Task 7.3 — Property 13: First remote login creates usable offline cache
# ===========================================================================

_REMOTE_UUID = "11111111-2222-4333-8444-555555555555"
_FIRST_LOGIN_PASSWORD = "FirstLoginPassword123!"


def _remote_success(
    full_name="Nuevo Operador",
    email="newuser@example.com",
    user_id=_REMOTE_UUID,
):
    """Helper to build a successful remote sign_in result."""
    return RemoteAuthResult(
        success=True,
        user_id=user_id,
        access_token="dummy-jwt-not-persisted",
        email=email,
        full_name=full_name,
    )


class TestFirstRemoteLoginCreatesUser:
    """First remote sign_in with no local user creates a local cache."""

    def test_login_succeeds(self):
        remote = FakeRemoteAuthPort(_remote_success())
        repo = SpyUserRepository()
        auth = SpyAuthService()
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        result = svc.login("newuser@example.com", _FIRST_LOGIN_PASSWORD)

        assert result.success is True
        assert result.auth_method == "remote"
        assert result.user is not None

    def test_user_created_in_repo(self):
        remote = FakeRemoteAuthPort(_remote_success())
        repo = SpyUserRepository()
        auth = SpyAuthService()
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        svc.login("newuser@example.com", _FIRST_LOGIN_PASSWORD)

        assert remote.sign_in_calls == 1
        assert repo.create_calls == 1

    def test_user_fields_correct(self):
        remote = FakeRemoteAuthPort(_remote_success())
        repo = SpyUserRepository()
        auth = SpyAuthService()
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        result = svc.login("newuser@example.com", _FIRST_LOGIN_PASSWORD)
        user = result.user

        assert user.email == "newuser@example.com"
        assert user.full_name == "Nuevo Operador"
        assert user.remote_user_id == _REMOTE_UUID
        assert user.role == "operator"
        assert user.is_active is True
        assert user.sync_status == "synced"
        assert user.id is not None
        assert user.last_login_at is not None
        assert user.last_login_at.tzinfo is None

    def test_hash_is_pbkdf2_not_plaintext(self):
        remote = FakeRemoteAuthPort(_remote_success())
        repo = SpyUserRepository()
        auth = SpyAuthService()
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        result = svc.login("newuser@example.com", _FIRST_LOGIN_PASSWORD)
        user = result.user

        assert user.password_hash != _FIRST_LOGIN_PASSWORD
        assert user.password_hash.startswith("pbkdf2_sha256$")

    def test_hash_verifies_with_original_password(self):
        remote = FakeRemoteAuthPort(_remote_success())
        repo = SpyUserRepository()
        auth = SpyAuthService()
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        result = svc.login("newuser@example.com", _FIRST_LOGIN_PASSWORD)

        real_auth = AuthService()
        assert real_auth.verify_password(_FIRST_LOGIN_PASSWORD, result.user.password_hash)

    def test_jwt_not_persisted(self):
        remote = FakeRemoteAuthPort(_remote_success())
        repo = SpyUserRepository()
        auth = SpyAuthService()
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        result = svc.login("newuser@example.com", _FIRST_LOGIN_PASSWORD)

        assert not hasattr(result.user, "access_token")
        assert not hasattr(result, "access_token")


class TestOfflineLoginAfterFirstRemote:
    """After first remote login, offline login with same password works."""

    def test_offline_login_succeeds(self):
        # Step 1: first remote login
        remote = FakeRemoteAuthPort(_remote_success())
        repo = SpyUserRepository()
        auth = SpyAuthService()
        online_svc = HybridAuthService(auth, repo, remote_auth=remote)

        first_result = online_svc.login("newuser@example.com", _FIRST_LOGIN_PASSWORD)
        assert first_result.success is True
        created_id = first_result.user.id

        # Step 2: new service instance in offline-only mode
        offline_svc = HybridAuthService(auth, repo, remote_auth=None)

        # Step 3: login with same credentials
        offline_result = offline_svc.login("newuser@example.com", _FIRST_LOGIN_PASSWORD)

        assert offline_result.success is True
        assert offline_result.auth_method == "local"
        assert offline_result.user.id == created_id
        assert offline_result.user.remote_user_id == _REMOTE_UUID


class TestFirstLoginFullNameFromMetadata:
    """full_name from remote metadata is used for the local User."""

    def test_full_name_from_result(self):
        remote = FakeRemoteAuthPort(_remote_success(full_name="Nombre Desde Metadata"))
        repo = SpyUserRepository()
        auth = SpyAuthService()
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        result = svc.login("newuser@example.com", "pass")

        assert result.success is True
        assert result.user.full_name == "Nombre Desde Metadata"


class TestFirstLoginFullNameFallbackToEmail:
    """When full_name is absent/empty, email is used as display name."""

    @pytest.mark.parametrize("bad_full_name", [None, "", "   "])
    def test_uses_email_as_fallback(self, bad_full_name):
        remote = FakeRemoteAuthPort(_remote_success(
            full_name=bad_full_name,
            email="metadata-none@example.com",
        ))
        repo = SpyUserRepository()
        auth = SpyAuthService()
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        result = svc.login("metadata-none@example.com", "pass")

        assert result.success is True
        assert result.auth_method == "remote"
        assert result.user is not None
        assert result.user.full_name == "metadata-none@example.com"


# ===========================================================================
# Task 7.4 — Identity association and conflict
# ===========================================================================

_UUID_A = "11111111-2222-4333-8444-555555555555"
_UUID_B = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
_OLD_PASSWORD = "OldLocalPassword123!"
_NEW_PASSWORD = "NewRemotePassword456!"


class TestLegacyUserAssociation:
    """Case B: local User with remote_user_id=None gets UUID associated."""

    def _setup(self):
        auth = SpyAuthService()
        old_hash = auth.hash_password(_OLD_PASSWORD)
        repo = SpyUserRepository()
        user = repo.seed_user(User(
            full_name="Administrador Local",
            email="user@example.com",
            password_hash=old_hash,
            role="admin",
            is_active=True,
            remote_user_id=None,
            sync_status="local_only",
        ))
        remote = FakeRemoteAuthPort(_remote_success(
            user_id=_UUID_A,
            email="user@example.com",
            full_name="Administrador Local",
        ))
        svc = HybridAuthService(auth, repo, remote_auth=remote)
        return svc, repo, remote, auth, old_hash, user

    def test_login_succeeds(self):
        svc, repo, remote, auth, _, _ = self._setup()
        result = svc.login("user@example.com", _NEW_PASSWORD)
        assert result.success is True
        assert result.auth_method == "remote"

    def test_uuid_associated(self):
        svc, repo, remote, auth, _, _ = self._setup()
        svc.login("user@example.com", _NEW_PASSWORD)
        user = repo.get_by_email("user@example.com")
        assert user.remote_user_id == _UUID_A

    def test_role_preserved(self):
        svc, repo, remote, auth, _, _ = self._setup()
        svc.login("user@example.com", _NEW_PASSWORD)
        user = repo.get_by_email("user@example.com")
        assert user.role == "admin"

    def test_full_name_preserved(self):
        svc, repo, remote, auth, _, _ = self._setup()
        svc.login("user@example.com", _NEW_PASSWORD)
        user = repo.get_by_email("user@example.com")
        assert user.full_name == "Administrador Local"

    def test_hash_updated_to_new_password(self):
        svc, repo, remote, auth, old_hash, _ = self._setup()
        svc.login("user@example.com", _NEW_PASSWORD)
        user = repo.get_by_email("user@example.com")
        # Hash changed
        assert user.password_hash != old_hash
        assert user.password_hash != _NEW_PASSWORD
        assert user.password_hash.startswith("pbkdf2_sha256$")
        # New password verifies
        real_auth = AuthService()
        assert real_auth.verify_password(_NEW_PASSWORD, user.password_hash)
        # Old password no longer verifies against new hash
        assert not real_auth.verify_password(_OLD_PASSWORD, user.password_hash)

    def test_sync_status_synced(self):
        svc, repo, remote, auth, _, _ = self._setup()
        svc.login("user@example.com", _NEW_PASSWORD)
        user = repo.get_by_email("user@example.com")
        assert user.sync_status == "synced"

    def test_no_user_created(self):
        svc, repo, remote, auth, _, _ = self._setup()
        svc.login("user@example.com", _NEW_PASSWORD)
        assert repo.create_calls == 0
        assert repo.update_calls == 1


class TestMatchingUuidLogin:
    """Case C: local User with matching remote_user_id logs in normally."""

    def _setup(self):
        auth = SpyAuthService()
        old_hash = auth.hash_password(_OLD_PASSWORD)
        repo = SpyUserRepository()
        user = repo.seed_user(User(
            full_name="Usuario Existente",
            email="user@example.com",
            password_hash=old_hash,
            role="admin",
            is_active=True,
            remote_user_id=_UUID_A,
            sync_status="pending_sync",
        ))
        remote = FakeRemoteAuthPort(_remote_success(
            user_id=_UUID_A,
            email="user@example.com",
            full_name="Usuario Existente",
        ))
        svc = HybridAuthService(auth, repo, remote_auth=remote)
        return svc, repo, remote, auth, old_hash, user

    def test_login_succeeds(self):
        svc, repo, remote, auth, _, _ = self._setup()
        result = svc.login("user@example.com", _NEW_PASSWORD)
        assert result.success is True
        assert result.auth_method == "remote"

    def test_uuid_unchanged(self):
        svc, repo, remote, auth, _, _ = self._setup()
        svc.login("user@example.com", _NEW_PASSWORD)
        user = repo.get_by_email("user@example.com")
        assert user.remote_user_id == _UUID_A

    def test_hash_updated(self):
        svc, repo, remote, auth, old_hash, _ = self._setup()
        svc.login("user@example.com", _NEW_PASSWORD)
        user = repo.get_by_email("user@example.com")
        assert user.password_hash != old_hash
        real_auth = AuthService()
        assert real_auth.verify_password(_NEW_PASSWORD, user.password_hash)

    def test_sync_status_updated(self):
        svc, repo, remote, auth, _, _ = self._setup()
        svc.login("user@example.com", _NEW_PASSWORD)
        user = repo.get_by_email("user@example.com")
        assert user.sync_status == "synced"

    def test_role_and_full_name_preserved(self):
        svc, repo, remote, auth, _, _ = self._setup()
        svc.login("user@example.com", _NEW_PASSWORD)
        user = repo.get_by_email("user@example.com")
        assert user.role == "admin"
        assert user.full_name == "Usuario Existente"

    def test_no_duplicate_user(self):
        svc, repo, remote, auth, _, _ = self._setup()
        svc.login("user@example.com", _NEW_PASSWORD)
        assert repo.create_calls == 0
        assert repo.update_calls == 1


class TestIdentityConflict:
    """Case D: remote UUID differs from local → IDENTITY_CONFLICT rejection."""

    def _setup(self):
        from datetime import datetime
        auth = SpyAuthService()
        old_hash = auth.hash_password(_OLD_PASSWORD)
        fixed_login_at = datetime(2025, 6, 15, 10, 30, 0)
        repo = SpyUserRepository()
        user = repo.seed_user(User(
            full_name="Admin Conflict",
            email="user@example.com",
            password_hash=old_hash,
            role="admin",
            is_active=True,
            remote_user_id=_UUID_A,
            sync_status="pending_sync",
            last_login_at=fixed_login_at,
        ))
        # Remote returns UUID_B (different!)
        remote = FakeRemoteAuthPort(_remote_success(
            user_id=_UUID_B,
            email="user@example.com",
            full_name="Admin Conflict",
        ))
        svc = HybridAuthService(auth, repo, remote_auth=remote)
        return svc, repo, remote, auth, old_hash, user, fixed_login_at

    def test_login_rejected(self):
        svc, *_ = self._setup()
        result = svc.login("user@example.com", _NEW_PASSWORD)
        assert result.success is False
        assert result.user is None
        assert result.auth_method == "none"
        assert result.requires_internet is False

    def test_uuid_not_overwritten(self):
        svc, repo, *_ = self._setup()
        svc.login("user@example.com", _NEW_PASSWORD)
        user = repo.get_by_email("user@example.com")
        assert user.remote_user_id == _UUID_A

    def test_zero_mutations(self):
        svc, repo, remote, auth, *_ = self._setup()
        svc.login("user@example.com", _NEW_PASSWORD)
        assert remote.sign_in_calls == 1
        # get_by_email is called once by _resolve_local_user
        assert repo.get_by_email_calls == 1
        assert repo.create_calls == 0
        assert repo.update_calls == 0

    def test_hash_intact(self):
        svc, repo, remote, auth, old_hash, *_ = self._setup()
        svc.login("user@example.com", _NEW_PASSWORD)
        user = repo.get_by_email("user@example.com")
        assert user.password_hash == old_hash
        real_auth = AuthService()
        assert real_auth.verify_password(_OLD_PASSWORD, old_hash)

    def test_last_login_at_unchanged(self):
        svc, repo, remote, auth, old_hash, user, fixed_login_at = self._setup()
        svc.login("user@example.com", _NEW_PASSWORD)
        user_after = repo.get_by_email("user@example.com")
        assert user_after.last_login_at == fixed_login_at

    def test_sync_status_unchanged(self):
        svc, repo, *_ = self._setup()
        svc.login("user@example.com", _NEW_PASSWORD)
        user = repo.get_by_email("user@example.com")
        assert user.sync_status == "pending_sync"

    def test_no_local_verify_password(self):
        svc, repo, remote, auth, *_ = self._setup()
        svc.login("user@example.com", _NEW_PASSWORD)
        assert auth.verify_password_calls == 0

    def test_identity_conflict_logged(self, caplog):
        import logging
        svc, *_ = self._setup()
        with caplog.at_level(logging.ERROR):
            svc.login("user@example.com", _NEW_PASSWORD)
        assert "IDENTITY_CONFLICT" in caplog.text
        # UUIDs and passwords must NOT appear in logs
        assert _UUID_A not in caplog.text
        assert _UUID_B not in caplog.text
        assert _NEW_PASSWORD not in caplog.text
        assert _OLD_PASSWORD not in caplog.text


# ===========================================================================
# Task 7.5 — Fallback, local direct, and register tests
# ===========================================================================

_OFFLINE_PASSWORD = "OfflinePassword123!"


class TestLoginLocalDirect:
    """remote_auth=None → direct local authentication."""

    def test_login_succeeds_with_correct_password(self):
        auth = SpyAuthService()
        repo = SpyUserRepository()
        repo.seed_user(User(
            full_name="Local Op",
            email="local@example.com",
            password_hash=auth.hash_password(_OFFLINE_PASSWORD),
            is_active=True,
        ))
        svc = HybridAuthService(auth, repo, remote_auth=None)

        result = svc.login("local@example.com", _OFFLINE_PASSWORD)

        assert result.success is True
        assert result.auth_method == "local"
        assert result.user.email == "local@example.com"
        assert result.requires_internet is False

    def test_verify_password_called(self):
        auth = SpyAuthService()
        repo = SpyUserRepository()
        repo.seed_user(User(
            full_name="Local Op",
            email="local@example.com",
            password_hash=auth.hash_password(_OFFLINE_PASSWORD),
            is_active=True,
        ))
        svc = HybridAuthService(auth, repo, remote_auth=None)

        svc.login("local@example.com", _OFFLINE_PASSWORD)

        assert auth.verify_password_calls == 1
        assert repo.update_calls == 1  # last_login_at

    def test_last_login_at_updated(self):
        auth = SpyAuthService()
        repo = SpyUserRepository()
        repo.seed_user(User(
            full_name="Local Op",
            email="local@example.com",
            password_hash=auth.hash_password(_OFFLINE_PASSWORD),
            is_active=True,
        ))
        svc = HybridAuthService(auth, repo, remote_auth=None)

        result = svc.login("local@example.com", _OFFLINE_PASSWORD)

        assert result.user.last_login_at is not None
        assert result.user.last_login_at.tzinfo is None


class TestConnectivityFallbackSuccess:
    """CONNECTIVITY → local fallback with valid hash."""

    def test_fallback_succeeds(self):
        auth = SpyAuthService()
        repo = SpyUserRepository()
        repo.seed_user(User(
            full_name="Off User",
            email="user@example.com",
            password_hash=auth.hash_password(_OFFLINE_PASSWORD),
            is_active=True,
        ))
        remote = FakeRemoteAuthPort(sign_in_result=RemoteAuthResult(
            success=False, error_type="CONNECTIVITY", error_message="timeout",
        ))
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        result = svc.login("user@example.com", _OFFLINE_PASSWORD)

        assert result.success is True
        assert result.auth_method == "local"
        assert result.requires_internet is False
        assert remote.sign_in_calls == 1
        assert repo.get_by_email_calls == 1
        assert auth.verify_password_calls == 1
        assert repo.update_calls == 1  # last_login_at


class TestConnectivityFallbackWrongPassword:
    """CONNECTIVITY + wrong local password → failure (not requires_internet)."""

    def test_wrong_password_rejects(self):
        auth = SpyAuthService()
        repo = SpyUserRepository()
        repo.seed_user(User(
            full_name="Off User",
            email="user@example.com",
            password_hash=auth.hash_password(_OFFLINE_PASSWORD),
            is_active=True,
        ))
        remote = FakeRemoteAuthPort(sign_in_result=RemoteAuthResult(
            success=False, error_type="CONNECTIVITY", error_message="timeout",
        ))
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        result = svc.login("user@example.com", "WrongPassword999!")

        assert result.success is False
        assert result.auth_method == "none"
        assert result.requires_internet is False
        assert auth.verify_password_calls == 1
        assert repo.create_calls == 0
        assert repo.update_calls == 0


class TestRemoteUnavailableFallbackSuccess:
    """REMOTE_UNAVAILABLE → same fallback behavior as CONNECTIVITY."""

    def test_fallback_succeeds(self):
        auth = SpyAuthService()
        repo = SpyUserRepository()
        repo.seed_user(User(
            full_name="Off User",
            email="user@example.com",
            password_hash=auth.hash_password(_OFFLINE_PASSWORD),
            is_active=True,
        ))
        remote = FakeRemoteAuthPort(sign_in_result=RemoteAuthResult(
            success=False, error_type="REMOTE_UNAVAILABLE", error_message="503",
        ))
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        result = svc.login("user@example.com", _OFFLINE_PASSWORD)

        assert result.success is True
        assert result.auth_method == "local"
        assert remote.sign_in_calls == 1
        assert auth.verify_password_calls == 1
        assert repo.update_calls == 1


class TestConnectivityNoLocalUser:
    """CONNECTIVITY + user does not exist → requires_internet."""

    def test_requires_internet(self):
        auth = SpyAuthService()
        repo = SpyUserRepository()  # empty
        remote = FakeRemoteAuthPort(sign_in_result=RemoteAuthResult(
            success=False, error_type="CONNECTIVITY", error_message="timeout",
        ))
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        result = svc.login("nobody@example.com", "pass")

        assert result.success is False
        assert result.auth_method == "none"
        assert result.requires_internet is True
        assert remote.sign_in_calls == 1
        assert repo.get_by_email_calls == 1
        assert auth.verify_password_calls == 0
        assert repo.create_calls == 0
        assert repo.update_calls == 0


# ===========================================================================
# Register tests
# ===========================================================================

_REGISTER_UUID = "33333333-4444-4555-8666-777777777777"


class TestRegisterSuccess:
    """Successful remote registration creates local user cache."""

    def test_register_succeeds(self):
        auth = SpyAuthService()
        repo = SpyUserRepository()
        remote = FakeRemoteAuthPort(sign_up_result=RemoteAuthResult(
            success=True,
            user_id=_REGISTER_UUID,
            access_token="dummy-register-jwt",
            email="register@example.com",
            full_name="Registered User",
        ))
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        result = svc.register("register@example.com", "RegisterPassword123!", "Registered User")

        assert result.success is True
        assert result.auth_method == "remote"
        assert result.user is not None
        assert remote.sign_up_calls == 1
        assert repo.create_calls == 1

    def test_user_fields(self):
        auth = SpyAuthService()
        repo = SpyUserRepository()
        remote = FakeRemoteAuthPort(sign_up_result=RemoteAuthResult(
            success=True,
            user_id=_REGISTER_UUID,
            access_token="dummy-register-jwt",
            email="register@example.com",
            full_name="Registered User",
        ))
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        result = svc.register("register@example.com", "RegisterPassword123!", "Registered User")
        user = result.user

        assert user.email == "register@example.com"
        assert user.full_name == "Registered User"
        assert user.remote_user_id == _REGISTER_UUID
        assert user.role == "operator"
        assert user.is_active is True
        assert user.sync_status == "synced"
        assert user.last_login_at is not None

    def test_hash_is_pbkdf2(self):
        auth = SpyAuthService()
        repo = SpyUserRepository()
        remote = FakeRemoteAuthPort(sign_up_result=RemoteAuthResult(
            success=True,
            user_id=_REGISTER_UUID,
            access_token="dummy-register-jwt",
            email="register@example.com",
            full_name="Registered User",
        ))
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        result = svc.register("register@example.com", "RegisterPassword123!", "Registered User")

        assert result.user.password_hash != "RegisterPassword123!"
        assert result.user.password_hash.startswith("pbkdf2_sha256$")
        real_auth = AuthService()
        assert real_auth.verify_password("RegisterPassword123!", result.user.password_hash)

    def test_jwt_not_persisted(self):
        auth = SpyAuthService()
        repo = SpyUserRepository()
        remote = FakeRemoteAuthPort(sign_up_result=RemoteAuthResult(
            success=True,
            user_id=_REGISTER_UUID,
            access_token="dummy-register-jwt",
            email="register@example.com",
            full_name="Registered User",
        ))
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        result = svc.register("register@example.com", "RegisterPassword123!", "Registered User")

        assert not hasattr(result.user, "access_token")
        assert not hasattr(result, "access_token")


class TestRegisterNoRemoteAuth:
    """Register without remote_auth → rejection, no local user."""

    def test_rejects(self):
        auth = SpyAuthService()
        repo = SpyUserRepository()
        svc = HybridAuthService(auth, repo, remote_auth=None)

        result = svc.register("x@example.com", "pass", "Name")

        assert result.success is False
        assert result.auth_method == "none"
        assert result.requires_internet is True
        assert repo.create_calls == 0
        assert repo.update_calls == 0


class TestRegisterConnectivity:
    """Register fails with CONNECTIVITY/REMOTE_UNAVAILABLE → no local user."""

    @pytest.mark.parametrize("error_type", ["CONNECTIVITY", "REMOTE_UNAVAILABLE"])
    def test_requires_internet(self, error_type):
        auth = SpyAuthService()
        repo = SpyUserRepository()
        remote = FakeRemoteAuthPort(sign_up_result=RemoteAuthResult(
            success=False, error_type=error_type, error_message="unreachable",
        ))
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        result = svc.register("x@example.com", "pass", "Name")

        assert result.success is False
        assert result.requires_internet is True
        assert remote.sign_up_calls == 1
        assert repo.create_calls == 0
        assert repo.update_calls == 0


class TestRegisterEmailExists:
    """EMAIL_EXISTS → rejection, no local user."""

    def test_rejects(self):
        auth = SpyAuthService()
        repo = SpyUserRepository()
        remote = FakeRemoteAuthPort(sign_up_result=RemoteAuthResult(
            success=False, error_type="EMAIL_EXISTS", error_message="already registered",
        ))
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        result = svc.register("x@example.com", "pass", "Name")

        assert result.success is False
        assert result.requires_internet is False
        assert "ya está registrado" in result.error_message
        assert repo.create_calls == 0
        assert repo.update_calls == 0


class TestRegisterRateLimited:
    """RATE_LIMITED → rejection, no local user."""

    def test_rejects(self):
        auth = SpyAuthService()
        repo = SpyUserRepository()
        remote = FakeRemoteAuthPort(sign_up_result=RemoteAuthResult(
            success=False, error_type="RATE_LIMITED", error_message="too many",
        ))
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        result = svc.register("x@example.com", "pass", "Name")

        assert result.success is False
        assert result.requires_internet is False
        assert repo.create_calls == 0


class TestRegisterEmptyFullName:
    """Empty/whitespace full_name → rejection without calling remote."""

    def test_rejects_without_remote_call(self):
        auth = SpyAuthService()
        repo = SpyUserRepository()
        remote = FakeRemoteAuthPort(sign_up_result=RemoteAuthResult(
            success=True, user_id="uuid", email="x@x.com", full_name="X",
        ))
        svc = HybridAuthService(auth, repo, remote_auth=remote)

        result = svc.register("x@example.com", "pass", "   ")

        assert result.success is False
        assert remote.sign_up_calls == 0
        assert repo.create_calls == 0
