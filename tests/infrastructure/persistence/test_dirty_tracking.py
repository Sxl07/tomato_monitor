"""Tests for dirty tracking: synced entities edited → pending for re-sync.

Validates:
- Greenhouse synced + real change → pending, same remote_id, error cleared
- Module synced + real change → pending, same remote_id, error cleared
- No-op update (same values) → synced stays synced
- Dirty entity syncs with same UUID via RemoteSyncService
- mark_synced does not trigger dirty tracking

Spec 017 — Task 13.6.
"""

import copy
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.application.interfaces.remote_data_port import RemoteUpsertResult
from src.application.interfaces.remote_storage_port import RemoteUploadResult
from src.application.interfaces.sync_state_port import StoragePaths
from src.application.services.remote_sync_service import RemoteSyncService
from src.application.services.sync_runtime_state import SyncRuntimeState
from src.infrastructure.persistence.models.base import Base
from src.infrastructure.persistence.models.greenhouse_model import GreenhouseModel
from src.infrastructure.persistence.models.module_model import ModuleModel
from src.infrastructure.persistence.repositories.sql_greenhouse_repository import (
    SqlGreenhouseRepository,
)
from src.infrastructure.persistence.repositories.sql_module_repository import (
    SqlModuleRepository,
)
from src.infrastructure.persistence.sync_state_repository import SyncStateRepository
from src.domain.entities.greenhouse import Greenhouse
from src.domain.entities.module import Module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    @event.listens_for(eng, "connect")
    def set_pragma(dbapi_conn, conn_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


@pytest.fixture
def gh_repo(session_factory):
    return SqlGreenhouseRepository(session=session_factory())


@pytest.fixture
def mod_repo(session_factory):
    return SqlModuleRepository(session=session_factory())


@pytest.fixture
def sync_repo(session_factory):
    return SyncStateRepository(session_factory=session_factory)


# ---------------------------------------------------------------------------
# Greenhouse dirty tracking
# ---------------------------------------------------------------------------


class TestGreenhouseDirtyTracking:
    """Editing a synced greenhouse marks it pending with same remote_id."""

    def test_real_change_marks_pending(self, gh_repo, sync_repo):
        # Create and sync a greenhouse
        gh = gh_repo.create(Greenhouse(name="GH Original"))
        sync_repo.reserve_remote_id("greenhouse", gh.id, "gh-uuid-aaa")
        sync_repo.mark_synced("greenhouse", gh.id, "gh-uuid-aaa")

        # Edit domain field
        gh_repo.update(gh.id, "GH Modified", gh.location)

        # Check via fresh session
        remote_id = sync_repo.get_remote_id("greenhouse", gh.id)
        pending = sync_repo.get_pending_entities("greenhouse")

        assert remote_id == "gh-uuid-aaa"  # preserved
        assert len(pending) == 1
        assert pending[0]["remote_sync_status"] == "pending"
        assert pending[0]["remote_id"] == "gh-uuid-aaa"

    def test_error_cleared_on_dirty(self, gh_repo, sync_repo):
        gh = gh_repo.create(Greenhouse(name="GH"))
        sync_repo.reserve_remote_id("greenhouse", gh.id, "gh-uuid")
        sync_repo.mark_error("greenhouse", gh.id, "previous error")

        # mark_synced first, then edit
        sync_repo.mark_synced("greenhouse", gh.id, "gh-uuid")
        gh_repo.update(gh.id, "GH Changed", None)

        pending = sync_repo.get_pending_entities("greenhouse")
        assert len(pending) == 1
        assert pending[0]["remote_sync_error"] is None

    def test_no_change_stays_synced(self, gh_repo, sync_repo):
        gh = gh_repo.create(Greenhouse(name="GH Same", location="Norte"))
        sync_repo.reserve_remote_id("greenhouse", gh.id, "gh-uuid")
        sync_repo.mark_synced("greenhouse", gh.id, "gh-uuid")

        # Update with SAME values
        gh_repo.update(gh.id, "GH Same", "Norte")

        pending = sync_repo.get_pending_entities("greenhouse")
        assert len(pending) == 0  # still synced


# ---------------------------------------------------------------------------
# Module dirty tracking
# ---------------------------------------------------------------------------


class TestModuleDirtyTracking:
    """Editing a synced module marks it pending with same remote_id."""

    def test_real_change_marks_pending(self, gh_repo, mod_repo, sync_repo):
        gh = gh_repo.create(Greenhouse(name="GH"))
        mod = mod_repo.create(gh.id, Module(name="Mod1", greenhouse_id=gh.id))
        sync_repo.reserve_remote_id("module", mod.id, "mod-uuid-bbb")
        sync_repo.mark_synced("module", mod.id, "mod-uuid-bbb")

        # Edit domain field
        mod_repo.update(mod.id, {"name": "Mod1 Modified"})

        remote_id = sync_repo.get_remote_id("module", mod.id)
        pending = sync_repo.get_pending_entities("module")

        assert remote_id == "mod-uuid-bbb"
        assert len(pending) == 1
        assert pending[0]["remote_sync_status"] == "pending"

    def test_no_change_stays_synced(self, gh_repo, mod_repo, sync_repo):
        gh = gh_repo.create(Greenhouse(name="GH"))
        mod = mod_repo.create(gh.id, Module(name="Mod1", greenhouse_id=gh.id, crop_type="Tomate Cherry"))
        sync_repo.reserve_remote_id("module", mod.id, "mod-uuid")
        sync_repo.mark_synced("module", mod.id, "mod-uuid")

        # Same values
        mod_repo.update(mod.id, {"name": "Mod1", "crop_type": "Tomate Cherry"})

        pending = sync_repo.get_pending_entities("module")
        assert len(pending) == 0


# ---------------------------------------------------------------------------
# Dirty entity syncs with same UUID
# ---------------------------------------------------------------------------


class FakeSyncStateForDirty:
    """Minimal fake for RemoteSyncService test of dirty entity."""

    def __init__(self, entity):
        self._entity = entity
        self._status = entity.get("remote_sync_status", "pending")

    def get_pending_entities(self, entity_type):
        if entity_type == "greenhouse" and self._status in ("pending", "error", "syncing"):
            return [dict(self._entity)]
        return []

    def get_remote_id(self, entity_type, local_id):
        if entity_type == "greenhouse" and local_id == self._entity["id"]:
            return self._entity.get("remote_id")
        return None

    def get_user_remote_id(self, local_user_id):
        # The greenhouse belongs to the syncing user.
        return "user-uuid"

    def get_effective_owner_local_user_id(self, entity_type, local_id):
        # Effective local owner matches the session user (own hierarchy).
        return 1

    def get_local_user_id_by_remote_id(self, user_remote_id):
        # Maps the session's remote id back to its local user id.
        return 1

    def reserve_remote_id(self, entity_type, local_id, remote_id):
        self._entity["remote_id"] = remote_id

    def mark_syncing(self, entity_type, local_id):
        self._status = "syncing"

    def mark_synced(self, entity_type, local_id, remote_id):
        self._status = "synced"
        self._entity["remote_sync_status"] = "synced"

    def mark_error(self, entity_type, local_id, error_msg):
        self._status = "error"

    def get_storage_paths(self, s):
        return StoragePaths()

    def set_storage_paths(self, s, r, a):
        pass


class FakeDataForDirty:
    def __init__(self):
        self.calls = []

    def upsert(self, token, table, data):
        self.calls.append({"table": table, "data": copy.deepcopy(data)})
        return RemoteUpsertResult(success=True, remote_id=data["id"])


class FakeStorageForDirty:
    def upload_file(self, token, local, remote):
        return RemoteUploadResult(success=True, object_path=remote)


class TestDirtyEntitySyncsWithSameUuid:
    """A dirty entity (pending with existing remote_id) syncs using that UUID."""

    def test_same_uuid_in_payload(self):
        entity = {
            "id": 1, "owner_user_id": 1, "name": "GH Edited", "location": None,
            "remote_id": "gh-uuid-known",
            "remote_sync_status": "pending",
            "created_at": datetime(2025, 1, 1),
            "updated_at": datetime(2025, 1, 2),
        }
        state = FakeSyncStateForDirty(entity)
        data = FakeDataForDirty()
        svc = RemoteSyncService(data, FakeStorageForDirty(), state, SyncRuntimeState())
        result = svc.execute_sync("jwt", "user-uuid")

        assert result.entities_synced == 1
        assert data.calls[0]["data"]["id"] == "gh-uuid-known"
        assert entity["remote_sync_status"] == "synced"


# ---------------------------------------------------------------------------
# mark_synced does NOT trigger dirty tracking
# ---------------------------------------------------------------------------


class TestMarkSyncedNotDirty:
    """SyncStateRepository.mark_synced leaves entity synced (no re-dirty)."""

    def test_stays_synced(self, gh_repo, sync_repo):
        gh = gh_repo.create(Greenhouse(name="GH"))
        sync_repo.reserve_remote_id("greenhouse", gh.id, "gh-uuid")
        sync_repo.mark_synced("greenhouse", gh.id, "gh-uuid")

        # Confirm synced
        pending = sync_repo.get_pending_entities("greenhouse")
        assert len(pending) == 0

        # Call mark_synced again (simulating re-sync)
        sync_repo.mark_synced("greenhouse", gh.id, "gh-uuid")

        pending = sync_repo.get_pending_entities("greenhouse")
        assert len(pending) == 0  # still synced, not re-dirtied
