"""Tests for analysis progress in GET /monitoring/{id}/status.

Validates:
- analysis_processed and analysis_total returned from in-memory registry
- Graceful degradation when registry/runtime/progress is absent or broken
- MonitoringNotFoundError still returns 404
- Existing fields preserved
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock

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
    """Build a fake monitoring object compatible with MonitoringStatusResponse."""
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
    """FastAPI app with monitoring router and overridden service dependency."""
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


# ---------------------------------------------------------------------------
# 1. Analysis runtime with progress
# ---------------------------------------------------------------------------


class TestAnalysisProgress:
    """Progress read from runtime registry during analyzing."""

    def test_analysis_progress_returned(self, app, client, mock_service):
        mock_service.get_status.return_value = _make_monitoring(status="analyzing")

        mock_registry = MagicMock()
        mock_runtime = MagicMock()
        mock_runtime.progress = SimpleNamespace(
            processed_snapshots=3, total_snapshots=10
        )
        mock_registry.get_worker.return_value = mock_runtime
        app.state.monitoring_runtime_registry = mock_registry

        response = client.get("/monitoring/1/status")

        assert response.status_code == 200
        data = response.json()
        assert data["analysis_processed"] == 3
        assert data["analysis_total"] == 10

    def test_registry_get_worker_called_with_monitoring_id(self, app, client, mock_service):
        mock_service.get_status.return_value = _make_monitoring()

        mock_registry = MagicMock()
        mock_registry.get_worker.return_value = None
        app.state.monitoring_runtime_registry = mock_registry

        client.get("/monitoring/42/status")

        mock_registry.get_worker.assert_called_once_with(42)


# ---------------------------------------------------------------------------
# 2. Runtime is None (worker=None registered)
# ---------------------------------------------------------------------------


class TestRuntimeNone:
    """get_worker returns None → progress 0/0."""

    def test_none_runtime_returns_zero(self, app, client, mock_service):
        mock_service.get_status.return_value = _make_monitoring(status="analyzing")

        mock_registry = MagicMock()
        mock_registry.get_worker.return_value = None
        app.state.monitoring_runtime_registry = mock_registry

        response = client.get("/monitoring/1/status")

        assert response.status_code == 200
        data = response.json()
        assert data["analysis_processed"] == 0
        assert data["analysis_total"] == 0


# ---------------------------------------------------------------------------
# 3. Capture runtime without progress attribute
# ---------------------------------------------------------------------------


class TestCaptureRuntimeNoProgress:
    """CaptureWorker doesn't have progress → 0/0."""

    def test_no_progress_attribute_returns_zero(self, app, client, mock_service):
        mock_service.get_status.return_value = _make_monitoring(status="running")

        mock_registry = MagicMock()
        # Object without progress attribute
        mock_runtime = object()
        mock_registry.get_worker.return_value = mock_runtime
        app.state.monitoring_runtime_registry = mock_registry

        response = client.get("/monitoring/1/status")

        assert response.status_code == 200
        data = response.json()
        assert data["analysis_processed"] == 0
        assert data["analysis_total"] == 0


# ---------------------------------------------------------------------------
# 4. Registry absent from app.state
# ---------------------------------------------------------------------------


class TestRegistryAbsent:
    """No monitoring_runtime_registry on app.state → 0/0."""

    def test_no_registry_returns_zero(self, app, client, mock_service):
        mock_service.get_status.return_value = _make_monitoring(status="running")

        # Ensure no registry exists
        if hasattr(app.state, "monitoring_runtime_registry"):
            delattr(app.state, "monitoring_runtime_registry")

        response = client.get("/monitoring/1/status")

        assert response.status_code == 200
        data = response.json()
        assert data["analysis_processed"] == 0
        assert data["analysis_total"] == 0


# ---------------------------------------------------------------------------
# 5. Values are None
# ---------------------------------------------------------------------------


class TestNoneValues:
    """processed_snapshots=None, total_snapshots=None → 0/0."""

    def test_none_progress_values_return_zero(self, app, client, mock_service):
        mock_service.get_status.return_value = _make_monitoring(status="analyzing")

        mock_registry = MagicMock()
        mock_runtime = MagicMock()
        mock_runtime.progress = SimpleNamespace(
            processed_snapshots=None, total_snapshots=None
        )
        mock_registry.get_worker.return_value = mock_runtime
        app.state.monitoring_runtime_registry = mock_registry

        response = client.get("/monitoring/1/status")

        assert response.status_code == 200
        data = response.json()
        assert data["analysis_processed"] == 0
        assert data["analysis_total"] == 0


# ---------------------------------------------------------------------------
# 6. Negative values clamped to 0
# ---------------------------------------------------------------------------


