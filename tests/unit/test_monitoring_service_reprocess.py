"""Task 11.6 tests: reprocess_monitoring + STRICT sync guard.

Uses a real temp SQLite DB so the strict guard (has_synced_descendants) runs
real SQL across Monitoring / Snapshot / DetectionInspectionResult /
MonitoringMetrics. Video I/O is faked at the boundaries that matter:
    - a real (tiny) monitoring.mp4 file exists under BASE_DIR/outputs so the
      precondition "file exists" holds and reprocess never deletes it;
    - the analysis thread is intercepted (not actually run) to assert launch
      without loading heavy inference.

Covers:
    - allowed reprocess when NOTHING is synced: clears results, resets to
      analyzing, launches analysis on the SAME video; video preserved.
    - BLOCKED reprocess per entity type (Monitoring / Snapshot /
      DetectionInspectionResult / MonitoringMetrics synced) → nothing deleted.
    - precondition rejections: not terminal, video_path null, file missing,
      active session, reprocess-in-progress.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from src.application.services.monitoring_service import (
    MonitoringService,
    ReprocessPreconditionError,
    ReprocessBlockedBySyncError,
    ReprocessInProgressError,
    ActiveSessionError,
)
from src.domain.entities.monitoring import Monitoring
from src.domain.entities.snapshot import Snapshot
from src.domain.entities.inspection_result import DetectionInspectionResult
from src.domain.entities.monitoring_metrics import MonitoringMetrics
from src.domain.value_objects.monitoring_status import MonitoringState


@pytest.fixture
def env(tmp_path, monkeypatch):
    from src.infrastructure.persistence.database import DatabaseManager
    from src.infrastructure.persistence.repositories import (
        SqlMonitoringRepository,
        SqlSnapshotRepository,
        SqlInspectionResultRepository,
        SqlMonitoringMetricsRepository,
        SqlModuleRepository,
        SqlGreenhouseRepository,
    )
    from src.application.services.monitoring_runtime_registry import (
        MonitoringRuntimeRegistry,
    )

    manager = DatabaseManager(db_path=str(tmp_path / "rp.db"))
    manager.init_db()

    monkeypatch.setattr("src.infrastructure.config.settings.BASE_DIR", tmp_path)
    monkeypatch.setattr("src.infrastructure.config.settings.OUTPUTS_DIR", tmp_path / "outputs")
    # Background analysis threads build their own DatabaseManager(); force ours.
    monkeypatch.setattr(
        "src.infrastructure.persistence.database.DatabaseManager",
        lambda *a, **k: manager,
    )

    session = manager.get_session()
    gh_repo = SqlGreenhouseRepository(session=session)
    mod_repo = SqlModuleRepository(session=session)
    mon_repo = SqlMonitoringRepository(session=session)
    snap_repo = SqlSnapshotRepository(session=session)
    insp_repo = SqlInspectionResultRepository(session=session)
    metrics_repo = SqlMonitoringMetricsRepository(session=session)

    from src.domain.entities.greenhouse import Greenhouse
    from src.domain.entities.module import Module

    gh = gh_repo.create(Greenhouse(name="GH RP", location="lab"))
    module = mod_repo.create(gh.id, Module(greenhouse_id=gh.id, name="Mod RP"))
    session.commit()

    service = MonitoringService(
        monitoring_repo=mon_repo,
        snapshot_repo=snap_repo,
        inspection_result_repo=insp_repo,
        metrics_repo=metrics_repo,
        module_repo=mod_repo,
        runtime_registry=MonitoringRuntimeRegistry(),
    )

    return {
        "manager": manager, "session": session, "service": service,
        "mon_repo": mon_repo, "snap_repo": snap_repo, "insp_repo": insp_repo,
        "metrics_repo": metrics_repo, "module_id": module.id, "tmp_path": tmp_path,
    }


def _write_video(env, monitoring_id, rel=None):
    rel = rel or f"outputs/monitorings/{monitoring_id}/video/monitoring.mp4"
    abs_path = env["tmp_path"] / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(b"\x00\x01fake-video-bytes")
    return rel, abs_path


def _seed_completed_with_children(env):
    """Create a completed monitoring with 1 snapshot, 1 result, 1 metrics + video."""
    mon = env["mon_repo"].create(env["module_id"], Monitoring(module_id=env["module_id"]))
    rel, abs_path = _write_video(env, mon.id)
    env["mon_repo"].update_video_path(mon.id, rel)

    snap = env["snap_repo"].create(
        mon.id,
        Snapshot(
            monitoring_id=mon.id,
            image_path=f"outputs/monitorings/{mon.id}/snapshots/raw/snapshot_000000.jpg",
            frame_index=0,
            has_detections=True,
        ),
    )
    env["insp_repo"].create(
        snap.id,
        DetectionInspectionResult(
            snapshot_id=snap.id,
            detection_index=1,
            bbox_x1=0, bbox_y1=0, bbox_x2=10, bbox_y2=10,
            detection_score=0.9,
            health_label="healthy",
            health_confidence=0.8,
            maturity_stage="red",
            maturity_percent=90.0,
        ),
    )
    # Force terminal completed BEFORE creating metrics (metrics repo requires a
    # completed/aborted monitoring).
    from src.infrastructure.persistence.models.monitoring_model import MonitoringModel
    m = env["session"].get(MonitoringModel, mon.id)
    m.status = MonitoringState.COMPLETED.value
    env["session"].commit()
    env["metrics_repo"].create(
        mon.id,
        MonitoringMetrics(
            monitoring_id=mon.id, total_tomatoes=1, healthy_count=1, unhealthy_count=0,
            pct_healthy=100.0, pct_unhealthy=0.0, snapshots_with_detections=1,
        ),
    )
    return mon.id, snap.id, rel, abs_path


def _set_synced(env, *, monitoring=False, snapshot=False, result=False, metrics=False,
                monitoring_id=None):
    from src.infrastructure.persistence.models.monitoring_model import MonitoringModel
    from src.infrastructure.persistence.models.snapshot_model import SnapshotModel
    from src.infrastructure.persistence.models.inspection_result_model import (
        InspectionResultModel,
    )
    from src.infrastructure.persistence.models.monitoring_metrics_model import (
        MonitoringMetricsModel,
    )
    s = env["session"]
    if monitoring:
        s.get(MonitoringModel, monitoring_id).remote_sync_status = "synced"
    if snapshot:
        row = s.query(SnapshotModel).filter(SnapshotModel.monitoring_id == monitoring_id).first()
        row.remote_sync_status = "synced"
    if result:
        row = (
            s.query(InspectionResultModel)
            .join(SnapshotModel, InspectionResultModel.snapshot_id == SnapshotModel.id)
            .filter(SnapshotModel.monitoring_id == monitoring_id)
            .first()
        )
        row.remote_sync_status = "synced"
    if metrics:
        row = (
            s.query(MonitoringMetricsModel)
            .filter(MonitoringMetricsModel.monitoring_id == monitoring_id)
            .first()
        )
        row.remote_sync_status = "synced"
    s.commit()


def _intercept_analysis(monkeypatch):
    """Prevent the reprocess analysis thread from running real inference."""
    launched = {}

    class _NoThread:
        def __init__(self, *a, **k):
            launched["name"] = k.get("name")
            launched["args"] = k.get("args")
        def start(self):
            launched["started"] = True
        def is_alive(self):
            return True

    monkeypatch.setattr("threading.Thread", _NoThread)
    return launched


# --------------------------------------------------------------------------- #
# Allowed reprocess (nothing synced)
# --------------------------------------------------------------------------- #

def test_reprocess_allowed_when_nothing_synced(env, monkeypatch):
    mon_id, snap_id, rel, abs_path = _seed_completed_with_children(env)
    launched = _intercept_analysis(monkeypatch)

    env["service"].reprocess_monitoring(mon_id, config="CFG")

    # Analysis launched on the SAME video with the provided config.
    assert launched.get("started") is True
    assert launched["args"] == (mon_id, rel, "CFG")
    # Results cleared: no snapshots, no metrics; state reset to analyzing.
    assert env["snap_repo"].get_by_monitoring(mon_id) == []
    assert env["metrics_repo"].get_by_monitoring(mon_id) is None
    assert env["mon_repo"].get_by_id(mon_id).status == MonitoringState.ANALYZING.value
    # Video preserved + video_path unchanged.
    assert abs_path.exists()
    assert env["mon_repo"].get_by_id(mon_id).video_path == rel


# --------------------------------------------------------------------------- #
# Blocked per entity type (strict guard) — nothing deleted
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("which", ["monitoring", "snapshot", "result", "metrics"])
def test_reprocess_blocked_when_any_entity_synced(env, monkeypatch, which):
    mon_id, snap_id, rel, abs_path = _seed_completed_with_children(env)
    _set_synced(env, monitoring_id=mon_id, **{which: True})

    launched = _intercept_analysis(monkeypatch)

    with pytest.raises(ReprocessBlockedBySyncError):
        env["service"].reprocess_monitoring(mon_id, config="CFG")

    # NOTHING deleted, no analysis launched, state unchanged (completed), video kept.
    assert launched.get("started") is None
    assert len(env["snap_repo"].get_by_monitoring(mon_id)) == 1
    assert env["metrics_repo"].get_by_monitoring(mon_id) is not None
    assert env["mon_repo"].get_by_id(mon_id).status == MonitoringState.COMPLETED.value
    assert abs_path.exists()


# --------------------------------------------------------------------------- #
# Precondition rejections
# --------------------------------------------------------------------------- #

def test_reprocess_rejected_when_not_terminal(env, monkeypatch):
    mon = env["mon_repo"].create(env["module_id"], Monitoring(module_id=env["module_id"]))
    env["mon_repo"].update_status(mon.id, MonitoringState.RUNNING.value)
    rel, _ = _write_video(env, mon.id)
    env["mon_repo"].update_video_path(mon.id, rel)
    env["session"].commit()
    with pytest.raises(ReprocessPreconditionError):
        env["service"].reprocess_monitoring(mon.id)

def test_reprocess_rejected_when_video_path_null(env):
    mon = env["mon_repo"].create(env["module_id"], Monitoring(module_id=env["module_id"]))
    from src.infrastructure.persistence.models.monitoring_model import MonitoringModel
    m = env["session"].get(MonitoringModel, mon.id)
    m.status = MonitoringState.COMPLETED.value
    env["session"].commit()
    with pytest.raises(ReprocessPreconditionError):
        env["service"].reprocess_monitoring(mon.id)

def test_reprocess_rejected_when_file_missing(env):
    mon = env["mon_repo"].create(env["module_id"], Monitoring(module_id=env["module_id"]))
    env["mon_repo"].update_video_path(
        mon.id, f"outputs/monitorings/{mon.id}/video/monitoring.mp4"
    )
    from src.infrastructure.persistence.models.monitoring_model import MonitoringModel
    m = env["session"].get(MonitoringModel, mon.id)
    m.status = MonitoringState.COMPLETED.value
    env["session"].commit()
    with pytest.raises(ReprocessPreconditionError):
        env["service"].reprocess_monitoring(mon.id)

def test_reprocess_rejected_when_module_has_active_session(env):
    mon_id, snap_id, rel, abs_path = _seed_completed_with_children(env)
    # Another active session on the same module.
    other = env["mon_repo"].create(env["module_id"], Monitoring(module_id=env["module_id"]))
    env["mon_repo"].update_status(other.id, MonitoringState.RUNNING.value)
    env["session"].commit()
    with pytest.raises(ActiveSessionError):
        env["service"].reprocess_monitoring(mon_id)

def test_reprocess_rejected_when_reprocess_in_progress(env, monkeypatch):
    mon_id, snap_id, rel, abs_path = _seed_completed_with_children(env)
    # Simulate an in-progress reprocess by pre-claiming finalization.
    env["service"]._registry.claim_finalization(mon_id)
    with pytest.raises(ReprocessInProgressError):
        env["service"].reprocess_monitoring(mon_id)
