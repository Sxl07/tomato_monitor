"""Domain repository interfaces package.

Exports all abstract repository contracts. Implementations reside in
the infrastructure layer (src/infrastructure/persistence/).
"""

# Legacy vision pipeline repositories
from src.domain.repositories.artifact_repository import ArtifactRepository
from src.domain.repositories.inspection_repository import InspectionRepository
from src.domain.repositories.session_repository import SessionRepository

# Agricultural data-model repositories
from src.domain.repositories.greenhouse_repository import GreenhouseRepository
from src.domain.repositories.module_repository import ModuleRepository
from src.domain.repositories.monitoring_repository import MonitoringRepository
from src.domain.repositories.snapshot_repository import SnapshotRepository
from src.domain.repositories.inspection_result_repository import (
    InspectionResultRepository,
)
from src.domain.repositories.monitoring_metrics_repository import (
    MonitoringMetricsRepository,
)

# Traceability and operations repositories (Spec 015)
from src.domain.repositories.user_repository import UserRepository
from src.domain.repositories.activity_type_repository import ActivityTypeRepository
from src.domain.repositories.activity_log_repository import ActivityLogRepository
from src.domain.repositories.export_package_repository import ExportPackageRepository

__all__ = [
    # Legacy
    "ArtifactRepository",
    "InspectionRepository",
    "SessionRepository",
    # Agricultural data-model
    "GreenhouseRepository",
    "ModuleRepository",
    "MonitoringRepository",
    "SnapshotRepository",
    "InspectionResultRepository",
    "MonitoringMetricsRepository",
    # Traceability and operations
    "UserRepository",
    "ActivityTypeRepository",
    "ActivityLogRepository",
    "ExportPackageRepository",
]
