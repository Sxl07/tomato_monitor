from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ModelMetadata:
    model_name: str
    model_version: str
    model_path: str
    device: str
    extra_info: Optional[dict] = None