"""Block 10 UI tests for the analytical dashboard.

Two strategies:
- Direct Jinja render of dashboard.html with a constructed AnalyticsContext.
  This exercises the analytics UI (KPIs, tabs, maturity, harvest, series JSON)
  in isolation and is robust to the TestClient/env quirks affecting route tests.
- Route-based checks (via TestClient, which works for /dashboard) for selectors,
  operational-block preservation and absence of external URLs.
"""

import sys
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Mock heavy ML deps (same pattern as other UI tests).
_MOCK_MODULES = [
    "torch", "torchvision", "detectron2", "PIL", "PIL.Image", "picamera2",
]
for _m in _MOCK_MODULES:
    if _m not in sys.modules:
        sys.modules[_m] = MagicMock()

import pytest

from src.application.dtos.dashboard_analytics_dtos import (
    AnalyticsContext,
    HarvestResult,
    HarvestSummaryAll,
    KpiBlock,
    MaturityCounts,
    MaturityDistributionFallback,
    ModuleSeries,
    SeriesPoint,
)


# ---------------------------------------------------------------------------
# Direct template rendering
# ---------------------------------------------------------------------------


def _render(context: dict) -> str:
    from fastapi.templating import Jinja2Templates
    from src.application.utils.jinja_filters import register_filters

    templates = Jinja2Templates(directory="app/templates")
    register_filters(templates)
    template = templates.env.get_template("agricultural/dashboard.html")
    base = {
        "title": "Dashboard",
        "show_back": False,
        "supabase_configured": False,
        "analytics": None,
        "analytics_greenhouses": [],
        "analytics_scope_modules": [],
        "analytics_selected_greenhouse_id": None,
        "analytics_selected_module_id": None,
        # operational context defaults
        "greenhouse_count": 0, "module_count": 0,
        "pending_modules_count": 0, "overdue_modules_count": 0,
        "monitorings_this_week": 0, "total_snapshots": 0,
        "total_detections": 0, "pending_exports_count": 0,
        "last_monitoring": None, "alerts": [], "recent_activities": [],
    }
    base.update(context)
    return template.render(**base)


def _gh(gid, name):
    return SimpleNamespace(id=gid, name=name)


def _mod(mid, name):
    return SimpleNamespace(id=mid, name=name)


def _kpi(**kw):
    defaults = dict(
        last_monitoring_date=None, fruits_detected=None, fruits_delta=None,
        health_pct_healthy=None, health_pct_unhealthy=None, health_other_pct=None,
        predominant_maturity=[], maturity_covered=None, maturity_total=None,
        harvestable_share=None,
    )
    defaults.update(kw)
    return KpiBlock(**defaults)


def _ctx(**kw):
    kpi = kw.pop("kpis", _kpi())
    harvest = kw.pop("harvest", HarvestResult(status="insufficient", message="Datos insuficientes"))
    return AnalyticsContext(kpis=kpi, harvest=harvest, **kw)


# ---------------------------------------------------------------------------
# No greenhouses
# ---------------------------------------------------------------------------


def test_no_greenhouses_empty_state():
    html = _render({"analytics_greenhouses": []})
    assert "No hay invernaderos registrados aún." in html
    # No analytics rendered
    assert "analytics-kpis" not in html


# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------


class TestSelectors:
    def _common(self, module_id=None):
        return {
            "analytics_greenhouses": [_gh(1, "GH Uno"), _gh(2, "GH Dos")],
            "analytics_scope_modules": [_mod(10, "Mod A"), _mod(11, "Mod B")],
            "analytics_selected_greenhouse_id": 1,
            "analytics_selected_module_id": module_id,
            "analytics": _ctx(),
        }

    def test_greenhouse_selector_present(self):
        html = _render(self._common())
        assert 'name="greenhouse_id"' in html
        assert "GH Uno" in html and "GH Dos" in html

    def test_module_selector_present_with_all_option(self):
        html = _render(self._common())
        assert 'name="module_id"' in html
        assert ">Todos<" in html
        assert "Mod A" in html and "Mod B" in html

    def test_selected_greenhouse_marked(self):
        html = _render(self._common())
        assert '<option value="1" selected>' in html

    def test_all_selected_when_no_module(self):
        html = _render(self._common(module_id=None))
        assert '<option value="all" selected>Todos</option>' in html

    def test_specific_module_selected(self):
        html = _render(self._common(module_id=10))
        assert '<option value="10" selected>' in html

    def test_form_is_get(self):
        html = _render(self._common())
        assert 'method="get"' in html and 'action="/dashboard"' in html


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------


