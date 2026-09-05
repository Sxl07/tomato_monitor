"""Static architecture boundary tests.

Verifies that architectural rules documented in steering files are
enforced in the codebase. These tests read source files as text and
check for prohibited imports or missing documentation — they do NOT
import any heavy modules (no torch, cv2, detectron2).

Covers:
- Domain layer isolation (no FastAPI, cv2, torch, detectron2, sqlalchemy)
- Presentation layer isolation (no detectron2, torch, cv2 direct imports)
- CaptureWorker contains no inference code
- SnapshotAnalysisService has no top-level heavy imports
- MonitoringService uses CaptureWorker, not MonitoringWorker
- Steering documentation contains required governance rules
"""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
APP_DIR = PROJECT_ROOT / "app"
STEERING_DIR = PROJECT_ROOT / ".kiro" / "steering"


def _read_all_python_files(directory: Path) -> list[tuple[Path, str]]:
    """Read all .py files in a directory tree, returning (path, content) pairs."""
    results = []
    if not directory.exists():
        return results
    for py_file in directory.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
            results.append((py_file, content))
        except (OSError, UnicodeDecodeError):
            continue
    return results


def _get_import_lines(content: str) -> list[str]:
    """Extract lines that are import statements."""
    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            lines.append(stripped)
    return lines


# --- Domain layer isolation ---


class TestDomainLayerIsolation:
    """src/domain/ must not import external frameworks."""

    @pytest.fixture(scope="class")
    def domain_files(self) -> list[tuple[Path, str]]:
        return _read_all_python_files(SRC_DIR / "domain")

    def test_domain_does_not_import_fastapi(self, domain_files):
        for path, content in domain_files:
            imports = _get_import_lines(content)
            for imp in imports:
                assert "fastapi" not in imp.lower(), (
                    f"{path.relative_to(PROJECT_ROOT)} imports fastapi: {imp}"
                )

    def test_domain_does_not_import_cv2(self, domain_files):
        for path, content in domain_files:
            imports = _get_import_lines(content)
            for imp in imports:
                assert "import cv2" not in imp and "from cv2" not in imp, (
                    f"{path.relative_to(PROJECT_ROOT)} imports cv2: {imp}"
                )

    def test_domain_does_not_import_torch(self, domain_files):
        for path, content in domain_files:
            imports = _get_import_lines(content)
            for imp in imports:
                assert "import torch" not in imp and "from torch" not in imp, (
                    f"{path.relative_to(PROJECT_ROOT)} imports torch: {imp}"
                )

    def test_domain_does_not_import_detectron2(self, domain_files):
        for path, content in domain_files:
            imports = _get_import_lines(content)
            for imp in imports:
                assert "detectron2" not in imp, (
                    f"{path.relative_to(PROJECT_ROOT)} imports detectron2: {imp}"
                )

    def test_domain_does_not_import_sqlalchemy(self, domain_files):
        for path, content in domain_files:
            imports = _get_import_lines(content)
            for imp in imports:
                assert "sqlalchemy" not in imp, (
                    f"{path.relative_to(PROJECT_ROOT)} imports sqlalchemy: {imp}"
                )


# --- Presentation layer isolation ---


class TestPresentationLayerIsolation:
    """app/routes/ must not import heavy ML frameworks directly."""

    @pytest.fixture(scope="class")
    def route_files(self) -> list[tuple[Path, str]]:
        return _read_all_python_files(APP_DIR / "routes")

    def test_routes_do_not_import_detectron2(self, route_files):
        for path, content in route_files:
            imports = _get_import_lines(content)
            for imp in imports:
                assert "detectron2" not in imp, (
                    f"{path.relative_to(PROJECT_ROOT)} imports detectron2: {imp}"
                )

    def test_routes_do_not_import_torch(self, route_files):
        for path, content in route_files:
            imports = _get_import_lines(content)
            for imp in imports:
                assert "import torch" not in imp and "from torch" not in imp, (
                    f"{path.relative_to(PROJECT_ROOT)} imports torch: {imp}"
                )

    def test_routes_do_not_import_cv2_directly(self, route_files):
        for path, content in route_files:
            imports = _get_import_lines(content)
            for imp in imports:
                assert "import cv2" not in imp and "from cv2" not in imp, (
                    f"{path.relative_to(PROJECT_ROOT)} imports cv2: {imp}"
                )


# --- CaptureWorker isolation ---


class TestCaptureWorkerIsolation:
    """CaptureWorker must contain NO inference code."""

    @pytest.fixture(scope="class")
    def capture_worker_content(self) -> str:
        path = SRC_DIR / "application" / "services" / "capture_worker.py"
        assert path.exists(), "capture_worker.py not found"
        return path.read_text(encoding="utf-8")

    def test_no_snapshot_inference_runner(self, capture_worker_content):
        assert "SnapshotInferenceRunner" not in capture_worker_content

    def test_no_process_frame(self, capture_worker_content):
        assert "process_frame" not in capture_worker_content

    def test_no_build_pipeline_components(self, capture_worker_content):
        assert "build_pipeline_components" not in capture_worker_content


