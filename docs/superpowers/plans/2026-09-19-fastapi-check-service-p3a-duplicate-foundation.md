# P3a 查重域底座 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「标书查重的编排骨架」整体搬到新服务：15 张 `duplicate_check_*` 表落库、文件簿记（招标 / 投标按 `role` 入表）、四段状态机（`metadata`/`outline`/`content`/`image`）、`signature` 幂等、`overallProgress` 等权平均、REST + SSE 可观察四段推进。**四段跑零算法占位实现**——本计划钉的是闭环与状态机，不是算法。

**Architecture:** 分三层。`app/domain/duplicate/` 放纯逻辑（签名、进度、状态机状态归一化、簿记校验），不碰框架与 DB；`app/infra/repositories/duplicate.py` 放 15 张表的读写（沿用 P1 `Database`）；`app/infra/tasks/duplicate_runner.py` 放编排（`runDuplicateAnalysis` 的 Python 等价物），把四段算法通过**显式注入的 callable** 接进来，P3a 注入占位、P3b/P3c/P3d 逐段替换。

**换算法不换编排**是本计划最重要的设计决定：`DuplicateAnalysisRunner` 接受一个 `sections` 字典（`{"metadata": callable, "outline": callable, ...}`），P3a 注册的是「走一遍进度、写零结果」的 stub，P3b 起只替换对应 callable —— 编排、幂等、进度、签名作废全部不动，也就不用重测。

**Tech Stack:** stdlib（`hashlib` / `json` / `datetime`）+ 既有 `Database` / `TaskEngine` / `EventPublisher` + FastAPI（**仅** `app/api/` 层）

**Spec:** `docs/superpowers/specs/2026-09-15-fastapi-check-service-design.md` §5.1 / §5.2（裁定 R31）/ §6 / §9.2 / §10

**参考实现（只读，用于对照行为）：** `client/electron/services/duplicateCheckService.cjs`（2870 行）、`client/electron/services/duplicateCheckStore.cjs`（987 行）、`client/electron/services/sqliteDatabase.cjs:380-585`

## Global Constraints

- 工作区 `.worktrees/fastapi-check-service`，分支 `fastapi-check-service`（**平铺名，不得含 `/`**）。
- **不得改动 `client/` 下任何文件**。参考实现只读。
- 在 P1 + P2a 之上增量开发；**不得破坏既有 290 个测试**。
- 提交身份 `git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit`；**不推送**；按显式路径 `git add`。
- `app/domain/**` 禁止 import `fastapi` / `starlette` / `sqlalchemy` / `httpx` / `pydantic` / `sqlite3`（护栏测试强制）。解析 / 图片库只能出现在 `app/infra/parsing/`。
- **本计划不新增任何依赖。**
- 验收标准是**行为对齐**（spec §8.5 的精神），不是逐字节一致。字面量（阈值 / 文案 / 表名列名）必须与参考实现逐字相同的地方，任务里会显式标注「逐字」。

## 明确不做（留给后续计划）

- **P3b**：正文切句、正文去重与招标匹配、目录抽取与分组、相似度算法。
- **P3c**：图片出现位置提取、按图片字节 hash 去重。
- **P3d**：元数据二进制指纹（docx rsid / OLE / PDF）、6 工作表 Excel 导出、前端页面。

P3a **不碰 `extractMetadata` / `splitContentSentences` / `extractOutlineItems` / `extractImageOccurrences` 的任何算法**；四段 stub 只做「推进度 + 写空结果 + 写正确的状态机终态」。

## 已定接口（P3a 冻结，后续计划依赖）

这些签名一旦本计划结束就冻结，P3b/P3c/P3d 只能填充实现、不能改签名。**改动即破坏下游计划**。

```python
# app/domain/duplicate/signature.py
def compute_analysis_signature(files: Sequence[FileRef]) -> str: ...
    # 逐字对齐 duplicateCheckService.cjs:126-131：
    #   '|'.join(f"{file_path}|{size}|{modified_at}") 去重后按行排序？→ 不排序、不去重，
    #   保持输入顺序；整体 '\n'.join 后 sha1。
    # ⚠ 参考实现是 [...tenderFiles, ...bidFiles] 的**输入顺序**，不排序。

def stable_file_id(file_path: str, file_name: str) -> str: ...
    # 逐字对齐 :118-120：优先 file.id；无 id 时 sha1(file_path or file_name)
    # P3a 的 FileRef 没有 id 字段（上传后由 DB 生成），故恒走 sha1 分支，
    # 但函数保留 id 参数以便 P4 迁移脚本复用。

# app/domain/duplicate/progress.py
def analysis_progress(state: SectionState | None) -> int: ...
    # 逐字对齐 :2229-2233：None→0；success/error→100；否则 clamp(progress, 0, 99)

def overall_progress(states: Mapping[str, SectionState | None]) -> int: ...
    # 逐字对齐 :2235-2242：四段等权平均，**向下取整**（Math.round 对 .5 上取，
    # 但四段整数平均的 .5 只可能出现在…→ 见 Task 4 的边界用例）
    # 顺序固定 metadata, outline, content, image

def latest_analysis_message(states: Mapping[str, SectionState | None]) -> str: ...
    # 逐字对齐 :2245-2251：image → content → outline → metadata → '标书查重分析运行中。'

# app/domain/duplicate/state.py
ANALYSIS_SECTIONS: tuple[str, ...] = ("metadata", "outline", "content", "image")
SECTION_STATUSES = frozenset({"pending", "running", "success", "error"})

def normalize_section_status(value: object, *, fallback: str = "pending") -> str: ...

@dataclass(frozen=True)
class SectionState:
    status: str = "pending"
    progress: int = 0
    message: str = ""
    signature: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    stats: Mapping[str, Any] = field(default_factory=dict)

def build_section_stats_patch(section: str, analysis: Mapping[str, Any]) -> dict[str, Any]: ...
    # 逐字对齐 duplicateCheckStore.cjs:179-222 的四段白名单：
    #   metadata → contentExtraction / metadataExtraction / logs / files(裁剪)
    #   outline  → tenderSentenceCount / tenderMatchedItemCount / extraction / files(裁剪)
    #   content  → tenderSentenceCount / tenderMatchedSentenceCount / totalSentenceCount / extraction
    #   image    → extraction / totalImageCount
    # "hasOwn" 语义：键不存在则不产出（区别于「存在但为 None」）
```

---

### Task 1: 15 张查重表落 schema

**Files:**
- Modify: `server/app/infra/db/schema.py`
- Test: `server/tests/test_db_schema.py`（追加）

**Interfaces:**
- Consumes: 既有 `SCHEMA_STATEMENTS`
- Produces: 全部 15 张 `duplicate_check_*` 表 + 索引。**列名、类型、约束、默认值、索引名逐字照抄 `sqliteDatabase.cjs:380-585`**，只做两处 spec 要求的改动：

  1. 所有表去掉 `CHECK (id = 1)` / `type TEXT PRIMARY KEY` 的单例设计，改为带 `project_id`
  2. 并入 `tasks` 表的两张（`duplicate_check_tasks` 不建）

- [ ] **Step 1: 写失败的测试**

在 `server/tests/test_db_schema.py` 追加：

```python
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


def test_duplicate_tables_exist(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    apply_schema(db)
    names = {row["name"] for row in db.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [t for t in DUPLICATE_TABLES if t not in names]
    assert missing == [], f"缺表：{missing}"


def test_duplicate_check_tasks_is_not_created(tmp_path):
    """任务统一收归 tasks 表（spec §5.1），不得再建 domain 私有任务表。"""
    db = Database(tmp_path / "t.sqlite")
    apply_schema(db)
    names = {row["name"] for row in db.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "duplicate_check_tasks" not in names


def test_duplicate_check_meta_is_project_scoped(tmp_path):
    """不复用 CHECK(id=1)，改 UNIQUE(project_id)，能容纳两个项目各一行。"""
    db = Database(tmp_path / "t.sqlite")
    apply_schema(db)
    db.execute("INSERT INTO projects(id, name, created_at) VALUES ('p1','a','t')")
    db.execute("INSERT INTO projects(id, name, created_at) VALUES ('p2','b','t')")
    for pid in ("p1", "p2"):
        db.execute(
            "INSERT INTO duplicate_check_meta(project_id, created_at, updated_at) "
            "VALUES (?, 'now', 'now')", (pid,))
    assert db.query_one("SELECT COUNT(*) AS c FROM duplicate_check_meta")["c"] == 2


def test_duplicate_section_rows_are_project_scoped(tmp_path):
    """analysis_sections 同理：section 变成 (project_id, section) 联合主键。"""
    db = Database(tmp_path / "t.sqlite")
    apply_schema(db)
    db.execute("INSERT INTO projects(id, name, created_at) VALUES ('p1','a','t')")
    db.execute(
        "INSERT INTO duplicate_check_analysis_sections"
        "(project_id, section, status, message, updated_at) "
        "VALUES ('p1','metadata','running','', 'now')")
    row = db.query_one("SELECT status FROM duplicate_check_analysis_sections")
    assert row["status"] == "running"


def test_duplicate_files_role_order_index(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    apply_schema(db)
    idx = {row["name"] for row in db.query(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_duplicate_check_files_role_order" in idx


def test_apply_schema_is_idempotent_with_duplicate_tables(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    apply_schema(db)
    apply_schema(db)  # 不抛
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd server && .venv/Scripts/python.exe -m pytest tests/test_db_schema.py -k duplicate -q
```

