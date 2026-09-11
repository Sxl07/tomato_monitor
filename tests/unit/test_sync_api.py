"""Tests for sync API lifecycle, concurrency, and release behavior (Task 13.5).

Covers:
- Monitoring running/analyzing blocks sync (HTTP 409)
- Concurrent sync attempt returns 409
- sign_in failure releases runtime state
- RemoteSyncService exception releases runtime state
- RemoteSyncService success releases and stores result
- Missing remote_user_id returns 400
- Remote identity mismatch blocks sync and releases runtime
- POST success contains counters, no password/JWT leaked
- GET /api/sync/status reflects configuration and progress
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import (
    get_deletion_outbox_repository,
    get_sync_runtime_state,
    get_sync_state_repository,
    get_monitoring_runtime_registry,
    require_current_user_api,
)
from src.application.interfaces.sync_state_port import SyncStatusCounts
from src.application.services.sync_runtime_state import SyncRuntimeState
from src.domain.entities.user import User


class _FakeDeletionOutbox:
    """Minimal DeletionOutboxPort stub exposing get_pending_for_propagation."""

    def __init__(self, pending=None):
        self._pending = list(pending or [])
        self.requested_owner_user_id = None

    def get_pending_for_propagation(self, owner_user_id):
        self.requested_owner_user_id = owner_user_id
        return list(self._pending)


def _outbox_entry(entry_id, status):
    """Build a DeletionOutboxEntry-like object with the fields the route reads."""
    from datetime import datetime, timezone

    from src.application.interfaces.deletion_outbox_port import DeletionOutboxEntry

    return DeletionOutboxEntry(
        id=entry_id,
        entity_type="monitoring",
        entity_local_id=entry_id,
        remote_table="monitorings",
        remote_id="remote-uuid-%d" % entry_id,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        status=status,
        local_delete_status="completed",
        cleanup_status="pending",
        deleted_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        last_error=None,
        retry_count=0,
    )


@pytest.fixture(autouse=True)
def _default_empty_deletion_outbox():
    """By default, the GET /status route sees NO pending deletions.

    The status route now depends on get_deletion_outbox_repository. Without an
    override it would build a real repository against app.state.db_manager,
    making existing status tests depend on durable outbox history. Overriding
    with an empty fake keeps those tests deterministic; A/B tests below set
    their own override explicitly.
    """
    app.dependency_overrides[get_deletion_outbox_repository] = (
        lambda: _FakeDeletionOutbox([])
    )
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_deletion_outbox_repository, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sync_runtime():
    """Fresh SyncRuntimeState for each test."""
    return SyncRuntimeState()


@pytest.fixture
def mock_registry():
    """Mock MonitoringRuntimeRegistry."""
    registry = MagicMock()
    registry.has_any_live_thread.return_value = False
    return registry


@pytest.fixture
def mock_sync_state_repo():
    """Mock SyncStateRepository with default counts."""
    repo = MagicMock()
    repo.get_sync_status_counts.return_value = SyncStatusCounts(
        pending_count=5,
        synced_count=10,
        error_count=1,
        last_sync_at=None,
    )
    return repo


@pytest.fixture
def user_with_remote_id():
    """User with remote_user_id configured."""
    return User(
        id=1,
        full_name="Test Operator",
        email="operator@example.com",
        password_hash="pbkdf2_sha256$260000$aaaa$bbbb",
        role="operator",
        remote_user_id="remote-uuid-123",
    )


@pytest.fixture
def user_without_remote_id():
    """User without remote_user_id."""
    return User(
        id=1,
        full_name="Test Operator",
        email="operator@example.com",
        password_hash="pbkdf2_sha256$260000$aaaa$bbbb",
        role="operator",
        remote_user_id=None,
    )


@pytest.fixture
def sync_client(sync_runtime, mock_registry, mock_sync_state_repo, user_with_remote_id):
    """TestClient with all sync dependencies overridden."""
    app.dependency_overrides[get_sync_runtime_state] = lambda: sync_runtime
    app.dependency_overrides[get_monitoring_runtime_registry] = lambda: mock_registry
    app.dependency_overrides[get_sync_state_repository] = lambda: mock_sync_state_repo
    app.dependency_overrides[require_current_user_api] = lambda: user_with_remote_id

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.pop(get_sync_runtime_state, None)
    app.dependency_overrides.pop(get_monitoring_runtime_registry, None)
    app.dependency_overrides.pop(get_sync_state_repository, None)
    app.dependency_overrides.pop(require_current_user_api, None)


def _make_supabase_config():
    """Create a minimal SupabaseConfig for tests."""
    from src.infrastructure.supabase.supabase_config import SupabaseConfig
    return SupabaseConfig(
        url="https://test.supabase.co",
        publishable_key="pk_test_key",
        storage_bucket="test-bucket",
    )


# ---------------------------------------------------------------------------
# A. Monitoring running → HTTP 409
# ---------------------------------------------------------------------------


class TestMonitoringBlocksSync:
    """Sync is blocked when monitoring is running or analyzing."""

    def test_monitoring_running_blocks_sync(
        self, sync_runtime, mock_registry, mock_sync_state_repo, user_with_remote_id
    ):
        """Active monitoring thread (running) returns 409."""
        mock_registry.has_any_live_thread.return_value = True

        app.dependency_overrides[get_sync_runtime_state] = lambda: sync_runtime
        app.dependency_overrides[get_monitoring_runtime_registry] = lambda: mock_registry
        app.dependency_overrides[get_sync_state_repository] = lambda: mock_sync_state_repo
        app.dependency_overrides[require_current_user_api] = lambda: user_with_remote_id

        with TestClient(app) as client:
            response = client.post(
                "/api/sync/trigger",
                json={"password": "test123"},
            )

        app.dependency_overrides.pop(get_sync_runtime_state, None)
        app.dependency_overrides.pop(get_monitoring_runtime_registry, None)
        app.dependency_overrides.pop(get_sync_state_repository, None)
        app.dependency_overrides.pop(require_current_user_api, None)

        assert response.status_code == 409
        assert "monitoreo" in response.json()["detail"].lower()
        # Runtime was never acquired
        assert sync_runtime.is_syncing is False

    def test_monitoring_analyzing_blocks_sync(
        self, sync_runtime, mock_registry, mock_sync_state_repo, user_with_remote_id
    ):
        """Active monitoring thread (analyzing phase) returns 409."""
        # has_any_live_thread covers both running and analyzing phases
        mock_registry.has_any_live_thread.return_value = True

        app.dependency_overrides[get_sync_runtime_state] = lambda: sync_runtime
        app.dependency_overrides[get_monitoring_runtime_registry] = lambda: mock_registry
        app.dependency_overrides[get_sync_state_repository] = lambda: mock_sync_state_repo
        app.dependency_overrides[require_current_user_api] = lambda: user_with_remote_id

        with TestClient(app) as client:
            response = client.post(
                "/api/sync/trigger",
                json={"password": "test123"},
            )

        app.dependency_overrides.pop(get_sync_runtime_state, None)
        app.dependency_overrides.pop(get_monitoring_runtime_registry, None)
        app.dependency_overrides.pop(get_sync_state_repository, None)
        app.dependency_overrides.pop(require_current_user_api, None)

        assert response.status_code == 409
        assert sync_runtime.is_syncing is False


# ---------------------------------------------------------------------------
# C. Concurrent sync → HTTP 409
# ---------------------------------------------------------------------------


class TestConcurrentSyncBlocked:
    """Second sync request while first is active returns 409."""

    def test_second_sync_returns_409(
        self, sync_runtime, mock_registry, mock_sync_state_repo, user_with_remote_id
    ):
        """When runtime is already acquired, second request gets 409."""
        # Pre-acquire the runtime to simulate an active sync
        assert sync_runtime.try_acquire() is True

        app.dependency_overrides[get_sync_runtime_state] = lambda: sync_runtime
        app.dependency_overrides[get_monitoring_runtime_registry] = lambda: mock_registry
        app.dependency_overrides[get_sync_state_repository] = lambda: mock_sync_state_repo
        app.dependency_overrides[require_current_user_api] = lambda: user_with_remote_id

        with TestClient(app) as client:
            response = client.post(
                "/api/sync/trigger",
                json={"password": "test123"},
            )

        app.dependency_overrides.pop(get_sync_runtime_state, None)
        app.dependency_overrides.pop(get_monitoring_runtime_registry, None)
        app.dependency_overrides.pop(get_sync_state_repository, None)
        app.dependency_overrides.pop(require_current_user_api, None)

        assert response.status_code == 409
        assert "en curso" in response.json()["detail"].lower()
        # Still acquired (not released by 409 path — was pre-acquired externally)
        assert sync_runtime.is_syncing is True

        # Cleanup
        sync_runtime.release(None)


# ---------------------------------------------------------------------------
# D. sign_in fails after acquire → release
# ---------------------------------------------------------------------------


class TestSignInFailureReleasesRuntime:
    """Runtime is released when sign_in fails."""

    @patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter")
    def test_sign_in_failure_releases_runtime(
        self, mock_adapter_cls, sync_client, sync_runtime
    ):
        """sign_in failure returns 401 and releases runtime state."""
        from src.application.interfaces.remote_auth_port import RemoteAuthResult

        # Configure supabase_config on app.state
        app.state.supabase_config = _make_supabase_config()

        mock_adapter = MagicMock()
        mock_adapter.sign_in.return_value = RemoteAuthResult(
            success=False,
            error_type="INVALID_CREDENTIALS",
            error_message="Invalid login credentials",
        )
        mock_adapter_cls.return_value = mock_adapter

        response = sync_client.post(
            "/api/sync/trigger",
            json={"password": "wrong_password"},
        )

        assert response.status_code == 401
        assert "contraseña" in response.json()["detail"].lower()
        # Runtime was released
        assert sync_runtime.is_syncing is False


# ---------------------------------------------------------------------------
# E. RemoteSyncService raises Exception → release
# ---------------------------------------------------------------------------


class TestSyncServiceExceptionReleasesRuntime:
    """Runtime is released when RemoteSyncService raises."""

    @patch("src.application.services.remote_sync_service.RemoteSyncService.execute_sync")
    @patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
    def test_sync_service_exception_releases_runtime(
        self,
        mock_sign_in,
        mock_execute_sync,
        sync_client,
        sync_runtime,
    ):
        """Exception in execute_sync releases runtime."""
        from src.application.interfaces.remote_auth_port import RemoteAuthResult

        app.state.supabase_config = _make_supabase_config()

        # Auth succeeds
        mock_sign_in.return_value = RemoteAuthResult(
            success=True,
            user_id="remote-uuid-123",
            access_token="ephemeral-jwt-token",
            email="operator@example.com",
        )

        # RemoteSyncService raises
        mock_execute_sync.side_effect = RuntimeError("Connection lost")

        response = sync_client.post(
            "/api/sync/trigger",
            json={"password": "correct_password"},
        )

        assert response.status_code == 500
        # Runtime was released
        assert sync_runtime.is_syncing is False


# ---------------------------------------------------------------------------
# F. RemoteSyncService success → release with result
# ---------------------------------------------------------------------------


class TestSyncServiceSuccess:
    """Successful sync releases runtime with result."""

    @patch("src.application.services.remote_sync_service.RemoteSyncService.execute_sync")
    @patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
    def test_successful_sync_releases_with_result(
        self,
        mock_sign_in,
        mock_execute_sync,
        sync_client,
        sync_runtime,
    ):
        """Successful sync releases runtime and stores last_result."""
        from src.application.interfaces.remote_auth_port import RemoteAuthResult
        from src.application.services.remote_sync_service import SyncResult

        app.state.supabase_config = _make_supabase_config()

        mock_sign_in.return_value = RemoteAuthResult(
            success=True,
            user_id="remote-uuid-123",
            access_token="ephemeral-jwt-token",
            email="operator@example.com",
        )

        mock_execute_sync.return_value = SyncResult(
            success=True,
            entities_synced=20,
            entities_failed=2,
            images_uploaded=5,
            images_failed=0,
            errors=["minor warning"],
            duration_seconds=3.5,
        )

        response = sync_client.post(
            "/api/sync/trigger",
            json={"password": "correct_password"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["entities_synced"] == 20
        assert data["entities_failed"] == 2
        assert data["images_uploaded"] == 5
        assert data["images_failed"] == 0
        assert data["duration_seconds"] == 3.5
        assert "minor warning" in data["errors"]

        # Runtime released
        assert sync_runtime.is_syncing is False
        # last_result stored
        assert sync_runtime.last_result is not None
        assert sync_runtime.last_result["success"] is True
        assert sync_runtime.last_result["entities_synced"] == 20


# ---------------------------------------------------------------------------
# G. remote_user_id absent → HTTP 400
# ---------------------------------------------------------------------------


class TestMissingRemoteUserId:
    """User without remote_user_id cannot trigger sync."""

    def test_no_remote_user_id_returns_400(
        self, sync_runtime, mock_registry, mock_sync_state_repo, user_without_remote_id
    ):
        """HTTP 400 when user has no remote_user_id."""
        app.dependency_overrides[get_sync_runtime_state] = lambda: sync_runtime
        app.dependency_overrides[get_monitoring_runtime_registry] = lambda: mock_registry
        app.dependency_overrides[get_sync_state_repository] = lambda: mock_sync_state_repo
        app.dependency_overrides[require_current_user_api] = lambda: user_without_remote_id

        with TestClient(app) as client:
            response = client.post(
                "/api/sync/trigger",
                json={"password": "test123"},
            )

        app.dependency_overrides.pop(get_sync_runtime_state, None)
        app.dependency_overrides.pop(get_monitoring_runtime_registry, None)
        app.dependency_overrides.pop(get_sync_state_repository, None)
        app.dependency_overrides.pop(require_current_user_api, None)

        assert response.status_code == 400
        assert "remoto" in response.json()["detail"].lower()
        # No sign_in called, no runtime acquired
        assert sync_runtime.is_syncing is False


# ---------------------------------------------------------------------------
# H. Remote identity mismatch → sync NOT executed, runtime released
# ---------------------------------------------------------------------------


class TestRemoteIdentityMismatch:
    """Sync is blocked and runtime released on identity mismatch."""

    @patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
    def test_identity_mismatch_releases_runtime(
        self, mock_sign_in, sync_client, sync_runtime
    ):
        """Remote user_id differs from local → 403 and runtime released."""
        from src.application.interfaces.remote_auth_port import RemoteAuthResult

        app.state.supabase_config = _make_supabase_config()

        mock_sign_in.return_value = RemoteAuthResult(
            success=True,
            user_id="different-remote-uuid-999",  # Does NOT match remote-uuid-123
            access_token="ephemeral-jwt-token",
            email="operator@example.com",
        )

        response = sync_client.post(
            "/api/sync/trigger",
            json={"password": "correct_password"},
        )

        assert response.status_code == 403
        assert "identidad" in response.json()["detail"].lower()
        # Runtime released
        assert sync_runtime.is_syncing is False


# ---------------------------------------------------------------------------
# I. POST success — no password/JWT in response
# ---------------------------------------------------------------------------


class TestNoSecretsInResponse:
    """Secrets are never leaked in the response body."""

    @patch("src.application.services.remote_sync_service.RemoteSyncService.execute_sync")
    @patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
    def test_no_secrets_in_response(
        self,
        mock_sign_in,
        mock_execute_sync,
        sync_client,
        sync_runtime,
    ):
        """Response JSON does not contain password or JWT."""
        from src.application.interfaces.remote_auth_port import RemoteAuthResult
        from src.application.services.remote_sync_service import SyncResult

        app.state.supabase_config = _make_supabase_config()

        mock_sign_in.return_value = RemoteAuthResult(
            success=True,
            user_id="remote-uuid-123",
            access_token="super-secret-jwt-token-xyz",
            email="operator@example.com",
        )

        mock_execute_sync.return_value = SyncResult(
            success=True,
            entities_synced=10,
            entities_failed=0,
            images_uploaded=2,
            images_failed=0,
            errors=[],
            duration_seconds=1.0,
        )

        response = sync_client.post(
            "/api/sync/trigger",
            json={"password": "my_secret_password"},
        )

        assert response.status_code == 200
        response_text = response.text
        assert "my_secret_password" not in response_text
        assert "super-secret-jwt-token-xyz" not in response_text


# ---------------------------------------------------------------------------
# J. GET /api/sync/status
# ---------------------------------------------------------------------------


class TestGetSyncStatus:
    """GET /api/sync/status returns configuration and progress."""

    def test_status_reflects_configured_state(
        self, sync_runtime, mock_registry, mock_sync_state_repo, user_with_remote_id
    ):
        """Status shows supabase configured and user remote id."""
        app.dependency_overrides[get_sync_runtime_state] = lambda: sync_runtime
        app.dependency_overrides[get_monitoring_runtime_registry] = lambda: mock_registry
        app.dependency_overrides[get_sync_state_repository] = lambda: mock_sync_state_repo
        app.dependency_overrides[require_current_user_api] = lambda: user_with_remote_id

        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            response = client.get("/api/sync/status")

        app.dependency_overrides.pop(get_sync_runtime_state, None)
        app.dependency_overrides.pop(get_monitoring_runtime_registry, None)
        app.dependency_overrides.pop(get_sync_state_repository, None)
        app.dependency_overrides.pop(require_current_user_api, None)

        assert response.status_code == 200
        data = response.json()
        assert data["supabase_configured"] is True
        assert data["user_has_remote_id"] is True
        assert data["is_syncing"] is False
        assert data["pending_count"] == 5
        assert data["synced_count"] == 10
        assert data["error_count"] == 1
        assert data["current_progress"] is None

    def test_status_shows_progress_when_syncing(
        self, sync_runtime, mock_registry, mock_sync_state_repo, user_with_remote_id
    ):
        """Status shows current_progress when sync is active."""
        # Simulate active sync
        sync_runtime.try_acquire()
        sync_runtime.update_progress("monitoring", 5, 12)

        app.dependency_overrides[get_sync_runtime_state] = lambda: sync_runtime
        app.dependency_overrides[get_monitoring_runtime_registry] = lambda: mock_registry
        app.dependency_overrides[get_sync_state_repository] = lambda: mock_sync_state_repo
        app.dependency_overrides[require_current_user_api] = lambda: user_with_remote_id

        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            response = client.get("/api/sync/status")

        app.dependency_overrides.pop(get_sync_runtime_state, None)
        app.dependency_overrides.pop(get_monitoring_runtime_registry, None)
        app.dependency_overrides.pop(get_sync_state_repository, None)
        app.dependency_overrides.pop(require_current_user_api, None)

        assert response.status_code == 200
        data = response.json()
        assert data["is_syncing"] is True
        assert data["current_progress"] == {
            "phase": "monitoring",
            "processed": 5,
            "total": 12,
        }

        # Cleanup
        sync_runtime.release(None)

    def test_status_without_supabase_config(
        self, sync_runtime, mock_registry, mock_sync_state_repo, user_with_remote_id
    ):
        """Status shows supabase_configured=False when config is None."""
        app.dependency_overrides[get_sync_runtime_state] = lambda: sync_runtime
        app.dependency_overrides[get_monitoring_runtime_registry] = lambda: mock_registry
        app.dependency_overrides[get_sync_state_repository] = lambda: mock_sync_state_repo
        app.dependency_overrides[require_current_user_api] = lambda: user_with_remote_id

        with TestClient(app) as client:
            app.state.supabase_config = None
            response = client.get("/api/sync/status")

        app.dependency_overrides.pop(get_sync_runtime_state, None)
        app.dependency_overrides.pop(get_monitoring_runtime_registry, None)
        app.dependency_overrides.pop(get_sync_state_repository, None)
        app.dependency_overrides.pop(require_current_user_api, None)

        assert response.status_code == 200
        data = response.json()
        assert data["supabase_configured"] is False

    def test_status_user_without_remote_id(
        self, sync_runtime, mock_registry, mock_sync_state_repo, user_without_remote_id
    ):
        """Status shows user_has_remote_id=False for user without remote id."""
        app.dependency_overrides[get_sync_runtime_state] = lambda: sync_runtime
        app.dependency_overrides[get_monitoring_runtime_registry] = lambda: mock_registry
        app.dependency_overrides[get_sync_state_repository] = lambda: mock_sync_state_repo
        app.dependency_overrides[require_current_user_api] = lambda: user_without_remote_id

        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            response = client.get("/api/sync/status")

        app.dependency_overrides.pop(get_sync_runtime_state, None)
        app.dependency_overrides.pop(get_monitoring_runtime_registry, None)
        app.dependency_overrides.pop(get_sync_state_repository, None)
        app.dependency_overrides.pop(require_current_user_api, None)

        assert response.status_code == 200
        data = response.json()
        assert data["user_has_remote_id"] is False


# ---------------------------------------------------------------------------
# Authentication required
# ---------------------------------------------------------------------------


class TestSyncApiRequiresAuth:
    """Both endpoints require authentication."""

    def test_trigger_requires_auth(self):
        """POST /api/sync/trigger returns 401 without auth."""
        # Clear ALL overrides to test real auth
        saved = dict(app.dependency_overrides)
        app.dependency_overrides.clear()
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/api/sync/trigger",
                    json={"password": "test"},
                )
            assert response.status_code == 401
        finally:
            app.dependency_overrides.update(saved)

    def test_status_requires_auth(self):
        """GET /api/sync/status returns 401 without auth."""
        saved = dict(app.dependency_overrides)
        app.dependency_overrides.clear()
        try:
            with TestClient(app) as client:
                response = client.get("/api/sync/status")
            assert response.status_code == 401
        finally:
            app.dependency_overrides.update(saved)


# ---------------------------------------------------------------------------
# last_sync_at UTC explicit (Z suffix)
# ---------------------------------------------------------------------------


class TestLastSyncAtUtc:
    """last_sync_at in GET /api/sync/status uses UTC with Z suffix."""

    def test_last_sync_at_has_z_suffix(
        self, sync_runtime, mock_registry, mock_sync_state_repo, user_with_remote_id
    ):
        """Naive datetime is formatted as ISO with Z suffix."""
        from datetime import datetime

        mock_sync_state_repo.get_sync_status_counts.return_value = SyncStatusCounts(
            pending_count=0,
            synced_count=5,
            error_count=0,
            last_sync_at=datetime(2026, 8, 26, 1, 30, 0),  # naive — UTC by convention
        )

        app.dependency_overrides[get_sync_runtime_state] = lambda: sync_runtime
        app.dependency_overrides[get_monitoring_runtime_registry] = lambda: mock_registry
        app.dependency_overrides[get_sync_state_repository] = lambda: mock_sync_state_repo
        app.dependency_overrides[require_current_user_api] = lambda: user_with_remote_id

        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            response = client.get("/api/sync/status")

        app.dependency_overrides.pop(get_sync_runtime_state, None)
        app.dependency_overrides.pop(get_monitoring_runtime_registry, None)
        app.dependency_overrides.pop(get_sync_state_repository, None)
        app.dependency_overrides.pop(require_current_user_api, None)

        assert response.status_code == 200
        data = response.json()
        assert data["last_sync_at"] == "2026-08-26T01:30:00Z"

    def test_last_sync_at_null_when_never_synced(
        self, sync_runtime, mock_registry, mock_sync_state_repo, user_with_remote_id
    ):
        """last_sync_at is null when no entities have been synced."""
        mock_sync_state_repo.get_sync_status_counts.return_value = SyncStatusCounts(
            pending_count=3,
            synced_count=0,
            error_count=0,
            last_sync_at=None,
        )

        app.dependency_overrides[get_sync_runtime_state] = lambda: sync_runtime
        app.dependency_overrides[get_monitoring_runtime_registry] = lambda: mock_registry
        app.dependency_overrides[get_sync_state_repository] = lambda: mock_sync_state_repo
        app.dependency_overrides[require_current_user_api] = lambda: user_with_remote_id

        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            response = client.get("/api/sync/status")

        app.dependency_overrides.pop(get_sync_runtime_state, None)
        app.dependency_overrides.pop(get_monitoring_runtime_registry, None)
        app.dependency_overrides.pop(get_sync_state_repository, None)
        app.dependency_overrides.pop(require_current_user_api, None)

        assert response.status_code == 200
        assert response.json()["last_sync_at"] is None


# ---------------------------------------------------------------------------
# Completed monitoring allows sync (positive path)
# ---------------------------------------------------------------------------


class TestCompletedMonitoringAllowsSync:
    """Sync is allowed when no live monitoring thread exists."""

    @patch("src.application.services.remote_sync_service.RemoteSyncService.execute_sync")
    @patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
    def test_no_active_monitoring_allows_sync(
        self,
        mock_sign_in,
        mock_execute_sync,
        sync_client,
        sync_runtime,
        mock_registry,
    ):
        """With no live thread (monitoring completed), sync proceeds to 200."""
        from src.application.interfaces.remote_auth_port import RemoteAuthResult
        from src.application.services.remote_sync_service import SyncResult

        app.state.supabase_config = _make_supabase_config()

        # Explicitly confirm no active monitoring
        mock_registry.has_any_live_thread.return_value = False

        mock_sign_in.return_value = RemoteAuthResult(
            success=True,
            user_id="remote-uuid-123",
            access_token="ephemeral-jwt",
            email="operator@example.com",
        )

        mock_execute_sync.return_value = SyncResult(
            success=True,
            entities_synced=8,
            entities_failed=0,
            images_uploaded=3,
            images_failed=0,
            errors=[],
            duration_seconds=2.1,
        )

        response = sync_client.post(
            "/api/sync/trigger",
            json={"password": "correct_password"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["entities_synced"] == 8

        # Service was called exactly once
        mock_execute_sync.assert_called_once_with("ephemeral-jwt", "remote-uuid-123")

        # Runtime released
        assert sync_runtime.is_syncing is False
        assert sync_runtime.last_result is not None
        assert sync_runtime.last_result["success"] is True


# ---------------------------------------------------------------------------
# Password/JWT not in logs (caplog validation)
# ---------------------------------------------------------------------------


class TestSecretsNotInLogs:
    """Password and JWT must never appear in log output."""

    @patch("src.application.services.remote_sync_service.RemoteSyncService.execute_sync")
    @patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
    def test_password_and_jwt_not_in_logs(
        self,
        mock_sign_in,
        mock_execute_sync,
        sync_client,
        sync_runtime,
        caplog,
    ):
        """Sentinel password and JWT do not appear in any log record."""
        from src.application.interfaces.remote_auth_port import RemoteAuthResult
        from src.application.services.remote_sync_service import SyncResult
        import logging

        app.state.supabase_config = _make_supabase_config()

        password_sentinel = "SUPER_SECRET_PASSWORD_DO_NOT_LOG"
        jwt_sentinel = "SUPER_SECRET_JWT_DO_NOT_LOG"

        mock_sign_in.return_value = RemoteAuthResult(
            success=True,
            user_id="remote-uuid-123",
            access_token=jwt_sentinel,
            email="operator@example.com",
        )

        mock_execute_sync.return_value = SyncResult(
            success=True,
            entities_synced=5,
            entities_failed=0,
            images_uploaded=1,
            images_failed=0,
            errors=[],
            duration_seconds=0.5,
        )

        with caplog.at_level(logging.DEBUG):
            response = sync_client.post(
                "/api/sync/trigger",
                json={"password": password_sentinel},
            )

        assert response.status_code == 200

        # Check all log records
        full_log_text = caplog.text
        assert password_sentinel not in full_log_text, (
            "Password sentinel found in logs"
        )
        assert jwt_sentinel not in full_log_text, (
            "JWT sentinel found in logs"
        )

    @patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
    def test_password_not_in_logs_on_auth_failure(
        self,
        mock_sign_in,
        sync_client,
        sync_runtime,
        caplog,
    ):
        """Password is not logged even when authentication fails."""
        from src.application.interfaces.remote_auth_port import RemoteAuthResult
        import logging

        app.state.supabase_config = _make_supabase_config()

        password_sentinel = "SUPER_SECRET_PASSWORD_DO_NOT_LOG"

        mock_sign_in.return_value = RemoteAuthResult(
            success=False,
            error_type="INVALID_CREDENTIALS",
            error_message="Invalid login credentials",
        )

        with caplog.at_level(logging.DEBUG):
            response = sync_client.post(
                "/api/sync/trigger",
                json={"password": password_sentinel},
            )

        assert response.status_code == 401

        full_log_text = caplog.text
        assert password_sentinel not in full_log_text, (
            "Password sentinel found in logs on auth failure path"
        )


# ---------------------------------------------------------------------------
# Observability logging tests (Task 16.2)
# ---------------------------------------------------------------------------


class TestSyncLogging:
    """Verify structured safe logging for sync operations."""

    @patch("src.application.services.remote_sync_service.RemoteSyncService.execute_sync")
    @patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
    def test_logs_contain_start_and_complete_indicators(
        self,
        mock_sign_in,
        mock_execute_sync,
        sync_client,
        sync_runtime,
        caplog,
    ):
        """Successful sync logs start and complete messages."""
        from src.application.interfaces.remote_auth_port import RemoteAuthResult
        from src.application.services.remote_sync_service import SyncResult
        import logging

        app.state.supabase_config = _make_supabase_config()

        mock_sign_in.return_value = RemoteAuthResult(
            success=True,
            user_id="remote-uuid-123",
            access_token="jwt-token",
            email="operator@example.com",
        )
        mock_execute_sync.return_value = SyncResult(
            success=True,
            entities_synced=10,
            entities_failed=0,
            images_uploaded=2,
            images_failed=0,
            errors=[],
            duration_seconds=1.5,
        )

        with caplog.at_level(logging.DEBUG):
            response = sync_client.post(
                "/api/sync/trigger",
                json={"password": "test123"},
            )

        assert response.status_code == 200
        assert "Remote sync started" in caplog.text
        assert "Remote sync completed" in caplog.text

    @patch("src.application.services.remote_sync_service.RemoteSyncService.execute_sync")
    @patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
    def test_completion_log_contains_safe_counters(
        self,
        mock_sign_in,
        mock_execute_sync,
        sync_client,
        sync_runtime,
        caplog,
    ):
        """Completion log includes counters but no secrets."""
        from src.application.interfaces.remote_auth_port import RemoteAuthResult
        from src.application.services.remote_sync_service import SyncResult
        import logging

        app.state.supabase_config = _make_supabase_config()

        mock_sign_in.return_value = RemoteAuthResult(
            success=True,
            user_id="remote-uuid-123",
            access_token="jwt-token",
            email="operator@example.com",
        )
        mock_execute_sync.return_value = SyncResult(
            success=True,
            entities_synced=15,
            entities_failed=3,
            images_uploaded=4,
            images_failed=1,
            errors=["some error", "another error"],
            duration_seconds=2.5,
        )

        with caplog.at_level(logging.DEBUG):
            sync_client.post(
                "/api/sync/trigger",
                json={"password": "test123"},
            )

        assert "entities_synced=15" in caplog.text
        assert "entities_failed=3" in caplog.text
        assert "images_uploaded=4" in caplog.text
        assert "images_failed=1" in caplog.text
        assert "errors_count=2" in caplog.text

    @patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
    def test_auth_failure_logs_error_type_only(
        self,
        mock_sign_in,
        sync_client,
        sync_runtime,
        caplog,
    ):
        """Auth failure logs error_type category without password."""
        from src.application.interfaces.remote_auth_port import RemoteAuthResult
        import logging

        app.state.supabase_config = _make_supabase_config()

        mock_sign_in.return_value = RemoteAuthResult(
            success=False,
            error_type="INVALID_CREDENTIALS",
            error_message="Invalid login credentials",
        )

        with caplog.at_level(logging.DEBUG):
            sync_client.post(
                "/api/sync/trigger",
                json={"password": "SUPER_SECRET_PASSWORD_DO_NOT_LOG"},
            )

        assert "INVALID_CREDENTIALS" in caplog.text
        assert "SUPER_SECRET_PASSWORD_DO_NOT_LOG" not in caplog.text

    @patch("src.application.services.remote_sync_service.RemoteSyncService.execute_sync")
    @patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
    def test_unexpected_exception_does_not_log_message(
        self,
        mock_sign_in,
        mock_execute_sync,
        sync_client,
        sync_runtime,
        caplog,
    ):
        """Exception with sensitive message does not appear in logs."""
        from src.application.interfaces.remote_auth_port import RemoteAuthResult
        import logging

        app.state.supabase_config = _make_supabase_config()

        mock_sign_in.return_value = RemoteAuthResult(
            success=True,
            user_id="remote-uuid-123",
            access_token="SUPER_SECRET_JWT_DO_NOT_LOG",
            email="operator@example.com",
        )
        mock_execute_sync.side_effect = RuntimeError(
            "SUPER_SECRET_EXCEPTION_CONTENT"
        )

        with caplog.at_level(logging.DEBUG):
            response = sync_client.post(
                "/api/sync/trigger",
                json={"password": "SUPER_SECRET_PASSWORD_DO_NOT_LOG"},
            )

        assert response.status_code == 500
        # Exception type is logged
        assert "RuntimeError" in caplog.text
        # But sensitive content is NOT logged
        assert "SUPER_SECRET_EXCEPTION_CONTENT" not in caplog.text
        assert "SUPER_SECRET_PASSWORD_DO_NOT_LOG" not in caplog.text
        assert "SUPER_SECRET_JWT_DO_NOT_LOG" not in caplog.text

    @patch("src.application.services.remote_sync_service.RemoteSyncService.execute_sync")
    @patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
    def test_all_sentinel_secrets_absent_from_logs(
        self,
        mock_sign_in,
        mock_execute_sync,
        sync_client,
        sync_runtime,
        caplog,
    ):
        """Combined sentinel check: password, JWT, and key never in logs."""
        from src.application.interfaces.remote_auth_port import RemoteAuthResult
        from src.application.services.remote_sync_service import SyncResult
        import logging

        app.state.supabase_config = _make_supabase_config()

        mock_sign_in.return_value = RemoteAuthResult(
            success=True,
            user_id="remote-uuid-123",
            access_token="SUPER_SECRET_JWT_DO_NOT_LOG",
            email="operator@example.com",
        )
        mock_execute_sync.return_value = SyncResult(
            success=True,
            entities_synced=5,
            entities_failed=0,
            images_uploaded=1,
            images_failed=0,
            errors=[],
            duration_seconds=0.8,
        )

        with caplog.at_level(logging.DEBUG):
            sync_client.post(
                "/api/sync/trigger",
                json={"password": "SUPER_SECRET_PASSWORD_DO_NOT_LOG"},
            )

        assert "SUPER_SECRET_PASSWORD_DO_NOT_LOG" not in caplog.text
        assert "SUPER_SECRET_JWT_DO_NOT_LOG" not in caplog.text
        assert "SUPER_SECRET_KEY_DO_NOT_LOG" not in caplog.text


# ---------------------------------------------------------------------------
# Spec 021 hotfix — Deletion_Outbox reflected in sync status / trigger response
# ---------------------------------------------------------------------------


class TestSyncStatusIncludesDeletionOutbox:
    """GET /api/sync/status folds pending/error deletions into the counts."""

    def test_pending_deletion_counted_when_entities_zero(
        self, sync_runtime, mock_registry, user_with_remote_id
    ):
        """A entity pending=0, but 1 completed+pending outbox -> pending_count == 1."""
        from unittest.mock import MagicMock

        state_repo = MagicMock()
        state_repo.get_sync_status_counts.return_value = SyncStatusCounts(
            pending_count=0, synced_count=0, error_count=0, last_sync_at=None
        )
        outbox = _FakeDeletionOutbox([_outbox_entry(1, "pending")])

        app.dependency_overrides[get_sync_runtime_state] = lambda: sync_runtime
        app.dependency_overrides[get_monitoring_runtime_registry] = lambda: mock_registry
        app.dependency_overrides[get_sync_state_repository] = lambda: state_repo
        app.dependency_overrides[require_current_user_api] = lambda: user_with_remote_id
        app.dependency_overrides[get_deletion_outbox_repository] = lambda: outbox

        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            response = client.get("/api/sync/status")

        app.dependency_overrides.pop(get_sync_runtime_state, None)
        app.dependency_overrides.pop(get_monitoring_runtime_registry, None)
        app.dependency_overrides.pop(get_sync_state_repository, None)
        app.dependency_overrides.pop(require_current_user_api, None)

        assert response.status_code == 200
        data = response.json()
        assert data["pending_count"] == 1
        assert data["error_count"] == 0

    def test_error_deletion_counted_in_pending_and_error(
        self, sync_runtime, mock_registry, user_with_remote_id
    ):
        """A completed+error outbox increments BOTH pending_count and error_count."""
        from unittest.mock import MagicMock

        state_repo = MagicMock()
        state_repo.get_sync_status_counts.return_value = SyncStatusCounts(
            pending_count=2, synced_count=4, error_count=1, last_sync_at=None
        )
        outbox = _FakeDeletionOutbox([_outbox_entry(1, "error")])

        app.dependency_overrides[get_sync_runtime_state] = lambda: sync_runtime
        app.dependency_overrides[get_monitoring_runtime_registry] = lambda: mock_registry
        app.dependency_overrides[get_sync_state_repository] = lambda: state_repo
        app.dependency_overrides[require_current_user_api] = lambda: user_with_remote_id
        app.dependency_overrides[get_deletion_outbox_repository] = lambda: outbox

        with TestClient(app) as client:
            app.state.supabase_config = _make_supabase_config()
            response = client.get("/api/sync/status")

        app.dependency_overrides.pop(get_sync_runtime_state, None)
        app.dependency_overrides.pop(get_monitoring_runtime_registry, None)
        app.dependency_overrides.pop(get_sync_state_repository, None)
        app.dependency_overrides.pop(require_current_user_api, None)

        assert response.status_code == 200
        data = response.json()
        # entity pending(2) + 1 error deletion == 3; entity error(1) + 1 == 2.
        assert data["pending_count"] == 3
        assert data["error_count"] == 2


class TestSyncTriggerReturnsDeletionCounters:
    """POST /api/sync/trigger surfaces deletions_synced / deletions_failed."""

    @patch("src.application.services.remote_sync_service.RemoteSyncService.execute_sync")
    @patch("src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in")
    def test_trigger_response_includes_deletion_counters(
        self, mock_sign_in, mock_execute_sync, sync_client
    ):
        """A DELETE-only sync (0 entities, 1 deletion) is reflected in the JSON."""
        from src.application.interfaces.remote_auth_port import RemoteAuthResult
        from src.application.services.remote_sync_service import SyncResult

        app.state.supabase_config = _make_supabase_config()
        mock_sign_in.return_value = RemoteAuthResult(
            success=True,
            user_id="remote-uuid-123",
            access_token="ephemeral-jwt-token",
            email="operator@example.com",
        )
        mock_execute_sync.return_value = SyncResult(
            success=True,
            entities_synced=0,
            entities_failed=0,
            images_uploaded=0,
            images_failed=0,
            errors=[],
            duration_seconds=0.2,
            deletions_synced=1,
            deletions_failed=0,
        )

        response = sync_client.post(
            "/api/sync/trigger", json={"password": "correct_password"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["deletions_synced"] == 1
        assert data["deletions_failed"] == 0
        assert data["entities_synced"] == 0


# ---------------------------------------------------------------------------
# Recovery active blocks sync (Spec 022, block E)
# ---------------------------------------------------------------------------


class TestRecoveryBlocksSync:
    """A manual recovery in progress blocks POST /api/sync/trigger with 409."""

    @patch("src.application.services.remote_sync_service.RemoteSyncService")
    @patch(
        "src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in"
    )
    def test_sync_blocked_while_recovery_active(
        self,
        mock_sign_in,
        mock_service_cls,
        sync_runtime,
        mock_registry,
        mock_sync_state_repo,
        user_with_remote_id,
    ):
        # Recovery is currently holding the shared cloud-operation lock.
        assert sync_runtime.try_acquire_recovery() is True

        app.dependency_overrides[get_sync_runtime_state] = lambda: sync_runtime
        app.dependency_overrides[get_monitoring_runtime_registry] = lambda: mock_registry
        app.dependency_overrides[get_sync_state_repository] = lambda: mock_sync_state_repo
        app.dependency_overrides[require_current_user_api] = lambda: user_with_remote_id

        try:
            with TestClient(app) as client:
                app.state.supabase_config = _make_supabase_config()
                response = client.post(
                    "/api/sync/trigger", json={"password": "secret"}
                )

            assert response.status_code == 409
            # Detail indicates a recovery is in progress.
            assert "recuperación" in response.json()["detail"].lower()
            # Neither sign_in nor RemoteSyncService were invoked.
            mock_sign_in.assert_not_called()
            mock_service_cls.assert_not_called()
            # Recovery lock is still held (sync did not release it).
            assert sync_runtime.get_active_operation() == "recovery"
        finally:
            # Cleanup: release recovery so no state leaks to other tests.
            sync_runtime.release_recovery()
            app.dependency_overrides.pop(get_sync_runtime_state, None)
            app.dependency_overrides.pop(get_monitoring_runtime_registry, None)
            app.dependency_overrides.pop(get_sync_state_repository, None)
            app.dependency_overrides.pop(require_current_user_api, None)
