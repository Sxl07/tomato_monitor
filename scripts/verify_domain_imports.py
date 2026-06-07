"""Verify that all domain layer modules import cleanly."""
import sys
import os

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from src.domain.entities import (
        Greenhouse, Module, Monitoring, Snapshot,
        DetectionInspectionResult, MonitoringMetrics,
    )
    from src.domain.value_objects import BoundingBox, MonitoringState, MonitoringStatus
    from src.domain.repositories import (
        GreenhouseRepository, ModuleRepository, MonitoringRepository,
        SnapshotRepository, InspectionResultRepository, MonitoringMetricsRepository,
    )
    from src.domain.exceptions import (
        DomainError, InvalidTransitionError, DuplicateModuleError,
        ParentNotFoundError, InvalidImagePathError, MetricsNotAllowedError,
    )
    print("Domain layer imports: ALL OK")
    print(f"  Entities: Greenhouse, Module, Monitoring, Snapshot, DetectionInspectionResult, MonitoringMetrics")
    print(f"  Value Objects: BoundingBox, MonitoringState, MonitoringStatus")
    print(f"  Repositories: 6 ABCs")
    print(f"  Exceptions: 6 exception classes")
except ImportError as e:
    print(f"IMPORT ERROR: {e}", file=sys.stderr)
    sys.exit(1)
