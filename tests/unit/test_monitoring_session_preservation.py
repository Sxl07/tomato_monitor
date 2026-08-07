"""Preservation property tests for monitoring session routes — MUST PASS before and after fix.

**Validates: Requirements 3.1, 3.2, 3.5**

These tests encode the PRESERVED behavior of monitoring_start and monitoring_abort
routes for non-bug-condition inputs (inputs that never reach MonitoringService).
They establish a regression baseline that must continue to pass after the fix.

Tested preservation behavior:
- POST start with invalid dimensions (zero, negative, non-numeric) → validation error
  template (status 200 with error messages), MonitoringService never called
- POST start for non-existent module → redirect to /invernaderos?error=Módulo+no+encontrado
- POST abort for non-existent monitoring → redirect to /invernaderos?error=Monitoreo+no+encontrado

Testing framework: pytest + hypothesis (property-based tests)
"""

import sys
from unittest.mock import MagicMock, patch, PropertyMock

# Mock heavy ML dependencies not available in the test environment.
# numpy and opencv are available; torch, detectron2, PIL, picamera2 are not.
_MOCK_MODULES = [
    "torch", "torch.nn", "torch.nn.functional", "torch.utils",
    "torch.utils.data", "torch.cuda", "torch.hub",
    "torchvision", "torchvision.transforms", "torchvision.transforms.functional",
    "torchvision.models", "torchvision.ops",
    "detectron2", "detectron2.config", "detectron2.engine",
    "detectron2.modeling", "detectron2.data", "detectron2.structures",
    "detectron2.utils", "detectron2.utils.logger",
    "detectron2.checkpoint", "detectron2.engine.defaults",
    "PIL", "PIL.Image",
    "picamera2",
]
for _mod_name in _MOCK_MODULES:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

import pytest
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import require_current_user_html, require_current_user_api
from types import SimpleNamespace as _SimpleNamespace

# Override auth dependencies for all tests in this module
_fake_user = _SimpleNamespace(id=1, full_name="Test", email="test@test.com", role="operator")


async def _override_html(request=None):
    return _fake_user


async def _override_api(request=None):
    return _fake_user


@pytest.fixture(autouse=True)
def _ensure_auth_override():
    """Ensure auth overrides are active for all tests in this module, with proper cleanup."""
    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[require_current_user_html] = _override_html
    app.dependency_overrides[require_current_user_api] = _override_api
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

@st.composite
def zero_or_negative_dimension_strings(draw):
    """Generate string representations of zero or negative numbers.

    These should always fail dimension validation (must be > 0).
    """
    strategy = st.one_of(
        # Exact zero
        st.just("0"),
        st.just("0.0"),
        st.just("0.00"),
        # Negative floats
        st.floats(min_value=-1e6, max_value=-0.001).map(str),
        # Negative integers
        st.integers(min_value=-10000, max_value=-1).map(str),
    )
    return draw(strategy)


@st.composite
def non_numeric_dimension_strings(draw):
    """Generate strings that cannot be parsed as valid floats.

    These should always fail dimension validation at the parse stage.
    Excludes empty strings because FastAPI Form(...) rejects them with 422
    before reaching application-level validation.
    """
    strategy = st.one_of(
        st.just("abc"),
        st.just("NaN"),
        st.just("inf"),
        st.just("-inf"),
        st.just("Infinity"),
        st.just("--1"),
        st.just("1.2.3"),
        st.just("cinco"),
        st.just("N/A"),
        st.text(
            alphabet=st.characters(whitelist_categories=("L", "P", "S")),
            min_size=1,
            max_size=10,
        ).filter(lambda s: not _is_finite_float(s) and s.strip() != ""),
    )
    return draw(strategy)


