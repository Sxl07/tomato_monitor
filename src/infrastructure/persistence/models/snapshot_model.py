"""SQLAlchemy ORM model for the Snapshot entity."""

from datetime import datetime, timezone

from sqlalchemy import Integer, String, Float, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.persistence.models.base import Base


def utcnow() -> datetime:
    """Return current UTC time as timezone-naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SnapshotModel(Base):
    """ORM model mapping the Snapshot entity to the 'snapshots' table."""

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    monitoring_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("monitorings.id"), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    image_path: Mapped[str] = mapped_column(String(500), nullable=False)
    frame_index: Mapped[int] = mapped_column(Integer, nullable=False)
    change_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_detections: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Remote sync metadata
    remote_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    remote_sync_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remote_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Remote storage paths
    raw_storage_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    annotated_storage_path: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )

    # Relationships
    monitoring = relationship("MonitoringModel", back_populates="snapshots")
    inspection_results = relationship(
        "InspectionResultModel", back_populates="snapshot", cascade="all, delete-orphan"
    )