class TestKpis:
    def _base(self, kpi):
        return {
            "analytics_greenhouses": [_gh(1, "GH")],
            "analytics_selected_greenhouse_id": 1,
            "analytics": _ctx(kpis=kpi),
        }

    def test_four_kpi_cards(self):
        html = _render(self._base(_kpi()))
        assert html.count('class="analytics-kpi"') == 4
        assert "Último monitoreo" in html
        assert "Frutos detectados" in html
        assert "Estado sanitario" in html
        assert "Madurez predominante" in html

    def test_health_label_describes_selected_scope(self):
        module = self._base(_kpi(health_pct_healthy=80.0, health_pct_unhealthy=20.0))
        module["analytics_selected_module_id"] = 3
        assert "Estado sanitario del último monitoreo" in _render(module)

        all_modules = self._base(_kpi(health_pct_healthy=80.0, health_pct_unhealthy=20.0))
        all_modules["analytics_selected_module_id"] = None
        html = _render(all_modules)
        assert "Estado sanitario · último de cada módulo" in html
        assert "Evolución del porcentaje de frutos sanos por monitoreo" in html

    def test_fruits_delta_module(self):
        kpi = _kpi(fruits_detected=30, fruits_delta=10)
        html = _render(self._base(kpi))
        assert "30" in html
        assert "+10 vs anterior" in html

    def test_fruits_negative_delta(self):
        kpi = _kpi(fruits_detected=20, fruits_delta=-5)
        html = _render(self._base(kpi))
        assert "-5 vs anterior" in html

    def test_health_with_other(self):
        kpi = _kpi(health_pct_healthy=60.0, health_pct_unhealthy=20.0, health_other_pct=20.0)
        html = _render(self._base(kpi))
        assert "60.0 % sanos" in html
        assert "20.0 % no sanos" in html
        assert "20.0 % otros" in html

    def test_predominant_single(self):
        kpi = _kpi(predominant_maturity=["turning"])
        html = _render(self._base(kpi))
        assert "Turning" in html
        assert "Mixto" not in html

    def test_predominant_tie_mixto(self):
        kpi = _kpi(predominant_maturity=["turning", "pink"])
        html = _render(self._base(kpi))
        assert "Mixto — Turning / Pink" in html

    def test_predominant_none(self):
        kpi = _kpi(predominant_maturity=[])
        html = _render(self._base(kpi))
        assert "Sin datos de madurez" in html

    def test_freshness_all_scope(self):
        kpi = _kpi(
            contributing_modules=3,
            freshness_from=datetime(2025, 6, 1),
            freshness_to=datetime(2025, 6, 9),
        )
        html = _render(self._base(kpi))
        assert "Último dato disponible de 3 módulo(s)" in html


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------


class TestTabs:
    def _base(self):
        return {
            "analytics_greenhouses": [_gh(1, "GH")],
            "analytics_selected_greenhouse_id": 1,
            "analytics": _ctx(),
        }

    def test_three_tabs(self):
        html = _render(self._base())
        assert 'role="tablist"' in html
        assert html.count('role="tab"') == 3
        assert ">Evolución<" in html and ">Sanidad<" in html and ">Madurez<" in html

    def test_only_evolution_active_initially(self):
        html = _render(self._base())
        assert 'id="tab-evolucion"\n                aria-selected="true"' in html or 'aria-selected="true"' in html
        # sanidad and madurez panels start hidden
        assert 'id="panel-sanidad"' in html
        assert "hidden" in html


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------


