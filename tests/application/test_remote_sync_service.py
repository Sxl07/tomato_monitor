"""Tests for RemoteSyncService — Task 13.2: parent-child dependencies.

Validates:
- Entities sync in strict hierarchical order
- Children use parent remote UUIDs (not local integer FKs)
- Children blocked when parent not synced (even with remote_id reserved)
- Parent failure leaves child pending (not failed)
- ActivityLog mapping: activity_type_code, user_remote_id, module_remote_id
- monitoring.created_by_user_id maps to user_remote_id

Spec 017 — Supabase Remote Sync.
Requirements: 12.1–12.6, 14.1, 14.9, 27.4
"""

import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from src.application.interfaces.remote_data_port import RemoteUpsertResult
from src.application.interfaces.remote_storage_port import RemoteUploadResult
from src.application.interfaces.sync_state_port import StoragePaths
from src.application.services.remote_sync_service import RemoteSyncService, SyncResult
from src.application.services.sync_runtime_state import SyncRuntimeState


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

_USER_REMOTE_ID = "99999999-8888-4777-8666-555555555555"


class FakeSyncState:
    """In-memory SyncStatePort implementation for testing."""

    def __init__(self):
        # entity_type -> list of row dicts
        self._rows: dict[str, list[dict]] = {}
        self._storage_paths: dict[int, tuple] = {}
        self.call_log: list[tuple] = []
        # local_user_id -> remote_user_id (auth.uid()); missing owners fall back
        # to _USER_REMOTE_ID in get_user_remote_id.
        self.user_remote_ids: dict[int, Optional[str]] = {}
        # (entity_type, local_id) -> effective owner LOCAL user id; unset
        # entries default to 99 (the seeded current user).
        self.effective_owners: dict[tuple, Optional[int]] = {}
        # user_remote_id -> local user id (besides the default 99/_USER_REMOTE_ID).
        self.local_user_ids: dict[str, Optional[int]] = {}

    def add_entity(self, entity_type: str, row: dict) -> None:
        """Seed an entity row for testing."""
        self._rows.setdefault(entity_type, []).append(row)

    def _find(self, entity_type: str, local_id: int) -> Optional[dict]:
        for row in self._rows.get(entity_type, []):
            if row["id"] == local_id:
                return row
        return None

    def get_pending_entities(self, entity_type: str) -> list[dict]:
        return [
            dict(row) for row in self._rows.get(entity_type, [])
            if row.get("remote_sync_status") in ("pending", "error", "syncing")
        ]

    def get_remote_id(self, entity_type: str, local_id: int) -> Optional[str]:
        row = self._find(entity_type, local_id)
        if row is None:
            return None
        return row.get("remote_id")

    def get_user_remote_id(self, local_user_id: int) -> Optional[str]:
        # Map a local owner id -> remote_user_id (auth.uid()). Configurable via
        # user_remote_ids; defaults to _USER_REMOTE_ID for any known/seeded owner.
        if local_user_id in self.user_remote_ids:
            return self.user_remote_ids[local_user_id]
        return _USER_REMOTE_ID

    def get_effective_owner_local_user_id(
        self, entity_type: str, local_id: int
    ) -> Optional[int]:
        # Configurable per (entity_type, local_id) via effective_owners; when
        # unset, seeded entities belong to the current local user (id 99).
        return self.effective_owners.get((entity_type, local_id), 99)

    def get_local_user_id_by_remote_id(self, user_remote_id: str) -> Optional[int]:
        # The default seeded owner (local id 99) maps to _USER_REMOTE_ID.
        if user_remote_id == _USER_REMOTE_ID:
            return 99
        return self.local_user_ids.get(user_remote_id)

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
        self.call_log.append(("error", entity_type, local_id, error_msg))

    def get_storage_paths(self, snapshot_id: int) -> StoragePaths:
        raw, ann = self._storage_paths.get(snapshot_id, (None, None))
        return StoragePaths(raw_storage_path=raw, annotated_storage_path=ann)

    def set_storage_paths(self, snapshot_id: int, raw_path: Optional[str], annotated_path: Optional[str]) -> None:
        self._storage_paths[snapshot_id] = (raw_path, annotated_path)
        self.call_log.append(("set_storage_paths", snapshot_id, raw_path, annotated_path))


class FakeRemoteData:
    """Fake RemoteDataPort that records calls and supports per-table failures."""

    def __init__(self, fail_tables: Optional[set] = None):
        self.calls: list[dict] = []
        self._fail_tables = fail_tables or set()

    def upsert(self, access_token: str, table: str, data: dict) -> RemoteUpsertResult:
        self.calls.append({"table": table, "data": copy.deepcopy(data)})
        if table in self._fail_tables:
            return RemoteUpsertResult(
                success=False, error_type="CONNECTIVITY", error_message="fake fail"
            )
        return RemoteUpsertResult(success=True, remote_id=data["id"])


