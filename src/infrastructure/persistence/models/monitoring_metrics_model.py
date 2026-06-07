"""SQLAlchemy ORM model for the MonitoringMetrics entity."""

from datetime import datetime, timezone

from sqlalchemy import Integer, Float, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.persistence.models.base import Base


def utcnow() -> datetime:
    """Return current UTC time as timezone-naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MonitoringMetricsModel(Base):
    """ORM model mapping the MonitoringMetrics entity to the 'monitoring_metrics' table."""

    __tablename__ = "monitoring_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    monitoring_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("monitorings.id"), nullable=False, unique=True
    )
    total_tomatoes: Mapped[int] = mapped_column(Integer, nullable=False)
    healthy_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unhealthy_count: Mapped[int] = mapped_column(Integer, nullable=False)
    pct_healthy: Mapped[float] = mapped_column(Float, nullable=False)
    pct_unhealthy: Mapped[float] = mapped_column(Float, nullable=False)
    pct_green: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    pct_breaker: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    pct_turning: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    pct_pink: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    pct_light_red: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    pct_red: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    snapshots_with_detections: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )

    # Relationships
    monitoring = relationship("MonitoringModel", back_populates="metrics")
