"""Model availability verification service."""
from enum import Enum
from pathlib import Path


class ModelStatus(str, Enum):
    AVAILABLE = "available"
    NOT_FOUND = "not_found"


class ModelService:
    """Lightweight model file existence check. Does NOT load model into memory."""

    def __init__(self, model_path: Path) -> None:
        self._model_path = model_path

    def check_availability(self) -> ModelStatus:
        """Return AVAILABLE if model file exists, NOT_FOUND otherwise."""
        if self._model_path.exists() and self._model_path.is_file():
            return ModelStatus.AVAILABLE
        return ModelStatus.NOT_FOUND
