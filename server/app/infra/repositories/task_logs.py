from __future__ import annotations

from app.infra.db.engine import Database


class TaskLogRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def append(self, project_id: str, task_domain: str, task_type: str,
               task_id: str, message: str, created_at: str) -> None:
        self._db.execute(
            "INSERT INTO task_logs(project_id, task_domain, task_type, task_id,"
            " message, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, task_domain, task_type, task_id, message, created_at),
        )

    def list_for_task(self, task_id: str, limit: int = 200) -> list[dict]:
        rows = self._db.query(
            "SELECT * FROM task_logs WHERE task_id = ? ORDER BY id ASC LIMIT ?",
            (task_id, limit),
        )
        return [dict(row) for row in rows]
