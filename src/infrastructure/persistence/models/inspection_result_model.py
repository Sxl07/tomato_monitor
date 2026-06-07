"""SQLAlchemy ORM model for the InspectionResult entity."""

from datetime import datetime, timezone

from sqlalchemy import Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.persistence.models.base import Base


def utcnow() -> datetime:
    """Return current UTC time as timezone-naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class InspectionResultModel(Base):
    """ORM model mapping the InspectionResult entity to the 'inspection_results' table."""

    __tablename__ = "inspection_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("snapshots.id"), nullable=False
    )
    detection_index: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_x1: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_y1: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_x2: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_y2: Mapped[int] = mapped_column(Integer, nullable=False)
    detection_score: Mapped[float] = mapped_column(Float, nullable=False)
    health_label: Mapped[str] = mapped_column(String(20), nullable=False)
    health_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    maturity_stage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    maturity_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )

    # Relationships
    snapshot = relationship("SnapshotModel", back_populates="inspection_results")