预期：`test_duplicate_tables_exist` 失败（缺 13 张表）。

- [ ] **Step 3: 写实现**

在 `server/app/infra/db/schema.py` 的 `SCHEMA_STATEMENTS` 末尾追加。**逐字照抄参考实现的列定义**，改动处已用注释标出：

```python
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_meta (
      project_id TEXT PRIMARY KEY REFERENCES projects(id),
      step TEXT NOT NULL DEFAULT 'upload',
      active_analysis_tab TEXT NOT NULL DEFAULT 'metadata',
      current_signature TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_files (
      project_id TEXT NOT NULL REFERENCES projects(id),
      file_id TEXT NOT NULL,
      role TEXT NOT NULL,
      file_name TEXT NOT NULL,
      file_path TEXT NOT NULL,
      extension TEXT NOT NULL,
      size INTEGER NOT NULL DEFAULT 0,
      modified_at TEXT,
      sort_order INTEGER NOT NULL DEFAULT 0,
      content_hash TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (project_id, file_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_files_role_order
    ON duplicate_check_files(project_id, role, sort_order)
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_analysis_sections (
      project_id TEXT NOT NULL REFERENCES projects(id),
      section TEXT NOT NULL,
      status TEXT NOT NULL,
      progress INTEGER NOT NULL DEFAULT 0,
      message TEXT NOT NULL DEFAULT '',
      signature TEXT,
      stats_json TEXT,
      started_at TEXT,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (project_id, section)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_content_files (
      project_id TEXT NOT NULL REFERENCES projects(id),
      file_id TEXT NOT NULL,
      status TEXT NOT NULL,
      content_path TEXT,
      content_length INTEGER NOT NULL DEFAULT 0,
      parser_label TEXT,
      error TEXT,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (project_id, file_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_content_files_status
    ON duplicate_check_content_files(project_id, status)
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_metadata_items (
      project_id TEXT NOT NULL REFERENCES projects(id),
      id INTEGER NOT NULL,
      file_id TEXT NOT NULL,
      key TEXT NOT NULL,
      label TEXT NOT NULL,
      value TEXT NOT NULL DEFAULT '',
      normalized TEXT,
      date_day TEXT,
      comparable INTEGER NOT NULL DEFAULT 0,
      date_comparable INTEGER NOT NULL DEFAULT 0,
      sort_order INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (project_id, id),
      UNIQUE (project_id, file_id, key)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_metadata_file_order
    ON duplicate_check_metadata_items(project_id, file_id, sort_order)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_metadata_key
    ON duplicate_check_metadata_items(project_id, key)
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_outline_items (
      project_id TEXT NOT NULL REFERENCES projects(id),
      item_id TEXT NOT NULL,
      file_id TEXT NOT NULL,
      parent_item_id TEXT,
      level INTEGER NOT NULL,
      number TEXT,
      title TEXT NOT NULL,
      normalized_title TEXT NOT NULL,
      path_titles_json TEXT NOT NULL,
      normalized_path TEXT NOT NULL,
      source TEXT NOT NULL,
      confidence REAL NOT NULL DEFAULT 0,
      sort_order INTEGER NOT NULL DEFAULT 0,
      from_tender INTEGER NOT NULL DEFAULT 0,
      matched_tender_sentence TEXT,
      PRIMARY KEY (project_id, item_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_outline_file_order
    ON duplicate_check_outline_items(project_id, file_id, sort_order)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_outline_normalized
    ON duplicate_check_outline_items(project_id, normalized_title, normalized_path)
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_outline_groups (
      project_id TEXT NOT NULL REFERENCES projects(id),
      group_id TEXT NOT NULL,
      type TEXT NOT NULL,
      title TEXT NOT NULL,
      score REAL NOT NULL DEFAULT 0,
      file_ids_json TEXT NOT NULL,
      item_ids_json TEXT NOT NULL,
      paths_json TEXT NOT NULL,
      sort_order INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (project_id, group_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_outline_groups_order
    ON duplicate_check_outline_groups(project_id, sort_order)
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_outline_pairwise (
      project_id TEXT NOT NULL REFERENCES projects(id),
      id INTEGER NOT NULL,
      file_a_id TEXT NOT NULL,
      file_b_id TEXT NOT NULL,
      score REAL NOT NULL DEFAULT 0,
      title_overlap REAL NOT NULL DEFAULT 0,
      path_overlap REAL NOT NULL DEFAULT 0,
      order_similarity REAL NOT NULL DEFAULT 0,
      shared_count INTEGER NOT NULL DEFAULT 0,
      risk TEXT NOT NULL DEFAULT 'none',
      PRIMARY KEY (project_id, id),
      UNIQUE (project_id, file_a_id, file_b_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_outline_pairwise_score
    ON duplicate_check_outline_pairwise(project_id, score DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_content_duplicates (
      project_id TEXT NOT NULL REFERENCES projects(id),
      duplicate_id TEXT NOT NULL,
      sentence TEXT NOT NULL,
      normalized TEXT NOT NULL,
      file_ids_json TEXT NOT NULL,
      first_order INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (project_id, duplicate_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_content_duplicates_order
    ON duplicate_check_content_duplicates(project_id, first_order)
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_content_occurrences (
      project_id TEXT NOT NULL REFERENCES projects(id),
      id INTEGER NOT NULL,
      duplicate_id TEXT NOT NULL,
      file_id TEXT NOT NULL,
      occurrence_count INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (project_id, id),
      UNIQUE (project_id, duplicate_id, file_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_content_occ_file
    ON duplicate_check_content_occurrences(project_id, file_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_image_files (
      project_id TEXT NOT NULL REFERENCES projects(id),
      file_id TEXT NOT NULL,
      status TEXT NOT NULL,
      image_count INTEGER NOT NULL DEFAULT 0,
      unique_image_count INTEGER NOT NULL DEFAULT 0,
      error TEXT,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (project_id, file_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_duplicate_images (
      project_id TEXT NOT NULL REFERENCES projects(id),
      image_id TEXT NOT NULL,
      hash TEXT NOT NULL,
      preview_url TEXT NOT NULL,
      file_ids_json TEXT NOT NULL,
      sort_order INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (project_id, image_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_duplicate_images_hash
    ON duplicate_check_duplicate_images(project_id, hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_duplicate_images_order
    ON duplicate_check_duplicate_images(project_id, sort_order)
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_image_occurrences (
      project_id TEXT NOT NULL REFERENCES projects(id),
      id INTEGER NOT NULL,
      image_id TEXT NOT NULL,
      file_id TEXT NOT NULL,
      occurrence_count INTEGER NOT NULL DEFAULT 0,
      locations_json TEXT,
      PRIMARY KEY (project_id, id),
      UNIQUE (project_id, image_id, file_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_image_occ_file
    ON duplicate_check_image_occurrences(project_id, file_id)
    """,
```

> **注意 `AUTOINCREMENT` 的处理**：参考实现里 `metadata_items` / `outline_pairwise` / `content_occurrences` / `image_occurrences` 用的是 `INTEGER PRIMARY KEY AUTOINCREMENT`，四张表都靠它做隐式行 id。改成 `(project_id, id)` 联合主键后**不能再声明 `AUTOINCREMENT`**（SQLite 只允许在 `INTEGER PRIMARY KEY` 上用），所以上面写的是 `id INTEGER NOT NULL`，**由仓储层显式分配 id**（Task 2 的 `_next_row_id`）。这是本任务唯一的语义偏差，必须记入报告。
>
> **`ON DELETE CASCADE` 的处理**：参考实现有 `FOREIGN KEY ... ON DELETE CASCADE`。保留它们**会**在删项目时级联删干净，但需要 `PRAGMA foreign_keys=ON`（P1 已开）。本计划**照抄这些外键**到 `file_id` → `duplicate_check_files` 上：

