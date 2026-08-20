"""Unit tests for the OperationalAlert domain entity."""

import pytest

from src.domain.entities.operational_alert import OperationalAlert


class TestOperationalAlertCreation:
    """Test valid and invalid creation of OperationalAlert."""

    def test_valid_creation(self):
        alert = OperationalAlert(
            alert_type="monitoring_overdue",
            severity="warning",
            title="Monitoreo vencido",
            message="El módulo tiene 3 días de retraso.",
            module_id=1,
        )
        assert alert.alert_type == "monitoring_overdue"
        assert alert.severity == "warning"
        assert alert.title == "Monitoreo vencido"
        assert alert.message == "El módulo tiene 3 días de retraso."
        assert alert.module_id == 1
        assert alert.source == "computed"

    def test_valid_creation_minimal(self):
        alert = OperationalAlert(
            alert_type="export_pending",
            severity="info",
            title="Exportación pendiente",
            message="Hay 2 exportaciones en proceso.",
        )
        assert alert.module_id is None
        assert alert.greenhouse_id is None
        assert alert.monitoring_id is None

    def test_invalid_severity_raises(self):
        with pytest.raises(ValueError, match="severity must be"):
            OperationalAlert(
                alert_type="monitoring_overdue",
                severity="urgent",
                title="Test",
                message="Test message",
            )

    def test_empty_alert_type_raises(self):
        with pytest.raises(ValueError, match="alert_type must not be empty"):
            OperationalAlert(
                alert_type="",
                severity="info",
                title="Test",
                message="Test message",
            )

    def test_whitespace_alert_type_raises(self):
        with pytest.raises(ValueError, match="alert_type must not be empty"):
            OperationalAlert(
                alert_type="   ",
                severity="info",
                title="Test",
                message="Test message",
            )

    def test_empty_title_raises(self):
        with pytest.raises(ValueError, match="title must not be empty"):
            OperationalAlert(
                alert_type="monitoring_overdue",
                severity="warning",
                title="",
                message="Test message",
            )

    def test_empty_message_raises(self):
        with pytest.raises(ValueError, match="message must not be empty"):
            OperationalAlert(
                alert_type="monitoring_overdue",
                severity="warning",
                title="Test",
                message="",
            )

    def test_negative_module_id_raises(self):
        with pytest.raises(ValueError, match="module_id must be positive"):
            OperationalAlert(
                alert_type="monitoring_overdue",
                severity="warning",
                title="Test",
                message="Test message",
                module_id=-1,
            )

    def test_zero_module_id_raises(self):
        with pytest.raises(ValueError, match="module_id must be positive"):
            OperationalAlert(
                alert_type="monitoring_overdue",
                severity="warning",
                title="Test",
                message="Test message",
                module_id=0,
            )

    def test_negative_greenhouse_id_raises(self):
        with pytest.raises(ValueError, match="greenhouse_id must be positive"):
            OperationalAlert(
                alert_type="monitoring_overdue",
                severity="warning",
                title="Test",
                message="Test message",
                greenhouse_id=-1,
            )

    def test_negative_monitoring_id_raises(self):
        with pytest.raises(ValueError, match="monitoring_id must be positive"):
            OperationalAlert(
                alert_type="analysis_error",
                severity="critical",
                title="Test",
                message="Test message",
                monitoring_id=0,
            )

    def test_all_valid_severities(self):
        for sev in ("info", "warning", "critical"):
            alert = OperationalAlert(
                alert_type="test",
                severity=sev,
                title="Test",
                message="Message",
            )
            assert alert.severity == sev