# --- SnapshotAnalysisService module-level isolation ---


class TestSnapshotAnalysisServiceIsolation:
    """SnapshotAnalysisService must not import detectron2/torch at module level."""

    @pytest.fixture(scope="class")
    def analysis_service_content(self) -> str:
        path = SRC_DIR / "application" / "services" / "snapshot_analysis_service.py"
        assert path.exists(), "snapshot_analysis_service.py not found"
        return path.read_text(encoding="utf-8")

    def test_no_top_level_detectron2_import(self, analysis_service_content):
        # Check only top-level imports (not inside functions)
        imports = _get_import_lines(analysis_service_content)
        for imp in imports:
            assert "detectron2" not in imp, (
                f"snapshot_analysis_service.py has top-level detectron2 import: {imp}"
            )

    def test_no_top_level_torch_import(self, analysis_service_content):
        imports = _get_import_lines(analysis_service_content)
        for imp in imports:
            assert "import torch" not in imp and "from torch" not in imp, (
                f"snapshot_analysis_service.py has top-level torch import: {imp}"
            )


# --- Video-first application modules isolation (Spec 019, Task 15.1) ---


class TestVideoFirstApplicationIsolation:
    """video_analysis_service and video_recording_worker (application) must not
    import cv2/torch/detectron2 at module level. The cv2 dependency is confined
    to the infrastructure adapters (OpenCvVideoReader / VideoRecorder /
    VideoFileFrameSource)."""

    @pytest.fixture(scope="class")
    def video_analysis_content(self) -> str:
        path = SRC_DIR / "application" / "services" / "video_analysis_service.py"
        assert path.exists(), "video_analysis_service.py not found"
        return path.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    def video_worker_content(self) -> str:
        path = SRC_DIR / "application" / "services" / "video_recording_worker.py"
        assert path.exists(), "video_recording_worker.py not found"
        return path.read_text(encoding="utf-8")

    def test_video_analysis_no_top_level_cv2(self, video_analysis_content):
        for imp in _get_import_lines(video_analysis_content):
            assert "import cv2" not in imp and "from cv2" not in imp, (
                f"video_analysis_service.py has top-level cv2 import: {imp}"
            )

    def test_video_analysis_no_top_level_torch(self, video_analysis_content):
        for imp in _get_import_lines(video_analysis_content):
            assert "import torch" not in imp and "from torch" not in imp, (
                f"video_analysis_service.py has top-level torch import: {imp}"
            )

    def test_video_analysis_no_top_level_detectron2(self, video_analysis_content):
        for imp in _get_import_lines(video_analysis_content):
            assert "detectron2" not in imp, (
                f"video_analysis_service.py has top-level detectron2 import: {imp}"
            )

    def test_video_worker_no_top_level_cv2(self, video_worker_content):
        for imp in _get_import_lines(video_worker_content):
            assert "import cv2" not in imp and "from cv2" not in imp, (
                f"video_recording_worker.py has top-level cv2 import: {imp}"
            )

    def test_video_worker_no_top_level_torch(self, video_worker_content):
        for imp in _get_import_lines(video_worker_content):
            assert "import torch" not in imp and "from torch" not in imp, (
                f"video_recording_worker.py has top-level torch import: {imp}"
            )

    def test_video_worker_no_top_level_detectron2(self, video_worker_content):
        for imp in _get_import_lines(video_worker_content):
            assert "detectron2" not in imp, (
                f"video_recording_worker.py has top-level detectron2 import: {imp}"
            )

    def test_video_file_frame_source_infra_owns_cv2(self):
        """VideoFileFrameSource (infrastructure) is an allowed home for cv2."""
        path = SRC_DIR / "infrastructure" / "camera" / "video_file_frame_source.py"
        assert path.exists(), "video_file_frame_source.py not found"
        content = path.read_text(encoding="utf-8")
        assert any(
            imp == "import cv2" or imp.startswith("import cv2")
            for imp in _get_import_lines(content)
        ), "video_file_frame_source.py should import cv2 (infra confinement)"


# --- MonitoringService wiring ---


class TestMonitoringServiceWiring:
    """MonitoringService uses CaptureWorker, NOT MonitoringWorker."""

    @pytest.fixture(scope="class")
    def monitoring_service_content(self) -> str:
        path = SRC_DIR / "application" / "services" / "monitoring_service.py"
        assert path.exists(), "monitoring_service.py not found"
        return path.read_text(encoding="utf-8")

    def test_imports_capture_worker(self, monitoring_service_content):
        assert "CaptureWorker" in monitoring_service_content

    def test_does_not_import_monitoring_worker(self, monitoring_service_content):
        imports = _get_import_lines(monitoring_service_content)
        for imp in imports:
            assert "MonitoringWorker" not in imp, (
                f"monitoring_service.py imports MonitoringWorker: {imp}"
            )