对 `duplicate_check_content_files` / `_metadata_items` / `_outline_items` / `_content_occurrences` / `_image_files` / `_image_occurrences` 各追加：

```sql
      , FOREIGN KEY (project_id, file_id)
        REFERENCES duplicate_check_files(project_id, file_id) ON DELETE CASCADE
```

对 `duplicate_check_outline_items` 再加自引用：

```sql
      , FOREIGN KEY (project_id, parent_item_id)
        REFERENCES duplicate_check_outline_items(project_id, item_id) ON DELETE CASCADE
```

对 `_content_occurrences` 再加：

```sql
      , FOREIGN KEY (project_id, duplicate_id)
        REFERENCES duplicate_check_content_duplicates(project_id, duplicate_id) ON DELETE CASCADE
```

对 `_image_occurrences` 再加：

```sql
      , FOREIGN KEY (project_id, image_id)
        REFERENCES duplicate_check_duplicate_images(project_id, image_id) ON DELETE CASCADE
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd server && .venv/Scripts/python.exe -m pytest tests/test_db_schema.py -q
```

- [ ] **Step 5: 变异探针（必做）**

临时把 `duplicate_check_meta` 的 `PRIMARY KEY (project_id)` 改回 `CHECK (id = 1)` 风格（或直接删掉 `project_id` 列），跑 `test_duplicate_check_meta_is_project_scoped`，**必须变红**。确认后改回。在报告里贴出红/绿两次输出。

- [ ] **Step 6: 提交**

```bash
git add server/app/infra/db/schema.py server/tests/test_db_schema.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): P3a 查重域 13 张表落 schema（project_id 作用域，spec §5.2 裁定 R31）"
```

---

### Task 2: 查重仓储（15 张表的读写）

**Files:**
- Create: `server/app/infra/repositories/duplicate.py`
- Test: `server/tests/test_repositories_duplicate.py`

**Interfaces:**
- Consumes: `Database`（`app.infra.db.engine`）
- Produces:
  - `class DuplicateRepository`
    - `__init__(self, db: Database)`
    - **元信息**：`get_meta(project_id) -> dict | None`、`save_meta(project_id, *, step=None, active_analysis_tab=None, current_signature=_UNSET) -> None`
    - **文件簿记**：`replace_files(project_id, role, files: Sequence[DuplicateFileInput]) -> None`（先删该 role 旧行再插，`sort_order` 按入参顺序）、`list_files(project_id, role=None) -> list[dict]`、`delete_files(project_id) -> None`
    - **四段状态**：`get_sections(project_id) -> dict[str, dict]`、`save_section(project_id, section, *, status, progress, message, signature=_UNSET, stats=_UNSET, started_at=_UNSET, updated_at) -> None`、`delete_sections(project_id) -> None`
    - **结果表写入**（P3a 只提供 API，stub 会传空列表）：`replace_content_files(project_id, rows)`、`replace_metadata_items(project_id, rows)`、`replace_outline_items(project_id, rows)`、`replace_outline_groups(project_id, rows)`、`replace_outline_pairwise(project_id, rows)`、`replace_content_duplicates(project_id, rows, occurrences)`、`replace_image_files(project_id, rows)`、`replace_duplicate_images(project_id, images, occurrences)`
    - **读回**（供结果接口与 P3b–P3d 的对照）：`load_analysis(project_id) -> dict`（组装成参考实现 `store.loadDuplicateCheck()` 的 analysis 形状）
  - `@dataclass(frozen=True) class DuplicateFileInput`：`file_id: str`、`file_name: str`、`file_path: str`、`extension: str`、`size: int = 0`、`modified_at: str | None = None`、`content_hash: str | None = None`
  - 模块级 `_UNSET = object()`（哨兵，实现「未携带则保留」语义）

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_repositories_duplicate.py`。**必须复用 P1 的 conftest 夹具**（若 `conftest.py` 已有 `db` fixture 则直接用；没有则在文件内自建，见下）：

```python
from __future__ import annotations

import pytest

from app.infra.db.engine import Database
from app.infra.db.migrate import apply_schema
from app.infra.repositories.duplicate import DuplicateFileInput, DuplicateRepository


@pytest.fixture()
def repo(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    apply_schema(db)
    db.execute("INSERT INTO projects(id, name, created_at) VALUES ('p1','a','now')")
    return DuplicateRepository(db)


def _file(fid, name, *, role_path=None, size=100, modified="2026-01-01T00:00:00Z"):
    return DuplicateFileInput(
        file_id=fid, file_name=name,
        file_path=role_path or f"/tmp/{name}", extension="docx",
        size=size, modified_at=modified,
    )


def test_replace_files_assigns_sort_order_by_input_order(repo):
    repo.replace_files("p1", "bid", [_file("b", "b.docx"), _file("a", "a.docx")])
    rows = repo.list_files("p1", "bid")
    assert [r["file_id"] for r in rows] == ["b", "a"]
    assert [r["sort_order"] for r in rows] == [0, 1]


def test_replace_files_scoped_by_role(repo):
    repo.replace_files("p1", "tender", [_file("t", "t.docx")])
    repo.replace_files("p1", "bid", [_file("b", "b.docx")])
    assert [r["file_id"] for r in repo.list_files("p1", "tender")] == ["t"]
    assert [r["file_id"] for r in repo.list_files("p1", "bid")] == ["b"]


def test_replace_files_replaces_not_appends(repo):
    repo.replace_files("p1", "bid", [_file("a", "a.docx")])
    repo.replace_files("p1", "bid", [_file("b", "b.docx")])
    assert [r["file_id"] for r in repo.list_files("p1", "bid")] == ["b"]


def test_save_section_preserves_omitted_fields(repo):
    repo.save_section("p1", "metadata", status="running", progress=10,
                      message="跑", signature="sig1", stats={"a": 1},
                      started_at="2026-01-01T00:00:00Z",
                      updated_at="2026-01-01T00:00:01Z")
    # 只更新 progress/message，不带 signature/stats/started_at
    repo.save_section("p1", "metadata", status="running", progress=20,
                      message="跑2", updated_at="2026-01-01T00:00:02Z")
    row = repo.get_sections("p1")["metadata"]
    assert row["progress"] == 20
    assert row["message"] == "跑2"
    assert row["signature"] == "sig1"          # 未被清空
    assert row["stats"] == {"a": 1}            # 未被清空
    assert row["started_at"] == "2026-01-01T00:00:00Z"


def test_save_section_can_null_signature_explicitly(repo):
    repo.save_section("p1", "metadata", status="running", progress=1, message="",
                      signature="sig1", stats={}, started_at=None,
                      updated_at="2026-01-01T00:00:00Z")
    repo.save_section("p1", "metadata", status="pending", progress=0, message="",
                      signature=None, updated_at="2026-01-01T00:00:01Z")
    assert repo.get_sections("p1")["metadata"]["signature"] is None


def test_get_sections_starts_empty(repo):
    assert repo.get_sections("p1") == {}


def test_save_meta_preserves_omitted_fields(repo):
    repo.save_meta("p1", step="analysis", active_analysis_tab="content",
                   current_signature="s1")
    repo.save_meta("p1", step="upload")
    meta = repo.get_meta("p1")
    assert meta["step"] == "upload"
    assert meta["active_analysis_tab"] == "content"
    assert meta["current_signature"] == "s1"


def test_delete_files_cascades_scoped_rows(repo):
    repo.replace_files("p1", "bid", [_file("b", "b.docx")])
    repo.replace_content_files("p1", [
        {"file_id": "b", "status": "success", "content_path": "/x/b.md",
         "content_length": 5, "parser_label": "local", "error": None,
         "updated_at": "now"}])
    assert repo.load_analysis("p1")["contentFiles"]
    repo.delete_files("p1")
    assert repo.list_files("p1") == []
    assert repo.load_analysis("p1")["contentFiles"] == []
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd server && .venv/Scripts/python.exe -m pytest tests/test_repositories_duplicate.py -q
```

预期：`ModuleNotFoundError: app.infra.repositories.duplicate`。

- [ ] **Step 3: 写实现**

创建 `server/app/infra/repositories/duplicate.py`。要点：

- **`_UNSET` 哨兵**实现「未携带则保留」：写 SQL 前先 `SELECT` 现有行，把未携带的字段填回旧值，再整体 `INSERT OR REPLACE`。**不要**用 `COALESCE` 拼 SQL（无法区分「未携带」与「显式 NULL」）。
- **`stats_json` 的编解码**：`json.dumps(..., ensure_ascii=False, sort_keys=True)` / `json.loads(... or "{}")`。序列化必须**稳定**（同一 dict 两次 dump 出同一字符串），否则幂等比对会误判。
- **自增 id**：`_next_row_id(project_id, table) -> int`，实现为 `SELECT COALESCE(MAX(id), 0) + 1 FROM <table> WHERE project_id = ?`，在事务内调用以保证不重号。四张需要 id 的表都用它。
- **`replace_*` 一律「先 DELETE 本项目该表旧行再插」**，在 `db.transaction()` 内完成，保证重跑幂等。
- **`load_analysis`** 返回的形状必须与参考实现 `store.loadDuplicateCheck()` 一致（键名逐字）：

```python
{
  "step": ..., "activeAnalysisTab": ...,
  "metadataAnalysis": {"status":..., "progress":..., "message":..., "signature":...,
                       "stats": {...}, "files": [...], "rows": [...]},
  "outlineAnalysis":  {"status":..., "progress":..., "message":..., "signature":...,
                       "stats": {...}, "files": [...], "items": [...],
                       "groups": [...], "pairwise": [...]},
  "contentAnalysis":  {"status":..., "progress":..., "message":..., "signature":...,
                       "stats": {...}, "files": [...], "duplicates": [...],
                       "occurrences": [...]},
  "imageAnalysis":    {"status":..., "progress":..., "message":..., "signature":...,
                       "stats": {...}, "files": [...], "duplicateImages": [...]},
  "files": [...], "contentFiles": [...],
}
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd server && .venv/Scripts/python.exe -m pytest tests/test_repositories_duplicate.py -q
```

- [ ] **Step 5: 变异探针（必做）**

把 `save_section` 里「未携带则保留」的合并逻辑删掉（改成无条件覆盖），跑 `test_save_section_preserves_omitted_fields`，**必须变红**。贴出红/绿两次输出。

同时给 `_next_row_id` 的变异做一次：改成常量 `1`，跑 `test_replace_content_files` 类用例（若当前用例没覆盖两条并发写就**先加一条**：同一项目连续两次 `replace_content_files` 且都带 2 行，断言两次都成功且 id 不冲突）。

- [ ] **Step 6: 提交**

```bash
git add server/app/infra/repositories/duplicate.py server/tests/test_repositories_duplicate.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): P3a 查重仓储——15 张表读写 + 未携带保留语义 + load_analysis 形状对齐"
```

---

### Task 3: 签名、稳定 file_id、进度与状态归一化（纯 domain）

**Files:**
- Create: `server/app/domain/duplicate/__init__.py`、`signature.py`、`progress.py`、`state.py`
- Test: `server/tests/test_duplicate_signature.py`、`test_duplicate_progress.py`、`test_duplicate_state.py`

**Interfaces:**
- Consumes: 无（纯函数，零依赖）
- Produces: 见上文「已定接口」小节的全部签名。

- [ ] **Step 1: 写失败的测试**

`server/tests/test_duplicate_signature.py`：

```python
from __future__ import annotations

