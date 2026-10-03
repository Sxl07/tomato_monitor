"""Shared operational alert loading and header presentation."""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader

from app.dependencies import require_current_user_api, require_current_user_html
from app.main import app
from app.operational_alerts import format_operational_alert
from src.domain.entities.operational_alert import OperationalAlert


@pytest.mark.parametrize(
    ("alert_type", "module_id", "monitoring_id", "expected_url"),
    [
        ("monitoring_pending", 12, None, "/modulos/12"),
        ("monitoring_overdue", 12, None, "/modulos/12"),
        ("analysis_error", 12, 34, "/monitoreos/34/reporte"),
        ("analysis_error", None, 34, "/monitoreos/34/reporte"),
        ("analysis_error", 12, None, "/modulos/12"),
        ("export_pending", 12, 34, None),
        ("unrecognized", 12, 34, None),
        ("monitoring_pending", None, None, None),
        ("analysis_error", None, None, None),
    ],
)
def test_alert_presentation_url_mapping(alert_type, module_id, monitoring_id, expected_url):
    alert = OperationalAlert(
        alert_type=alert_type,
        severity="warning",
        title="Existing title",
        message="Existing message",
        module_id=module_id,
        monitoring_id=monitoring_id,
    )

    assert format_operational_alert(alert) == {
        "severity": "warning",
        "title": "Existing title",
        "message": "Existing message",
        "url": expected_url,
    }


def test_authenticated_agricultural_page_renders_header_bell():
    app.dependency_overrides[require_current_user_html] = lambda: SimpleNamespace(id=1)
    try:
        with TestClient(app) as client:
            response = client.get("/invernaderos/crear")
    finally:
        app.dependency_overrides.pop(require_current_user_html, None)

    assert response.status_code == 200
    assert 'id="header-alerts"' in response.text
    assert 'aria-label="Alertas operativas"' in response.text
    assert 'data-loaded="false"' in response.text


def test_header_endpoint_counts_full_owner_scoped_list():
    greenhouses = {
        1: SimpleNamespace(id=11, name="Owner A"),
        2: SimpleNamespace(id=22, name="Owner B"),
    }
    modules = {
        11: [SimpleNamespace(
            id=index, greenhouse_id=11, name=f"A module {index}",
            crop_type="Tomate Cherry", monitoring_frequency_days=7,
        ) for index in range(1, 8)],
        22: [SimpleNamespace(
            id=20, greenhouse_id=22, name="B module",
            crop_type="Lechuga", monitoring_frequency_days=None,
        )],
    }
    greenhouse_repo = Mock()
    greenhouse_repo.get_all_by_owner.side_effect = lambda uid: [greenhouses[uid]]
    module_repo = Mock()
    module_repo.get_by_greenhouse.side_effect = lambda gid: modules[gid]
    monitoring_repo = Mock()
    monitoring_repo.get_by_module.side_effect = lambda mid: (
        [SimpleNamespace(id=101, status="error", started_at=datetime(2025, 1, 1))]
        if mid == 1 else []
    )
    export_repo = Mock()
    export_repo.list_by_user.return_value = []

    current_user = SimpleNamespace(id=1)
    app.dependency_overrides[require_current_user_api] = lambda: current_user
    try:
        with patch("app.operational_alerts.get_greenhouse_repository", return_value=greenhouse_repo), \
             patch("app.operational_alerts.get_module_repository", return_value=module_repo), \
             patch("app.operational_alerts.get_monitoring_repository", return_value=monitoring_repo), \
             patch("app.operational_alerts.get_export_package_repository", return_value=export_repo):
            with TestClient(app) as client:
                a = client.get("/api/operational-alerts")
                current_user.id = 2
                b = client.get("/api/operational-alerts")
    finally:
        app.dependency_overrides.pop(require_current_user_api, None)

    assert a.status_code == b.status_code == 200
    assert a.json()["count"] == 8  # Seven pending + one existing error alert.
    assert len(a.json()["alerts"]) == 5
    assert a.json()["alerts"][0]["severity"] == "critical"
    assert a.json()["alerts"][0]["url"] == "/monitoreos/101/reporte"
    assert all(item["url"] is not None for item in a.json()["alerts"])
    assert all("Owner B" not in item["message"] for item in a.json()["alerts"])
    assert b.json() == {"count": 0, "alerts": []}
    assert greenhouse_repo.get_all_by_owner.call_args_list[0].args == (1,)
    assert greenhouse_repo.get_all_by_owner.call_args_list[1].args == (2,)
    assert [call.args for call in export_repo.list_by_user.call_args_list] == [(1,), (2,)]


