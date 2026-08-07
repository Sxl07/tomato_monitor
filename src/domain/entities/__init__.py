"""Domain entities package.

Exports both the legacy vision pipeline entities and the new
agricultural data-model entities.
"""

from src.domain.entities.fruit_detection import FruitDetection
from src.domain.entities.health_assessment import HealthAssessment
from src.domain.entities.inspection_result import (
    DetectionInspectionResult,
    InspectionResult,
)
from src.domain.entities.inspection_session import InspectionSession
from src.domain.entities.maturity_assessment import MaturityAssessment

# Agricultural data-model entities
from src.domain.entities.greenhouse import Greenhouse
from src.domain.entities.module import Module
from src.domain.entities.monitoring import Monitoring
from src.domain.entities.monitoring_metrics import MonitoringMetrics
from src.domain.entities.snapshot import Snapshot

# Traceability and operations entities (Spec 015)
from src.domain.entities.user import User
from src.domain.entities.activity_type import ActivityType
from src.domain.entities.activity_log import ActivityLog
from src.domain.entities.export_package import ExportPackage

__all__ = [
    # Legacy vision pipeline entities
    "FruitDetection",
    "HealthAssessment",
    "InspectionResult",
    "InspectionSession",
    "MaturityAssessment",
    # Agricultural data-model entities
    "DetectionInspectionResult",
    "Greenhouse",
    "Module",
    "Monitoring",
    "MonitoringMetrics",
    "Snapshot",
    # Traceability and operations entities
    "User",
    "ActivityType",
    "ActivityLog",
    "ExportPackage",
]
