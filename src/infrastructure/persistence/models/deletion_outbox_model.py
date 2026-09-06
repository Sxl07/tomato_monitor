"""SQLAlchemy ORM models for the durable Deletion_Outbox (local-only).

These models back the two-transaction durable deletion protocol:
- ``DeletionOutboxModel`` is the outbox entry per deleted root entity. Its
  ``status`` tracks REMOTE propagation while ``local_delete_status`` tracks the
  durability of the LOCAL delete.
- ``DeletionOutboxStoragePathModel`` records remote Storage object paths to be
  removed during remote propagation.
- ``DeletionOutboxLocalArtifactModel`` records local physical artifact paths
  (relative to ``OUTPUTS_DIR``) to be cleaned up during the deferred physical
  cleanup phase.

These tables are local-only. They do NOT modify existing tables nor the remote
Supabase schema. New columns are nullable or carry defaults for migration
safety.
"""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Integer, String, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.persistence.models.base import Base


def utcnow() -> datetime:
    """Return current UTC time as timezone-naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DeletionOutboxModel(Base):
    """ORM model for a durable deletion outbox entry ('deletion_outbox').

    ``status`` tracks REMOTE propagation (``pending | syncing | synced | error``).
    ``local_delete_status`` tracks LOCAL delete durability
    (``prepared | completed | failed``).
    """

    __tablename__ = "deletion_outbox"

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'syncing', 'synced', 'error')",
            name="ck_deletion_outbox_status",
        ),
        CheckConstraint(
            "local_delete_status IN ('prepared', 'completed', 'failed')",
            name="ck_deletion_outbox_local_delete_status",
        ),
        CheckConstraint(
            "cleanup_status IN ('pending', 'done', 'error')",
            name="ck_deletion_outbox_cleanup_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_local_id: Mapped[int] = mapped_column(Integer, nullable=False)
    remote_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    remote_table: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    # Tracks REMOTE propagation.
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    # Tracks LOCAL delete durability.
    local_delete_status: Mapped[str] = mapped_column(
        String(12), nullable=False, default="prepared"
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cleanup_status: Mapped[str] = mapped_column(
        String(12), nullable=False, default="pending"
    )
    # Moment of successful LOCAL deletion. NOT set at creation/prepared; assigned
    # only when TX2 completes (together with local_delete_status='completed').
    # Starts the Retention_Window. Nullable with NO default by design.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships (parent -> children, cascade delete-orphan at ORM level)
    storage_paths = relationship(
        "DeletionOutboxStoragePathModel",
        back_populates="outbox",
        cascade="all, delete-orphan",
    )
    local_artifacts = relationship(
        "DeletionOutboxLocalArtifactModel",
        back_populates="outbox",
        cascade="all, delete-orphan",
    )


class DeletionOutboxStoragePathModel(Base):
    """ORM model for a remote Storage path to remove ('deletion_outbox_storage_path').

    ``status`` is one of ``pending | removed | error``; ``removed`` is the sole
    terminal state.
    """

    __tablename__ = "deletion_outbox_storage_path"

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'removed', 'error')",
            name="ck_deletion_outbox_storage_path_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    outbox_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("deletion_outbox.id", ondelete="CASCADE"),
        nullable=False,
    )
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")

    # Relationships
    outbox = relationship("DeletionOutboxModel", back_populates="storage_paths")


class DeletionOutboxLocalArtifactModel(Base):
    """ORM model for a local physical artifact to clean up.

    Table 'deletion_outbox_local_artifact'. ``relative_path`` is RELATIVE to
    ``OUTPUTS_DIR`` (no ``outputs/`` prefix, e.g. ``monitorings/{id}``).
    ``status`` is one of ``pending | done | error``.
    """

    __tablename__ = "deletion_outbox_local_artifact"

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'done', 'error')",
            name="ck_deletion_outbox_local_artifact_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    outbox_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("deletion_outbox.id", ondelete="CASCADE"),
        nullable=False,
    )
    relative_path: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    outbox = relationship("DeletionOutboxModel", back_populates="local_artifacts")
