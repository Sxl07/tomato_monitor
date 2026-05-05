from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.entities.inspection_result import InspectionResult


class InspectionRepository(ABC):
    @abstractmethod
    def save_result(self, result: InspectionResult) -> None:
        """Guarda un resultado de inspección individual."""
        raise NotImplementedError

    @abstractmethod
    def save_many(self, results: list[InspectionResult]) -> None:
        """Guarda varios resultados de inspección."""
        raise NotImplementedError

    @abstractmethod
    def list_by_session(self, session_id: str) -> list[InspectionResult]:
        """Lista resultados asociados a una sesión."""
        raise NotImplementedError