class FakeRemoteStorage:
    """Fake RemoteStoragePort that always succeeds."""

    def __init__(self):
        self.calls: list[dict] = []

    def upload_file(self, access_token: str, local_file_path: str, remote_path: str) -> RemoteUploadResult:
        self.calls.append({"local": local_file_path, "remote": remote_path})
        return RemoteUploadResult(success=True, object_path=remote_path)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def full_tree(tmp_path):
    """Seed a complete entity tree with all 7 types pending."""
    state = FakeSyncState()

    # Create raw snapshot file
    raw_dir = tmp_path / "monitorings" / "3" / "snapshots" / "raw"
    raw_dir.mkdir(parents=True)
    raw_file = raw_dir / "snapshot_000007.jpg"
    raw_file.write_bytes(b"fake-jpeg")

    now = datetime(2025, 6, 1, 12, 0, 0)

    state.add_entity("greenhouse", {
        "id": 1, "owner_user_id": 99, "name": "GH1", "location": "Norte",
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
        "total_snapshots": 1, "total_detections": 3,
        "created_by_user_id": 99,
        "remote_id": None, "remote_sync_status": "pending",
    })
    state.add_entity("monitoring_metrics", {
        "id": 4, "monitoring_id": 3,
        "total_tomatoes": 10, "healthy_count": 8, "unhealthy_count": 2,
        "pct_healthy": 80.0, "pct_unhealthy": 20.0,
        "pct_green": 10.0, "pct_breaker": 5.0, "pct_turning": 5.0,
        "pct_pink": 20.0, "pct_light_red": 30.0, "pct_red": 30.0,
        "snapshots_with_detections": 1, "computed_at": now,
        "remote_id": None, "remote_sync_status": "pending",
    })
    state.add_entity("snapshot", {
        "id": 5, "monitoring_id": 3,
        "image_path": str(raw_file), "frame_index": 7,
        "captured_at": now, "change_score": 0.5, "has_detections": True,
        "remote_id": None, "remote_sync_status": "pending",
    })
    state.add_entity("inspection_result", {
        "id": 6, "snapshot_id": 5,
        "detection_index": 0, "bbox_x1": 10, "bbox_y1": 20,
        "bbox_x2": 50, "bbox_y2": 60, "detection_score": 0.95,
        "health_label": "healthy", "health_confidence": 0.9,
        "maturity_stage": "red", "maturity_percent": 85.0,
        "created_at": now,
        "remote_id": None, "remote_sync_status": "pending",
    })
    state.add_entity("activity_log", {
        "id": 7, "module_id": 2, "activity_type_id": 1,
        "activity_type_code": "riego", "user_id": 99,
        "product_name": None, "quantity": 2.0, "unit": "L",
        "notes": "Riego AM", "occurred_at": now, "created_at": now,
        "remote_id": None, "remote_sync_status": "pending",
    })

    return state


# ===========================================================================
# Happy path: full tree syncs in order
# ===========================================================================


class TestFullTreeOrder:
    """Complete tree syncs parents before children with correct FKs."""

    def test_all_entities_synced(self, full_tree, tmp_path):
        data = FakeRemoteData()
        storage = FakeRemoteStorage()
        runtime = SyncRuntimeState()
        svc = RemoteSyncService(data, storage, full_tree, runtime)

        result = svc.execute_sync("dummy-jwt", _USER_REMOTE_ID)

        assert result.success is True
        assert result.entities_synced == 7
        assert result.entities_failed == 0
        assert result.images_uploaded == 1  # raw only, annotated doesn't exist

    def test_table_order(self, full_tree, tmp_path):
        data = FakeRemoteData()
        svc = RemoteSyncService(data, FakeRemoteStorage(), full_tree, SyncRuntimeState())
        svc.execute_sync("jwt", _USER_REMOTE_ID)

        tables = [c["table"] for c in data.calls]
        assert tables == [
            "greenhouses", "modules", "monitorings", "monitoring_metrics",
            "snapshots", "inspection_results", "activity_logs",
        ]

    def test_module_uses_greenhouse_remote_uuid(self, full_tree, tmp_path):
        data = FakeRemoteData()
        svc = RemoteSyncService(data, FakeRemoteStorage(), full_tree, SyncRuntimeState())
        svc.execute_sync("jwt", _USER_REMOTE_ID)

        gh_uuid = data.calls[0]["data"]["id"]
        mod_payload = data.calls[1]["data"]
        assert mod_payload["greenhouse_id"] == gh_uuid
        assert mod_payload["greenhouse_id"] != 1

    def test_monitoring_uses_module_remote_uuid(self, full_tree, tmp_path):
        data = FakeRemoteData()
        svc = RemoteSyncService(data, FakeRemoteStorage(), full_tree, SyncRuntimeState())
        svc.execute_sync("jwt", _USER_REMOTE_ID)

        mod_uuid = data.calls[1]["data"]["id"]
        mon_payload = data.calls[2]["data"]
        assert mon_payload["module_id"] == mod_uuid
        assert mon_payload["module_id"] != 2

    def test_monitoring_created_by_user_id(self, full_tree, tmp_path):
        data = FakeRemoteData()
        svc = RemoteSyncService(data, FakeRemoteStorage(), full_tree, SyncRuntimeState())
        svc.execute_sync("jwt", _USER_REMOTE_ID)

        mon_payload = data.calls[2]["data"]
        assert mon_payload["created_by_user_id"] == _USER_REMOTE_ID
        assert mon_payload["created_by_user_id"] != 99

    def test_metrics_uses_monitoring_uuid(self, full_tree, tmp_path):
        data = FakeRemoteData()
        svc = RemoteSyncService(data, FakeRemoteStorage(), full_tree, SyncRuntimeState())
        svc.execute_sync("jwt", _USER_REMOTE_ID)

        mon_uuid = data.calls[2]["data"]["id"]
        metrics_payload = data.calls[3]["data"]
        assert metrics_payload["monitoring_id"] == mon_uuid

    def test_snapshot_uses_monitoring_uuid(self, full_tree, tmp_path):
        data = FakeRemoteData()
        svc = RemoteSyncService(data, FakeRemoteStorage(), full_tree, SyncRuntimeState())
        svc.execute_sync("jwt", _USER_REMOTE_ID)

        mon_uuid = data.calls[2]["data"]["id"]
        snap_payload = data.calls[4]["data"]
        assert snap_payload["monitoring_id"] == mon_uuid

    def test_inspection_result_uses_snapshot_uuid(self, full_tree, tmp_path):
        data = FakeRemoteData()
        svc = RemoteSyncService(data, FakeRemoteStorage(), full_tree, SyncRuntimeState())
        svc.execute_sync("jwt", _USER_REMOTE_ID)

        snap_uuid = data.calls[4]["data"]["id"]
        ir_payload = data.calls[5]["data"]
        assert ir_payload["snapshot_id"] == snap_uuid
        assert ir_payload["snapshot_id"] != 5

    def test_activity_log_mapping(self, full_tree, tmp_path):
        data = FakeRemoteData()
        svc = RemoteSyncService(data, FakeRemoteStorage(), full_tree, SyncRuntimeState())
        svc.execute_sync("jwt", _USER_REMOTE_ID)

        mod_uuid = data.calls[1]["data"]["id"]
        al_payload = data.calls[6]["data"]
        assert al_payload["module_id"] == mod_uuid
        assert al_payload["user_id"] == _USER_REMOTE_ID
        assert al_payload["activity_type_code"] == "riego"
        assert "activity_type_id" not in al_payload
        assert al_payload["user_id"] != 99

    def test_all_marked_synced(self, full_tree, tmp_path):
        svc = RemoteSyncService(FakeRemoteData(), FakeRemoteStorage(), full_tree, SyncRuntimeState())
        svc.execute_sync("jwt", _USER_REMOTE_ID)

        for et in ["greenhouse", "module", "monitoring", "monitoring_metrics",
                   "snapshot", "inspection_result", "activity_log"]:
            for row in full_tree._rows[et]:
                assert row["remote_sync_status"] == "synced"
                assert row["remote_id"] is not None


