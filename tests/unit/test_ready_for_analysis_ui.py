"""Spec 020, Task 9.4 — UI for the ready_for_analysis state.

Verifies (by inspecting the real templates/JS source, no browser/HTTP needed):
    - monitoring_execution.html has a #status-ready_for_analysis block that does
      NOT use the word "pausado" and exposes a primary "Iniciar análisis" action;
    - the energy-confirmation dialog posts to /iniciar-analisis with a hidden
      power_source_confirmed=true field;
    - the analyzing block does NOT expose "Iniciar análisis";
    - monitoring.js ALL_STATUSES includes "ready_for_analysis";
    - dashboard.html routes ready_for_analysis to the execution screen;
    - no robot/autonomy language in the new block.
"""

from __future__ import annotations

import re
from pathlib import Path

EXEC = Path("app/templates/agricultural/monitoring_execution.html").read_text(encoding="utf-8")
JS = Path("app/static/js/monitoring.js").read_text(encoding="utf-8")
DASH = Path("app/templates/agricultural/dashboard.html").read_text(encoding="utf-8")


def _block(html: str, block_id: str) -> str:
    """Return the block starting at id==block_id up to the NEXT id="..." anchor.

    This keeps assertions scoped to a single block and avoids bleeding into the
    following one (e.g. the paused block that legitimately says "Pausado").
    """
    idx = html.find(f'id="{block_id}"')
    assert idx != -1, f"block {block_id} not found"
    nxt = html.find('id="', idx + len(f'id="{block_id}"'))
    end = nxt if nxt != -1 else idx + 1200
    return html[idx:end]


class TestReadyForAnalysisBlock:
    def test_block_exists(self):
        assert 'id="status-ready_for_analysis"' in EXEC

    def test_block_does_not_use_pausado(self):
        block = _block(EXEC, "status-ready_for_analysis")
        assert "pausado" not in block.lower()

    def test_block_has_iniciar_analisis_action(self):
        block = _block(EXEC, "status-ready_for_analysis")
        assert "Iniciar análisis" in block
        assert "start-analysis-dialog" in block

    def test_block_communicates_pending_analysis(self):
        block = _block(EXEC, "status-ready_for_analysis")
        assert "análisis pendiente" in block.lower()

    def test_no_robot_language_in_block(self):
        block = _block(EXEC, "status-ready_for_analysis").lower()
        for banned in ("robot", "autónom", "chasis", "motor"):
            assert banned not in block


class TestEnergyConfirmationDialog:
    def test_dialog_posts_to_iniciar_analisis(self):
        assert 'action="/monitoreos/{{ monitoring.id }}/iniciar-analisis"' in EXEC

    def test_dialog_has_hidden_power_source_confirmed_true(self):
        # A hidden input carries the per-request confirmation flag = true.
        assert re.search(
            r'name="power_source_confirmed"\s+value="true"', EXEC
        ), "hidden power_source_confirmed=true field missing"

    def test_dialog_mentions_power_source_in_spanish(self):
        # The confirmation dialog and its form live together; assert the phrase
        # appears near the start-analysis form action.
        idx = EXEC.find('action="/monitoreos/{{ monitoring.id }}/iniciar-analisis"')
        assert idx != -1
        window = EXEC[max(0, idx - 600): idx + 200]
        assert "fuente de energía" in window


class TestAnalyzingBlockHidesStartAction:
    def test_analyzing_block_has_no_start_analysis_button(self):
        block = _block(EXEC, "status-analyzing")
        assert "start-analysis-dialog" not in block
        assert "iniciar-analisis" not in block.lower()


class TestMonitoringJsStatuses:
    def test_all_statuses_includes_ready_for_analysis(self):
        # Find the ALL_STATUSES array and assert membership.
        m = re.search(r"ALL_STATUSES\s*=\s*\[(.*?)\]", JS, re.DOTALL)
        assert m is not None
        assert "ready_for_analysis" in m.group(1)

    def test_ready_for_analysis_is_not_terminal_in_js(self):
        m = re.search(r"TERMINAL_STATES\s*=\s*\[(.*?)\]", JS, re.DOTALL)
        assert m is not None
        assert "ready_for_analysis" not in m.group(1)


class TestDashboardLabel:
    def test_dashboard_routes_ready_for_analysis_to_execution(self):
        assert "ready_for_analysis" in DASH
        # It is part of the "Ver ejecución" branch (execution link), not report.
        m = re.search(
            r"status in \(([^)]*)\)", DASH
        )
        assert m is not None and "ready_for_analysis" in m.group(1)
