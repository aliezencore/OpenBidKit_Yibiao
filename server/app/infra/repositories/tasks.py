from __future__ import annotations

import json

from app.infra.db.engine import Database

ACTIVE_STATUSES = ("queued", "running")


class TaskRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, *, task_id: str, project_id: str, domain: str, kind: str,
               payload: dict, created_at: str) -> None:
        self._db.execute(
            "INSERT INTO tasks(task_id, project_id, domain, kind, status, progress,"
            " payload_json, created_at) VALUES (?, ?, ?, ?, 'queued', 0, ?, ?)",
            (task_id, project_id, domain, kind, json.dumps(payload, ensure_ascii=False), created_at),
        )

    def get(self, task_id: str) -> dict | None:
        row = self._db.query_one("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        return dict(row) if row else None

    def list_for_project(self, project_id: str) -> list[dict]:
        rows = self._db.query(
            "SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        )
        return [dict(row) for row in rows]

    def list_active(self, project_id: str | None = None) -> list[dict]:
        placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
        sql = f"SELECT * FROM tasks WHERE status IN ({placeholders})"
        params: list[object] = list(ACTIVE_STATUSES)
        if project_id is not None:
            sql += " AND project_id = ?"
            params.append(project_id)
        rows = self._db.query(sql, params)
        return [dict(row) for row in rows]

    def mark_running(self, task_id: str, started_at: str) -> None:
        self._db.execute(
            "UPDATE tasks SET status = 'running', started_at = ? WHERE task_id = ?",
            (started_at, task_id),
        )

    def set_progress(self, task_id: str, progress: int, stage_label: str | None) -> None:
        self._db.execute(
            "UPDATE tasks SET progress = ?, stage_label = ? WHERE task_id = ?",
            (progress, stage_label, task_id),
        )

    def set_checkpoint(self, task_id: str, state: dict) -> None:
        self._db.execute(
            "UPDATE tasks SET checkpoint_json = ? WHERE task_id = ?",
            (json.dumps(state, ensure_ascii=False), task_id),
        )

    def mark_finished(self, task_id: str, status: str, finished_at: str,
                      error: str | None = None) -> None:
        self._db.execute(
            "UPDATE tasks SET status = ?, finished_at = ?, error = ? WHERE task_id = ?",
            (status, finished_at, error, task_id),
        )
