"""Repository CRUD tests using in-memory SQLite.

Tests cover Greenhouse, Module, and Monitoring repository operations:
create, read, update, delete (with cascade), and error cases.
"""

import pytest

from src.domain.entities.greenhouse import Greenhouse
from src.domain.entities.module import Module
from src.domain.entities.monitoring import Monitoring
from src.domain.exceptions import DuplicateModuleError, InvalidTransitionError
from src.infrastructure.persistence.repositories.sql_greenhouse_repository import (
    SqlGreenhouseRepository,
)
from src.infrastructure.persistence.repositories.sql_module_repository import (
    SqlModuleRepository,
)
from src.infrastructure.persistence.repositories.sql_monitoring_repository import (
    SqlMonitoringRepository,
)


# --- Greenhouse CRUD ---


class TestGreenhouseRepository:
    """Tests for SqlGreenhouseRepository CRUD operations."""

    def test_create_greenhouse(self, db_session):
        repo = SqlGreenhouseRepository(db_session)
        gh = repo.create(Greenhouse(name="Invernadero 1", location="Zona Norte"))

        assert gh.id is not None
        assert gh.name == "Invernadero 1"
        assert gh.location == "Zona Norte"
        assert gh.created_at is not None

    def test_get_by_id(self, db_session):
        repo = SqlGreenhouseRepository(db_session)
        created = repo.create(Greenhouse(name="Invernadero Get"))

        found = repo.get_by_id(created.id)
        assert found is not None
        assert found.name == "Invernadero Get"

    def test_get_by_id_nonexistent_returns_none(self, db_session):
        repo = SqlGreenhouseRepository(db_session)
        assert repo.get_by_id(99999) is None

    def test_get_all(self, db_session):
        repo = SqlGreenhouseRepository(db_session)
        repo.create(Greenhouse(name="GH All 1"))
        repo.create(Greenhouse(name="GH All 2"))

        all_gh = repo.get_all()
        names = [g.name for g in all_gh]
        assert "GH All 1" in names
        assert "GH All 2" in names

    def test_update_greenhouse(self, db_session):
        repo = SqlGreenhouseRepository(db_session)
        created = repo.create(Greenhouse(name="Original Name"))

        updated = repo.update(created.id, name="Updated Name", location="New Loc")
        assert updated.name == "Updated Name"
        assert updated.location == "New Loc"

    def test_delete_greenhouse(self, db_session):
        repo = SqlGreenhouseRepository(db_session)
        created = repo.create(Greenhouse(name="To Delete"))

        repo.delete(created.id)
        assert repo.get_by_id(created.id) is None


# --- Module CRUD ---


class TestModuleRepository:
    """Tests for SqlModuleRepository CRUD operations."""

    def test_create_module(self, db_session):
        gh_repo = SqlGreenhouseRepository(db_session)
        mod_repo = SqlModuleRepository(db_session)

        gh = gh_repo.create(Greenhouse(name="GH for Module"))
        module = mod_repo.create(
            gh.id, Module(greenhouse_id=gh.id, name="Módulo 1", width_m=5.0, length_m=2.0)
        )

        assert module.id is not None
        assert module.name == "Módulo 1"
        assert module.greenhouse_id == gh.id
        assert module.width_m == 5.0

    def test_get_by_greenhouse(self, db_session):
        gh_repo = SqlGreenhouseRepository(db_session)
        mod_repo = SqlModuleRepository(db_session)

        gh = gh_repo.create(Greenhouse(name="GH Modules List"))
        mod_repo.create(gh.id, Module(greenhouse_id=gh.id, name="Mod A"))
        mod_repo.create(gh.id, Module(greenhouse_id=gh.id, name="Mod B"))

        modules = mod_repo.get_by_greenhouse(gh.id)
        assert len(modules) == 2

    def test_duplicate_name_raises_error(self, db_session):
        gh_repo = SqlGreenhouseRepository(db_session)
        mod_repo = SqlModuleRepository(db_session)

        gh = gh_repo.create(Greenhouse(name="GH Duplicate"))
        mod_repo.create(gh.id, Module(greenhouse_id=gh.id, name="Same Name"))

        with pytest.raises(DuplicateModuleError):
            mod_repo.create(gh.id, Module(greenhouse_id=gh.id, name="Same Name"))

    def test_delete_module_cascades(self, db_session):
        gh_repo = SqlGreenhouseRepository(db_session)
        mod_repo = SqlModuleRepository(db_session)
        mon_repo = SqlMonitoringRepository(db_session)

        gh = gh_repo.create(Greenhouse(name="GH Cascade"))
        module = mod_repo.create(gh.id, Module(greenhouse_id=gh.id, name="Cascade Mod"))
        monitoring = mon_repo.create(
            module.id, Monitoring(module_id=module.id, width_m=3.0, length_m=2.0)
        )

        mod_repo.delete(module.id)
        assert mod_repo.get_by_id(module.id) is None
        assert mon_repo.get_by_id(monitoring.id) is None


