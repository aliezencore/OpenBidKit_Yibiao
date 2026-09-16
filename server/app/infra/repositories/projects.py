from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.infra.db.engine import Database


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, name: str, project_id: str | None = None) -> str:
        new_id = project_id or uuid.uuid4().hex[:12]
        self._db.execute(
            "INSERT INTO projects(id, name, created_at) VALUES (?, ?, ?)",
            (new_id, name, utc_now()),
        )
        return new_id

    def get(self, project_id: str) -> dict | None:
        row = self._db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        return dict(row) if row else None

    def list_all(self) -> list[dict]:
        rows = self._db.query("SELECT * FROM projects ORDER BY created_at ASC")
        return [dict(row) for row in rows]
