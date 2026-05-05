from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from src.domain.entities.inspection_session import InspectionSession
from src.domain.repositories.session_repository import SessionRepository


class CsvSessionRepository(SessionRepository):
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = self.base_dir / "sessions.csv"

    def save(self, session: InspectionSession) -> None:
        rows = self._read_all_rows()

        session_row = {
            "session_id": session.session_id,
            "strategy_name": session.strategy_name,
            "source_video": session.source_video,
            "started_at": session.started_at.isoformat(),
            "status": session.status,
            "completed_at": session.completed_at.isoformat() if session.completed_at else "",
            "parameters": repr(session.parameters),
        }

        updated = False
        for idx, row in enumerate(rows):
            if row["session_id"] == session.session_id:
                rows[idx] = session_row
                updated = True
                break

        if not updated:
            rows.append(session_row)

        self._write_rows(rows)

    def get_by_id(self, session_id: str) -> InspectionSession | None:
        rows = self._read_all_rows()
        for row in rows:
            if row["session_id"] == session_id:
                return self._row_to_entity(row)
        return None

    def list_recent(self, limit: int = 10) -> list[InspectionSession]:
        rows = self._read_all_rows()
        sessions = [self._row_to_entity(row) for row in rows]
        sessions.sort(key=lambda s: s.started_at, reverse=True)
        return sessions[:limit]

    def delete(self, session_id: str) -> None:
        rows = self._read_all_rows()
        filtered = [row for row in rows if row["session_id"] != session_id]
        self._write_rows(filtered)

    def _read_all_rows(self) -> list[dict]:
        if not self.csv_path.exists():
            return []

        with open(self.csv_path, "r", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def _write_rows(self, rows: list[dict]) -> None:
        fieldnames = [
            "session_id",
            "strategy_name",
            "source_video",
            "started_at",
            "status",
            "completed_at",
            "parameters",
        ]

        with open(self.csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def _row_to_entity(self, row: dict) -> InspectionSession:
        completed_at = row.get("completed_at", "").strip()
        return InspectionSession(
            session_id=row["session_id"],
            strategy_name=row["strategy_name"],
            source_video=row["source_video"],
            started_at=datetime.fromisoformat(row["started_at"]),
            status=row.get("status", "created"),
            completed_at=datetime.fromisoformat(completed_at) if completed_at else None,
            parameters={},
        )