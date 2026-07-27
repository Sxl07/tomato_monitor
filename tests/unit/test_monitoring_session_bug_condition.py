"""Bug condition exploration test for monitoring session start/abort routes.

**Validates: Requirements 1.1, 1.3, 1.4, 1.5**

Bug Condition: The UI routes POST /modulos/{id}/monitoreo/iniciar and
POST /monitoreos/{id}/abortar bypass MonitoringService, calling repositories
directly. This means no background worker is spawned on start, and no worker
signal/partial metrics computation occurs on abort.

Expected Behavior (what the fix should achieve):
- monitoring_start delegates to MonitoringService.start_session()
- monitoring_abort delegates to MonitoringService.abort_session()

After fix: Routes call MonitoringService methods which handle worker lifecycle,
invariant enforcement, and metrics computation.

Testing framework: pytest + hypothesis (property-based tests)
"""

import sys
from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st


# --- Patch heavy dependencies BEFORE importing app modules ---
# torch, detectron2, cv2 are not needed for route-level testing.
# numpy is kept real because Hypothesis uses it internally.

_HEAVY_MOCKS = {}
for _mod_name in [
    "torch", "torch.nn", "torch.nn.functional", "torch.utils",
    "torch.utils.data", "torch.cuda",
    "torchvision", "torchvision.transforms", "torchvision.models",
    "cv2",
    "detectron2", "detectron2.config", "detectron2.engine",
    "detectron2.model_zoo", "detectron2.modeling",
    "PIL", "PIL.Image",
    "picamera2",
]:
    if _mod_name not in sys.modules:
        _HEAVY_MOCKS[_mod_name] = MagicMock()

# Apply patches at module level so imports of app code work
for _k, _v in _HEAVY_MOCKS.items():
    sys.modules.setdefault(_k, _v)

# Now we can safely import app modules
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.routes.agricultural_ui import router


# --- Strategies for generating valid dimensions ---

@st.composite
def valid_dimension_pairs(draw):
    """Generate random valid positive dimension pairs (width, length).

    Valid dimensions are positive floats that pass the application's
    validate_dimensions() function.
    """
    width = draw(st.floats(min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False))
    length = draw(st.floats(min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False))
    return (width, length)


# --- Test app fixture ---

@pytest.fixture
def test_client():
    """Create a FastAPI TestClient with the agricultural router."""
    test_app = FastAPI()
    test_app.include_router(router)

    # Set up minimal app state
    mock_db_manager = MagicMock()
    mock_db_manager.get_session.return_value = MagicMock()
    test_app.state.db_manager = mock_db_manager
    test_app.state.log_service = MagicMock()
    test_app.state.camera_service = MagicMock()

    client = TestClient(test_app, raise_server_exceptions=False)
    return client