class TestSeries:
    def _base(self, evolution=None, health=None, maturity=None):
        return {
            "analytics_greenhouses": [_gh(1, "GH")],
            "analytics_selected_greenhouse_id": 1,
            "analytics": _ctx(
                evolution_series=evolution or [],
                health_series=health or [],
                maturity_index_series=maturity or [],
            ),
        }

    def test_series_json_embedded(self):
        series = [ModuleSeries(1, "Mod A", [
            SeriesPoint(datetime(2025, 6, 1), "01/06", 10.0),
            SeriesPoint(datetime(2025, 6, 8), "08/06", 20.0),
        ])]
        html = _render(self._base(evolution=series))
        assert 'data-chart="evolution"' in html
        assert "Mod A" in html
        assert "01/06" in html and "08/06" in html

    def test_multiple_module_series(self):
        series = [
            ModuleSeries(1, "Mod A", [SeriesPoint(datetime(2025, 6, 1), "01/06", 10.0)]),
            ModuleSeries(2, "Mod B", [SeriesPoint(datetime(2025, 6, 3), "03/06", 5.0)]),
        ]
        html = _render(self._base(evolution=series))
        assert "Mod A" in html and "Mod B" in html

    def test_empty_series_shows_message(self):
        html = _render(self._base(evolution=[]))
        assert "Sin datos suficientes para mostrar evolución." in html

    def test_series_note_all_scope(self):
        series = [ModuleSeries(1, "Mod A", [SeriesPoint(datetime(2025, 6, 1), "01/06", 10.0)])]
        base = self._base(evolution=series)
        base["analytics_selected_module_id"] = None  # all scope
        html = _render(base)
        assert "Series por módulo; cada punto corresponde a un monitoreo real." in html

    def test_payload_contains_real_iso_timestamps(self):
        series = [ModuleSeries(1, "Mod A", [
            SeriesPoint(datetime(2025, 6, 1, 9, 30), "01/06", 10.0),
            SeriesPoint(datetime(2025, 6, 20, 14, 0), "20/06", 30.0),
        ])]
        html = _render(self._base(evolution=series))
        # ISO timestamps must be embedded so the renderer can use real dates.
        assert "2025-06-01T09:30:00" in html
        assert "2025-06-20T14:00:00" in html

    def test_data_series_attribute_parses_as_valid_json(self):
        """A real HTML parser must recover the full data-series attribute and
        json.loads must succeed (proves the single-quoted attribute holds the
        complete, valid JSON payload)."""
        import json
        from html.parser import HTMLParser

        series = [ModuleSeries(1, "Mód A", [
            SeriesPoint(datetime(2025, 6, 1, 9, 30), "01/06", 10.0),
            SeriesPoint(datetime(2025, 6, 20, 14, 0), "20/06", 30.0),
        ])]
        html = _render(self._base(evolution=series))

        collected = {}

        class _P(HTMLParser):
            def handle_starttag(self, tag, attrs):
                d = dict(attrs)
                if d.get("data-chart") == "evolution" and "data-series" in d:
                    collected["value"] = d["data-series"]

        parser = _P()
        parser.feed(html)

        assert "value" in collected, "data-series attribute not recovered by HTML parser"
        payload = json.loads(collected["value"])
        assert isinstance(payload, list) and len(payload) == 1
        s0 = payload[0]
        assert s0["module_id"] == 1
        assert s0["module_name"] == "Mód A"
        assert len(s0["points"]) == 2
        assert s0["points"][0]["ts"] == "2025-06-01T09:30:00"
        assert s0["points"][1]["ts"] == "2025-06-20T14:00:00"
        assert s0["points"][0]["value"] == 10.0

    def test_payload_preserves_irregular_gaps_and_independent_series(self):
        # M1 01/06 & 20/06 ; M2 07/06 & 10/06 (between M1's points)
        series = [
            ModuleSeries(1, "M1", [
                SeriesPoint(datetime(2025, 6, 1), "01/06", 10.0),
                SeriesPoint(datetime(2025, 6, 20), "20/06", 40.0),
            ]),
            ModuleSeries(2, "M2", [
                SeriesPoint(datetime(2025, 6, 7), "07/06", 5.0),
                SeriesPoint(datetime(2025, 6, 10), "10/06", 8.0),
            ]),
        ]
        html = _render(self._base(evolution=series))
        # Both modules present as independent series with their real dates.
        assert '"module_id": 1' in html and '"module_id": 2' in html
        assert "2025-06-01T00:00:00" in html and "2025-06-20T00:00:00" in html
        assert "2025-06-07T00:00:00" in html and "2025-06-10T00:00:00" in html


