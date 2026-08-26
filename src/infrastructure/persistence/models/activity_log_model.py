"""SQLAlchemy ORM model for the ActivityLog entity."""

from datetime import datetime, timezone

from sqlalchemy import Integer, String, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.persistence.models.base import Base


def utcnow() -> datetime:
    """Return current UTC time as timezone-naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ActivityLogModel(Base):
    """ORM model mapping the ActivityLog entity to the 'activity_logs' table."""

    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    module_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("modules.id"), nullable=False
    )
    activity_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("activity_types.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    product_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    sync_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )

    # Remote sync metadata
    remote_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    remote_sync_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remote_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    module = relationship("ModuleModel", back_populates="activity_logs")
    activity_type = relationship("ActivityTypeModel")
    user = relationship("UserModel")
