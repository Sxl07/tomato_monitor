"""
system_monitor.py — Reusable system metrics helpers for benchmark scripts.

Provides functions to read CPU temperature, CPU usage, and RAM usage on demand.
Designed to be imported by other benchmark scripts in this package, consolidating
helpers that were previously duplicated across individual scripts.

Platform support:
  - Raspberry Pi (via vcgencmd for temperature)
  - Generic Linux (via /proc/stat, /proc/meminfo)
  - Other platforms: best-effort via psutil (optional, not a required dependency)

Usage:
    from scripts.benchmarks.system_monitor import (
        read_temperature,
        read_cpu_usage,
        read_ram_usage,
        system_snapshot,
    )
"""
from __future__ import annotations

import os
import subprocess
import time
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Temperature helpers
# ---------------------------------------------------------------------------

def _read_temp_vcgencmd() -> Optional[float]:
    """Read SoC temperature via vcgencmd (Raspberry Pi only)."""
    try:
        result = subprocess.run(
            ["vcgencmd", "measure_temp"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        raw = result.stdout.strip()
        if raw.startswith("temp="):
            value = raw.replace("temp=", "").replace("'C", "").replace("°C", "")
            return float(value)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


def _read_temp_thermal_zone() -> Optional[float]:
    """Read CPU temperature from /sys/class/thermal (Linux generic)."""
    thermal_path = "/sys/class/thermal/thermal_zone0/temp"
    try:
        with open(thermal_path, "r") as f:
            raw = f.read().strip()
            # Value is in millidegrees Celsius (e.g., 47800 = 47.8°C)
            return float(raw) / 1000.0
    except (FileNotFoundError, ValueError, PermissionError, OSError):
        pass
    return None


def _read_temp_psutil() -> Optional[float]:
    """Read CPU temperature via psutil (best-effort fallback, optional dependency)."""
    try:
        import psutil  # type: ignore

        temps = psutil.sensors_temperatures()
        if not temps:
            return None
        for key in ("cpu_thermal", "coretemp", "k10temp", "acpitz"):
            if key in temps and temps[key]:
                return float(temps[key][0].current)
        # Fallback: first available sensor
        for entries in temps.values():
            if entries:
                return float(entries[0].current)
    except (ImportError, AttributeError, OSError):
        pass
    return None


def read_temperature() -> Optional[float]:
    """
    Return CPU/SoC temperature in degrees Celsius, or None if unavailable.

    Tries vcgencmd first (Raspberry Pi), then /sys/class/thermal (Linux),
    then psutil as a last resort.
    """
    temp = _read_temp_vcgencmd()
    if temp is not None:
        return temp

    temp = _read_temp_thermal_zone()
    if temp is not None:
        return temp

    return _read_temp_psutil()


# ---------------------------------------------------------------------------
# Temperature formatting
# ---------------------------------------------------------------------------

def format_temp(value: Optional[float]) -> str:
    """Return a display string for a temperature reading."""
    if value is None:
        return "N/A"
    return f"{value:.1f} °C"


# ---------------------------------------------------------------------------
# CPU usage helpers
# ---------------------------------------------------------------------------

def _read_proc_stat() -> Optional[Dict[str, int]]:
    """Parse aggregate CPU times from /proc/stat (Linux only)."""
    try:
        with open("/proc/stat", "r") as f:
            for line in f:
                if line.startswith("cpu "):
                    parts = line.split()
                    # user, nice, system, idle, iowait, irq, softirq, steal
                    user = int(parts[1])
                    nice = int(parts[2])
                    system = int(parts[3])
                    idle = int(parts[4])
                    iowait = int(parts[5]) if len(parts) > 5 else 0
                    irq = int(parts[6]) if len(parts) > 6 else 0
                    softirq = int(parts[7]) if len(parts) > 7 else 0
                    steal = int(parts[8]) if len(parts) > 8 else 0
                    total = user + nice + system + idle + iowait + irq + softirq + steal
                    idle_total = idle + iowait
                    return {"total": total, "idle": idle_total}
    except (FileNotFoundError, ValueError, PermissionError, OSError):
        pass
    return None


def read_cpu_usage(interval: float = 0.5) -> Optional[float]:
    """
    Return current CPU usage percentage (0-100), or None if unavailable.

    On Linux, reads /proc/stat twice with the given interval to compute delta.
    Falls back to psutil if /proc/stat is not available.

    Args:
        interval: Seconds between two /proc/stat samples. Default 0.5s.
    """
    # Attempt /proc/stat method (Linux)
    sample1 = _read_proc_stat()
    if sample1 is not None:
        time.sleep(interval)
        sample2 = _read_proc_stat()
        if sample2 is not None:
            total_delta = sample2["total"] - sample1["total"]
            idle_delta = sample2["idle"] - sample1["idle"]
            if total_delta > 0:
                usage = ((total_delta - idle_delta) / total_delta) * 100.0
                return round(usage, 1)

    # Fallback: psutil (optional)
    try:
        import psutil  # type: ignore
        return psutil.cpu_percent(interval=interval)
    except (ImportError, OSError):
        pass

    return None


# ---------------------------------------------------------------------------
# RAM usage helpers
# ---------------------------------------------------------------------------

def _read_proc_meminfo() -> Optional[Dict[str, float]]:
    """Parse RAM metrics from /proc/meminfo (Linux only). Values in MB."""
    try:
        meminfo: Dict[str, int] = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split()
                key = parts[0].rstrip(":")
                value_kb = int(parts[1])
                meminfo[key] = value_kb

        total_kb = meminfo.get("MemTotal", 0)
        available_kb = meminfo.get("MemAvailable", 0)

        if total_kb == 0:
            return None

        total_mb = total_kb / 1024.0
        available_mb = available_kb / 1024.0
        used_mb = total_mb - available_mb
        percent_used = (used_mb / total_mb) * 100.0

        return {
            "total_mb": round(total_mb, 1),
            "used_mb": round(used_mb, 1),
            "available_mb": round(available_mb, 1),
            "percent_used": round(percent_used, 1),
        }
    except (FileNotFoundError, ValueError, KeyError, PermissionError, OSError):
        pass
    return None


def _read_ram_psutil() -> Optional[Dict[str, float]]:
    """Read RAM metrics via psutil (best-effort fallback, optional dependency)."""
    try:
        import psutil  # type: ignore

        mem = psutil.virtual_memory()
        return {
            "total_mb": round(mem.total / (1024 * 1024), 1),
            "used_mb": round(mem.used / (1024 * 1024), 1),
            "available_mb": round(mem.available / (1024 * 1024), 1),
            "percent_used": round(mem.percent, 1),
        }
    except (ImportError, OSError):
        pass
    return None


def read_ram_usage() -> Optional[Dict[str, float]]:
    """
    Return dict with RAM metrics, or None if unavailable.

    Keys: total_mb, used_mb, available_mb, percent_used.
    On Linux, reads /proc/meminfo. Falls back to psutil if available.
    """
    ram = _read_proc_meminfo()
    if ram is not None:
        return ram
    return _read_ram_psutil()


# ---------------------------------------------------------------------------
# All-in-one snapshot
# ---------------------------------------------------------------------------

def system_snapshot(cpu_interval: float = 0.5) -> Dict[str, object]:
    """
    Return a dict with temperature, cpu_percent, and ram info in a single call.

    Useful for benchmark scripts that want a quick system state capture.

    Args:
        cpu_interval: Seconds for CPU usage measurement. Default 0.5s.

    Returns:
        Dict with keys:
          - temperature_c: float or None
          - cpu_percent: float or None
          - ram: dict with total_mb, used_mb, available_mb, percent_used (or None)
    """
    return {
        "temperature_c": read_temperature(),
        "cpu_percent": read_cpu_usage(interval=cpu_interval),
        "ram": read_ram_usage(),
    }


# ---------------------------------------------------------------------------
# Demo / self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("System Monitor — Quick Check")
    print("=" * 40)

    temp = read_temperature()
    print(f"Temperature: {temp}°C" if temp is not None else "Temperature: N/A")

    cpu = read_cpu_usage(interval=1.0)
    print(f"CPU usage:   {cpu}%" if cpu is not None else "CPU usage:   N/A")

    ram = read_ram_usage()
    if ram is not None:
        print(f"RAM total:   {ram['total_mb']} MB")
        print(f"RAM used:    {ram['used_mb']} MB ({ram['percent_used']}%)")
        print(f"RAM avail:   {ram['available_mb']} MB")
    else:
        print("RAM:         N/A")

    print()
    print("Full snapshot:")
    snapshot = system_snapshot(cpu_interval=1.0)
    for key, value in snapshot.items():
        print(f"  {key}: {value}")
