"""Verify that all persistence layer modules import cleanly."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from src.infrastructure.persistence.database import DatabaseManager
    from src.infrastructure.persistence.repositories import (
        SqlGreenhouseRepository,
        SqlModuleRepository,
        SqlMonitoringRepository,
        SqlSnapshotRepository,
        SqlInspectionResultRepository,
        SqlMonitoringMetricsRepository,
    )
    from src.infrastructure.persistence.models import (
        Base,
        GreenhouseModel,
        ModuleModel,
        MonitoringModel,
        SnapshotModel,
        InspectionResultModel,
        MonitoringMetricsModel,
    )
    print("Persistence layer imports: ALL OK")
    print(f"  DatabaseManager: {DatabaseManager}")
    print(f"  Models: 6 ORM models + Base")
    print(f"  Repositories: 6 concrete implementations")
except ImportError as e:
    print(f"IMPORT ERROR: {e}", file=sys.stderr)
    sys.exit(1)
