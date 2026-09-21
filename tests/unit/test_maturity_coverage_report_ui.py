"""Report UI tests for non-estimable maturity coverage (Cases A/B/C).

The productive report must communicate when maturity could not be estimated,
WITHOUT altering the semantics of the six USDA percentages (which are always
computed over the covered subset only) and WITHOUT introducing a seventh
persisted category.

Case A: maturity_covered == 0 and total_tomatoes > 0
        → fully gray bar + "no fue posible estimar la madurez" message.
Case B: 0 < maturity_covered < total_tomatoes
        → six-stage bar + "X de Y (Z %)" note.
Case C: maturity_covered == total_tomatoes
        → six-stage bar, no extra note.
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

# Mock heavy ML deps (same pattern as other UI tests).
_MOCK_MODULES = [
    "torch", "torchvision", "detectron2", "PIL", "PIL.Image", "picamera2",
]
for _m in _MOCK_MODULES:
    if _m not in sys.modules:
        sys.modules[_m] = MagicMock()

from app.context_builders import build_report_metrics


# ---------------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------------


def _metrics(total, *, pct_stages=None):
    """Build a MonitoringMetricsModel-like object.

    pct_stages: dict of the six USDA percentages (over the covered subset).
    """
    stages = {
        "green": 0.0, "breaker": 0.0, "turning": 0.0,
        "pink": 0.0, "light_red": 0.0, "red": 0.0,
    }
    if pct_stages:
        stages.update(pct_stages)
    return SimpleNamespace(
        total_tomatoes=total,
        healthy_count=total,
        unhealthy_count=0,
        pct_healthy=100.0 if total else 0.0,
        pct_unhealthy=0.0,
        pct_green=stages["green"],
        pct_breaker=stages["breaker"],
        pct_turning=stages["turning"],
        pct_pink=stages["pink"],
        pct_light_red=stages["light_red"],
        pct_red=stages["red"],
        snapshots_with_detections=1 if total else 0,
    )


def _render_report(report_metrics) -> str:
    from fastapi.templating import Jinja2Templates
    from src.application.utils.jinja_filters import register_filters

    templates = Jinja2Templates(directory="app/templates")
    register_filters(templates)
    template = templates.env.get_template("agricultural/monitoring_report.html")
    return template.render(
        title="Reporte",
        show_back=True,
        back_url="/modulos/1",
        monitoring=SimpleNamespace(id=1, status="completed"),
        module=SimpleNamespace(id=1, name="Módulo 1"),
        metrics=report_metrics,
        gallery=[],
        date_str="12 Jun 2025",
        time_str="14:30",
        error=None,
        can_delete=False,
    )


# ---------------------------------------------------------------------------
# Context builder: coverage computation
# ---------------------------------------------------------------------------


class TestBuildReportMetricsCoverage:
    def test_case_a_zero_coverage(self):
        rm = build_report_metrics(_metrics(5), maturity_covered=0)
        assert rm.total_tomatoes == 5
        assert rm.maturity_covered == 0
        assert rm.maturity_uncovered == 5
        assert rm.pct_maturity_covered == 0.0

    def test_case_b_partial_coverage(self):
        rm = build_report_metrics(
            _metrics(10, pct_stages={"red": 100.0}), maturity_covered=4
        )
        assert rm.maturity_covered == 4
        assert rm.maturity_uncovered == 6
        assert round(rm.pct_maturity_covered, 1) == 40.0
        # Six-stage semantics unchanged (still over covered subset).
        assert rm.maturity_stages["red"] == 100.0

    def test_case_c_full_coverage(self):
        rm = build_report_metrics(
            _metrics(3, pct_stages={"green": 100.0}), maturity_covered=3
        )
        assert rm.maturity_covered == 3
        assert rm.maturity_uncovered == 0
        assert rm.pct_maturity_covered == 100.0

    def test_covered_clamped_to_total(self):
        rm = build_report_metrics(_metrics(2), maturity_covered=99)
        assert rm.maturity_covered == 2
        assert rm.maturity_uncovered == 0


# ---------------------------------------------------------------------------
# Template rendering: Cases A/B/C
# ---------------------------------------------------------------------------


class TestReportTemplateCoverage:
    def test_case_a_renders_gray_bar_and_message(self):
        rm = build_report_metrics(_metrics(5), maturity_covered=0)
        html = _render_report(rm)
        assert "maturity-bar-segment--unknown" in html
        assert "No fue posible estimar la madurez" in html
        # No partial-coverage note in Case A.
        assert "no fue posible obtener una estimación confiable" not in html

    def test_case_b_renders_partial_note(self):
        rm = build_report_metrics(
            _metrics(10, pct_stages={"red": 100.0}), maturity_covered=4
        )
        html = _render_report(rm)
        # Six-stage bar present (not the gray unknown bar).
        assert "maturity-bar-segment--unknown" not in html
        assert "Madurez estimada en 4 de 10 tomates" in html
        assert "En 6 tomates no fue posible obtener una estimación confiable" in html

    def test_case_c_renders_no_extra_note(self):
        rm = build_report_metrics(
            _metrics(3, pct_stages={"green": 100.0}), maturity_covered=3
        )
        html = _render_report(rm)
        assert "maturity-bar-segment--unknown" not in html
        assert "no fue posible obtener una estimación confiable" not in html
        assert "No fue posible estimar la madurez" not in html
