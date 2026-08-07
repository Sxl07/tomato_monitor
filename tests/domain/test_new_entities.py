"""Tests for Spec 015 domain entities: User, ActivityType, ActivityLog, ExportPackage.

Also tests extensions to Module and Monitoring entities.
"""

import pytest
from datetime import datetime

from src.domain.entities.user import User
from src.domain.entities.activity_type import ActivityType
from src.domain.entities.activity_log import ActivityLog
from src.domain.entities.export_package import ExportPackage
from src.domain.entities.module import Module
from src.domain.entities.monitoring import Monitoring
from src.domain.value_objects.sync_status import SyncStatus, ExportStatus


class TestUserEntity:
    """Tests for the User domain entity."""

    def test_create_user_with_required_fields(self):
        user = User(
            full_name="Juan Pérez",
            email="juan@test.com",
            password_hash="hashed_pw_123",
        )
        assert user.full_name == "Juan Pérez"
        assert user.email == "juan@test.com"
        assert user.password_hash == "hashed_pw_123"
        assert user.id is None
        assert user.role == "operator"
        assert user.is_active is True
        assert user.remote_user_id is None
        assert user.sync_status == "local_only"
        assert user.created_at is None
        assert user.updated_at is None
        assert user.last_login_at is None

    def test_create_admin_user(self):
        user = User(
            full_name="Admin",
            email="admin@test.com",
            password_hash="hash",
            role="admin",
        )
        assert user.role == "admin"

    def test_user_with_all_optional_fields(self):
        now = datetime(2025, 1, 15, 10, 30)
        user = User(
            full_name="Test",
            email="test@test.com",
            password_hash="hash",
            id=1,
            role="operator",
            is_active=False,
            remote_user_id="remote-123",
            sync_status="synced",
            created_at=now,
            updated_at=now,
            last_login_at=now,
        )
        assert user.id == 1
        assert user.is_active is False
        assert user.remote_user_id == "remote-123"
        assert user.sync_status == "synced"
        assert user.last_login_at == now


class TestActivityTypeEntity:
    """Tests for the ActivityType domain entity."""

    def test_create_activity_type_with_required_fields(self):
        at = ActivityType(code="riego", name="Riego", category="mantenimiento")
        assert at.code == "riego"
        assert at.name == "Riego"
        assert at.category == "mantenimiento"
        assert at.id is None
        assert at.requires_product is False
        assert at.allows_quantity is False
        assert at.default_unit is None
        assert at.is_active is True

    def test_create_activity_type_with_product_and_quantity(self):
        at = ActivityType(
            code="fertilizacion",
            name="Fertilización",
            category="nutrición",
            requires_product=True,
            allows_quantity=True,
            default_unit="L",
        )
        assert at.requires_product is True
        assert at.allows_quantity is True
        assert at.default_unit == "L"


class TestActivityLogEntity:
    """Tests for the ActivityLog domain entity."""

    def test_create_activity_log_with_required_fields(self):
        log = ActivityLog(module_id=1, activity_type_id=2, user_id=3)
        assert log.module_id == 1
        assert log.activity_type_id == 2
        assert log.user_id == 3
        assert log.id is None
        assert log.product_name is None
        assert log.quantity is None
        assert log.unit is None
        assert log.notes is None
        assert log.occurred_at is None
        assert log.created_at is None
        assert log.sync_status == "pending"

    def test_create_activity_log_with_all_fields(self):
        now = datetime(2025, 1, 15, 10, 30)
        log = ActivityLog(
            module_id=1,
            activity_type_id=2,
            user_id=3,
            id=10,
            product_name="NPK 20-20-20",
            quantity=2.5,
            unit="L",
            notes="Applied evenly",
            occurred_at=now,
            created_at=now,
            sync_status="exported",
        )
        assert log.product_name == "NPK 20-20-20"
        assert log.quantity == 2.5
        assert log.unit == "L"
        assert log.notes == "Applied evenly"
        assert log.sync_status == "exported"


