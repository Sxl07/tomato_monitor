from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class ArtifactRepository(ABC):
    @abstractmethod
    def create_session_dirs(self, session_id: str) -> dict[str, Path]:
        """Crea y devuelve las rutas principales de artifacts para una sesión."""
        raise NotImplementedError

    @abstractmethod
    def get_session_dirs(self, session_id: str) -> dict[str, Path]:
        """Devuelve las rutas principales de una sesión existente. Debe fallar si no existe."""
        raise NotImplementedError

    @abstractmethod
    def delete_session_artifacts(self, session_id: str) -> None:
        """Elimina artifacts de una sesión."""
        raise NotImplementedError

    @abstractmethod
    def save_summary(self, session_id: str, summary: dict) -> Path:
        raise NotImplementedError

    @abstractmethod
    def save_per_frame_rows(self, session_id: str, rows: list[dict]) -> Path:
        raise NotImplementedError

    @abstractmethod
    def save_per_detection_rows(self, session_id: str, rows: list[dict]) -> Path:
        raise NotImplementedError

    @abstractmethod
    def list_snapshots(self, session_id: str, limit: int = 20) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def list_annotated_videos(self, session_id: str) -> list[str]:
        raise NotImplementedError