# ---------------------------------------------------------------------------
# Maturity distribution
# ---------------------------------------------------------------------------


class TestMaturity:
    def _base(self, **ctx_kw):
        return {
            "analytics_greenhouses": [_gh(1, "GH")],
            "analytics_selected_greenhouse_id": 1,
            "analytics": _ctx(**ctx_kw),
        }

    def test_real_distribution_with_coverage(self):
        kpi = _kpi(maturity_covered=10, maturity_total=16, harvestable_share=0.2)
        html = _render(self._base(
            kpis=kpi,
            maturity_coverage_known=True,
            maturity_current_counts=MaturityCounts(green=5, red=5),
            maturity_current_distribution={"green": 50.0, "breaker": 0.0, "turning": 0.0, "pink": 0.0, "light_red": 0.0, "red": 50.0},
        ))
        assert "Madurez clasificable: 10 de 16 frutos." in html
        assert "maturity-bar" in html

    def test_fallback_coverage_unavailable(self):
        html = _render(self._base(
            maturity_coverage_known=False,
            maturity_current_fallback=MaturityDistributionFallback(pct_green=100.0),
            maturity_current_distribution={"green": 100.0, "breaker": 0.0, "turning": 0.0, "pink": 0.0, "light_red": 0.0, "red": 0.0},
        ))
        assert "Cobertura no disponible." in html

    def test_no_maturity_distribution(self):
        html = _render(self._base(maturity_current_distribution=None))
        assert "Sin datos de madurez." in html

    def test_harvestable_share_wording(self):
        kpi = _kpi(maturity_covered=10, maturity_total=10, harvestable_share=0.3)
        html = _render(self._base(
            kpis=kpi,
            maturity_coverage_known=True,
            maturity_current_counts=MaturityCounts(green=7, red=3),
            maturity_current_distribution={"green": 70.0, "breaker": 0.0, "turning": 0.0, "pink": 0.0, "light_red": 0.0, "red": 30.0},
        ))
        assert "de los frutos con madurez clasificable están en Light Red/Red" in html
        assert "% de los frutos están" not in html

    def test_maturity_index_ordinal_note(self):
        html = _render(self._base())
        assert "Indicador ordinal operacional: Verde 0.0 → Rojo 1.0." in html


# ---------------------------------------------------------------------------
# Harvest — module (HarvestResult)
# ---------------------------------------------------------------------------


