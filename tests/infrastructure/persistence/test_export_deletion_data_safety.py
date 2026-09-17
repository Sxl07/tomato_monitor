"""Data-safety test for export package deletion (Spec 025).

Deleting an ExportPackage record must never affect source data: monitorings,
snapshots, inspection results, metrics, or activity logs. This test seeds a
full hierarchy plus an export package, deletes only the export package via the
repository, and asserts the source rows still exist.

No sync, outbox, or Supabase interaction is involved: the repository ``delete``
touches only the ExportPackage table.
"""

from src.domain.entities.export_package import ExportPackage
from src.infrastructure.persistence.models import (
    ActivityLogModel,
    ActivityTypeModel,
    GreenhouseModel,
    InspectionResultModel,
    ModuleModel,
    MonitoringMetricsModel,
    MonitoringModel,
    SnapshotModel,
    UserModel,
)
from src.infrastructure.persistence.repositories.sql_export_package_repository import (
    SqlExportPackageRepository,
)


def _seed_sources(session):
    """Seed a full hierarchy of source entities; return an id dict."""
    user = UserModel(
        full_name="Data Safety Op",
        email="datasafety@test.com",
        password_hash="hash",
        role="operator",
    )
    session.add(user)
    session.flush()

    # The activity type catalog is seeded during init_db(); reuse an existing
    # entry instead of inserting a duplicate (unique 'code' constraint).
    activity_type = session.query(ActivityTypeModel).first()
    if activity_type is None:
        activity_type = ActivityTypeModel(
            code="riego", name="Riego", category="mantenimiento",
        )
        session.add(activity_type)
        session.flush()

    gh = GreenhouseModel(name="GH-DataSafety")
    session.add(gh)
    session.flush()

    mod = ModuleModel(greenhouse_id=gh.id, name="Mod-DataSafety")
    session.add(mod)
    session.flush()

    mon = MonitoringModel(module_id=mod.id, status="completed", width_m=5.0, length_m=2.0)
    session.add(mon)
    session.flush()

    snap = SnapshotModel(
        monitoring_id=mon.id,
        image_path="monitorings/x/snapshots/raw/snapshot_0.jpg",
        frame_index=0,
    )
    session.add(snap)
    session.flush()

    metrics = MonitoringMetricsModel(
        monitoring_id=mon.id,
        total_tomatoes=10, healthy_count=8, unhealthy_count=2,
        pct_healthy=80.0, pct_unhealthy=20.0, snapshots_with_detections=1,
    )
    session.add(metrics)
    session.flush()

    ir = InspectionResultModel(
        snapshot_id=snap.id, detection_index=0,
        bbox_x1=10, bbox_y1=20, bbox_x2=50, bbox_y2=60,
        detection_score=0.95, health_label="healthy", health_confidence=0.9,
    )
    session.add(ir)
    session.flush()

    al = ActivityLogModel(
        module_id=mod.id, activity_type_id=activity_type.id,
        user_id=user.id, notes="Test activity",
    )
    session.add(al)
    session.flush()
    session.commit()

    return {
        "user": user.id,
        "monitoring": mon.id,
        "snapshot": snap.id,
        "metrics": metrics.id,
        "inspection_result": ir.id,
        "activity_log": al.id,
    }


def test_deleting_export_package_preserves_source_data(db_session):
    ids = _seed_sources(db_session)
    repo = SqlExportPackageRepository(db_session)

    created = repo.create(
        ExportPackage(created_by_user_id=ids["user"], scope="full", status="completed")
    )

    # Sanity: export package exists before deletion.
    assert repo.get_by_id(created.id) is not None

    repo.delete(created.id)

    # Export package is gone.
    assert repo.get_by_id(created.id) is None

    # All source entities remain intact.
    assert db_session.query(MonitoringModel).filter_by(id=ids["monitoring"]).first() is not None
    assert db_session.query(SnapshotModel).filter_by(id=ids["snapshot"]).first() is not None
    assert db_session.query(InspectionResultModel).filter_by(id=ids["inspection_result"]).first() is not None
    assert db_session.query(MonitoringMetricsModel).filter_by(id=ids["metrics"]).first() is not None
    assert db_session.query(ActivityLogModel).filter_by(id=ids["activity_log"]).first() is not None
    assert db_session.query(UserModel).filter_by(id=ids["user"]).first() is not None