class TestMonitoringStartBugCondition:
    """Property-based tests confirming the start route delegates to MonitoringService.

    These tests encode the EXPECTED behavior: when a valid start form is submitted,
    MonitoringService.start_session() is called with proper dependencies.

    **Validates: Requirements 1.1, 1.4, 1.5**
    """

    @given(dims=valid_dimension_pairs())
    @settings(
        max_examples=15,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_start_route_calls_monitoring_service_start_session(self, dims, test_client):
        """Property: For any valid dimension pair (width, length) submitted to
        POST /modulos/{id}/monitoreo/iniciar where the module exists,
        MonitoringService.start_session() SHOULD be called.

        **Validates: Requirements 1.1, 1.4, 1.5**

        Bug: Original code called monitoring_repo.create() directly,
        never invoking MonitoringService.start_session().
        Fix: Route now delegates to MonitoringService.start_session().
        """
        width, length = dims

        # Mock the module repository to return a valid module
        mock_module = MagicMock()
        mock_module.id = 1
        mock_module.name = "Módulo Test"

        # Mock the monitoring service
        mock_monitoring_service = MagicMock()
        mock_monitoring = MagicMock()
        mock_monitoring.id = 42
        mock_monitoring_service.start_session.return_value = mock_monitoring

        # Mock frame source factory
        mock_frame_source = MagicMock()

        with patch("app.routes.agricultural_ui.get_module_repository") as mock_get_module_repo, \
             patch("app.routes.agricultural_ui.get_monitoring_service") as mock_get_monitoring_svc, \
             patch("src.application.services.frame_source_factory.create_frame_source") as mock_create_fs, \
             patch("app.routes.agricultural_ui.ModelService") as mock_model_svc_cls, \
             patch("app.dependencies.get_log_service") as mock_get_log_svc, \
             patch("app.routes.agricultural_ui.DETECTION_MODEL_PATH", Path("/fake/model.pth")):

            # Configure mocks
            mock_repo = MagicMock()
            mock_repo.get_by_id.return_value = mock_module
            mock_get_module_repo.return_value = mock_repo

            mock_get_monitoring_svc.return_value = mock_monitoring_service
            mock_create_fs.return_value = mock_frame_source

            mock_model_svc_instance = MagicMock()
            mock_model_svc_instance.check_availability.return_value = MagicMock(value="available")
            mock_model_svc_cls.return_value = mock_model_svc_instance

            mock_get_log_svc.return_value = MagicMock()

            response = test_client.post(
                "/modulos/1/monitoreo/iniciar",
                data={
                    "width_m": str(width),
                    "length_m": str(length),
                    "notes": "",
                },
            )

            # EXPECTED BEHAVIOR: MonitoringService.start_session() was called
            assert mock_monitoring_service.start_session.called, (
                f"MonitoringService.start_session() was NOT called when posting "
                f"valid dimensions width={width}, length={length}. "
                f"This means the route bypasses MonitoringService and calls "
                f"repositories directly (bug condition confirmed). "
                f"Response status: {response.status_code}"
            )


class TestMonitoringAbortBugCondition:
    """Tests confirming the abort route delegates to MonitoringService.

    **Validates: Requirements 1.3**
    """

    def test_abort_route_calls_monitoring_service_abort_session(self, test_client):
        """Property: For a POST /monitoreos/{id}/abortar where the monitoring exists,
        MonitoringService.abort_session() SHOULD be called.

        **Validates: Requirements 1.3**

        Bug: Original code called monitoring_repo.update_status(id, "aborted") directly,
        never invoking MonitoringService.abort_session().
        Fix: Route now delegates to MonitoringService.abort_session().
        """
        # Mock the monitoring service
        mock_monitoring_service = MagicMock()
        mock_monitoring = MagicMock()
        mock_monitoring.id = 5
        mock_monitoring.module_id = 1
        mock_monitoring_service.abort_session.return_value = mock_monitoring

        with patch("app.routes.agricultural_ui.get_monitoring_service") as mock_get_svc:
            mock_get_svc.return_value = mock_monitoring_service

            response = test_client.post("/monitoreos/5/abortar")

            # EXPECTED BEHAVIOR: MonitoringService.abort_session() was called
            assert mock_monitoring_service.abort_session.called, (
                f"MonitoringService.abort_session() was NOT called when posting "
                f"abort for monitoring id=5. "
                f"This means the route bypasses MonitoringService and calls "
                f"monitoring_repo.update_status() directly (bug condition confirmed). "
                f"Response status: {response.status_code}"
            )

    @given(monitoring_id=st.integers(min_value=1, max_value=1000))
    @settings(
        max_examples=10,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_abort_route_calls_service_for_any_valid_id(self, monitoring_id, test_client):
        """Property: For any valid monitoring ID, the abort route delegates
        to MonitoringService.abort_session() with that ID.

        **Validates: Requirements 1.3**
        """
        mock_monitoring_service = MagicMock()
        mock_monitoring = MagicMock()
        mock_monitoring.id = monitoring_id
        mock_monitoring.module_id = 1
        mock_monitoring_service.abort_session.return_value = mock_monitoring

        with patch("app.routes.agricultural_ui.get_monitoring_service") as mock_get_svc:
            mock_get_svc.return_value = mock_monitoring_service

            response = test_client.post(f"/monitoreos/{monitoring_id}/abortar")

            # EXPECTED: abort_session called with the monitoring ID
            assert mock_monitoring_service.abort_session.called, (
                f"MonitoringService.abort_session() was NOT called for "
                f"monitoring_id={monitoring_id}. Route bypasses service."
            )
            call_args = mock_monitoring_service.abort_session.call_args
            called_id = call_args[0][0] if call_args[0] else call_args[1].get("id")
            assert called_id == monitoring_id, (
                f"abort_session called with wrong ID. "
                f"Expected: {monitoring_id}, Got: {called_id}"
            )