class TestExportPackageEntity:
    """Tests for the ExportPackage domain entity."""

    def test_create_export_package_with_required_fields(self):
        ep = ExportPackage(created_by_user_id=1, scope="full")
        assert ep.created_by_user_id == 1
        assert ep.scope == "full"
        assert ep.id is None
        assert ep.scope_id is None
        assert ep.file_path is None
        assert ep.file_size_bytes is None
        assert ep.status == "pending"
        assert ep.error_message is None
        assert ep.records_count == 0
        assert ep.images_count == 0
        assert ep.created_at is None
        assert ep.completed_at is None
        assert ep.manifest_json is None

    def test_create_export_package_completed(self):
        now = datetime(2025, 1, 15, 12, 0)
        ep = ExportPackage(
            created_by_user_id=1,
            scope="module",
            scope_id=5,
            file_path="outputs/exports/export_1_2025.zip",
            file_size_bytes=1024000,
            status="completed",
            records_count=50,
            images_count=20,
            created_at=now,
            completed_at=now,
            manifest_json='{"checksum": "abc123"}',
        )
        assert ep.scope_id == 5
        assert ep.file_size_bytes == 1024000
        assert ep.status == "completed"
        assert ep.records_count == 50


class TestModuleExtension:
    """Tests for the monitoring_frequency_days extension to Module."""

    def test_module_default_no_frequency(self):
        m = Module(greenhouse_id=1, name="Módulo 1")
        assert m.monitoring_frequency_days is None

    def test_module_with_frequency(self):
        m = Module(greenhouse_id=1, name="Módulo 1", monitoring_frequency_days=7)
        assert m.monitoring_frequency_days == 7


class TestMonitoringExtension:
    """Tests for created_by_user_id and sync_status extensions to Monitoring."""

    def test_monitoring_default_sync_status(self):
        m = Monitoring(module_id=1, width_m=5.0, length_m=2.0)
        assert m.created_by_user_id is None
        assert m.sync_status == "pending"

    def test_monitoring_with_user_and_sync(self):
        m = Monitoring(
            module_id=1,
            width_m=5.0,
            length_m=2.0,
            created_by_user_id=3,
            sync_status="exported",
        )
        assert m.created_by_user_id == 3
        assert m.sync_status == "exported"


class TestSyncStatusEnum:
    """Tests for the SyncStatus and ExportStatus enums."""

    def test_sync_status_values(self):
        assert SyncStatus.PENDING == "pending"
        assert SyncStatus.EXPORTED == "exported"
        assert SyncStatus.SYNCED == "synced"
        assert SyncStatus.ERROR == "error"
        assert SyncStatus.LOCAL_ONLY == "local_only"
        assert SyncStatus.PENDING_SYNC == "pending_sync"

    def test_export_status_values(self):
        assert ExportStatus.PENDING == "pending"
        assert ExportStatus.GENERATING == "generating"
        assert ExportStatus.COMPLETED == "completed"
        assert ExportStatus.ERROR == "error"

    def test_sync_status_is_string(self):
        assert isinstance(SyncStatus.PENDING, str)
        assert SyncStatus.PENDING == "pending"

    def test_export_status_is_string(self):
        assert isinstance(ExportStatus.COMPLETED, str)
        assert ExportStatus.COMPLETED == "completed"


class TestUserValidation:
    """Negative tests for User __post_init__ validations."""

    def test_empty_email_raises(self):
        with pytest.raises(ValueError, match="email"):
            User(full_name="Test", email="", password_hash="hash123")

    def test_empty_password_hash_raises(self):
        with pytest.raises(ValueError, match="password_hash"):
            User(full_name="Test", email="a@b.c", password_hash="")

    def test_invalid_role_raises(self):
        with pytest.raises(ValueError, match="role"):
            User(full_name="Test", email="a@b.c", password_hash="hash", role="superuser")

    def test_invalid_sync_status_raises(self):
        with pytest.raises(ValueError, match="sync_status"):
            User(full_name="Test", email="a@b.c", password_hash="hash", sync_status="bad")

    def test_empty_full_name_raises(self):
        with pytest.raises(ValueError, match="full_name"):
            User(full_name="", email="a@b.c", password_hash="hash")


