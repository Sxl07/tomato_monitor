"""Tests for Spec 015 SQL repository implementations.

Tests User, ActivityType, ActivityLog, and ExportPackage repositories,
plus the activity type seed and schema migration logic.
"""

import pytest
from datetime import datetime, timezone

from src.domain.entities.user import User
from src.domain.entities.activity_type import ActivityType
from src.domain.entities.activity_log import ActivityLog
from src.domain.entities.export_package import ExportPackage
from src.infrastructure.persistence.repositories.sql_user_repository import (
    SqlUserRepository,
)
from src.infrastructure.persistence.repositories.sql_activity_type_repository import (
    SqlActivityTypeRepository,
)
from src.infrastructure.persistence.repositories.sql_activity_log_repository import (
    SqlActivityLogRepository,
)
from src.infrastructure.persistence.repositories.sql_export_package_repository import (
    SqlExportPackageRepository,
)
from src.infrastructure.persistence.models.user_model import UserModel
from src.infrastructure.persistence.models.activity_type_model import ActivityTypeModel
from src.infrastructure.persistence.models.greenhouse_model import GreenhouseModel
from src.infrastructure.persistence.models.module_model import ModuleModel


class TestSqlUserRepository:
    """Tests for SqlUserRepository CRUD operations."""

    def test_create_and_get_by_id(self, db_session):
        repo = SqlUserRepository(db_session)
        user = User(
            full_name="Juan Pérez",
            email="juan@test.com",
            password_hash="hashed_pw",
        )
        created = repo.create(user)
        assert created.id is not None
        assert created.full_name == "Juan Pérez"
        assert created.email == "juan@test.com"
        assert created.role == "operator"
        assert created.is_active is True
        assert created.created_at is not None

        fetched = repo.get_by_id(created.id)
        assert fetched is not None
        assert fetched.email == "juan@test.com"

    def test_get_by_email(self, db_session):
        repo = SqlUserRepository(db_session)
        user = User(
            full_name="Maria",
            email="maria@test.com",
            password_hash="hash",
        )
        repo.create(user)

        found = repo.get_by_email("maria@test.com")
        assert found is not None
        assert found.full_name == "Maria"

        not_found = repo.get_by_email("nonexistent@test.com")
        assert not_found is None

    def test_update_user(self, db_session):
        repo = SqlUserRepository(db_session)
        user = User(
            full_name="Original",
            email="original@test.com",
            password_hash="hash",
        )
        created = repo.create(user)

        updated = repo.update(created.id, {"full_name": "Updated Name", "role": "admin"})
        assert updated.full_name == "Updated Name"
        assert updated.role == "admin"

    def test_list_active(self, db_session):
        repo = SqlUserRepository(db_session)
        repo.create(User(full_name="Active", email="active@test.com", password_hash="h"))
        repo.create(User(full_name="Inactive", email="inactive@test.com", password_hash="h", is_active=False))

        active = repo.list_active()
        assert len(active) == 1
        assert active[0].full_name == "Active"

    def test_get_by_id_not_found(self, db_session):
        repo = SqlUserRepository(db_session)
        assert repo.get_by_id(9999) is None


class TestSqlActivityTypeRepository:
    """Tests for SqlActivityTypeRepository CRUD operations."""

    def test_create_and_get_by_code(self, db_session):
        repo = SqlActivityTypeRepository(db_session)
        # Use a unique code not in the seed catalog
        at = ActivityType(code="custom_test", name="Custom Test", category="testing")
        created = repo.create(at)
        assert created.id is not None
        assert created.code == "custom_test"

        fetched = repo.get_by_code("custom_test")
        assert fetched is not None
        assert fetched.name == "Custom Test"

    def test_get_by_code_seeded(self, db_session):
        """Seeded activity types should be retrievable by code."""
        repo = SqlActivityTypeRepository(db_session)
        fetched = repo.get_by_code("riego")
        assert fetched is not None
        assert fetched.name == "Riego"
        assert fetched.category == "mantenimiento"

    def test_list_active(self, db_session):
        repo = SqlActivityTypeRepository(db_session)
        # All 12 seeded types are active, add one inactive
        repo.create(ActivityType(code="inactive_test", name="Inactive", category="cat", is_active=False))

        active = repo.list_active()
        # 12 seeded + 0 extra active (the new one is inactive)
        assert len(active) == 12

    def test_list_all(self, db_session):
        repo = SqlActivityTypeRepository(db_session)
        repo.create(ActivityType(code="all_test", name="All Test", category="cat", is_active=False))

        all_types = repo.list_all()
        # 12 seeded + 1 new inactive
        assert len(all_types) == 13

    def test_get_by_id(self, db_session):
        repo = SqlActivityTypeRepository(db_session)
        created = repo.create(ActivityType(code="id_test", name="ID Test", category="cat"))
        fetched = repo.get_by_id(created.id)
        assert fetched is not None
        assert fetched.code == "id_test"


