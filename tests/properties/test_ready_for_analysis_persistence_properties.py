"""Property-based test: ready_for_analysis persistence round-trip (Spec 020).

Testing framework: pytest + hypothesis
Minimum examples: 100 per property

# Feature: 020-deferred-manual-analysis-workflow, Property 3
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from src.domain.entities.monitoring import Monitoring
from src.domain.value_objects.monitoring_status import MonitoringState
from src.infrastructure.persistence.database import DatabaseManager
from src.infrastructure.persistence.repositories import SqlMonitoringRepository


def _seed_module(manager) -> int:
    from src.infrastructure.persistence.models.greenhouse_model import GreenhouseModel
    from src.infrastructure.persistence.models.module_model import ModuleModel

    session = manager.get_session()
    try:
        gh = GreenhouseModel(name="GH Prop3", location="Test")
        session.add(gh)
        session.flush()
        module = ModuleModel(greenhouse_id=gh.id, name="Mod Prop3")
        session.add(module)
        session.flush()
        session.commit()
        return module.id
    finally:
        session.close()


@settings(max_examples=100, deadline=None)
@given(rel_path=st.text(alphabet="abcdefghijklmnop/_.0123456789", min_size=1, max_size=40))
def test_property_3_ready_for_analysis_round_trip(tmp_path_factory, rel_path):
    """Property 3: a monitoring persisted as ready_for_analysis reads back exactly
    ready_for_analysis (including after a simulated restart), without recompute.

    # Feature: 020-deferred-manual-analysis-workflow, Property 3
    """
    # Keep the path relative (no leading slash) so it is stored as-is.
    rel_path = "outputs/monitorings/" + rel_path.lstrip("/")

    db_dir = tmp_path_factory.mktemp("prop3")
    db = str(db_dir / "p3.db")
    manager = DatabaseManager(db_path=db)
    manager.init_db()
    module_id = _seed_module(manager)

    s1 = manager.get_session()
    try:
        repo = SqlMonitoringRepository(s1)
        created = repo.create(
            module_id,
            Monitoring(module_id=module_id, width_m=5.0, length_m=2.0),
        )
        repo.update_status(created.id, MonitoringState.RUNNING.value)
        repo.update_video_path(created.id, rel_path)
        repo.update_status(created.id, MonitoringState.READY_FOR_ANALYSIS.value)
        mid = created.id
    finally:
        s1.close()

    # Simulate restart with a fresh engine/session over the same file.
    manager2 = DatabaseManager(db_path=db)
    s2 = manager2.get_session()
    try:
        repo2 = SqlMonitoringRepository(s2)
        fetched = repo2.get_by_id(mid)
        assert fetched.status == MonitoringState.READY_FOR_ANALYSIS.value
        assert fetched.video_path == rel_path
    finally:
        s2.close()
