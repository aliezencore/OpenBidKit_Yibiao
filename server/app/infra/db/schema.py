from __future__ import annotations

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS projects (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
      task_id TEXT PRIMARY KEY,
      project_id TEXT NOT NULL REFERENCES projects(id),
      domain TEXT NOT NULL,
      kind TEXT NOT NULL,
      status TEXT NOT NULL,
      progress INTEGER NOT NULL DEFAULT 0,
      stage_label TEXT,
      payload_json TEXT,
      checkpoint_json TEXT,
      stats_json TEXT,
      error TEXT,
      claimed_by TEXT,
      heartbeat_at TEXT,
      created_at TEXT NOT NULL,
      started_at TEXT,
      finished_at TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tasks_project_status
    ON tasks(project_id, status)
    """,
    """
    CREATE TABLE IF NOT EXISTS task_logs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      project_id TEXT NOT NULL,
      task_domain TEXT NOT NULL,
      task_type TEXT NOT NULL,
      task_id TEXT NOT NULL,
      message TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_task_logs_task
    ON task_logs(project_id, task_domain, task_type, task_id, id DESC)
    """,
)
