"""Concrete SQLAlchemy implementation of InspectionResultRepository.

Persists DetectionInspectionResult entities to the SQLite database, converting
between domain dataclasses and ORM models internally.
"""

from typing import Optional

from sqlalchemy.orm import Session

from src.domain.entities.inspection_result import DetectionInspectionResult
from src.domain.exceptions import ParentNotFoundError, RecoveredEntityAlreadyExistsError
from src.domain.repositories.inspection_result_repository import (
    InspectionResultRepository,
)
from src.infrastructure.persistence.models.inspection_result_model import (
    InspectionResultModel,
    utcnow,
)
from src.infrastructure.persistence.models.snapshot_model import SnapshotModel


class SqlInspectionResultRepository(InspectionResultRepository):
    """SQLAlchemy-backed repository for DetectionInspectionResult entities.

    All operations use the injected Session instance. Cascade deletes are
    handled by the ORM relationship configuration on SnapshotModel.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, snapshot_id: int, result: DetectionInspectionResult
    ) -> DetectionInspectionResult:
        """Persist a new inspection result under the given snapshot.

        Raises ParentNotFoundError if snapshot_id does not exist.
        """
        snapshot = self._session.get(SnapshotModel, snapshot_id)
        if snapshot is None:
            raise ParentNotFoundError("Snapshot", snapshot_id)

        model = InspectionResultModel(
            snapshot_id=snapshot_id,
            detection_index=result.detection_index,
            bbox_x1=result.bbox_x1,
            bbox_y1=result.bbox_y1,
            bbox_x2=result.bbox_x2,
            bbox_y2=result.bbox_y2,
            detection_score=result.detection_score,
            health_label=result.health_label,
            health_confidence=result.health_confidence,
            maturity_stage=result.maturity_stage,
            maturity_percent=result.maturity_percent,
        )
        self._session.add(model)
        self._session.flush()
        return self._to_entity(model)

    def get_by_snapshot(self, snapshot_id: int) -> list[DetectionInspectionResult]:
        """Return all inspection results for the given snapshot.

        Returns an empty list if none exist.
        """
        models = (
            self._session.query(InspectionResultModel)
            .filter(InspectionResultModel.snapshot_id == snapshot_id)
            .all()
        )
        return [self._to_entity(m) for m in models]

    def get_by_monitoring(self, monitoring_id: int) -> list[DetectionInspectionResult]:
        """Return all inspection results across all snapshots of the given monitoring.

        Joins InspectionResultModel with SnapshotModel where
        snapshot.monitoring_id matches the given monitoring_id.
        Returns an empty list if none exist.
        """
        models = (
            self._session.query(InspectionResultModel)
            .join(SnapshotModel, InspectionResultModel.snapshot_id == SnapshotModel.id)
            .filter(SnapshotModel.monitoring_id == monitoring_id)
            .all()
        )
        return [self._to_entity(m) for m in models]

    def find_by_remote_id(
        self, remote_id: str
    ) -> Optional[DetectionInspectionResult]:
        """Return the inspection result mapped to the given remote_id, or None."""
        model = (
            self._session.query(InspectionResultModel)
            .filter(InspectionResultModel.remote_id == remote_id)
            .first()
        )
        if model is None:
            return None
        return self._to_entity(model)

    def insert_preserving_remote_id(
        self, snapshot_id: int, entity: DetectionInspectionResult, remote_id: str
    ) -> DetectionInspectionResult:
        """Insert a recovered inspection result under the LOCAL parent snapshot id.

        Import-missing-only: guards against duplicate remote_id, assigns a new
        autoincrement id, and marks the row synced. Commits so the recovered
        row is a durable LOCAL checkpoint (create() stays flush-only, unchanged).
        """
        existing = (
            self._session.query(InspectionResultModel)
            .filter(InspectionResultModel.remote_id == remote_id)
            .first()
        )
        if existing is not None:
            raise RecoveredEntityAlreadyExistsError(
                "DetectionInspectionResult", remote_id
            )

        model = InspectionResultModel(
            snapshot_id=snapshot_id,
            detection_index=entity.detection_index,
            bbox_x1=entity.bbox_x1,
            bbox_y1=entity.bbox_y1,
            bbox_x2=entity.bbox_x2,
            bbox_y2=entity.bbox_y2,
            detection_score=entity.detection_score,
            health_label=entity.health_label,
            health_confidence=entity.health_confidence,
            maturity_stage=entity.maturity_stage,
            maturity_percent=entity.maturity_percent,
            remote_id=remote_id,
            remote_sync_status="synced",
            remote_sync_error=None,
            last_synced_at=utcnow(),
        )
        if entity.created_at is not None:
            model.created_at = entity.created_at
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

    def _to_entity(self, model: InspectionResultModel) -> DetectionInspectionResult:
        """Convert an ORM model instance to a domain entity."""
        return DetectionInspectionResult(
            id=model.id,
            snapshot_id=model.snapshot_id,
            detection_index=model.detection_index,
            bbox_x1=model.bbox_x1,
            bbox_y1=model.bbox_y1,
            bbox_x2=model.bbox_x2,
            bbox_y2=model.bbox_y2,
            detection_score=model.detection_score,
            health_label=model.health_label,
            health_confidence=model.health_confidence,
            maturity_stage=model.maturity_stage,
            maturity_percent=model.maturity_percent,
            created_at=model.created_at,
        )