# --- Monitoring CRUD ---


class TestMonitoringRepository:
    """Tests for SqlMonitoringRepository CRUD operations."""

    def test_create_monitoring(self, db_session):
        gh_repo = SqlGreenhouseRepository(db_session)
        mod_repo = SqlModuleRepository(db_session)
        mon_repo = SqlMonitoringRepository(db_session)

        gh = gh_repo.create(Greenhouse(name="GH Mon"))
        module = mod_repo.create(gh.id, Module(greenhouse_id=gh.id, name="Mon Mod"))
        monitoring = mon_repo.create(
            module.id,
            Monitoring(module_id=module.id, width_m=4.0, length_m=3.0, notes="Test"),
        )

        assert monitoring.id is not None
        assert monitoring.status == "initializing"
        assert monitoring.width_m == 4.0
        assert monitoring.notes == "Test"

    def test_get_by_module(self, db_session):
        gh_repo = SqlGreenhouseRepository(db_session)
        mod_repo = SqlModuleRepository(db_session)
        mon_repo = SqlMonitoringRepository(db_session)

        gh = gh_repo.create(Greenhouse(name="GH Mon List"))
        module = mod_repo.create(gh.id, Module(greenhouse_id=gh.id, name="Mon List Mod"))
        mon_repo.create(module.id, Monitoring(module_id=module.id, width_m=1.0, length_m=1.0))
        mon_repo.create(module.id, Monitoring(module_id=module.id, width_m=2.0, length_m=2.0))

        monitorings = mon_repo.get_by_module(module.id)
        assert len(monitorings) == 2

    def test_update_status_valid_transition(self, db_session):
        gh_repo = SqlGreenhouseRepository(db_session)
        mod_repo = SqlModuleRepository(db_session)
        mon_repo = SqlMonitoringRepository(db_session)

        gh = gh_repo.create(Greenhouse(name="GH Status"))
        module = mod_repo.create(gh.id, Module(greenhouse_id=gh.id, name="Status Mod"))
        monitoring = mon_repo.create(
            module.id, Monitoring(module_id=module.id, width_m=1.0, length_m=1.0)
        )

        # initializing → running
        updated = mon_repo.update_status(monitoring.id, "running")
        assert updated.status == "running"

    def test_update_status_invalid_transition_raises(self, db_session):
        gh_repo = SqlGreenhouseRepository(db_session)
        mod_repo = SqlModuleRepository(db_session)
        mon_repo = SqlMonitoringRepository(db_session)

        gh = gh_repo.create(Greenhouse(name="GH Invalid"))
        module = mod_repo.create(gh.id, Module(greenhouse_id=gh.id, name="Invalid Mod"))
        monitoring = mon_repo.create(
            module.id, Monitoring(module_id=module.id, width_m=1.0, length_m=1.0)
        )

        # initializing → completed is not valid
        with pytest.raises(InvalidTransitionError):
            mon_repo.update_status(monitoring.id, "completed")

    def test_delete_greenhouse_cascades_to_monitoring(self, db_session):
        gh_repo = SqlGreenhouseRepository(db_session)
        mod_repo = SqlModuleRepository(db_session)
        mon_repo = SqlMonitoringRepository(db_session)

        gh = gh_repo.create(Greenhouse(name="GH Full Cascade"))
        module = mod_repo.create(gh.id, Module(greenhouse_id=gh.id, name="Full Cascade Mod"))
        monitoring = mon_repo.create(
            module.id, Monitoring(module_id=module.id, width_m=1.0, length_m=1.0)
        )

        gh_repo.delete(gh.id)
        assert gh_repo.get_by_id(gh.id) is None
        assert mod_repo.get_by_id(module.id) is None
        assert mon_repo.get_by_id(monitoring.id) is None