from app.domain.duplicate.signature import compute_analysis_signature, stable_file_id


class _File:
    """用最小替身而非 Mock，避免测试绑定到 Mock 内部。"""
    def __init__(self, path, size, modified, fid=None, name=""):
        self.file_path = path
        self.size = size
        self.modified_at = modified
        self.id = fid
        self.file_name = name


def test_signature_matches_reference_format():
    """逐字对齐 :126-131：'path|size|modified' 按输入顺序、\\n 连接、sha1。"""
    import hashlib
    files = [_File("/a.docx", 10, "t1"), _File("/b.docx", 20, "t2")]
    expected = hashlib.sha1("/a.docx|10|t1\n/b.docx|20|t2".encode()).hexdigest()
    assert compute_analysis_signature(files) == expected


def test_signature_is_order_sensitive():
    a = _File("/a.docx", 10, "t1")
    b = _File("/b.docx", 20, "t2")
    assert compute_analysis_signature([a, b]) != compute_analysis_signature([b, a])


def test_signature_changes_when_size_changes():
    a = _File("/a.docx", 10, "t1")
    b = _File("/a.docx", 11, "t1")
    assert compute_analysis_signature([a]) != compute_analysis_signature([b])


def test_signature_changes_when_modified_changes():
    assert (compute_analysis_signature([_File("/a.docx", 10, "t1")])
            != compute_analysis_signature([_File("/a.docx", 10, "t2")]))


def test_signature_of_empty_is_sha1_of_empty_string():
    import hashlib
    assert compute_analysis_signature([]) == hashlib.sha1(b"").hexdigest()


def test_signature_skips_falsy_entries():
    """.filter(Boolean)：None 与空值被跳过。"""
    import hashlib
    files = [None, _File("/a.docx", 10, "t1")]
    expected = hashlib.sha1("/a.docx|10|t1".encode()).hexdigest()
    assert compute_analysis_signature(files) == expected


def test_stable_file_id_prefers_explicit_id():
    import hashlib
    assert stable_file_id("/a.docx", "a.docx", explicit_id="fixed") == "fixed"


def test_stable_file_id_falls_back_to_path_hash():
    import hashlib
    expected = hashlib.sha1("/a.docx".encode()).hexdigest()
    assert stable_file_id("/a.docx", "a.docx") == expected


def test_stable_file_id_uses_name_when_path_empty():
    import hashlib
    expected = hashlib.sha1("a.docx".encode()).hexdigest()
    assert stable_file_id("", "a.docx") == expected
```

> **注意**：`test_stable_file_id_falls_back_to_path_hash` 的 `import hashlib` 写在函数体内只是为了让每例独立；实现里同一文件不要重复 import。

`server/tests/test_duplicate_progress.py`：

```python
from __future__ import annotations

import pytest

from app.domain.duplicate.progress import (
    analysis_progress, latest_analysis_message, overall_progress,
)
from app.domain.duplicate.state import SectionState


def _s(status="running", progress=0, message=""):
    return SectionState(status=status, progress=progress, message=message)


def test_analysis_progress_none_is_zero():
    assert analysis_progress(None) == 0


def test_analysis_progress_success_is_100_regardless_of_progress_field():
    assert analysis_progress(_s("success", progress=3)) == 100


def test_analysis_progress_error_is_100_regardless_of_progress_field():
    assert analysis_progress(_s("error", progress=3)) == 100


def test_analysis_progress_running_clamps_to_99():
    assert analysis_progress(_s("running", progress=100)) == 99


def test_analysis_progress_negative_clamps_to_zero():
    assert analysis_progress(_s("running", progress=-5)) == 0


def test_analysis_progress_pending_keeps_progress():
    assert analysis_progress(_s("pending", progress=7)) == 7


@pytest.mark.parametrize("value,expected", [
    (0, 0), (25, 25), (50, 50), (75, 75), (100, 100),
])
def test_overall_progress_equal_weight_four_sections(value, expected):
    states = {k: _s("running", progress=value)
              for k in ("metadata", "outline", "content", "image")}
    assert overall_progress(states) == expected


def test_overall_progress_averages_and_rounds_half_up():
    """三个 0 一个 2 → 2/4 = 0.5 → Math.round 上取为 1（不是 Python 的 round→0）。"""
    states = {
        "metadata": _s("running", progress=2),
        "outline": _s("pending", progress=0),
        "content": _s("pending", progress=0),
        "image": _s("pending", progress=0),
    }
    assert overall_progress(states) == 1


def test_overall_progress_treats_missing_section_as_zero():
    assert overall_progress({"metadata": _s("success")}) == 25


def test_overall_progress_all_success_is_100():
    states = {k: _s("success") for k in ("metadata", "outline", "content", "image")}
    assert overall_progress(states) == 100


def test_latest_message_priority_image_first():
    states = {
        "metadata": _s(message="m"), "outline": _s(message="o"),
        "content": _s(message="c"), "image": _s(message="i"),
    }
    assert latest_analysis_message(states) == "i"


def test_latest_message_falls_back_in_order():
    states = {"metadata": _s(message="m"), "outline": _s(message="o"),
              "content": _s(message="c")}
    assert latest_analysis_message(states) == "c"


def test_latest_message_default():
    assert latest_analysis_message({}) == "标书查重分析运行中。"
