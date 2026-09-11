"""Concrete repository implementation for Snapshot using SQLAlchemy ORM."""

import re
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.domain.entities.snapshot import Snapshot
from src.domain.exceptions import (
    InvalidImagePathError,
    ParentNotFoundError,
    RecoveredEntityAlreadyExistsError,
)
from src.domain.repositories.snapshot_repository import SnapshotRepository
from src.infrastructure.persistence.models.snapshot_model import SnapshotModel, utcnow


# Regex to detect Windows drive letter absolute paths (e.g., C:\, D:/)
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:[\\\/]")


class SqlSnapshotRepository(SnapshotRepository):
    """SQLAlchemy-backed implementation of SnapshotRepository."""

    def __init__(self, session: Session) -> None:
        """Initialize with an active SQLAlchemy session.

        Args:
            session: SQLAlchemy Session instance for database operations.
        """
        self._session = session

    def create(self, monitoring_id: int, snapshot: Snapshot) -> Snapshot:
        """Persist a new snapshot under the given monitoring.

        Validates image_path before insertion:
        - Rejects paths exceeding 500 characters.
        - Rejects paths containing '../' (path traversal).
        - Rejects absolute paths (starting with '/' or drive letter like 'C:\\').

        Args:
            monitoring_id: ID of the parent monitoring session.
            snapshot: Snapshot entity to persist.

        Returns:
            Snapshot entity with assigned id and timestamps.

        Raises:
            InvalidImagePathError: If image_path fails validation.
            ParentNotFoundError: If monitoring_id does not exist.
        """
        self._validate_image_path(snapshot.image_path)

        model = self._to_model(monitoring_id, snapshot)
        self._session.add(model)
        try:
            self._session.flush()
        except IntegrityError as e:
            self._session.rollback()
            raise ParentNotFoundError(
                parent_type="Monitoring", parent_id=monitoring_id
            ) from e

        self._session.refresh(model)
        return self._to_entity(model)

    def get_by_monitoring(self, monitoring_id: int) -> list[Snapshot]:
        """Return all snapshots for the given monitoring, ordered by captured_at descending.

        Args:
            monitoring_id: ID of the parent monitoring session.

        Returns:
            List of Snapshot entities ordered by captured_at descending.
            Empty list if none exist.
        """
        models = (
            self._session.query(SnapshotModel)
            .filter(SnapshotModel.monitoring_id == monitoring_id)
            .order_by(SnapshotModel.captured_at.desc())
            .all()
        )
        return [self._to_entity(m) for m in models]

    def get_by_id(self, id: int) -> Optional[Snapshot]:
        """Return the snapshot with the given id, or None if not found.

        Args:
            id: Snapshot primary key.

        Returns:
            Snapshot entity or None if no record exists with that id.
        """
        model = self._session.get(SnapshotModel, id)
        if model is None:
            return None
        return self._to_entity(model)

    def update_has_detections(self, id: int, has_detections: bool) -> Snapshot:
        """Update the has_detections field on a snapshot.

        Args:
            id: Snapshot primary key.
            has_detections: New value for has_detections flag.

        Returns:
            Updated Snapshot domain entity.

        Raises:
            ParentNotFoundError: If no snapshot exists with the given id.
        """
        model = self._session.get(SnapshotModel, id)
        if model is None:
            raise ParentNotFoundError("Snapshot", id)
        model.has_detections = has_detections
        self._session.flush()
        return self._to_entity(model)

    def find_by_remote_id(self, remote_id: str) -> Optional[Snapshot]:
        """Return the snapshot mapped to the given remote_id, or None."""
        model = (
            self._session.query(SnapshotModel)
            .filter(SnapshotModel.remote_id == remote_id)
            .first()
        )
        if model is None:
            return None
        return self._to_entity(model)

    def insert_preserving_remote_id(
        self,
        monitoring_id: int,
        entity: Snapshot,
        remote_id: str,
        raw_storage_path: Optional[str] = None,
        annotated_storage_path: Optional[str] = None,
    ) -> Snapshot:
        """Insert a recovered snapshot under the LOCAL parent monitoring id.

        Import-missing-only: validates image_path, guards against duplicate
        remote_id, assigns a new autoincrement id, records remote storage paths
        as given, and marks the row synced. Commits so the recovered row is a
        durable LOCAL checkpoint (create() stays flush+refresh, unchanged).
        """
        self._validate_image_path(entity.image_path)

        existing = (
            self._session.query(SnapshotModel)
            .filter(SnapshotModel.remote_id == remote_id)
            .first()
        )
        if existing is not None:
            raise RecoveredEntityAlreadyExistsError("Snapshot", remote_id)

        model = SnapshotModel(
            monitoring_id=monitoring_id,
            image_path=entity.image_path,
            frame_index=entity.frame_index,
            change_score=entity.change_score,
            has_detections=entity.has_detections,
            raw_storage_path=raw_storage_path,
            annotated_storage_path=annotated_storage_path,
            remote_id=remote_id,
            remote_sync_status="synced",
            remote_sync_error=None,
            last_synced_at=utcnow(),
        )
        if entity.captured_at is not None:
            model.captured_at = entity.captured_at
        # Recovery is a durable LOCAL checkpoint: commit so the recovered row
        # survives even if a later remote request fails. Distinct transactional
        # context from create() (which only flushes); create() is unchanged.
        try:
            self._session.add(model)
            self._session.flush()
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        self._session.refresh(model)
        return self._to_entity(model)

    def _validate_image_path(self, path: str) -> None:
        """Validate image path before database insertion.

        Args:
            path: The image path to validate.

        Raises:
            InvalidImagePathError: If the path fails any validation rule.
        """
        if len(path) > 500:
            raise InvalidImagePathError(path, "exceeds 500 characters")

        if "../" in path:
            raise InvalidImagePathError(path, "contains path traversal sequence")

        if path.startswith("/") or _DRIVE_LETTER_RE.match(path):
            raise InvalidImagePathError(path, "must be a relative path")

    def _to_entity(self, model: SnapshotModel) -> Snapshot:
        """Convert a SnapshotModel ORM instance to a Snapshot domain entity.

        Args:
            model: SQLAlchemy ORM model instance.

        Returns:
            Snapshot domain entity.
        """
        return Snapshot(
            id=model.id,
            monitoring_id=model.monitoring_id,
            captured_at=model.captured_at,
            image_path=model.image_path,
            frame_index=model.frame_index,
            change_score=model.change_score,
            has_detections=model.has_detections,
        )

    def _to_model(self, monitoring_id: int, entity: Snapshot) -> SnapshotModel:
        """Convert a Snapshot domain entity to a SnapshotModel ORM instance.

        Args:
            monitoring_id: ID of the parent monitoring session.
            entity: Snapshot domain entity.

        Returns:
            SnapshotModel ORM instance ready for persistence.
        """
        return SnapshotModel(
            monitoring_id=monitoring_id,
            image_path=entity.image_path,
            frame_index=entity.frame_index,
            change_score=entity.change_score,
            has_detections=entity.has_detections,
        )
