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

    width_m: float
    length_m: float
    notes: Optional[str] = None
    analysis_processed: int = 0
    analysis_total: int = 0


class ErrorResponse(BaseModel):
    """Error response for state machine conflicts and validation errors."""

    detail: str
    current_status: Optional[str] = None