```

> **`test_overall_progress_averages_and_rounds_half_up` 是逐字对齐的核心用例。** JS `Math.round(0.5) === 1`，Python `round(0.5) === 0`。实现**必须**用 `math.floor(x + 0.5)`（与 P2b 的 `half_up` 同一约定）。这个用例如果写成 0 就是错的。

`server/tests/test_duplicate_state.py`：

```python
from __future__ import annotations

from app.domain.duplicate.state import (
    ANALYSIS_SECTIONS, build_section_stats_patch, normalize_section_status,
)


def test_analysis_sections_order_is_fixed():
    assert ANALYSIS_SECTIONS == ("metadata", "outline", "content", "image")


def test_normalize_section_status_accepts_known_values():
    for value in ("pending", "running", "success", "error"):
        assert normalize_section_status(value) == value


def test_normalize_section_status_falls_back_on_unknown():
    assert normalize_section_status("weird") == "pending"
    assert normalize_section_status(None) == "pending"
    assert normalize_section_status(123) == "pending"


def test_stats_patch_metadata_whitelist():
    analysis = {"contentExtraction": {"a": 1}, "metadataExtraction": {"b": 2},
                "logs": ["x"], "files": [{"file_id": "f", "file_name": "n",
                                          "status": "success", "error": None,
                                          "extra": "dropped"}],
                "unknownKey": "dropped"}
    patch = build_section_stats_patch("metadata", analysis)
    assert set(patch) == {"contentExtraction", "metadataExtraction", "logs", "files"}
    assert patch["files"] == [{"file_id": "f", "file_name": "n",
                               "status": "success", "error": None}]


def test_stats_patch_omits_absent_keys():
    """"hasOwn" 语义：键不存在则不产出（区别于存在但为 None）。"""
    patch = build_section_stats_patch("metadata", {})
    assert patch == {}


def test_stats_patch_includes_key_present_with_none_value():
    patch = build_section_stats_patch("metadata", {"logs": None})
    assert patch == {"logs": None}


def test_stats_patch_outline_whitelist():
    analysis = {"tenderSentenceCount": 3, "tenderMatchedItemCount": 1,
                "extraction": {"e": 1}, "nope": 1,
                "files": [{"file_id": "f", "file_name": "n", "status": "success",
                           "source": "heading", "confidence": 0.8,
                           "item_count": 2, "tender_matched_count": 1,
                           "error": None, "extra": "dropped"}]}
    patch = build_section_stats_patch("outline", analysis)
    assert set(patch) == {"tenderSentenceCount", "tenderMatchedItemCount",
                          "extraction", "files"}
    assert patch["files"][0] == {"file_id": "f", "file_name": "n",
                                 "status": "success", "source": "heading",
                                 "confidence": 0.8, "item_count": 2,
                                 "tender_matched_count": 1, "error": None}


def test_stats_patch_content_whitelist():
    analysis = {"tenderSentenceCount": 1, "tenderMatchedSentenceCount": 2,
                "totalSentenceCount": 3, "extraction": {"e": 1}, "nope": 1}
    patch = build_section_stats_patch("content", analysis)
    assert set(patch) == {"tenderSentenceCount", "tenderMatchedSentenceCount",
                          "totalSentenceCount", "extraction"}


def test_stats_patch_image_whitelist():
    analysis = {"extraction": {"e": 1}, "totalImageCount": 5, "nope": 1}
    patch = build_section_stats_patch("image", analysis)
    assert set(patch) == {"extraction", "totalImageCount"}


def test_stats_patch_unknown_section_is_empty():
    assert build_section_stats_patch("nope", {"a": 1}) == {}


def test_stats_patch_empty_files_list_stays_empty_list():
    assert build_section_stats_patch("metadata", {"files": []}) == {"files": []}
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd server && .venv/Scripts/python.exe -m pytest tests/test_duplicate_signature.py tests/test_duplicate_progress.py tests/test_duplicate_state.py -q
```

- [ ] **Step 3: 写实现**

`signature.py`：注意 sha1 输入是**字节**（`"...".join(...).encode("utf-8")`）。`modified_at` 为 `None` 时参考实现会插字面 `"null"`（JS 模板字符串）——**逐字对齐**：`f"{path}|{size}|{modified}"` 里 `modified` 为 `None` 时要输出 `"null"`，不是空串。加一条用例钉住：

```python
def test_signature_renders_none_modified_as_js_null():
    import hashlib
    expected = hashlib.sha1("/a.docx|10|null".encode()).hexdigest()
    assert compute_analysis_signature([_File("/a.docx", 10, None)]) == expected
```

`progress.py`：`overall_progress` 用 `statistics`？不用，直接：

```python
def overall_progress(states):
    values = [analysis_progress(states.get(section)) for section in ANALYSIS_SECTIONS]
    return math.floor(sum(values) / len(values) + 0.5)
```

`state.py`：`SectionState` 用 `frozen=True` + `field(default_factory=dict)`；`build_section_stats_patch` 用四个显式分支 + `_pick(analysis, keys)` 助手（`if key in analysis`，不是 `analysis.get(key) is not None`）。

- [ ] **Step 4: 跑测试确认通过**

```bash
cd server && .venv/Scripts/python.exe -m pytest tests/test_duplicate_signature.py tests/test_duplicate_progress.py tests/test_duplicate_state.py -q
```

- [ ] **Step 5: 变异探针（必做，三处）**

1. 把 `overall_progress` 的 `math.floor(x + 0.5)` 改成 `round(x)` → `test_overall_progress_averages_and_rounds_half_up` **必须变红**。
2. 把 `analysis_progress` 里 `status in ("success", "error") → 100` 改成只认 `success` → `test_analysis_progress_error_is_100_...` **必须变红**。
3. 把 `build_section_stats_patch` 的 `if key in analysis` 改成 `if analysis.get(key)` → `test_stats_patch_includes_key_present_with_none_value` **必须变红**。

三处都贴出红/绿输出。

- [ ] **Step 6: 提交**

```bash
git add server/app/domain/duplicate/ server/tests/test_duplicate_signature.py server/tests/test_duplicate_progress.py server/tests/test_duplicate_state.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): P3a 查重纯 domain——签名/稳定 file_id/四段进度/状态补丁（逐字对齐参考实现）"
```

---

### Task 4: 四段编排骨架（stub 驱动）

**Files:**
- Create: `server/app/infra/tasks/duplicate_runner.py`
- Test: `server/tests/test_duplicate_runner.py`

**Interfaces:**
- Consumes: `DuplicateRepository`、`TaskContext`、Task 3 的 domain 函数

  **`TaskContext` 的真实接口（已核对 `server/app/infra/tasks/context.py`，不要照抄别的形状）：**
  - `ctx.project_id` / `ctx.task_id` / `ctx.domain` / `ctx.kind` 是**属性**
  - `ctx.progress(percent: int, label: str | None = None) -> None` —— **两个位置参数**，第二个可省
  - `ctx.log(message: str) -> None` —— **只收一个参数**，没有格式化参数
  - `ctx.checkpoint(state: dict) -> None` —— 落 `checkpoint_json`，就是下次的 `previous_state`
  - `ctx.raise_if_cancelled() -> None`

- Produces:
  - `SectionRunner = Callable[[SectionRunContext], Awaitable[SectionOutput]]`
  - `@dataclass(frozen=True) class SectionOutput`：`stats: dict`、`rows: dict`（按表名分组的结果行，P3a 用空 dict）
  - `class DuplicateAnalysisRunner`
    - `__init__(self, *, repo: DuplicateRepository, sections: Mapping[str, SectionRunner] | None = None, now_fn=_utc_now, signature_reader: Callable[[str], str | None] | None = None)`
      - `signature_reader(project_id) -> str | None` 默认读 `repo.get_meta(project_id)["current_signature"]`。**存在的唯一理由是让「过期签名丢弃」可测**——测试注入一个受控的 reader，不需要 monkeypatch 内部方法。
    - `async def __call__(self, ctx, payload, previous_state) -> None`（即 `Runner` 协议）
    - 默认 `sections` 为 `_stub_sections()`（四段占位实现）
  - `def _stub_sections() -> dict[str, SectionRunner]` —— 四段各推进 0→50→100 并写空 stats

**核心行为（逐条对照 spec §9.2 编排）：**

1. 读取 `previous_state`（即上次任务检查点里的 `{"sections": {...}, "signature": ...}`）。
2. **幂等**：若 `payload.force` 非真 且 四段 `status` 全为 `success` 且 四段 `signature` 全等于本次签名 → 直接 `overall=100`、`ctx.log("标书查重分析已完成，无需重复分析。")` 并返回。**不重跑**。
3. 若签名不同 → 清空四段状态与全部结果表（`repo.delete_sections` + 各 `replace_*` 传空），`ctx.log` 记录「签名变化，已作废旧结果」。
4. 写 `repo.save_meta(step="analysis", current_signature=signature)`。
5. **content 段先起、不等待**；`metadata` 段 `await` 完成 —— 两段**并行**（用 `asyncio.create_task` + `asyncio.gather`）。
6. metadata 完成后，把 outline/content/image 三段置 `running`、`progress=1`、消息 `"元数据提取完成，等待正文内容用于目录分析"` / `…正文比对` / `…图片比对`。
7. `await` content 段；算 `metadata_failed = content 或 metadata 有 error`。
8. `metadata` 段总体置终态（`status = "error" if metadata_failed else "success"`、`progress=100`、消息 `"部分文件提取失败"` / `"元数据分析完成"`）。
9. `asyncio.gather` 跑 outline/content/image 三段（**并行**）。
10. `failed = metadata_failed or outline 有 error or content.status == "error" or image.status == "error"`。
11. **签名校验**：跑完后重读 `repo.get_meta(project_id)["current_signature"]`，若 ≠ 本次签名（用户已改动文件）→ **丢弃结果不落库**，`ctx.log` 记录并返回。
12. **每次进度更新**：`ctx.progress(overall_progress(states), latest_analysis_message(states))`。

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_duplicate_runner.py`：

