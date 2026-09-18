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


DUPLICATE_INDEXES = (
    "idx_duplicate_check_files_role_order",
    "idx_duplicate_check_content_files_status",
    "idx_duplicate_check_metadata_file_order",
    "idx_duplicate_check_metadata_key",
    "idx_duplicate_check_outline_file_order",
    "idx_duplicate_check_outline_normalized",
    "idx_duplicate_check_outline_groups_order",
    "idx_duplicate_check_outline_pairwise_score",
    "idx_duplicate_check_content_duplicates_order",
    "idx_duplicate_check_content_occ_file",
    "idx_duplicate_check_duplicate_images_hash",
    "idx_duplicate_check_duplicate_images_order",
    "idx_duplicate_check_image_occ_file",
)


DUPLICATE_TABLES = (
    "duplicate_check_meta",
    "duplicate_check_files",
    "duplicate_check_content_files",
    "duplicate_check_metadata_items",
    "duplicate_check_outline_items",
    "duplicate_check_outline_groups",
    "duplicate_check_outline_pairwise",
    "duplicate_check_content_duplicates",
    "duplicate_check_content_occurrences",
    "duplicate_check_image_files",
    "duplicate_check_duplicate_images",
    "duplicate_check_image_occurrences",
    "duplicate_check_analysis_sections",
)


def _duplicate_object_count(db):
    return db.query_one(
        "SELECT COUNT(*) AS c FROM sqlite_master WHERE name LIKE 'duplicate_check_%'"
    )["c"]


def test_duplicate_tables_exist(tmp_path):
    db = make_db(tmp_path)
    apply_schema(db)
    names = {row["name"] for row in db.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [t for t in DUPLICATE_TABLES if t not in names]
    assert missing == [], f"缺表：{missing}"

    db.close()


def test_duplicate_check_tasks_is_not_created(tmp_path):
    """任务统一收归 tasks 表（spec §5.1），不得再建 domain 私有任务表。"""
    db = make_db(tmp_path)
    apply_schema(db)
    names = {row["name"] for row in db.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "duplicate_check_tasks" not in names

    db.close()


def test_duplicate_check_meta_is_project_scoped(tmp_path):
    """不复用 CHECK(id=1)，改 UNIQUE(project_id)，能容纳两个项目各一行。"""
    db = make_db(tmp_path)
    apply_schema(db)
    db.execute("INSERT INTO projects(id, name, created_at) VALUES ('p1','a','t')")
    db.execute("INSERT INTO projects(id, name, created_at) VALUES ('p2','b','t')")
    for pid in ("p1", "p2"):
        db.execute(
            "INSERT INTO duplicate_check_meta(project_id, created_at, updated_at) "
            "VALUES (?, 'now', 'now')", (pid,))
    assert db.query_one("SELECT COUNT(*) AS c FROM duplicate_check_meta")["c"] == 2

    db.close()


def test_duplicate_section_rows_are_project_scoped(tmp_path):
    """analysis_sections 同理：section 变成 (project_id, section) 联合主键。

    两个项目各插一行、且用**同一个 section='metadata'**：若退回单例
    `section TEXT PRIMARY KEY`，第二行必然撞主键而红。
    """
    db = make_db(tmp_path)
    apply_schema(db)
    db.execute("INSERT INTO projects(id, name, created_at) VALUES ('p1','a','t')")
    db.execute("INSERT INTO projects(id, name, created_at) VALUES ('p2','b','t')")
    db.execute(
        "INSERT INTO duplicate_check_analysis_sections"
        "(project_id, section, status, message, updated_at) "
        "VALUES ('p1','metadata','running','', 'now')")
    db.execute(
        "INSERT INTO duplicate_check_analysis_sections"
        "(project_id, section, status, message, updated_at) "
        "VALUES ('p2','metadata','done','', 'now')")
    assert db.query_one(
        "SELECT COUNT(*) AS c FROM duplicate_check_analysis_sections")["c"] == 2
    row = db.query_one(
        "SELECT status FROM duplicate_check_analysis_sections WHERE project_id = 'p1'")
    assert row["status"] == "running"

    db.close()


def test_duplicate_files_role_order_index(tmp_path):
    db = make_db(tmp_path)
    apply_schema(db)
    idx = {row["name"] for row in db.query(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_duplicate_check_files_role_order" in idx

    db.close()


def test_duplicate_tables_and_indexes_all_exist(tmp_path):
    """13 张表 + DUPLICATE_INDEXES 里列出的索引必须全部落在 sqlite_master。"""
    db = make_db(tmp_path)
    apply_schema(db)
    names = {row["name"] for row in db.query("SELECT name FROM sqlite_master")}
    missing_indexes = [i for i in DUPLICATE_INDEXES if i not in names]
    assert missing_indexes == [], f"缺索引：{missing_indexes}"

    db.close()


def test_apply_schema_is_idempotent_with_duplicate_tables(tmp_path):
    """第二次 apply_schema 不抛，且 sqlite_master 里的 duplicate_check_* 对象数不增。"""
    db = make_db(tmp_path)
    apply_schema(db)
    before = _duplicate_object_count(db)
    assert before > 0

    apply_schema(db)

    assert _duplicate_object_count(db) == before

    db.close()
