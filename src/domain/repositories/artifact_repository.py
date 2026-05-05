from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class ArtifactRepository(ABC):
    @abstractmethod
    def ensure_session_dirs(self, session_id: str) -> dict[str, Path]:
        """Crea y devuelve las rutas principales de artifacts para una sesión."""
        raise NotImplementedError

    @abstractmethod
    def save_summary(self, session_id: str, summary: dict) -> Path:
        """Guarda el resumen de una sesión."""
        raise NotImplementedError

    @abstractmethod
    def save_per_frame_rows(self, session_id: str, rows: list[dict]) -> Path:
        """Guarda el reporte por frame."""
        raise NotImplementedError

    @abstractmethod
    def save_per_detection_rows(self, session_id: str, rows: list[dict]) -> Path:
        """Guarda el reporte por detección."""
        raise NotImplementedError

    @abstractmethod
    def list_snapshots(self, session_id: str, limit: int = 20) -> list[str]:
        """Lista snapshots anotados de una sesión."""
        raise NotImplementedError

    @abstractmethod
    def list_annotated_videos(self, session_id: str) -> list[str]:
        """Lista videos anotados de una sesión."""
        raise NotImplementedError