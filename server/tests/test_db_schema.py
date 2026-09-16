import sqlite3

import pytest

from app.infra.db.engine import Database
from app.infra.db.migrate import add_column_if_missing, apply_schema, table_columns

# 在 monkeypatch 动 `app.infra.db.engine.sqlite3.connect` 之前先抓住真函数，
# 供包装类内部创建真实连接用。
_REAL_CONNECT = sqlite3.connect


class _PragmaFailingConnection:
    """首次执行 PRAGMA 语句时抛 OperationalError 的连接包装（裁定 F7 回归用）。

    内部持有一个真实 `sqlite3.Connection` 并委托其关闭，保证测试不留真实连接；
    `row_factory` 等属性赋值按普通属性存储，与 `Database.__init__` 的行为兼容。
    """

    def __init__(self, *args, **kwargs):
        self._conn = _REAL_CONNECT(*args, **kwargs)
        self.close_calls = 0

    def execute(self, sql, *args, **kwargs):
        if sql.lstrip().upper().startswith("PRAGMA"):
            raise sqlite3.OperationalError("database is locked")
        return self._conn.execute(sql, *args, **kwargs)

    def close(self):
        self.close_calls += 1
        self._conn.close()


def make_db(tmp_path):
    return Database(tmp_path / "test.sqlite")


def test_wal_and_foreign_keys_enabled(tmp_path):
    db = make_db(tmp_path)

    assert db.query_one("PRAGMA journal_mode")[0].lower() == "wal"
    assert db.query_one("PRAGMA foreign_keys")[0] == 1

    db.close()


def test_apply_schema_creates_core_tables(tmp_path):
    db = make_db(tmp_path)

    apply_schema(db)
    names = {row[0] for row in db.query("SELECT name FROM sqlite_master WHERE type='table'")}

    assert {"projects", "tasks", "task_logs"} <= names

    db.close()


def test_apply_schema_is_idempotent(tmp_path):
    db = make_db(tmp_path)

    apply_schema(db)
    apply_schema(db)

    assert db.query_one("SELECT COUNT(*) FROM projects")[0] == 0

    db.close()


def test_tasks_table_has_scheme_b_reserved_columns(tmp_path):
    db = make_db(tmp_path)
    apply_schema(db)

    columns = table_columns(db, "tasks")

    assert {"claimed_by", "heartbeat_at", "checkpoint_json", "payload_json"} <= columns

    db.close()


def test_add_column_if_missing_is_idempotent(tmp_path):
    db = make_db(tmp_path)
    apply_schema(db)

    add_column_if_missing(db, "tasks", "stats_json", "TEXT")
    add_column_if_missing(db, "tasks", "stats_json", "TEXT")

    assert "stats_json" in table_columns(db, "tasks")

    db.close()


def test_add_column_if_missing_adds_absent_column(tmp_path):
    db = make_db(tmp_path)
    apply_schema(db)

    assert "progress_note" not in table_columns(db, "tasks")

    add_column_if_missing(db, "tasks", "progress_note", "TEXT")

    assert "progress_note" in table_columns(db, "tasks")

    db.close()


def test_execute_and_query_roundtrip(tmp_path):
    db = make_db(tmp_path)
    apply_schema(db)

    db.execute(
        "INSERT INTO projects(id, name, created_at) VALUES (?, ?, ?)",
        ("p1", "示例项目", "2026-09-15T00:00:00+00:00"),
    )
    row = db.query_one("SELECT name FROM projects WHERE id = ?", ("p1",))

    assert row["name"] == "示例项目"

    db.close()


def test_transaction_rolls_back_on_error(tmp_path):
    db = make_db(tmp_path)
    apply_schema(db)

    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO projects(id, name, created_at) VALUES (?, ?, ?)",
                ("p1", "回滚", "2026-09-15T00:00:00+00:00"),
            )
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert db.query_one("SELECT COUNT(*) FROM projects")[0] == 0

    db.close()


def test_foreign_key_violation_raises(tmp_path):
    db = make_db(tmp_path)
    apply_schema(db)

    try:
        db.execute(
            "INSERT INTO tasks(task_id, project_id, domain, kind, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("t1", "missing-project", "rejection", "rejection-check-run", "queued", "2026-09-15T00:00:00+00:00"),
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("应当因外键约束失败")

    db.close()


def test_init_closes_connection_when_pragma_fails(tmp_path, monkeypatch):
    """裁定 F7 回归：__init__ 里任一 PRAGMA 失败必须关闭已建连接再抛出。

    monkeypatch 的是 `app.infra.db.engine` 命名空间里的 `sqlite3.connect`
    （engine 模块 `import sqlite3`，monkeypatch 会在用例结束后自动还原）。
    """
    created: list[_PragmaFailingConnection] = []

    def fake_connect(*args, **kwargs):
        conn = _PragmaFailingConnection(*args, **kwargs)
        created.append(conn)
        return conn

    monkeypatch.setattr("app.infra.db.engine.sqlite3.connect", fake_connect)

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        Database(tmp_path / "pragma-fail.sqlite")

    assert len(created) == 1
    assert created[0].close_calls == 1
