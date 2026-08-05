"""Tests for active monitoring card on module detail screen.

Validates build_active_monitoring_context() and template integration.
No DB, no FastAPI TestClient, no browser, no outputs/ writes.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest


def _make_monitoring(id, status, started_at=None):
    if started_at is None:
        started_at = datetime(2025, 7, 17, 1, 39, 0)
    return SimpleNamespace(id=id, status=status, started_at=started_at)


class TestBuildActiveMonitoringContext:
    """build_active_monitoring_context() logic."""

    def test_returns_none_when_no_monitorings(self):
        from app.context_builders import build_active_monitoring_context
        assert build_active_monitoring_context([]) is None

    def test_returns_none_when_only_terminal(self):
        from app.context_builders import build_active_monitoring_context
        monitorings = [
            _make_monitoring(1, "completed"),
            _make_monitoring(2, "aborted"),
            _make_monitoring(3, "error"),
        ]
        assert build_active_monitoring_context(monitorings) is None

    @pytest.mark.parametrize("status", [
        "initializing", "running", "paused", "finishing", "analyzing"
    ])
    def test_includes_active_status(self, status):
        from app.context_builders import build_active_monitoring_context
        m = _make_monitoring(10, status)
        result = build_active_monitoring_context([m])
        assert result is not None
        assert result.id == 10
        assert result.status == status

    @pytest.mark.parametrize("status", ["completed", "aborted", "error"])
    def test_excludes_terminal_status(self, status):
        from app.context_builders import build_active_monitoring_context
        m = _make_monitoring(10, status)
        assert build_active_monitoring_context([m]) is None

    def test_picks_most_recent_when_multiple_active(self):
        from app.context_builders import build_active_monitoring_context
        older = _make_monitoring(1, "running", datetime(2025, 1, 1))
        newer = _make_monitoring(2, "analyzing", datetime(2025, 7, 1))
        result = build_active_monitoring_context([older, newer])
        assert result.id == 2

    def test_analyzing_label(self):
        from app.context_builders import build_active_monitoring_context
        m = _make_monitoring(5, "analyzing")
        result = build_active_monitoring_context([m])
        assert result.status_label == "Analizando snapshots"

    def test_execution_url(self):
        from app.context_builders import build_active_monitoring_context
        m = _make_monitoring(42, "running")
        result = build_active_monitoring_context([m])
        assert result.execution_url == "/monitoreos/42/ejecucion"

    def test_started_at_display_formatted(self):
        from app.context_builders import build_active_monitoring_context
        m = _make_monitoring(1, "running", datetime(2025, 7, 17, 1, 39, 0))
        result = build_active_monitoring_context([m])
        assert "17 Jul 2025" in result.started_at_display
        assert "01:39" in result.started_at_display

    def test_does_not_mutate_input(self):
        from app.context_builders import build_active_monitoring_context
        m = _make_monitoring(1, "analyzing")
        original_status = m.status
        build_active_monitoring_context([m])
        assert m.status == original_status


# ---------------------------------------------------------------------------
# Static route/template verification
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class TestRouteImport:
    """agricultural_ui.py integrates build_active_monitoring_context."""

    def test_imports_build_active_monitoring_context(self):
        source = (_PROJECT_ROOT / "app" / "routes" / "agricultural_ui.py").read_text("utf-8")
        assert "build_active_monitoring_context" in source

    def test_module_detail_calls_build_active(self):
        source = (_PROJECT_ROOT / "app" / "routes" / "agricultural_ui.py").read_text("utf-8")
        assert "build_active_monitoring_context(monitorings)" in source


class TestTemplateActiveMonitoring:
    """module_detail.html shows active monitoring card."""

    @pytest.fixture(scope="class")
    def template(self):
        return (_PROJECT_ROOT / "app" / "templates" / "agricultural" / "module_detail.html").read_text("utf-8")

    def test_uses_active_monitoring(self, template):
        assert "active_monitoring" in template

    def test_shows_monitoreo_en_curso(self, template):
        assert "Monitoreo en curso" in template

    def test_shows_volver_al_monitoreo(self, template):
        assert "Volver al monitoreo" in template

    def test_uses_execution_url(self, template):
        assert "active_monitoring.execution_url" in template

    def test_shows_active_info_text(self, template):
        assert "Ya existe un monitoreo activo para este módulo." in template

    def test_keeps_historial(self, template):
        assert "Historial de Monitoreos" in template

    def test_keeps_report_link(self, template):
        assert "/monitoreos/{{ item.id }}/reporte" in template
