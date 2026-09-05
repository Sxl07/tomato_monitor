"""Verify all application modules are importable without hardware.

Ensures that the codebase doesn't have broken imports or missing
dependencies at the module level, excluding modules that require
GPU, camera, or heavy ML frameworks at import time.
"""

import importlib
import pkgutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Modules that require hardware-specific imports (torch, detectron2, cv2)
SKIP_MODULES = {
    "src.infrastructure.vision.detectron_detector",
    "src.infrastructure.vision.resnet_health_classifier",
    "src.infrastructure.vision.maturity_estimator",
    "src.infrastructure.vision.maturity_colorimetry",
    "src.infrastructure.vision.pipeline_orchestrator",
    "src.infrastructure.vision.video_inspection_runner",
    "src.infrastructure.vision.snapshot_inference_runner",
    "src.infrastructure.config.settings",  # imports torch
    "src.infrastructure.vision.capture_gate",
    "src.infrastructure.vision.cropper",
    "src.infrastructure.vision.tracker_adapter",
    "src.infrastructure.vision.visual_tracker",
}

# Hardware-related import errors that are acceptable to skip
HARDWARE_IMPORTS = {"torch", "detectron2", "cv2", "torchvision", "numpy"}


def discover_modules(base_package: str, base_path: Path) -> list[str]:
    """Discover all Python modules under a package."""
    modules = []
    if not base_path.exists():
        return modules
    for importer, modname, ispkg in pkgutil.walk_packages(
        path=[str(base_path)],
        prefix=f"{base_package}.",
    ):
        modules.append(modname)
    return modules


def test_src_domain_modules_importable():
    """All modules under src/domain/ should be importable (no hardware deps)."""
    modules = discover_modules("src.domain", PROJECT_ROOT / "src" / "domain")
    assert len(modules) > 0, "No modules found under src/domain/"

    for mod_name in modules:
        importlib.import_module(mod_name)


def test_src_application_modules_importable():
    """All modules under src/application/ should be importable without hardware."""
    modules = discover_modules("src.application", PROJECT_ROOT / "src" / "application")
    assert len(modules) > 0, "No modules found under src/application/"

    for mod_name in modules:
        if mod_name in SKIP_MODULES:
            continue
        try:
            importlib.import_module(mod_name)
        except ImportError as e:
            err_str = str(e)
            if any(hw in err_str for hw in HARDWARE_IMPORTS):
                pytest.skip(f"Hardware dependency: {e}")
            else:
                raise


def test_src_infrastructure_modules_importable():
    """Infrastructure modules (except vision/config hardware) should be importable."""
    modules = discover_modules(
        "src.infrastructure", PROJECT_ROOT / "src" / "infrastructure"
    )
    assert len(modules) > 0, "No modules found under src/infrastructure/"

    for mod_name in modules:
        if mod_name in SKIP_MODULES:
            continue
        try:
            importlib.import_module(mod_name)
        except ImportError as e:
            err_str = str(e)
            if any(hw in err_str for hw in HARDWARE_IMPORTS):
                pytest.skip(f"Hardware dependency: {e}")
            else:
                raise


def test_video_analysis_service_importable_without_cv2_and_detectron2():
    """VideoAnalysisService must import with cv2 AND detectron2 blocked.

    Spec 019, Task 15.1: the service uses VideoReaderPort (injected) and lazy
    factories, so it must NOT pull cv2/detectron2 at module import.

    The block is exercised in a SUBPROCESS so poisoning sys.modules (cv2/
    detectron2/torch = None) cannot pollute the parent test process (other tests
    rely on the real cv2). The child forces those imports to fail and imports the
    service module fresh; a clean exit proves the import path is heavy-free.
    """
    import subprocess

    code = (
        "import sys\n"
        "for _n in ('cv2', 'detectron2', 'torch'):\n"
        "    sys.modules[_n] = None\n"  # any import of these now raises ImportError
        "import importlib\n"
        "mod = importlib.import_module('src.application.services.video_analysis_service')\n"
        "assert hasattr(mod, 'VideoAnalysisService')\n"
        "assert getattr(mod, 'cv2', None) is None\n"
        "assert getattr(mod, 'torch', None) is None\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "video_analysis_service failed to import with cv2/detectron2/torch "
        f"blocked.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "OK" in result.stdout


def test_video_reader_port_importable_without_cv2():
    """VideoReaderPort (application interface) must import without cv2/torch/detectron2.

    Spec 019, Task 2.3 (case I): the port defines only the abstract contract and
    dataclass; the OpenCV dependency lives in the infrastructure adapter.
    """
    import importlib

    mod = importlib.import_module("src.application.interfaces.video_reader_port")
    assert hasattr(mod, "VideoReaderPort")
    assert hasattr(mod, "VideoMetadata")
    # VideoReaderError is part of the port contract (application layer).
    assert hasattr(mod, "VideoReaderError")
    # The port module must not have pulled in heavy backends as a side effect.
    assert getattr(mod, "cv2", None) is None
    assert getattr(mod, "torch", None) is None


def test_app_modules_importable():
    """All modules under app/ should be importable."""
    modules = discover_modules("app", PROJECT_ROOT / "app")
    if not modules:
        pytest.skip("No modules found under app/")

    for mod_name in modules:
        if mod_name in SKIP_MODULES:
            continue
        try:
            importlib.import_module(mod_name)
        except ImportError as e:
            err_str = str(e)
            if any(hw in err_str for hw in HARDWARE_IMPORTS):
                pytest.skip(f"Hardware dependency: {e}")
            else:
                raise