@st.composite
def invalid_dimension_pairs(draw):
    """Generate pairs of (width_str, length_str) where at least one is invalid.

    Returns a tuple (width_str, length_str) guaranteed to fail validation.
    Excludes empty strings and whitespace-only strings because FastAPI Form(...)
    rejects those at the framework level with 422 before reaching the route handler.
    """
    # Strategy: randomly decide which dimension is invalid
    invalid_type = draw(st.sampled_from(["width_invalid", "length_invalid", "both_invalid"]))

    valid_dim = draw(st.floats(min_value=0.1, max_value=50.0).map(lambda f: f"{f:.2f}"))

    if invalid_type == "width_invalid":
        invalid_width = draw(st.one_of(
            zero_or_negative_dimension_strings(),
            non_numeric_dimension_strings(),
        ))
        return (invalid_width, valid_dim)
    elif invalid_type == "length_invalid":
        invalid_length = draw(st.one_of(
            zero_or_negative_dimension_strings(),
            non_numeric_dimension_strings(),
        ))
        return (valid_dim, invalid_length)
    else:
        invalid_width = draw(st.one_of(
            zero_or_negative_dimension_strings(),
            non_numeric_dimension_strings(),
        ))
        invalid_length = draw(st.one_of(
            zero_or_negative_dimension_strings(),
            non_numeric_dimension_strings(),
        ))
        return (invalid_width, invalid_length)


@st.composite
def non_existent_module_ids(draw):
    """Generate module IDs that won't exist in the database.

    Uses high integers that are unlikely to conflict with real data.
    """
    return draw(st.integers(min_value=9000, max_value=99999))


@st.composite
def non_existent_monitoring_ids(draw):
    """Generate monitoring IDs that won't exist in the database.

    Uses high integers that are unlikely to conflict with real data.
    """
    return draw(st.integers(min_value=9000, max_value=99999))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_finite_float(s: str) -> bool:
    """Check if a string can be parsed as a finite float."""
    import math
    try:
        val = float(s.strip())
        return math.isfinite(val)
    except (ValueError, TypeError):
        return False


def _create_mock_module(module_id: int = 1):
    """Create a mock Module object with standard attributes."""
    mock_module = MagicMock()
    mock_module.id = module_id
    mock_module.name = "Módulo Test"
    mock_module.greenhouse_id = 1
    mock_module.crop_type = "Tomate Cherry"
    mock_module.width_m = 5.0
    mock_module.length_m = 2.0
    return mock_module


def _create_test_client_with_mocks(
    module_exists: bool = True,
    module_id: int = 1,
    monitoring_service_mock: MagicMock = None,
):
    """Create a TestClient with mocked dependencies.

    Args:
        module_exists: Whether get_module_repository().get_by_id() returns a module
        module_id: The ID for the mock module
        monitoring_service_mock: Optional mock for MonitoringService
    """
    mock_module_repo = MagicMock()
    if module_exists:
        mock_module_repo.get_by_id.return_value = _create_mock_module(module_id)
    else:
        mock_module_repo.get_by_id.return_value = None
    mock_module_repo.update.return_value = None

    mock_monitoring_service = monitoring_service_mock or MagicMock()

    mock_db_manager = MagicMock()
    mock_db_manager.get_session.return_value = MagicMock()

    # Create a fresh app state
    app.state.db_manager = mock_db_manager
    app.state.log_service = MagicMock()

    # Ensure auth overrides are active
    app.dependency_overrides[require_current_user_html] = _override_html

    client = TestClient(app, raise_server_exceptions=False)

    return client, mock_module_repo, mock_monitoring_service


# ---------------------------------------------------------------------------
# Property tests: Dimension Validation Preservation
# ---------------------------------------------------------------------------

