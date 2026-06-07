"""Abstract interface for frame-based movement decisions."""
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any


class MovementDecision(str, Enum):
    ADVANCE = "advance"
    PAUSE = "pause"
    WAIT = "wait"


class DecisionService(ABC):
    """Decides whether the robot should advance, pause, or wait."""

    @abstractmethod
    def decide(self, frame_context: Any) -> MovementDecision:
        """Decide the next robot action based on the current frame context."""
        ...
