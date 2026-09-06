"""Concrete SQLAlchemy implementation of LocalCascadePort.

Executes the transactional local cascade delete (TX2) of a root entity and,
within the SAME transaction, marks the associated Deletion_Outbox entry as
``local_delete_status = 'completed'`` and sets its ``deleted_at``.

Design notes:
    - This repository owns its own transaction lifecycle. It receives a
      ``session_factory`` (``Callable[[], Session]``) so that the whole TX2
      (cascade + completed marking) is a single atomic unit committed once.
    - The local cascade relies on the ORM relationship configuration
      (``cascade='all, delete-orphan'``) declared across the hierarchy:
      greenhouse -> modules -> monitorings -> snapshots -> inspection_results,
      monitorings -> metrics, and modules -> activity_logs. Deleting the root
      via ``session.delete(root_obj)`` fires those cascades. DB-level
      ``ON DELETE CASCADE`` (with ``PRAGMA foreign_keys = ON`` set per-connection
      by ``DatabaseManager``) acts as a second line of defense.
    - Only local database records are touched. Physical artifacts under
      ``outputs/`` are NEVER removed here; deferred physical cleanup is a later
      phase driven by the Retention_Window.
    - ``deleted_at`` is assigned ONLY here (on TX2 success), together with
      ``local_delete_status = 'completed'``. It must never be set during TX1
      (prepared).
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from sqlalchemy.orm import Session

from src.application.interfaces.local_cascade_port import (
    LocalCascadePort,
    LocalCascadeResult,
)
from src.infrastructure.persistence.models.deletion_outbox_model import (
    DeletionOutboxModel,
)
from src.infrastructure.persistence.models.greenhouse_model import GreenhouseModel
from src.infrastructure.persistence.models.module_model import ModuleModel
from src.infrastructure.persistence.models.monitoring_model import MonitoringModel


# Maps the supported root entity types to their ORM model classes.
_ENTITY_MODELS = {
    "greenhouse": GreenhouseModel,
    "module": ModuleModel,
    "monitoring": MonitoringModel,
}


class LocalCascadeRepository(LocalCascadePort):
    """SQLAlchemy-backed transactional local cascade delete (TX2).

    Deletes a root entity (``greenhouse`` | ``module`` | ``monitoring``) and its
    dependent hierarchy relying on local ON DELETE CASCADE / ORM cascade, and
    marks the associated outbox entry ``completed`` with ``deleted_at`` in the
    same transaction. Commits once on success; rolls back the whole transaction
    on any failure.
    """

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        """Initialize the repository.

        Args:
            session_factory: Callable returning a fresh SQLAlchemy Session. A new
                session is created per ``execute_cascade`` call so that TX2 is a
                single, self-contained transaction.
        """
        self._session_factory = session_factory

    def execute_cascade(
        self,
        entity_type: str,
        entity_local_id: int,
        outbox_id: int,
        deleted_at: datetime,
    ) -> LocalCascadeResult:
        """Delete the root entity's hierarchy and mark the entry completed (TX2).

        Within a single transaction: deletes the root entity (and its dependent
        hierarchy via cascade) and sets the outbox entry's
        ``local_delete_status = 'completed'`` and ``deleted_at``. Commits once on
        success. On any failure, rolls back the whole transaction, leaving the
        hierarchy intact and the entry NOT marked completed.

        Args:
            entity_type: One of ``greenhouse`` | ``module`` | ``monitoring``.
            entity_local_id: Local id of the root entity to delete.
            outbox_id: Local id of the associated outbox entry to mark completed.
            deleted_at: UTC timestamp recorded as the moment of successful local
                deletion (set together with ``local_delete_status = 'completed'``).

        Returns:
            LocalCascadeResult(success=True) when the cascade committed and the
            entry was marked completed; otherwise
            LocalCascadeResult(success=False, error_message=...).
        """
        model_cls = _ENTITY_MODELS.get(entity_type)
        if model_cls is None:
            return LocalCascadeResult(
                success=False,
                error_message=(
                    f"Unsupported entity_type '{entity_type}'; expected one of "
                    "greenhouse | module | monitoring."
                ),
            )

        session: Optional[Session] = None
        try:
            session = self._session_factory()

            # Load the associated outbox entry that must be marked completed.
            outbox = session.get(DeletionOutboxModel, outbox_id)
            if outbox is None:
                session.rollback()
                return LocalCascadeResult(
                    success=False,
                    error_message=f"Deletion outbox entry id={outbox_id} not found.",
                )

            # Load the root entity to delete.
            root = session.get(model_cls, entity_local_id)
            if root is None:
                session.rollback()
                return LocalCascadeResult(
                    success=False,
                    error_message=(
                        f"{entity_type} with id={entity_local_id} not found."
                    ),
                )

            # Delete the root; ORM/DB cascade removes the dependent hierarchy.
            session.delete(root)

            # Mark the outbox entry completed within the SAME transaction (TX2).
            outbox.local_delete_status = "completed"
            outbox.deleted_at = deleted_at

            session.commit()
            return LocalCascadeResult(success=True)
        except Exception as exc:  # noqa: BLE001 - report failure, rollback whole TX2
            if session is not None:
                try:
                    session.rollback()
                except Exception:
                    # Rollback best-effort; the original failure is reported below.
                    pass
            return LocalCascadeResult(
                success=False,
                error_message=(
                    f"Local cascade failed for {entity_type} id={entity_local_id}: "
                    f"{exc}"
                ),
            )
        finally:
            if session is not None:
                session.close()