# ===========================================================================
# Parent remote_id reserved but NOT synced → child blocked
# ===========================================================================


class TestRemoteIdNotSufficient:
    """remote_id reserved + status error does NOT enable child sync."""

    def test_module_blocked_when_greenhouse_has_id_but_error(self):
        state = FakeSyncState()
        state.add_entity("greenhouse", {
            "id": 1, "owner_user_id": 99, "name": "GH", "remote_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "remote_sync_status": "error",
        })
        state.add_entity("module", {
            "id": 2, "greenhouse_id": 1, "name": "Mod",
            "remote_id": None, "remote_sync_status": "pending",
        })
        for t in ["monitoring", "monitoring_metrics", "snapshot", "inspection_result", "activity_log"]:
            pass  # empty

        # Greenhouse upsert fails again
        data = FakeRemoteData(fail_tables={"greenhouses"})
        svc = RemoteSyncService(data, FakeRemoteStorage(), state, SyncRuntimeState())
        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        # Module never upserted
        tables_called = [c["table"] for c in data.calls]
        assert "modules" not in tables_called
        # Module stays pending with no remote_id
        mod = state._find("module", 2)
        assert mod["remote_sync_status"] == "pending"
        assert mod["remote_id"] is None


# ===========================================================================
# Parent failure → child stays pending (not failed)
# ===========================================================================


class TestParentFailureChildPending:
    """Parent failure leaves child pending, child not counted as failed."""

    def test_child_not_failed(self):
        state = FakeSyncState()
        state.add_entity("greenhouse", {
            "id": 1, "owner_user_id": 99, "name": "GH", "remote_id": None, "remote_sync_status": "pending",
        })
        state.add_entity("module", {
            "id": 2, "greenhouse_id": 1, "name": "Mod",
            "remote_id": None, "remote_sync_status": "pending",
        })

        data = FakeRemoteData(fail_tables={"greenhouses"})
        svc = RemoteSyncService(data, FakeRemoteStorage(), state, SyncRuntimeState())
        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        assert result.entities_failed == 1  # greenhouse only
        assert result.entities_synced == 0
        mod = state._find("module", 2)
        assert mod["remote_sync_status"] == "pending"
        assert mod["remote_id"] is None


# ===========================================================================
# Snapshot blocked when monitoring not synced
# ===========================================================================


class TestSnapshotBlockedByMonitoring:
    """Snapshot not processed when monitoring parent is not synced."""

    def test_no_storage_or_upsert(self, tmp_path):
        state = FakeSyncState()
        state.add_entity("monitoring", {
            "id": 3, "remote_id": "mon-uuid", "remote_sync_status": "error",
        })
        raw = tmp_path / "snap.jpg"
        raw.write_bytes(b"x")
        state.add_entity("snapshot", {
            "id": 5, "monitoring_id": 3, "image_path": str(raw),
            "frame_index": 0, "remote_id": None, "remote_sync_status": "pending",
        })
        for t in ["greenhouse", "module", "monitoring_metrics", "inspection_result", "activity_log"]:
            pass

        data = FakeRemoteData()
        storage = FakeRemoteStorage()
        svc = RemoteSyncService(data, storage, state, SyncRuntimeState())
        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        assert len(storage.calls) == 0
        tables_called = [c["table"] for c in data.calls]
        assert "snapshots" not in tables_called
        snap = state._find("snapshot", 5)
        assert snap["remote_sync_status"] == "pending"
        assert snap["remote_id"] is None


# ===========================================================================
# InspectionResult blocked when snapshot not synced
# ===========================================================================


class TestInspectionResultBlocked:
    """InspectionResult blocked when snapshot parent not synced."""

    def test_no_upsert(self):
        state = FakeSyncState()
        state.add_entity("snapshot", {
            "id": 5, "remote_id": "snap-uuid", "remote_sync_status": "error",
        })
        state.add_entity("inspection_result", {
            "id": 6, "snapshot_id": 5,
            "detection_index": 0, "bbox_x1": 0, "bbox_y1": 0, "bbox_x2": 10, "bbox_y2": 10,
            "detection_score": 0.9, "health_label": "healthy", "health_confidence": 0.8,
            "maturity_stage": None, "maturity_percent": None, "created_at": datetime(2025, 1, 1),
            "remote_id": None, "remote_sync_status": "pending",
        })

        data = FakeRemoteData()
        svc = RemoteSyncService(data, FakeRemoteStorage(), state, SyncRuntimeState())
        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        tables = [c["table"] for c in data.calls]
        assert "inspection_results" not in tables
        ir = state._find("inspection_result", 6)
        assert ir["remote_sync_status"] == "pending"
        assert ir["remote_id"] is None


