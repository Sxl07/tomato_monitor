"""Concrete SQLAlchemy implementation of InspectionResultRepository.

Persists DetectionInspectionResult entities to the SQLite database, converting
between domain dataclasses and ORM models internally.
"""

from sqlalchemy.orm import Session

from src.domain.entities.inspection_result import DetectionInspectionResult
from src.domain.exceptions import ParentNotFoundError
from src.domain.repositories.inspection_result_repository import (
    InspectionResultRepository,
)
from src.infrastructure.persistence.models.inspection_result_model import (
    InspectionResultModel,
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