class TestDimensionValidationPreservation:
    """Property tests verifying that invalid dimensions produce validation errors
    on the setup screen without reaching MonitoringService.

    **Validates: Requirements 3.1, 3.2**
    """

    @given(dims=invalid_dimension_pairs())
    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_invalid_dimensions_return_validation_error_template(self, dims):
        """Property: For all invalid dimension inputs (zero, negative, non-numeric),
        POST /modulos/{id}/monitoreo/iniciar returns a 200 response with validation
        error messages rendered on the setup template.

        **Validates: Requirements 3.1**

        Preservation: Invalid dimensions are rejected BEFORE any service call,
        producing a template response with error messages for the farmer.
        """
        width_str, length_str = dims

        mock_module_repo = MagicMock()
        mock_module_repo.get_by_id.return_value = _create_mock_module(1)

        mock_monitoring_service = MagicMock()

        mock_db_manager = MagicMock()
        mock_db_manager.get_session.return_value = MagicMock()

        app.state.db_manager = mock_db_manager
        app.state.log_service = MagicMock()

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_service", return_value=mock_monitoring_service), \
             patch("app.routes.agricultural_ui.DETECTION_MODEL_PATH", MagicMock(exists=MagicMock(return_value=True))):

            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/modulos/1/monitoreo/iniciar",
                data={
                    "width_m": width_str,
                    "length_m": length_str,
                    "notes": "",
                },
            )

        # Preservation: returns 200 with template (validation error displayed on setup screen)
        assert response.status_code == 200, (
            f"Expected 200 for invalid dims ({width_str!r}, {length_str!r}), "
            f"got {response.status_code}"
        )

        # MonitoringService should NEVER be called for invalid dimensions
        mock_monitoring_service.start_session.assert_not_called()

    @given(width_str=zero_or_negative_dimension_strings())
    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_zero_or_negative_width_shows_error(self, width_str):
        """Property: For all zero or negative width values, the response contains
        a validation error message and MonitoringService is never called.

        **Validates: Requirements 3.1**

        Preservation: Width <= 0 always triggers validation error on setup screen.
        """
        mock_module_repo = MagicMock()
        mock_module_repo.get_by_id.return_value = _create_mock_module(1)

        mock_monitoring_service = MagicMock()

        mock_db_manager = MagicMock()
        mock_db_manager.get_session.return_value = MagicMock()

        app.state.db_manager = mock_db_manager
        app.state.log_service = MagicMock()

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_service", return_value=mock_monitoring_service), \
             patch("app.routes.agricultural_ui.DETECTION_MODEL_PATH", MagicMock(exists=MagicMock(return_value=True))):

            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/modulos/1/monitoreo/iniciar",
                data={
                    "width_m": width_str,
                    "length_m": "3.0",
                    "notes": "",
                },
            )

        assert response.status_code == 200
        # MonitoringService must NOT be called
        mock_monitoring_service.start_session.assert_not_called()

    @given(width_str=non_numeric_dimension_strings())
    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_non_numeric_width_shows_error(self, width_str):
        """Property: For all non-numeric width strings, the response contains
        a validation error and MonitoringService is never called.

        **Validates: Requirements 3.1**

        Preservation: Non-parseable width always triggers validation error.
        """
        mock_module_repo = MagicMock()
        mock_module_repo.get_by_id.return_value = _create_mock_module(1)

        mock_monitoring_service = MagicMock()

        mock_db_manager = MagicMock()
        mock_db_manager.get_session.return_value = MagicMock()

        app.state.db_manager = mock_db_manager
        app.state.log_service = MagicMock()

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_service", return_value=mock_monitoring_service), \
             patch("app.routes.agricultural_ui.DETECTION_MODEL_PATH", MagicMock(exists=MagicMock(return_value=True))):

            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/modulos/1/monitoreo/iniciar",
                data={
                    "width_m": width_str,
                    "length_m": "3.0",
                    "notes": "",
                },
            )

        assert response.status_code == 200
        mock_monitoring_service.start_session.assert_not_called()


# ---------------------------------------------------------------------------
# Property tests: Non-Existent Module Preservation
# ---------------------------------------------------------------------------

