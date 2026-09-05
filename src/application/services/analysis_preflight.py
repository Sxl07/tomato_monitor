"""AnalysisPreflight — technical checks before starting deferred analysis (Spec 020).

Pure, testable application-layer helper. Runs an ORDERED set of checks and stops
at the first failure with an actionable Spanish message. It does NOT load
RetinaNet and does NOT open the camera. It validates the recorded video only by
opening it read-only (reusing VideoRecorder.validate()-style logic) and checks
device/thermal/undervoltage conditions.

Checks, in order:
    1. status == ready_for_analysis
    2. video_path present (non-null)
    3. video_path is a safe relative path within BASE_DIR (path traversal guard)
    4. file exists on disk and size > 0
    5. video opens and yields >= 1 frame
    6. no concurrent analysis for this monitoring; no module session conflict
       (Active_Status_Set, excluding this monitoring's own id); no active
       hardware/compute phase on the device (device-global guard)
    7. current CPU temperature <= ACTIVE_PROFILE.analysis_thermal_pause_threshold
       (the existing threshold at which analysis pauses; reused, not reinvented).
       If temperature cannot be read (off-RPi / vcgencmd missing) -> do NOT block.
    8. current undervoltage: if a probe is available and reports PRESENT -> block;
       if unavailable -> do NOT block (delegate to operator confirmation).

The preflight NEVER claims a specific/official power source.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

from src.domain.value_objects.monitoring_status import MonitoringState
from src.infrastructure.monitoring.undervoltage_probe import (
    UndervoltagePresenceProbe,
    UndervoltageStatus,
)
from src.infrastructure.security.path_sanitizer import (
    PathTraversalError,
    validate_safe_path,
)

logger = logging.getLogger(__name__)

# Statuses that count as an ACTIVE session for one-session-per-module conflicts.
_ACTIVE_STATUSES = {
    MonitoringState.INITIALIZING.value,
    MonitoringState.RUNNING.value,
    MonitoringState.PAUSED.value,
    MonitoringState.FINISHING.value,
    MonitoringState.READY_FOR_ANALYSIS.value,
    MonitoringState.ANALYZING.value,
}


@dataclass
class PreflightResult:
    """Outcome of a preflight run."""

    ok: bool
    reason_code: Optional[str] = None      # machine-readable code (None if ok)
    message: Optional[str] = None          # actionable Spanish message (None if ok)
    temperature_c: Optional[float] = None  # observed temp at preflight (None if unavailable)

    @classmethod
    def success(cls, temperature_c: Optional[float]) -> "PreflightResult":
        return cls(ok=True, temperature_c=temperature_c)

    @classmethod
    def failure(cls, code: str, message: str, temperature_c: Optional[float] = None) -> "PreflightResult":
        return cls(ok=False, reason_code=code, message=message, temperature_c=temperature_c)


def _read_cpu_temperature(timeout_seconds: float = 5.0) -> Optional[float]:
    """Read current CPU temperature via `vcgencmd measure_temp`, or None.

    Mirrors ThermalMonitor's parsing without starting a monitor thread. Returns
    None off-RPi / when vcgencmd is missing or output cannot be parsed.
    """
    try:
        result = subprocess.run(
            ["vcgencmd", "measure_temp"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if result.returncode == 0:
            temp_str = result.stdout.strip()  # e.g. "temp=61.5'C"
            return float(temp_str.split("=")[1].replace("'C", ""))
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, IndexError, OSError):
        pass
    except Exception:  # pragma: no cover - defensive
        pass
    return None


def _default_video_readable(abs_path: str) -> bool:
    """Return True if the video opens and yields >= 1 frame (no RetinaNet, no camera)."""
    try:
        import cv2  # local import: keep module importable without cv2
    except Exception:  # pragma: no cover
        return False
    cap = None
    try:
        cap = cv2.VideoCapture(abs_path)
        if not cap.isOpened():
            return False
        ok, frame = cap.read()
        return bool(ok) and frame is not None
    except Exception:
        return False
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:  # pragma: no cover
                pass


class AnalysisPreflight:
    """Ordered technical checks before a manual deferred-analysis start."""

    def __init__(
        self,
        base_dir,
        monitoring_repo,
        runtime_registry,
        thermal_pause_threshold_c: float,
        *,
        undervoltage_probe: Optional[UndervoltagePresenceProbe] = None,
        temperature_reader: Optional[Callable[[], Optional[float]]] = None,
        video_readable: Optional[Callable[[str], bool]] = None,
        os_path_exists: Optional[Callable[[str], bool]] = None,
        os_path_getsize: Optional[Callable[[str], int]] = None,
    ) -> None:
        self._base_dir = base_dir
        self._monitoring_repo = monitoring_repo
        self._registry = runtime_registry
        self._thermal_threshold = float(thermal_pause_threshold_c)
        self._undervoltage_probe = undervoltage_probe or UndervoltagePresenceProbe()
        self._read_temp = temperature_reader or _read_cpu_temperature
        self._video_readable = video_readable or _default_video_readable
        import os as _os
        self._exists = os_path_exists or _os.path.exists
        self._getsize = os_path_getsize or _os.path.getsize

    def run(self, monitoring) -> PreflightResult:
        """Execute the ordered checks and return a PreflightResult."""
        # 1) status
        if monitoring.status != MonitoringState.READY_FOR_ANALYSIS.value:
            return PreflightResult.failure(
                "invalid_status",
                "El monitoreo no está listo para análisis.",
            )

        # 2) video_path present
        video_path = getattr(monitoring, "video_path", None)
        if not video_path:
            return PreflightResult.failure(
                "video_missing_path",
                "El monitoreo no tiene un video asociado para analizar.",
            )

        # 3) safe path
        try:
            abs_path = str(validate_safe_path(video_path, self._base_dir))
        except PathTraversalError:
            return PreflightResult.failure(
                "video_unsafe_path",
                "La ruta del video del monitoreo no es válida.",
            )

        # 4) exists and size > 0
        if not self._exists(abs_path):
            return PreflightResult.failure(
                "video_not_found",
                "El archivo de video del monitoreo no existe en disco.",
            )
        try:
            if self._getsize(abs_path) <= 0:
                return PreflightResult.failure(
                    "video_empty",
                    "El archivo de video del monitoreo está vacío.",
                )
        except OSError:
            return PreflightResult.failure(
                "video_not_found",
                "El archivo de video del monitoreo no existe en disco.",
            )

        # 5) opens and reads >= 1 frame
        if not self._video_readable(abs_path):
            return PreflightResult.failure(
                "video_unreadable",
                "El archivo de video del monitoreo no es legible. "
                "Verifica que el archivo no esté dañado.",
            )

        # 6) concurrency: no analysis already claimed for this monitoring;
        #    no other active session for the module (excluding own id);
        #    no active hardware/compute phase on the device (device-global guard).
        if self._registry.is_analysis_claimed(monitoring.id):
            return PreflightResult.failure(
                "analysis_in_progress",
                "El análisis de este monitoreo ya está en curso.",
            )

        module_id = getattr(monitoring, "module_id", None)
        if module_id is not None:
            for other in self._monitoring_repo.get_by_module(module_id):
                if other.id != monitoring.id and other.status in _ACTIVE_STATUSES:
                    return PreflightResult.failure(
                        "module_session_conflict",
                        "El módulo tiene otra sesión activa. "
                        "Espera a que termine para iniciar el análisis.",
                    )

        # Device-global capture guard: a capture active on ANY module blocks a
        # deferred analysis. Uses the GLOBAL, module-independent has_active_capture()
        # (has_live_worker_for_module is per-module and NOT sufficient here);
        # is_camera_locked() is only a hardware safety net, not the sole source.
        if hasattr(self._registry, "has_active_capture") and self._registry.has_active_capture():
            return PreflightResult.failure(
                "device_capture_active",
                "Hay una captura en curso en el dispositivo. "
                "Espera a que finalice para iniciar el análisis.",
            )
        # Device-global heavy-analysis guard: at most one analysis on the device.
        if (
            hasattr(self._registry, "is_global_analysis_active")
            and self._registry.is_global_analysis_active()
        ):
            return PreflightResult.failure(
                "device_analysis_active",
                "Ya hay un análisis en curso en el dispositivo. "
                "Espera a que finalice para iniciar este análisis.",
            )

        # 7) temperature (reuse existing threshold; do NOT block if unreadable)
        temperature = self._read_temp()
        if temperature is not None and temperature > self._thermal_threshold:
            return PreflightResult.failure(
                "temperature_high",
                f"Temperatura alta ({temperature:.1f}°C). "
                "Espera a que el dispositivo se enfríe antes de iniciar el análisis.",
                temperature_c=temperature,
            )

        # 8) undervoltage (block only on PRESENT; UNAVAILABLE never blocks)
        try:
            uv_status = self._undervoltage_probe.check()
        except Exception:  # pragma: no cover - defensive
            uv_status = UndervoltageStatus.UNAVAILABLE
        if uv_status == UndervoltageStatus.PRESENT:
            return PreflightResult.failure(
                "undervoltage_present",
                "Se detectó bajo voltaje. Revisa la fuente de energía "
                "antes de iniciar el análisis.",
                temperature_c=temperature,
            )
        if uv_status == UndervoltageStatus.UNAVAILABLE:
            logger.info(
                "AnalysisPreflight: undervoltage probe unavailable; "
                "delegating power adequacy to operator confirmation."
            )

        return PreflightResult.success(temperature_c=temperature)
