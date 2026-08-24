"""Unit tests for thermal UI elements in monitoring.js and template — Task 12B.

Tests verify:
- analysis-thermal-alert element exists in the analyzing block
- analysis-thermal-text element exists
- analysis-thermal-stats element exists
- Alert is hidden by default
- JS uses "pause_reason"
- JS contains "Pausado por temperatura"
- JS contains "Temperatura actual"
- JS contains "Temperatura máxima observada"
- JS does not use innerHTML
- JS does not contain stopMonitoringPolling inside updateAnalysisThermalState
- "analyzing" not in TERMINAL_STATES
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "app" / "templates" / "agricultural" / "monitoring_execution.html"
JS_PATH = Path(__file__).resolve().parents[2] / "app" / "static" / "js" / "monitoring.js"


@pytest.fixture
def template_content():
    """Read the monitoring execution template."""
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture
def js_content():
    """Read the monitoring.js file."""
    return JS_PATH.read_text(encoding="utf-8")


class TestThermalAlertElement:
    """Thermal alert element exists in the analyzing block."""

    def test_analysis_thermal_alert_exists(self, template_content):
        assert 'id="analysis-thermal-alert"' in template_content

    def test_analysis_thermal_alert_inside_analyzing_block(self, template_content):
        # Find the analyzing block and check it contains the alert
        analyzing_start = template_content.find('id="status-analyzing"')
        analyzing_end = template_content.find('id="status-paused"')
        analyzing_block = template_content[analyzing_start:analyzing_end]
        assert 'id="analysis-thermal-alert"' in analyzing_block

    def test_analysis_thermal_alert_hidden_by_default(self, template_content):
        # The alert div should have 'hidden' class
        pattern = r'id="analysis-thermal-alert"[^>]*class="[^"]*hidden[^"]*"'
        assert re.search(pattern, template_content)

    def test_analysis_thermal_text_exists(self, template_content):
        assert 'id="analysis-thermal-text"' in template_content

    def test_analysis_thermal_stats_exists(self, template_content):
        assert 'id="analysis-thermal-stats"' in template_content

    def test_alert_has_warning_class(self, template_content):
        # The alert should have alert--warning class
        pattern = r'id="analysis-thermal-alert"[^>]*class="[^"]*alert--warning[^"]*"'
        assert re.search(pattern, template_content)

    def test_alert_has_temperature_icon(self, template_content):
        # Between analysis-thermal-alert and analysis-thermal-text there should be 🌡️
        alert_start = template_content.find('id="analysis-thermal-alert"')
        text_start = template_content.find('id="analysis-thermal-text"')
        between = template_content[alert_start:text_start]
        assert "icon('thermometer')" in between or "<svg" in between


class TestMonitoringJsThermalFunction:
    """monitoring.js has updateAnalysisThermalState with correct behavior."""

    def test_update_analysis_thermal_state_defined(self, js_content):
        assert "function updateAnalysisThermalState" in js_content

    def test_update_analysis_thermal_state_exposed_on_window(self, js_content):
        assert "window.updateAnalysisThermalState" in js_content

    def test_called_from_update_execution_ui(self, js_content):
        # Should be called after updateAnalysisProgress
        idx_progress = js_content.find("updateAnalysisProgress(data)")
        idx_thermal = js_content.find("updateAnalysisThermalState(data)")
        assert idx_progress > 0
        assert idx_thermal > idx_progress

    def test_uses_pause_reason(self, js_content):
        """JS uses data.pause_reason for the thermal text."""
        assert "data.pause_reason" in js_content

    def test_contains_pausado_por_temperatura(self, js_content):
        """JS contains the fallback pause text."""
        assert "Pausado por temperatura" in js_content

    def test_contains_temperatura_actual(self, js_content):
        """JS shows current temperature text."""
        assert "Temperatura actual:" in js_content

    def test_contains_temperatura_maxima_observada(self, js_content):
        """JS shows peak temperature fallback text."""
        assert "Temperatura máxima observada:" in js_content

    def test_does_not_use_innerhtml(self, js_content):
        """updateAnalysisThermalState does not use innerHTML."""
        # Extract the function body
        fn_start = js_content.find("function updateAnalysisThermalState")
        # Find the matching closing brace (approximation: next function or end)
        fn_end = js_content.find("function setMonitoringActionsDisabled", fn_start)
        fn_body = js_content[fn_start:fn_end]
        assert "innerHTML" not in fn_body

    def test_does_not_contain_stop_monitoring_in_thermal_fn(self, js_content):
        """updateAnalysisThermalState does not call stopMonitoringPolling."""
        fn_start = js_content.find("function updateAnalysisThermalState")
        fn_end = js_content.find("function setMonitoringActionsDisabled", fn_start)
        fn_body = js_content[fn_start:fn_end]
        assert "stopMonitoringPolling" not in fn_body

    def test_analyzing_not_in_terminal_states(self, js_content):
        """'analyzing' is not in TERMINAL_STATES array."""
        # Find TERMINAL_STATES definition
        ts_start = js_content.find("TERMINAL_STATES")
        ts_end = js_content.find("]", ts_start)
        ts_block = js_content[ts_start:ts_end + 1]
        assert "analyzing" not in ts_block
