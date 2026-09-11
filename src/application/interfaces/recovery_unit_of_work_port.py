"""Port: RecoveryUnitOfWork (Spec 022, block D2.1).

Defines a LOCAL unit-of-work used exclusively by the RecoveryService to bound a
single SQLite session/transaction around the processing of ONE recovered row.

Rationale:
    - No SQLite session/transaction may stay open around remote/HTTP requests.
    - A failed row must not contaminate the session used by the next row, so a
      fresh session is created per row and always closed on exit.

The unit-of-work is a context manager exposing the eight repositories bound to a
single session. Concrete implementations live in the infrastructure layer; this
port keeps the application layer free of SQLAlchemy.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.repositories.activity_log_repository import ActivityLogRepository
from src.domain.repositories.activity_type_repository import ActivityTypeRepository
from src.domain.repositories.greenhouse_repository import GreenhouseRepository
from src.domain.repositories.inspection_result_repository import (
    InspectionResultRepository,
)
from src.domain.repositories.module_repository import ModuleRepository
from src.domain.repositories.monitoring_metrics_repository import (
    MonitoringMetricsRepository,
)
from src.domain.repositories.monitoring_repository import MonitoringRepository
from src.domain.repositories.snapshot_repository import SnapshotRepository


@runtime_checkable
class RecoveryUnitOfWork(Protocol):
    """A context-managed set of repositories bound to a single local session.

    Contract:
        - Entering the context creates ONE new session and builds the eight
          repositories over it.
        - Exiting the context performs a defensive rollback (to release any read
          transaction or recover a failed session) and ALWAYS closes the
          session. It does NOT commit — the recovery insert primitives commit
          durably themselves, and those commits are never reverted on exit.
    """

    greenhouse_repo: GreenhouseRepository
    module_repo: ModuleRepository
    monitoring_repo: MonitoringRepository
    monitoring_metrics_repo: MonitoringMetricsRepository
    snapshot_repo: SnapshotRepository
    inspection_result_repo: InspectionResultRepository
    activity_log_repo: ActivityLogRepository
    activity_type_repo: ActivityTypeRepository

    def __enter__(self) -> "RecoveryUnitOfWork":
        ...

    def __exit__(self, exc_type, exc, tb) -> bool:
        ...


@runtime_checkable
class RecoveryUnitOfWorkFactory(Protocol):
    """Callable that produces a fresh RecoveryUnitOfWork per invocation."""

    def __call__(self) -> RecoveryUnitOfWork:
        ...