class TestNonExistentModulePreservation:
    """Property tests verifying that requests for non-existent modules produce
    a redirect to /invernaderos with error message.

    **Validates: Requirements 3.2**
    """

    @given(module_id=non_existent_module_ids())
    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_non_existent_module_redirects_to_greenhouses(self, module_id):
        """Property: For all non-existent module IDs, POST /modulos/{id}/monitoreo/iniciar
        returns a 303 redirect to /invernaderos?error=Módulo+no+encontrado.

        **Validates: Requirements 3.2**

        Preservation: Non-existent modules always redirect to greenhouses with
        an error parameter, regardless of form data submitted.
        """
        mock_module_repo = MagicMock()
        mock_module_repo.get_by_id.return_value = None  # Module does not exist

        mock_monitoring_service = MagicMock()

        mock_db_manager = MagicMock()
        mock_db_manager.get_session.return_value = MagicMock()

        app.state.db_manager = mock_db_manager
        app.state.log_service = MagicMock()

        app.dependency_overrides[require_current_user_html] = _override_html

        with patch("app.routes.agricultural_ui.get_module_repository", return_value=mock_module_repo), \
             patch("app.routes.agricultural_ui.get_monitoring_service", return_value=mock_monitoring_service):

            client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
            response = client.post(
                f"/modulos/{module_id}/monitoreo/iniciar",
                data={
                    "width_m": "5.0",
                    "length_m": "2.0",
                    "notes": "",
                },
            )

        # Preservation: 303 redirect to greenhouses with error
        assert response.status_code == 303, (
            f"Expected 303 redirect for non-existent module {module_id}, "
            f"got {response.status_code}"
        )
        location = response.headers.get("location", "")
        assert "/invernaderos" in location, (
            f"Expected redirect to /invernaderos, got location: {location}"
        )
        assert "error=" in location, (
            f"Expected error parameter in redirect, got location: {location}"
        )
        # MonitoringService should NEVER be called for non-existent modules
        mock_monitoring_service.start_session.assert_not_called()


# ---------------------------------------------------------------------------
# Property tests: Non-Existent Monitoring Abort Preservation
# ---------------------------------------------------------------------------

class TestNonExistentMonitoringAbortPreservation:
    """Property tests verifying that abort requests for non-existent monitorings
    produce a redirect to /invernaderos with error message.

    **Validates: Requirements 3.5**
    """

    @given(monitoring_id=non_existent_monitoring_ids())
    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_non_existent_monitoring_abort_redirects_to_greenhouses(self, monitoring_id):
        """Property: For all non-existent monitoring IDs, POST /monitoreos/{id}/abortar
        returns a 303 redirect to /invernaderos?error=Monitoreo+no+encontrado.

        **Validates: Requirements 3.5**

        Preservation: Non-existent monitorings always redirect to greenhouses with
        an error parameter when abort is attempted.
        """
        from src.application.services.monitoring_service import MonitoringNotFoundError

        mock_monitoring_service = MagicMock()
        mock_monitoring_service.abort_session.side_effect = MonitoringNotFoundError(monitoring_id)

        mock_db_manager = MagicMock()
        mock_db_manager.get_session.return_value = MagicMock()

        app.state.db_manager = mock_db_manager
        app.state.log_service = MagicMock()

        app.dependency_overrides[require_current_user_html] = _override_html

        with patch("app.routes.agricultural_ui.get_monitoring_service", return_value=mock_monitoring_service):

            client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
            response = client.post(f"/monitoreos/{monitoring_id}/abortar")

        # Preservation: 303 redirect to greenhouses with error
        assert response.status_code == 303, (
            f"Expected 303 redirect for non-existent monitoring {monitoring_id}, "
            f"got {response.status_code}"
        )
        location = response.headers.get("location", "")
        assert "/invernaderos" in location, (
            f"Expected redirect to /invernaderos, got location: {location}"
        )
        assert "error=" in location, (
            f"Expected error parameter in redirect, got location: {location}"
        )
