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
