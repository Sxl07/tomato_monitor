#!/usr/bin/env python3
"""Collect environment identification info for benchmark documentation.

Run this script on the Raspberry Pi 5 to gather the test identification
fields required by docs/benchmarks/raspberry-baseline.md (section 6).

Usage:
    python scripts/benchmarks/collect_env_info.py

Output:
    Structured summary printed to stdout, ready to paste into documentation.
"""

import datetime
import platform
import subprocess
import sys
from pathlib import Path


def run_cmd(cmd: list[str], fallback: str = "N/A") -> str:
    """Run a shell command and return stripped stdout, or fallback on error."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stdout.strip()
        return output if output else fallback
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return fallback


def get_git_commit() -> str:
    """Get the current git commit hash (short)."""
    return run_cmd(["git", "log", "--oneline", "-1"])


def get_git_branch() -> str:
    """Get the current git branch name."""
    return run_cmd(["git", "branch", "--show-current"])


def get_os_info() -> str:
    """Get OS identification string."""
    try:
        os_release = Path("/etc/os-release")
        if os_release.exists():
            lines = os_release.read_text().splitlines()
            pretty_name = next(
                (l.split("=", 1)[1].strip('"') for l in lines if l.startswith("PRETTY_NAME=")),
                None,
            )
            if pretty_name:
                return pretty_name
    except OSError:
        pass
    return f"{platform.system()} {platform.release()} ({platform.machine()})"


def get_python_version() -> str:
    """Get Python version string."""
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def get_torch_version() -> str:
    """Get PyTorch version if available."""
    try:
        import torch
        return torch.__version__
    except ImportError:
        return "Not installed"


def get_torchvision_version() -> str:
    """Get TorchVision version if available."""
    try:
        import torchvision
        return torchvision.__version__
    except ImportError:
        return "Not installed"


def get_detectron2_version() -> str:
    """Get Detectron2 version or commit info."""
    try:
        import detectron2
        version = getattr(detectron2, "__version__", None)
        if version:
            return version
        # Try to get the installed commit from pip
        result = run_cmd(["pip", "show", "detectron2"])
        for line in result.splitlines():
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
        return "Installed (version unknown)"
    except ImportError:
        return "Not installed"


def get_opencv_version() -> str:
    """Get OpenCV version if available."""
    try:
        import cv2
        return cv2.__version__
    except ImportError:
        return "Not installed"


def get_numpy_version() -> str:
    """Get NumPy version if available."""
    try:
        import numpy
        return numpy.__version__
    except ImportError:
        return "Not installed"


def get_psutil_version() -> str:
    """Get psutil version if available."""
    try:
        import psutil
        return psutil.__version__
    except ImportError:
        return "Not installed"


def get_cpu_temperature() -> str:
    """Read CPU temperature via vcgencmd (Raspberry Pi specific)."""
    output = run_cmd(["vcgencmd", "measure_temp"], fallback="")
    if output and "temp=" in output:
        return output.replace("temp=", "").replace("'C", " °C")
    return "N/A (not on Raspberry Pi or vcgencmd unavailable)"


def main() -> None:
    """Collect and print all environment information."""
    print("=" * 60)
    print("  BENCHMARK ENVIRONMENT INFORMATION")
    print("  Tomato Monitor — Raspberry Baseline Benchmark (Spec 001)")
    print("=" * 60)
    print()

    # Section 6.1: Test identification
    print("--- Section 6.1: Identificación de la prueba ---")
    print()
    print(f"  Fecha:              {datetime.date.today().isoformat()}")
    print(f"  Dispositivo:        Raspberry Pi 5 (8 GB RAM)")
    print(f"  Sistema operativo:  {get_os_info()}")
    print(f"  Arquitectura:       {platform.machine()}")
    print(f"  Python:             {get_python_version()}")
    print(f"  Git commit:         {get_git_commit()}")
    print(f"  Git branch:         {get_git_branch()}")
    print()

    # Section 6.2: Software versions
    print("--- Section 6.2: Versiones de software ---")
    print()
    print(f"  PyTorch:            {get_torch_version()}")
    print(f"  TorchVision:        {get_torchvision_version()}")
    print(f"  Detectron2:         {get_detectron2_version()}")
    print(f"  OpenCV:             {get_opencv_version()}")
    print(f"  NumPy:              {get_numpy_version()}")
    print(f"  psutil:             {get_psutil_version()}")
    print()

    # Additional context
    print("--- Contexto adicional ---")
    print()
    print(f"  Temperatura CPU:    {get_cpu_temperature()}")
    print(f"  Platform:           {platform.platform()}")
    print(f"  Python full:        {sys.version}")
    print()

    # Markdown-ready output for copy-paste
    print("=" * 60)
    print("  MARKDOWN OUTPUT (copy-paste into raspberry-baseline.md)")
    print("=" * 60)
    print()
    print("### 6.1 Identificación")
    print()
    print("| Campo | Valor |")
    print("|---|---|")
    print(f"| Nombre de la prueba | Raspberry Pi 5 Baseline Benchmark |")
    print(f"| Fecha | {datetime.date.today().isoformat()} |")
    print(f"| Dispositivo | Raspberry Pi 5 (8 GB RAM) |")
    print(f"| Sistema operativo | {get_os_info()} |")
    print(f"| Versión de Python | {get_python_version()} |")
    print(f"| Git commit | {get_git_commit()} |")
    print(f"| Branch | {get_git_branch()} |")
    print()
    print("### 6.2 Versiones de software")
    print()
    print("| Componente | Versión |")
    print("|---|---|")
    print(f"| PyTorch | {get_torch_version()} |")
    print(f"| TorchVision | {get_torchvision_version()} |")
    print(f"| Detectron2 | {get_detectron2_version()} |")
    print(f"| OpenCV | {get_opencv_version()} |")
    print(f"| NumPy | {get_numpy_version()} |")
    print(f"| psutil | {get_psutil_version()} |")
    print()


if __name__ == "__main__":
    main()