# ===========================================================================
# ActivityLog blocked when module not synced
# ===========================================================================


class TestActivityLogBlocked:
    """ActivityLog blocked when module parent not synced."""

    def test_no_upsert(self):
        state = FakeSyncState()
        state.add_entity("module", {
            "id": 2, "remote_id": "mod-uuid", "remote_sync_status": "error",
        })
        state.add_entity("activity_log", {
            "id": 7, "module_id": 2, "activity_type_id": 1,
            "activity_type_code": "riego", "user_id": 99,
            "product_name": None, "quantity": None, "unit": None,
            "notes": None, "occurred_at": datetime(2025, 1, 1),
            "created_at": datetime(2025, 1, 1),
            "remote_id": None, "remote_sync_status": "pending",
        })

        data = FakeRemoteData()
        svc = RemoteSyncService(data, FakeRemoteStorage(), state, SyncRuntimeState())
        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        tables = [c["table"] for c in data.calls]
        assert "activity_logs" not in tables
        al = state._find("activity_log", 7)
        assert al["remote_sync_status"] == "pending"
        assert al["remote_id"] is None


# ===========================================================================
# ActivityLog with module synced succeeds
# ===========================================================================


class TestActivityLogWithSyncedModule:
    """ActivityLog syncs when module parent is synced."""

    def test_uses_correct_fks(self):
        state = FakeSyncState()
        state.add_entity("module", {
            "id": 2, "remote_id": "mod-uuid-known", "remote_sync_status": "synced",
        })
        state.add_entity("activity_log", {
            "id": 7, "module_id": 2, "activity_type_id": 1,
            "activity_type_code": "riego", "user_id": 99,
            "product_name": None, "quantity": None, "unit": None,
            "notes": "Test", "occurred_at": datetime(2025, 6, 1),
            "created_at": datetime(2025, 6, 1),
            "remote_id": None, "remote_sync_status": "pending",
        })

        data = FakeRemoteData()
        svc = RemoteSyncService(data, FakeRemoteStorage(), state, SyncRuntimeState())
        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        assert result.entities_synced == 1
        payload = data.calls[0]["data"]
        assert payload["module_id"] == "mod-uuid-known"
        assert payload["user_id"] == _USER_REMOTE_ID
        assert payload["activity_type_code"] == "riego"
        assert "activity_type_id" not in payload


# ===========================================================================
# Monitoring blocked by module not synced
# ===========================================================================


class TestMonitoringBlockedByModule:
    """Monitoring blocked when module parent not synced."""

    def test_no_upsert(self):
        state = FakeSyncState()
        state.add_entity("module", {
            "id": 2, "remote_id": "mod-uuid", "remote_sync_status": "error",
        })
        state.add_entity("monitoring", {
            "id": 3, "module_id": 2, "status": "completed",
            "remote_id": None, "remote_sync_status": "pending",
        })

        data = FakeRemoteData()
        svc = RemoteSyncService(data, FakeRemoteStorage(), state, SyncRuntimeState())
        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        tables = [c["table"] for c in data.calls]
        assert "monitorings" not in tables
        mon = state._find("monitoring", 3)
        assert mon["remote_sync_status"] == "pending"


# ===========================================================================
# MonitoringMetrics blocked by monitoring not synced
# ===========================================================================


class TestMetricsBlockedByMonitoring:
    """MonitoringMetrics blocked when monitoring parent not synced."""

    def test_no_upsert(self):
        state = FakeSyncState()
        state.add_entity("monitoring", {
            "id": 3, "remote_id": "mon-uuid", "remote_sync_status": "error",
        })
        state.add_entity("monitoring_metrics", {
            "id": 4, "monitoring_id": 3,
            "total_tomatoes": 5, "healthy_count": 4, "unhealthy_count": 1,
            "pct_healthy": 80.0, "pct_unhealthy": 20.0,
            "pct_green": 0, "pct_breaker": 0, "pct_turning": 0,
            "pct_pink": 0, "pct_light_red": 0, "pct_red": 100.0,
            "snapshots_with_detections": 1, "computed_at": datetime(2025, 1, 1),
            "remote_id": None, "remote_sync_status": "pending",
        })

        data = FakeRemoteData()
        svc = RemoteSyncService(data, FakeRemoteStorage(), state, SyncRuntimeState())
        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        tables = [c["table"] for c in data.calls]
        assert "monitoring_metrics" not in tables


# ===========================================================================
# Parent synced but remote_id=None → child blocked
# ===========================================================================


class TestSyncedParentWithoutRemoteId:
    """Parent with status='synced' but remote_id=None does NOT enable child."""

    def test_module_blocked(self):
        state = FakeSyncState()
        state.add_entity("greenhouse", {
            "id": 1, "name": "GH",
            "remote_id": None, "remote_sync_status": "synced",
        })
        state.add_entity("module", {
            "id": 2, "greenhouse_id": 1, "name": "Mod",
            "remote_id": None, "remote_sync_status": "pending",
        })

        data = FakeRemoteData()
        svc = RemoteSyncService(data, FakeRemoteStorage(), state, SyncRuntimeState())
        result = svc.execute_sync("dummy-jwt", _USER_REMOTE_ID)

        # No upsert to modules
        tables = [c["table"] for c in data.calls]
        assert "modules" not in tables

        # Module stays untouched
        mod = state._find("module", 2)
        assert mod["remote_sync_status"] == "pending"
        assert mod["remote_id"] is None

        # No reserve/syncing/error for module
        module_calls = [c for c in state.call_log if len(c) >= 3 and c[2] == 2]
        assert module_calls == []

        # Counters
        assert result.entities_synced == 0
        assert result.entities_failed == 0


