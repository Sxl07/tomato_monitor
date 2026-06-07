"""Abstract interface for robot movement control."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class RobotPosition:
    """Current position of the robot in the module coordinate system."""
    x_meters: float
    y_meters: float
    heading_degrees: float


class RobotMovementService(ABC):
    """Abstract interface for robot movement control.

    No implementation in this spec — stubs only for future integration.
    """

    @abstractmethod
    def advance(self) -> None:
        """Command the robot to advance one step forward."""
        ...

    @abstractmethod
    def pause(self) -> None:
        """Command the robot to stop in place (resumable)."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Command the robot to perform a full stop (non-resumable)."""
        ...

    @abstractmethod
    def get_position(self) -> Optional[RobotPosition]:
        """Return current robot position, or None if unavailable."""
        ...
