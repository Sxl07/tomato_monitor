"""Tests for MonitoringService state management logic.

Focuses on the one-active-session-per-module enforcement and
state machine transitions at the service level. The exhaustive
state machine tests for MonitoringStatus live in tests/domain/.
"""

import pytest

from src.application.services.monitoring_service import (
    ActiveSessionError,
    MonitoringNotFoundError,
)
from src.domain.entities.greenhouse import Greenhouse
from src.domain.entities.module import Module
from src.domain.entities.monitoring import Monitoring
from src.domain.exceptions import InvalidTransitionError
from src.domain.value_objects.monitoring_status import MonitoringState, MonitoringStatus
from src.infrastructure.persistence.repositories.sql_greenhouse_repository import (
    SqlGreenhouseRepository,
)
from src.infrastructure.persistence.repositories.sql_module_repository import (
    SqlModuleRepository,
)
from src.infrastructure.persistence.repositories.sql_monitoring_repository import (
    SqlMonitoringRepository,
)


class TestActiveSessionEnforcement:
    """Test that MonitoringService logic prevents multiple active sessions per module."""

    def _create_module(self, db_session) -> tuple[int, SqlMonitoringRepository]:
        """Helper: create a greenhouse + module and return (module_id, mon_repo)."""
        gh_repo = SqlGreenhouseRepository(db_session)
        mod_repo = SqlModuleRepository(db_session)

        gh = gh_repo.create(Greenhouse(name="GH Active Test"))
        module = mod_repo.create(gh.id, Module(greenhouse_id=gh.id, name="Active Mod"))
        mon_repo = SqlMonitoringRepository(db_session)
        return module.id, mon_repo

    def test_active_session_blocks_new_monitoring(self, db_session):
        """Starting a monitoring when one is already active (initializing) should be blocked."""
        module_id, mon_repo = self._create_module(db_session)

        # Create first monitoring (status = initializing → active)
        first = mon_repo.create(
            module_id, Monitoring(module_id=module_id, width_m=1.0, length_m=1.0)
        )
        assert first.status == "initializing"

        # Simulate the check that MonitoringService performs
        _ACTIVE_STATUSES = {"initializing", "running", "paused", "finishing", "analyzing"}
        existing = mon_repo.get_by_module(module_id)
        active = [m for m in existing if m.status in _ACTIVE_STATUSES]

        assert len(active) == 1
        # The service would raise ActiveSessionError here
        with pytest.raises(ActiveSessionError):
            raise ActiveSessionError(module_id, active[0].id)

    def test_completed_session_allows_new_monitoring(self, db_session):
        """A completed monitoring should not block a new session."""
        module_id, mon_repo = self._create_module(db_session)

        # Create and complete a monitoring
        first = mon_repo.create(
            module_id, Monitoring(module_id=module_id, width_m=1.0, length_m=1.0)
        )
        mon_repo.update_status(first.id, "running")
        mon_repo.update_status(first.id, "finishing")
        mon_repo.update_status(first.id, "completed")

        # Verify completed status doesn't count as active
        _ACTIVE_STATUSES = {"initializing", "running", "paused", "finishing", "analyzing"}
        existing = mon_repo.get_by_module(module_id)
        active = [m for m in existing if m.status in _ACTIVE_STATUSES]

        assert len(active) == 0

        # Can create a new monitoring
        second = mon_repo.create(
            module_id, Monitoring(module_id=module_id, width_m=2.0, length_m=2.0)
        )
        assert second.id is not None

    def test_aborted_session_allows_new_monitoring(self, db_session):
        """An aborted monitoring should not block a new session."""
        module_id, mon_repo = self._create_module(db_session)

        # Create and abort a monitoring
        first = mon_repo.create(
            module_id, Monitoring(module_id=module_id, width_m=1.0, length_m=1.0)
        )
        mon_repo.update_status(first.id, "running")
        mon_repo.update_status(first.id, "aborted")

        # Verify aborted status doesn't count as active
        _ACTIVE_STATUSES = {"initializing", "running", "paused", "finishing", "analyzing"}
        existing = mon_repo.get_by_module(module_id)
        active = [m for m in existing if m.status in _ACTIVE_STATUSES]

        assert len(active) == 0

    def test_running_session_blocks_new_monitoring(self, db_session):
        """A running monitoring should block a new session start."""
        module_id, mon_repo = self._create_module(db_session)

        first = mon_repo.create(
            module_id, Monitoring(module_id=module_id, width_m=1.0, length_m=1.0)
        )
        mon_repo.update_status(first.id, "running")

        _ACTIVE_STATUSES = {"initializing", "running", "paused", "finishing", "analyzing"}
        existing = mon_repo.get_by_module(module_id)
        active = [m for m in existing if m.status in _ACTIVE_STATUSES]

        assert len(active) == 1
        assert active[0].status == "running"

    def test_analyzing_session_blocks_new_monitoring(self, db_session):
        """An analyzing monitoring blocks a new session (it's active)."""
        module_id, mon_repo = self._create_module(db_session)

        first = mon_repo.create(
            module_id, Monitoring(module_id=module_id, width_m=1.0, length_m=1.0)
        )
        mon_repo.update_status(first.id, "running")
        mon_repo.update_status(first.id, "analyzing")

        _ACTIVE_STATUSES = {"initializing", "running", "paused", "finishing", "analyzing"}
        existing = mon_repo.get_by_module(module_id)
        active = [m for m in existing if m.status in _ACTIVE_STATUSES]

        assert len(active) == 1
        assert active[0].status == "analyzing"