class TestSqlActivityLogRepository:
    """Tests for SqlActivityLogRepository CRUD operations."""

    def _create_prerequisites(self, session):
        """Create user, greenhouse, module, and activity type for FK constraints."""
        user = UserModel(
            full_name="Test User",
            email="logtest@test.com",
            password_hash="hash",
        )
        session.add(user)
        session.flush()

        greenhouse = GreenhouseModel(name="GH1")
        session.add(greenhouse)
        session.flush()

        module = ModuleModel(
            greenhouse_id=greenhouse.id,
            name="Module 1",
            width_m=5.0,
            length_m=2.0,
        )
        session.add(module)
        session.flush()

        activity_type = ActivityTypeModel(
            code="test_activity",
            name="Test Activity",
            category="test",
        )
        session.add(activity_type)
        session.flush()
        session.commit()

        return user.id, module.id, activity_type.id

    def test_create_and_get_by_id(self, db_session):
        user_id, module_id, type_id = self._create_prerequisites(db_session)
        repo = SqlActivityLogRepository(db_session)

        log = ActivityLog(
            module_id=module_id,
            activity_type_id=type_id,
            user_id=user_id,
            notes="Test note",
        )
        created = repo.create(log)
        assert created.id is not None
        assert created.notes == "Test note"
        assert created.occurred_at is not None
        assert created.sync_status == "pending"

        fetched = repo.get_by_id(created.id)
        assert fetched is not None
        assert fetched.module_id == module_id

    def test_list_by_module(self, db_session):
        user_id, module_id, type_id = self._create_prerequisites(db_session)
        repo = SqlActivityLogRepository(db_session)

        repo.create(ActivityLog(module_id=module_id, activity_type_id=type_id, user_id=user_id))
        repo.create(ActivityLog(module_id=module_id, activity_type_id=type_id, user_id=user_id))

        logs = repo.list_by_module(module_id)
        assert len(logs) == 2

    def test_list_recent(self, db_session):
        user_id, module_id, type_id = self._create_prerequisites(db_session)
        repo = SqlActivityLogRepository(db_session)

        for _ in range(5):
            repo.create(ActivityLog(module_id=module_id, activity_type_id=type_id, user_id=user_id))

        recent = repo.list_recent(limit=3)
        assert len(recent) == 3

    def test_list_by_user(self, db_session):
        user_id, module_id, type_id = self._create_prerequisites(db_session)
        repo = SqlActivityLogRepository(db_session)

        repo.create(ActivityLog(module_id=module_id, activity_type_id=type_id, user_id=user_id))

        logs = repo.list_by_user(user_id)
        assert len(logs) == 1