# ===========================================================================
# Task 13.3 — Local integrity preservation on remote errors
# ===========================================================================


class FakeRemoteStorageConfigurable:
    """Storage fake that supports per-path failure and tracks uploaded objects."""

    def __init__(self, fail_paths: Optional[set] = None):
        self.calls: list[dict] = []
        self.uploaded_objects: set = set()
        self._fail_paths = fail_paths or set()

    def upload_file(self, access_token: str, local_file_path: str, remote_path: str) -> RemoteUploadResult:
        self.calls.append({"local": local_file_path, "remote": remote_path})
        if remote_path in self._fail_paths or "*" in self._fail_paths:
            return RemoteUploadResult(
                success=False, error_type="CONNECTIVITY", error_message="storage unavailable"
            )
        self.uploaded_objects.add(remote_path)
        return RemoteUploadResult(success=True, object_path=remote_path)


class FakeRemoteDataConfigurable:
    """Data fake that supports per-table failure."""

    def __init__(self, fail_tables: Optional[set] = None):
        self.calls: list[dict] = []
        self._fail_tables = fail_tables or set()

    def upsert(self, access_token: str, table: str, data: dict) -> RemoteUpsertResult:
        self.calls.append({"table": table, "data": copy.deepcopy(data)})
        if table in self._fail_tables:
            return RemoteUpsertResult(
                success=False, error_type="CONNECTIVITY", error_message="metadata unavailable"
            )
        return RemoteUpsertResult(success=True, remote_id=data["id"])


# ---------------------------------------------------------------------------
# Test 1: Network error preserves reserved remote_id
# ---------------------------------------------------------------------------


class TestNetworkErrorPreservesRemoteId:
    """remote_id is reserved BEFORE remote upsert, preserved on failure."""

    def test_remote_id_preserved_after_failure(self):
        state = FakeSyncState()
        state.add_entity("greenhouse", {
            "id": 1, "owner_user_id": 99, "name": "GH", "location": None,
            "remote_id": None, "remote_sync_status": "pending",
        })
        for t in ["module", "monitoring", "monitoring_metrics", "snapshot", "inspection_result", "activity_log"]:
            pass  # empty

        data = FakeRemoteDataConfigurable(fail_tables={"greenhouses"})
        svc = RemoteSyncService(data, FakeRemoteStorage(), state, SyncRuntimeState())
        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        gh = state._find("greenhouse", 1)
        assert gh["remote_id"] is not None  # UUID was reserved
        assert gh["remote_sync_status"] == "error"
        assert result.entities_failed == 1
        assert result.success is False

    def test_call_order_reserve_before_error(self):
        state = FakeSyncState()
        state.add_entity("greenhouse", {
            "id": 1, "owner_user_id": 99, "name": "GH",
            "remote_id": None, "remote_sync_status": "pending",
        })

        data = FakeRemoteDataConfigurable(fail_tables={"greenhouses"})
        svc = RemoteSyncService(data, FakeRemoteStorage(), state, SyncRuntimeState())
        svc.execute_sync("jwt", _USER_REMOTE_ID)

        # Verify order: reserve → syncing → error
        gh_calls = [(c[0], c[1]) for c in state.call_log if len(c) >= 3 and c[2] == 1]
        statuses = [c[0] for c in gh_calls]
        assert "reserve" in statuses
        assert "syncing" in statuses
        assert "error" in statuses
        assert statuses.index("reserve") < statuses.index("syncing") < statuses.index("error")


# ---------------------------------------------------------------------------
# Test 2: Preexisting remote_id reused (not replaced)
# ---------------------------------------------------------------------------


class TestPreexistingRemoteIdReused:
    """Entity with existing remote_id uses that exact UUID, never generates new."""

    def test_same_uuid_in_payload(self):
        known_uuid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        state = FakeSyncState()
        state.add_entity("greenhouse", {
            "id": 1, "owner_user_id": 99, "name": "GH Retry",
            "remote_id": known_uuid, "remote_sync_status": "error",
        })

        data = FakeRemoteDataConfigurable()
        svc = RemoteSyncService(data, FakeRemoteStorage(), state, SyncRuntimeState())
        svc.execute_sync("jwt", _USER_REMOTE_ID)

        assert data.calls[0]["data"]["id"] == known_uuid
        gh = state._find("greenhouse", 1)
        assert gh["remote_id"] == known_uuid
        assert gh["remote_sync_status"] == "synced"


# ---------------------------------------------------------------------------
# Test 3-4: Snapshot Storage failure preserves image_path and raw file
# ---------------------------------------------------------------------------


