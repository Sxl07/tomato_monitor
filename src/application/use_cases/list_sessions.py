from __future__ import annotations

from src.domain.repositories.session_repository import SessionRepository


class ListSessionsUseCase:
    def __init__(self, session_repository: SessionRepository) -> None:
        self.session_repository = session_repository

    def execute(self, limit: int = 10) -> list:
        return self.session_repository.list_recent(limit=limit)