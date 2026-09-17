"""Concrete repository implementation for ExportPackage entities using SQLAlchemy."""

from typing import Optional

from sqlalchemy.orm import Session

from src.domain.entities.export_package import ExportPackage
from src.domain.repositories.export_package_repository import ExportPackageRepository
from src.infrastructure.persistence.models.export_package_model import (
    ExportPackageModel,
)


class SqlExportPackageRepository(ExportPackageRepository):
    """SQLAlchemy-based implementation of ExportPackageRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, export_package: ExportPackage) -> ExportPackage:
        """Persist a new export package. Return it with assigned id."""
        model = self._to_model(export_package)
        self._session.add(model)
        self._session.flush()
        self._session.commit()
        self._session.refresh(model)
        return self._to_entity(model)

    def get_by_id(self, id: int) -> Optional[ExportPackage]:
        """Return the export package with the given id, or None if not found."""
        model = (
            self._session.query(ExportPackageModel)
            .filter(ExportPackageModel.id == id)
            .first()
        )
        if model is None:
            return None
        return self._to_entity(model)

    def list_by_user(self, user_id: int) -> list[ExportPackage]:
        """Return all export packages for the given user, newest first."""
        models = (
            self._session.query(ExportPackageModel)
            .filter(ExportPackageModel.created_by_user_id == user_id)
            .order_by(ExportPackageModel.created_at.desc())
            .all()
        )
        return [self._to_entity(m) for m in models]

    def update(self, id: int, fields: dict) -> ExportPackage:
        """Update export package fields. Return updated entity."""
        model = (
            self._session.query(ExportPackageModel)
            .filter(ExportPackageModel.id == id)
            .one()
        )
        allowed_fields = {
            "status",
            "file_path",
            "file_size_bytes",
            "error_message",
            "records_count",
            "images_count",
            "completed_at",
            "manifest_json",
        }
        for key, value in fields.items():
            if key in allowed_fields:
                setattr(model, key, value)
        self._session.flush()
        self._session.commit()
        self._session.refresh(model)
        return self._to_entity(model)

    def list_pending(self) -> list[ExportPackage]:
        """Return all export packages with status 'pending' or 'generating'."""
        models = (
            self._session.query(ExportPackageModel)
            .filter(ExportPackageModel.status.in_(["pending", "generating"]))
            .order_by(ExportPackageModel.created_at.desc())
            .all()
        )
        return [self._to_entity(m) for m in models]

    def delete(self, id: int) -> None:
        """Delete the ExportPackage record with the given id.

        Removes only the SQLite row. Performs no filesystem I/O. Deleting a
        nonexistent id is an idempotent no-op.
        """
        model = (
            self._session.query(ExportPackageModel)
            .filter(ExportPackageModel.id == id)
            .first()
        )
        if model is None:
            return
        self._session.delete(model)
        self._session.flush()
        self._session.commit()

    def _to_entity(self, model: ExportPackageModel) -> ExportPackage:
        return ExportPackage(
            id=model.id,
            created_by_user_id=model.created_by_user_id,
            scope=model.scope,
            scope_id=model.scope_id,
            file_path=model.file_path,
            file_size_bytes=model.file_size_bytes,
            status=model.status,
            error_message=model.error_message,
            records_count=model.records_count,
            images_count=model.images_count,
            created_at=model.created_at,
            completed_at=model.completed_at,
            manifest_json=model.manifest_json,
        )

    def _to_model(self, entity: ExportPackage) -> ExportPackageModel:
        return ExportPackageModel(
            created_by_user_id=entity.created_by_user_id,
            scope=entity.scope,
            scope_id=entity.scope_id,
            file_path=entity.file_path,
            file_size_bytes=entity.file_size_bytes,
            status=entity.status,
            error_message=entity.error_message,
            records_count=entity.records_count,
            images_count=entity.images_count,
            manifest_json=entity.manifest_json,
        )