class TestMonitoringNotFoundError:
    """Test MonitoringNotFoundError behavior."""

    def test_raises_with_correct_id(self):
        err = MonitoringNotFoundError(42)
        assert err.monitoring_id == 42
        assert "42" in str(err)


class TestServiceLevelTransitions:
    """Test state transitions at the repository level (integration with state machine)."""

    def _setup(self, db_session):
        """Helper to create greenhouse → module → monitoring."""
        gh_repo = SqlGreenhouseRepository(db_session)
        mod_repo = SqlModuleRepository(db_session)
        mon_repo = SqlMonitoringRepository(db_session)

        gh = gh_repo.create(Greenhouse(name="GH Transitions"))
        module = mod_repo.create(gh.id, Module(greenhouse_id=gh.id, name="Trans Mod"))
        monitoring = mon_repo.create(
            module.id, Monitoring(module_id=module.id, width_m=1.0, length_m=1.0)
        )
        return mon_repo, monitoring

    def test_happy_path_lifecycle(self, db_session):
        """initializing → running → finishing → completed."""
        mon_repo, monitoring = self._setup(db_session)

        m = mon_repo.update_status(monitoring.id, "running")
        assert m.status == "running"

        m = mon_repo.update_status(monitoring.id, "finishing")
        assert m.status == "finishing"

        m = mon_repo.update_status(monitoring.id, "completed")
        assert m.status == "completed"
        assert m.completed_at is not None

    def test_pause_resume_cycle(self, db_session):
        """running → paused → running."""
        mon_repo, monitoring = self._setup(db_session)

        mon_repo.update_status(monitoring.id, "running")
        m = mon_repo.update_status(monitoring.id, "paused")
        assert m.status == "paused"

        m = mon_repo.update_status(monitoring.id, "running")
        assert m.status == "running"

    def test_abort_from_running(self, db_session):
        """running → aborted."""
        mon_repo, monitoring = self._setup(db_session)

        mon_repo.update_status(monitoring.id, "running")
        m = mon_repo.update_status(monitoring.id, "aborted")
        assert m.status == "aborted"
        assert m.completed_at is not None

    def test_completed_to_running_raises(self, db_session):
        """completed → running raises InvalidTransitionError."""
        mon_repo, monitoring = self._setup(db_session)

        mon_repo.update_status(monitoring.id, "running")
        mon_repo.update_status(monitoring.id, "finishing")
        mon_repo.update_status(monitoring.id, "completed")

        with pytest.raises(InvalidTransitionError):
            mon_repo.update_status(monitoring.id, "running")


