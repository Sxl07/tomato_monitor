from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BoundingBox:
    x1: int
    y1: int
    x2: int
    y2: int

    def width(self) -> int:
        return max(0, self.x2 - self.x1)

    def height(self) -> int:
        return max(0, self.y2 - self.y1)

    def area(self) -> int:
        return self.width() * self.height()

    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    def to_tuple(self) -> tuple[int, int, int, int]:
        return (self.x1, self.y1, self.x2, self.y2)

    @staticmethod
    def from_tuple(values: tuple[int, int, int, int]) -> "BoundingBox":
        x1, y1, x2, y2 = values
        return BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2)

    def is_valid(self) -> bool:
        return self.x2 > self.x1 and self.y2 > self.y1