class TestSqlExportPackageRepository:
    """Tests for SqlExportPackageRepository CRUD operations."""

    def _create_user(self, session):
        user = UserModel(
            full_name="Export User",
            email="export@test.com",
            password_hash="hash",
        )
        session.add(user)
        session.flush()
        session.commit()
        return user.id

    def test_create_and_get_by_id(self, db_session):
        user_id = self._create_user(db_session)
        repo = SqlExportPackageRepository(db_session)

        ep = ExportPackage(created_by_user_id=user_id, scope="full")
        created = repo.create(ep)
        assert created.id is not None
        assert created.scope == "full"
        assert created.status == "pending"
        assert created.created_at is not None

        fetched = repo.get_by_id(created.id)
        assert fetched is not None
        assert fetched.created_by_user_id == user_id

    def test_list_by_user(self, db_session):
        user_id = self._create_user(db_session)
        repo = SqlExportPackageRepository(db_session)

        repo.create(ExportPackage(created_by_user_id=user_id, scope="full"))
        repo.create(ExportPackage(created_by_user_id=user_id, scope="module", scope_id=1))

        packages = repo.list_by_user(user_id)
        assert len(packages) == 2

    def test_update_status(self, db_session):
        user_id = self._create_user(db_session)
        repo = SqlExportPackageRepository(db_session)

        created = repo.create(ExportPackage(created_by_user_id=user_id, scope="full"))
        updated = repo.update(created.id, {
            "status": "completed",
            "file_path": "outputs/exports/test.zip",
            "file_size_bytes": 5000,
            "records_count": 10,
            "images_count": 5,
        })
        assert updated.status == "completed"
        assert updated.file_path == "outputs/exports/test.zip"
        assert updated.file_size_bytes == 5000

    def test_list_pending(self, db_session):
        user_id = self._create_user(db_session)
        repo = SqlExportPackageRepository(db_session)

        repo.create(ExportPackage(created_by_user_id=user_id, scope="full"))
        ep2 = repo.create(ExportPackage(created_by_user_id=user_id, scope="module"))
        repo.update(ep2.id, {"status": "completed"})

        pending = repo.list_pending()
        assert len(pending) == 1


class TestModuleMonitoringFrequencyPersistence:
    """Tests for Module.monitoring_frequency_days persistence."""

    def test_module_persists_monitoring_frequency_days(self, db_session):
        """Module.monitoring_frequency_days is persisted and retrieved."""
        from src.infrastructure.persistence.models.greenhouse_model import GreenhouseModel
        from src.domain.entities.module import Module
        from src.infrastructure.persistence.repositories.sql_module_repository import SqlModuleRepository

        gh = GreenhouseModel(name="GH Freq Test")
        db_session.add(gh)
        db_session.flush()

        repo = SqlModuleRepository(session=db_session)
        module = Module(greenhouse_id=gh.id, name="Freq Module", monitoring_frequency_days=7)
        created = repo.create(greenhouse_id=gh.id, module=module)
        assert created.monitoring_frequency_days == 7

        fetched = repo.get_by_id(created.id)
        assert fetched.monitoring_frequency_days == 7


class TestMonitoringNewFieldsPersistence:
    """Tests for Monitoring.created_by_user_id and sync_status persistence."""

    def test_monitoring_persists_created_by_user_id_and_sync_status(self, db_session):
        """Monitoring.created_by_user_id and sync_status are persisted."""
        from src.infrastructure.persistence.models.greenhouse_model import GreenhouseModel
        from src.infrastructure.persistence.models.module_model import ModuleModel
        from src.infrastructure.persistence.models.user_model import UserModel
        from src.domain.entities.monitoring import Monitoring
        from src.infrastructure.persistence.repositories.sql_monitoring_repository import SqlMonitoringRepository

        gh = GreenhouseModel(name="GH Mon Test")
        db_session.add(gh)
        db_session.flush()

        mod = ModuleModel(greenhouse_id=gh.id, name="Mod Mon Test")
        db_session.add(mod)
        db_session.flush()

        user = UserModel(full_name="Op", email="op@test.com", password_hash="hash123", role="operator")
        db_session.add(user)
        db_session.flush()

        repo = SqlMonitoringRepository(session=db_session)
        monitoring = Monitoring(module_id=mod.id, width_m=5.0, length_m=2.0, created_by_user_id=user.id, sync_status="pending")
        created = repo.create(module_id=mod.id, monitoring=monitoring)
        assert created.created_by_user_id == user.id
        assert created.sync_status == "pending"

        fetched = repo.get_by_id(created.id)
        assert fetched.created_by_user_id == user.id
        assert fetched.sync_status == "pending"