class TestSnapshotStorageFailurePreservesLocal:
    """Storage failure preserves image_path and raw file bytes."""

    def test_image_path_unchanged(self, tmp_path):
        raw_dir = tmp_path / "monitorings" / "3" / "snapshots" / "raw"
        raw_dir.mkdir(parents=True)
        raw_file = raw_dir / "snapshot_000007.jpg"
        raw_file.write_bytes(b"original-raw-bytes")

        state = FakeSyncState()
        state.add_entity("monitoring", {
            "id": 3, "remote_id": "mon-uuid", "remote_sync_status": "synced",
        })
        state.add_entity("snapshot", {
            "id": 5, "monitoring_id": 3, "image_path": str(raw_file),
            "frame_index": 7, "captured_at": datetime(2025, 1, 1),
            "change_score": 0.5, "has_detections": True,
            "remote_id": None, "remote_sync_status": "pending",
        })

        storage = FakeRemoteStorageConfigurable(fail_paths={"*"})
        svc = RemoteSyncService(FakeRemoteDataConfigurable(), storage, state, SyncRuntimeState())
        svc.execute_sync("jwt", _USER_REMOTE_ID)

        snap = state._find("snapshot", 5)
        assert snap["image_path"] == str(raw_file)
        assert snap["remote_sync_status"] == "error"

    def test_raw_file_intact(self, tmp_path):
        raw_dir = tmp_path / "monitorings" / "3" / "snapshots" / "raw"
        raw_dir.mkdir(parents=True)
        raw_file = raw_dir / "snapshot_000007.jpg"
        raw_file.write_bytes(b"original-raw-bytes")

        state = FakeSyncState()
        state.add_entity("monitoring", {
            "id": 3, "remote_id": "mon-uuid", "remote_sync_status": "synced",
        })
        state.add_entity("snapshot", {
            "id": 5, "monitoring_id": 3, "image_path": str(raw_file),
            "frame_index": 7, "remote_id": None, "remote_sync_status": "pending",
        })

        storage = FakeRemoteStorageConfigurable(fail_paths={"*"})
        svc = RemoteSyncService(FakeRemoteDataConfigurable(), storage, state, SyncRuntimeState())
        svc.execute_sync("jwt", _USER_REMOTE_ID)

        assert raw_file.exists()
        assert raw_file.read_bytes() == b"original-raw-bytes"


# ---------------------------------------------------------------------------
# Test 5: Raw Storage failure counters
# ---------------------------------------------------------------------------