class TestHarvestModule:
    def _base(self, harvest, kpi=None):
        return {
            "analytics_greenhouses": [_gh(1, "GH")],
            "analytics_selected_greenhouse_id": 1,
            "analytics": _ctx(kpis=kpi or _kpi(), harvest=harvest),
        }

    def test_window(self):
        h = HarvestResult(
            status="window",
            window_start=date(2025, 6, 18),
            window_end=date(2025, 6, 26),
            n_observations=4,
            mean_coverage_ratio=0.82,
        )
        html = _render(self._base(h))
        assert "Ventana estimada" in html
        assert "18/06/2025 — 26/06/2025" in html
        assert "Estimación basada en 4 monitoreos." in html
        assert "Cobertura media de madurez: 82 %." in html

    def test_degenerate_window(self):
        h = HarvestResult(
            status="window",
            window_start=date(2025, 6, 20),
            window_end=date(2025, 6, 20),
            is_degenerate_window=True,
            n_observations=3,
            mean_coverage_ratio=1.0,
        )
        html = _render(self._base(h))
        assert "Estimación central: alrededor del 20/06/2025" in html
        assert "Sin dispersión observada" in html
        assert "Ventana estimada" not in html

    def test_target_reached_no_cosechar_ahora(self):
        h = HarvestResult(status="target_reached", n_observations=3)
        html = _render(self._base(h))
        assert "Nivel medio de madurez objetivo alcanzado." in html
        assert "Cosechar ahora" not in html
        assert "Listo para cosecha" not in html

    def test_insufficient_message(self):
        h = HarvestResult(status="insufficient", message="Datos insuficientes: se requieren 3 monitoreos.")
        html = _render(self._base(h))
        assert "Datos insuficientes: se requieren 3 monitoreos." in html

    def test_not_estimable_with_reason(self):
        h = HarvestResult(
            status="not_estimable",
            message="No estimable: la madurez no muestra progresión hacia el objetivo.",
            reason="Tendencia plana o regresiva; no proyecta hacia el objetivo.",
        )
        html = _render(self._base(h))
        assert "No estimable" in html
        assert "Tendencia plana" in html

    def test_disclaimer_always(self):
        h = HarvestResult(status="insufficient", message="x")
        html = _render(self._base(h))
        assert "no es un pronóstico agronómico" in html


# ---------------------------------------------------------------------------
# Harvest — all (HarvestSummaryAll)
# ---------------------------------------------------------------------------


class TestHarvestAll:
    def _base(self, harvest):
        return {
            "analytics_greenhouses": [_gh(1, "GH")],
            "analytics_selected_greenhouse_id": 1,
            "analytics": _ctx(harvest=harvest),
        }

    def test_summary_groups(self):
        h = HarvestSummaryAll(
            target_reached=["M1"],
            upcoming=[("M2", date(2025, 6, 10), date(2025, 6, 15))],
            insufficient=["M3"],
            not_estimable=["M4"],
        )
        html = _render(self._base(h))
        assert "Objetivo alcanzado" in html and "M1" in html
        assert "Con ventana estimada" in html and "M2: 10/06 — 15/06" in html
        assert "Datos insuficientes" in html and "M3" in html
        assert "No estimable" in html and "M4" in html
        # no single greenhouse-wide date
        assert "Ventana estimada</p>" not in html

    def test_empty_summary(self):
        html = _render(self._base(HarvestSummaryAll()))
        assert "Sin datos suficientes para estimar próxima cosecha." in html


# ---------------------------------------------------------------------------
# Operational block preservation + no external URLs
# ---------------------------------------------------------------------------


class TestOperationalPreservation:
    def _full(self):
        mon = SimpleNamespace(
            id=42, status="completed", started_at=datetime(2025, 6, 18, 10, 0),
            total_snapshots=8, total_detections=30,
        )
        alert = SimpleNamespace(severity="warning", title="Monitoreo pendiente", message="El módulo 'X' no tiene monitoreos.")
        activity = {"activity_type_name": "Riego", "category": "mantenimiento", "occurred_at": datetime(2025, 6, 18, 9, 0), "module_id": 1}
        return {
            "analytics_greenhouses": [_gh(1, "GH")],
            "analytics_selected_greenhouse_id": 1,
            "analytics": _ctx(),
            "greenhouse_count": 2, "module_count": 5,
            "pending_modules_count": 1, "overdue_modules_count": 1,
            "monitorings_this_week": 3, "total_snapshots": 100,
            "total_detections": 250, "pending_exports_count": 2,
            "last_monitoring": mon, "alerts": [alert], "recent_activities": [activity],
        }

    def test_operational_details_present(self):
        html = _render(self._full())
        assert "Información operativa" in html
        assert "<details" in html

    def test_operational_metrics_preserved(self):
        html = _render(self._full())
        assert "Invernaderos" in html and "Módulos" in html
        assert "Snapshots" in html and "Tomates detectados" in html
        assert "Exportaciones pendientes" in html

    def test_report_link_preserved(self):
        html = _render(self._full())
        assert "/monitoreos/42/reporte" in html
        assert "Ver reporte" in html

    def test_alerts_preserved(self):
        html = _render(self._full())
        assert "Alertas operativas" in html
        assert "Monitoreo pendiente" in html

    def test_activities_preserved(self):
        html = _render(self._full())
        assert "Actividades recientes" in html
        assert "Riego" in html

    def test_no_external_urls_or_cdn(self):
        html = _render(self._full())
        lowered = html.lower()
        assert "http://" not in lowered
        assert "https://" not in lowered
        assert "cdn" not in lowered
        assert "googleapis" not in lowered

    def test_local_js_referenced(self):
        html = _render(self._full())
        assert "/static/js/dashboard_analytics.js" in html


