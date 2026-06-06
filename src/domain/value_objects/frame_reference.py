from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FrameReference:
    source_name: str
    frame_index: int

    def display_name(self) -> str:
        return f"{self.source_name}#frame_{self.frame_index:06d}"