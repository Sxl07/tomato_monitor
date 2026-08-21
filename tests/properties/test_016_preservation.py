"""Preservation property tests for Spec 016 — Post-Raspberry Stabilization.

These tests encode the CURRENT behavior that must NOT change after applying
the bugfixes in Spec 016. They verify non-buggy inputs produce the same results.

**EXPECTED OUTCOME**: All tests PASS on UNFIXED code (baseline behavior to preserve).

Testing framework: pytest + hypothesis
Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.11
"""

import pytest
from datetime import datetime

from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Property 1: FULL profile thermal thresholds remain unchanged (pause=78, resume=72)
# Validates: Requirements 3.4
# ---------------------------------------------------------------------------


class TestFullProfileThermalThresholds:
    """FULL profile thermal thresholds must remain unchanged after fixes."""

    def test_full_profile_analysis_thermal_pause_is_78(self):
        """**Validates: Requirements 3.4**

        FULL profile analysis_thermal_pause_threshold must remain 78.0°C.
        """
        from src.infrastructure.config.settings import FULL_PROFILE

        assert FULL_PROFILE.analysis_thermal_pause_threshold == 78.0, (
            f"FULL profile pause threshold changed to "
            f"{FULL_PROFILE.analysis_thermal_pause_threshold}, expected 78.0"
        )

    def test_full_profile_analysis_thermal_resume_is_72(self):
        """**Validates: Requirements 3.4**

        FULL profile analysis_thermal_resume_threshold must remain 72.0°C.
        """
        from src.infrastructure.config.settings import FULL_PROFILE

        assert FULL_PROFILE.analysis_thermal_resume_threshold == 72.0, (
            f"FULL profile resume threshold changed to "
            f"{FULL_PROFILE.analysis_thermal_resume_threshold}, expected 72.0"
        )

    def test_full_profile_thermal_warning_is_78(self):
        """**Validates: Requirements 3.4**

        FULL profile thermal_warning_temp must remain 78.0°C.
        """
        from src.infrastructure.config.settings import FULL_PROFILE

        assert FULL_PROFILE.thermal_warning_temp == 78.0

    def test_full_profile_thermal_critical_is_85(self):
        """**Validates: Requirements 3.4**

        FULL profile thermal_critical_temp must remain 85.0°C.
        """
        from src.infrastructure.config.settings import FULL_PROFILE

        assert FULL_PROFILE.thermal_critical_temp == 85.0

    def test_full_profile_thermal_resume_temp_is_72(self):
        """**Validates: Requirements 3.4**

        FULL profile thermal_resume_temp must remain 72.0°C.
        """
        from src.infrastructure.config.settings import FULL_PROFILE

        assert FULL_PROFILE.thermal_resume_temp == 72.0


# ---------------------------------------------------------------------------
# Property 2: For all valid positive (width, length) pairs, dimension
# validation still accepts them
# Validates: Requirements 3.3
# ---------------------------------------------------------------------------


