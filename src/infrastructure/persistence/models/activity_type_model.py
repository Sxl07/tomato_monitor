"""SQLAlchemy ORM model for the ActivityType entity."""

from sqlalchemy import Integer, String, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.persistence.models.base import Base


class ActivityTypeModel(Base):
    """ORM model mapping the ActivityType entity to the 'activity_types' table."""

    __tablename__ = "activity_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    requires_product: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    allows_quantity: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    default_unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
