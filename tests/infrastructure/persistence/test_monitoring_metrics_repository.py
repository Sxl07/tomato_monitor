"""Tests for SqlMonitoringMetricsRepository.

Focused on:
- create() accepts completed, aborted; rejects running, analyzing
- create_pending_for_finalization() accepts running, analyzing;
  rejects initializing, completed, aborted, error
- create_pending_for_finalization flushes but does not commit
- Unique constraint on monitoring_id honored
"""

import pytest
from sqlalchemy.exc import IntegrityError

from src.domain.entities.greenhouse import Greenhouse
from src.domain.entities.module import Module
from src.domain.entities.monitoring import Monitoring
from src.domain.entities.monitoring_metrics import MonitoringMetrics
from src.domain.exceptions import MetricsNotAllowedError
from src.infrastructure.persistence.repositories.sql_greenhouse_repository import (
    SqlGreenhouseRepository,
)
from src.infrastructure.persistence.repositories.sql_module_repository import (
    SqlModuleRepository,
)
from src.infrastructure.persistence.repositories.sql_monitoring_repository import (
    SqlMonitoringRepository,
)
from src.infrastructure.persistence.repositories.sql_monitoring_metrics_repository import (
    SqlMonitoringMetricsRepository,
)


def _make_metrics(monitoring_id: int) -> MonitoringMetrics:
    return MonitoringMetrics(
        monitoring_id=monitoring_id,
        total_tomatoes=10,
        healthy_count=7,
        unhealthy_count=3,
        pct_healthy=70.0,
        pct_unhealthy=30.0,
        pct_green=10.0,
        pct_breaker=10.0,
        pct_turning=20.0,
        pct_pink=20.0,
        pct_light_red=20.0,
        pct_red=20.0,
        snapshots_with_detections=5,
    )


def _setup_monitoring(db_session, target_status: str) -> int:
    """Create a greenhouse → module → monitoring and transition to target_status."""
    gh_repo = SqlGreenhouseRepository(db_session)
    mod_repo = SqlModuleRepository(db_session)
    mon_repo = SqlMonitoringRepository(db_session)

    gh = gh_repo.create(Greenhouse(name="GH Metrics"))
    module = mod_repo.create(gh.id, Module(greenhouse_id=gh.id, name="Mod Metrics"))
    monitoring = mon_repo.create(
        module.id, Monitoring(module_id=module.id, width_m=5.0, length_m=2.0)
    )

    # Transition through valid states to reach target
    transitions = {
        "initializing": [],
        "running": ["running"],
        "analyzing": ["running", "analyzing"],
        "completed": ["running", "analyzing", "completed"],
        "aborted": ["running", "aborted"],
        "error": ["running", "error"],
    }

    for status in transitions[target_status]:
        mon_repo.update_status(monitoring.id, status)

    db_session.flush()
    return monitoring.id


# ---------------------------------------------------------------------------
# Tests for create()
# ---------------------------------------------------------------------------


class TestMetricsRepoCreate:
    """SqlMonitoringMetricsRepository.create() status guards."""

    def test_create_accepts_completed(self, db_session):
        monitoring_id = _setup_monitoring(db_session, "completed")
        repo = SqlMonitoringMetricsRepository(db_session)

        metrics = repo.create(monitoring_id, _make_metrics(monitoring_id))
        assert metrics.id is not None
        assert metrics.total_tomatoes == 10

    def test_create_accepts_aborted(self, db_session):
        monitoring_id = _setup_monitoring(db_session, "aborted")
        repo = SqlMonitoringMetricsRepository(db_session)

        metrics = repo.create(monitoring_id, _make_metrics(monitoring_id))
        assert metrics.id is not None

    def test_create_rejects_running(self, db_session):
        monitoring_id = _setup_monitoring(db_session, "running")
        repo = SqlMonitoringMetricsRepository(db_session)

        with pytest.raises(MetricsNotAllowedError):
            repo.create(monitoring_id, _make_metrics(monitoring_id))

    def test_create_rejects_analyzing(self, db_session):
        monitoring_id = _setup_monitoring(db_session, "analyzing")
        repo = SqlMonitoringMetricsRepository(db_session)

        with pytest.raises(MetricsNotAllowedError):
            repo.create(monitoring_id, _make_metrics(monitoring_id))


