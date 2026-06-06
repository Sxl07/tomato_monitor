"""
check_versions.py — Environment version reporter for Tomato Monitor benchmarks.

Run this script on the Raspberry Pi to record the exact library versions
installed in the active virtual environment. Copy the output into the
benchmark report at docs/benchmarks/raspberry-baseline.md.

Usage:
    python scripts/benchmarks/check_versions.py

Output format is structured for direct copy-paste into a Markdown table.
"""

import platform
import sys


def get_torch_info() -> dict:
    try:
        import torch
        return {
            "version": torch.__version__,
            "cuda_available": torch.cuda.is_available(),
            "backends_mps_available": getattr(torch.backends, "mps", None) is not None
            and torch.backends.mps.is_available(),
        }
    except ImportError:
        return {"version": "NOT INSTALLED", "cuda_available": False, "backends_mps_available": False}


def get_torchvision_version() -> str:
    try:
        import torchvision
        return torchvision.__version__
    except ImportError:
        return "NOT INSTALLED"


def get_opencv_version() -> str:
    try:
        import cv2
        return cv2.__version__
    except ImportError:
        return "NOT INSTALLED"


def get_numpy_version() -> str:
    try:
        import numpy
        return numpy.__version__
    except ImportError:
        return "NOT INSTALLED"


def get_detectron2_version() -> str:
    try:
        import detectron2
        version = getattr(detectron2, "__version__", None)
        if version:
            return version
        # Detectron2 installed from source may not expose __version__
        # Try to read from the package metadata as a fallback.
        try:
            from importlib.metadata import version as pkg_version
            return pkg_version("detectron2")
        except Exception:
            return "INSTALLED (version not accessible — likely source install)"
    except ImportError:
        return "NOT INSTALLED"


def collect_versions() -> dict:
    torch_info = get_torch_info()
    return {
        "python": sys.version,
        "python_short": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or "N/A",
        "architecture": " / ".join(platform.architecture()),
        "torch": torch_info["version"],
        "torch_cuda": str(torch_info["cuda_available"]),
        "torch_mps": str(torch_info["backends_mps_available"]),
        "torchvision": get_torchvision_version(),
        "opencv": get_opencv_version(),
        "numpy": get_numpy_version(),
        "detectron2": get_detectron2_version(),
    }


def print_report(v: dict) -> None:
    separator = "=" * 60

    print(separator)
    print("  TOMATO MONITOR — ENVIRONMENT VERSION REPORT")
    print(separator)

    print("\n### Platform")
    print(f"  Python version   : {v['python_short']}")
    print(f"  Full version     : {v['python']}")
    print(f"  Platform         : {v['platform']}")
    print(f"  Architecture     : {v['architecture']}")
    print(f"  Machine          : {v['machine']}")
    print(f"  Processor        : {v['processor']}")

    print("\n### Deep Learning")
    print(f"  PyTorch          : {v['torch']}")
    print(f"    CUDA available : {v['torch_cuda']}")
    print(f"    MPS available  : {v['torch_mps']}")
    print(f"  TorchVision      : {v['torchvision']}")
    print(f"  Detectron2       : {v['detectron2']}")

    print("\n### Computer Vision / Numerics")
    print(f"  OpenCV           : {v['opencv']}")
    print(f"  NumPy            : {v['numpy']}")

    print("\n### Markdown table (copy into raspberry-baseline.md)")
    print()
    print("| Library     | Version |")
    print("|-------------|---------|")
    print(f"| Python      | {v['python_short']} |")
    print(f"| PyTorch     | {v['torch']} |")
    print(f"| TorchVision | {v['torchvision']} |")
    print(f"| OpenCV      | {v['opencv']} |")
    print(f"| NumPy       | {v['numpy']} |")
    print(f"| Detectron2  | {v['detectron2']} |")
    print()
    print(f"Platform: {v['platform']}")
    print(separator)


if __name__ == "__main__":
    versions = collect_versions()
    print_report(versions)
