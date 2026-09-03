"""Pydantic DTOs for monitoring session API endpoints."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class StartMonitoringRequest(BaseModel):
    """Request body for starting a new monitoring session."""

    module_id: int
    width_m: float = Field(gt=0, le=1000)
    length_m: float = Field(gt=0, le=1000)
    notes: Optional[str] = Field(default=None, max_length=500)


class MonitoringResponse(BaseModel):
    """Response body for monitoring session operations."""

    id: int
    module_id: int
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_snapshots: int = 0
    total_detections: int = 0

    model_config = {"from_attributes": True}


class MonitoringStatusResponse(MonitoringResponse):
    """Extended response including session configuration details."""

    # Nullable: an INITIALIZING or startup-reconciled monitoring may not have
    # confirmed dimensions yet. Keeping these Optional mirrors what
    # MonitoringService/the ORM can actually carry and prevents the /status
    # endpoint from 500-ing on a partially-initialized row.
    width_m: Optional[float] = None
    length_m: Optional[float] = None
    notes: Optional[str] = None
    analysis_processed: int = 0
    analysis_total: int = 0
    temperature: Optional[float] = None
    pause_reason: Optional[str] = None
    analysis_thermal_paused: bool = False
    analysis_peak_temperature_c: float = 0.0
    analysis_thermal_pause_count: int = 0
    analysis_thermal_pause_duration_seconds: float = 0.0


class ErrorResponse(BaseModel):
    """Error response for state machine conflicts and validation errors."""

    detail: str
    current_status: Optional[str] = None
