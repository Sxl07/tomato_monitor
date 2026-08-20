"""SQLAlchemy ORM model for the Module entity."""

from datetime import datetime, timezone

from sqlalchemy import Integer, String, Float, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.persistence.models.base import Base


def utcnow() -> datetime:
    """Return current UTC time as timezone-naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ModuleModel(Base):
    """ORM model mapping the Module entity to the 'modules' table."""

    __tablename__ = "modules"
    __table_args__ = (
        UniqueConstraint("greenhouse_id", "name", name="uq_module_greenhouse_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    greenhouse_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("greenhouses.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    crop_type: Mapped[str] = mapped_column(
        String(100), nullable=False, default="Tomate Cherry"
    )
    width_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    length_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    monitoring_frequency_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    # Relationships
    greenhouse = relationship("GreenhouseModel", back_populates="modules")
    monitorings = relationship(
        "MonitoringModel", back_populates="module", cascade="all, delete-orphan"
    )
    activity_logs = relationship(
        "ActivityLogModel", back_populates="module", cascade="all, delete-orphan"
    )