class TestActivityTypeValidation:
    """Negative tests for ActivityType __post_init__ validations."""

    def test_empty_code_raises(self):
        with pytest.raises(ValueError, match="code"):
            ActivityType(code="", name="Test", category="cat")

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="name"):
            ActivityType(code="test", name="", category="cat")

    def test_empty_category_raises(self):
        with pytest.raises(ValueError, match="category"):
            ActivityType(code="test", name="Test", category="")


class TestActivityLogValidation:
    """Negative tests for ActivityLog __post_init__ validations."""

    def test_zero_module_id_raises(self):
        with pytest.raises(ValueError, match="module_id"):
            ActivityLog(module_id=0, activity_type_id=1, user_id=1)

    def test_zero_activity_type_id_raises(self):
        with pytest.raises(ValueError, match="activity_type_id"):
            ActivityLog(module_id=1, activity_type_id=0, user_id=1)

    def test_zero_user_id_raises(self):
        with pytest.raises(ValueError, match="user_id"):
            ActivityLog(module_id=1, activity_type_id=1, user_id=0)

    def test_negative_quantity_raises(self):
        with pytest.raises(ValueError, match="quantity"):
            ActivityLog(module_id=1, activity_type_id=1, user_id=1, quantity=-1.0)

    def test_invalid_sync_status_raises(self):
        with pytest.raises(ValueError, match="sync_status"):
            ActivityLog(module_id=1, activity_type_id=1, user_id=1, sync_status="bad")


class TestExportPackageValidation:
    """Negative tests for ExportPackage __post_init__ validations."""

    def test_zero_user_id_raises(self):
        with pytest.raises(ValueError, match="created_by_user_id"):
            ExportPackage(created_by_user_id=0, scope="full")

    def test_invalid_scope_raises(self):
        with pytest.raises(ValueError, match="scope"):
            ExportPackage(created_by_user_id=1, scope="invalid")

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError, match="status"):
            ExportPackage(created_by_user_id=1, scope="full", status="bad")

    def test_negative_records_count_raises(self):
        with pytest.raises(ValueError, match="records_count"):
            ExportPackage(created_by_user_id=1, scope="full", records_count=-1)

    def test_negative_images_count_raises(self):
        with pytest.raises(ValueError, match="images_count"):
            ExportPackage(created_by_user_id=1, scope="full", images_count=-1)

    def test_negative_file_size_raises(self):
        with pytest.raises(ValueError, match="file_size_bytes"):
            ExportPackage(created_by_user_id=1, scope="full", file_size_bytes=-1)


class TestModuleFrequencyValidation:
    """Negative tests for Module.monitoring_frequency_days validation."""

    def test_zero_frequency_raises(self):
        with pytest.raises(ValueError, match="monitoring_frequency_days"):
            Module(greenhouse_id=1, name="M1", monitoring_frequency_days=0)

    def test_negative_frequency_raises(self):
        with pytest.raises(ValueError, match="monitoring_frequency_days"):
            Module(greenhouse_id=1, name="M1", monitoring_frequency_days=-5)

    def test_valid_frequency(self):
        m = Module(greenhouse_id=1, name="M1", monitoring_frequency_days=7)
        assert m.monitoring_frequency_days == 7


class TestMonitoringNewFieldsValidation:
    """Negative tests for Monitoring created_by_user_id and sync_status validation."""

    def test_zero_created_by_user_id_raises(self):
        with pytest.raises(ValueError, match="created_by_user_id"):
            Monitoring(module_id=1, width_m=5.0, length_m=2.0, created_by_user_id=0)

    def test_invalid_sync_status_raises(self):
        with pytest.raises(ValueError, match="sync_status"):
            Monitoring(module_id=1, width_m=5.0, length_m=2.0, sync_status="bad")

    def test_default_sync_status_pending(self):
        m = Monitoring(module_id=1, width_m=5.0, length_m=2.0)
        assert m.sync_status == "pending"