class TestRawStorageFailureCounters:
    """Raw upload failure produces correct counters."""

    def test_counters(self, tmp_path):
        raw_dir = tmp_path / "monitorings" / "3" / "snapshots" / "raw"
        raw_dir.mkdir(parents=True)
        raw_file = raw_dir / "snapshot_000007.jpg"
        raw_file.write_bytes(b"raw")

        state = FakeSyncState()
        state.add_entity("monitoring", {
            "id": 3, "remote_id": "mon-uuid", "remote_sync_status": "synced",
        })
        state.add_entity("snapshot", {
            "id": 5, "monitoring_id": 3, "image_path": str(raw_file),
            "frame_index": 7, "remote_id": None, "remote_sync_status": "pending",
        })

        storage = FakeRemoteStorageConfigurable(fail_paths={"*"})
        svc = RemoteSyncService(FakeRemoteDataConfigurable(), storage, state, SyncRuntimeState())
        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        assert result.entities_failed == 1
        assert result.images_uploaded == 0
        assert result.images_failed == 1
        assert result.success is False
        assert any("CONNECTIVITY" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test 6: Storage OK + PostgREST FAIL
# ---------------------------------------------------------------------------


class TestStorageOkPostgrestFail:
    """Storage succeeds but metadata upsert fails — objects remain, no synced."""

    def test_uploaded_objects_preserved(self, tmp_path):
        raw_dir = tmp_path / "monitorings" / "3" / "snapshots" / "raw"
        raw_dir.mkdir(parents=True)
        raw_file = raw_dir / "snapshot_000007.jpg"
        raw_file.write_bytes(b"original-raw-bytes")

        state = FakeSyncState()
        state.add_entity("monitoring", {
            "id": 3, "remote_id": "mon-uuid", "remote_sync_status": "synced",
        })
        state.add_entity("snapshot", {
            "id": 5, "monitoring_id": 3, "image_path": str(raw_file),
            "frame_index": 7, "captured_at": datetime(2025, 1, 1),
            "change_score": 0.5, "has_detections": True,
            "remote_id": None, "remote_sync_status": "pending",
        })

        storage = FakeRemoteStorageConfigurable()  # all uploads succeed
        data = FakeRemoteDataConfigurable(fail_tables={"snapshots"})
        svc = RemoteSyncService(data, storage, state, SyncRuntimeState())
        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        # Storage object persists (simulated)
        assert len(storage.uploaded_objects) == 1
        assert any("raw" in p for p in storage.uploaded_objects)

    def test_snapshot_state_after_metadata_fail(self, tmp_path):
        raw_dir = tmp_path / "monitorings" / "3" / "snapshots" / "raw"
        raw_dir.mkdir(parents=True)
        raw_file = raw_dir / "snapshot_000007.jpg"
        raw_file.write_bytes(b"original-raw-bytes")

        state = FakeSyncState()
        state.add_entity("monitoring", {
            "id": 3, "remote_id": "mon-uuid", "remote_sync_status": "synced",
        })
        state.add_entity("snapshot", {
            "id": 5, "monitoring_id": 3, "image_path": str(raw_file),
            "frame_index": 7, "captured_at": datetime(2025, 1, 1),
            "change_score": 0.5, "has_detections": True,
            "remote_id": None, "remote_sync_status": "pending",
        })

        storage = FakeRemoteStorageConfigurable()
        data = FakeRemoteDataConfigurable(fail_tables={"snapshots"})
        svc = RemoteSyncService(data, storage, state, SyncRuntimeState())
        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        snap = state._find("snapshot", 5)
        assert snap["remote_sync_status"] == "error"
        assert snap["remote_id"] is not None  # UUID reserved before fail
        assert snap["image_path"] == str(raw_file)
        assert raw_file.exists()
        assert raw_file.read_bytes() == b"original-raw-bytes"

    def test_no_set_storage_paths_on_metadata_fail(self, tmp_path):
        raw_dir = tmp_path / "monitorings" / "3" / "snapshots" / "raw"
        raw_dir.mkdir(parents=True)
        raw_file = raw_dir / "snapshot_000007.jpg"
        raw_file.write_bytes(b"x")

        state = FakeSyncState()
        state.add_entity("monitoring", {
            "id": 3, "remote_id": "mon-uuid", "remote_sync_status": "synced",
        })
        state.add_entity("snapshot", {
            "id": 5, "monitoring_id": 3, "image_path": str(raw_file),
            "frame_index": 7, "remote_id": None, "remote_sync_status": "pending",
        })

        storage = FakeRemoteStorageConfigurable()
        data = FakeRemoteDataConfigurable(fail_tables={"snapshots"})
        svc = RemoteSyncService(data, storage, state, SyncRuntimeState())
        svc.execute_sync("jwt", _USER_REMOTE_ID)

        # set_storage_paths NOT called
        sp_calls = [c for c in state.call_log if c[0] == "set_storage_paths"]
        assert sp_calls == []
        # get_storage_paths still returns None
        paths = state.get_storage_paths(5)
        assert paths.raw_storage_path is None

    def test_no_mark_synced_on_metadata_fail(self, tmp_path):
        raw_dir = tmp_path / "monitorings" / "3" / "snapshots" / "raw"
        raw_dir.mkdir(parents=True)
        raw_file = raw_dir / "snapshot_000007.jpg"
        raw_file.write_bytes(b"x")

        state = FakeSyncState()
        state.add_entity("monitoring", {
            "id": 3, "remote_id": "mon-uuid", "remote_sync_status": "synced",
        })
        state.add_entity("snapshot", {
            "id": 5, "monitoring_id": 3, "image_path": str(raw_file),
            "frame_index": 7, "remote_id": None, "remote_sync_status": "pending",
        })

        storage = FakeRemoteStorageConfigurable()
        data = FakeRemoteDataConfigurable(fail_tables={"snapshots"})
        svc = RemoteSyncService(data, storage, state, SyncRuntimeState())
        svc.execute_sync("jwt", _USER_REMOTE_ID)

        synced_calls = [c for c in state.call_log if c[0] == "synced" and c[1] == "snapshot"]
        assert synced_calls == []

    def test_counters_storage_ok_data_fail(self, tmp_path):
        raw_dir = tmp_path / "monitorings" / "3" / "snapshots" / "raw"
        raw_dir.mkdir(parents=True)
        raw_file = raw_dir / "snapshot_000007.jpg"
        raw_file.write_bytes(b"x")

        state = FakeSyncState()
        state.add_entity("monitoring", {
            "id": 3, "remote_id": "mon-uuid", "remote_sync_status": "synced",
        })
        state.add_entity("snapshot", {
            "id": 5, "monitoring_id": 3, "image_path": str(raw_file),
            "frame_index": 7, "remote_id": None, "remote_sync_status": "pending",
        })

        storage = FakeRemoteStorageConfigurable()
        data = FakeRemoteDataConfigurable(fail_tables={"snapshots"})
        svc = RemoteSyncService(data, storage, state, SyncRuntimeState())
        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        assert result.entities_failed == 1
        assert result.images_uploaded == 1
        assert result.images_failed == 0
        assert result.success is False


# ---------------------------------------------------------------------------
# Test 7: Raw local missing
# ---------------------------------------------------------------------------


class TestRawLocalMissing:
    """Missing raw file → error, no Storage, no metadata upsert."""

    def test_snapshot_error(self, tmp_path):
        missing_path = str(tmp_path / "missing.jpg")

        state = FakeSyncState()
        state.add_entity("monitoring", {
            "id": 3, "remote_id": "mon-uuid", "remote_sync_status": "synced",
        })
        state.add_entity("snapshot", {
            "id": 5, "monitoring_id": 3, "image_path": missing_path,
            "frame_index": 0, "remote_id": None, "remote_sync_status": "pending",
        })

        storage = FakeRemoteStorageConfigurable()
        data = FakeRemoteDataConfigurable()
        svc = RemoteSyncService(data, storage, state, SyncRuntimeState())
        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        snap = state._find("snapshot", 5)
        assert snap["remote_sync_status"] == "error"
        assert snap["remote_id"] is not None  # UUID reserved
        assert snap["image_path"] == missing_path  # preserved
        assert len(storage.calls) == 0  # no Storage
        tables = [c["table"] for c in data.calls]
        assert "snapshots" not in tables  # no metadata upsert
        assert result.entities_failed == 1
        assert result.images_uploaded == 0
        assert result.images_failed == 1


# ---------------------------------------------------------------------------
# Test 8: Annotated + metadata fail preserves both files
# ---------------------------------------------------------------------------


class TestAnnotatedAndMetadataFailPreservesFiles:
    """Both uploads succeed, metadata fails — both local files intact."""

    def test_files_preserved(self, tmp_path):
        raw_dir = tmp_path / "monitorings" / "3" / "snapshots" / "raw"
        raw_dir.mkdir(parents=True)
        raw_file = raw_dir / "snapshot_000007.jpg"
        raw_file.write_bytes(b"original-raw-bytes")

        ann_dir = tmp_path / "monitorings" / "3" / "annotated_snapshots"
        ann_dir.mkdir(parents=True)
        ann_file = ann_dir / "snapshot_000007.jpg"
        ann_file.write_bytes(b"original-annotated-bytes")

        state = FakeSyncState()
        state.add_entity("monitoring", {
            "id": 3, "remote_id": "mon-uuid", "remote_sync_status": "synced",
        })
        state.add_entity("snapshot", {
            "id": 5, "monitoring_id": 3, "image_path": str(raw_file),
            "frame_index": 7, "captured_at": datetime(2025, 1, 1),
            "change_score": 0.5, "has_detections": True,
            "remote_id": None, "remote_sync_status": "pending",
        })

        storage = FakeRemoteStorageConfigurable()  # all succeed
        data = FakeRemoteDataConfigurable(fail_tables={"snapshots"})
        svc = RemoteSyncService(data, storage, state, SyncRuntimeState())
        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        # Files intact
        assert raw_file.exists()
        assert raw_file.read_bytes() == b"original-raw-bytes"
        assert ann_file.exists()
        assert ann_file.read_bytes() == b"original-annotated-bytes"

        # Both uploaded remotely (simulated)
        assert len(storage.uploaded_objects) == 2

        # Counters
        assert result.images_uploaded == 2
        assert result.images_failed == 0
        assert result.entities_failed == 1


# ---------------------------------------------------------------------------
# Test 9: Error of one entity doesn't corrupt another
# ---------------------------------------------------------------------------


class TestUnrelatedEntityIntegrity:
    """Failure of one entity does not modify an unrelated synced entity."""

    def test_synced_entity_unchanged(self):
        state = FakeSyncState()
        # Greenhouse A: will fail
        state.add_entity("greenhouse", {
            "id": 1, "owner_user_id": 99, "name": "GH Failing",
            "remote_id": None, "remote_sync_status": "pending",
        })
        # Greenhouse B: already synced, should be untouched
        state.add_entity("greenhouse", {
            "id": 2, "name": "GH Untouched", "location": "South",
            "remote_id": "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
            "remote_sync_status": "synced",
        })

        data = FakeRemoteDataConfigurable(fail_tables={"greenhouses"})
        svc = RemoteSyncService(data, FakeRemoteStorageConfigurable(), state, SyncRuntimeState())
        svc.execute_sync("jwt", _USER_REMOTE_ID)

        # B must be completely untouched
        gh_b = state._find("greenhouse", 2)
        assert gh_b["name"] == "GH Untouched"
        assert gh_b["location"] == "South"
        assert gh_b["remote_id"] == "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
        assert gh_b["remote_sync_status"] == "synced"


# ===========================================================================
# Task 13.4 — Crash-recovery + UUID reuse
# ===========================================================================


class TestRetryReusesExactUuid:
    """Entities in error/syncing reuse their existing remote_id exactly."""

    @pytest.mark.parametrize("stale_status", ["error", "syncing"])
    def test_same_uuid_on_retry(self, stale_status):
        known_uuid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        state = FakeSyncState()
        state.add_entity("greenhouse", {
            "id": 1, "owner_user_id": 99, "name": "GH Retry",
            "remote_id": known_uuid, "remote_sync_status": stale_status,
        })

        data = FakeRemoteDataConfigurable()
        svc = RemoteSyncService(data, FakeRemoteStorageConfigurable(), state, SyncRuntimeState())
        result = svc.execute_sync("jwt", _USER_REMOTE_ID)

        # Exact same UUID used in payload
        assert data.calls[0]["data"]["id"] == known_uuid
        # Final state
        gh = state._find("greenhouse", 1)
        assert gh["remote_id"] == known_uuid
        assert gh["remote_sync_status"] == "synced"
        assert result.entities_synced == 1


class SimulatedCrash(BaseException):
    """Simulates abrupt process termination (not caught by except Exception)."""
    pass


class CrashOnMarkSyncedState(FakeSyncState):
    """FakeSyncState that crashes once on mark_synced for a specific entity."""

    def __init__(self, crash_entity_type: str, crash_local_id: int):
        super().__init__()
        self._crash_entity_type = crash_entity_type
        self._crash_local_id = crash_local_id
        self._crash_armed = True

    def mark_synced(self, entity_type: str, local_id: int, remote_id: str) -> None:
        if (self._crash_armed and entity_type == self._crash_entity_type
                and local_id == self._crash_local_id):
            self._crash_armed = False
            raise SimulatedCrash("Process terminated between upsert and mark_synced")
        super().mark_synced(entity_type, local_id, remote_id)


class TestCrashRecoveryBetweenUpsertAndMarkSynced:
    """Crash after remote upsert but before mark_synced → retry with same UUID."""

    def test_uuid_preserved_across_crash_and_retry(self):
        state = CrashOnMarkSyncedState("greenhouse", 1)
        state.add_entity("greenhouse", {
            "id": 1, "owner_user_id": 99, "name": "GH Crash",
            "remote_id": None, "remote_sync_status": "pending",
        })

        data1 = FakeRemoteDataConfigurable()
        svc = RemoteSyncService(data1, FakeRemoteStorageConfigurable(), state, SyncRuntimeState())

        # First execution: crashes on mark_synced
        with pytest.raises(SimulatedCrash):
            svc.execute_sync("jwt", _USER_REMOTE_ID)

        # After crash: UUID was reserved, status is syncing (mark_syncing ran)
        gh = state._find("greenhouse", 1)
        first_uuid = gh["remote_id"]
        assert first_uuid is not None
        assert gh["remote_sync_status"] == "syncing"
        first_payload_id = data1.calls[0]["data"]["id"]
        assert first_payload_id == first_uuid

        # Second execution: retry on the SAME state (syncing is retryable)
        data2 = FakeRemoteDataConfigurable()
        svc2 = RemoteSyncService(data2, FakeRemoteStorageConfigurable(), state, SyncRuntimeState())
        result = svc2.execute_sync("jwt", _USER_REMOTE_ID)

        # Same UUID reused
        second_payload_id = data2.calls[0]["data"]["id"]
        assert second_payload_id == first_uuid

        # Final state: synced
        gh = state._find("greenhouse", 1)
        assert gh["remote_id"] == first_uuid
        assert gh["remote_sync_status"] == "synced"
        assert result.success is True
        assert result.entities_synced == 1
