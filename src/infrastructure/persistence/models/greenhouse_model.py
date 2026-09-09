"""SQLAlchemy ORM model for the Greenhouse entity."""

from datetime import datetime, timezone

from sqlalchemy import Integer, String, DateTime, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.persistence.models.base import Base


def utcnow() -> datetime:
    """Return current UTC time as timezone-naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class GreenhouseModel(Base):
    """ORM model mapping the Greenhouse entity to the 'greenhouses' table."""

    __tablename__ = "greenhouses"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "name", name="uq_greenhouse_owner_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, default=None
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    # Remote sync metadata
    remote_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    remote_sync_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remote_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    modules = relationship(
        "ModuleModel", back_populates="greenhouse", cascade="all, delete-orphan"
    )
