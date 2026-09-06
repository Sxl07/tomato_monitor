"""Productive wiring test for the manual sync trigger (Spec 021, defect fix).

Proves that the PRODUCTIVE sync path (POST /api/sync/trigger in
app/routes/sync_api.py) constructs RemoteSyncService with a real / non-None
``deletion_outbox``, so FASE 0 (durable-deletion propagation) is NOT a no-op in
production. Testing RemoteSyncService in isolation is insufficient — this test
exercises the route wiring end-to-end (with the remote calls faked/offline).

The test stays offline: SupabaseAuthAdapter.sign_in and RemoteSyncService are
patched, and the deletion-outbox dependency is overridden with a sentinel so we
can assert exactly what the route passed to the RemoteSyncService constructor.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import (
    get_deletion_outbox_repository,
    get_monitoring_runtime_registry,
    get_sync_runtime_state,
    get_sync_state_repository,
    require_current_user_api,
)
from src.application.interfaces.sync_state_port import SyncStatusCounts
from src.application.services.sync_runtime_state import SyncRuntimeState
from src.domain.entities.user import User


def _make_supabase_config():
    from src.infrastructure.supabase.supabase_config import SupabaseConfig

    return SupabaseConfig(
        url="https://test.supabase.co",
        publishable_key="test-key",
        storage_bucket="test-bucket",
    )


@pytest.fixture
def user_with_remote_id():
    return User(
        id=1,
        full_name="Test Operator",
        email="operator@example.com",
        password_hash="pbkdf2_sha256$260000$aaaa$bbbb",
        role="operator",
        remote_user_id="remote-uuid-123",
    )


class TestSyncTriggerWiresDeletionOutbox:
    """The productive trigger constructs RemoteSyncService with a real outbox."""

    @patch("src.application.services.remote_sync_service.RemoteSyncService")
    @patch(
        "src.infrastructure.supabase.supabase_auth_adapter.SupabaseAuthAdapter.sign_in"
    )
    def test_trigger_builds_service_with_non_none_deletion_outbox(
        self, mock_sign_in, mock_service_cls, user_with_remote_id
    ):
        from src.application.interfaces.remote_auth_port import RemoteAuthResult
        from src.application.services.remote_sync_service import SyncResult

        # Auth succeeds and the remote identity matches the local user.
        mock_sign_in.return_value = RemoteAuthResult(
            success=True,
            user_id="remote-uuid-123",
            access_token="ephemeral-jwt",
        )

        # Capture the kwargs the route passes to the RemoteSyncService ctor and
        # return a spy instance whose execute_sync yields a minimal SyncResult.
        captured = {}

        def _fake_ctor(**kwargs):
            captured.update(kwargs)
            instance = MagicMock()
            instance.execute_sync.return_value = SyncResult(
                success=True,
                entities_synced=0,
                entities_failed=0,
                images_uploaded=0,
                images_failed=0,
                errors=[],
                duration_seconds=0.0,
            )
            return instance

        mock_service_cls.side_effect = _fake_ctor

        sentinel_outbox = object()

        sync_runtime = SyncRuntimeState()
        registry = MagicMock()
        registry.has_any_live_thread.return_value = False
        sync_state_repo = MagicMock()
        sync_state_repo.get_sync_status_counts.return_value = SyncStatusCounts()

        previous = dict(app.dependency_overrides)
        app.dependency_overrides[get_sync_runtime_state] = lambda: sync_runtime
        app.dependency_overrides[get_monitoring_runtime_registry] = lambda: registry
        app.dependency_overrides[get_sync_state_repository] = lambda: sync_state_repo
        app.dependency_overrides[require_current_user_api] = lambda: user_with_remote_id
        app.dependency_overrides[get_deletion_outbox_repository] = lambda: sentinel_outbox
        try:
            with TestClient(app) as client:
                app.state.supabase_config = _make_supabase_config()
                response = client.post(
                    "/api/sync/trigger", json={"password": "secret-pass"}
                )
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)

        assert response.status_code == 200
        # The productive path constructed RemoteSyncService exactly once and
        # passed a non-None deletion_outbox (the injected repository), proving
        # FASE 0 is wired in production.
        mock_service_cls.assert_called_once()
        assert "deletion_outbox" in captured
        assert captured["deletion_outbox"] is not None
        assert captured["deletion_outbox"] is sentinel_outbox
