"""Activity service for managing agricultural activity logs.

Coordinates creation and querying of agricultural activities.
Does not import SQLAlchemy or FastAPI — receives repos as parameters.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.domain.entities.activity_log import ActivityLog
from src.domain.repositories.activity_type_repository import ActivityTypeRepository
from src.domain.repositories.activity_log_repository import ActivityLogRepository


class ActivityValidationError(Exception):
    """Raised when activity input fails validation."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ActivityTypeNotFoundError(Exception):
    """Raised when the referenced activity type does not exist or is inactive."""

    def __init__(self, activity_type_id: int):
        self.message = "Tipo de actividad no encontrado o inactivo."
        super().__init__(self.message)


class ActivityService:
    """Coordinates creation and querying of agricultural activities."""

    def list_activity_types(self, activity_type_repo: ActivityTypeRepository) -> list:
        """Return all active activity types for form select."""
        return activity_type_repo.list_active()

    def list_activities_by_module(
        self,
        module_id: int,
        activity_log_repo: ActivityLogRepository,
        activity_type_repo: ActivityTypeRepository,
    ) -> list[dict]:
        """Return activities for a module with type info for display."""
        logs = activity_log_repo.list_by_module(module_id)
        types = {t.id: t for t in activity_type_repo.list_all()}
        items = []
        for log in logs:
            at = types.get(log.activity_type_id)
            items.append({
                "id": log.id,
                "activity_type_name": at.name if at else "Desconocido",
                "category": at.category if at else "",
                "occurred_at": log.occurred_at,
                "product_name": log.product_name,
                "quantity": log.quantity,
                "unit": log.unit,
                "notes": log.notes,
                "sync_status": log.sync_status,
            })
        return items

    def create_activity(
        self,
        module_id: int,
        activity_type_id: int,
        user_id: int,
        product_name: Optional[str],
        quantity: Optional[float],
        unit: Optional[str],
        notes: Optional[str],
        occurred_at: Optional[datetime],
        activity_type_repo: ActivityTypeRepository,
        activity_log_repo: ActivityLogRepository,
    ) -> ActivityLog:
        """Create and persist a new activity log entry with validation."""
        if module_id <= 0:
            raise ActivityValidationError("module_id must be positive")
        if user_id <= 0:
            raise ActivityValidationError("user_id must be positive")

        # Validate activity type exists and is active
        activity_type = activity_type_repo.get_by_id(activity_type_id)
        if activity_type is None or not activity_type.is_active:
            raise ActivityTypeNotFoundError(activity_type_id)

        # Validate product if required
        clean_product = product_name.strip() if product_name else None
        if clean_product == "":
            clean_product = None
        if activity_type.requires_product and not clean_product:
            raise ActivityValidationError(
                "El producto es obligatorio para este tipo de actividad."
            )

        # Validate quantity
        clean_quantity = None
        clean_unit = None
        if activity_type.allows_quantity:
            if quantity is not None:
                if quantity <= 0:
                    raise ActivityValidationError(
                        "La cantidad debe ser mayor a cero."
                    )
                clean_quantity = quantity
                clean_unit = (unit.strip() if unit else None) or activity_type.default_unit
        # If allows_quantity=False, ignore quantity/unit

        # Clean notes
        clean_notes = notes.strip() if notes else None
        if clean_notes == "":
            clean_notes = None

        # Default occurred_at
        if occurred_at is None:
            occurred_at = datetime.now(timezone.utc).replace(tzinfo=None)

        log = ActivityLog(
            module_id=module_id,
            activity_type_id=activity_type_id,
            user_id=user_id,
            product_name=clean_product,
            quantity=clean_quantity,
            unit=clean_unit,
            notes=clean_notes,
            occurred_at=occurred_at,
            sync_status="pending",
        )
        return activity_log_repo.create(log)
