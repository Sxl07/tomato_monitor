"""Tests for build_monitoring_history status handling and module_detail template.

Validates:
- completed, aborted, error included in history
- active statuses excluded
- Correct status label mapping in template
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest


def _make_monitoring(id, status, started_at=None, total_detections=0):
    if started_at is None:
        started_at = datetime(2025, 6, 1, 10, 0, 0)
    return SimpleNamespace(
        id=id,
        status=status,
        started_at=started_at,
        total_detections=total_detections,
    )


def _make_metrics(pct_healthy=75.0):
    return SimpleNamespace(pct_healthy=pct_healthy)


class TestHistoryInclusion:
    """build_monitoring_history includes the right statuses."""

    def test_includes_completed(self):
        from app.context_builders import build_monitoring_history
        m = _make_monitoring(1, "completed")
        result = build_monitoring_history([m], {})
        assert len(result) == 1
        assert result[0].status == "completed"

    def test_includes_aborted(self):
        from app.context_builders import build_monitoring_history
        m = _make_monitoring(2, "aborted")
        result = build_monitoring_history([m], {})
        assert len(result) == 1
        assert result[0].status == "aborted"

    def test_includes_error(self):
        from app.context_builders import build_monitoring_history
        m = _make_monitoring(3, "error")
        result = build_monitoring_history([m], {})
        assert len(result) == 1
        assert result[0].status == "error"

    @pytest.mark.parametrize("status", [
        "initializing", "running", "paused", "finishing", "analyzing"
    ])
    def test_excludes_active_statuses(self, status):
        from app.context_builders import build_monitoring_history
        m = _make_monitoring(10, status)
        result = build_monitoring_history([m], {})
        assert len(result) == 0

    def test_sorted_by_started_at_descending(self):
        from app.context_builders import build_monitoring_history
        m1 = _make_monitoring(1, "completed", datetime(2025, 1, 1))
        m2 = _make_monitoring(2, "completed", datetime(2025, 6, 1))
        m3 = _make_monitoring(3, "error", datetime(2025, 3, 1))
        result = build_monitoring_history([m1, m2, m3], {})
        assert [r.id for r in result] == [2, 3, 1]

    def test_pct_healthy_from_metrics(self):
        from app.context_builders import build_monitoring_history
        m = _make_monitoring(1, "completed")
        metrics = {1: _make_metrics(82.5)}
        result = build_monitoring_history([m], metrics)
        assert result[0].pct_healthy == 82.5

    def test_pct_healthy_zero_without_metrics(self):
        from app.context_builders import build_monitoring_history
        m = _make_monitoring(1, "completed")
        result = build_monitoring_history([m], {})
        assert result[0].pct_healthy == 0.0

    def test_total_tomatoes_from_total_detections(self):
        from app.context_builders import build_monitoring_history
        m = _make_monitoring(1, "completed", total_detections=42)
        result = build_monitoring_history([m], {})
        assert result[0].total_tomatoes == 42

    def test_error_without_metrics_does_not_break(self):
        from app.context_builders import build_monitoring_history
        m = _make_monitoring(1, "error", total_detections=0)
        result = build_monitoring_history([m], {})
        assert len(result) == 1
        assert result[0].pct_healthy == 0.0


# ---------------------------------------------------------------------------
# Template static tests
# ---------------------------------------------------------------------------

_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "app" / "templates" / "agricultural" / "module_detail.html"


class TestTemplateStatusBadges:
    """Static verification of status badge rendering in module_detail.html."""

    @pytest.fixture(scope="class")
    def template(self):
        return _TEMPLATE_PATH.read_text(encoding="utf-8")

    def test_completed_badge_exists(self, template):
        assert 'item.status == "completed"' in template
        assert "Completado" in template

    def test_aborted_badge_exists(self, template):
        assert 'item.status == "aborted"' in template
        assert "Cancelado" in template

    def test_error_badge_exists(self, template):
        assert 'item.status == "error"' in template
        assert "Error" in template

    def test_cancelado_only_for_aborted(self, template):
        """'Cancelado' text only appears in the aborted branch."""
        # Find the line with Cancelado
        lines = template.split("\n")
        for i, line in enumerate(lines):
            if "Cancelado" in line:
                # Check preceding lines for aborted condition
                context = "\n".join(lines[max(0, i - 3):i + 1])
                assert "aborted" in context
                assert "error" not in context.replace("aborted", "").split("Cancelado")[0].split("{% elif")[-1] if "{% elif" in context else True

    def test_cancelado_not_used_for_error(self, template):
        """The error branch uses 'Error', not 'Cancelado'."""
        lines = template.split("\n")
        for i, line in enumerate(lines):
            if 'item.status == "error"' in line:
                # Next few lines should have "Error" not "Cancelado"
                following = "\n".join(lines[i:i + 3])
                assert "Error" in following
                assert "Cancelado" not in following
