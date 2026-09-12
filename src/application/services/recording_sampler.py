"""RecordingSampler — temporal frame-selection policy for video recording.

Spec 023: decouples the RECORDING cadence (recording_target_fps) from the
PHYSICAL camera cadence (camera_stream_fps). The camera produces frames at
~camera_stream_fps; this sampler decides, purely by monotonic time, which of
those real frames should be persisted so the stored video stays at
~recording_target_fps.

Pure application-layer policy: it holds no camera/frame state, performs no I/O
and imports nothing from cv2, picamera2, torch, FastAPI, SQLAlchemy, VideoRecorder
or FrameSource. The clock is injectable for deterministic tests.

Algorithm — phase-preserving, no-burst:
    - The first frame is always eligible (written).
    - A frame before the next scheduled slot is skipped.
    - A frame at/after the next slot is written using the CURRENT real frame; the
      next slot is advanced along the ORIGINAL time grid, jumping over any missed
      slots. It NEVER emits extra writes to "catch up" lost slots.

Why not ``next_due = now + interval``: anchoring the next slot to ``now``
accumulates each frame's jitter and drifts progressively away from the target
grid. Anchoring to ``_next_due + k*interval`` preserves the phase, so small jitter
does not accumulate.
"""

from __future__ import annotations

import time
from typing import Callable, Optional


class RecordingSampler:
    """Decide, by monotonic time, whether a real frame should be persisted.

    Writes at most one frame per ``should_write()`` call and never duplicates or
    interpolates frames.
    """

    def __init__(
        self,
        recording_fps: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if recording_fps <= 0:
            raise ValueError(f"recording_fps must be > 0, got {recording_fps}")
        self._interval = 1.0 / recording_fps
        self._clock = clock
        self._next_due: Optional[float] = None

    def should_write(self) -> bool:
        """Return True if the current real frame should be persisted now."""
        now = self._clock()

        # First frame is always eligible; anchor the grid to it.
        if self._next_due is None:
            self._next_due = now + self._interval
            return True

        # Not yet time for the next slot.
        if now < self._next_due:
            return False

        # Time to write. Advance the next slot along the original grid, jumping
        # over any slots missed during a gap (no burst, no catch-up writes).
        skipped = int((now - self._next_due) // self._interval)
        self._next_due = self._next_due + (skipped + 1) * self._interval
        return True
