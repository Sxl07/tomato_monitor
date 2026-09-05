"""UndervoltagePresenceProbe — optional current-undervoltage detection (Spec 020).

Reads ``vcgencmd get_throttled`` and interprets the returned bit mask:
    - bit 0  (0x1)     : undervoltage detected NOW (current) -> PRESENT
    - bit 16 (0x10000) : undervoltage has occurred SINCE BOOT (historical/latched)
                         -> NEVER interpreted as a current fault

Only the CURRENT-undervoltage bit (bit 0) can report ``PRESENT``. Historical/
latched flags are explicitly ignored. When ``vcgencmd`` is absent, errors, or the
output cannot be parsed (e.g. off-Raspberry-Pi), the probe returns ``UNAVAILABLE``
and NEVER blocks. It makes no claim about the physical power supply type (no
"official PSU" detection).

Layer: infrastructure. No robot/motor language, no new dependencies.
"""

from __future__ import annotations

import logging
import subprocess
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

# Bit masks for `vcgencmd get_throttled`.
UNDERVOLTAGE_NOW_BIT = 0x1        # bit 0  — currently under-voltage
UNDERVOLTAGE_OCCURRED_BIT = 0x10000  # bit 16 — under-voltage has occurred (historical)


class UndervoltageStatus(str, Enum):
    """Tri-valued result of an undervoltage presence check."""

    PRESENT = "present"          # current undervoltage detected (bit 0 set)
    ABSENT = "absent"            # valid reading, bit 0 clear
    UNAVAILABLE = "unavailable"  # cannot read (off-RPi, vcgencmd missing, parse error)


class UndervoltagePresenceProbe:
    """Optional probe for CURRENT undervoltage via ``vcgencmd get_throttled``."""

    def __init__(self, *, timeout_seconds: float = 5.0) -> None:
        self._timeout = timeout_seconds

    def read_raw(self) -> Optional[int]:
        """Return the raw throttled bit mask, or None if it cannot be read.

        Parses output of the form ``throttled=0x50005``. Any failure
        (command missing, timeout, non-zero exit, unparseable output) -> None.
        """
        try:
            result = subprocess.run(
                ["vcgencmd", "get_throttled"],
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None
        except Exception:  # pragma: no cover - defensive
            return None

        if result.returncode != 0:
            return None

        out = (result.stdout or "").strip()
        # Expected: "throttled=0x0" (may contain extra spaces).
        if "=" not in out:
            return None
        value_str = out.split("=", 1)[1].strip()
        try:
            return int(value_str, 16) if value_str.lower().startswith("0x") else int(value_str)
        except ValueError:
            return None

    @staticmethod
    def interpret(mask: Optional[int]) -> UndervoltageStatus:
        """Map a raw mask (or None) to an UndervoltageStatus.

        - None                       -> UNAVAILABLE
        - bit 0 set                  -> PRESENT (current undervoltage)
        - bit 0 clear (incl. bit 16) -> ABSENT (historical flags never block)
        """
        if mask is None:
            return UndervoltageStatus.UNAVAILABLE
        if mask & UNDERVOLTAGE_NOW_BIT:
            return UndervoltageStatus.PRESENT
        return UndervoltageStatus.ABSENT

    def check(self) -> UndervoltageStatus:
        """Read and interpret current undervoltage. Never raises."""
        return self.interpret(self.read_raw())

    def is_available(self) -> bool:
        """True if a valid reading can currently be obtained."""
        return self.read_raw() is not None
