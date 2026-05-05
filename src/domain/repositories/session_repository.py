from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from src.domain.entities.inspection_session import InspectionSession


class SessionRepository(ABC):
    @abstractmethod
    def save(self, session: InspectionSession) -> None:
        """Guarda o actualiza una sesión de inspección."""
        raise NotImplementedError

    @abstractmethod
    def get_by_id(self, session_id: str) -> Optional[InspectionSession]:
        """Obtiene una sesión por su identificador."""
        raise NotImplementedError

    @abstractmethod
    def list_recent(self, limit: int = 10) -> list[InspectionSession]:
        """Lista sesiones recientes."""
        raise NotImplementedError