"""Targeted tests for Greenhouse ownership (Spec 022, Bloque B).

Covers ONLY:
- Local creation persists owner_user_id (Requirement 1.2) via the repository.
- Repository ownership resolver find_owner_remote_id
  (owner_user_id -> users.id -> users.remote_user_id).
- RemoteSyncService greenhouse payload maps the local owner to the owner's
  remote_user_id (UUID = auth.uid()), never the local integer id.
- Greenhouse with owner_user_id NULL is not uploaded and is reported (error),
  without breaking the rest of the sync (fault isolation).
- Greenhouse whose owner has no remote_user_id is not uploaded and is reported.
- FASE 0 and the existing hierarchical order remain preserved when a valid
  owner is present.
"""

import copy
import os
import tempfile
from datetime import datetime
from typing import Optional

import pytest

from src.application.interfaces.remote_data_port import RemoteUpsertResult
from src.application.interfaces.remote_storage_port import RemoteUploadResult
from src.application.interfaces.sync_state_port import StoragePaths
from src.application.services.remote_sync_service import RemoteSyncService
from src.application.services.sync_runtime_state import SyncRuntimeState
from src.domain.entities.greenhouse import Greenhouse
from src.infrastructure.persistence.database import DatabaseManager
from src.infrastructure.persistence.models.user_model import UserModel
from src.infrastructure.persistence.repositories.sql_greenhouse_repository import (
    SqlGreenhouseRepository,
)


_OWNER_REMOTE_ID = "11111111-2222-4333-8444-555555555555"


# ---------------------------------------------------------------------------
# Local creation with owner (Requirement 1.2)
# ---------------------------------------------------------------------------