# ---------------------------------------------------------------------------
# Tests for create_pending_for_finalization()
# ---------------------------------------------------------------------------


class TestMetricsRepoPending:
    """SqlMonitoringMetricsRepository.create_pending_for_finalization() guards."""

    def test_pending_accepts_running(self, db_session):
        monitoring_id = _setup_monitoring(db_session, "running")
        repo = SqlMonitoringMetricsRepository(db_session)

        metrics = repo.create_pending_for_finalization(
            monitoring_id, _make_metrics(monitoring_id)
        )
        assert metrics.id is not None

    def test_pending_accepts_analyzing(self, db_session):
        monitoring_id = _setup_monitoring(db_session, "analyzing")
        repo = SqlMonitoringMetricsRepository(db_session)

        metrics = repo.create_pending_for_finalization(
            monitoring_id, _make_metrics(monitoring_id)
        )
        assert metrics.id is not None

    def test_pending_rejects_initializing(self, db_session):
        monitoring_id = _setup_monitoring(db_session, "initializing")
        repo = SqlMonitoringMetricsRepository(db_session)

        with pytest.raises(MetricsNotAllowedError):
            repo.create_pending_for_finalization(
                monitoring_id, _make_metrics(monitoring_id)
            )

    def test_pending_rejects_completed(self, db_session):
        monitoring_id = _setup_monitoring(db_session, "completed")
        repo = SqlMonitoringMetricsRepository(db_session)

        with pytest.raises(MetricsNotAllowedError):
            repo.create_pending_for_finalization(
                monitoring_id, _make_metrics(monitoring_id)
            )

    def test_pending_rejects_aborted(self, db_session):
        monitoring_id = _setup_monitoring(db_session, "aborted")
        repo = SqlMonitoringMetricsRepository(db_session)

        with pytest.raises(MetricsNotAllowedError):
            repo.create_pending_for_finalization(
                monitoring_id, _make_metrics(monitoring_id)
            )

    def test_pending_rejects_error(self, db_session):
        monitoring_id = _setup_monitoring(db_session, "error")
        repo = SqlMonitoringMetricsRepository(db_session)

        with pytest.raises(MetricsNotAllowedError):
            repo.create_pending_for_finalization(
                monitoring_id, _make_metrics(monitoring_id)
            )

    def test_pending_flushes_but_does_not_commit(self, db_session):
        """After create_pending, the model is flushed but commit not called."""
        monitoring_id = _setup_monitoring(db_session, "running")
        repo = SqlMonitoringMetricsRepository(db_session)

        # Spy on commit to verify it's not called by create_pending
        commit_calls = []
        original_commit = db_session.commit

        def spy_commit():
            commit_calls.append(True)
            original_commit()

        db_session.commit = spy_commit

        metrics = repo.create_pending_for_finalization(
            monitoring_id, _make_metrics(monitoring_id)
        )

        # commit was NOT called by create_pending_for_finalization
        assert len(commit_calls) == 0

        # But the record IS visible within the session (was flushed)
        found = repo.get_by_monitoring(monitoring_id)
        assert found is not None
        assert found.total_tomatoes == 10

        # Restore original
        db_session.commit = original_commit

    def test_pending_unique_constraint(self, db_session):
        """Second create_pending for same monitoring_id raises IntegrityError."""
        monitoring_id = _setup_monitoring(db_session, "running")
        repo = SqlMonitoringMetricsRepository(db_session)

        repo.create_pending_for_finalization(
            monitoring_id, _make_metrics(monitoring_id)
        )

        with pytest.raises(IntegrityError):
            repo.create_pending_for_finalization(
                monitoring_id, _make_metrics(monitoring_id)
            )
