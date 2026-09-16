# 平台底座 实施计划（P1）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭起标书检查服务的可运行骨架——FastAPI 进程、SQLite 存储、任务引擎、AI 客户端，并把「领域层不依赖框架」这条纪律变成会自动失败的护栏测试。

**Architecture:** 单体同进程（spec §3 D3）。`api → domain → infra` 单向依赖，`domain` 只声明 `Protocol`，由 `infra` 实现（依赖倒置）。SQLite 用标准库 `sqlite3` 直写 SQL（与现有 Node 侧 `sqliteDatabase.cjs` 同构，DDL 可 1:1 翻译，零额外依赖）。任务引擎用 asyncio 编排、CPU 密集段走 `ThreadPoolExecutor`。

**Tech Stack:** Python 3.13 · FastAPI · uvicorn · pydantic-settings · httpx · sqlite3（标准库）· pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-09-15-fastapi-check-service-design.md`

## Global Constraints

- Python 解释器（本机托管）：`C:/Users/eagle/.workbuddy/binaries/python/versions/3.13.12/python.exe`（实际 3.13.14）。
- 新代码全部位于 worktree `.worktrees/fastapi-check-service` 的 `server/` 目录下，分支 `fastapi-check-service`。
- **不得改动 `client/` 下任何文件**（spec §2.2）。
- 分支名必须**平铺**，不得含 `/`（本机 git 写嵌套引用会删除父目录下的兄弟分支）。
- 测试命令（Windows，在 `server/` 下执行）：`.venv/Scripts/python.exe -m pytest -q`
- 提交身份固定：`git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit`
- **不推送**。
- `app/domain/**` 内**禁止**出现 `fastapi` / `starlette` / `sqlalchemy` / `httpx` / `pydantic` 的 import（Task 5 的护栏测试会强制这一点）。
- SQLite 连接必须开启 `journal_mode=WAL`、`foreign_keys=ON`、`busy_timeout=5000`。
- 时间戳一律用 ISO 8601 UTC 字符串（`datetime.now(timezone.utc).isoformat()`），与现有 Node 侧 `new Date().toISOString()` 口径一致。
- 所有表的时间列与 hash 列用 `TEXT`，不用 SQLite 的日期类型。

## File Structure

| 文件 | 职责 |
|---|---|
| `server/pyproject.toml` | 依赖、pytest 配置、打包元数据 |
| `server/app/main.py` | `create_app()` 组装与模块级 `app` |
| `server/app/config.py` | `Settings` / `ModelConfig`，从环境变量加载 |
| `server/app/infra/db/engine.py` | `Database`：连接、WAL、线程安全执行 |
| `server/app/infra/db/schema.py` | 建表 DDL 常量 |
| `server/app/infra/db/migrate.py` | `apply_schema()`、`add_column_if_missing()` |
| `server/app/infra/repositories/projects.py` | 项目仓储 |
| `server/app/infra/repositories/tasks.py` | 任务仓储 |
| `server/app/infra/repositories/task_logs.py` | 任务日志仓储 |
| `server/app/domain/shared/protocols.py` | `TaskContextProtocol`、`AiClientProtocol`、`EventPublisherProtocol` |
| `server/app/infra/tasks/context.py` | `TaskContext`：progress / log / checkpoint / raise_if_cancelled |
| `server/app/infra/tasks/engine.py` | `TaskEngine`：提交、状态流转、进度节流、取消、并发闸门 |
| `server/app/infra/tasks/events.py` | `EventBus` 与 `sse_event_stream()` |
| `server/app/infra/ai/logging.py` | `log_ai_failure()` 单行日志 |
| `server/app/infra/ai/json_collect.py` | `extract_json()` 抽取与括号配平 |
| `server/app/infra/ai/client.py` | `AiClient`：chat / collect_json_response / 并发闸门 |
| `server/app/api/deps.py` | 应用状态依赖（db / repos / engine / settings） |
| `server/app/api/health.py` `projects.py` `tasks.py` | HTTP 路由 |
| `server/tests/**` | 单测 |

---

### Task 1: 项目骨架与 `/health`

**Files:**
- Create: `server/pyproject.toml`
- Create: `server/app/__init__.py`
- Create: `server/app/main.py`
- Create: `server/tests/test_health.py`

**Interfaces:**
- Consumes: 无
- Produces: `app.main.create_app() -> FastAPI`；`app.main.app: FastAPI`；`GET /health -> {"status": "ok"}`

- [ ] **Step 1: 建 venv 并写 `pyproject.toml`**

创建 `server/pyproject.toml`：

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "yibiao-check-service"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic-settings>=2.4",
  "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "pytest-asyncio>=0.24",
]

[tool.hatch.build.targets.wheel]
packages = ["app"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: 建 venv 并安装依赖**

```bash
cd /c/workspace/OpenBidKit_Yibiao/.worktrees/fastapi-check-service/server
"C:/Users/eagle/.workbuddy/binaries/python/versions/3.13.12/python.exe" -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

预期：输出 `Successfully installed fastapi-... uvicorn-... pytest-...`。

- [ ] **Step 3: 写失败的测试**

创建 `server/tests/test_health.py`：

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_health_returns_ok():
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 4: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_health.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 5: 写最小实现**

创建 `server/app/__init__.py`（空文件）。

创建 `server/app/main.py`：

```python
from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Yibiao Check Service")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_health.py -v`
Expected: PASS（1 passed）

- [ ] **Step 7: 提交**

```bash
git add server/pyproject.toml server/app/__init__.py server/app/main.py server/tests/test_health.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): FastAPI 骨架与 /health"
```

---

### Task 2: 配置层

**Files:**
- Create: `server/app/config.py`
- Create: `server/tests/test_config.py`

**Interfaces:**
- Consumes: 无
- Produces: `app.config.ModelConfig`（字段 `base_url: str`、`api_key: str`、`model_name: str`、`context_limit: int = 120000`、`timeout_seconds: float = 300.0`）；`app.config.Settings`（字段 `data_root: Path = Path("data")`、`text_model: ModelConfig`、`vision_model: ModelConfig`、`ai_max_concurrency: int = 8`、`task_global_concurrency: int = 4`、`progress_write_interval_ms: int = 200`、`cpu_workers: int = 2`、`sse_heartbeat_seconds: float = 15.0`）；`app.config.get_settings() -> Settings`

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_config.py`：

```python
from pathlib import Path

from app.config import ModelConfig, Settings, get_settings


def test_defaults():
    settings = Settings(_env_file=None)

    assert settings.data_root == Path("data")
    assert settings.ai_max_concurrency == 8
    assert settings.task_global_concurrency == 4
    assert settings.progress_write_interval_ms == 200
    assert settings.cpu_workers == 2
    assert settings.sse_heartbeat_seconds == 15.0
    assert settings.text_model.model_name == ""
    assert settings.text_model.context_limit == 120000
    assert settings.text_model.timeout_seconds == 300.0


def test_reads_nested_model_config_from_env(monkeypatch):
    monkeypatch.setenv("YIBIAO_TEXT_MODEL__BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("YIBIAO_TEXT_MODEL__API_KEY", "sk-text")
    monkeypatch.setenv("YIBIAO_TEXT_MODEL__MODEL_NAME", "gpt-4o-mini")
    monkeypatch.setenv("YIBIAO_VISION_MODEL__MODEL_NAME", "qwen-vl-max")
    monkeypatch.setenv("YIBIAO_AI_MAX_CONCURRENCY", "3")

    settings = Settings(_env_file=None)

    assert settings.text_model.base_url == "https://api.example.com/v1"
    assert settings.text_model.api_key == "sk-text"
    assert settings.text_model.model_name == "gpt-4o-mini"
    assert settings.vision_model.model_name == "qwen-vl-max"
    assert settings.ai_max_concurrency == 3


def test_get_settings_is_cached(monkeypatch):
    monkeypatch.setenv("YIBIAO_DATA_ROOT", "./tmpdata")
    get_settings.cache_clear()

    assert get_settings() is get_settings()

    get_settings.cache_clear()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 3: 写最小实现**

创建 `server/app/config.py`：

```python
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelConfig(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model_name: str = ""
    context_limit: int = 120000
    timeout_seconds: float = 300.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YIBIAO_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    data_root: Path = Path("data")
    text_model: ModelConfig = ModelConfig()
    vision_model: ModelConfig = ModelConfig()
    ai_max_concurrency: int = 8
    task_global_concurrency: int = 4
    progress_write_interval_ms: int = 200
    cpu_workers: int = 2
    sse_heartbeat_seconds: float = 15.0

    @property
    def db_path(self) -> Path:
        return self.data_root / "yibiao_check.sqlite"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/config.py server/tests/test_config.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): 配置层，模型配置走环境变量嵌套"
```

---

### Task 3: SQLite 引擎、schema 与幂等迁移

**Files:**
- Create: `server/app/infra/__init__.py`
- Create: `server/app/infra/db/__init__.py`
- Create: `server/app/infra/db/engine.py`
- Create: `server/app/infra/db/schema.py`
- Create: `server/app/infra/db/migrate.py`
- Create: `server/tests/test_db_schema.py`

**Interfaces:**
- Consumes: `app.config.Settings`
- Produces: `Database(db_path: Path)`，方法 `execute(sql, params=())`、`query(sql, params=()) -> list[sqlite3.Row]`、`query_one(sql, params=()) -> sqlite3.Row | None`、`transaction()`（上下文管理器）、`close()`；`schema.SCHEMA_STATEMENTS: tuple[str, ...]`；`migrate.apply_schema(db: Database) -> None`；`migrate.add_column_if_missing(db, table, column, ddl) -> None`；`migrate.table_columns(db, table) -> set[str]`

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_db_schema.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_db_schema.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.infra'`

- [ ] **Step 3: 写 `Database`**

创建 `server/app/infra/__init__.py` 与 `server/app/infra/db/__init__.py`（均为空文件）。

创建 `server/app/infra/db/engine.py`：

```python
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence


class Database:
    """单连接 + 互斥锁的 SQLite 封装。

    低并发场景下（内网少数人共用）这样做最简单且正确：WAL 让读不阻塞写，
    锁把同一连接上的并发访问串行化。将来若演进出多进程（方案 B），
    每个进程各自持连接，由 SQLite 的文件锁与 busy_timeout 负责互斥。
    """

    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._path),
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def executemany(self, sql: str, seq: Sequence[Sequence[Any]]) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.executemany(sql, [tuple(item) for item in seq])

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchone()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
```

- [ ] **Step 4: 跑测试确认 `Database` 相关用例通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_db_schema.py -v`
Expected: `test_wal_and_foreign_keys_enabled`、`test_execute_and_query_roundtrip` 通过；其余因 `app.infra.db.migrate` 不存在而 FAIL

- [ ] **Step 5: 写 schema**

创建 `server/app/infra/db/schema.py`。本计划只建三张核心表；业务表在各自的后续计划里追加。

```python
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
```

- [ ] **Step 6: 写迁移**

创建 `server/app/infra/db/migrate.py`：

```python
from __future__ import annotations

import sqlite3

from app.infra.db.engine import Database
from app.infra.db.schema import SCHEMA_STATEMENTS


def apply_schema(db: Database) -> None:
    """幂等建表。沿用现有 Node 侧 sqliteDatabase.cjs 的做法。"""
    for statement in SCHEMA_STATEMENTS:
        db.execute(statement)


def table_columns(db: Database, table: str) -> set[str]:
    return {row["name"] for row in db.query(f"PRAGMA table_info({table})")}


def add_column_if_missing(db: Database, table: str, column: str, ddl: str) -> None:
    """对齐现有 Node 侧 addColumnIfMissing 的语义：已存在则空操作。"""
    if column in table_columns(db, table):
        return
    try:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    except sqlite3.OperationalError as error:
        if "duplicate column name" not in str(error).lower():
            raise
```

- [ ] **Step 7: 跑测试确认全部通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_db_schema.py -v`
Expected: PASS（8 passed）

- [ ] **Step 8: 提交**

```bash
git add server/app/infra server/tests/test_db_schema.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): SQLite 引擎、核心 schema 与幂等迁移"
```

---

### Task 4: 仓储层（projects / tasks / task_logs）

**Files:**
- Create: `server/app/infra/repositories/__init__.py`
- Create: `server/app/infra/repositories/projects.py`
- Create: `server/app/infra/repositories/tasks.py`
- Create: `server/app/infra/repositories/task_logs.py`
- Create: `server/tests/test_repositories.py`

**Interfaces:**
- Consumes: `Database`、`apply_schema`
- Produces:
  - `ProjectRepository(db)`：`create(name: str, project_id: str | None = None) -> str`、`get(project_id: str) -> dict | None`、`list_all() -> list[dict]`
  - `TaskRepository(db)`：`create(task_id: str, project_id: str, domain: str, kind: str, payload: dict, created_at: str) -> None`、`get(task_id: str) -> dict | None`、`list_for_project(project_id: str) -> list[dict]`、`list_active(project_id: str | None = None) -> list[dict]`、`mark_running(task_id: str, started_at: str) -> None`、`set_progress(task_id: str, progress: int, stage_label: str | None) -> None`、`set_checkpoint(task_id: str, state: dict) -> None`、`mark_finished(task_id: str, status: str, finished_at: str, error: str | None = None) -> None`
  - `TaskLogRepository(db)`：`append(project_id, task_domain, task_type, task_id, message, created_at) -> None`、`list_for_task(task_id: str, limit: int = 200) -> list[dict]`
  - 三者的 `get`/`list_*` 返回 **dict**（不是 `sqlite3.Row`），键名与数据库列名一致

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_repositories.py`：

```python
import pytest

from app.infra.db.engine import Database
from app.infra.db.migrate import apply_schema
from app.infra.repositories.projects import ProjectRepository
from app.infra.repositories.task_logs import TaskLogRepository
from app.infra.repositories.tasks import TaskRepository

NOW = "2026-09-15T00:00:00+00:00"


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "repo.sqlite")
    apply_schema(database)
    yield database
    database.close()


@pytest.fixture
def repos(db):
    return ProjectRepository(db), TaskRepository(db), TaskLogRepository(db)


def test_project_create_and_get(repos):
    projects, _, _ = repos

    project_id = projects.create("示例项目")

    assert project_id
    assert projects.get(project_id)["name"] == "示例项目"
    assert projects.get("missing") is None


def test_task_lifecycle(repos):
    projects, tasks, _ = repos
    project_id = projects.create("P")

    tasks.create(
        task_id="t1", project_id=project_id, domain="duplicate",
        kind="duplicate-analysis", payload={"force": False}, created_at=NOW,
    )
    created = tasks.get("t1")
    assert created["status"] == "queued"
    assert created["progress"] == 0
    assert created["payload_json"] == '{"force": false}'
    assert created["claimed_by"] is None
    assert created["heartbeat_at"] is None

    tasks.mark_running("t1", NOW)
    assert tasks.get("t1")["status"] == "running"
    assert tasks.get("t1")["started_at"] == NOW

    tasks.set_progress("t1", 42, "正在提取元数据")
    assert tasks.get("t1")["progress"] == 42
    assert tasks.get("t1")["stage_label"] == "正在提取元数据"

    tasks.set_checkpoint("t1", {"stage": "outline"})
    assert tasks.get("t1")["checkpoint_json"] == '{"stage": "outline"}'

    tasks.mark_finished("t1", "success", NOW)
    assert tasks.get("t1")["status"] == "success"
    assert tasks.get("t1")["finished_at"] == NOW


def test_task_mark_finished_records_error(repos):
    projects, tasks, _ = repos
    project_id = projects.create("P")
    tasks.create(task_id="t1", project_id=project_id, domain="duplicate",
                 kind="duplicate-analysis", payload={}, created_at=NOW)

    tasks.mark_finished("t1", "error", NOW, error="模型超时")

    assert tasks.get("t1")["error"] == "模型超时"


def test_list_active_returns_only_unfinished(repos):
    projects, tasks, _ = repos
    project_id = projects.create("P")
    for task_id in ("t1", "t2", "t3"):
        tasks.create(task_id=task_id, project_id=project_id, domain="duplicate",
                     kind="duplicate-analysis", payload={}, created_at=NOW)
    tasks.mark_finished("t2", "success", NOW)

    active = tasks.list_active(project_id)

    assert {item["task_id"] for item in active} == {"t1", "t3"}


def test_list_for_project_filters_by_project(repos):
    projects, tasks, _ = repos
    p1 = projects.create("P1")
    p2 = projects.create("P2")
    tasks.create(task_id="t1", project_id=p1, domain="duplicate",
                 kind="duplicate-analysis", payload={}, created_at=NOW)
    tasks.create(task_id="t2", project_id=p2, domain="duplicate",
                 kind="duplicate-analysis", payload={}, created_at=NOW)

    assert [item["task_id"] for item in tasks.list_for_project(p1)] == ["t1"]


def test_task_logs_append_and_list_in_order(repos):
    projects, _, logs = repos
    project_id = projects.create("P")

    logs.append(project_id, "duplicate", "duplicate-analysis", "t1", "第一条", NOW)
    logs.append(project_id, "duplicate", "duplicate-analysis", "t1", "第二条", NOW)

    messages = [item["message"] for item in logs.list_for_task("t1")]

    assert messages == ["第一条", "第二条"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_repositories.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.infra.repositories'`

- [ ] **Step 3: 写三个仓储**

创建 `server/app/infra/repositories/__init__.py`（空文件）。

创建 `server/app/infra/repositories/projects.py`：

```python
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
```

创建 `server/app/infra/repositories/tasks.py`：

```python
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
```

创建 `server/app/infra/repositories/task_logs.py`：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_repositories.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/infra/repositories server/tests/test_repositories.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): projects/tasks/task_logs 仓储"
```

---

### Task 5: 领域层 Protocol 与「不碰框架」护栏

**Files:**
- Create: `server/app/domain/__init__.py`
- Create: `server/app/domain/shared/__init__.py`
- Create: `server/app/domain/shared/protocols.py`
- Create: `server/tests/test_domain_boundary.py`

**Interfaces:**
- Consumes: 无（`domain` 不得 import 任何 `infra` 或框架）
- Produces: `TaskContextProtocol`（`progress(percent: int, label: str | None = None) -> None`、`log(message: str) -> None`、`checkpoint(state: dict) -> None`、`raise_if_cancelled() -> None`）；`AiClientProtocol`（`async chat(messages: list[dict], *, use_vision: bool = False) -> str`、`async collect_json_response(messages: list[dict], *, use_vision: bool = False) -> dict`）；`EventPublisherProtocol`（`publish(task_id: str, event: dict) -> None`）

- [ ] **Step 1: 写失败的测试（含护栏）**

创建 `server/tests/test_domain_boundary.py`：

```python
import ast
from pathlib import Path

from app.domain.shared.protocols import (
    AiClientProtocol,
    EventPublisherProtocol,
    TaskContextProtocol,
)

DOMAIN_DIR = Path(__file__).resolve().parents[1] / "app" / "domain"
FORBIDDEN_ROOTS = {
    "fastapi",
    "starlette",
    "sqlalchemy",
    "httpx",
    "pydantic",
    "pydantic_settings",
    "sqlite3",
    "uvicorn",
}


def iter_domain_modules():
    return sorted(path for path in DOMAIN_DIR.rglob("*.py"))


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_domain_modules_exist():
    assert iter_domain_modules(), "domain 目录下应当存在模块"


def test_domain_does_not_import_frameworks():
    offenders = {}
    for path in iter_domain_modules():
        bad = imported_roots(path) & FORBIDDEN_ROOTS
        if bad:
            offenders[path.name] = sorted(bad)

    assert offenders == {}, f"domain 层不得依赖框架：{offenders}"


def test_domain_does_not_import_infra():
    offenders = []
    for path in iter_domain_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("app.infra") or "/infra" in node.module:
                    offenders.append(f"{path.name} -> {node.module}")

    assert offenders == [], f"domain 层不得依赖 infra：{offenders}"


class FakeContext:
    def __init__(self):
        self.progress_calls = []
        self.logs = []
        self.checkpoints = []
        self.cancelled = False

    def progress(self, percent, label=None):
        self.progress_calls.append((percent, label))

    def log(self, message):
        self.logs.append(message)

    def checkpoint(self, state):
        self.checkpoints.append(state)

    def raise_if_cancelled(self):
        if self.cancelled:
            raise RuntimeError("cancelled")


class FakeAi:
    async def chat(self, messages, *, use_vision=False):
        return "{}"

    async def collect_json_response(self, messages, *, use_vision=False):
        return {}


class FakePublisher:
    def publish(self, task_id, event):
        pass


def test_fakes_satisfy_protocols():
    assert isinstance(FakeContext(), TaskContextProtocol)
    assert isinstance(FakeAi(), AiClientProtocol)
    assert isinstance(FakePublisher(), EventPublisherProtocol)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_domain_boundary.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.domain'`

- [ ] **Step 3: 写 Protocol**

创建 `server/app/domain/__init__.py` 与 `server/app/domain/shared/__init__.py`（均为空文件）。

创建 `server/app/domain/shared/protocols.py`：

```python
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TaskContextProtocol(Protocol):
    """领域函数看到的任务上下文。

    对应现有实现 runRejectionCheckTask 的 { updateTask, checkpointTask } 两个参数。
    """

    def progress(self, percent: int, label: str | None = None) -> None: ...

    def log(self, message: str) -> None: ...

    def checkpoint(self, state: dict[str, Any]) -> None: ...

    def raise_if_cancelled(self) -> None: ...


@runtime_checkable
class AiClientProtocol(Protocol):
    """领域函数看到的模型客户端，对应现有 aiService。"""

    async def chat(self, messages: list[dict[str, Any]], *, use_vision: bool = False) -> str: ...

    async def collect_json_response(
        self, messages: list[dict[str, Any]], *, use_vision: bool = False
    ) -> dict[str, Any]: ...


@runtime_checkable
class EventPublisherProtocol(Protocol):
    """任务事件发布口，对应现有 taskService 推给 Renderer 的 tasks:event。"""

    def publish(self, task_id: str, event: dict[str, Any]) -> None: ...
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_domain_boundary.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/domain server/tests/test_domain_boundary.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): 领域层 Protocol 与不碰框架的护栏测试"
```

---

### Task 6: 任务引擎（提交 / 状态流转 / 进度节流 / 取消 / checkpoint）

**Files:**
- Create: `server/app/infra/tasks/__init__.py`
- Create: `server/app/infra/tasks/context.py`
- Create: `server/app/infra/tasks/engine.py`
- Create: `server/tests/test_task_engine.py`

**Interfaces:**
- Consumes: `TaskRepository`、`TaskLogRepository`、`TaskContextProtocol`、`EventPublisherProtocol`、`Settings`
- Produces:
  - `TaskContext(task_id, project_id, repo, log_repo, publisher, *, progress_interval_ms, now_fn=None, monotonic_fn=None)`
  - `TaskEngine(task_repo, log_repo, settings, *, publisher=None, now_fn=None, monotonic_fn=None)`，方法 `async submit(*, project_id, domain, kind, payload, runner) -> str`、`cancel(task_id) -> bool`、`has_active_task(project_id) -> bool`、`async drain() -> None`
  - `runner` 签名：`async def runner(ctx: TaskContext, payload: dict, previous_state: dict | None) -> None`

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_task_engine.py`：

```python
import asyncio

import pytest

from app.config import Settings
from app.infra.db.engine import Database
from app.infra.db.migrate import apply_schema
from app.infra.repositories.projects import ProjectRepository
from app.infra.repositories.task_logs import TaskLogRepository
from app.infra.repositories.tasks import TaskRepository
from app.infra.tasks.context import TaskCancelled, TaskContext
from app.infra.tasks.engine import TaskEngine


class Clock:
    """受控时间源，用于确定性地验证进度节流。"""

    def __init__(self):
        self.seconds = 0.0

    def now(self) -> str:
        return "2026-09-15T00:00:00+00:00"

    def monotonic(self) -> float:
        return self.seconds


class SpyPublisher:
    def __init__(self):
        self.events = []

    def publish(self, task_id, event):
        self.events.append((task_id, event))


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "engine.sqlite")
    apply_schema(db)
    projects = ProjectRepository(db)
    tasks = TaskRepository(db)
    logs = TaskLogRepository(db)
    clock = Clock()
    publisher = SpyPublisher()
    settings = Settings(_env_file=None, progress_write_interval_ms=200)
    engine = TaskEngine(tasks, logs, settings, publisher=publisher,
                        now_fn=clock.now, monotonic_fn=clock.monotonic)
    project_id = projects.create("P")
    yield {"db": db, "projects": projects, "tasks": tasks, "logs": logs,
           "engine": engine, "clock": clock, "publisher": publisher,
           "project_id": project_id}
    db.close()


def read(env, task_id):
    return env["tasks"].get(task_id)


async def test_submit_runs_runner_and_marks_success(env):
    seen = {}

    async def runner(ctx, payload, previous_state):
        seen["payload"] = payload
        seen["previous_state"] = previous_state
        ctx.log("开始")
        ctx.progress(100, "完成")

    task_id = await env["engine"].submit(
        project_id=env["project_id"], domain="duplicate", kind="duplicate-analysis",
        payload={"force": True}, runner=runner,
    )
    await env["engine"].drain()

    task = read(env, task_id)
    assert task["status"] == "success"
    assert task["progress"] == 100
    assert seen["payload"] == {"force": True}
    assert seen["previous_state"] is None
    assert [item["message"] for item in env["logs"].list_for_task(task_id)] == ["开始"]


async def test_runner_failure_marks_error_with_message(env):
    async def runner(ctx, payload, previous_state):
        raise RuntimeError("模型超时")

    task_id = await env["engine"].submit(
        project_id=env["project_id"], domain="duplicate", kind="duplicate-analysis",
        payload={}, runner=runner,
    )
    await env["engine"].drain()

    task = read(env, task_id)
    assert task["status"] == "error"
    assert task["error"] == "模型超时"


async def test_progress_write_is_throttled_but_still_broadcast(env):
    async def runner(ctx, payload, previous_state):
        ctx.progress(10, "a")
        env["clock"].seconds += 0.05
        ctx.progress(20, "b")
        env["clock"].seconds += 0.30
        ctx.progress(25, "c")

    task_id = await env["engine"].submit(
        project_id=env["project_id"], domain="duplicate", kind="duplicate-analysis",
        payload={}, runner=runner,
    )
    await env["engine"].drain()

    progress_events = [event for _, event in env["publisher"].events
                       if event["type"] == "progress"]
    persisted = [event["progress"] for event in progress_events if event["persisted"]]
    broadcast = [event["progress"] for event in progress_events]

    assert read(env, task_id)["progress"] == 25
    assert persisted == [10, 25], "20% 落在节流窗口内，不应落库"
    assert broadcast == [10, 20, 25], "节流只影响落库，不影响实时广播"


async def test_checkpoint_persists_state(env):
    async def runner(ctx, payload, previous_state):
        ctx.checkpoint({"stage": "outline", "done": [1, 2]})

    task_id = await env["engine"].submit(
        project_id=env["project_id"], domain="duplicate", kind="duplicate-analysis",
        payload={}, runner=runner,
    )
    await env["engine"].drain()

    assert read(env, task_id)["checkpoint_json"] == '{"stage": "outline", "done": [1, 2]}'


async def test_cancel_marks_cancelled_and_stops_runner(env):
    started = asyncio.Event()

    async def runner(ctx, payload, previous_state):
        started.set()
        for _ in range(100):
            ctx.raise_if_cancelled()
            await asyncio.sleep(0.01)

    task_id = await env["engine"].submit(
        project_id=env["project_id"], domain="duplicate", kind="duplicate-analysis",
        payload={}, runner=runner,
    )
    await started.wait()
    assert env["engine"].cancel(task_id) is True
    await env["engine"].drain()

    assert read(env, task_id)["status"] == "cancelled"


def test_raise_if_cancelled_only_raises_after_mark_cancelled(env):
    ctx = TaskContext(
        task_id="t1", project_id=env["project_id"], domain="duplicate",
        kind="duplicate-analysis", repo=env["tasks"], log_repo=env["logs"],
        now_fn=env["clock"].now, monotonic_fn=env["clock"].monotonic,
    )

    ctx.raise_if_cancelled()

    ctx.mark_cancelled()
    with pytest.raises(TaskCancelled):
        ctx.raise_if_cancelled()


def test_recover_interrupted_marks_running_tasks(env):
    project_id = env["project_id"]
    for task_id in ("t1", "t2", "t3"):
        env["tasks"].create(task_id=task_id, project_id=project_id, domain="duplicate",
                            kind="duplicate-analysis", payload={},
                            created_at="2026-09-15T00:00:00+00:00")
    env["tasks"].mark_running("t1", "2026-09-15T00:00:00+00:00")
    env["tasks"].mark_running("t2", "2026-09-15T00:00:00+00:00")
    env["tasks"].mark_finished("t3", "success", "2026-09-15T00:00:00+00:00")

    recovered = env["engine"].recover_interrupted()

    assert set(recovered) == {"t1", "t2"}
    assert env["tasks"].get("t1")["status"] == "interrupted"
    assert env["tasks"].get("t2")["status"] == "interrupted"
    assert env["tasks"].get("t3")["status"] == "success"


def test_recover_interrupted_is_noop_when_nothing_running(env):
    assert env["engine"].recover_interrupted() == []


async def test_has_active_task_reports_running_task(env):
    release = asyncio.Event()

    async def runner(ctx, payload, previous_state):
        await release.wait()

    assert env["engine"].has_active_task(env["project_id"]) is False
    task_id = await env["engine"].submit(
        project_id=env["project_id"], domain="duplicate", kind="duplicate-analysis",
        payload={}, runner=runner,
    )
    await asyncio.sleep(0)
    assert env["engine"].has_active_task(env["project_id"]) is True

    release.set()
    await env["engine"].drain()
    assert env["engine"].has_active_task(env["project_id"]) is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_task_engine.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.infra.tasks'`

- [ ] **Step 3: 写 `TaskContext`**

创建 `server/app/infra/tasks/__init__.py`（空文件）。

创建 `server/app/infra/tasks/context.py`：

```python
from __future__ import annotations

from typing import Any

from app.infra.repositories.task_logs import TaskLogRepository
from app.infra.repositories.tasks import TaskRepository


class TaskCancelled(Exception):
    """协作式取消。领域函数在每个耗时步骤前调用 raise_if_cancelled()。"""


class _NullPublisher:
    def publish(self, task_id: str, event: dict[str, Any]) -> None:
        return None


class TaskContext:
    def __init__(
        self,
        *,
        task_id: str,
        project_id: str,
        domain: str,
        kind: str,
        repo: TaskRepository,
        log_repo: TaskLogRepository,
        publisher: Any = None,
        progress_interval_ms: int = 200,
        now_fn,
        monotonic_fn,
    ) -> None:
        self.task_id = task_id
        self.project_id = project_id
        self.domain = domain
        self.kind = kind
        self._repo = repo
        self._log_repo = log_repo
        self._publisher = publisher or _NullPublisher()
        self._interval = max(0, progress_interval_ms) / 1000.0
        self._now = now_fn
        self._monotonic = monotonic_fn
        self._last_write_at = float("-inf")
        self._last_percent: int | None = None
        self._cancelled = False

    def progress(self, percent: int, label: str | None = None) -> None:
        value = max(0, min(100, int(percent)))
        now = self._monotonic()
        changed = value != self._last_percent
        elapsed = now - self._last_write_at
        # 裁定 R13（Task 6 评审）：写库条件必须是 `changed and elapsed >= interval`。
        # 原先写法（`changed and elapsed < interval` 才跳过）会让百分比**未变化**时
        # 也落到写库分支——runner 在多文件循环里重算进度停在同一个整数上很常见，
        # 会反复写同一个值。节流只约束中间帧；终态由 `_run` 的 `_flush_last_progress`
        # 兜底落库（裁定 R25/F2）。
        persisted = False
        if changed and elapsed >= self._interval:
            self._last_write_at = now
            self._last_percent = value
            self._repo.set_progress(self.task_id, value, label)
            persisted = True
        self._publisher.publish(self.task_id, {
            "type": "progress", "taskId": self.task_id,
            "progress": value, "stageLabel": label, "persisted": persisted,
        })

    def log(self, message: str) -> None:
        self._log_repo.append(self.project_id, self.domain, self.kind, self.task_id,
                              message, self._now())
        self._publisher.publish(self.task_id, {
            "type": "log", "taskId": self.task_id, "message": message,
        })

    def checkpoint(self, state: dict[str, Any]) -> None:
        self._repo.set_checkpoint(self.task_id, state)

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise TaskCancelled(self.task_id)

    def mark_cancelled(self) -> None:
        """由 TaskEngine.cancel() 调用；领域函数在下一个 raise_if_cancelled() 处退出。"""
        self._cancelled = True
```

- [ ] **Step 4: 写 `TaskEngine`**

创建 `server/app/infra/tasks/engine.py`：

```python
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.config import Settings
from app.infra.repositories.task_logs import TaskLogRepository
from app.infra.repositories.tasks import TaskRepository
from app.infra.tasks.context import TaskCancelled, TaskContext

Runner = Callable[[TaskContext, dict, dict | None], Awaitable[None]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskEngine:
    def __init__(
        self,
        task_repo: TaskRepository,
        log_repo: TaskLogRepository,
        settings: Settings,
        *,
        publisher: Any = None,
        now_fn=_utc_now,
        monotonic_fn=None,
    ) -> None:
        self._repo = task_repo
        self._log_repo = log_repo
        self._settings = settings
        self._publisher = publisher
        self._now = now_fn
        self._monotonic = monotonic_fn or (lambda: asyncio.get_running_loop().time())
        self._running: dict[str, asyncio.Task] = {}
        self._contexts: dict[str, TaskContext] = {}

    async def submit(self, *, project_id: str, domain: str, kind: str,
                     payload: dict, runner: Runner) -> str:
        task_id = f"{kind}_{uuid.uuid4().hex[:12]}"
        self._repo.create(task_id=task_id, project_id=project_id, domain=domain,
                          kind=kind, payload=payload, created_at=self._now())
        previous_state = self._load_previous_state(project_id, kind)
        ctx = TaskContext(
            task_id=task_id, project_id=project_id, domain=domain, kind=kind,
            repo=self._repo, log_repo=self._log_repo, publisher=self._publisher,
            progress_interval_ms=self._settings.progress_write_interval_ms,
            now_fn=self._now, monotonic_fn=self._monotonic,
        )
        self._contexts[task_id] = ctx
        self._publish(task_id, {"type": "status", "taskId": task_id, "status": "queued"})
        self._running[task_id] = asyncio.create_task(
            self._run(task_id, ctx, runner, payload, previous_state)
        )
        return task_id

    async def _run(self, task_id: str, ctx: TaskContext, runner: Runner,
                   payload: dict, previous_state: dict | None) -> None:
        self._repo.mark_running(task_id, self._now())
        self._publish(task_id, {"type": "status", "taskId": task_id, "status": "running"})
        try:
            await runner(ctx, payload, previous_state)
        except (TaskCancelled, asyncio.CancelledError):
            self._repo.mark_finished(task_id, "cancelled", self._now())
            self._publish(task_id, {"type": "status", "taskId": task_id, "status": "cancelled"})
        except Exception as error:  # noqa: BLE001 —— 任务边界必须记录所有失败
            self._repo.mark_finished(task_id, "error", self._now(), error=str(error))
            self._publish(task_id, {"type": "status", "taskId": task_id,
                                    "status": "error", "error": str(error)})
        else:
            self._repo.mark_finished(task_id, "success", self._now())
            self._publish(task_id, {"type": "status", "taskId": task_id, "status": "success"})
        finally:
            self._contexts.pop(task_id, None)
            self._running.pop(task_id, None)

    def cancel(self, task_id: str) -> bool:
        ctx = self._contexts.get(task_id)
        task = self._running.get(task_id)
        if ctx is None or task is None:
            return False
        ctx.mark_cancelled()
        return True

    def has_active_task(self, project_id: str) -> bool:
        for task_id in list(self._running):
            task = self._repo.get(task_id)
            if task and task["project_id"] == project_id:
                return True
        return False

    def recover_interrupted(self) -> list[str]:
        """进程启动时调用：把上次遗留的 running 任务标记为 interrupted。

        对齐 spec §6.4 —— 进程重启会中断进行中的任务，靠 checkpoint_json 续跑。
        """
        interrupted: list[str] = []
        for task in self._repo.list_active():
            if task["status"] != "running":
                continue
            self._repo.mark_finished(task["task_id"], "interrupted", self._now())
            interrupted.append(task["task_id"])
        return interrupted

    def _load_previous_state(self, project_id: str, kind: str) -> dict | None:
        rows = self._repo.list_for_project(project_id)
        for row in rows:
            if row["kind"] == kind and row["checkpoint_json"]:
                return json.loads(row["checkpoint_json"])
        return None

    def _publish(self, task_id: str, event: dict) -> None:
        if self._publisher is not None:
            self._publisher.publish(task_id, event)

    async def drain(self) -> None:
        while self._running:
            await asyncio.gather(*list(self._running.values()), return_exceptions=True)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_task_engine.py -v`
Expected: PASS（9 passed）

- [ ] **Step 6: 提交**

```bash
git add server/app/infra/tasks server/tests/test_task_engine.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): 任务引擎，含进度节流与协作式取消"
```

---

### Task 7: 并发闸门、事件总线与 SSE 流

**Files:**
- Create: `server/app/infra/tasks/events.py`
- Modify: `server/app/infra/tasks/engine.py`（接入闸门）
- Modify: `server/tests/test_task_engine.py`（追加闸门用例）
- Create: `server/tests/test_events.py`

**Interfaces:**
- Consumes: `EventPublisherProtocol`、`TaskEngine`
- Produces: `EventBus()`，方法 `publish(task_id, event)`、`subscribe(task_id) -> AsyncIterator[dict]`、`unsubscribe(task_id, queue)`；`sse_event_stream(bus, task_id, *, heartbeat_seconds: float = 15.0) -> AsyncIterator[str]`（产出 `data: {json}\n\n` 与 `: ping\n\n`）

- [ ] **Step 1: 写失败的测试（事件总线与 SSE）**

创建 `server/tests/test_events.py`：

```python
import asyncio
import json

from app.infra.tasks.events import EventBus, sse_event_stream


async def test_publish_reaches_subscriber():
    bus = EventBus()
    queue = bus.subscribe("t1")

    bus.publish("t1", {"type": "progress", "progress": 10})

    assert await asyncio.wait_for(queue.get(), timeout=1) == {"type": "progress", "progress": 10}


async def test_publish_without_subscriber_is_noop():
    bus = EventBus()

    bus.publish("nobody", {"type": "log"})

    assert bus.subscriber_count("nobody") == 0


async def test_unsubscribe_removes_queue():
    bus = EventBus()
    queue = bus.subscribe("t1")

    bus.unsubscribe("t1", queue)

    assert bus.subscriber_count("t1") == 0


async def test_sse_stream_serializes_events_as_json():
    bus = EventBus()
    stream = sse_event_stream(bus, "t1", heartbeat_seconds=60)
    first = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0)
    bus.publish("t1", {"type": "status", "status": "running"})

    chunk = await asyncio.wait_for(first, timeout=1)

    assert chunk == 'data: {"type": "status", "status": "running"}\n\n'
    await stream.aclose()


async def test_sse_stream_emits_heartbeat_when_idle():
    bus = EventBus()
    stream = sse_event_stream(bus, "t1", heartbeat_seconds=0.01)

    chunk = await asyncio.wait_for(stream.__anext__(), timeout=1)

    assert chunk == ": ping\n\n"
    await stream.aclose()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_events.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.infra.tasks.events'`

- [ ] **Step 3: 写事件总线与 SSE**

创建 `server/app/infra/tasks/events.py`：

```python
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator


class EventBus:
    """进程内任务事件广播。方案 A 下 SSE 直接走这里，不查库。"""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, task_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(task_id, set()).add(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        queues = self._subscribers.get(task_id)
        if not queues:
            return
        queues.discard(queue)
        if not queues:
            self._subscribers.pop(task_id, None)

    def publish(self, task_id: str, event: dict[str, Any]) -> None:
        for queue in self._subscribers.get(task_id, set()):
            queue.put_nowait(event)

    def subscriber_count(self, task_id: str) -> int:
        return len(self._subscribers.get(task_id, set()))


async def sse_event_stream(
    bus: EventBus, task_id: str, *, heartbeat_seconds: float = 15.0
) -> AsyncIterator[str]:
    queue = bus.subscribe(task_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    finally:
        bus.unsubscribe(task_id, queue)
```

- [ ] **Step 4: 跑事件测试确认通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_events.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 写并发闸门的失败测试**

在 `server/tests/test_task_engine.py` 末尾追加：

```python
async def test_project_gate_serializes_second_task(tmp_path):
    db = Database(tmp_path / "gate.sqlite")
    apply_schema(db)
    projects = ProjectRepository(db)
    tasks = TaskRepository(db)
    logs = TaskLogRepository(db)
    settings = Settings(_env_file=None, task_global_concurrency=4)
    engine = TaskEngine(tasks, logs, settings)
    project_id = projects.create("P")

    order = []
    release = asyncio.Event()

    async def runner(ctx, payload, previous_state):
        order.append(payload["name"])
        await release.wait()

    await engine.submit(project_id=project_id, domain="duplicate",
                        kind="duplicate-analysis", payload={"name": "first"}, runner=runner)
    await asyncio.sleep(0)
    await engine.submit(project_id=project_id, domain="duplicate",
                        kind="duplicate-analysis", payload={"name": "second"}, runner=runner)
    await asyncio.sleep(0.05)

    assert order == ["first"], "同一项目同时只允许一个写任务"

    release.set()
    await engine.drain()
    assert order == ["first", "second"]
    db.close()


async def test_global_gate_limits_across_projects(tmp_path):
    db = Database(tmp_path / "gate2.sqlite")
    apply_schema(db)
    projects = ProjectRepository(db)
    tasks = TaskRepository(db)
    logs = TaskLogRepository(db)
    settings = Settings(_env_file=None, task_global_concurrency=2)
    engine = TaskEngine(tasks, logs, settings)

    running = 0
    peak = 0
    release = asyncio.Event()

    async def runner(ctx, payload, previous_state):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await release.wait()
        running -= 1

    for index in range(5):
        await engine.submit(project_id=projects.create(f"P{index}"), domain="duplicate",
                            kind="duplicate-analysis", payload={}, runner=runner)
    for _ in range(10):
        await asyncio.sleep(0.01)

    assert peak == 2, "全局并发上限应生效"

    release.set()
    await engine.drain()
    db.close()
```

- [ ] **Step 6: 跑测试确认新增用例失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_task_engine.py -v`
Expected: 两个新增用例 FAIL（`order == ["first", "second"]`，闸门尚未实现）

- [ ] **Step 7: 实现闸门**

在 `__init__` 末尾（`self._contexts` 之后）追加：

```python
        self._global_gate = asyncio.Semaphore(settings.task_global_concurrency)
        self._project_gates: dict[str, asyncio.Semaphore] = {}
```

把 `submit` 末尾的

```python
        self._running[task_id] = asyncio.create_task(
            self._run(task_id, ctx, runner, payload, previous_state)
        )
        return task_id
```

替换为：

```python
        self._running[task_id] = asyncio.create_task(
            self._run_gated(task_id, ctx, runner, payload, previous_state, project_id)
        )
        return task_id

    def _project_gate(self, project_id: str) -> asyncio.Semaphore:
        gate = self._project_gates.get(project_id)
        if gate is None:
            gate = asyncio.Semaphore(1)
            self._project_gates[project_id] = gate
        return gate

    async def _run_gated(self, task_id: str, ctx: TaskContext, runner: Runner,
                         payload: dict, previous_state: dict | None,
                         project_id: str) -> None:
        async with self._global_gate:
            async with self._project_gate(project_id):
                await self._run(task_id, ctx, runner, payload, previous_state)
```

排队中的任务已经在 `self._running` 里，所以 `drain()` 不需要改动。

- [ ] **Step 8: 跑测试确认全部通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_task_engine.py tests/test_events.py -v`
Expected: PASS（16 passed）

- [ ] **Step 9: 提交**

```bash
git add server/app/infra/tasks server/tests/test_events.py server/tests/test_task_engine.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): 任务并发闸门、事件总线与 SSE 流"
```

---

### Task 8: CPU 线程池执行器

**Files:**
- Create: `server/app/infra/tasks/cpu.py`
- Create: `server/tests/test_cpu_executor.py`

**Interfaces:**
- Consumes: `Settings.cpu_workers`
- Produces: `CpuExecutor(max_workers: int)`，方法 `await run(fn, *args)`、`shutdown(wait: bool = True) -> None`

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_cpu_executor.py`：

```python
import asyncio
import threading
import time

from app.infra.tasks.cpu import CpuExecutor


async def test_run_returns_function_result():
    executor = CpuExecutor(max_workers=2)

    result = await executor.run(lambda a, b: a + b, 2, 3)

    assert result == 5
    executor.shutdown()


async def test_run_executes_off_the_event_loop_thread():
    executor = CpuExecutor(max_workers=2)
    caller = threading.get_ident()

    worker = await executor.run(threading.get_ident)

    assert worker != caller
    executor.shutdown()


async def test_run_limits_concurrency_to_max_workers():
    executor = CpuExecutor(max_workers=2)
    lock = threading.Lock()
    state = {"running": 0, "peak": 0}

    def work():
        with lock:
            state["running"] += 1
            state["peak"] = max(state["peak"], state["running"])
        time.sleep(0.05)
        with lock:
            state["running"] -= 1

    await asyncio.gather(*(executor.run(work) for _ in range(6)))

    assert state["peak"] == 2, "线程池不得超过 max_workers"
    executor.shutdown()


async def test_run_propagates_exception():
    executor = CpuExecutor(max_workers=1)

    def boom():
        raise ValueError("解析失败")

    try:
        await executor.run(boom)
    except ValueError as error:
        assert str(error) == "解析失败"
    else:
        raise AssertionError("异常应当传播")
    finally:
        executor.shutdown()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_cpu_executor.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.infra.tasks.cpu'`

- [ ] **Step 3: 写最小实现**

创建 `server/app/infra/tasks/cpu.py`：

```python
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class CpuExecutor:
    """CPU 密集段的受控线程池。

    查重指纹提取、相似度计算、docx 解析都是同步且吃 CPU 的，直接放在事件循环
    里会堵住整个服务（spec §6.3）。不用 asyncio.to_thread：它走事件循环的默认
    执行器，max_workers 高达 min(32, cpu+4)，对 CPU 密集任务过宽 —— 这里显式
    持有自己的池并设上限。
    """

    def __init__(self, max_workers: int) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, max_workers), thread_name_prefix="yibiao-cpu"
        )

    async def run(self, fn: Callable[..., T], *args: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, fn, *args)

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_cpu_executor.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/infra/tasks/cpu.py server/tests/test_cpu_executor.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): CPU 线程池执行器，隔离重活不堵事件循环"
```

---

### Task 9: AI 客户端（chat / JSON 收集与修复 / 失败日志）

**Files:**
- Create: `server/app/infra/ai/__init__.py`
- Create: `server/app/infra/ai/logging.py`
- Create: `server/app/infra/ai/json_collect.py`
- Create: `server/app/infra/ai/client.py`
- Create: `server/tests/test_ai_client.py`

**Interfaces:**
- Consumes: `ModelConfig`、`Settings`、`AiClientProtocol`
- Produces:
  - `log_ai_failure(scope: str, payload: dict) -> None`（始终打印单行 `key=value`）
  - `extract_json(text: str) -> dict | None`
  - `AiClient(settings, *, transport=None)`：`async chat(messages, *, use_vision=False) -> str`、`async collect_json_response(messages, *, use_vision=False) -> dict`、`async aclose() -> None`
  - `AiRequestError(Exception)`，携带 `message` 与 `status_code: int | None`

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_ai_client.py`：

```python
import json

import httpx
import pytest

from app.config import ModelConfig, Settings
from app.infra.ai.client import AiClient, AiRequestError
from app.infra.ai.json_collect import extract_json
from app.infra.ai.logging import log_ai_failure


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        # 裁定 R1（Task 9 评审）：围栏剥离只负责去 `'''`，不解析 JS 单引号字面量；
        # 故此处的围栏内必须是**合法 JSON**。
        ("'''{\"a\": 1}'''", {"a": 1}),
        ('前言 {"a": {"b": 2}} 后语', {"a": {"b": 2}}),
        ("没有 JSON", None),
        ("", None),
        ('{"a": 1', None),
    ],
)
def test_extract_json(raw, expected):
    assert extract_json(raw) == expected


def test_log_ai_failure_prints_single_line(capsys):
    log_ai_failure("主体印章图片识别", {"type": "http-error", "status": 429, "count": 7})

    out = capsys.readouterr().out.strip().splitlines()

    assert len(out) == 1
    assert out[0].startswith("ai_failure scope=主体印章图片识别")
    assert "type=http-error" in out[0]
    assert "status=429" in out[0]


def make_settings():
    return Settings(
        _env_file=None,
        text_model=ModelConfig(base_url="https://api.example.com/v1", api_key="sk-a",
                               model_name="text-model"),
        vision_model=ModelConfig(base_url="https://api.example.com/v1", api_key="sk-a",
                                 model_name="vision-model"),
        ai_max_concurrency=2,
    )


def make_client(handler):
    return AiClient(make_settings(), transport=httpx.MockTransport(handler))


async def test_chat_posts_openai_compatible_payload_and_returns_content():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "结果文本"}}],
        })

    client = make_client(handler)
    text = await client.chat([{"role": "user", "content": "你好"}])
    await client.aclose()

    assert text == "结果文本"
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-a"
    assert captured["body"]["model"] == "text-model"
    assert captured["body"]["messages"] == [{"role": "user", "content": "你好"}]


async def test_chat_uses_vision_model_when_requested():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = make_client(handler)
    await client.chat([{"role": "user", "content": "看图"}], use_vision=True)
    await client.aclose()

    assert captured["body"]["model"] == "vision-model"


async def test_http_error_raises_ai_request_error_with_status():
    def handler(request):
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    client = make_client(handler)
    with pytest.raises(AiRequestError) as info:
        await client.chat([{"role": "user", "content": "x"}])
    await client.aclose()

    assert info.value.status_code == 429
    assert "429" in str(info.value)


async def test_collect_json_response_parses_content():
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '```json\n{"findings": [1, 2]}\n```'}}],
        })

    client = make_client(handler)
    payload = await client.collect_json_response([{"role": "user", "content": "x"}])
    await client.aclose()

    assert payload == {"findings": [1, 2]}


async def test_collect_json_response_repairs_invalid_json():
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "这不是 JSON"}}],
            })
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"ok": true}'}}],
        })

    client = make_client(handler)
    payload = await client.collect_json_response([{"role": "user", "content": "x"}])
    await client.aclose()

    assert payload == {"ok": True}
    assert calls["count"] == 2


async def test_collect_json_response_raises_when_repair_also_fails():
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "仍然不是 JSON"}}],
        })

    client = make_client(handler)
    with pytest.raises(AiRequestError) as info:
        await client.collect_json_response([{"role": "user", "content": "x"}])
    await client.aclose()

    assert "JSON" in str(info.value)


async def test_missing_base_url_raises():
    settings = Settings(_env_file=None, text_model=ModelConfig())
    client = AiClient(settings, transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    with pytest.raises(AiRequestError) as info:
        await client.chat([{"role": "user", "content": "x"}])
    await client.aclose()

    assert "Base URL" in str(info.value)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_ai_client.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.infra.ai'`

- [ ] **Step 3: 写失败日志与 JSON 抽取**

创建 `server/app/infra/ai/__init__.py`（空文件）。

创建 `server/app/infra/ai/logging.py`：

```python
from __future__ import annotations

from typing import Any


def _render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    text = str(value)
    return text.replace("\n", " ").replace("\r", " ")


def log_ai_failure(scope: str, payload: dict[str, Any]) -> None:
    """始终打印的单行 key=value 日志，不受任何 debug 开关控制。

    对齐现有 utils/aiLog.cjs 的 logAiFailure 语义：这是排查线上 AI 调用
    唯一的常开通道，因此不做级别过滤、不做多行输出。
    """
    parts = [f"ai_failure scope={_render(scope)}"]
    for key, value in payload.items():
        parts.append(f"{key}={_render(value)}")
    print(" ".join(parts), flush=True)
```

创建 `server/app/infra/ai/json_collect.py`：

```python
from __future__ import annotations

import json
from typing import Any


def _strip_wrappers(text: str) -> str:
    trimmed = text.strip()
    if trimmed.startswith("'''") and trimmed.endswith("'''") and len(trimmed) >= 6:
        trimmed = trimmed[3:-3].strip()
    if trimmed.startswith("```"):
        lines = trimmed.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        trimmed = "\n".join(lines).strip()
    return trimmed


def _first_balanced_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出里抽出第一个完整 JSON 对象。

    依次尝试：原文直解 → 去三引号/代码围栏 → 括号配平切片。
    三条路径对齐现有 aiService 的 JSON 抽取行为。
    """
    if not text:
        return None
    for candidate in (_strip_wrappers(text), _first_balanced_object(_strip_wrappers(text))):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None
```

- [ ] **Step 4: 跑测试确认抽取与日志用例通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_ai_client.py -v`
Expected: `test_extract_json[*]`（7 个）与 `test_log_ai_failure_prints_single_line` 通过；其余 FAIL

- [ ] **Step 5: 写 `AiClient`**

创建 `server/app/infra/ai/client.py`：

```python
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import ModelConfig, Settings
from app.infra.ai.json_collect import extract_json
from app.infra.ai.logging import log_ai_failure

REPAIR_PROMPT = (
    "上一次输出不是合法 JSON。请只输出一个合法的 JSON 对象，不要任何解释、"
    "不要 Markdown 代码围栏、不要前后缀文字。"
)


class AiRequestError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AiClient:
    """OpenAI 兼容客户端。

    刻意不引 openai SDK：现有实现（aiService.cjs）是裸 fetch，
    引 SDK 会引入请求体默认值、重试、超时等行为差异，破坏与现状的对照。
    """

    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.text_model.timeout_seconds),
            transport=transport,
        )
        self._gate = asyncio.Semaphore(settings.ai_max_concurrency)

    async def aclose(self) -> None:
        await self._http.aclose()

    def _resolve(self, use_vision: bool) -> ModelConfig:
        config = self._settings.vision_model if use_vision else self._settings.text_model
        if not config.base_url.strip():
            label = "视觉模型" if use_vision else "文本模型"
            raise AiRequestError(f"请先配置{label} Base URL")
        return config

    async def chat(self, messages: list[dict[str, Any]], *, use_vision: bool = False) -> str:
        config = self._resolve(use_vision)
        url = f"{config.base_url.rstrip('/')}/chat/completions"
        body = {"model": config.model_name, "messages": messages}
        headers = {"Authorization": f"Bearer {config.api_key}"}
        async with self._gate:
            try:
                response = await self._http.post(url, json=body, headers=headers)
            except httpx.HTTPError as error:
                log_ai_failure("chat", {"type": "transport-error", "error": str(error)})
                raise AiRequestError(f"模型请求失败：{error}") from error
        if response.status_code >= 400:
            log_ai_failure("chat", {"type": "http-error", "status": response.status_code,
                                    "model": config.model_name})
            raise AiRequestError(
                f"模型返回 HTTP {response.status_code}：{response.text[:200]}",
                status_code=response.status_code,
            )
        try:
            return response.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as error:
            log_ai_failure("chat", {"type": "shape-error", "body": response.text[:200]})
            raise AiRequestError("模型响应结构不符合预期") from error

    async def collect_json_response(
        self, messages: list[dict[str, Any]], *, use_vision: bool = False
    ) -> dict[str, Any]:
        content = await self.chat(messages, use_vision=use_vision)
        parsed = extract_json(content)
        if parsed is not None:
            return parsed
        log_ai_failure("collect-json", {"type": "invalid-json", "attempt": 1,
                                        "preview": content[:120]})
        repair_messages = [
            *messages,
            {"role": "assistant", "content": content},
            {"role": "user", "content": REPAIR_PROMPT},
        ]
        repaired = await self.chat(repair_messages, use_vision=use_vision)
        parsed = extract_json(repaired)
        if parsed is None:
            log_ai_failure("collect-json", {"type": "invalid-json", "attempt": 2,
                                            "preview": repaired[:120]})
            raise AiRequestError("模型两次都未返回合法 JSON")
        return parsed
```

- [ ] **Step 6: 跑测试确认全部通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_ai_client.py -v`
Expected: PASS（16 passed）

- [ ] **Step 7: 提交**

```bash
git add server/app/infra/ai server/tests/test_ai_client.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): AI 客户端、JSON 收集与修复、常开失败日志"
```

---

### Task 10: HTTP API（projects / tasks / SSE）与应用装配

**Files:**
- Create: `server/app/api/__init__.py`
- Create: `server/app/api/deps.py`
- Create: `server/app/api/health.py`
- Create: `server/app/api/projects.py`
- Create: `server/app/api/tasks.py`
- Modify: `server/app/main.py`
- Modify: `server/tests/test_health.py`
- Create: `server/tests/test_api_tasks.py`

**Interfaces:**
- Consumes: 前述全部
- Produces:
  - `app.api.deps.build_state(settings) -> AppState`（含 `db`、`projects`、`tasks`、`logs`、`bus`、`engine`、`cpu`、`ai`；装配时调用 `engine.recover_interrupted()`）
  - 路由：`GET /health`、`POST /projects`、`GET /projects`、`GET /projects/{project_id}`、`GET /projects/{project_id}/tasks`、`GET /tasks/{task_id}`、`GET /tasks/{task_id}/events`（SSE）、`POST /tasks/{task_id}/cancel`
  - `create_app(settings: Settings | None = None) -> FastAPI`

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_api_tasks.py`：

```python
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, data_root=tmp_path, sse_heartbeat_seconds=0.01)
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def test_create_and_get_project(client):
    created = client.post("/projects", json={"name": "示例项目"})

    assert created.status_code == 201
    project_id = created.json()["id"]

    fetched = client.get(f"/projects/{project_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "示例项目"

    listed = client.get("/projects").json()
    assert [item["id"] for item in listed] == [project_id]


def test_get_missing_project_returns_404(client):
    assert client.get("/projects/nope").status_code == 404


def test_health_reports_db_ready(client):
    payload = client.get("/health").json()

    assert payload["status"] == "ok"
    assert payload["db"] == "ready"


def test_task_endpoints_report_queued_placeholders(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]

    assert client.get(f"/projects/{project_id}/tasks").json() == []
    assert client.get("/tasks/missing").status_code == 404
    assert client.post("/tasks/missing/cancel").status_code == 404


def test_sse_endpoint_streams_heartbeat(tmp_path):
    # 裁定 R24（Task 10 评审）：**本例不能用 TestClient**。
    # starlette 1.6.0 的 `_TestClientTransport.handle_request` 会把整段响应体
    # 缓冲进 BytesIO，只在 more_body=False 后才构造 Response；无限 SSE 流
    # 永不结束，`client.stream(...).__enter__()` 在任何读取之前就死锁。
    # （实测 httpx.ASGITransport 同样会缓冲。）因此改用真实回环服务器：
    # 在测试内起 `uvicorn.Server(port=0)` 的后台任务，用 httpx.AsyncClient
    # 流式读取首帧，并在收到帧后关停服务器。
    # **强制**：对流获取加 `asyncio.wait_for` 硬超时，使将来若引入缓冲会
    # 快速失败而非挂住套件。事件投递的确定性验证在 tests/test_events.py。
    # 实现见 server/tests/test_api_tasks.py 的同名用例。
    ...


def test_task_endpoint_returns_seeded_task(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    state = client.app.state.yibiao
    state.tasks.create(task_id="t1", project_id=project_id, domain="duplicate",
                       kind="duplicate-analysis", payload={"force": True},
                       created_at="2026-09-15T00:00:00+00:00")
    state.tasks.set_progress("t1", 50, "半程")

    body = client.get("/tasks/t1").json()

    assert body["status"] == "queued"
    assert body["progress"] == 50
    assert body["stage_label"] == "半程"
    assert body["payload_json"] == '{"force": true}'


def test_startup_recovers_interrupted_tasks(tmp_path):
    settings = Settings(_env_file=None, data_root=tmp_path)

    with TestClient(create_app(settings)) as first:
        project_id = first.post("/projects", json={"name": "P"}).json()["id"]
        state = first.app.state.yibiao
        state.tasks.create(task_id="t-interrupted", project_id=project_id,
                           domain="duplicate", kind="duplicate-analysis", payload={},
                           created_at="2026-09-15T00:00:00+00:00")
        state.tasks.mark_running("t-interrupted", "2026-09-15T00:00:00+00:00")

    # 模拟进程重启：同一个 data_root 重新装配应用
    with TestClient(create_app(settings)) as second:
        task = second.get("/tasks/t-interrupted").json()

    assert task["status"] == "interrupted"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_api_tasks.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.api'`

- [ ] **Step 3: 写 `AppState` 与依赖**

创建 `server/app/api/__init__.py`（空文件）。

创建 `server/app/api/deps.py`：

```python
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.config import Settings
from app.infra.ai.client import AiClient
from app.infra.db.engine import Database
from app.infra.db.migrate import apply_schema
from app.infra.repositories.projects import ProjectRepository
from app.infra.repositories.task_logs import TaskLogRepository
from app.infra.repositories.tasks import TaskRepository
from app.infra.tasks.cpu import CpuExecutor
from app.infra.tasks.engine import TaskEngine
from app.infra.tasks.events import EventBus


@dataclass
class AppState:
    settings: Settings
    db: Database
    projects: ProjectRepository
    tasks: TaskRepository
    logs: TaskLogRepository
    bus: EventBus
    engine: TaskEngine
    cpu: CpuExecutor
    ai: AiClient

    async def aclose(self) -> None:
        await self.ai.aclose()
        self.cpu.shutdown(wait=False)
        self.db.close()


def build_state(settings: Settings) -> AppState:
    db = Database(settings.db_path)
    apply_schema(db)
    projects = ProjectRepository(db)
    tasks = TaskRepository(db)
    logs = TaskLogRepository(db)
    bus = EventBus()
    engine = TaskEngine(tasks, logs, settings, publisher=bus)
    # 上次进程被强杀时遗留的 running 任务标为 interrupted（spec §6.4）
    engine.recover_interrupted()
    cpu = CpuExecutor(settings.cpu_workers)
    ai = AiClient(settings)
    return AppState(settings=settings, db=db, projects=projects, tasks=tasks,
                    logs=logs, bus=bus, engine=engine, cpu=cpu, ai=ai)


def get_state(request: Request) -> AppState:
    return request.app.state.yibiao
```

- [ ] **Step 4: 写路由**

创建 `server/app/api/health.py`：

```python
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import AppState, get_state

router = APIRouter()


@router.get("/health")
def health(state: AppState = Depends(get_state)) -> dict[str, str]:
    state.db.query_one("SELECT 1")
    return {"status": "ok", "db": "ready"}
```

创建 `server/app/api/projects.py`：

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import AppState, get_state

router = APIRouter()


class CreateProjectRequest(BaseModel):
    name: str


@router.post("/projects", status_code=201)
def create_project(body: CreateProjectRequest, state: AppState = Depends(get_state)) -> dict:
    project_id = state.projects.create(body.name)
    return state.projects.get(project_id)


@router.get("/projects")
def list_projects(state: AppState = Depends(get_state)) -> list[dict]:
    return state.projects.list_all()


@router.get("/projects/{project_id}")
def get_project(project_id: str, state: AppState = Depends(get_state)) -> dict:
    project = state.projects.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project
```

创建 `server/app/api/tasks.py`：

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api.deps import AppState, get_state
from app.infra.tasks.events import sse_event_stream

router = APIRouter()


@router.get("/projects/{project_id}/tasks")
def list_project_tasks(project_id: str, state: AppState = Depends(get_state)) -> list[dict]:
    return state.tasks.list_for_project(project_id)


@router.get("/tasks/{task_id}")
def get_task(task_id: str, state: AppState = Depends(get_state)) -> dict:
    task = state.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("/tasks/{task_id}/events")
def task_events(task_id: str, state: AppState = Depends(get_state)) -> StreamingResponse:
    return StreamingResponse(
        sse_event_stream(state.bus, task_id,
                         heartbeat_seconds=state.settings.sse_heartbeat_seconds),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, state: AppState = Depends(get_state)) -> dict:
    if state.tasks.get(task_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"cancelled": state.engine.cancel(task_id)}
```

- [ ] **Step 5: 改写 `main.py` 完成装配**

把 `server/app/main.py` 整体替换为：

```python
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, projects, tasks
from app.api.deps import build_state
from app.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.yibiao = build_state(resolved)
        try:
            yield
        finally:
            await app.state.yibiao.aclose()

    app = FastAPI(title="Yibiao Check Service", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(tasks.router)
    return app


app = create_app()
```

- [ ] **Step 6: 更新 `test_health.py` 的期望值**

`/health` 现在多返回一个 `db` 字段。把 `server/tests/test_health.py` 整体替换为：

```python
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, data_root=tmp_path))) as test_client:
        yield test_client


def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ready"}
```

- [ ] **Step 7: 跑全部测试**

Run: `cd server && .venv/Scripts/python.exe -m pytest -q`
Expected: PASS（全部通过）

- [ ] **Step 8: 手工验证服务能起来**

```bash
cd /c/workspace/OpenBidKit_Yibiao/.worktrees/fastapi-check-service/server
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8123 &
sleep 3
curl -s http://127.0.0.1:8123/health
curl -s -X POST http://127.0.0.1:8123/projects -H "Content-Type: application/json" -d '{"name":"冒烟"}'
kill %1
```

预期：第一条返回 `{"status":"ok","db":"ready"}`；第二条返回含 `id` 与 `name` 的项目 JSON。

- [ ] **Step 9: 提交**

```bash
git add server/app/api server/app/main.py server/tests
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): HTTP API、SSE 端点与应用装配"
```

---

## 完成标准

- `cd server && .venv/Scripts/python.exe -m pytest -q` 全绿。
- `uvicorn app.main:app` 起得来，`/health` 返回 `{"status":"ok","db":"ready"}`。
- `POST /projects` 能建项目并在 `GET /projects` 里列出。
- 任务引擎能提交假 runner 跑完，状态落库、进度被节流、取消生效、SSE 能推事件。
- CPU 密集段有受控线程池（`CpuExecutor`）且并发不超过 `cpu_workers`。
- 进程重启后遗留的 `running` 任务被标为 `interrupted`（`build_state` 装配时自动执行）。
- `tests/test_domain_boundary.py` 通过——这是后续三个计划的护栏：任何人在 `app/domain/` 里 import 框架都会被测试拦下。
