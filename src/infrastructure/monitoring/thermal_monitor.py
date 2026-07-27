"""Thermal monitor for Raspberry Pi CPU temperature management.

Monitors CPU temperature via vcgencmd and auto-pauses/resumes
the monitoring worker when thermal thresholds are exceeded.
Falls back gracefully on non-RPi platforms (no-op mode).
"""
import logging
import subprocess
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class ThermalMonitor:
    """Monitors CPU temperature and triggers pause/resume on MonitoringWorker."""

    def __init__(
        self,
        pause_event: threading.Event,
        *,
        poll_interval_seconds: float = 5.0,
        warning_temp: float = 75.0,
        critical_temp: float = 80.0,
        resume_temp: float = 70.0,
    ) -> None:
        self._pause_event = pause_event
        self._poll_interval = poll_interval_seconds
        self._warning_temp = warning_temp
        self._critical_temp = critical_temp
        self._resume_temp = resume_temp

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._vcgencmd_available: Optional[bool] = None

        # Metrics
        self._peak_temperature: float = 0.0
        self._pause_count: int = 0
        self._total_pause_duration: float = 0.0
        self._pause_start_time: Optional[float] = None
        self._cooling_warning_at_start: bool = False
        self._current_temperature: Optional[float] = None

    @property
    def pause_event(self) -> threading.Event:
        """The threading.Event used to signal thermal pause."""
        return self._pause_event

    @property
    def current_temperature(self) -> Optional[float]:
        """Last read temperature in °C (None if never read)."""
        return self._current_temperature

    @property
    def peak_temperature(self) -> float:
        return self._peak_temperature

    @property
    def pause_count(self) -> int:
        return self._pause_count

    @property
    def total_pause_duration_seconds(self) -> float:
        return self._total_pause_duration

    @property
    def cooling_warning_at_start(self) -> bool:
        """Whether temperature was above warning threshold at monitor start."""
        return self._cooling_warning_at_start

    def _record_temperature(self, temp: float) -> None:
        """Record a temperature reading, updating current and peak."""
        if temp is None:
            return
        self._current_temperature = float(temp)
        if self._current_temperature > self._peak_temperature:
            self._peak_temperature = self._current_temperature

    def start(self) -> None:
        """Start the temperature monitoring thread (daemon)."""
        self._running = True
        self._thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="thermal-monitor",
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the monitoring thread."""
        self._running = False
        # Account for in-progress pause duration
        if self._pause_start_time is not None:
            self._total_pause_duration += time.time() - self._pause_start_time
            self._pause_start_time = None
        if self._thread is not None:
            self._thread.join(timeout=self._poll_interval * 2)

    def get_session_metadata(self) -> dict:
        """Return thermal session stats for inclusion in monitoring metadata."""
        return {
            "peak_temperature_c": self.peak_temperature,
            "pause_count": self.pause_count,
            "total_pause_duration_s": self.total_pause_duration_seconds,
            "cooling_warning_at_start": self.cooling_warning_at_start,
            "current_temperature_c": self.current_temperature,
            "is_paused": self.pause_event.is_set(),
        }

    def _monitor_loop(self) -> None:
        """Main polling loop. Runs in daemon thread."""
        # Check if vcgencmd is available
        if not self._check_vcgencmd():
            logger.info(
                "vcgencmd not available — thermal monitoring disabled (non-RPi platform)"
            )
            return

        # Initial temperature check
        temp = self._read_temperature()
        self._record_temperature(temp)
        if temp is not None and temp > self._warning_temp:
            self._cooling_warning_at_start = True
            logger.warning(
                f"Temperature at start is {temp:.1f}°C "
                f"(above warning threshold {self._warning_temp}°C). "
                f"Ensure active cooling is running."
            )

        while self._running:
            time.sleep(self._poll_interval)
            if not self._running:
                break

            temp = self._read_temperature()
            self._record_temperature(temp)
            if temp is None:
                continue

            # Critical: pause
            if temp >= self._critical_temp and not self._pause_event.is_set():
                logger.warning(
                    f"CRITICAL: Temperature {temp:.1f}°C >= "
                    f"{self._critical_temp}°C — pausing monitoring"
                )
                self._pause_event.set()
                self._pause_count += 1
                self._pause_start_time = time.time()

            # Resume: temp dropped below resume threshold
            elif (
                temp < self._resume_temp
                and self._pause_event.is_set()
                and self._pause_start_time is not None
            ):
                duration = time.time() - self._pause_start_time
                self._total_pause_duration += duration
                self._pause_start_time = None
                self._pause_event.clear()
                logger.info(
                    f"Temperature {temp:.1f}°C < {self._resume_temp}°C — "
                    f"resuming monitoring (paused {duration:.1f}s)"
                )

            # Warning
            elif temp >= self._warning_temp and temp < self._critical_temp:
                logger.warning(
                    f"Temperature elevated: {temp:.1f}°C "
                    f"(warning threshold: {self._warning_temp}°C)"
                )

    def _check_vcgencmd(self) -> bool:
        """Check if vcgencmd is available on this system."""
        if self._vcgencmd_available is not None:
            return self._vcgencmd_available
        try:
            result = subprocess.run(
                ["vcgencmd", "measure_temp"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            self._vcgencmd_available = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self._vcgencmd_available = False
        return self._vcgencmd_available

    def _read_temperature(self) -> Optional[float]:
        """Read current CPU temperature via vcgencmd."""
        try:
            result = subprocess.run(
                ["vcgencmd", "measure_temp"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                # Parse "temp=XX.X'C"
                temp_str = result.stdout.strip()
                temp_value = float(temp_str.split("=")[1].replace("'C", ""))
                return temp_value
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, IndexError):
            pass
        return None