def test_header_markup_is_touch_friendly_and_links_to_operational_section():
    base = Path("app/templates/base_agricultural.html").read_text(encoding="utf-8")
    css = Path("app/static/css/operational_alerts.css").read_text(encoding="utf-8")
    script = Path("app/static/js/operational_alerts.js").read_text(encoding="utf-8")

    assert 'id="header-alerts"' in base
    assert 'aria-label="Alertas operativas"' in base
    assert '/static/css/operational_alerts.css?v=20261002-2' in base
    assert '/static/js/operational_alerts.js?v=20261002-2' in base
    assert base.index('id="header-alerts"') < base.index('action="/logout"')
    assert 'href="/dashboard#informacion-operativa"' in base
    assert 'width: 44px' in css and 'height: 44px' in css
    assert 'right: 0' in css and 'calc(100vw - 32px)' in css
    assert 'scroll-margin-top: 72px' in css
    assert 'data.count > 99 ? "99+"' in script
    assert 'localStorage' not in script and 'sessionStorage' not in script


def test_server_rendered_header_has_no_zero_badge_and_limits_preview():
    template = Environment(loader=FileSystemLoader("app/templates")).get_template(
        "base_agricultural.html"
    )
    empty = template.render(header_alert_count=0, header_alerts=[])
    alerts = [SimpleNamespace(severity="warning", title=f"Alert {i}", message="Existing message")
              for i in range(5)]
    populated = template.render(header_alert_count=105, header_alerts=alerts)

    assert 'id="header-alert-count" hidden' in empty
    assert 'id="header-alert-count" >99+</span>' in populated
    assert populated.count('class="header-alerts__item ') == 5


def test_dashboard_and_api_headers_share_formatter_and_link_markup():
    dashboard_route = Path("app/routes/agricultural_ui.py").read_text(encoding="utf-8")
    api_route = Path("app/routes/operational_alert_api.py").read_text(encoding="utf-8")
    script = Path("app/static/js/operational_alerts.js").read_text(encoding="utf-8")
    css = Path("app/static/css/operational_alerts.css").read_text(encoding="utf-8")
    assert '"header_alerts": [format_operational_alert(alert) for alert in full_alerts[:5]]' in dashboard_route
    assert '"alerts": [format_operational_alert(alert) for alert in alerts[:5]]' in api_route

    template = Environment(loader=FileSystemLoader("app/templates")).get_template(
        "base_agricultural.html"
    )
    linked = OperationalAlert(
        alert_type="monitoring_pending", severity="warning",
        title="Module alert", message="Open module", module_id=12,
    )
    unlinked = OperationalAlert(
        alert_type="export_pending", severity="info",
        title="Export alert", message="No target",
    )
    rendered = template.render(
        header_alert_count=2,
        header_alerts=[format_operational_alert(linked), format_operational_alert(unlinked)],
    )

    assert '<a class="header-alerts__item header-alerts__item--warning" href="/modulos/12">' in rendered
    assert '<div class="header-alerts__item header-alerts__item--info">' in rendered
    assert 'href="None"' not in rendered
    assert 'document.createElement(alert.url ? "a" : "div")' in script
    assert 'if (alert.url) item.href = alert.url;' in script
    assert 'a.header-alerts__item {' in css
    assert 'min-height: 44px' in css
    assert 'text-decoration: none' in css
    assert 'a.header-alerts__item:focus-visible' in css


def test_loader_keeps_dashboard_pending_only_export_input():
    from app.operational_alerts import load_operational_alerts

    exports = Mock()
    exports.list_by_user.return_value = [
        SimpleNamespace(status="pending"),
        SimpleNamespace(status="generating"),
        SimpleNamespace(status="error"),
    ]
    greenhouses = Mock()
    greenhouses.get_all_by_owner.return_value = []
    with patch("app.operational_alerts.get_greenhouse_repository", return_value=greenhouses), \
         patch("app.operational_alerts.get_module_repository", return_value=Mock()), \
         patch("app.operational_alerts.get_monitoring_repository", return_value=Mock()), \
         patch("app.operational_alerts.get_export_package_repository", return_value=exports):
        alerts = load_operational_alerts(SimpleNamespace(), SimpleNamespace(id=4))

    assert len(alerts) == 1
    assert alerts[0].title == "Exportaciones pendientes"
    greenhouses.get_all_by_owner.assert_called_once_with(4)
    exports.list_by_user.assert_called_once_with(4)


def test_dashboard_hash_opens_existing_disclosure():
    dashboard = Path("app/templates/agricultural/dashboard.html").read_text(encoding="utf-8")
    assert 'id="informacion-operativa"' in dashboard
    assert 'window.location.hash !== "#informacion-operativa"' in dashboard
    assert 'window.addEventListener("hashchange", openOperationalInfo)' in dashboard
    assert 'section.open = true' in dashboard
    assert 'section.scrollIntoView({ block: "start" })' in dashboard