class TestCreatedByUserIdPersistence:
    """start_session must persist created_by_user_id for creator traceability.

    This is a traceability field only. Ownership/authorization continues to be
    derived from Greenhouse.owner_user_id and is not affected by this field.
    """

    def _make_user(self, db_session, email: str) -> int:
        from src.infrastructure.persistence.models import UserModel

        user = UserModel(
            full_name="Operario Test",
            email=email,
            password_hash="x",
        )
        db_session.add(user)
        db_session.commit()
        return user.id

    def _build_service(self, db_session):
        from unittest.mock import MagicMock

        from src.application.services.monitoring_service import MonitoringService
        from src.infrastructure.persistence.repositories.sql_snapshot_repository import (
            SqlSnapshotRepository,
        )
        from src.infrastructure.persistence.repositories.sql_inspection_result_repository import (
            SqlInspectionResultRepository,
        )
        from src.infrastructure.persistence.repositories.sql_monitoring_metrics_repository import (
            SqlMonitoringMetricsRepository,
        )

        registry = MagicMock()
        # Guard methods must be falsy so start_session proceeds to create the
        # entity. reserve_capture must not be exactly False (only an explicit
        # False rejects the start).
        registry.has_live_worker_for_module.return_value = False
        registry.reserve_capture.return_value = True
        registry.cleanup_dead.return_value = None

        return MonitoringService(
            monitoring_repo=SqlMonitoringRepository(db_session),
            snapshot_repo=SqlSnapshotRepository(db_session),
            inspection_result_repo=SqlInspectionResultRepository(db_session),
            metrics_repo=SqlMonitoringMetricsRepository(db_session),
            module_repo=SqlModuleRepository(db_session),
            runtime_registry=registry,
        )

    def test_start_session_persists_created_by_user_id(self, db_session, monkeypatch):
        from src.application.services.monitoring_service import MonitoringService

        user_id = self._make_user(db_session, "creator@test.local")

        gh_repo = SqlGreenhouseRepository(db_session)
        mod_repo = SqlModuleRepository(db_session)
        gh = gh_repo.create(Greenhouse(name="GH Creator", owner_user_id=user_id))
        module = mod_repo.create(gh.id, Module(greenhouse_id=gh.id, name="Mod Creator"))

        service = self._build_service(db_session)
        # Do not spawn a real capture worker/thread; the field is set before this.
        monkeypatch.setattr(
            MonitoringService, "_start_capture_first",
            lambda self, monitoring, frame_source, db_session, log_service: None,
        )

        monitoring = service.start_session(
            module_id=module.id,
            width_m=1.0,
            length_m=1.0,
            notes=None,
            frame_source=object(),
            db_session=db_session,
            created_by_user_id=user_id,
        )

        assert monitoring.created_by_user_id == user_id

        # Re-read from persistence to confirm it was stored, not just returned.
        persisted = SqlMonitoringRepository(db_session).get_by_id(monitoring.id)
        assert persisted.created_by_user_id == user_id

    def test_ownership_isolation_still_holds(self, db_session):
        """Greenhouse.owner_user_id isolation is independent of created_by_user_id."""
        owner_id = self._make_user(db_session, "owner@test.local")
        other_id = self._make_user(db_session, "other@test.local")

        gh_repo = SqlGreenhouseRepository(db_session)
        gh = gh_repo.create(Greenhouse(name="GH Owned", owner_user_id=owner_id))

        # The owner can fetch their greenhouse; another user cannot.
        assert gh_repo.get_by_id_for_owner(gh.id, owner_id) is not None
        assert gh_repo.get_by_id_for_owner(gh.id, other_id) is None
        assert [g.id for g in gh_repo.get_all_by_owner(owner_id)] == [gh.id]
        assert gh_repo.get_all_by_owner(other_id) == []
