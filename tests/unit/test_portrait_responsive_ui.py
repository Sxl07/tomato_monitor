"""
Tests for portrait responsive UI (Task 10 — Spec 015).

Validates structural correctness of HTML templates and CSS
for Raspberry Pi vertical (480×800) usage.
"""

import os
import re

import pytest

# Paths relative to project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE_TEMPLATE = os.path.join(
    PROJECT_ROOT, "app", "templates", "base_agricultural.html"
)
CSS_FILE = os.path.join(
    PROJECT_ROOT, "app", "static", "css", "agricultural.css"
)
MONITORING_TEMPLATE = os.path.join(
    PROJECT_ROOT, "app", "templates", "agricultural", "monitoring_execution.html"
)
CHECKLIST_DOC = os.path.join(
    PROJECT_ROOT, "docs", "raspberry-portrait-ui-checklist.md"
)
TEMPLATES_DIR = os.path.join(PROJECT_ROOT, "app", "templates")


@pytest.fixture
def base_template_content():
    with open(BASE_TEMPLATE, "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def css_content():
    with open(CSS_FILE, "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def monitoring_template_content():
    with open(MONITORING_TEMPLATE, "r", encoding="utf-8") as f:
        return f.read()


class TestViewportMeta:
    """Verify viewport meta tag is set correctly for responsive behavior."""

    def test_base_template_has_device_width_viewport(self, base_template_content):
        assert 'width=device-width' in base_template_content, (
            "base_agricultural.html must use width=device-width for responsive behavior"
        )

    def test_base_template_does_not_have_fixed_800_viewport(self, base_template_content):
        assert 'width=800' not in base_template_content, (
            "base_agricultural.html must NOT use width=800 (fixed landscape)"
        )


class TestBottomNavigation:
    """Verify bottom navigation exists with expected structure."""

    def test_bottom_nav_exists(self, base_template_content):
        assert 'class="bottom-nav"' in base_template_content, (
            "base_agricultural.html must contain bottom-nav element"
        )

    def test_bottom_nav_has_aria_label(self, base_template_content):
        assert 'aria-label="Navegación principal"' in base_template_content

    def test_bottom_nav_has_dashboard_link(self, base_template_content):
        assert 'href="/dashboard"' in base_template_content

    def test_bottom_nav_has_greenhouses_link(self, base_template_content):
        assert 'href="/invernaderos"' in base_template_content

    def test_bottom_nav_has_export_link(self, base_template_content):
        assert 'href="/exportar"' in base_template_content

    def test_bottom_nav_has_sync_link(self, base_template_content):
        assert 'href="/sincronizacion"' in base_template_content

    def test_bottom_nav_items_have_class(self, base_template_content):
        items = re.findall(r'class="bottom-nav__item"', base_template_content)
        assert len(items) == 4, "Expected exactly 4 bottom-nav items"


class TestPortraitCSS:
    """Verify CSS contains portrait media queries and key responsive rules."""

    def test_has_portrait_media_query(self, css_content):
        assert "@media" in css_content
        assert "orientation: portrait" in css_content

    def test_has_max_width_600_media_query(self, css_content):
        assert "max-width: 600px" in css_content

    def test_dashboard_metrics_column_layout(self, css_content):
        # Inside media query, dashboard-metrics should be flex-direction: column
        assert ".dashboard-metrics" in css_content
        assert "flex-direction: column" in css_content

    def test_bottom_nav_hidden_by_default(self, css_content):
        # The .bottom-nav rule outside media query should have display: none
        # Find the first .bottom-nav rule (outside @media)
        pattern = r'\.bottom-nav\s*\{[^}]*display:\s*none'
        assert re.search(pattern, css_content), (
            "bottom-nav must be display:none by default (landscape)"
        )

    def test_bottom_nav_shown_in_portrait(self, css_content):
        # Inside @media, .bottom-nav should have display: flex
        pattern = r'@media[^{]*\{[^}]*\.bottom-nav\s*\{[^}]*display:\s*flex'
        assert re.search(pattern, css_content), (
            "bottom-nav must be display:flex inside portrait media query"
        )

    def test_main_content_padding_bottom(self, css_content):
        assert "padding-bottom: 72px" in css_content, (
            "main-content needs padding-bottom to avoid bottom-nav overlap"
        )

    def test_touch_targets_min_height(self, css_content):
        assert "min-height: var(--touch-min)" in css_content, (
            "Interactive elements must have min-height: var(--touch-min) (44px)"
        )

    def test_forms_full_width(self, css_content):
        # Inside the media query, form inputs should be width: 100%
        assert 'input[type="text"]' in css_content
        assert 'input[type="email"]' in css_content
        assert 'input[type="password"]' in css_content

    def test_execution_actions_stacked(self, css_content):
        # .execution-actions flex-direction: column
        pattern = r'\.execution-actions\s*\{[^}]*flex-direction:\s*column'
        assert re.search(pattern, css_content), (
            "execution-actions must stack vertically in portrait"
        )


class TestMonitoringExecutionPreserved:
    """Verify monitoring execution template preserves essential buttons."""

    def test_has_finalizar_captura_button(self, monitoring_template_content):
        assert "Finalizar captura" in monitoring_template_content

    def test_has_cancelar_monitoreo_button(self, monitoring_template_content):
        assert "Cancelar monitoreo" in monitoring_template_content

    def test_finalize_and_cancel_are_separate(self, monitoring_template_content):
        # Both should exist as separate buttons
        finalizar_count = monitoring_template_content.count("Finalizar captura")
        cancelar_count = monitoring_template_content.count("Cancelar monitoreo")
        assert finalizar_count >= 1
        assert cancelar_count >= 1


class TestNoExternalFrameworks:
    """Verify no external CSS frameworks are introduced."""

    def test_no_bootstrap_in_css(self, css_content):
        assert "bootstrap" not in css_content.lower()

    def test_no_tailwind_in_css(self, css_content):
        assert "tailwind" not in css_content.lower()

    def test_no_cdn_in_base_template(self, base_template_content):
        assert "cdn." not in base_template_content.lower()
        assert "cdnjs" not in base_template_content.lower()
        assert "unpkg" not in base_template_content.lower()


class TestNoRobotLanguage:
    """Verify no robot/chassis/motor terms in active templates."""

    FORBIDDEN_TERMS = ["robot", "chassis", "motor", "bts7960", "navegación autónoma"]

    def test_base_template_no_robot_terms(self, base_template_content):
        content_lower = base_template_content.lower()
        for term in self.FORBIDDEN_TERMS:
            assert term not in content_lower, (
                f"base_agricultural.html must not contain '{term}'"
            )

    def test_monitoring_template_no_robot_terms(self, monitoring_template_content):
        content_lower = monitoring_template_content.lower()
        for term in self.FORBIDDEN_TERMS:
            assert term not in content_lower, (
                f"monitoring_execution.html must not contain '{term}'"
            )


class TestDocumentation:
    """Verify documentation exists."""

    def test_checklist_doc_exists(self):
        assert os.path.isfile(CHECKLIST_DOC), (
            "docs/raspberry-portrait-ui-checklist.md must exist"
        )


class TestPortraitStackedButtonMarginReset:
    """Verify stacked buttons in portrait don't have conflicting margin-left."""

    def test_portrait_resets_stacked_button_margin_left(self, css_content):
        """CSS resets margin-left on stacked buttons inside portrait media query."""
        assert "margin-left: 0" in css_content
        assert ".quick-links .btn + .btn" in css_content
        assert ".form-actions .btn + .btn" in css_content
        assert ".execution-actions .btn + .btn" in css_content
        assert ".bottom-nav__item + .bottom-nav__item" in css_content


class TestLogoutFormInLayout:
    """Verify the base layout contains a POST logout form."""

    def test_logout_form_present(self, base_template_content):
        """base_agricultural.html must have a form POST to /logout."""
        assert 'action="/logout"' in base_template_content
        assert 'method="post"' in base_template_content

    def test_logout_button_text(self, base_template_content):
        """The logout button must display 'Cerrar sesión'."""
        assert "Cerrar sesión" in base_template_content
