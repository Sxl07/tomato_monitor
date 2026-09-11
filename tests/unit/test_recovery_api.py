"""Tests for the manual cloud recovery API (Spec 022, block E).

Covers POST /api/recovery/trigger preconditions, mutual exclusion with sync,
the monitoring guard (with recheck), remote re-auth + identity verification,
ephemeral-token handling, RecoveryService execution/response, safe logging, and
the productive wiring of RecoveryService. Confirms there is NO GET status route.

All tests stay offline: sign_in and RecoveryService are patched/overridden; no
real Supabase, HTTP, or filesystem access.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import (
    get_monitoring_runtime_registry,
    get_sync_runtime_state,
    require_current_user_api,
)
from src.application.services.recovery_service import RecoveryIssue, RecoveryResult
from src.application.services.sync_runtime_state import SyncRuntimeState
from src.domain.entities.user import User


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

REMOTE_UUID = "remote-uuid-123"


def _make_supabase_config():
    from src.infrastructure.supabase.supabase_config import SupabaseConfig

    return SupabaseConfig(
        url="https://test.supabase.co",
        publishable_key="test-key",
        storage_bucket="test-bucket",
    )


def _user(remote_id=REMOTE_UUID):
    return User(
        id=1,
        full_name="Test Operator",
        email="operator@example.com",
        password_hash="pbkdf2_sha256$260000$aaaa$bbbb",
        role="operator",
        remote_user_id=remote_id,
    )


def _auth_ok():
    from src.application.interfaces.remote_auth_port import RemoteAuthResult

    return RemoteAuthResult(
        success=True, user_id=REMOTE_UUID, access_token="ephemeral-jwt"
    )


def _recovery_result(success=True):
    return RecoveryResult(
        success=success,
        entities_recovered=10,
        entities_reused=5,
        entities_skipped=2,
        conflicts=1,
        images_downloaded=8,
        images_skipped=3,
        images_failed=1,
        errors=[
            RecoveryIssue(
                entity_type="snapshots",
                remote_id="snap-uuid",
                code="IMAGE_NOT_FOUND",
                message="not found",
            )
        ],
    )


class _Overrides:
    """Context manager applying dependency overrides + configuring app.state."""

    def __init__(self, user=None, runtime=None, registry=None, configured=True):
        self.user = user if user is not None else _user()
        self.runtime = runtime if runtime is not None else SyncRuntimeState()
        self.registry = registry if registry is not None else MagicMock()
        if registry is None:
            self.registry.has_any_live_thread.return_value = False
        self.configured = configured
        self._previous = None

    def __enter__(self):
        self._previous = dict(app.dependency_overrides)
        app.dependency_overrides[require_current_user_api] = lambda: self.user
        app.dependency_overrides[get_sync_runtime_state] = lambda: self.runtime
        app.dependency_overrides[get_monitoring_runtime_registry] = lambda: self.registry
        return self

    def __exit__(self, *exc):
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self._previous)


class _client_with_config:
    """Enter a TestClient (runs lifespan) THEN set app.state.supabase_config.

    The app lifespan startup overwrites supabase_config via load_supabase_config
    (None in tests), so the desired config must be applied AFTER startup.
    """

    def __init__(self, overrides):
        self._overrides = overrides
        self._client = None

    def __enter__(self):
        self._client = TestClient(app)
        self._client.__enter__()
        app.state.supabase_config = (
            _make_supabase_config() if self._overrides.configured else None
        )
        return self._client

    def __exit__(self, *exc):
        return self._client.__exit__(*exc)


# ---------------------------------------------------------------------------
# 1. Auth required
# ---------------------------------------------------------------------------


def test_post_without_auth_returns_401():
    # Force the auth dependency to raise 401 (as production does when no user).
    from fastapi import HTTPException

    def _deny():
        raise HTTPException(status_code=401, detail="Not authenticated")

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[require_current_user_api] = _deny
    try:
        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            resp = client.post("/api/recovery/trigger", json={"password": "x"})
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


# ---------------------------------------------------------------------------
# 2. Missing remote_user_id -> 400, no remote auth
# ---------------------------------------------------------------------------


@patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
def test_missing_remote_user_id_returns_400_no_signin(mock_sign_in):
    with _Overrides(user=_user(remote_id=None)) as ov:
        with _client_with_config(ov) as client:
            resp = client.post("/api/recovery/trigger", json={"password": "x"})
    assert resp.status_code == 400
    mock_sign_in.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Supabase not configured -> same error/status as sync (400)
# ---------------------------------------------------------------------------


@patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
def test_supabase_not_configured_returns_400(mock_sign_in):
    with _Overrides(configured=False) as ov:
        with _client_with_config(ov) as client:
            resp = client.post("/api/recovery/trigger", json={"password": "x"})
    assert resp.status_code == 400
    mock_sign_in.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Sync active -> recovery 409, sign_in not called
# ---------------------------------------------------------------------------


@patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
def test_sync_active_blocks_recovery_409(mock_sign_in):
    runtime = SyncRuntimeState()
    runtime.try_acquire()  # sync active
    with _Overrides(runtime=runtime) as ov:
        with _client_with_config(ov) as client:
            resp = client.post("/api/recovery/trigger", json={"password": "x"})
    assert resp.status_code == 409
    assert "sincronización" in resp.json()["detail"].lower()
    mock_sign_in.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Recovery already active -> second recovery 409
# ---------------------------------------------------------------------------


@patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
def test_recovery_active_blocks_second_recovery_409(mock_sign_in):
    runtime = SyncRuntimeState()
    runtime.try_acquire_recovery()  # recovery active
    with _Overrides(runtime=runtime) as ov:
        with _client_with_config(ov) as client:
            resp = client.post("/api/recovery/trigger", json={"password": "x"})
    assert resp.status_code == 409
    assert "recuperación" in resp.json()["detail"].lower()
    mock_sign_in.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Active monitoring/live thread -> 409, lock released, no sign_in
# ---------------------------------------------------------------------------


@patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
def test_active_monitoring_blocks_recovery_409(mock_sign_in):
    registry = MagicMock()
    registry.has_any_live_thread.return_value = True
    runtime = SyncRuntimeState()
    with _Overrides(runtime=runtime, registry=registry) as ov:
        with _client_with_config(ov) as client:
            resp = client.post("/api/recovery/trigger", json={"password": "x"})
    assert resp.status_code == 409
    mock_sign_in.assert_not_called()
    # Recovery lock released.
    assert runtime.get_active_operation() is None


# ---------------------------------------------------------------------------
# 7. sign_in INVALID_CREDENTIALS -> 401, no RecoveryService, lock released
# ---------------------------------------------------------------------------


@patch("app.routes.recovery_api.get_recovery_service")
@patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
def test_invalid_credentials_returns_401(mock_sign_in, mock_get_service):
    from src.application.interfaces.remote_auth_port import RemoteAuthResult

    mock_sign_in.return_value = RemoteAuthResult(
        success=False, error_type="INVALID_CREDENTIALS"
    )
    runtime = SyncRuntimeState()
    with _Overrides(runtime=runtime) as ov:
        with _client_with_config(ov) as client:
            resp = client.post("/api/recovery/trigger", json={"password": "bad"})
    assert resp.status_code == 401
    mock_get_service.assert_not_called()
    assert runtime.get_active_operation() is None


# ---------------------------------------------------------------------------
# 8. sign_in connectivity/server failure -> mapped status, no fallback, released
# ---------------------------------------------------------------------------


@patch("app.routes.recovery_api.get_recovery_service")
@patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
def test_connectivity_failure_mapped_and_released(mock_sign_in, mock_get_service):
    from src.application.interfaces.remote_auth_port import RemoteAuthResult

    mock_sign_in.return_value = RemoteAuthResult(
        success=False, error_type="CONNECTIVITY"
    )
    runtime = SyncRuntimeState()
    with _Overrides(runtime=runtime) as ov:
        with _client_with_config(ov) as client:
            resp = client.post("/api/recovery/trigger", json={"password": "x"})
    assert resp.status_code == 401  # sync maps auth errors to 401
    mock_get_service.assert_not_called()
    assert runtime.get_active_operation() is None


# ---------------------------------------------------------------------------
# 9. remote identity mismatch -> 403, no overwrite, no RecoveryService, released
# ---------------------------------------------------------------------------


@patch("app.routes.recovery_api.get_recovery_service")
@patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
def test_remote_identity_mismatch_returns_403(mock_sign_in, mock_get_service):
    from src.application.interfaces.remote_auth_port import RemoteAuthResult

    mock_sign_in.return_value = RemoteAuthResult(
        success=True, user_id="a-different-uuid", access_token="jwt"
    )
    user = _user(remote_id=REMOTE_UUID)
    runtime = SyncRuntimeState()
    with _Overrides(user=user, runtime=runtime) as ov:
        with _client_with_config(ov) as client:
            resp = client.post("/api/recovery/trigger", json={"password": "x"})
    assert resp.status_code == 403
    mock_get_service.assert_not_called()
    # Local user unchanged.
    assert user.remote_user_id == REMOTE_UUID
    assert runtime.get_active_operation() is None


# ---------------------------------------------------------------------------
# 10. missing access_token after auth success -> fail closed, no RecoveryService
# ---------------------------------------------------------------------------


@patch("app.routes.recovery_api.get_recovery_service")
@patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
def test_missing_access_token_fails_closed(mock_sign_in, mock_get_service):
    from src.application.interfaces.remote_auth_port import RemoteAuthResult

    mock_sign_in.return_value = RemoteAuthResult(
        success=True, user_id=REMOTE_UUID, access_token=None
    )
    runtime = SyncRuntimeState()
    with _Overrides(runtime=runtime) as ov:
        with _client_with_config(ov) as client:
            resp = client.post("/api/recovery/trigger", json={"password": "x"})
    assert resp.status_code == 502
    mock_get_service.assert_not_called()
    assert runtime.get_active_operation() is None


# ---------------------------------------------------------------------------
# 11. success: sign_in args, execute_recovery args, response counters, released
# ---------------------------------------------------------------------------


@patch("app.routes.recovery_api.get_recovery_service")
@patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
def test_success_flow(mock_sign_in, mock_get_service):
    mock_sign_in.return_value = _auth_ok()
    service = MagicMock()
    service.execute_recovery.return_value = _recovery_result(success=True)
    mock_get_service.return_value = service

    user = _user()
    runtime = SyncRuntimeState()
    with _Overrides(user=user, runtime=runtime) as ov:
        with _client_with_config(ov) as client:
            resp = client.post(
                "/api/recovery/trigger", json={"password": "secret-pass"}
            )

    assert resp.status_code == 200
    # sign_in received EXACTLY current_user.email + request password.
    mock_sign_in.assert_called_once_with("operator@example.com", "secret-pass")
    # execute_recovery received access_token, remote_user_id, local id.
    service.execute_recovery.assert_called_once_with(
        "ephemeral-jwt", REMOTE_UUID, 1
    )
    data = resp.json()
    assert data["success"] is True
    assert data["entities_recovered"] == 10
    assert data["entities_reused"] == 5
    assert data["entities_skipped"] == 2
    assert data["conflicts"] == 1
    assert data["images_downloaded"] == 8
    assert data["images_skipped"] == 3
    assert data["images_failed"] == 1
    assert data["errors"][0]["code"] == "IMAGE_NOT_FOUND"
    assert runtime.get_active_operation() is None


# ---------------------------------------------------------------------------
# 12. RecoveryResult(success=False) -> HTTP 200 with partial counters
# ---------------------------------------------------------------------------


@patch("app.routes.recovery_api.get_recovery_service")
@patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
def test_partial_result_returns_200(mock_sign_in, mock_get_service):
    mock_sign_in.return_value = _auth_ok()
    service = MagicMock()
    service.execute_recovery.return_value = _recovery_result(success=False)
    mock_get_service.return_value = service

    with _Overrides() as ov:
        with _client_with_config(ov) as client:
            resp = client.post("/api/recovery/trigger", json={"password": "x"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert data["entities_recovered"] == 10  # partial counters preserved


# ---------------------------------------------------------------------------
# 13. exception in RecoveryService -> 500, lock released, next recovery works
# ---------------------------------------------------------------------------


@patch("app.routes.recovery_api.get_recovery_service")
@patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
def test_recovery_exception_returns_500_and_releases(mock_sign_in, mock_get_service):
    mock_sign_in.return_value = _auth_ok()
    service = MagicMock()
    service.execute_recovery.side_effect = RuntimeError("boom")
    mock_get_service.return_value = service

    runtime = SyncRuntimeState()
    with _Overrides(runtime=runtime) as ov:
        with _client_with_config(ov) as client:
            resp = client.post("/api/recovery/trigger", json={"password": "x"})
    assert resp.status_code == 500
    assert runtime.get_active_operation() is None
    # A subsequent recovery can acquire the lock.
    assert runtime.try_acquire_recovery() is True


# ---------------------------------------------------------------------------
# 14. second monitoring guard (recheck) -> 409, execute not called, released
# ---------------------------------------------------------------------------


@patch("app.routes.recovery_api.get_recovery_service")
@patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
def test_second_guard_after_auth_blocks(mock_sign_in, mock_get_service):
    mock_sign_in.return_value = _auth_ok()
    service = MagicMock()
    mock_get_service.return_value = service

    registry = MagicMock()
    # First guard False (passes), second guard True (blocks after re-auth).
    registry.has_any_live_thread.side_effect = [False, True]
    runtime = SyncRuntimeState()
    with _Overrides(runtime=runtime, registry=registry) as ov:
        with _client_with_config(ov) as client:
            resp = client.post("/api/recovery/trigger", json={"password": "x"})
    assert resp.status_code == 409
    service.execute_recovery.assert_not_called()
    assert runtime.get_active_operation() is None


# ---------------------------------------------------------------------------
# 15. password / JWT never leak to logs
# ---------------------------------------------------------------------------


@patch("app.routes.recovery_api.get_recovery_service")
@patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
def test_password_and_jwt_not_logged(mock_sign_in, mock_get_service, caplog):
    mock_sign_in.return_value = _auth_ok()
    service = MagicMock()
    service.execute_recovery.return_value = _recovery_result(success=True)
    mock_get_service.return_value = service

    with _Overrides() as ov:
        with _client_with_config(ov) as client:
            with caplog.at_level(logging.DEBUG):
                resp = client.post(
                    "/api/recovery/trigger",
                    json={"password": "super-secret-pass"},
                )
    assert resp.status_code == 200
    combined = "\n".join(r.getMessage() for r in caplog.records)
    assert "super-secret-pass" not in combined
    assert "ephemeral-jwt" not in combined


# ---------------------------------------------------------------------------
# 16. No GET /api/recovery/status
# ---------------------------------------------------------------------------


def test_no_get_recovery_status():
    with _Overrides() as ov:
        with _client_with_config(ov) as client:
            resp = client.get("/api/recovery/status")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 28. Productive wiring: get_recovery_service builds RecoveryService with the
# approved implementations (non-None guard/download/files, OUTPUTS_DIR, no
# service_role).
# ---------------------------------------------------------------------------


@patch("src.application.services.recovery_service.RecoveryService")
@patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
def test_productive_wiring_constructs_recovery_service(
    mock_sign_in, mock_recovery_cls
):
    mock_sign_in.return_value = _auth_ok()

    captured = {}

    def _fake_ctor(**kwargs):
        captured.update(kwargs)
        instance = MagicMock()
        instance.execute_recovery.return_value = _recovery_result(success=True)
        return instance

    mock_recovery_cls.side_effect = _fake_ctor

    # NOTE: do NOT override get_recovery_service here — we exercise the real
    # productive wiring from app.dependencies. A real DatabaseManager is needed
    # on app.state, so we run within the app lifespan (TestClient context).
    user = _user()
    runtime = SyncRuntimeState()
    registry = MagicMock()
    registry.has_any_live_thread.return_value = False

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[require_current_user_api] = lambda: user
    app.dependency_overrides[get_sync_runtime_state] = lambda: runtime
    app.dependency_overrides[get_monitoring_runtime_registry] = lambda: registry
    try:
        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            resp = client.post("/api/recovery/trigger", json={"password": "x"})
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)

    assert resp.status_code == 200
    mock_recovery_cls.assert_called_once()

    from src.infrastructure.persistence.recovery_file_adapter import (
        LocalRecoveryFileAdapter,
    )
    from src.infrastructure.persistence.recovery_tombstone_adapter import (
        SqlRecoveryTombstoneAdapter,
    )
    from src.infrastructure.supabase.supabase_read_adapter import (
        SupabaseRemoteReadAdapter,
    )
    from src.infrastructure.supabase.supabase_storage_adapter import (
        SupabaseStorageAdapter,
    )
    from src.infrastructure.config.settings import OUTPUTS_DIR

    assert isinstance(captured["remote_read"], SupabaseRemoteReadAdapter)
    assert isinstance(captured["tombstone_guard"], SqlRecoveryTombstoneAdapter)
    assert isinstance(captured["remote_download"], SupabaseStorageAdapter)
    assert isinstance(captured["recovery_files"], LocalRecoveryFileAdapter)
    # Non-None recovery-specific dependencies.
    assert captured["tombstone_guard"] is not None
    assert captured["remote_download"] is not None
    assert captured["recovery_files"] is not None
    # UoW factory produces a FRESH unit-of-work per call.
    from src.infrastructure.persistence.recovery_unit_of_work import (
        SqlRecoveryUnitOfWork,
    )

    uow1 = captured["uow_factory"]()
    uow2 = captured["uow_factory"]()
    assert isinstance(uow1, SqlRecoveryUnitOfWork)
    assert uow1 is not uow2
    # OUTPUTS_DIR productivo pasado al file adapter.
    assert captured["recovery_files"]._outputs_dir == OUTPUTS_DIR


# ---------------------------------------------------------------------------
# Block E microfix: request-scoped auth session released before remote calls.
# ---------------------------------------------------------------------------


def _seed_local_user(db_manager):
    """Insert a real local user with a remote_user_id; return its local id.

    Uses a unique email so repeated runs against the persistent dev DB never
    collide on the UNIQUE email constraint.
    """
    import uuid

    from src.infrastructure.persistence.models.user_model import UserModel

    session = db_manager.get_session()
    try:
        user = UserModel(
            full_name="Real Operator",
            email=f"real-{uuid.uuid4().hex}@example.com",
            password_hash="x",
            remote_user_id=REMOTE_UUID,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user.id
    finally:
        session.close()


@patch("app.routes.recovery_api.get_recovery_service")
@patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
def test_auth_session_released_before_signin_and_recovery(
    mock_sign_in, mock_get_service, tmp_path
):
    """A REAL auth dependency opens request.state._db_session; it must be closed
    (None) before sign_in and remain None during execute_recovery.

    This uses the productive _get_request_session + SqlUserRepository so the bug
    (session held open across remote calls) would be detectable — a plain User
    override never opens the session and would not catch it.
    """
    from app.dependencies import _get_request_session
    from src.infrastructure.persistence.repositories.sql_user_repository import (
        SqlUserRepository,
    )

    captured = {}

    from fastapi import Request as _Request

    async def _real_auth_dependency(request: _Request):
        # Opens the request-scoped session and loads a real user through the ORM.
        session = _get_request_session(request)
        captured["request"] = request
        captured["auth_session"] = session
        # Force a read so the session begins a transaction.
        user = SqlUserRepository(session=session).get_by_id(captured["local_id"])
        captured["in_txn_during_auth"] = session.in_transaction()
        return user

    def _sign_in_probe(email, password):
        # At sign_in time the auth session MUST already be released.
        req = captured["request"]
        captured["db_session_at_signin"] = getattr(req.state, "_db_session", None)
        captured["auth_session_closed_at_signin"] = not captured[
            "auth_session"
        ].in_transaction()
        return _auth_ok()

    mock_sign_in.side_effect = _sign_in_probe

    service = MagicMock()

    def _execute_recovery(*args, **kwargs):
        req = captured["request"]
        captured["db_session_during_recovery"] = getattr(
            req.state, "_db_session", None
        )
        return _recovery_result(success=True)

    service.execute_recovery.side_effect = _execute_recovery
    mock_get_service.return_value = service

    runtime = SyncRuntimeState()
    registry = MagicMock()
    registry.has_any_live_thread.return_value = False

    # Isolate persistence: use a throwaway DatabaseManager on a tmp_path DB so
    # the seeded user never touches the productive/default SQLite file. The
    # productive lifespan still builds app.state.db_manager on startup; we swap
    # it for the temp manager for the duration of the request and always restore
    # it in finally.
    from src.infrastructure.persistence.database import DatabaseManager

    temp_manager = DatabaseManager(
        db_path=str(tmp_path / "recovery-session-boundary.db")
    )
    temp_manager.init_db()

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[require_current_user_api] = _real_auth_dependency
    app.dependency_overrides[get_sync_runtime_state] = lambda: runtime
    app.dependency_overrides[get_monitoring_runtime_registry] = lambda: registry
    try:
        with TestClient(app) as client:
            original_manager = app.state.db_manager
            try:
                # Swap in the temp manager BEFORE seeding and the POST so both
                # the seed and the real auth dependency (via
                # request.app.state.db_manager) use the isolated DB.
                app.state.db_manager = temp_manager
                app.state.supabase_config = _make_supabase_config()
                captured["local_id"] = _seed_local_user(temp_manager)
                assert app.state.db_manager is temp_manager
                resp = client.post("/api/recovery/trigger", json={"password": "x"})
            finally:
                app.state.db_manager = original_manager
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        try:
            temp_manager.engine.dispose()
        except Exception:
            pass

    assert resp.status_code == 200
    # Session WAS active (in a transaction) during local auth.
    assert captured["in_txn_during_auth"] is True
    # Released BEFORE sign_in.
    assert captured["db_session_at_signin"] is None
    assert captured["auth_session_closed_at_signin"] is True
    # Still None during execute_recovery.
    assert captured["db_session_during_recovery"] is None
    # current_user remained usable after release (execute_recovery got real args).
    service.execute_recovery.assert_called_once_with(
        "ephemeral-jwt", REMOTE_UUID, captured["local_id"]
    )


def test_release_request_db_session_is_idempotent():
    """release_request_db_session is a no-op when no session, and safe twice."""
    from app.dependencies import release_request_db_session

    class _State:
        pass

    class _Req:
        def __init__(self):
            self.state = _State()

    req = _Req()
    # No _db_session attribute at all -> no error.
    release_request_db_session(req)
    assert getattr(req.state, "_db_session", None) is None

    # A fake open session gets rolled back + closed, then None; second call ok.
    class _FakeSession:
        def __init__(self):
            self.rolled_back = False
            self.closed = False

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    fake = _FakeSession()
    req.state._db_session = fake
    release_request_db_session(req)
    assert fake.rolled_back is True and fake.closed is True
    assert req.state._db_session is None
    # Idempotent second call.
    release_request_db_session(req)
    assert req.state._db_session is None


@patch("app.routes.recovery_api.get_recovery_service")
@patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
def test_whitespace_access_token_fails_closed(mock_sign_in, mock_get_service):
    from src.application.interfaces.remote_auth_port import RemoteAuthResult

    mock_sign_in.return_value = RemoteAuthResult(
        success=True, user_id=REMOTE_UUID, access_token="   "
    )
    runtime = SyncRuntimeState()
    with _Overrides(runtime=runtime) as ov:
        with _client_with_config(ov) as client:
            resp = client.post("/api/recovery/trigger", json={"password": "x"})
    assert resp.status_code == 502
    mock_get_service.assert_not_called()
    assert runtime.get_active_operation() is None