class TestNegativeValues:
    """Negative progress values clamped to 0."""

    def test_negative_values_clamped(self, app, client, mock_service):
        mock_service.get_status.return_value = _make_monitoring(status="analyzing")

        mock_registry = MagicMock()
        mock_runtime = MagicMock()
        mock_runtime.progress = SimpleNamespace(
            processed_snapshots=-5, total_snapshots=-1
        )
        mock_registry.get_worker.return_value = mock_runtime
        app.state.monitoring_runtime_registry = mock_registry

        response = client.get("/monitoring/1/status")

        assert response.status_code == 200
        data = response.json()
        assert data["analysis_processed"] == 0
        assert data["analysis_total"] == 0


# ---------------------------------------------------------------------------
# 7. Registry.get_worker raises exception
# ---------------------------------------------------------------------------


class TestRegistryFailure:
    """Exception from registry doesn't propagate — returns 0/0."""

    def test_get_worker_exception_returns_zero(self, app, client, mock_service):
        mock_service.get_status.return_value = _make_monitoring(status="analyzing")

        mock_registry = MagicMock()
        mock_registry.get_worker.side_effect = RuntimeError("Registry corrupted")
        app.state.monitoring_runtime_registry = mock_registry

        response = client.get("/monitoring/1/status")

        assert response.status_code == 200
        data = response.json()
        assert data["analysis_processed"] == 0
        assert data["analysis_total"] == 0


# ---------------------------------------------------------------------------
# 8. MonitoringNotFoundError → 404
# ---------------------------------------------------------------------------


class TestNotFound:
    """MonitoringNotFoundError returns 404 without consulting registry."""

    def test_not_found_returns_404(self, app, client, mock_service):
        mock_service.get_status.side_effect = MonitoringNotFoundError(99)

        mock_registry = MagicMock()
        app.state.monitoring_runtime_registry = mock_registry

        response = client.get("/monitoring/99/status")

        assert response.status_code == 404
        # Registry never consulted
        mock_registry.get_worker.assert_not_called()


# ---------------------------------------------------------------------------
# 9. Backward compatibility — existing fields preserved
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Existing fields like status, total_snapshots, total_detections preserved."""

    def test_existing_fields_preserved(self, app, client, mock_service):
        mock_service.get_status.return_value = _make_monitoring(
            id=7,
            module_id=10,
            status="running",
            total_snapshots=15,
            total_detections=3,
            width_m=4.5,
            length_m=2.5,
        )

        if hasattr(app.state, "monitoring_runtime_registry"):
            delattr(app.state, "monitoring_runtime_registry")

        response = client.get("/monitoring/7/status")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 7
        assert data["module_id"] == 10
        assert data["status"] == "running"
        assert data["total_snapshots"] == 15
        assert data["total_detections"] == 3
        assert data["width_m"] == 4.5
        assert data["length_m"] == 2.5
        assert data["analysis_processed"] == 0
        assert data["analysis_total"] == 0

    def test_thermal_fields_present_with_defaults(self, app, client, mock_service):
        """New thermal fields present in response with default values."""
        mock_service.get_status.return_value = _make_monitoring(status="running")

        if hasattr(app.state, "monitoring_runtime_registry"):
            delattr(app.state, "monitoring_runtime_registry")

        response = client.get("/monitoring/1/status")

        assert response.status_code == 200
        data = response.json()
        assert "temperature" in data
        assert "pause_reason" in data
        assert "analysis_thermal_paused" in data
        assert "analysis_peak_temperature_c" in data
        assert "analysis_thermal_pause_count" in data
        assert "analysis_thermal_pause_duration_seconds" in data
        # Defaults
        assert data["temperature"] is None
        assert data["pause_reason"] is None
        assert data["analysis_thermal_paused"] is False
        assert data["analysis_peak_temperature_c"] == 0.0
        assert data["analysis_thermal_pause_count"] == 0
        assert data["analysis_thermal_pause_duration_seconds"] == 0.0


# ---------------------------------------------------------------------------
# 10. Only get_worker is called, not get_thread or other methods
# ---------------------------------------------------------------------------


class TestOnlyGetWorkerCalled:
    """Endpoint only calls registry.get_worker, not other registry methods."""

    def test_does_not_call_get_thread(self, app, client, mock_service):
        mock_service.get_status.return_value = _make_monitoring()

        mock_registry = MagicMock()
        mock_registry.get_worker.return_value = None
        app.state.monitoring_runtime_registry = mock_registry

        client.get("/monitoring/1/status")

        mock_registry.get_worker.assert_called_once()
        mock_registry.get_thread.assert_not_called()
