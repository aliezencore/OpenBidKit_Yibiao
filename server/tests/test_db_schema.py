import sqlite3

from app.infra.db.engine import Database
from app.infra.db.migrate import add_column_if_missing, apply_schema, table_columns


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
