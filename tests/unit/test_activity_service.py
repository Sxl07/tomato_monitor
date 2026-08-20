"""Unit tests for ActivityService."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.application.services.activity_service import (
    ActivityService,
    ActivityValidationError,
    ActivityTypeNotFoundError,
)
from src.domain.entities.activity_type import ActivityType
from src.domain.entities.activity_log import ActivityLog


def _make_activity_type(
    id=1,
    code="riego",
    name="Riego",
    category="mantenimiento",
    requires_product=False,
    allows_quantity=True,
    default_unit="L",
    is_active=True,
):
    at = ActivityType(
        code=code,
        name=name,
        category=category,
        requires_product=requires_product,
        allows_quantity=allows_quantity,
        default_unit=default_unit,
        is_active=is_active,
    )
    at.id = id
    return at


def _mock_type_repo(activity_type=None):
    repo = MagicMock()
    repo.get_by_id.return_value = activity_type
    repo.list_active.return_value = [activity_type] if activity_type else []
    repo.list_all.return_value = [activity_type] if activity_type else []
    return repo


def _mock_log_repo():
    repo = MagicMock()

    def _create(log):
        log.id = 99
        log.created_at = datetime(2025, 1, 1, 12, 0, 0)
        return log

    repo.create.side_effect = _create
    repo.list_by_module.return_value = []
    return repo


class TestListActivityTypes:
    """Tests for listing activity types."""

    def test_returns_active_types(self):
        at = _make_activity_type()
        type_repo = _mock_type_repo(at)
        service = ActivityService()
        result = service.list_activity_types(type_repo)
        assert len(result) == 1
        assert result[0].code == "riego"


class TestCreateActivity:
    """Tests for creating activity log entries."""

    def test_valid_creation_with_quantity(self):
        """Valid activity with quantity creates log with pending sync_status."""
        at = _make_activity_type(allows_quantity=True, default_unit="L")
        type_repo = _mock_type_repo(at)
        log_repo = _mock_log_repo()
        service = ActivityService()

        result = service.create_activity(
            module_id=1,
            activity_type_id=1,
            user_id=1,
            product_name=None,
            quantity=2.5,
            unit="L",
            notes="Test note",
            occurred_at=datetime(2025, 6, 15, 10, 0, 0),
            activity_type_repo=type_repo,
            activity_log_repo=log_repo,
        )

        assert result.id == 99
        assert result.sync_status == "pending"
        assert result.quantity == 2.5
        assert result.unit == "L"
        assert result.notes == "Test note"

    def test_requires_product_without_product_raises_error(self):
        """Activity type requiring product without product_name raises validation error."""
        at = _make_activity_type(requires_product=True)
        type_repo = _mock_type_repo(at)
        log_repo = _mock_log_repo()
        service = ActivityService()

        with pytest.raises(ActivityValidationError, match="producto es obligatorio"):
            service.create_activity(
                module_id=1,
                activity_type_id=1,
                user_id=1,
                product_name="",
                quantity=None,
                unit=None,
                notes=None,
                occurred_at=None,
                activity_type_repo=type_repo,
                activity_log_repo=log_repo,
            )

    def test_allows_quantity_with_zero_raises_error(self):
        """Quantity <= 0 raises validation error when allows_quantity is True."""
        at = _make_activity_type(allows_quantity=True)
        type_repo = _mock_type_repo(at)
        log_repo = _mock_log_repo()
        service = ActivityService()

        with pytest.raises(ActivityValidationError, match="cantidad debe ser mayor"):
            service.create_activity(
                module_id=1,
                activity_type_id=1,
                user_id=1,
                product_name=None,
                quantity=0,
                unit=None,
                notes=None,
                occurred_at=None,
                activity_type_repo=type_repo,
                activity_log_repo=log_repo,
            )

    def test_allows_quantity_negative_raises_error(self):
        """Negative quantity raises validation error."""
        at = _make_activity_type(allows_quantity=True)
        type_repo = _mock_type_repo(at)
        log_repo = _mock_log_repo()
        service = ActivityService()

        with pytest.raises(ActivityValidationError, match="cantidad debe ser mayor"):
            service.create_activity(
                module_id=1,
                activity_type_id=1,
                user_id=1,
                product_name=None,
                quantity=-5.0,
                unit=None,
                notes=None,
                occurred_at=None,
                activity_type_repo=type_repo,
                activity_log_repo=log_repo,
            )

    def test_allows_quantity_false_ignores_quantity(self):
        """When allows_quantity is False, quantity and unit are ignored."""
        at = _make_activity_type(allows_quantity=False)
        type_repo = _mock_type_repo(at)
        log_repo = _mock_log_repo()
        service = ActivityService()

        result = service.create_activity(
            module_id=1,
            activity_type_id=1,
            user_id=1,
            product_name=None,
            quantity=100.0,  # should be ignored
            unit="kg",  # should be ignored
            notes=None,
            occurred_at=datetime(2025, 6, 15),
            activity_type_repo=type_repo,
            activity_log_repo=log_repo,
        )

        assert result.quantity is None
        assert result.unit is None

    def test_activity_type_not_found_raises_error(self):
        """Non-existent activity type raises ActivityTypeNotFoundError with Spanish message."""
        type_repo = MagicMock()
        type_repo.get_by_id.return_value = None
        log_repo = _mock_log_repo()
        service = ActivityService()

        with pytest.raises(ActivityTypeNotFoundError) as exc_info:
            service.create_activity(
                module_id=1,
                activity_type_id=999,
                user_id=1,
                product_name=None,
                quantity=None,
                unit=None,
                notes=None,
                occurred_at=None,
                activity_type_repo=type_repo,
                activity_log_repo=log_repo,
            )
        assert exc_info.value.message == "Tipo de actividad no encontrado o inactivo."

    def test_activity_type_inactive_raises_error(self):
        """Inactive activity type raises ActivityTypeNotFoundError."""
        at = _make_activity_type(is_active=False)
        type_repo = _mock_type_repo(at)
        log_repo = _mock_log_repo()
        service = ActivityService()

        with pytest.raises(ActivityTypeNotFoundError):
            service.create_activity(
                module_id=1,
                activity_type_id=1,
                user_id=1,
                product_name=None,
                quantity=None,
                unit=None,
                notes=None,
                occurred_at=None,
                activity_type_repo=type_repo,
                activity_log_repo=log_repo,
            )

    def test_empty_notes_saved_as_none(self):
        """Empty or whitespace-only notes are saved as None."""
        at = _make_activity_type()
        type_repo = _mock_type_repo(at)
        log_repo = _mock_log_repo()
        service = ActivityService()

        result = service.create_activity(
            module_id=1,
            activity_type_id=1,
            user_id=1,
            product_name=None,
            quantity=None,
            unit=None,
            notes="   ",
            occurred_at=datetime(2025, 6, 15),
            activity_type_repo=type_repo,
            activity_log_repo=log_repo,
        )

        assert result.notes is None

    def test_occurred_at_none_uses_generated_datetime(self):
        """When occurred_at is None, a datetime is generated."""
        at = _make_activity_type()
        type_repo = _mock_type_repo(at)
        log_repo = _mock_log_repo()
        service = ActivityService()

        result = service.create_activity(
            module_id=1,
            activity_type_id=1,
            user_id=1,
            product_name=None,
            quantity=None,
            unit=None,
            notes=None,
            occurred_at=None,
            activity_type_repo=type_repo,
            activity_log_repo=log_repo,
        )

        assert result.occurred_at is not None

    def test_unit_uses_default_when_quantity_present_and_unit_empty(self):
        """When quantity is provided but unit is empty, uses activity_type's default_unit."""
        at = _make_activity_type(allows_quantity=True, default_unit="mL")
        type_repo = _mock_type_repo(at)
        log_repo = _mock_log_repo()
        service = ActivityService()

        result = service.create_activity(
            module_id=1,
            activity_type_id=1,
            user_id=1,
            product_name=None,
            quantity=5.0,
            unit="",
            notes=None,
            occurred_at=datetime(2025, 6, 15),
            activity_type_repo=type_repo,
            activity_log_repo=log_repo,
        )

        assert result.unit == "mL"

    def test_user_id_is_preserved_in_log(self):
        """create_activity preserves user_id in the resulting ActivityLog."""
        at = _make_activity_type()
        type_repo = _mock_type_repo(at)
        log_repo = _mock_log_repo()
        service = ActivityService()

        result = service.create_activity(
            module_id=3,
            activity_type_id=1,
            user_id=7,
            product_name=None,
            quantity=None,
            unit=None,
            notes=None,
            occurred_at=datetime(2025, 6, 15),
            activity_type_repo=type_repo,
            activity_log_repo=log_repo,
        )

        assert result.user_id == 7
        assert result.module_id == 3


class TestListActivitiesByModule:
    """Tests for listing activities by module."""

    def test_returns_formatted_activities(self):
        at = _make_activity_type(id=1, name="Riego", category="mantenimiento")
        log = ActivityLog(
            module_id=1,
            activity_type_id=1,
            user_id=1,
            product_name="Agua",
            quantity=10.0,
            unit="L",
            notes="Morning irrigation",
            occurred_at=datetime(2025, 6, 15, 8, 0, 0),
            sync_status="pending",
        )
        log.id = 1

        type_repo = MagicMock()
        type_repo.list_all.return_value = [at]
        log_repo = MagicMock()
        log_repo.list_by_module.return_value = [log]

        service = ActivityService()
        items = service.list_activities_by_module(1, log_repo, type_repo)

        assert len(items) == 1
        assert items[0]["activity_type_name"] == "Riego"
        assert items[0]["category"] == "mantenimiento"
        assert items[0]["product_name"] == "Agua"
        assert items[0]["quantity"] == 10.0
        assert items[0]["sync_status"] == "pending"