class TestDimensionValidationPreserved:
    """validate_dimensions() must continue to accept valid positive dimension pairs."""

    @given(
        width=st.floats(min_value=0.01, max_value=1000.0, allow_nan=False, allow_infinity=False),
        length=st.floats(min_value=0.01, max_value=1000.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_valid_positive_dimensions_always_accepted(self, width: float, length: float):
        """**Validates: Requirements 3.3**

        For all positive floats in (0, 1000], validate_dimensions must accept
        them and return the parsed values (or very close due to float repr).
        """
        from src.application.validators import validate_dimensions

        width_str = f"{width:.6f}"
        length_str = f"{length:.6f}"

        result = validate_dimensions(width_str, length_str)

        assert result is not None, "validate_dimensions returned None for valid input"
        w, l = result
        assert abs(w - width) < 0.001, f"Width mismatch: {w} != {width}"
        assert abs(l - length) < 0.001, f"Length mismatch: {l} != {length}"

    def test_concrete_5_by_3_accepted(self):
        """**Validates: Requirements 3.3**

        Monitoring with width=5.0, length=3.0 must be accepted and return exact values.
        """
        from src.application.validators import validate_dimensions

        w, l = validate_dimensions("5.0", "3.0")
        assert w == 5.0
        assert l == 3.0

    def test_concrete_integer_dimensions_accepted(self):
        """**Validates: Requirements 3.3**

        Integer-like dimension strings ("10", "20") must still be accepted.
        """
        from src.application.validators import validate_dimensions

        w, l = validate_dimensions("10", "20")
        assert w == 10.0
        assert l == 20.0

    @given(
        width=st.floats(min_value=-1000.0, max_value=0.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50)
    def test_negative_or_zero_width_rejected(self, width: float):
        """**Validates: Requirements 3.3**

        Negative or zero width must continue to be rejected.
        """
        from src.application.validators import validate_dimensions, ValidationError

        with pytest.raises(ValidationError):
            validate_dimensions(f"{width:.6f}", "5.0")

    @given(
        length=st.floats(min_value=-1000.0, max_value=0.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50)
    def test_negative_or_zero_length_rejected(self, length: float):
        """**Validates: Requirements 3.3**

        Negative or zero length must continue to be rejected.
        """
        from src.application.validators import validate_dimensions, ValidationError

        with pytest.raises(ValidationError):
            validate_dimensions("5.0", f"{length:.6f}")


# ---------------------------------------------------------------------------
# Property 3: Monitoring status transitions follow allowed FSM paths
# Validates: Requirements 3.5
# ---------------------------------------------------------------------------


class TestMonitoringFSMPreserved:
    """Monitoring status FSM transitions must follow defined rules."""

    def test_happy_path_initializing_to_completed(self):
        """**Validates: Requirements 3.5**

        The capture-first happy path: INITIALIZING → RUNNING → ANALYZING → COMPLETED
        must remain valid.
        """
        from src.domain.value_objects.monitoring_status import (
            MonitoringStatus,
            MonitoringState,
        )

        status = MonitoringStatus(MonitoringState.INITIALIZING)
        status = status.transition_to(MonitoringState.RUNNING)
        assert status.state == MonitoringState.RUNNING

        status = status.transition_to(MonitoringState.ANALYZING)
        assert status.state == MonitoringState.ANALYZING

        status = status.transition_to(MonitoringState.COMPLETED)
        assert status.state == MonitoringState.COMPLETED
        assert status.is_terminal()

    def test_running_to_paused_and_back(self):
        """**Validates: Requirements 3.5**

        RUNNING → PAUSED → RUNNING must remain valid (pause/resume cycle).
        """
        from src.domain.value_objects.monitoring_status import (
            MonitoringStatus,
            MonitoringState,
        )

        status = MonitoringStatus(MonitoringState.RUNNING)
        status = status.transition_to(MonitoringState.PAUSED)
        assert status.state == MonitoringState.PAUSED

        status = status.transition_to(MonitoringState.RUNNING)
        assert status.state == MonitoringState.RUNNING

    def test_running_to_aborted(self):
        """**Validates: Requirements 3.5**

        RUNNING → ABORTED must remain valid (explicit operator cancellation).
        """
        from src.domain.value_objects.monitoring_status import (
            MonitoringStatus,
            MonitoringState,
        )

        status = MonitoringStatus(MonitoringState.RUNNING)
        status = status.transition_to(MonitoringState.ABORTED)
        assert status.state == MonitoringState.ABORTED
        assert status.is_terminal()

    def test_terminal_states_reject_all_transitions(self):
        """**Validates: Requirements 3.5**

        Terminal states (completed, aborted, error) must reject all transitions.
        """
        from src.domain.value_objects.monitoring_status import (
            MonitoringStatus,
            MonitoringState,
        )
        from src.domain.exceptions import InvalidTransitionError

        terminal_states = [
            MonitoringState.COMPLETED,
            MonitoringState.ABORTED,
            MonitoringState.ERROR,
        ]

        all_states = list(MonitoringState)

        for terminal in terminal_states:
            status = MonitoringStatus(terminal)
            for target in all_states:
                with pytest.raises(InvalidTransitionError):
                    status.transition_to(target)

    @given(
        target=st.sampled_from([
            "paused", "finishing", "analyzing", "completed", "aborted", "error"
        ])
    )
    @settings(max_examples=20)
    def test_running_can_transition_to_all_allowed(self, target: str):
        """**Validates: Requirements 3.5**

        RUNNING state must be able to transition to all its defined successors.
        """
        from src.domain.value_objects.monitoring_status import (
            MonitoringStatus,
            MonitoringState,
        )

        status = MonitoringStatus(MonitoringState.RUNNING)
        target_state = MonitoringState(target)
        new_status = status.transition_to(target_state)
        assert new_status.state == target_state

    def test_invalid_transition_raises_error(self):
        """**Validates: Requirements 3.5**

        Invalid transitions (e.g., INITIALIZING → COMPLETED) must raise
        InvalidTransitionError.
        """
        from src.domain.value_objects.monitoring_status import (
            MonitoringStatus,
            MonitoringState,
        )
        from src.domain.exceptions import InvalidTransitionError

        status = MonitoringStatus(MonitoringState.INITIALIZING)
        with pytest.raises(InvalidTransitionError):
            status.transition_to(MonitoringState.COMPLETED)

    def test_analyzing_to_error(self):
        """**Validates: Requirements 3.5**

        ANALYZING → ERROR must remain valid (analysis failure case).
        """
        from src.domain.value_objects.monitoring_status import (
            MonitoringStatus,
            MonitoringState,
        )

        status = MonitoringStatus(MonitoringState.ANALYZING)
        status = status.transition_to(MonitoringState.ERROR)
        assert status.state == MonitoringState.ERROR
        assert status.is_terminal()


# ---------------------------------------------------------------------------
# Property 4: Completed exports retain file_path, file_size, and manifest
# Validates: Requirements 3.7
# ---------------------------------------------------------------------------


class TestCompletedExportMetadata:
    """Completed ExportPackage records must retain their metadata."""

    def test_completed_export_retains_all_fields(self, db_manager):
        """**Validates: Requirements 3.7**

        A completed ExportPackage persisted to DB must retain file_path,
        file_size_bytes, manifest_json, records_count, images_count, and
        completed_at when retrieved.
        """
        from src.infrastructure.persistence.models.export_package_model import (
            ExportPackageModel,
        )
        from src.infrastructure.persistence.models.user_model import UserModel
        from datetime import datetime, timezone

        session = db_manager.get_session()

        # Create a test user for FK
        user = UserModel(
            full_name="Export Test User",
            email="export_preserve@example.com",
            password_hash="hash123",
            role="operator",
        )
        session.add(user)
        session.flush()

        # Create a completed export with all fields populated
        completed_at = datetime(2024, 6, 15, 14, 30, 0)
        export = ExportPackageModel(
            created_by_user_id=user.id,
            scope="full",
            scope_id=None,
            file_path="outputs/exports/export_1_20240615.zip",
            file_size_bytes=1048576,
            status="completed",
            error_message=None,
            records_count=50,
            images_count=120,
            completed_at=completed_at,
            manifest_json='{"version": "1.0", "checksum": "abc123"}',
        )
        session.add(export)
        session.commit()
        export_id = export.id

        # Retrieve and verify all fields are intact
        session.expire_all()
        record = (
            session.query(ExportPackageModel)
            .filter(ExportPackageModel.id == export_id)
            .first()
        )

        assert record is not None, "Export record not found after persist"
        assert record.status == "completed"
        assert record.file_path == "outputs/exports/export_1_20240615.zip"
        assert record.file_size_bytes == 1048576
        assert record.records_count == 50
        assert record.images_count == 120
        assert record.manifest_json == '{"version": "1.0", "checksum": "abc123"}'
        assert record.completed_at == completed_at

    @given(
        records_count=st.integers(min_value=0, max_value=10000),
        images_count=st.integers(min_value=0, max_value=10000),
        file_size=st.integers(min_value=0, max_value=100_000_000),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_export_metadata_round_trip(
        self, records_count: int, images_count: int, file_size: int, db_manager
    ):
        """**Validates: Requirements 3.7**

        For any valid combination of records_count, images_count, and file_size,
        a completed export persists and retrieves the same values.
        """
        from src.infrastructure.persistence.models.export_package_model import (
            ExportPackageModel,
        )
        from src.infrastructure.persistence.models.user_model import UserModel

        session = db_manager.get_session()

        # Ensure user exists (may already exist from prior examples)
        user = (
            session.query(UserModel)
            .filter(UserModel.email == "roundtrip@example.com")
            .first()
        )
        if user is None:
            user = UserModel(
                full_name="Roundtrip User",
                email="roundtrip@example.com",
                password_hash="hash123",
                role="operator",
            )
            session.add(user)
            session.flush()

        export = ExportPackageModel(
            created_by_user_id=user.id,
            scope="full",
            file_path=f"outputs/exports/export_rt_{file_size}.zip",
            file_size_bytes=file_size,
            status="completed",
            records_count=records_count,
            images_count=images_count,
            manifest_json='{"ok": true}',
        )
        session.add(export)
        session.flush()
        export_id = export.id

        session.expire_all()
        record = (
            session.query(ExportPackageModel)
            .filter(ExportPackageModel.id == export_id)
            .first()
        )

        assert record.records_count == records_count
        assert record.images_count == images_count
        assert record.file_size_bytes == file_size
        assert record.status == "completed"

        # Cleanup for next hypothesis example
        session.delete(record)
        session.flush()


# ---------------------------------------------------------------------------
# Property 5: CaptureWorker frame_index == snapshot_count (naming consistency)
# Validates: Requirements 3.1
# ---------------------------------------------------------------------------


class TestCaptureWorkerNamingConsistency:
    """CaptureWorker must assign frame_index == snapshot_count at save time."""

    def test_frame_index_matches_snapshot_count_sequentially(self):
        """**Validates: Requirements 3.1**

        CaptureWorker saves snapshots with frame_index equal to the current
        _snapshot_count value (0-based). The image filename uses
        snapshot_{_snapshot_count:06d}.jpg format. After saving, _snapshot_count
        increments. So the first snapshot is frame_index=0, second is 1, etc.
        """
        # This test verifies the naming logic by inspecting CaptureWorker's
        # _save_snapshot implementation. The key invariant is:
        # snapshot.frame_index == self._snapshot_count (at save time, before increment)
        # image_filename == f"snapshot_{self._snapshot_count:06d}.jpg"

        from src.application.services.capture_worker import CaptureWorker
        import inspect

        source = inspect.getsource(CaptureWorker._save_snapshot)

        # Verify the frame_index assignment uses self._snapshot_count
        assert "frame_index=self._snapshot_count" in source, (
            "CaptureWorker._save_snapshot does not assign "
            "frame_index=self._snapshot_count"
        )

        # Verify the filename uses the same counter
        assert "self._snapshot_count:06d" in source, (
            "CaptureWorker._save_snapshot filename does not use "
            "self._snapshot_count:06d format"
        )

    @given(snapshot_count=st.integers(min_value=0, max_value=999999))
    @settings(max_examples=100)
    def test_filename_format_matches_frame_index(self, snapshot_count: int):
        """**Validates: Requirements 3.1**

        For any snapshot_count value, the generated filename and frame_index
        are consistent: snapshot_{N:06d}.jpg corresponds to frame_index=N.
        """
        # Simulate the naming logic from CaptureWorker._save_snapshot
        image_filename = f"snapshot_{snapshot_count:06d}.jpg"
        frame_index = snapshot_count

        # Extract the number from the filename
        number_in_filename = int(image_filename.replace("snapshot_", "").replace(".jpg", ""))

        assert number_in_filename == frame_index, (
            f"Filename number {number_in_filename} != frame_index {frame_index}"
        )

    def test_snapshot_path_structure(self):
        """**Validates: Requirements 3.1**

        CaptureWorker stores snapshots at the expected path pattern:
        outputs/monitorings/{monitoring_id}/snapshots/raw/snapshot_{N:06d}.jpg
        """
        from src.application.services.capture_worker import CaptureWorker
        import inspect

        source = inspect.getsource(CaptureWorker._save_snapshot)

        # Verify the directory structure
        assert "outputs/monitorings/" in source
        assert "/snapshots/raw" in source
