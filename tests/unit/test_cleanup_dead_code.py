"""Static verification tests for dead code cleanup (task 10.1).

Validates:
- _build_inference_runner removed from agricultural_ui.py
- monitoring_start doesn't reference inference components
- dependencies.py has no duplicate function definitions
- MonitoringService docstring updated
- Legacy files (monitoring_worker, snapshot_inference_runner) preserved
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_AG_UI_PATH = _PROJECT_ROOT / "app" / "routes" / "agricultural_ui.py"
_DEPS_PATH = _PROJECT_ROOT / "app" / "dependencies.py"
_SERVICE_PATH = _PROJECT_ROOT / "src" / "application" / "services" / "monitoring_service.py"


@pytest.fixture(scope="module")
def ag_ui_source():
    return _AG_UI_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def deps_source():
    return _DEPS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def service_source():
    return _SERVICE_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. agricultural_ui.py no longer contains inference helper
# ---------------------------------------------------------------------------


class TestAgUiNoInference:

    def test_no_build_inference_runner(self, ag_ui_source):
        assert "def _build_inference_runner" not in ag_ui_source

    def test_no_snapshot_inference_runner(self, ag_ui_source):
        assert "SnapshotInferenceRunner" not in ag_ui_source

    def test_no_build_tomato_detector(self, ag_ui_source):
        assert "build_tomato_detector" not in ag_ui_source

    def test_no_build_health_model_resnet(self, ag_ui_source):
        assert "build_health_model_resnet" not in ag_ui_source

    def test_no_health_model_b_path(self, ag_ui_source):
        assert "HEALTH_MODEL_B_PATH" not in ag_ui_source


# ---------------------------------------------------------------------------
# 2. monitoring_start doesn't reference inference
# ---------------------------------------------------------------------------


class TestMonitoringStartClean:

    def test_no_build_inference_runner_call(self, ag_ui_source):
        tree = ast.parse(ag_ui_source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "monitoring_start":
                fn_src = ast.get_source_segment(ag_ui_source, node)
                assert "_build_inference_runner" not in fn_src
                assert "inference_runner=" not in fn_src
                assert "SnapshotInferenceRunner" not in fn_src
                assert "build_tomato_detector" not in fn_src
                assert "build_health_model_resnet" not in fn_src
                return
        pytest.fail("monitoring_start function not found")


# ---------------------------------------------------------------------------
# 3. dependencies.py — each function defined exactly once
# ---------------------------------------------------------------------------


class TestDepsNoDuplicates:

    def test_each_function_defined_once(self, deps_source):
        tree = ast.parse(deps_source)
        fn_names = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef):
                fn_names.append(node.name)

        required = [
            "get_db_session",
            "get_greenhouse_repository",
            "get_module_repository",
            "get_monitoring_repository",
            "get_snapshot_repository",
            "get_inspection_result_repository",
            "get_monitoring_metrics_repository",
            "get_monitoring_service",
            "_get_request_session",
        ]

        for name in required:
            count = fn_names.count(name)
            assert count == 1, (
                f"{name} defined {count} time(s) in dependencies.py (expected 1)"
            )

    def test_get_monitoring_service_uses_request_session(self, deps_source):
        assert "_get_request_session(request)" in deps_source

    def test_get_monitoring_service_uses_registry(self, deps_source):
        assert "request.app.state.monitoring_runtime_registry" in deps_source


# ---------------------------------------------------------------------------
# 5. MonitoringService docstring
# ---------------------------------------------------------------------------


class TestServiceDocstring:

    def test_no_monitoring_worker_in_docstring(self, service_source):
        # Check only the module docstring (first triple-quote block)
        first_doc_end = service_source.index('"""', 3)
        docstring = service_source[:first_doc_end]
        assert "MonitoringWorker" not in docstring

    def test_imports_capture_worker(self, service_source):
        assert "from src.application.services.capture_worker import CaptureWorker" in service_source


# ---------------------------------------------------------------------------
# 7. Legacy files preserved
# ---------------------------------------------------------------------------


class TestLegacyPreserved:

    def test_monitoring_worker_exists(self):
        path = _PROJECT_ROOT / "src" / "application" / "services" / "monitoring_worker.py"
        assert path.exists()

    def test_snapshot_inference_runner_exists(self):
        path = _PROJECT_ROOT / "src" / "infrastructure" / "vision" / "snapshot_inference_runner.py"
        assert path.exists()