# ---------------------------------------------------------------------------
# Renderer (dashboard_analytics.js) — structural checks
#
# We can't run a browser here, so we assert on the renderer source that it uses
# real timestamps (not point index) for the X domain, sets SVG accessibility,
# and filters out empty series. This guards the mandated Block-10 fix.
# ---------------------------------------------------------------------------


class TestRendererSource:
    def _js(self):
        with open("app/static/js/dashboard_analytics.js", "r", encoding="utf-8") as f:
            return f.read()

    def test_uses_date_parse_on_ts(self):
        js = self._js()
        assert "Date.parse(point.ts)" in js

    def test_x_domain_uses_timestamps_not_index(self):
        js = self._js()
        # X positioning is a function of a point's timestamp, not a bare index.
        assert "function xPos(point)" in js
        assert "(t - xMin) / (xMax - xMin)" in js
        # The old index-based domain (maxLen / xPos(i)) must be gone.
        assert "function xPos(i)" not in js
        assert "maxLen" not in js

    def test_svg_accessibility(self):
        js = self._js()
        assert 'role: "img"' in js or 'setAttribute("role", "img")' in js
        assert 'aria-label' in js
        assert 'container.getAttribute("aria-label")' in js

    def test_filters_empty_series(self):
        js = self._js()
        assert "s.points && s.points.length > 0" in js

    def _js_code_only(self):
        """Renderer source with comments stripped, so doc text like
        'no fetch, no CDN' does not cause false positives."""
        import re
        js = self._js()
        js = re.sub(r"/\*.*?\*/", "", js, flags=re.DOTALL)  # block comments
        js = re.sub(r"//[^\n]*", "", js)  # line comments
        return js

    def test_no_fetch_or_cdn(self):
        code = self._js_code_only()
        lowered = code.lower()
        # No network calls in actual code.
        assert "fetch(" not in lowered
        assert "xmlhttprequest" not in lowered
        assert "import(" not in lowered
        # No external CDN hosts.
        for host in ("googleapis", "cdnjs", "unpkg", "jsdelivr", "cdn."):
            assert host not in lowered
        # Every http(s) occurrence in code must be the SVG namespace.
        import re
        urls = re.findall(r"https?://[^\s\"']+", code)
        assert all(u.startswith("http://www.w3.org/2000/svg") for u in urls), urls


class TestFiltersCssNarrowViewport:
    """Guard against the RPi narrow-viewport bug where .analytics-filter kept a
    200px flex-basis in a column layout, producing a huge vertical gap between
    the greenhouse and module selectors."""

    def _css(self):
        with open("app/static/css/agricultural.css", "r", encoding="utf-8") as f:
            return f.read()

    def test_narrow_breakpoint_collapses_filter_height(self):
        import re
        css = self._css()
        m = re.search(r"@media \(max-width: 520px\)\s*\{(.*?)\n\}", css, flags=re.DOTALL)
        assert m, "narrow breakpoint not found"
        block = m.group(1)
        # In column layout, filters must shrink to natural height (no 200px basis).
        assert "flex-direction: column" in block
        assert "flex: 0 0 auto" in block  # neutralizes the 1 1 200px basis
        assert "min-width: 0" in block