def _make_user(session, email: str, remote_user_id: Optional[str]) -> UserModel:
    user = UserModel(
        full_name="Operario",
        email=email,
        password_hash="x",
        remote_user_id=remote_user_id,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


class TestLocalOwnerScopedAccess:
    """Local SQLite isolation: a user only sees/accesses its own greenhouses."""

    def _mgr(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        manager = DatabaseManager(db_path=db_path)
        manager.init_db()
        return manager, db_path

    def _cleanup(self, manager, db_path):
        manager.engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(db_path + suffix):
                os.remove(db_path + suffix)

    def test_two_users_isolation(self):
        manager, db_path = self._mgr()
        session = manager.get_session()
        try:
            user_a = _make_user(session, "a@example.com", "uuid-a")
            user_b = _make_user(session, "b@example.com", "uuid-b")
            repo = SqlGreenhouseRepository(session)

            # Both A and B can have a greenhouse named "USB" (per-owner unique).
            gh_a = repo.create(Greenhouse(name="USB", owner_user_id=user_a.id))
            gh_b = repo.create(Greenhouse(name="USB", owner_user_id=user_b.id))

            # A lists only A; B lists only B.
            a_list = repo.get_all_by_owner(user_a.id)
            b_list = repo.get_all_by_owner(user_b.id)
            assert [g.id for g in a_list] == [gh_a.id]
            assert [g.id for g in b_list] == [gh_b.id]

            # B cannot access A's greenhouse (detail/edit/delete gate helper).
            assert repo.get_by_id_for_owner(gh_a.id, user_b.id) is None
            # B can access its own.
            assert repo.get_by_id_for_owner(gh_b.id, user_b.id).id == gh_b.id
            # A cannot access B's.
            assert repo.get_by_id_for_owner(gh_b.id, user_a.id) is None
        finally:
            session.close()
            self._cleanup(manager, db_path)

    def test_module_create_guard_rejects_foreign_greenhouse(self):
        """The module-create ownership gate (get_by_id_for_owner) rejects
        creating under another user's greenhouse."""
        manager, db_path = self._mgr()
        session = manager.get_session()
        try:
            user_a = _make_user(session, "a@example.com", "uuid-a")
            user_b = _make_user(session, "b@example.com", "uuid-b")
            repo = SqlGreenhouseRepository(session)
            gh_a = repo.create(Greenhouse(name="USB", owner_user_id=user_a.id))

            # B's guard resolves A's greenhouse to None -> creation refused.
            assert repo.get_by_id_for_owner(gh_a.id, user_b.id) is None
        finally:
            session.close()
            self._cleanup(manager, db_path)


class TestLocalCreationWithOwner:
    def test_create_persists_owner_user_id(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        manager = DatabaseManager(db_path=db_path)
        manager.init_db()
        session = manager.get_session()
        try:
            user = _make_user(session, "op@example.com", _OWNER_REMOTE_ID)
            repo = SqlGreenhouseRepository(session)

            created = repo.create(
                Greenhouse(name="USB", owner_user_id=user.id, location="Norte")
            )

            assert created.owner_user_id == user.id

            # Reloaded from DB it still carries the owner.
            reloaded = repo.get_by_id(created.id)
            assert reloaded.owner_user_id == user.id
        finally:
            session.close()
            manager.engine.dispose()
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(db_path + suffix):
                    os.remove(db_path + suffix)

    def test_find_owner_remote_id_resolves_chain(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        manager = DatabaseManager(db_path=db_path)
        manager.init_db()
        session = manager.get_session()
        try:
            user = _make_user(session, "op@example.com", _OWNER_REMOTE_ID)
            repo = SqlGreenhouseRepository(session)
            gh = repo.create(Greenhouse(name="USB", owner_user_id=user.id))

            # owner_user_id -> users.id -> users.remote_user_id
            assert repo.find_owner_remote_id(gh.id) == _OWNER_REMOTE_ID

        finally:
            session.close()
            manager.engine.dispose()
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(db_path + suffix):
                    os.remove(db_path + suffix)

    def test_find_owner_remote_id_none_when_owner_missing(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        manager = DatabaseManager(db_path=db_path)
        manager.init_db()
        session = manager.get_session()
        try:
            repo = SqlGreenhouseRepository(session)
            gh = repo.create(Greenhouse(name="Legacy", owner_user_id=None))
            assert repo.find_owner_remote_id(gh.id) is None
        finally:
            session.close()
            manager.engine.dispose()
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(db_path + suffix):
                    os.remove(db_path + suffix)

    def test_find_owner_remote_id_none_when_owner_has_no_remote_id(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        manager = DatabaseManager(db_path=db_path)
        manager.init_db()
        session = manager.get_session()
        try:
            user = _make_user(session, "op@example.com", None)
            repo = SqlGreenhouseRepository(session)
            gh = repo.create(Greenhouse(name="USB", owner_user_id=user.id))
            assert repo.find_owner_remote_id(gh.id) is None
        finally:
            session.close()
            manager.engine.dispose()
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(db_path + suffix):
                    os.remove(db_path + suffix)


# ---------------------------------------------------------------------------
# RemoteSyncService ownership mapping / skip / report
# ---------------------------------------------------------------------------

_USER_REMOTE_ID = "99999999-8888-4777-8666-555555555555"


class FakeSyncState:
    """In-memory SyncStatePort with configurable owner -> remote_user_id map."""

    def __init__(self):
        self._rows: dict[str, list[dict]] = {}
        self._storage_paths: dict[int, tuple] = {}
        self.call_log: list[tuple] = []
        self.user_remote_ids: dict[int, Optional[str]] = {}
        # (entity_type, local_id) -> effective owner LOCAL user id (or None).
        self.effective_owners: dict[tuple, Optional[int]] = {}
        # user_remote_id -> local user id (session identity override).
        self.session_local_ids: dict[str, Optional[int]] = {}

    def add_entity(self, entity_type: str, row: dict) -> None:
        self._rows.setdefault(entity_type, []).append(row)

    def _find(self, entity_type: str, local_id: int) -> Optional[dict]:
        for row in self._rows.get(entity_type, []):
            if row["id"] == local_id:
                return row
        return None

    def get_pending_entities(self, entity_type: str) -> list[dict]:
        return [
            dict(row)
            for row in self._rows.get(entity_type, [])
            if row.get("remote_sync_status") in ("pending", "error", "syncing")
        ]

    def get_remote_id(self, entity_type: str, local_id: int) -> Optional[str]:
        row = self._find(entity_type, local_id)
        return None if row is None else row.get("remote_id")

    def get_user_remote_id(self, local_user_id: int) -> Optional[str]:
        return self.user_remote_ids.get(local_user_id)

    def get_effective_owner_local_user_id(
        self, entity_type: str, local_id: int
    ) -> Optional[int]:
        # Explicit override wins.
        if (entity_type, local_id) in self.effective_owners:
            return self.effective_owners[(entity_type, local_id)]
        # Otherwise resolve the owning greenhouse from the seeded rows and
        # return its LOCAL owner_user_id (independent of remote_user_id).
        gh = self._resolve_greenhouse(entity_type, local_id)
        if gh is None:
            return None
        return gh.get("owner_user_id")

    def get_local_user_id_by_remote_id(self, user_remote_id: str) -> Optional[int]:
        # Explicit session mapping override (decouples session identity from
        # the owner->remote_user_id map used for payloads).
        if user_remote_id in self.session_local_ids:
            return self.session_local_ids[user_remote_id]
        # Otherwise inverse of user_remote_ids (local user id -> remote id).
        for local_id, remote_id in self.user_remote_ids.items():
            if remote_id == user_remote_id:
                return local_id
        return None

    def _resolve_greenhouse(self, entity_type: str, local_id: int) -> Optional[dict]:
        if entity_type == "greenhouse":
            return self._find("greenhouse", local_id)
        if entity_type == "module":
            m = self._find("module", local_id)
            return None if m is None else self._find("greenhouse", m.get("greenhouse_id"))
        if entity_type in ("monitoring",):
            mon = self._find("monitoring", local_id)
            return None if mon is None else self._resolve_greenhouse("module", mon.get("module_id"))
        if entity_type == "monitoring_metrics":
            met = self._find("monitoring_metrics", local_id)
            return None if met is None else self._resolve_greenhouse("monitoring", met.get("monitoring_id"))
        if entity_type == "snapshot":
            s = self._find("snapshot", local_id)
            return None if s is None else self._resolve_greenhouse("monitoring", s.get("monitoring_id"))
        if entity_type == "inspection_result":
            ir = self._find("inspection_result", local_id)
            return None if ir is None else self._resolve_greenhouse("snapshot", ir.get("snapshot_id"))
        if entity_type == "activity_log":
            al = self._find("activity_log", local_id)
            return None if al is None else self._resolve_greenhouse("module", al.get("module_id"))
        return None

    def reserve_remote_id(self, entity_type: str, local_id: int, remote_id: str) -> None:
        row = self._find(entity_type, local_id)
        if row is not None:
            row["remote_id"] = remote_id
            row["remote_sync_status"] = "pending"
        self.call_log.append(("reserve", entity_type, local_id, remote_id))

    def mark_syncing(self, entity_type: str, local_id: int) -> None:
        row = self._find(entity_type, local_id)
        if row is not None:
            row["remote_sync_status"] = "syncing"
        self.call_log.append(("syncing", entity_type, local_id))

    def mark_synced(self, entity_type: str, local_id: int, remote_id: str) -> None:
        row = self._find(entity_type, local_id)
        if row is not None:
            row["remote_sync_status"] = "synced"
            row["remote_id"] = remote_id
        self.call_log.append(("synced", entity_type, local_id, remote_id))

    def mark_error(self, entity_type: str, local_id: int, error_msg: str) -> None:
        row = self._find(entity_type, local_id)
        if row is not None:
            row["remote_sync_status"] = "error"
            row["remote_sync_error"] = error_msg
        self.call_log.append(("error", entity_type, local_id, error_msg))

    def get_storage_paths(self, snapshot_id: int) -> StoragePaths:
        raw, ann = self._storage_paths.get(snapshot_id, (None, None))
        return StoragePaths(raw_storage_path=raw, annotated_storage_path=ann)

    def set_storage_paths(self, snapshot_id, raw_path, annotated_path) -> None:
        self._storage_paths[snapshot_id] = (raw_path, annotated_path)
        self.call_log.append(("set_storage_paths", snapshot_id, raw_path, annotated_path))


class FakeRemoteData:
    def __init__(self, fail_tables: Optional[set] = None):
        self.calls: list[dict] = []
        self._fail_tables = fail_tables or set()

    def upsert(self, access_token: str, table: str, data: dict) -> RemoteUpsertResult:
        self.calls.append({"table": table, "data": copy.deepcopy(data)})
        if table in self._fail_tables:
            return RemoteUpsertResult(
                success=False, error_type="CONNECTIVITY", error_message="fail"
            )
        return RemoteUpsertResult(success=True, remote_id=data["id"])


class FakeRemoteStorage:
    def upload_file(self, access_token, local_file_path, remote_path) -> RemoteUploadResult:
        return RemoteUploadResult(success=True, object_path=remote_path)


def _svc(state, data=None):
    return RemoteSyncService(
        data or FakeRemoteData(), FakeRemoteStorage(), state, SyncRuntimeState()
    )


class TestGreenhouseOwnerPayloadMapping:
    def test_payload_owner_is_remote_user_id_not_local_int(self):
        state = FakeSyncState()
        state.user_remote_ids[42] = _OWNER_REMOTE_ID
        state.add_entity("greenhouse", {
            "id": 1, "owner_user_id": 42, "name": "USB", "location": None,
            "remote_id": None, "remote_sync_status": "pending",
        })

        data = FakeRemoteData()
        # The session user IS the owner (single-user sync).
        result = _svc(state, data).execute_sync("jwt", _OWNER_REMOTE_ID)

        assert result.entities_synced == 1
        payload = data.calls[0]["data"]
        # Owner is mapped to the remote UUID, never the local integer id.
        assert payload["owner_user_id"] == _OWNER_REMOTE_ID
        assert payload["owner_user_id"] != 42

    def test_null_owner_not_uploaded_and_reported(self):
        state = FakeSyncState()
        # The syncing operator is a valid local user (session resolves to 42);
        # the greenhouse is a legacy NULL-owner row of the same device.
        state.session_local_ids[_OWNER_REMOTE_ID] = 42
        state.add_entity("greenhouse", {
            "id": 1, "owner_user_id": None, "name": "Legacy", "location": None,
            "remote_id": None, "remote_sync_status": "pending",
        })

        data = FakeRemoteData()
        result = _svc(state, data).execute_sync("jwt", _OWNER_REMOTE_ID)

        # Not uploaded.
        assert [c["table"] for c in data.calls] == []
        # Reported via existing error mechanism.
        gh = state._find("greenhouse", 1)
        assert gh["remote_sync_status"] == "error"
        assert "OWNER_MISSING" in gh["remote_sync_error"]
        assert result.entities_failed == 1

    def test_owner_without_remote_user_id_not_uploaded_and_reported(self):
        state = FakeSyncState()
        # The greenhouse belongs to the CURRENT user (local id 42), but that
        # user has no remote_user_id for the payload mapping. The session maps
        # to local id 42 so the scope guard treats it as the user's OWN row;
        # the greenhouse gate then reports OWNER_NOT_SYNCED.
        state.session_local_ids[_OWNER_REMOTE_ID] = 42
        state.user_remote_ids[42] = None
        state.add_entity("greenhouse", {
            "id": 1, "owner_user_id": 42, "name": "USB", "location": None,
            "remote_id": None, "remote_sync_status": "pending",
        })

        data = FakeRemoteData()
        result = _svc(state, data).execute_sync("jwt", _OWNER_REMOTE_ID)

        assert [c["table"] for c in data.calls] == []
        gh = state._find("greenhouse", 1)
        assert gh["remote_sync_status"] == "error"
        assert "OWNER_NOT_SYNCED" in gh["remote_sync_error"]
        assert result.entities_failed == 1

    def test_owner_failure_does_not_break_whole_sync(self):
        """A NULL-owner greenhouse fails but an unrelated valid one still syncs."""
        state = FakeSyncState()
        state.user_remote_ids[42] = _OWNER_REMOTE_ID
        # id=1: no owner -> reported error, skipped
        state.add_entity("greenhouse", {
            "id": 1, "owner_user_id": None, "name": "Legacy", "location": None,
            "remote_id": None, "remote_sync_status": "pending",
        })
        # id=2: valid owner -> syncs
        state.add_entity("greenhouse", {
            "id": 2, "owner_user_id": 42, "name": "USB", "location": None,
            "remote_id": None, "remote_sync_status": "pending",
        })

        data = FakeRemoteData()
        result = _svc(state, data).execute_sync("jwt", _OWNER_REMOTE_ID)

        # The valid greenhouse was uploaded with the mapped owner.
        assert len(data.calls) == 1
        assert data.calls[0]["data"]["owner_user_id"] == _OWNER_REMOTE_ID
        gh2 = state._find("greenhouse", 2)
        assert gh2["remote_sync_status"] == "synced"
        # The invalid one is reported, sync continues (fault isolation).
        gh1 = state._find("greenhouse", 1)
        assert gh1["remote_sync_status"] == "error"
        assert result.entities_synced == 1
        assert result.entities_failed == 1


_A_REMOTE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_B_REMOTE_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class TestTwoUserSyncScope:
    """A sync run processes ONLY the executing user's hierarchy."""

    def _two_user_state(self):
        state = FakeSyncState()
        # User A (local id 1 -> _A_REMOTE_ID), User B (local id 2 -> _B_REMOTE_ID)
        state.user_remote_ids[1] = _A_REMOTE_ID
        state.user_remote_ids[2] = _B_REMOTE_ID
        now = datetime(2025, 6, 1, 12, 0, 0)

        # --- User A hierarchy (all pending) ---
        state.add_entity("greenhouse", {
            "id": 10, "owner_user_id": 1, "name": "USB", "location": None,
            "created_at": now, "updated_at": now,
            "remote_id": None, "remote_sync_status": "pending",
        })
        state.add_entity("module", {
            "id": 11, "greenhouse_id": 10, "name": "ModA", "crop_type": "Tomate Cherry",
            "width_m": 5.0, "length_m": 2.0, "monitoring_frequency_days": 7,
            "created_at": now, "updated_at": now,
            "remote_id": None, "remote_sync_status": "pending",
        })
        state.add_entity("monitoring", {
            "id": 12, "module_id": 11, "status": "completed",
            "started_at": now, "completed_at": now,
            "width_m": 5.0, "length_m": 2.0, "notes": None,
            "total_snapshots": 0, "total_detections": 0, "created_by_user_id": 1,
            "remote_id": None, "remote_sync_status": "pending",
        })

        # --- User B hierarchy (all pending) ---
        state.add_entity("greenhouse", {
            "id": 20, "owner_user_id": 2, "name": "USB", "location": None,
            "created_at": now, "updated_at": now,
            "remote_id": None, "remote_sync_status": "pending",
        })
        state.add_entity("module", {
            "id": 21, "greenhouse_id": 20, "name": "ModB", "crop_type": "Tomate Cherry",
            "width_m": 5.0, "length_m": 2.0, "monitoring_frequency_days": 7,
            "created_at": now, "updated_at": now,
            "remote_id": None, "remote_sync_status": "pending",
        })
        return state

    def test_b_sync_does_not_touch_a(self):
        state = self._two_user_state()
        data = FakeRemoteData()
        result = _svc(state, data).execute_sync("jwt", _B_REMOTE_ID)

        # Only B payloads reach remote.
        gh_ids = [c["data"]["id"] for c in data.calls if c["table"] == "greenhouses"]
        assert len(gh_ids) == 1
        assert data.calls[0]["data"]["owner_user_id"] == _B_REMOTE_ID

        # B synced.
        assert state._find("greenhouse", 20)["remote_sync_status"] == "synced"
        assert state._find("module", 21)["remote_sync_status"] == "synced"

        # A completely intact: no status change, no remote_id, no syncing/error.
        for et, lid in [("greenhouse", 10), ("module", 11), ("monitoring", 12)]:
            row = state._find(et, lid)
            assert row["remote_sync_status"] == "pending"
            assert row["remote_id"] is None
        a_calls = [
            c for c in state.call_log
            if len(c) >= 3 and (
                (c[1] == "greenhouse" and c[2] == 10)
                or (c[1] == "module" and c[2] == 11)
                or (c[1] == "monitoring" and c[2] == 12)
            )
        ]
        assert a_calls == []

        assert result.entities_synced == 2  # B greenhouse + B module

    def test_a_synced_parent_pending_child_not_leaked_to_b(self):
        """GH A already synced but a child A pending must NOT sync under B."""
        state = self._two_user_state()
        # Mark A greenhouse synced with a remote id; child module A stays pending.
        gh_a = state._find("greenhouse", 10)
        gh_a["remote_sync_status"] = "synced"
        gh_a["remote_id"] = _A_REMOTE_ID and "gh-a-uuid"

        data = FakeRemoteData()
        result = _svc(state, data).execute_sync("jwt", _B_REMOTE_ID)

        # Child module A never processed.
        mod_a = state._find("module", 11)
        assert mod_a["remote_sync_status"] == "pending"
        assert mod_a["remote_id"] is None
        mod_a_calls = [
            c for c in state.call_log
            if len(c) >= 3 and c[1] == "module" and c[2] == 11
        ]
        assert mod_a_calls == []

        # No A tables leaked; only B greenhouse+module synced.
        assert result.entities_synced == 2
        gh_owners = [
            c["data"]["owner_user_id"] for c in data.calls if c["table"] == "greenhouses"
        ]
        assert gh_owners == [_B_REMOTE_ID]


class TestOrderAndFasePreservedWithOwner:
    def test_hierarchy_order_preserved_with_owner(self, tmp_path):
        state = FakeSyncState()
        state.user_remote_ids[42] = _OWNER_REMOTE_ID
        now = datetime(2025, 6, 1, 12, 0, 0)

        raw_dir = tmp_path / "monitorings" / "3" / "snapshots" / "raw"
        raw_dir.mkdir(parents=True)
        raw_file = raw_dir / "snapshot_000007.jpg"
        raw_file.write_bytes(b"x")

        state.add_entity("greenhouse", {
            "id": 1, "owner_user_id": 42, "name": "GH1", "location": None,
            "created_at": now, "updated_at": now,
            "remote_id": None, "remote_sync_status": "pending",
        })
        state.add_entity("module", {
            "id": 2, "greenhouse_id": 1, "name": "Mod1", "crop_type": "Tomate Cherry",
            "width_m": 5.0, "length_m": 2.0, "monitoring_frequency_days": 7,
            "created_at": now, "updated_at": now,
            "remote_id": None, "remote_sync_status": "pending",
        })
        state.add_entity("monitoring", {
            "id": 3, "module_id": 2, "status": "completed",
            "started_at": now, "completed_at": now,
            "width_m": 5.0, "length_m": 2.0, "notes": None,
            "total_snapshots": 1, "total_detections": 3, "created_by_user_id": 42,
            "remote_id": None, "remote_sync_status": "pending",
        })

        data = FakeRemoteData()
        result = _svc(state, data).execute_sync("jwt", _OWNER_REMOTE_ID)

        tables = [c["table"] for c in data.calls]
        assert tables == ["greenhouses", "modules", "monitorings"]
        # greenhouse remote uuid used as module FK (parent-before-child order).
        gh_uuid = data.calls[0]["data"]["id"]
        assert data.calls[1]["data"]["greenhouse_id"] == gh_uuid
        assert result.entities_synced == 3
