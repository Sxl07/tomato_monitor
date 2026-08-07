"""Unit tests for thermal fields in GET /monitoring/{id}/status — Task 12B.

Tests verify:
- Thermal fields returned when analysis progress has thermal data
- pause_reason set when thermal_paused is True
- temperature from current or peak fallback
- Defaults when no thermal data available
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.monitoring import router
from app.dependencies import get_monitoring_service
from src.application.services.monitoring_service import (
    MonitoringNotFoundError,
    MonitoringService,
)


def _make_monitoring(
    id: int = 1,
    module_id: int = 10,
    status: str = "analyzing",
    total_snapshots: int = 5,
    total_detections: int = 0,
    width_m: float = 5.0,
    length_m: float = 2.0,
):
    return SimpleNamespace(
        id=id,
        module_id=module_id,
        status=status,
        started_at=datetime(2025, 1, 1, 12, 0, 0),
        completed_at=None,
        total_snapshots=total_snapshots,
        total_detections=total_detections,
        width_m=width_m,
        length_m=length_m,
        notes=None,
    )


@pytest.fixture
def mock_service():
    return MagicMock(spec=MonitoringService)


@pytest.fixture
def app(mock_service):
    from app.dependencies import require_current_user_api

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_monitoring_service] = lambda: mock_service
    application.dependency_overrides[require_current_user_api] = lambda: SimpleNamespace(
        id=1, full_name="Test", email="test@test.com", role="operator"
    )
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


class TestThermalFieldsReturned:
    """Thermal fields returned from status endpoint."""

    def test_thermal_paused_true_sets_pause_reason(self, app, client, mock_service):
        mock_service.get_status.return_value = _make_monitoring(status="analyzing")

        mock_registry = MagicMock()
        mock_runtime = MagicMock()
        mock_runtime.progress = SimpleNamespace(
            processed_snapshots=3,
            total_snapshots=10,
            thermal_paused=True,
            thermal_current_temperature_c=78.5,
            thermal_peak_temperature_c=80.0,
            thermal_pause_count=2,
            thermal_pause_duration_seconds=15.0,
        )
        mock_registry.get_worker.return_value = mock_runtime
        app.state.monitoring_runtime_registry = mock_registry

        response = client.get("/monitoring/1/status")

        assert response.status_code == 200
        data = response.json()
        assert data["analysis_thermal_paused"] is True
        assert data["pause_reason"] == "Pausado por temperatura. Esperando que la Raspberry Pi se enfríe para continuar el análisis."
        assert data["temperature"] == 78.5
        assert data["analysis_peak_temperature_c"] == 80.0
        assert data["analysis_thermal_pause_count"] == 2
        assert data["analysis_thermal_pause_duration_seconds"] == 15.0

    def test_pause_reason_exact_text(self, app, client, mock_service):
        """Verify pause_reason matches exact expected text."""
        mock_service.get_status.return_value = _make_monitoring(status="analyzing")

        mock_registry = MagicMock()
        mock_runtime = MagicMock()
        mock_runtime.progress = SimpleNamespace(
            processed_snapshots=1,
            total_snapshots=5,
            thermal_paused=True,
            thermal_current_temperature_c=82.0,
            thermal_peak_temperature_c=82.0,
            thermal_pause_count=1,
            thermal_pause_duration_seconds=5.0,
        )
        mock_registry.get_worker.return_value = mock_runtime
        app.state.monitoring_runtime_registry = mock_registry

        response = client.get("/monitoring/1/status")
        data = response.json()

        expected = "Pausado por temperatura. Esperando que la Raspberry Pi se enfríe para continuar el análisis."
        assert data["pause_reason"] == expected

    def test_thermal_not_paused_no_pause_reason(self, app, client, mock_service):
        mock_service.get_status.return_value = _make_monitoring(status="analyzing")

        mock_registry = MagicMock()
        mock_runtime = MagicMock()
        mock_runtime.progress = SimpleNamespace(
            processed_snapshots=5,
            total_snapshots=10,
            thermal_paused=False,
            thermal_current_temperature_c=55.0,
            thermal_peak_temperature_c=60.0,
            thermal_pause_count=0,
            thermal_pause_duration_seconds=0.0,
        )
        mock_registry.get_worker.return_value = mock_runtime
        app.state.monitoring_runtime_registry = mock_registry

        response = client.get("/monitoring/1/status")

        data = response.json()
        assert data["analysis_thermal_paused"] is False
        assert data["pause_reason"] is None
        assert data["temperature"] == 55.0

    def test_temperature_fallback_to_peak(self, app, client, mock_service):
        """When current_temperature is None, use peak as fallback."""
        mock_service.get_status.return_value = _make_monitoring(status="analyzing")

        mock_registry = MagicMock()
        mock_runtime = MagicMock()
        mock_runtime.progress = SimpleNamespace(
            processed_snapshots=5,
            total_snapshots=10,
            thermal_paused=False,
            thermal_current_temperature_c=None,
            thermal_peak_temperature_c=72.0,
            thermal_pause_count=1,
            thermal_pause_duration_seconds=3.0,
        )
        mock_registry.get_worker.return_value = mock_runtime
        app.state.monitoring_runtime_registry = mock_registry

        response = client.get("/monitoring/1/status")

        data = response.json()
        assert data["temperature"] == 72.0

    def test_temperature_none_when_no_data(self, app, client, mock_service):
        """When no thermal data available, temperature is None."""
        mock_service.get_status.return_value = _make_monitoring(status="running")

        if hasattr(app.state, "monitoring_runtime_registry"):
            delattr(app.state, "monitoring_runtime_registry")

        response = client.get("/monitoring/1/status")

        data = response.json()
        assert data["temperature"] is None
        assert data["analysis_thermal_paused"] is False
        assert data["analysis_peak_temperature_c"] == 0.0
        assert data["analysis_thermal_pause_count"] == 0
        assert data["analysis_thermal_pause_duration_seconds"] == 0.0

    def test_negative_thermal_values_clamped(self, app, client, mock_service):
        """Negative thermal values are clamped to defaults."""
        mock_service.get_status.return_value = _make_monitoring(status="analyzing")

        mock_registry = MagicMock()
        mock_runtime = MagicMock()
        mock_runtime.progress = SimpleNamespace(
            processed_snapshots=3,
            total_snapshots=10,
            thermal_paused=False,
            thermal_current_temperature_c=-5.0,
            thermal_peak_temperature_c=-10.0,
            thermal_pause_count=-1,
            thermal_pause_duration_seconds=-2.5,
        )
        mock_registry.get_worker.return_value = mock_runtime
        app.state.monitoring_runtime_registry = mock_registry

        response = client.get("/monitoring/1/status")
        data = response.json()

        assert data["temperature"] is None  # _safe_temperature returns None for negative
        assert data["analysis_peak_temperature_c"] == 0.0
        assert data["analysis_thermal_pause_count"] == 0
        assert data["analysis_thermal_pause_duration_seconds"] == 0.0

    def test_non_numeric_values_produce_defaults(self, app, client, mock_service):
        """Non-numeric values in progress fields produce defaults."""
        mock_service.get_status.return_value = _make_monitoring(status="analyzing")

        mock_registry = MagicMock()
        mock_runtime = MagicMock()
        mock_runtime.progress = SimpleNamespace(
            processed_snapshots="abc",
            total_snapshots="xyz",
            thermal_paused=False,
            thermal_current_temperature_c="not_a_number",
            thermal_peak_temperature_c="broken",
            thermal_pause_count="invalid",
            thermal_pause_duration_seconds="nope",
        )
        mock_registry.get_worker.return_value = mock_runtime
        app.state.monitoring_runtime_registry = mock_registry

        response = client.get("/monitoring/1/status")
        data = response.json()

        assert data["analysis_processed"] == 0
        assert data["analysis_total"] == 0
        assert data["temperature"] is None
        assert data["analysis_peak_temperature_c"] == 0.0
        assert data["analysis_thermal_pause_count"] == 0
        assert data["analysis_thermal_pause_duration_seconds"] == 0.0


class TestThermalFieldDefaults:
    """Default values when progress has no thermal attributes."""

    def test_progress_without_thermal_attrs(self, app, client, mock_service):
        """Progress object without thermal attributes → defaults."""
        mock_service.get_status.return_value = _make_monitoring(status="analyzing")

        mock_registry = MagicMock()
        mock_runtime = MagicMock()
        # Old-style progress without thermal fields
        mock_runtime.progress = SimpleNamespace(
            processed_snapshots=2,
            total_snapshots=8,
        )
        mock_registry.get_worker.return_value = mock_runtime
        app.state.monitoring_runtime_registry = mock_registry

        response = client.get("/monitoring/1/status")

        data = response.json()
        assert data["analysis_processed"] == 2
        assert data["analysis_total"] == 8
        assert data["analysis_thermal_paused"] is False
        assert data["temperature"] is None
        assert data["pause_reason"] is None

    def test_registry_failure_resets_all_thermal_to_defaults(self, app, client, mock_service):
        """When registry read fails completely, all thermal fields reset to defaults."""
        mock_service.get_status.return_value = _make_monitoring(status="analyzing")

        mock_registry = MagicMock()
        mock_registry.get_worker.side_effect = RuntimeError("registry broken")
        app.state.monitoring_runtime_registry = mock_registry

        response = client.get("/monitoring/1/status")
        data = response.json()

        assert data["analysis_processed"] == 0
        assert data["analysis_total"] == 0
        assert data["analysis_thermal_paused"] is False
        assert data["temperature"] is None
        assert data["pause_reason"] is None
        assert data["analysis_peak_temperature_c"] == 0.0
        assert data["analysis_thermal_pause_count"] == 0
        assert data["analysis_thermal_pause_duration_seconds"] == 0.0