# --- VideoReaderPort boundary (Spec 019, Task 2) ---


class TestVideoReaderPortBoundary:
    """The VideoReaderPort application interface must not import cv2/torch/detectron2.

    The OpenCV dependency for video reading must be confined to the
    infrastructure adapter OpenCvVideoReader.
    """

    @pytest.fixture(scope="class")
    def port_content(self) -> str:
        path = SRC_DIR / "application" / "interfaces" / "video_reader_port.py"
        assert path.exists(), "video_reader_port.py not found"
        return path.read_text(encoding="utf-8")

    def test_port_does_not_import_cv2(self, port_content):
        imports = _get_import_lines(port_content)
        for imp in imports:
            assert "import cv2" not in imp and "from cv2" not in imp, (
                f"video_reader_port.py imports cv2: {imp}"
            )

    def test_port_does_not_import_torch(self, port_content):
        imports = _get_import_lines(port_content)
        for imp in imports:
            assert "import torch" not in imp and "from torch" not in imp, (
                f"video_reader_port.py imports torch: {imp}"
            )

    def test_port_does_not_import_detectron2(self, port_content):
        imports = _get_import_lines(port_content)
        for imp in imports:
            assert "detectron2" not in imp, (
                f"video_reader_port.py imports detectron2: {imp}"
            )

    def test_port_defines_video_reader_error(self, port_content):
        """VideoReaderError is part of the application-level port contract."""
        assert "class VideoReaderError" in port_content, (
            "VideoReaderError must be defined in video_reader_port.py (application contract)"
        )

    def test_opencv_reader_adapter_imports_cv2(self):
        """The infrastructure adapter is the place that owns the cv2 import."""
        path = SRC_DIR / "infrastructure" / "camera" / "opencv_video_reader.py"
        assert path.exists(), "opencv_video_reader.py not found"
        content = path.read_text(encoding="utf-8")
        imports = _get_import_lines(content)
        assert any(imp == "import cv2" or imp.startswith("import cv2") for imp in imports), (
            "opencv_video_reader.py should import cv2 (it confines the OpenCV dependency)"
        )


class TestCameraServiceBoundary:
    """CameraService (application) must not import cv2 (Spec 019, Task 14).

    JPEG encoding is delegated to the infrastructure helper
    ``src/infrastructure/camera/jpeg_encoder.py``, which owns the cv2 import.
    """

    @pytest.fixture(scope="class")
    def camera_service_content(self) -> str:
        path = SRC_DIR / "application" / "services" / "camera_service.py"
        assert path.exists(), "camera_service.py not found"
        return path.read_text(encoding="utf-8")

    def test_camera_service_does_not_import_cv2(self, camera_service_content):
        imports = _get_import_lines(camera_service_content)
        for imp in imports:
            assert "import cv2" not in imp and "from cv2" not in imp, (
                f"camera_service.py must not import cv2: {imp}"
            )

    def test_jpeg_encoder_infra_owns_cv2(self):
        """The infrastructure JPEG encoder is the place that owns the cv2 import."""
        path = SRC_DIR / "infrastructure" / "camera" / "jpeg_encoder.py"
        assert path.exists(), "jpeg_encoder.py not found"
        content = path.read_text(encoding="utf-8")
        imports = _get_import_lines(content)
        assert any(imp == "import cv2" or imp.startswith("import cv2") for imp in imports), (
            "jpeg_encoder.py should import cv2 (it confines the OpenCV JPEG encoding)"
        )


# --- Steering documentation governance ---


class TestSteeringGovernance:
    """Steering files contain required governance rules."""

    def test_architecture_contains_single_camera_owner(self):
        path = STEERING_DIR / "architecture.md"
        assert path.exists(), "architecture.md not found"
        content = path.read_text(encoding="utf-8")
        assert "Single Camera Owner" in content

    def test_architecture_contains_robot_orchestrator(self):
        path = STEERING_DIR / "architecture.md"
        content = path.read_text(encoding="utf-8")
        assert "RobotOrchestrator" in content

    def test_ai_vision_pipeline_contains_snapshot_analysis_service(self):
        path = STEERING_DIR / "ai-vision-pipeline.md"
        assert path.exists(), "ai-vision-pipeline.md not found"
        content = path.read_text(encoding="utf-8")
        assert "SnapshotAnalysisService" in content

    def test_ux_design_contains_analyzing(self):
        path = STEERING_DIR / "ux-design.md"
        assert path.exists(), "ux-design.md not found"
        content = path.read_text(encoding="utf-8")
        assert "ANALYZING" in content

    def test_data_model_contains_analyzing(self):
        path = STEERING_DIR / "data-model.md"
        assert path.exists(), "data-model.md not found"
        content = path.read_text(encoding="utf-8")
        assert "analyzing" in content
