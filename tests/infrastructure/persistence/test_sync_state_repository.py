"""Unit tests for SyncStateRepository.

Validates:
- All 7 entity types are supported
- get_pending_entities returns pending/error/syncing, excludes synced
- Stale syncing is retryable after crash/restart
- Results are ordered by id ASC and returned as clean dicts
- reserve_remote_id is durable and idempotent for same UUID
- reserve_remote_id rejects conflicting UUID with ValueError
- mark_syncing, mark_error, mark_synced transition correctly
- mark_synced sets UTC-naive last_synced_at
- mark_synced without prior UUID works defensively
- mark_synced with conflicting UUID raises ValueError + rollback
- Non-existent IDs raise ValueError for mutations
- Invalid entity_type raises ValueError
- get/set_storage_paths works only on Snapshot
- Optional None paths are valid
- image_path and other fields are not altered by set_storage_paths
- Legacy sync_status is never touched
- All mutations commit durably (verified via fresh session)

Spec 017 — Supabase Remote Sync.
Requirements: 11.1–11.7
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.application.interfaces.sync_state_port import StoragePaths
from src.infrastructure.persistence.models.activity_log_model import ActivityLogModel
from src.infrastructure.persistence.models.activity_type_model import ActivityTypeModel
from src.infrastructure.persistence.models.base import Base
from src.infrastructure.persistence.models.greenhouse_model import GreenhouseModel
from src.infrastructure.persistence.models.inspection_result_model import (
    InspectionResultModel,
)
from src.infrastructure.persistence.models.module_model import ModuleModel
from src.infrastructure.persistence.models.monitoring_metrics_model import (
    MonitoringMetricsModel,
)
from src.infrastructure.persistence.models.monitoring_model import MonitoringModel
from src.infrastructure.persistence.models.snapshot_model import SnapshotModel
from src.infrastructure.persistence.models.user_model import UserModel
from src.infrastructure.persistence.sync_state_repository import SyncStateRepository

# Dummy UUIDs
UUID_A = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
UUID_B = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    """In-memory SQLite engine shared across sessions via StaticPool."""
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
    """Session factory bound to the in-memory engine."""
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


@pytest.fixture
def repo(session_factory):
    """SyncStateRepository instance."""
    return SyncStateRepository(session_factory=session_factory)


@pytest.fixture
def seeded_ids(session_factory):
    """Seed minimal related entities and return their IDs as a dict."""
    sess = session_factory()

    user = UserModel(
        full_name="Test Op", email="op@test.com",
        password_hash="hash", role="operator",
    )
    sess.add(user)
    sess.flush()

    activity_type = ActivityTypeModel(
        code="riego", name="Riego", category="mantenimiento",
    )
    sess.add(activity_type)
    sess.flush()

    gh = GreenhouseModel(name="GH1")
    sess.add(gh)
    sess.flush()

    mod = ModuleModel(greenhouse_id=gh.id, name="Mod1")
    sess.add(mod)
    sess.flush()

    mon = MonitoringModel(module_id=mod.id, width_m=5.0, length_m=2.0)
    sess.add(mon)
    sess.flush()

    snap = SnapshotModel(
        monitoring_id=mon.id, image_path="data/images/test.jpg", frame_index=0,
    )
    sess.add(snap)
    sess.flush()

    metrics = MonitoringMetricsModel(
        monitoring_id=mon.id, total_tomatoes=10, healthy_count=8,
        unhealthy_count=2, pct_healthy=80.0, pct_unhealthy=20.0,
        snapshots_with_detections=1,
    )
    sess.add(metrics)
    sess.flush()

    ir = InspectionResultModel(
        snapshot_id=snap.id, detection_index=0,
        bbox_x1=10, bbox_y1=20, bbox_x2=50, bbox_y2=60,
        detection_score=0.95, health_label="healthy", health_confidence=0.9,
    )
    sess.add(ir)
    sess.flush()

    al = ActivityLogModel(
        module_id=mod.id, activity_type_id=activity_type.id,
        user_id=user.id, notes="Test activity",
    )
    sess.add(al)
    sess.flush()

    sess.commit()

    ids = {
        "greenhouse": gh.id,
        "module": mod.id,
        "monitoring": mon.id,
        "snapshot": snap.id,
        "monitoring_metrics": metrics.id,
        "inspection_result": ir.id,
        "activity_log": al.id,
    }
    sess.close()
    return ids


# ===========================================================================
# 5. All 7 entity types supported
# ===========================================================================


class TestAllEntityTypesSupported:
    """get_pending_entities works for all 7 entity types."""

    @pytest.mark.parametrize("entity_type", [
        "greenhouse", "module", "monitoring", "snapshot",
        "monitoring_metrics", "inspection_result", "activity_log",
    ])
    def test_get_pending_returns_list(self, repo, seeded_ids, entity_type):
        result = repo.get_pending_entities(entity_type)
        assert isinstance(result, list)
        assert len(result) >= 1
        assert isinstance(result[0], dict)
        assert result[0]["id"] == seeded_ids[entity_type]


# ===========================================================================
# 6. get_pending_entities status filtering
# ===========================================================================


class TestGetPendingEntitiesFiltering:
    """Only pending/error/syncing are returned; synced is excluded."""

    def test_pending_included(self, repo, seeded_ids):
        result = repo.get_pending_entities("greenhouse")
        assert any(r["id"] == seeded_ids["greenhouse"] for r in result)

    def test_error_included(self, repo, seeded_ids):
        repo.mark_error("greenhouse", seeded_ids["greenhouse"], "err")
        result = repo.get_pending_entities("greenhouse")
        assert any(r["remote_sync_status"] == "error" for r in result)

    def test_syncing_included(self, repo, seeded_ids):
        repo.mark_syncing("greenhouse", seeded_ids["greenhouse"])
        result = repo.get_pending_entities("greenhouse")
        assert any(r["remote_sync_status"] == "syncing" for r in result)

    def test_synced_excluded(self, repo, seeded_ids):
        gid = seeded_ids["greenhouse"]
        repo.reserve_remote_id("greenhouse", gid, UUID_A)
        repo.mark_synced("greenhouse", gid, UUID_A)
        result = repo.get_pending_entities("greenhouse")
        assert not any(r["id"] == gid for r in result)

    def test_order_by_id_asc(self, repo, session_factory):
        # Create two additional greenhouses
        sess = session_factory()
        gh2 = GreenhouseModel(name="GH2")
        gh3 = GreenhouseModel(name="GH3")
        sess.add_all([gh2, gh3])
        sess.commit()
        sess.close()

        result = repo.get_pending_entities("greenhouse")
        ids = [r["id"] for r in result]
        assert ids == sorted(ids)


# ===========================================================================
# 7. Stale syncing
# ===========================================================================


class TestStaleSyncing:
    """Persisted 'syncing' is treated as retryable after crash."""

    def test_stale_syncing_in_pending(self, repo, seeded_ids, session_factory):
        gid = seeded_ids["greenhouse"]
        repo.mark_syncing("greenhouse", gid)

        # Simulate restart: use a fresh repository call
        result = repo.get_pending_entities("greenhouse")
        assert any(r["id"] == gid and r["remote_sync_status"] == "syncing" for r in result)


# ===========================================================================
# 8. Clean dict
# ===========================================================================


class TestCleanDict:
    """get_pending_entities returns clean dicts without ORM internals."""

    def test_no_sa_instance_state(self, repo, seeded_ids):
        result = repo.get_pending_entities("greenhouse")
        assert len(result) > 0
        for d in result:
            assert "_sa_instance_state" not in d

    def test_no_relationship_keys(self, repo, seeded_ids):
        result = repo.get_pending_entities("greenhouse")
        for d in result:
            assert "modules" not in d

    def test_contains_expected_keys(self, repo, seeded_ids):
        result = repo.get_pending_entities("greenhouse")
        d = result[0]
        assert "id" in d
        assert "name" in d
        assert "remote_id" in d
        assert "remote_sync_status" in d


# ===========================================================================
# 9. reserve_remote_id durable
# ===========================================================================


class TestReserveRemoteId:
    """reserve_remote_id persists UUID durably."""

    def test_reserve_sets_uuid(self, repo, seeded_ids, session_factory):
        gid = seeded_ids["greenhouse"]
        repo.reserve_remote_id("greenhouse", gid, UUID_A)

        # Verify via fresh session
        sess = session_factory()
        gh = sess.get(GreenhouseModel, gid)
        assert gh.remote_id == UUID_A
        assert gh.remote_sync_status == "pending"
        assert gh.remote_sync_error is None
        sess.close()


# ===========================================================================
# 10. Reserve same UUID — idempotent
# ===========================================================================


class TestReserveIdempotent:
    """Reserving the same UUID twice is a no-op."""

    def test_same_uuid_no_error(self, repo, seeded_ids):
        gid = seeded_ids["greenhouse"]
        repo.reserve_remote_id("greenhouse", gid, UUID_A)
        # Second call with same UUID
        repo.reserve_remote_id("greenhouse", gid, UUID_A)

    def test_same_uuid_resets_to_pending(self, repo, seeded_ids, session_factory):
        gid = seeded_ids["greenhouse"]
        repo.reserve_remote_id("greenhouse", gid, UUID_A)
        repo.mark_error("greenhouse", gid, "temp err")
        # Re-reserve same UUID
        repo.reserve_remote_id("greenhouse", gid, UUID_A)

        sess = session_factory()
        gh = sess.get(GreenhouseModel, gid)
        assert gh.remote_id == UUID_A
        assert gh.remote_sync_status == "pending"
        assert gh.remote_sync_error is None
        sess.close()


# ===========================================================================
# 11. Reserve different UUID — conflict
# ===========================================================================


class TestReserveConflict:
    """Reserving a different UUID on same entity raises ValueError."""

    def test_raises_value_error(self, repo, seeded_ids):
        gid = seeded_ids["greenhouse"]
        repo.reserve_remote_id("greenhouse", gid, UUID_A)
        with pytest.raises(ValueError, match="already has remote_id"):
            repo.reserve_remote_id("greenhouse", gid, UUID_B)

    def test_original_uuid_preserved(self, repo, seeded_ids, session_factory):
        gid = seeded_ids["greenhouse"]
        repo.reserve_remote_id("greenhouse", gid, UUID_A)
        with pytest.raises(ValueError):
            repo.reserve_remote_id("greenhouse", gid, UUID_B)

        sess = session_factory()
        gh = sess.get(GreenhouseModel, gid)
        assert gh.remote_id == UUID_A
        sess.close()


# ===========================================================================
# 12. get_remote_id
# ===========================================================================


class TestGetRemoteId:
    """get_remote_id returns UUID, None, or raises ValueError."""

    def test_returns_uuid_when_set(self, repo, seeded_ids):
        gid = seeded_ids["greenhouse"]
        repo.reserve_remote_id("greenhouse", gid, UUID_A)
        assert repo.get_remote_id("greenhouse", gid) == UUID_A

    def test_returns_none_when_null(self, repo, seeded_ids):
        gid = seeded_ids["greenhouse"]
        assert repo.get_remote_id("greenhouse", gid) is None

    def test_returns_none_for_nonexistent_id(self, repo, seeded_ids):
        assert repo.get_remote_id("greenhouse", 9999) is None

    def test_raises_for_invalid_entity_type(self, repo):
        with pytest.raises(ValueError, match="Unsupported entity_type"):
            repo.get_remote_id("invalid_type", 1)


# ===========================================================================
# 13. mark_syncing
# ===========================================================================


class TestMarkSyncing:
    """mark_syncing transitions to syncing and clears error."""

    def test_transitions_to_syncing(self, repo, seeded_ids, session_factory):
        gid = seeded_ids["greenhouse"]
        repo.reserve_remote_id("greenhouse", gid, UUID_A)
        repo.mark_error("greenhouse", gid, "previous error")
        repo.mark_syncing("greenhouse", gid)

        sess = session_factory()
        gh = sess.get(GreenhouseModel, gid)
        assert gh.remote_sync_status == "syncing"
        assert gh.remote_sync_error is None
        assert gh.remote_id == UUID_A
        sess.close()

    def test_nonexistent_raises(self, repo):
        with pytest.raises(ValueError, match="not found"):
            repo.mark_syncing("greenhouse", 9999)


# ===========================================================================
# 14. mark_error
# ===========================================================================


class TestMarkError:
    """mark_error sets status and message, preserves remote_id."""

    def test_sets_error(self, repo, seeded_ids, session_factory):
        gid = seeded_ids["greenhouse"]
        repo.reserve_remote_id("greenhouse", gid, UUID_A)
        repo.mark_synced("greenhouse", gid, UUID_A)
        original_synced_at = None
        sess = session_factory()
        gh = sess.get(GreenhouseModel, gid)
        original_synced_at = gh.last_synced_at
        sess.close()

        repo.mark_error("greenhouse", gid, "temporary failure")

        sess = session_factory()
        gh = sess.get(GreenhouseModel, gid)
        assert gh.remote_sync_status == "error"
        assert gh.remote_sync_error == "temporary failure"
        assert gh.remote_id == UUID_A
        assert gh.last_synced_at == original_synced_at
        sess.close()

    def test_nonexistent_raises(self, repo):
        with pytest.raises(ValueError, match="not found"):
            repo.mark_error("greenhouse", 9999, "msg")


# ===========================================================================
# 15. mark_synced — normal flow
# ===========================================================================


class TestMarkSynced:
    """mark_synced sets synced status with UTC timestamp."""

    def test_normal_flow(self, repo, seeded_ids, session_factory):
        gid = seeded_ids["greenhouse"]
        repo.reserve_remote_id("greenhouse", gid, UUID_A)
        repo.mark_syncing("greenhouse", gid)
        repo.mark_synced("greenhouse", gid, UUID_A)

        sess = session_factory()
        gh = sess.get(GreenhouseModel, gid)
        assert gh.remote_sync_status == "synced"
        assert gh.remote_id == UUID_A
        assert gh.remote_sync_error is None
        assert gh.last_synced_at is not None
        # UTC naive
        assert gh.last_synced_at.tzinfo is None
        # Reasonable recency (within last 10 seconds)
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        assert now_utc - gh.last_synced_at < timedelta(seconds=10)
        sess.close()

    def test_excluded_from_pending_after_synced(self, repo, seeded_ids):
        gid = seeded_ids["greenhouse"]
        repo.reserve_remote_id("greenhouse", gid, UUID_A)
        repo.mark_synced("greenhouse", gid, UUID_A)
        result = repo.get_pending_entities("greenhouse")
        assert not any(r["id"] == gid for r in result)


# ===========================================================================
# 16. mark_synced without prior UUID
# ===========================================================================


class TestMarkSyncedWithoutPriorUuid:
    """mark_synced defensively assigns UUID when remote_id is None."""

    def test_assigns_uuid(self, repo, seeded_ids, session_factory):
        gid = seeded_ids["greenhouse"]
        # No prior reserve_remote_id
        repo.mark_synced("greenhouse", gid, UUID_A)

        sess = session_factory()
        gh = sess.get(GreenhouseModel, gid)
        assert gh.remote_id == UUID_A
        assert gh.remote_sync_status == "synced"
        assert gh.last_synced_at is not None
        sess.close()


# ===========================================================================
# 17. mark_synced with conflicting UUID
# ===========================================================================


class TestMarkSyncedConflict:
    """mark_synced with wrong UUID raises ValueError and rolls back."""

    def test_raises_value_error(self, repo, seeded_ids):
        gid = seeded_ids["greenhouse"]
        repo.reserve_remote_id("greenhouse", gid, UUID_A)
        with pytest.raises(ValueError, match="already has remote_id"):
            repo.mark_synced("greenhouse", gid, UUID_B)

    def test_rollback_preserves_state(self, repo, seeded_ids, session_factory):
        gid = seeded_ids["greenhouse"]
        repo.reserve_remote_id("greenhouse", gid, UUID_A)
        repo.mark_syncing("greenhouse", gid)

        with pytest.raises(ValueError):
            repo.mark_synced("greenhouse", gid, UUID_B)

        sess = session_factory()
        gh = sess.get(GreenhouseModel, gid)
        assert gh.remote_id == UUID_A
        # Status should remain syncing (not synced)
        assert gh.remote_sync_status == "syncing"
        sess.close()


# ===========================================================================
# 18. Non-existent IDs in mutations
# ===========================================================================


class TestNonExistentIds:
    """Mutations on non-existent entities raise ValueError."""

    def test_reserve_nonexistent(self, repo):
        with pytest.raises(ValueError, match="not found"):
            repo.reserve_remote_id("greenhouse", 9999, UUID_A)

    def test_mark_syncing_nonexistent(self, repo):
        with pytest.raises(ValueError, match="not found"):
            repo.mark_syncing("greenhouse", 9999)

    def test_mark_synced_nonexistent(self, repo):
        with pytest.raises(ValueError, match="not found"):
            repo.mark_synced("greenhouse", 9999, UUID_A)

    def test_mark_error_nonexistent(self, repo):
        with pytest.raises(ValueError, match="not found"):
            repo.mark_error("greenhouse", 9999, "msg")


# ===========================================================================
# 19. Invalid entity_type
# ===========================================================================


class TestInvalidEntityType:
    """Invalid entity_type raises ValueError in all operations."""

    def test_get_pending(self, repo):
        with pytest.raises(ValueError, match="Unsupported entity_type"):
            repo.get_pending_entities("invalid_type")

    def test_get_remote_id(self, repo):
        with pytest.raises(ValueError, match="Unsupported entity_type"):
            repo.get_remote_id("invalid_type", 1)

    def test_reserve(self, repo):
        with pytest.raises(ValueError, match="Unsupported entity_type"):
            repo.reserve_remote_id("invalid_type", 1, UUID_A)

    def test_mark_syncing(self, repo):
        with pytest.raises(ValueError, match="Unsupported entity_type"):
            repo.mark_syncing("invalid_type", 1)

    def test_mark_synced(self, repo):
        with pytest.raises(ValueError, match="Unsupported entity_type"):
            repo.mark_synced("invalid_type", 1, UUID_A)

    def test_mark_error(self, repo):
        with pytest.raises(ValueError, match="Unsupported entity_type"):
            repo.mark_error("invalid_type", 1, "msg")


# ===========================================================================
# 20-24. Storage paths
# ===========================================================================


class TestStoragePaths:
    """get/set_storage_paths work correctly on Snapshot."""

    def test_initial_paths_are_none(self, repo, seeded_ids):
        paths = repo.get_storage_paths(seeded_ids["snapshot"])
        assert isinstance(paths, StoragePaths)
        assert paths.raw_storage_path is None
        assert paths.annotated_storage_path is None

    def test_set_and_get(self, repo, seeded_ids, session_factory):
        sid = seeded_ids["snapshot"]
        repo.set_storage_paths(sid, "remote/raw/snap.jpg", "remote/annotated/snap.jpg")

        paths = repo.get_storage_paths(sid)
        assert paths.raw_storage_path == "remote/raw/snap.jpg"
        assert paths.annotated_storage_path == "remote/annotated/snap.jpg"

    def test_partial_none_annotated(self, repo, seeded_ids):
        sid = seeded_ids["snapshot"]
        repo.set_storage_paths(sid, "remote/raw/snap.jpg", None)

        paths = repo.get_storage_paths(sid)
        assert paths.raw_storage_path == "remote/raw/snap.jpg"
        assert paths.annotated_storage_path is None

    def test_both_none(self, repo, seeded_ids):
        sid = seeded_ids["snapshot"]
        repo.set_storage_paths(sid, None, None)

        paths = repo.get_storage_paths(sid)
        assert paths.raw_storage_path is None
        assert paths.annotated_storage_path is None

    def test_does_not_alter_image_path(self, repo, seeded_ids, session_factory):
        sid = seeded_ids["snapshot"]
        repo.set_storage_paths(sid, "remote/raw.jpg", "remote/ann.jpg")

        sess = session_factory()
        snap = sess.get(SnapshotModel, sid)
        assert snap.image_path == "data/images/test.jpg"
        sess.close()

    def test_does_not_alter_remote_metadata(self, repo, seeded_ids, session_factory):
        sid = seeded_ids["snapshot"]
        repo.reserve_remote_id("snapshot", sid, UUID_A)
        repo.mark_syncing("snapshot", sid)

        repo.set_storage_paths(sid, "remote/raw.jpg", None)

        sess = session_factory()
        snap = sess.get(SnapshotModel, sid)
        assert snap.remote_id == UUID_A
        assert snap.remote_sync_status == "syncing"
        sess.close()

    def test_get_nonexistent_raises(self, repo):
        with pytest.raises(ValueError, match="not found"):
            repo.get_storage_paths(9999)

    def test_set_nonexistent_raises(self, repo):
        with pytest.raises(ValueError, match="not found"):
            repo.set_storage_paths(9999, "path", None)


# ===========================================================================
# 25. Legacy sync_status not touched
# ===========================================================================


class TestLegacySyncStatusPreserved:
    """Operations on remote_sync_status never alter legacy sync_status."""

    def test_monitoring_legacy_preserved(self, repo, seeded_ids, session_factory):
        mid = seeded_ids["monitoring"]

        # Set legacy sync_status to 'exported' directly
        sess = session_factory()
        mon = sess.get(MonitoringModel, mid)
        mon.sync_status = "exported"
        sess.commit()
        sess.close()

        # Run through remote sync operations
        repo.reserve_remote_id("monitoring", mid, UUID_A)
        repo.mark_syncing("monitoring", mid)
        repo.mark_error("monitoring", mid, "timeout")
        repo.mark_synced("monitoring", mid, UUID_A)

        # Verify legacy sync_status unchanged
        sess = session_factory()
        mon = sess.get(MonitoringModel, mid)
        assert mon.sync_status == "exported"
        assert mon.remote_sync_status == "synced"
        sess.close()

    def test_activity_log_legacy_preserved(self, repo, seeded_ids, session_factory):
        alid = seeded_ids["activity_log"]

        # Set legacy sync_status to 'exported' directly
        sess = session_factory()
        al = sess.get(ActivityLogModel, alid)
        al.sync_status = "exported"
        sess.commit()
        sess.close()

        # Run through remote sync operations
        repo.reserve_remote_id("activity_log", alid, UUID_A)
        repo.mark_syncing("activity_log", alid)
        repo.mark_synced("activity_log", alid, UUID_A)

        # Verify legacy sync_status unchanged
        sess = session_factory()
        al = sess.get(ActivityLogModel, alid)
        assert al.sync_status == "exported"
        assert al.remote_sync_status == "synced"
        sess.close()


# ===========================================================================
# 26. Session durability
# ===========================================================================


class TestSessionDurability:
    """Mutations commit before return (verified via fresh session reads)."""

    def test_reserve_durable(self, repo, seeded_ids, session_factory):
        gid = seeded_ids["greenhouse"]
        repo.reserve_remote_id("greenhouse", gid, UUID_A)

        sess = session_factory()
        gh = sess.get(GreenhouseModel, gid)
        assert gh.remote_id == UUID_A
        sess.close()

    def test_mark_syncing_durable(self, repo, seeded_ids, session_factory):
        gid = seeded_ids["greenhouse"]
        repo.mark_syncing("greenhouse", gid)

        sess = session_factory()
        gh = sess.get(GreenhouseModel, gid)
        assert gh.remote_sync_status == "syncing"
        sess.close()

    def test_mark_error_durable(self, repo, seeded_ids, session_factory):
        gid = seeded_ids["greenhouse"]
        repo.mark_error("greenhouse", gid, "fail")

        sess = session_factory()
        gh = sess.get(GreenhouseModel, gid)
        assert gh.remote_sync_status == "error"
        assert gh.remote_sync_error == "fail"
        sess.close()

    def test_mark_synced_durable(self, repo, seeded_ids, session_factory):
        gid = seeded_ids["greenhouse"]
        repo.reserve_remote_id("greenhouse", gid, UUID_A)
        repo.mark_synced("greenhouse", gid, UUID_A)

        sess = session_factory()
        gh = sess.get(GreenhouseModel, gid)
        assert gh.remote_sync_status == "synced"
        assert gh.last_synced_at is not None
        sess.close()

    def test_set_storage_paths_durable(self, repo, seeded_ids, session_factory):
        sid = seeded_ids["snapshot"]
        repo.set_storage_paths(sid, "r/raw.jpg", "r/ann.jpg")

        sess = session_factory()
        snap = sess.get(SnapshotModel, sid)
        assert snap.raw_storage_path == "r/raw.jpg"
        assert snap.annotated_storage_path == "r/ann.jpg"
        sess.close()


# ===========================================================================
# Pre-Task 13.1: ActivityLog enrichment with activity_type_code
# ===========================================================================


class TestActivityLogEnrichment:
    """get_pending_entities('activity_log') includes activity_type_code."""

    def test_contains_activity_type_code(self, repo, seeded_ids):
        result = repo.get_pending_entities("activity_log")
        assert len(result) >= 1
        al = result[0]
        assert "activity_type_code" in al
        assert al["activity_type_code"] == "riego"

    def test_preserves_activity_type_id(self, repo, seeded_ids):
        result = repo.get_pending_entities("activity_log")
        al = result[0]
        assert "activity_type_id" in al
        assert isinstance(al["activity_type_id"], int)

    def test_no_orm_object_in_dict(self, repo, seeded_ids):
        result = repo.get_pending_entities("activity_log")
        al = result[0]
        assert "_sa_instance_state" not in al
        assert "activity_type" not in al  # no ORM relationship object

    def test_greenhouse_does_not_have_activity_type_code(self, repo, seeded_ids):
        result = repo.get_pending_entities("greenhouse")
        assert len(result) >= 1
        gh = result[0]
        assert "activity_type_code" not in gh
