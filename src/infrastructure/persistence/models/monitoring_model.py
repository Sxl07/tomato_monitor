"""SQLAlchemy ORM model for the Monitoring entity."""

from datetime import datetime, timezone

from sqlalchemy import Integer, String, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.persistence.models.base import Base


def utcnow() -> datetime:
    """Return current UTC time as timezone-naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MonitoringModel(Base):
    """ORM model mapping the Monitoring entity to the 'monitorings' table."""

    __tablename__ = "monitorings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    module_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("modules.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="initializing"
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    width_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    length_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_snapshots: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_detections: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    sync_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )

    # Relationships
    module = relationship("ModuleModel", back_populates="monitorings")
    snapshots = relationship(
        "SnapshotModel", back_populates="monitoring", cascade="all, delete-orphan"
    )
    metrics = relationship(
        "MonitoringMetricsModel",
        back_populates="monitoring",
        uselist=False,
        cascade="all, delete-orphan",
    )