```python
from __future__ import annotations

import asyncio

import pytest

from app.infra.db.engine import Database
from app.infra.db.migrate import apply_schema
from app.infra.repositories.duplicate import DuplicateFileInput, DuplicateRepository
from app.infra.tasks.duplicate_runner import DuplicateAnalysisRunner


class FakeCtx:
    """TaskContext 的最小替身：只记，不做 I/O。"""
    def __init__(self):
        self.progress_calls: list[tuple[int, str]] = []
        self.checkpoints: list[dict] = []
        self.logs: list[str] = []

    def progress(self, value, message=""):
        self.progress_calls.append((value, message))

    def checkpoint(self, state):
        self.checkpoints.append(state)

    def log(self, message, *args, **kwargs):
        self.logs.append(message)


@pytest.fixture()
def repo(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    apply_schema(db)
    db.execute("INSERT INTO projects(id, name, created_at) VALUES ('p1','a','now')")
    r = DuplicateRepository(db)
    r.replace_files("p1", "tender", [DuplicateFileInput(
        file_id="t", file_name="t.docx", file_path="/t.docx", extension="docx",
        size=1, modified_at="m1")])
    r.replace_files("p1", "bid", [DuplicateFileInput(
        file_id="b", file_name="b.docx", file_path="/b.docx", extension="docx",
        size=2, modified_at="m2")])
    return r


def _payload(force=False):
    return {
        "force": force,
        "tenderFiles": [{"file_id": "t", "file_path": "/t.docx", "size": 1,
                         "modified_at": "m1"}],
        "bidFiles": [{"file_id": "b", "file_path": "/b.docx", "size": 2,
                      "modified_at": "m2"}],
    }


def _run(repo, payload, previous_state=None):
    ctx = FakeCtx()
    runner = DuplicateAnalysisRunner(repo=repo)
    asyncio.run(runner(ctx, payload, previous_state))
    return ctx


def test_stub_run_marks_all_sections_success(repo):
    ctx = _run(repo, _payload())
    sections = repo.get_sections("p1")
    assert set(sections) == {"metadata", "outline", "content", "image"}
    assert all(s["status"] == "success" for s in sections.values())


def test_stub_run_records_signature_on_every_section(repo):
    ctx = _run(repo, _payload())
    sigs = {s["signature"] for s in repo.get_sections("p1").values()}
    assert len(sigs) == 1 and None not in sigs


def test_progress_reaches_100(repo):
    ctx = _run(repo, _payload())
    assert ctx.progress_calls[-1][0] == 100


def test_progress_is_monotonic(repo):
    ctx = _run(repo, _payload())
    values = [v for v, _ in ctx.progress_calls]
    assert values == sorted(values), f"进度回退：{values}"


def test_second_run_same_signature_is_idempotent(repo):
    """四段全 success 且签名相同 → 不重跑，直接返回并落日志。"""
    ctx1 = _run(repo, _payload())
    first_calls = len(ctx1.progress_calls)
    ctx2 = _run(repo, _payload())
    assert any("无需重复分析" in m for m in ctx2.logs)
    assert len(ctx2.progress_calls) < first_calls, "幂等命中仍在重跑"
    assert ctx2.progress_calls[-1][0] == 100


def test_force_bypasses_idempotency(repo):
    _run(repo, _payload())
    ctx = _run(repo, _payload(force=True))
    assert not any("无需重复分析" in m for m in ctx.logs)


def test_signature_change_invalidates_previous_state(repo):
    _run(repo, _payload())
    changed = _payload()
    changed["bidFiles"][0]["size"] = 999
    ctx = _run(repo, changed)
    assert any("作废" in m or "签名" in m for m in ctx.logs)
    assert repo.get_sections("p1")["metadata"]["status"] == "success"


def test_metadata_runs_before_outline_content_image(repo):
    """编排顺序：metadata 先于后三段启动（靠进度消息钉住）。"""
    ctx = _run(repo, _payload())
    messages = [m for _, m in ctx.progress_calls]
    ready = [i for i, m in enumerate(messages) if "等待正文内容" in m]
    assert ready, "未见「等待正文内容」阶段消息"


def test_meta_current_signature_is_saved(repo):
    _run(repo, _payload())
    meta = repo.get_meta("p1")
    assert meta["step"] == "analysis"
    assert meta["current_signature"] is not None


def test_stale_signature_discards_results(repo):
    """跑完后重读 current_signature 若 ≠ 本次签名 → 丢弃结果、不写四段终态。

    用注入的 signature_reader 造确定性的「跑完那一刻签名已变」，
    不 monkeypatch 内部方法。
    """
    reads = {"n": 0}

    def stale_reader(project_id: str) -> str | None:
        reads["n"] += 1
        # 第 1 次是开跑前的读（用于判断幂等/重置）；其后一律返回「别人改过的签名」
        if reads["n"] == 1:
            return None
        return "someone-else"

    ctx = FakeCtx()
    runner = DuplicateAnalysisRunner(repo=repo, signature_reader=stale_reader)
    asyncio.run(runner(ctx, _payload(), None))

    assert any("丢弃" in m for m in ctx.logs), f"未检测到过期签名丢弃，logs={ctx.logs}"
    # 结果必须没落库：四段仍是重置后的初始态，而非 success
    sections = repo.get_sections("p1")
    assert all(s["status"] != "success" for s in sections.values()), \
        f"过期签名仍写了终态：{sections}"
```

> 实现里 `_is_up_to_date` 与「跑完后的签名校验」都走 `self._signature_reader(project_id)`，**不直接调 `repo.get_meta`**。这是让这个测试成为真实归因测试的关键。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd server && .venv/Scripts/python.exe -m pytest tests/test_duplicate_runner.py -q
```

- [ ] **Step 3: 写实现**

创建 `server/app/infra/tasks/duplicate_runner.py`。结构：

```python
class DuplicateAnalysisRunner:
    def __init__(self, *, repo, sections=None, now_fn=_utc_now,
                 signature_reader=None):
        self._repo = repo
        self._sections = sections or _stub_sections()
        self._now = now_fn
        self._signature_reader = signature_reader or self._read_signature

    async def __call__(self, ctx, payload, previous_state):
        files = _payload_files(payload)              # → list[FileRef]
        signature = compute_analysis_signature(files)
        project_id = ctx.project_id                  # ← TaskContext 属性，不从 payload 取

        if self._is_up_to_date(previous_state, signature, payload):
            ctx.log("标书查重分析已完成，无需重复分析。")
            ctx.progress(100)
            return

        await self._reset(project_id, signature, previous_state, ctx)
        # ... 并行编排（见 Interfaces 里的 1–12 步）
