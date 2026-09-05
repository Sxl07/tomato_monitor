"""Persistence round-trip tests for the READY_FOR_ANALYSIS status (Spec 020, Task 2.2).

Uses REAL temporary SQLite databases via DatabaseManager. Verifies:
- ready_for_analysis persists and reads back identically.
- The value survives a simulated restart (new engine/session over the same file).
- get_active() includes monitorings in ready_for_analysis.
- video_path is preserved alongside the state.
"""

from __future__ import annotations

import pytest

from src.domain.entities.monitoring import Monitoring
from src.domain.value_objects.monitoring_status import MonitoringState
from src.infrastructure.persistence.database import DatabaseManager
from src.infrastructure.persistence.repositories import SqlMonitoringRepository

REL_VIDEO = "outputs/monitorings/1/video/monitoring.mp4"


def _seed_module(manager) -> int:
    from src.infrastructure.persistence.models.greenhouse_model import GreenhouseModel
    from src.infrastructure.persistence.models.module_model import ModuleModel

    session = manager.get_session()
    try:
        gh = GreenhouseModel(name="GH RFA", location="Test")
        session.add(gh)
        session.flush()
        module = ModuleModel(greenhouse_id=gh.id, name="Mod RFA")
        session.add(module)
        session.flush()
        session.commit()
        return module.id
    finally:
        session.close()


def _seed_running_monitoring(manager, module_id, video_path=None) -> int:
    """Create a monitoring and move it to running (so it can go to ready_for_analysis)."""
    session = manager.get_session()
    try:
        repo = SqlMonitoringRepository(session)
        created = repo.create(
            module_id,
            Monitoring(module_id=module_id, width_m=5.0, length_m=2.0, video_path=video_path),
        )
        repo.update_status(created.id, MonitoringState.RUNNING.value)
        return created.id
    finally:
        session.close()


class TestReadyForAnalysisPersistence:
    def test_persist_and_read_back_identical(self, tmp_path):
        manager = DatabaseManager(db_path=str(tmp_path / "rfa.db"))
        manager.init_db()
        module_id = _seed_module(manager)
        mid = _seed_running_monitoring(manager, module_id, video_path=REL_VIDEO)

        session = manager.get_session()
        try:
            repo = SqlMonitoringRepository(session)
            repo.update_video_path(mid, REL_VIDEO)
            repo.update_status(mid, MonitoringState.READY_FOR_ANALYSIS.value)
            fetched = repo.get_by_id(mid)
            assert fetched.status == MonitoringState.READY_FOR_ANALYSIS.value
            assert fetched.video_path == REL_VIDEO
        finally:
            session.close()

    def test_survives_restart(self, tmp_path):
        db = str(tmp_path / "rfa_restart.db")
        manager = DatabaseManager(db_path=db)
        manager.init_db()
        module_id = _seed_module(manager)
        mid = _seed_running_monitoring(manager, module_id, video_path=REL_VIDEO)

        s1 = manager.get_session()
        try:
            repo = SqlMonitoringRepository(s1)
            repo.update_video_path(mid, REL_VIDEO)
            repo.update_status(mid, MonitoringState.READY_FOR_ANALYSIS.value)
        finally:
            s1.close()

        # Simulate restart: brand-new manager/engine/session over the same file.
        manager2 = DatabaseManager(db_path=db)
        s2 = manager2.get_session()
        try:
            repo2 = SqlMonitoringRepository(s2)
            fetched = repo2.get_by_id(mid)
            assert fetched.status == MonitoringState.READY_FOR_ANALYSIS.value
            assert fetched.video_path == REL_VIDEO
        finally:
            s2.close()

    def test_get_active_includes_ready_for_analysis(self, tmp_path):
        manager = DatabaseManager(db_path=str(tmp_path / "rfa_active.db"))
        manager.init_db()
        module_id = _seed_module(manager)
        mid = _seed_running_monitoring(manager, module_id, video_path=REL_VIDEO)

        session = manager.get_session()
        try:
            repo = SqlMonitoringRepository(session)
            repo.update_status(mid, MonitoringState.READY_FOR_ANALYSIS.value)
            active = repo.get_active()
            assert any(
                m.id == mid and m.status == MonitoringState.READY_FOR_ANALYSIS.value
                for m in active
            )
        finally:
            session.close()
