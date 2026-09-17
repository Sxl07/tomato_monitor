"""Tests for bulk repository methods (Spec 024, Block 7).

Verifies get_by_monitoring_ids on both MonitoringMetricsRepository and
InspectionResultRepository: multiple ids, empty list, non-existent ids, correct
grouping, and that the existing single-id methods still work.
"""

from src.domain.entities.greenhouse import Greenhouse
from src.domain.entities.inspection_result import DetectionInspectionResult
from src.domain.entities.module import Module
from src.domain.entities.monitoring import Monitoring
from src.domain.entities.monitoring_metrics import MonitoringMetrics
from src.domain.entities.snapshot import Snapshot
from src.infrastructure.persistence.models.monitoring_model import MonitoringModel
from src.infrastructure.persistence.repositories import (
    SqlGreenhouseRepository,
    SqlInspectionResultRepository,
    SqlModuleRepository,
    SqlMonitoringMetricsRepository,
    SqlMonitoringRepository,
    SqlSnapshotRepository,
)


def _mark_completed(session, monitoring_id):
    session.get(MonitoringModel, monitoring_id).status = "completed"
    session.flush()


def _env(session):
    gh_repo = SqlGreenhouseRepository(session=session)
    mod_repo = SqlModuleRepository(session=session)
    mon_repo = SqlMonitoringRepository(session=session)
    snap_repo = SqlSnapshotRepository(session=session)
    insp_repo = SqlInspectionResultRepository(session=session)
    metrics_repo = SqlMonitoringMetricsRepository(session=session)

    gh = gh_repo.create(Greenhouse(name="GH bulk"))
    module = mod_repo.create(gh.id, Module(greenhouse_id=gh.id, name="Mod bulk"))
    session.flush()
    return {
        "session": session,
        "mon_repo": mon_repo,
        "snap_repo": snap_repo,
        "insp_repo": insp_repo,
        "metrics_repo": metrics_repo,
        "module_id": module.id,
    }


def _create_monitoring(env, *, n_results=0, with_metrics=True):
    mon = env["mon_repo"].create(env["module_id"], Monitoring(module_id=env["module_id"]))
    if n_results:
        snap = env["snap_repo"].create(
            mon.id,
            Snapshot(
                monitoring_id=mon.id,
                image_path=f"outputs/monitorings/{mon.id}/snapshots/raw/s0.jpg",
                frame_index=0,
                has_detections=True,
            ),
        )
        for i in range(n_results):
            env["insp_repo"].create(
                snap.id,
                DetectionInspectionResult(
                    snapshot_id=snap.id,
                    detection_index=i,
                    bbox_x1=0, bbox_y1=0, bbox_x2=10, bbox_y2=10,
                    detection_score=0.9,
                    health_label="healthy",
                    health_confidence=0.8,
                    maturity_stage="red",
                    maturity_percent=90.0,
                ),
            )
    _mark_completed(env["session"], mon.id)
    if with_metrics:
        env["metrics_repo"].create(
            mon.id,
            MonitoringMetrics(
                monitoring_id=mon.id,
                total_tomatoes=n_results,
                healthy_count=n_results,
                unhealthy_count=0,
                pct_healthy=100.0,
                pct_unhealthy=0.0,
                snapshots_with_detections=1 if n_results else 0,
            ),
        )
    env["session"].flush()
    return mon.id


class TestMetricsBulk:
    def test_multiple_ids(self, session):
        env = _env(session)
        a = _create_monitoring(env)
        b = _create_monitoring(env)
        result = env["metrics_repo"].get_by_monitoring_ids([a, b])
        assert set(result.keys()) == {a, b}
        assert result[a].monitoring_id == a
        assert result[b].monitoring_id == b

    def test_empty_list_returns_empty_dict(self, session):
        env = _env(session)
        assert env["metrics_repo"].get_by_monitoring_ids([]) == {}

    def test_nonexistent_ids_absent(self, session):
        env = _env(session)
        a = _create_monitoring(env)
        result = env["metrics_repo"].get_by_monitoring_ids([a, 999999])
        assert set(result.keys()) == {a}
        assert 999999 not in result

    def test_monitoring_without_metrics_absent(self, session):
        env = _env(session)
        a = _create_monitoring(env, with_metrics=True)
        b = _create_monitoring(env, with_metrics=False)
        result = env["metrics_repo"].get_by_monitoring_ids([a, b])
        assert a in result
        assert b not in result

    def test_single_get_still_works(self, session):
        env = _env(session)
        a = _create_monitoring(env)
        assert env["metrics_repo"].get_by_monitoring(a).monitoring_id == a


class TestInspectionResultBulk:
    def test_grouping_by_monitoring(self, session):
        env = _env(session)
        a = _create_monitoring(env, n_results=3)
        b = _create_monitoring(env, n_results=1)
        result = env["insp_repo"].get_by_monitoring_ids([a, b])
        assert set(result.keys()) == {a, b}
        assert len(result[a]) == 3
        assert len(result[b]) == 1
        # each result belongs to its own monitoring's snapshot
        assert all(isinstance(r, DetectionInspectionResult) for r in result[a])

    def test_empty_list_returns_empty_dict(self, session):
        env = _env(session)
        assert env["insp_repo"].get_by_monitoring_ids([]) == {}

    def test_nonexistent_and_no_result_ids_absent(self, session):
        env = _env(session)
        a = _create_monitoring(env, n_results=2)
        b = _create_monitoring(env, n_results=0)  # no results
        result = env["insp_repo"].get_by_monitoring_ids([a, b, 999999])
        assert set(result.keys()) == {a}
        assert b not in result
        assert 999999 not in result

    def test_single_get_still_works(self, session):
        env = _env(session)
        a = _create_monitoring(env, n_results=2)
        assert len(env["insp_repo"].get_by_monitoring(a)) == 2
