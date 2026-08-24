"""Orphan export reconciliation at application startup.

Detects ExportPackage records stuck in 'generating' status (from an interrupted
process) and transitions them to 'error' so the operator can retry.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.infrastructure.persistence.database import DatabaseManager

logger = logging.getLogger(__name__)


def reconcile_orphan_exports(db_manager: DatabaseManager) -> int:
    """Reconcile orphan exports that are stuck in 'generating' status.

    Called once during application startup, after init_db().

    Args:
        db_manager: Initialized DatabaseManager instance.

    Returns:
        Number of orphan exports transitioned to 'error'.
    """
    from src.infrastructure.persistence.models.export_package_model import (
        ExportPackageModel,
    )

    session = db_manager.get_session()
    try:
        orphans = (
            session.query(ExportPackageModel)
            .filter(ExportPackageModel.status == "generating")
            .all()
        )

        if not orphans:
            return 0

        for orphan in orphans:
            orphan.status = "error"
            orphan.error_message = (
                "Exportación interrumpida antes de completarse. "
                "Puede volver a intentarse."
            )
            orphan.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)

        session.commit()
        logger.info(
            "Reconciled %d orphan export(s) to error status", len(orphans)
        )
        return len(orphans)

    except Exception as e:
        session.rollback()
        logger.warning("Export reconciliation failed: %s", e)
        return 0
    finally:
        session.close()