```

`_reset` 的判据：`previous_state` 里的签名 ≠ 本次签名，或 `payload.force`，或四段不全 success。
「跑完后的签名校验」在写终态**之前**调 `self._signature_reader(project_id)` 重新取值。

- [ ] **Step 4: 跑测试确认通过**

```bash
cd server && .venv/Scripts/python.exe -m pytest tests/test_duplicate_runner.py -q
```

- [ ] **Step 5: 变异探针（必做，三处）**

1. 去掉幂等分支（无条件重跑）→ `test_second_run_same_signature_is_idempotent` **必须变红**。
2. 把 `metadata` 与 `content` 的并行改成串行（先 await metadata 再起 content）→ 「等待正文内容」消息时序用例 **必须变红**。若杀不掉，就**删掉该用例并在报告里说明它不敏感**（不要留一条杀不掉的断言）。
3. 去掉签名校验 → `test_stale_signature_discards_results` **必须变红**。

- [ ] **Step 6: 提交**

```bash
git add server/app/infra/tasks/duplicate_runner.py server/tests/test_duplicate_runner.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): P3a 四段编排骨架——并行时序/幂等/签名作废（stub 驱动，算法留 P3b-d）"
```

---

### Task 5: 文件簿记 API（上传 = save-files 语义）

**Files:**
- Create: `server/app/api/duplicate.py`
- Modify: `server/app/main.py`（注册路由）、`server/app/api/deps.py`（`AppState` 加 `duplicate` 字段）
- Test: `server/tests/test_api_duplicate_files.py`

**`deps.py` 的必要改动（已核实 `build_state` 的形状）：**

```python
@dataclass
class AppState:
    ...
    duplicate: DuplicateRepository      # ← 新增
    engine: TaskEngine
    ...

def build_state(settings):
    ...
        logs = TaskLogRepository(db)
        duplicate = DuplicateRepository(db)     # ← 新增
        bus = EventBus()
        engine = TaskEngine(tasks, logs, settings, publisher=bus)
    ...
    return AppState(..., duplicate=duplicate, ...)
```

`DuplicateRepository` 不持有需释放的资源（只是 `Database` 的包装），故 **`aclose()` 不用改**。路由通过 `state.duplicate` 取用。

**Interfaces:**
- Consumes: `DuplicateRepository`、`app.infra.parsing.service.save_uploaded_document`（P2a）
- Produces（REST，前缀 `/projects/{project_id}/duplicate`）：
  - `POST /files` —— multipart，字段 `role` + `files[]`。**逐字对齐** `duplicate-check:save-files` 语义：先解析落盘（复用 P2a），再 `replace_files(project_id, role, ...)`。返回 `{"files": [...]}`。
  - `GET /files` —— 返回 `{"tender": [...], "bid": [...]}`。
  - `DELETE /files` —— 清空该项目的文件与全部结果表。
  - `GET /state` —— 返回 `load_analysis` 的完整形状 + `overallProgress` + `message`。
  - `POST /analysis` —— body `{"force": bool}`，提交任务，返回 `{"taskId": ...}`。
  - `GET /export` —— **P3a 返回 501**（导出属 P3d）。

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_api_duplicate_files.py`。**沿用 P1 `tests/test_api_tasks.py:14-19` 的既有夹具写法**（不要另起一套）：

```python
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, data_root=tmp_path)
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def project(client):
    return client.post("/projects", json={"name": "查重项目"}).json()["id"]


@pytest.fixture
def docx_bytes(tmp_path):
    """真实可解析的 docx（复用 P2a 夹具，不要手搓字节）。"""
    from tests.parsing_fixtures import build_docx

    path = build_docx(tmp_path / "doc.docx", blocks=[("p", "投标文件正文内容")])
    return path.read_bytes()


DOCX_MIME = ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document")


def _upload(client, project, role, name, data):
    return client.post(f"/projects/{project}/duplicate/files",
                       data={"role": role},
                       files=[("files", (name, data, DOCX_MIME))])


def test_upload_records_role_and_metadata(client, project, docx_bytes):
    resp = _upload(client, project, "tender", "招标文件.docx", docx_bytes)
    assert resp.status_code == 200, resp.text
    item = resp.json()["files"][0]
    assert item["role"] == "tender"
    assert item["file_name"] == "招标文件.docx"
    assert item["size"] == len(docx_bytes)


def test_upload_rejects_unknown_role(client, project, docx_bytes):
    assert _upload(client, project, "nope", "a.docx", docx_bytes).status_code == 400


def test_upload_replaces_previous_files_of_same_role(client, project, docx_bytes):
    _upload(client, project, "bid", "a.docx", docx_bytes)
    _upload(client, project, "bid", "b.docx", docx_bytes)
    listing = client.get(f"/projects/{project}/duplicate/files").json()
    assert [f["file_name"] for f in listing["bid"]] == ["b.docx"]
    assert listing["tender"] == []


def test_state_returns_four_sections_and_overall(client, project):
    body = client.get(f"/projects/{project}/duplicate/state").json()
    assert body["overallProgress"] == 0
    assert set(body["sections"]) == {"metadata", "outline", "content", "image"}
    assert body["message"] == "标书查重分析运行中。"


def test_analysis_requires_files(client, project):
    resp = client.post(f"/projects/{project}/duplicate/analysis",
                       json={"force": False})
    assert resp.status_code == 400


def test_export_is_not_implemented_in_p3a(client, project):
    assert client.get(f"/projects/{project}/duplicate/export").status_code == 501
```

> **路由挂载方式（已核实）**：`app/main.py` 用 `app.include_router(x.router)` **无全局前缀**，故路径就是 `/projects/{project_id}/duplicate/...`。测试里的路径即实际路径，不要再加 `/api`。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd server && .venv/Scripts/python.exe -m pytest tests/test_api_duplicate_files.py -q
```

- [ ] **Step 3: 写实现**

要点：
- `POST /files` 的落盘走 P2a 的 `save_uploaded_document`（**不要**重写解析）。**已核实签名**：

  ```python
  # app/infra/parsing/service.py:35
  def save_uploaded_document(*, data_root: Path, project_id: str,
                             source_path: Path, role: str,
                             source: str = "upload", provider: str = "local") -> SavedDocument
  ```

  典型调用：把上传字节写到临时文件 → `save_uploaded_document(data_root=state.settings.data_root, project_id=project_id, source_path=tmp_path, role=role)` → 取 `SavedDocument` 的 `document_id` / `markdown_path` / `content_hash` / `content_chars` 组装 `DuplicateFileInput` 与 `content_files` 行。**`provider="local"`**（P2c 才接 MinerU）。
- 解析失败的单个文件：记 `status="error"` 一行并继续（**不**整个请求失败）——这与参考实现 `duplicateCheckService` 的逐文件 try 一致。
- `role` 白名单 `{"tender", "bid"}`，非法 → 400。
- `POST /analysis` 需要至少 1 个招标或投标文件，否则 400。任务 `domain="duplicate"`、`kind="duplicate-analysis"`，`runner=DuplicateAnalysisRunner(repo=state.duplicate)`。
- `GET /state` 的 `sections` 键是四段名，值是 `{status, progress, message, signature, stats}`；`overallProgress` / `message` 用 Task 3 的 domain 函数现算。
- 路由全部放 `app/api/duplicate.py`，**domain 层不 import fastapi**（护栏测试会跑）。

- [ ] **Step 4: 跑测试确认通过**

```bash
cd server && .venv/Scripts/python.exe -m pytest tests/test_api_duplicate_files.py -q
```

- [ ] **Step 5: 变异探针（必做，两处）**

1. 把 `replace_files` 改成追加而非替换 → `test_upload_replaces_previous_files_of_same_role` **必须变红**。
2. 把 `role` 白名单校验删掉 → `test_upload_rejects_unknown_role` **必须变红**。

- [ ] **Step 6: 提交**

```bash
git add server/app/api/duplicate.py server/app/main.py server/tests/test_api_duplicate_files.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): P3a 查重文件簿记 API——上传/列表/清空/状态/触发分析"
```

---

### Task 6: 端到端闭环验收

**Files:**
- Modify: `server/app/main.py`（**仅当**需要把 `duplicate` domain 加进既有白名单时）
- Test: `server/tests/test_duplicate_e2e.py`

**Interfaces:**
- **不新建 SSE 端点。** P1 已有全局 `GET /tasks/{task_id}/events`（`app/api/tasks.py:25`）与 `GET /tasks/{task_id}`，查重的进度与日志**直接走它们**。先读 `app/api/tasks.py` 与 `app/infra/tasks/events.py` 确认 domain 是否需要登记；若需要，加进既有白名单即可，**不要**写第二套事件通道。

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_duplicate_e2e.py`。**这是本计划唯一的端到端验收**，必须真实跑通任务引擎：

