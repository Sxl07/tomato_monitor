"""Static verification tests for monitoring_execution.html and monitoring.js.

Reads both files with pathlib and verifies structural requirements
for the capture-first pipeline UI (tasks 4.1 and 4.2).

No browser, no jsdom, no npm dependencies. No writes to outputs/.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE_PATH = _PROJECT_ROOT / "app" / "templates" / "agricultural" / "monitoring_execution.html"
_JS_PATH = _PROJECT_ROOT / "app" / "static" / "js" / "monitoring.js"


@pytest.fixture(scope="module")
def template_content() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js_content() -> str:
    return _JS_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Template tests
# ---------------------------------------------------------------------------


class TestTemplateFinalize:
    """Finalize form structure."""

    def test_finalize_form_exists(self, template_content):
        assert 'id="finalize-form"' in template_content

    def test_finalize_form_method_post(self, template_content):
        # Find the form and check method
        match = re.search(r'<form[^>]*id="finalize-form"[^>]*>', template_content)
        assert match is not None
        assert 'method="post"' in match.group(0)

    def test_finalize_form_action_finalizar_captura(self, template_content):
        match = re.search(r'<form[^>]*id="finalize-form"[^>]*>', template_content)
        assert match is not None
        assert "/finalizar-captura" in match.group(0)

    def test_btn_confirm_finalize_exists(self, template_content):
        assert 'id="btn-confirm-finalize"' in template_content

    def test_primary_action_text_finalizar_captura(self, template_content):
        assert "Finalizar captura" in template_content

    def test_abort_form_still_points_to_abortar(self, template_content):
        match = re.search(r'<form[^>]*id="abort-form"[^>]*>', template_content)
        assert match is not None
        assert "/abortar" in match.group(0)

    def test_finalize_and_abort_use_different_forms(self, template_content):
        assert 'id="finalize-form"' in template_content
        assert 'id="abort-form"' in template_content
        # They are different elements
        finalize_pos = template_content.index('id="finalize-form"')
        abort_pos = template_content.index('id="abort-form"')
        assert finalize_pos != abort_pos


class TestTemplateAnalyzing:
    """Analyzing state block structure."""

    def test_status_analyzing_block_exists(self, template_content):
        assert 'id="status-analyzing"' in template_content

    def test_jinja_condition_for_analyzing(self, template_content):
        assert "monitoring.status != 'analyzing'" in template_content or \
               'monitoring.status == "analyzing"' in template_content or \
               "monitoring.status != 'analyzing'" in template_content

    def test_analysis_progress_text_exists(self, template_content):
        assert 'id="analysis-progress-text"' in template_content

    def test_analysis_processed_exists(self, template_content):
        assert 'id="analysis-processed"' in template_content

    def test_analysis_total_exists(self, template_content):
        assert 'id="analysis-total"' in template_content

    def test_analysis_progress_fill_exists(self, template_content):
        assert 'id="analysis-progress-fill"' in template_content

    def test_finalizing_capture_overlay_exists(self, template_content):
        assert 'id="finalizing-capture-overlay"' in template_content

    def test_analyzing_block_has_no_abort_form(self, template_content):
        # Extract the analyzing block
        start = template_content.index('id="status-analyzing"')
        # Find the next status block or end
        end_candidates = [
            template_content.find('id="status-paused"', start),
            template_content.find('id="status-finishing"', start),
        ]
        end = min(c for c in end_candidates if c > 0)
        analyzing_block = template_content[start:end]
        assert "abort-form" not in analyzing_block
        assert "finalize-form" not in analyzing_block

    def test_template_does_not_replace_finalizar_with_abortar(self, template_content):
        """The finalize form action must not point to /abortar."""
        match = re.search(r'<form[^>]*id="finalize-form"[^>]*>', template_content)
        assert match is not None
        assert "/abortar" not in match.group(0)


# ---------------------------------------------------------------------------
# JavaScript tests
# ---------------------------------------------------------------------------


class TestJsAnalyzingState:
    """monitoring.js recognizes analyzing state."""

    def test_all_statuses_contains_analyzing(self, js_content):
        assert '"analyzing"' in js_content
        # Specifically in ALL_STATUSES array
        match = re.search(r'ALL_STATUSES\s*=\s*\[(.*?)\]', js_content, re.DOTALL)
        assert match is not None
        assert '"analyzing"' in match.group(1)

    def test_terminal_states_does_not_contain_analyzing(self, js_content):
        match = re.search(r'TERMINAL_STATES\s*=\s*\[(.*?)\]', js_content, re.DOTALL)
        assert match is not None
        assert '"analyzing"' not in match.group(1)

    def test_update_analysis_progress_function_exists(self, js_content):
        assert "function updateAnalysisProgress" in js_content

    def test_uses_analysis_processed(self, js_content):
        assert "analysis_processed" in js_content

    def test_uses_analysis_total(self, js_content):
        assert "analysis_total" in js_content

    def test_updates_text_content(self, js_content):
        assert "textContent" in js_content

    def test_updates_style_width(self, js_content):
        assert "style.width" in js_content

    def test_updates_aria_valuenow(self, js_content):
        assert "aria-valuenow" in js_content

    def test_update_execution_ui_calls_update_analysis_progress(self, js_content):
        # Find updateExecutionUI function body and check it calls updateAnalysisProgress
        fn_match = re.search(
            r'function updateExecutionUI\(data\)\s*\{(.*?)(?=\n    // ---|\n    function )',
            js_content, re.DOTALL
        )
        assert fn_match is not None
        assert "updateAnalysisProgress" in fn_match.group(1)

    def test_completed_redirects_to_report(self, js_content):
        assert "/reporte" in js_content

    def test_analyzing_does_not_stop_polling(self, js_content):
        """analyzing is NOT in TERMINAL_STATES so polling continues."""
        match = re.search(r'TERMINAL_STATES\s*=\s*\[(.*?)\]', js_content, re.DOTALL)
        assert match is not None
        assert '"analyzing"' not in match.group(1)

    def test_set_monitoring_actions_disabled_exists(self, js_content):
        assert "function setMonitoringActionsDisabled" in js_content

    def test_analyzing_disables_actions(self, js_content):
        # Check that analyzing state calls setMonitoringActionsDisabled(true)
        assert 'setMonitoringActionsDisabled(true)' in js_content

    def test_running_enables_actions(self, js_content):
        assert 'setMonitoringActionsDisabled(false)' in js_content
