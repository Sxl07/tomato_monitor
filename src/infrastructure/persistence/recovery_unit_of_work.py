"""SqlRecoveryUnitOfWork: SQLAlchemy-backed recovery unit-of-work (Spec 022, D2.1).

Bounds a single SQLite Session around the processing of one recovered row. On
enter it creates a fresh Session and builds the eight SQL repositories over it;
on exit it performs a defensive rollback and always closes the Session.

The rollback on exit only clears a lingering read transaction or a failed
session state — it never reverts the durable commits that the recovery insert
primitives already performed.
"""

from __future__ import annotations

from typing import Callable

from sqlalchemy.orm import Session

from src.infrastructure.persistence.repositories.sql_activity_log_repository import (
    SqlActivityLogRepository,
)
from src.infrastructure.persistence.repositories.sql_activity_type_repository import (
    SqlActivityTypeRepository,
)
from src.infrastructure.persistence.repositories.sql_greenhouse_repository import (
    SqlGreenhouseRepository,
)
from src.infrastructure.persistence.repositories.sql_inspection_result_repository import (
    SqlInspectionResultRepository,
)
from src.infrastructure.persistence.repositories.sql_module_repository import (
    SqlModuleRepository,
)
from src.infrastructure.persistence.repositories.sql_monitoring_metrics_repository import (
    SqlMonitoringMetricsRepository,
)
from src.infrastructure.persistence.repositories.sql_monitoring_repository import (
    SqlMonitoringRepository,
)
from src.infrastructure.persistence.repositories.sql_snapshot_repository import (
    SqlSnapshotRepository,
)


class SqlRecoveryUnitOfWork:
    """A per-row unit-of-work over a single SQLAlchemy Session.

    Use as a context manager:

        with SqlRecoveryUnitOfWork(session_factory) as uow:
            uow.greenhouse_repo.insert_preserving_remote_id(...)
    """

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        """Initialize with a callable producing new Sessions.

        Args:
            session_factory: Callable returning a fresh Session (compatible with
                DatabaseManager.get_session).
        """
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlRecoveryUnitOfWork":
        self._session = self._session_factory()
        self.greenhouse_repo = SqlGreenhouseRepository(self._session)
        self.module_repo = SqlModuleRepository(self._session)
        self.monitoring_repo = SqlMonitoringRepository(self._session)
        self.monitoring_metrics_repo = SqlMonitoringMetricsRepository(self._session)
        self.snapshot_repo = SqlSnapshotRepository(self._session)
        self.inspection_result_repo = SqlInspectionResultRepository(self._session)
        self.activity_log_repo = SqlActivityLogRepository(self._session)
        self.activity_type_repo = SqlActivityTypeRepository(self._session)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # Defensive rollback: release any open read transaction and recover a
        # session left in a failed state. Committed inserts are NOT reverted.
        # Never auto-commit here. Never suppress exceptions (return False).
        if self._session is not None:
            try:
                self._session.rollback()
            except Exception:
                pass
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
        return False