```python
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

DOCX_MIME = ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document")


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, data_root=tmp_path)
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def _wait_for_task(client, task_id, *, timeout=30.0):
    """轮询任务终态。P1 的 TestClient 是同步的，任务在同一事件循环里跑完。"""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = client.get(f"/tasks/{task_id}").json()
        if last["status"] in {"success", "error", "cancelled", "interrupted"}:
            return last
        time.sleep(0.02)
    raise AssertionError(f"任务未在 {timeout}s 内结束，最后状态：{last}")


def _setup_project(client, tmp_path, *, need_tender=True, need_bid=True):
    from tests.parsing_fixtures import build_docx

    project = client.post("/projects", json={"name": "查重项目"}).json()["id"]
    base = f"/projects/{project}/duplicate"
    payload = build_docx(tmp_path / "招标.docx", blocks=[("p", "招标要求正文")])
    if need_tender:
        client.post(f"{base}/files", data={"role": "tender"},
                    files=[("files", ("招标.docx", payload.read_bytes(), DOCX_MIME))])
    if need_bid:
        bid = build_docx(tmp_path / "投标A.docx", blocks=[("p", "投标应答正文")])
        client.post(f"{base}/files", data={"role": "bid"},
                    files=[("files", ("投标A.docx", bid.read_bytes(), DOCX_MIME))])
    return project, base


def test_upload_then_analyze_reaches_success_with_four_sections(client, tmp_path):
    """完整闭环：上传招标+投标 → 触发分析 → 四段 success → state 100。"""
    project, base = _setup_project(client, tmp_path)
    task_id = client.post(f"{base}/analysis", json={"force": False}).json()["taskId"]
    task = _wait_for_task(client, task_id)
    assert task["status"] == "success", task

    state = client.get(f"{base}/state").json()
    assert state["overallProgress"] == 100
    assert {s["status"] for s in state["sections"].values()} == {"success"}


def test_second_analysis_is_idempotent_via_api(client, tmp_path):
    project, base = _setup_project(client, tmp_path)
    first = client.post(f"{base}/analysis", json={"force": False}).json()["taskId"]
    _wait_for_task(client, first)
    second = client.post(f"{base}/analysis", json={"force": False}).json()["taskId"]
    task = _wait_for_task(client, second)
    assert task["status"] == "success"
    logs = client.get(f"/projects/{project}/tasks").json()
    assert any("无需重复分析" in entry.get("message", "") for entry in logs) \
        or _task_logs_contain(client, second, "无需重复分析")


def test_changing_files_invalidates_and_reruns(client, tmp_path):
    project, base = _setup_project(client, tmp_path, need_tender=False)
    t1 = client.post(f"{base}/analysis", json={"force": False}).json()["taskId"]
    _wait_for_task(client, t1)

    from tests.parsing_fixtures import build_docx
    new_bid = build_docx(tmp_path / "投标B.docx", blocks=[("p", "换了一份投标文件")])
    client.post(f"{base}/files", data={"role": "bid"},
                files=[("files", ("投标B.docx", new_bid.read_bytes(), DOCX_MIME))])

    t2 = client.post(f"{base}/analysis", json={"force": False}).json()["taskId"]
    task = _wait_for_task(client, t2)
    assert task["status"] == "success", task
    assert client.get(f"{base}/state").json()["overallProgress"] == 100


def _task_logs_contain(client, task_id, needle):
    """任务日志的读取端点先以 app/api/tasks.py 的既有实现为准；
    若无日志端点，就用 GET /tasks/{id} 的 stats/error 或直接读 task_logs 表。"""
    resp = client.get(f"/tasks/{task_id}/logs")
    if resp.status_code == 404:
        return False
    return any(needle in entry.get("message", "") for entry in resp.json().get("logs", []))
```

> **`_task_logs_contain` 的兜底**：先读 `app/api/tasks.py` 确认有没有日志端点。若没有，**不要新建**——改为让 `test_second_analysis_is_idempotent_via_api` 断言「第二次任务的 `GET /tasks/{id}` 的 `stageLabel`/`progress` 直接是 100 且没有中间进度」，这同样能证明幂等命中。选哪条由实现者读完代码定，并在报告中说明。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd server && .venv/Scripts/python.exe -m pytest tests/test_duplicate_e2e.py -q
```

- [ ] **Step 3: 写实现**

接任何因真实编排暴露的问题（**大概率会暴露**：stub 与真实 TaskContext 的接口不匹配、`payload` 的实际形状、`previous_state` 的实际形状）。改的是实现，不是测试。

- [ ] **Step 4: 跑全部测试**

```bash
cd server && .venv/Scripts/python.exe -m pytest -q
```

**必须 ≥ 300 passed，0 failed。**

- [ ] **Step 5: 变异探针（必做）**

把幂等分支的 `previous_state` 判据改成恒假（永不幂等），跑 `test_second_analysis_is_idempotent_via_api`，**必须变红**。

- [ ] **Step 6: 提交**

```bash
git add -A server/app server/tests
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): P3a 端到端闭环验收（上传→分析→四段 success→幂等→换文件作废）"
```

---

### Task 7: 收尾——文档与 spec 状态回填

**Files:**
- Modify: `docs/superpowers/specs/2026-09-15-fastapi-check-service-design.md`（§15 状态列）
- Modify: `docs/superpowers/plans/2026-09-19-fastapi-check-service-p3a-duplicate-foundation.md`（勾选）

- [ ] **Step 1: 回填 spec §15**

把 P3a 行状态改为 `**已完成**（N 提交 / M 测试）`，并补一行「P3b 起替换 stub，编排与状态机不动」的说明。

- [ ] **Step 2: 勾选本计划全部步骤**

- [ ] **Step 3: 跑一次全量测试留底**

```bash
cd server && .venv/Scripts/python.exe -m pytest -q
```

- [ ] **Step 4: 提交**

```bash
git add docs/
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "docs(server): P3a 完成回填 spec §15 与本计划勾选"
```

---

## Self-Review

**Spec 覆盖检查：**

| Spec 条目 | 落在哪个任务 |
|---|---|
| §5.2 15 张表 + project_id | Task 1 |
| §5.2 裁定 R31（不抽中性表） | 已写入 spec；Task 1/2 落地 |
| §5.1 任务收归 tasks 表 | Task 1 Step 1 的 `test_duplicate_check_tasks_is_not_created` |
| §9.2 幂等 | Task 4 Step 1 + Task 6 |
| §9.2 并行时序（content 先起 / metadata await / 三段 gather） | Task 4 |
| §9.2 部分失败即整体 error 但结果保留 | Task 4 Step 3 的 `failed` 计算 |
| §9.2 签名校验与结果丢弃 | Task 4 + Task 6 |
| §9.2 overallProgress 等权平均 | Task 3 |
| §9.2 消息优先级 | Task 3 `latest_analysis_message` |
| §10 REST + SSE | Task 5、Task 6 |
| §9.3 Excel 导出 | **不做**（P3d），Task 5 明确 501 |
| §11 测试策略 | 各任务的变异探针 + Task 6 端到端 |

**已知空白（必须在报告中如实说明，不得掩盖）：**

1. **`AUTOINCREMENT` → 显式 `_next_row_id`** 是语义偏差。SQLite 原生自增不会重号，而 `MAX(id)+1` 在事务内也安全（P1 的单连接 + 互斥锁保证了串行），但**跨进程**（方案 B）会失效。记入报告，P3b 若发现并发热点再评估。
2. **`previous_state` 的真实形状**是 P1 `TaskEngine.submit` 里 `_load_previous_state(project_id, kind)` 的产物（上次同 kind 任务的 `checkpoint_json`，见 `engine.py:49`）。Task 4 的幂等实现依赖这个约定，**Task 6 会实证**。若实际不是这个契约，改 Task 4 实现并更新说明。
3. **`payload` 里放哪些键**由 `POST /analysis` 决定（Task 5），`ctx.project_id` 由 TaskEngine 注入（已核实 `context.py:35`）。runner **不得**从 payload 读 `projectId`。

**已核实、不需再猜的接口事实（派发时不要重新论证）：**

- `TaskContext.log(message)` **只收一个参数**（`context.py:81`）——测试断言要写成 `any("…" in m for m in ctx.logs)`，不能按格式化参数写。
- `TaskContext.progress(percent, label=None)`（`context.py:60`）。
- `TaskContext.checkpoint(state)` 写 `checkpoint_json`（`context.py:88`），即下次的 `previous_state`。
- `TaskEngine.submit(project_id=, domain=, kind=, payload=, runner=)`（`engine.py:44`）；runner 签名 `(ctx, payload, previous_state)`。
- `Database` 已有 `transaction()` 上下文管理器（`engine.py:56`），`replace_*` 用它。
