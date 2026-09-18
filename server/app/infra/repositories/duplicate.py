"""查重域仓储：13 张 `duplicate_check_*` 表的读写。

形状契约的权威来源是 Electron 侧只读参考实现
`client/electron/services/duplicateCheckStore.cjs`（`createDuplicateCheckStore`）。
本模块把它的读写路径逐键搬到服务端，但有三处**刻意的服务端差异**：

1. **作用域**：参考实现是单机单例（`duplicate_check_meta WHERE id = 1`、全表 DELETE），
   服务端按 `project_id` 作用域（Task 1 的表结构，spec §5.2 裁定 R31）。
2. **`id` 分配**：Task 1 为四张表去掉了 `AUTOINCREMENT`（SQLite 不允许它出现在非
   `INTEGER PRIMARY KEY` 上），改由 `_next_row_id` 显式分配，在事务内调用。
3. **无 `analysisTask`**：参考实现把分析任务存在 `duplicate_check_tasks`；服务端统一
   收归 `tasks` 表（spec §5.1），故 `load_analysis` 不含该键。

`load_analysis` 里 JS `undefined` 一律映射为 Python `None`（键仍在，值为 `None`），
因为 `None` 在 JSON 里序列化为 `null`，前端 `state.metadataAnalysis?.status` 一类
可选链读取与「键缺失」等价。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.infra.db.engine import Database
from app.infra.repositories.projects import utc_now

_UNSET = object()

# 四张 `INTEGER NOT NULL` 自增列 + 联合主键的表：id 由本层显式分配。
_ID_TABLES = frozenset({
    "duplicate_check_metadata_items",
    "duplicate_check_outline_pairwise",
    "duplicate_check_content_occurrences",
    "duplicate_check_image_occurrences",
})

# 四段分析派生的 detail 表（删除段的并集，对齐参考实现 clearSection，:386-407）。
# 复合主键的两个列名不一致（`id` / `duplicate_id` / `image_id` / `file_id`），
# 实测四种 `PRAGMA table_info` 都恰好一列标记 pk=1，故用 distinct-pk 列判别。
# 表名走本白名单，不是拼接用户输入。
_DETAIL_TABLES = (
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
)

_STATUSES = ("pending", "running", "success", "error")
_STEPS = ("upload", "analysis")
_TABS = ("metadata", "outline", "content", "image")


@dataclass(frozen=True)
class DuplicateFileInput:
    file_id: str
    file_name: str
    file_path: str
    extension: str
    size: int = 0
    modified_at: str | None = None
    content_hash: str | None = None


def _dump_json(value: Any) -> str:
    """稳定序列化：同一 dict 两次 dump 出同一字符串，否则幂等比对会误判。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load_json(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def _normalize_status(value: Any, fallback: str = "pending") -> str:
    return value if value in _STATUSES else fallback


def _normalize_step(value: Any) -> str:
    return value if value in _STEPS else "upload"


def _normalize_tab(value: Any) -> str:
    return value if value in _TABS else "metadata"


def _clamp_progress(value: Any) -> int:
    try:
        number = int(round(float(value or 0)))
    except (TypeError, ValueError):
        number = 0
    return max(0, min(100, number))


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_bool(value: Any) -> bool:
    return bool(value)


def _to_db_bool(value: Any) -> int:
    return 1 if value else 0


def _empty_progress(status: str = "pending", total: int = 0) -> dict:
    return {"status": status, "completed": 0, "total": total}


def _dedupe(values: list[str]) -> list[str]:
    """等价 JS `new Set(...)`：去重且保持首次出现顺序。"""
    return list(dict.fromkeys(values))


def _build_rows(files: list[dict]) -> list[dict]:
    """逐字对齐参考实现 `buildRows`（:141-177）：按 key 首次出现顺序成行。"""
    key_order: list[str] = []
    rows_by_key: dict[str, dict] = {}
    for file in files:
        for item in file["metadata"]:
            key = item["key"]
            if key not in rows_by_key:
                key_order.append(key)
                rows_by_key[key] = {
                    "key": key,
                    "label": item["label"],
                    "values": {},
                    "duplicate_file_ids": [],
                    "same_day_file_ids": [],
                }
            rows_by_key[key]["values"][file["file_id"]] = item["value"]

    for key in key_order:
        row = rows_by_key[key]
        normalized_to_files: dict[str, list[str]] = {}
        day_to_files: dict[str, list[str]] = {}
        for file in files:
            item = next((entry for entry in file["metadata"] if entry["key"] == key), None)
            if not item or not item["comparable"] or not item["normalized"]:
                continue
            if item["date_comparable"]:
                if not item["date_day"]:
                    continue
                day_to_files.setdefault(item["date_day"], []).append(file["file_id"])
                continue
            normalized_to_files.setdefault(item["normalized"], []).append(file["file_id"])
        row["duplicate_file_ids"] = _dedupe(
            [fid for ids in normalized_to_files.values() if len(ids) > 1 for fid in ids])
        row["same_day_file_ids"] = _dedupe(
            [fid for ids in day_to_files.values() if len(ids) > 1 for fid in ids])

    return [rows_by_key[key] for key in key_order]


def _file_from_row(row: Any) -> dict:
    """逐字对齐参考实现 `fileFromRow`（:110-119）。键名是 `id` 而非 `file_id`。"""
    return {
        "id": row["file_id"],
        "file_name": row["file_name"],
        "file_path": row["file_path"],
        "extension": row["extension"],
        "size": _as_int(row["size"]),
        "modified_at": row["modified_at"] or "",
    }


class DuplicateRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ---------------------------------------------------------------- 元信息

    def get_meta(self, project_id: str) -> dict | None:
        row = self._db.query_one(
            "SELECT * FROM duplicate_check_meta WHERE project_id = ?", (project_id,))
        return dict(row) if row else None

    def save_meta(self, project_id: str, *, step: str | None = None,
                  active_analysis_tab: str | None = None,
                  current_signature: Any = _UNSET) -> None:
        """写元信息（本项目无行则新建）。

        `step`/`active_analysis_tab` 传 `None` 即「未携带」——保留旧值（无旧行则
        回落 `'upload'`/`'metadata'`）。这两列在 schema 里是 `NOT NULL DEFAULT`，
        语义上不存在「清空成 NULL」这种状态，所以不需要 `_UNSET` 哨兵：
        `None` 已足够表达「未携带」，没有第二种要区分的含义。

        `current_signature` 不同：它可空，且「未携带」（保留旧值）与「显式写
        NULL」（清空签名）是两个真实不同的意图，用一个 `None` 表达会二义，故用
        `_UNSET` 哨兵区分。这也是写 SQL 前先 `SELECT` 现有行再
        `INSERT … ON CONFLICT DO UPDATE` 的原因——纯 SQL 的 `COALESCE` 分不开这两者。
        """
        existing = self.get_meta(project_id)
        timestamp = utc_now()
        new_step = _normalize_step(step) if step is not None else (existing or {}).get("step", "upload")
        new_tab = (_normalize_tab(active_analysis_tab) if active_analysis_tab is not None
                   else (existing or {}).get("active_analysis_tab", "metadata"))
        if current_signature is _UNSET:
            new_signature = (existing or {}).get("current_signature")
        else:
            new_signature = current_signature
        self._db.execute(
            "INSERT INTO duplicate_check_meta"
            "(project_id, step, active_analysis_tab, current_signature, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(project_id) DO UPDATE SET"
            " step = excluded.step,"
            " active_analysis_tab = excluded.active_analysis_tab,"
            " current_signature = excluded.current_signature,"
            " updated_at = excluded.updated_at",
            (project_id, new_step, new_tab, new_signature, timestamp, timestamp),
        )

    # -------------------------------------------------------------- 文件簿记

    def replace_files(self, project_id: str, role: str,
                      files: Sequence[DuplicateFileInput]) -> None:
        """先删本项目该 role 的旧行再插，`sort_order` 按入参顺序。

        删 `duplicate_check_files` 行会经 FK `ON DELETE CASCADE` 连带清掉该
        role 的下游行（content_files/metadata_items/outline_items/image_files/
        occurrences）。
        """
        timestamp = utc_now()
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM duplicate_check_files WHERE project_id = ? AND role = ?",
                (project_id, role))
            for index, item in enumerate(files):
                conn.execute(
                    "INSERT INTO duplicate_check_files"
                    "(project_id, file_id, role, file_name, file_path, extension, size,"
                    " modified_at, sort_order, content_hash, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (project_id, item.file_id, role, item.file_name, item.file_path,
                     item.extension, _as_int(item.size), item.modified_at, index,
                     item.content_hash, timestamp, timestamp))

    def list_files(self, project_id: str, role: str | None = None) -> list[dict]:
        if role is None:
            rows = self._db.query(
                "SELECT * FROM duplicate_check_files WHERE project_id = ?"
                " ORDER BY role ASC, sort_order ASC", (project_id,))
        else:
            rows = self._db.query(
                "SELECT * FROM duplicate_check_files WHERE project_id = ? AND role = ?"
                " ORDER BY sort_order ASC", (project_id, role))
        return [dict(row) for row in rows]

    def delete_files(self, project_id: str) -> None:
        """只删 `duplicate_check_files`；下游表靠 FK CASCADE 级联清空。"""
        self._db.execute(
            "DELETE FROM duplicate_check_files WHERE project_id = ?", (project_id,))

    # -------------------------------------------------------------- 四段状态

    def get_sections(self, project_id: str) -> dict[str, dict]:
        """返回 `{section: row}`，`row["stats"]` 是解码后的 dict（`stats_json` 不再重复暴露）。"""
        rows = self._db.query(
            "SELECT * FROM duplicate_check_analysis_sections WHERE project_id = ?",
            (project_id,))
        result: dict[str, dict] = {}
        for row in rows:
            item = dict(row)
            item["stats"] = _load_json(item.pop("stats_json", None), {})
            result[row["section"]] = item
        return result

    def save_section(self, project_id: str, section: str, *, status: str, progress: int,
                     message: str, signature: Any = _UNSET, stats: Any = _UNSET,
                     started_at: Any = _UNSET, updated_at: str) -> None:
        """`signature`/`stats`/`started_at` 未携带（`_UNSET`）则保留旧值；
        显式传 `None` 则写 NULL。这是一个真实的「未携带 vs 显式 NULL」二分。"""
        existing = self._db.query_one(
            "SELECT * FROM duplicate_check_analysis_sections"
            " WHERE project_id = ? AND section = ?", (project_id, section))

        if signature is _UNSET:
            new_signature = existing["signature"] if existing else None
        else:
            new_signature = signature
        if stats is _UNSET:
            new_stats_json = existing["stats_json"] if existing else None
        elif stats is None:
            new_stats_json = None
        else:
            new_stats_json = _dump_json(stats)
        if started_at is _UNSET:
            new_started_at = existing["started_at"] if existing else None
        else:
            new_started_at = started_at

        self._db.execute(
            "INSERT INTO duplicate_check_analysis_sections"
            "(project_id, section, status, progress, message, signature, stats_json,"
            " started_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(project_id, section) DO UPDATE SET"
            " status = excluded.status,"
            " progress = excluded.progress,"
            " message = excluded.message,"
            " signature = excluded.signature,"
            " stats_json = excluded.stats_json,"
            " started_at = excluded.started_at,"
            " updated_at = excluded.updated_at",
            (project_id, section, status, _clamp_progress(progress), message or "",
             new_signature, new_stats_json, new_started_at, updated_at),
        )

    def delete_sections(self, project_id: str) -> None:
        """删本项目全部分析状态，并在同一事务内清掉四段的 detail 表。

        对齐参考实现 `clearSection`（:386-407）的分段级联；本项目是「全清」，
        故并集是参考实现四个 `clearSection` 分支的并集，另外把
        `duplicate_check_files.content_hash` 置 NULL（参考 :391）。
        """
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM duplicate_check_analysis_sections WHERE project_id = ?",
                (project_id,))
            for table in _DETAIL_TABLES:
                conn.execute(f"DELETE FROM {table} WHERE project_id = ?", (project_id,))
            conn.execute(
                "UPDATE duplicate_check_files SET content_hash = NULL, updated_at = ?"
                " WHERE project_id = ?", (utc_now(), project_id))

    # ------------------------------------------------------------ 结果表写入

    def replace_content_files(self, project_id: str, rows: Sequence[dict]) -> None:
        """行键：`file_id`(必)、`status`、`content_path`、`content_length`、
        `parser_label`、`error`、`updated_at`，可选 `content_hash`。缺 `file_id` 的行跳过。

        事务内先把本项目所有 `duplicate_check_files.content_hash` 置 NULL，再对
        携带 `content_hash` 的行回写（对齐参考 `saveMetadataAnalysisDetails`
        :418 / :435-439）。`content_hash` 是内容提取的产物——上传阶段
        （`replace_files`）拿不到 hash，故权威写在这里，未携带 hash 的行保持 NULL。
        """
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM duplicate_check_content_files WHERE project_id = ?",
                (project_id,))
            conn.execute(
                "UPDATE duplicate_check_files SET content_hash = NULL, updated_at = ?"
                " WHERE project_id = ?", (utc_now(), project_id))
            for row in rows:
                file_id = row.get("file_id")
                if not file_id:
                    continue
                conn.execute(
                    "INSERT INTO duplicate_check_content_files"
                    "(project_id, file_id, status, content_path, content_length,"
                    " parser_label, error, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (project_id, str(file_id), str(row.get("status") or "pending"),
                     row.get("content_path"), _as_int(row.get("content_length")),
                     row.get("parser_label"), row.get("error"),
                     row.get("updated_at") or utc_now()))
                conn.execute(
                    "UPDATE duplicate_check_files SET content_hash = ?, updated_at = ?"
                    " WHERE project_id = ? AND file_id = ?",
                    (row.get("content_hash"), row.get("updated_at") or utc_now(),
                     project_id, str(file_id)))

    def replace_metadata_items(self, project_id: str, rows: Sequence[dict]) -> None:
        """行键：`file_id`(必)、`key`(必)、`label`、`value`、`normalized`、
        `date_day`、`comparable`、`date_comparable`、`sort_order`（缺省按入参序号）。
        `id` 由 `_next_row_id` 分配。"""
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM duplicate_check_metadata_items WHERE project_id = ?",
                (project_id,))
            for index, row in enumerate(rows):
                file_id = row.get("file_id")
                key = row.get("key")
                if not file_id or not key:
                    continue
                next_id = self._next_row_id(project_id, "duplicate_check_metadata_items")
                conn.execute(
                    "INSERT INTO duplicate_check_metadata_items"
                    "(project_id, id, file_id, key, label, value, normalized, date_day,"
                    " comparable, date_comparable, sort_order)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (project_id, next_id, str(file_id), str(key),
                     str(row.get("label") or key), str(row.get("value") or ""),
                     row.get("normalized"), row.get("date_day"),
                     _to_db_bool(row.get("comparable")),
                     _to_db_bool(row.get("date_comparable")),
                     _as_int(row.get("sort_order", index))))

    def replace_outline_items(self, project_id: str, rows: Sequence[dict]) -> None:
        """行键：`item_id`(必)、`file_id`(必)、`parent_item_id`、`level`、`number`、
        `title`、`normalized_title`、`path_titles`、`normalized_path`、`source`、
        `confidence`、`sort_order`、`from_tender`、`matched_tender_sentence`。

        `item_id`/`parent_item_id` 存 **scoped** 形式（`file_id::item_id`，对齐参考
        实现 `scopedOutlineItemId`），父行先于子行写入以满足自引用 FK。

        `parent_item_id` 默认按**子行自己的** `file_id` 前缀补齐为 scoped 形式（也可
        直接传已 scoped 的 id），父行先于子行写入以满足自引用 FK。跨文件的父指针
        （`parent_file_id` 与子行的 `file_id` 不同、或父 id 属于别的文件）在本服务端
        schema 下无意义（`item_id` 本身已 scoped），一律置 `None` 而不是拼出一个不
        存在的 id 去撞 `FOREIGN KEY constraint failed`。

        **本方法要求全量入参、须单次调用。** 它先 `DELETE` 本项目全部行再整体重建，
        而父指针的存在性校验完全交给自引用 FK——若调用方按文件分批调用，第二批次
        会把第一批已入库的父行删掉（因为整表被清），父 id 又无 `::`，父指针即静默
        丢失。参考实现同样不存在分批场景：`duplicateCheckService.cjs:2467-2475` 每
        次 partial 都重写全量快照，`duplicateCheckStore.cjs:474-488` 也是先 `DELETE`
        全表再逐文件插入。
        """
        scoped = [
            _scope_outline_row(row)
            for row in rows if row.get("item_id") and row.get("file_id")
        ]
        ordered = _parents_first(scoped)
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM duplicate_check_outline_items WHERE project_id = ?",
                (project_id,))
            for row in ordered:
                conn.execute(
                    "INSERT INTO duplicate_check_outline_items"
                    "(project_id, item_id, file_id, parent_item_id, level, number, title,"
                    " normalized_title, path_titles_json, normalized_path, source,"
                    " confidence, sort_order, from_tender, matched_tender_sentence)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (project_id, row["item_id"], row["file_id"], row["parent_item_id"],
                     row["level"], row["number"], row["title"], row["normalized_title"],
                     row["path_titles_json"], row["normalized_path"], row["source"],
                     row["confidence"], row["sort_order"], row["from_tender"],
                     row["matched_tender_sentence"]))

    def replace_outline_groups(self, project_id: str, rows: Sequence[dict]) -> None:
        """行键：`group_id`(必)、`type`、`title`、`score`、`file_ids`、`item_ids`、
        `paths`、`sort_order`（缺省按入参序号）。"""
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM duplicate_check_outline_groups WHERE project_id = ?",
                (project_id,))
            for index, row in enumerate(rows):
                group_id = row.get("group_id")
                if not group_id:
                    continue
                conn.execute(
                    "INSERT INTO duplicate_check_outline_groups"
                    "(project_id, group_id, type, title, score, file_ids_json,"
                    " item_ids_json, paths_json, sort_order)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (project_id, str(group_id), str(row.get("type") or "duplicate"),
                     str(row.get("title") or ""), _as_float(row.get("score")),
                     _dump_json(row.get("file_ids") or []),
                     _dump_json(row.get("item_ids") or {}),
                     _dump_json(row.get("paths") or {}),
                     _as_int(row.get("sort_order", index))))

    def replace_outline_pairwise(self, project_id: str, rows: Sequence[dict]) -> None:
        """行键：`file_a_id`(必)、`file_b_id`(必)、`score`、`title_overlap`、
        `path_overlap`、`order_similarity`、`shared_count`、`risk`。"""
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM duplicate_check_outline_pairwise WHERE project_id = ?",
                (project_id,))
            for row in rows:
                file_a_id = row.get("file_a_id")
                file_b_id = row.get("file_b_id")
                if not file_a_id or not file_b_id:
                    continue
                next_id = self._next_row_id(project_id, "duplicate_check_outline_pairwise")
                conn.execute(
                    "INSERT INTO duplicate_check_outline_pairwise"
                    "(project_id, id, file_a_id, file_b_id, score, title_overlap,"
                    " path_overlap, order_similarity, shared_count, risk)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (project_id, next_id, str(file_a_id), str(file_b_id),
                     _as_float(row.get("score")), _as_float(row.get("title_overlap")),
                     _as_float(row.get("path_overlap")),
                     _as_float(row.get("order_similarity")),
                     _as_int(row.get("shared_count")), str(row.get("risk") or "none")))

    def replace_content_duplicates(self, project_id: str, rows: Sequence[dict],
                                   occurrences: Sequence[dict]) -> None:
        """`rows` 行键：`duplicate_id`(必)、`sentence`、`normalized`、`file_ids`、
        `first_order`。
        `occurrences` 行键：`duplicate_id`(必)、`file_id`(必)、`occurrence_count`。
        `id` 由 `_next_row_id` 分配。"""
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM duplicate_check_content_occurrences WHERE project_id = ?",
                (project_id,))
            conn.execute(
                "DELETE FROM duplicate_check_content_duplicates WHERE project_id = ?",
                (project_id,))
            for index, row in enumerate(rows):
                duplicate_id = row.get("duplicate_id")
                if not duplicate_id:
                    continue
                conn.execute(
                    "INSERT INTO duplicate_check_content_duplicates"
                    "(project_id, duplicate_id, sentence, normalized, file_ids_json,"
                    " first_order) VALUES (?, ?, ?, ?, ?, ?)",
                    (project_id, str(duplicate_id), str(row.get("sentence") or ""),
                     str(row.get("normalized") or ""),
                     _dump_json(row.get("file_ids") or []),
                     _as_int(row.get("first_order", index))))
            for row in occurrences:
                duplicate_id = row.get("duplicate_id")
                file_id = row.get("file_id")
                if not duplicate_id or not file_id:
                    continue
                next_id = self._next_row_id(
                    project_id, "duplicate_check_content_occurrences")
                conn.execute(
                    "INSERT INTO duplicate_check_content_occurrences"
                    "(project_id, id, duplicate_id, file_id, occurrence_count)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (project_id, next_id, str(duplicate_id), str(file_id),
                     _as_int(row.get("occurrence_count"))))

    def replace_image_files(self, project_id: str, rows: Sequence[dict]) -> None:
        """行键：`file_id`(必)、`status`、`image_count`、`unique_image_count`、
        `error`、`updated_at`。"""
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM duplicate_check_image_files WHERE project_id = ?",
                (project_id,))
            for row in rows:
                file_id = row.get("file_id")
                if not file_id:
                    continue
                conn.execute(
                    "INSERT INTO duplicate_check_image_files"
                    "(project_id, file_id, status, image_count, unique_image_count,"
                    " error, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (project_id, str(file_id), str(row.get("status") or "pending"),
                     _as_int(row.get("image_count")),
                     _as_int(row.get("unique_image_count")), row.get("error"),
                     row.get("updated_at") or utc_now()))

    def replace_duplicate_images(self, project_id: str, images: Sequence[dict],
                                 occurrences: Sequence[dict]) -> None:
        """`images` 行键：`image_id`(必)、`hash`、`preview_url`、`file_ids`、
        `sort_order`（缺省按入参序号）。
        `occurrences` 行键：`image_id`(必)、`file_id`(必)、`occurrence_count`、
        `locations`。`id` 由 `_next_row_id` 分配。"""
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM duplicate_check_image_occurrences WHERE project_id = ?",
                (project_id,))
            conn.execute(
                "DELETE FROM duplicate_check_duplicate_images WHERE project_id = ?",
                (project_id,))
            for index, image in enumerate(images):
                image_id = image.get("image_id")
                if not image_id:
                    continue
                conn.execute(
                    "INSERT INTO duplicate_check_duplicate_images"
                    "(project_id, image_id, hash, preview_url, file_ids_json, sort_order)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (project_id, str(image_id), str(image.get("hash") or ""),
                     str(image.get("preview_url") or ""),
                     _dump_json(image.get("file_ids") or []),
                     _as_int(image.get("sort_order", index))))
            for row in occurrences:
                image_id = row.get("image_id")
                file_id = row.get("file_id")
                if not image_id or not file_id:
                    continue
                next_id = self._next_row_id(
                    project_id, "duplicate_check_image_occurrences")
                locations = row.get("locations")
                conn.execute(
                    "INSERT INTO duplicate_check_image_occurrences"
                    "(project_id, id, image_id, file_id, occurrence_count, locations_json)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (project_id, next_id, str(image_id), str(file_id),
                     _as_int(row.get("occurrence_count")),
                     None if locations is None else _dump_json(locations)))

    # ------------------------------------------------------------------ 读回

    def load_analysis(self, project_id: str) -> dict:
        """组装成参考实现 `store.loadDuplicateCheck()` 的 analysis 形状。

        与参考实现的**唯一差异**是多了顶层 `contentFiles`（参考实现里它只在
        `metadataAnalysis.contentFiles` 下，且当 metadata 段无行时不可达）——
        这个顶层键由 brief 的 `test_delete_files_cascades_scoped_rows` 钉住。
        """
        files_state = self._load_files_state(project_id)
        sections = self.get_sections(project_id)
        name_by_id = {file["id"]: file["file_name"]
                      for file in [*files_state["tenderFiles"], *files_state["bidFiles"]]}
        return {
            **files_state,
            "step": _normalize_step((self.get_meta(project_id) or {}).get("step")),
            "activeAnalysisTab": _normalize_tab(
                (self.get_meta(project_id) or {}).get("active_analysis_tab")),
            "metadataAnalysis": self._load_metadata_analysis(
                project_id, sections.get("metadata"), files_state, name_by_id),
            "outlineAnalysis": self._load_outline_analysis(
                project_id, sections.get("outline"), files_state),
            "contentAnalysis": self._load_content_analysis(
                project_id, sections.get("content"), files_state),
            "imageAnalysis": self._load_image_analysis(
                project_id, sections.get("image"), files_state, name_by_id),
            "contentFiles": self._load_content_files(project_id, name_by_id),
        }

    # ------------------------------------------------------------ 内部实现

    def _next_row_id(self, project_id: str, table: str) -> int:
        """`MAX(id) + 1`，在 `replace_*` 的事务内调用以保证不重号。"""
        if table not in _ID_TABLES:
            raise ValueError(f"表 {table} 不由 _next_row_id 分配 id")
        row = self._db.query_one(
            f"SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM {table}"
            " WHERE project_id = ?", (project_id,))
        return int(row["next_id"])

    def _load_files_state(self, project_id: str) -> dict:
        """逐字对齐参考实现 `loadFiles`（:250-260）+ `fileFromRow`。"""
        rows = self._db.query(
            "SELECT * FROM duplicate_check_files WHERE project_id = ?"
            " ORDER BY role ASC, sort_order ASC", (project_id,))
        tender_files = [_file_from_row(row) for row in rows if row["role"] == "tender"]
        bid_files = [_file_from_row(row) for row in rows if row["role"] == "bid"]
        return {
            "tenderFile": tender_files[0] if tender_files else None,
            "tenderFiles": tender_files,
            "bidFiles": bid_files,
        }

    def _load_content_files(self, project_id: str, name_by_id: dict[str, str]) -> list[dict]:
        rows = self._db.query(
            "SELECT * FROM duplicate_check_content_files WHERE project_id = ?"
            " ORDER BY file_id ASC", (project_id,))
        return [
            {
                "file_id": row["file_id"],
                "file_name": name_by_id.get(row["file_id"], row["file_id"]),
                "status": _normalize_status(row["status"]),
                "content_path": row["content_path"] or None,
                "content_length": _as_int(row["content_length"]),
                "parser_label": row["parser_label"] or None,
                "error": row["error"] or None,
            }
            for row in rows
        ]

    def _section_common(self, row: Any) -> dict:
        """`row` 是 `get_sections` 产出的行（`stats_json` 已被 pop 成 `stats`）。"""
        stats = row.get("stats") or {}
        return {
            "status": _normalize_status(row["status"]),
            "progress": _as_int(row["progress"]),
            "message": row["message"] or "",
            "signature": row["signature"] or None,
            "started_at": row["started_at"] or None,
            "updated_at": row["updated_at"] or None,
            "_stats": stats,
        }

    def _load_metadata_analysis(self, project_id: str, row: Any, files_state: dict,
                                name_by_id: dict[str, str]) -> dict | None:
        """逐字对齐参考实现 `loadMetadataAnalysis`（:633-682）。"""
        if row is None:
            return None
        common = self._section_common(row)
        stats = common.pop("_stats")
        content_files = self._load_content_files(project_id, name_by_id)
        metadata_rows = self._db.query(
            "SELECT * FROM duplicate_check_metadata_items WHERE project_id = ?"
            " ORDER BY file_id ASC, sort_order ASC, id ASC", (project_id,))
        status_by_file = {entry["file_id"]: entry for entry in stats.get("files") or []}

        files_by_id: dict[str, dict] = {}
        for file in files_state["bidFiles"]:
            summary = status_by_file.get(file["id"]) or {}
            files_by_id[file["id"]] = {
                "file_id": file["id"],
                "file_name": file["file_name"],
                "status": summary.get("status") or "pending",
                "metadata": [],
                "error": summary.get("error"),
            }
        for item in metadata_rows:
            if item["file_id"] not in files_by_id:
                summary = status_by_file.get(item["file_id"]) or {}
                files_by_id[item["file_id"]] = {
                    "file_id": item["file_id"],
                    "file_name": name_by_id.get(item["file_id"], item["file_id"]),
                    "status": summary.get("status") or "success",
                    "metadata": [],
                    "error": summary.get("error"),
                }
            files_by_id[item["file_id"]]["metadata"].append({
                "key": item["key"],
                "label": item["label"],
                "value": item["value"] or "",
                "normalized": item["normalized"] or None,
                "date_day": item["date_day"] or None,
                "comparable": _as_bool(item["comparable"]),
                "date_comparable": _as_bool(item["date_comparable"]),
            })
        files = list(files_by_id.values())
        return {
            **common,
            "contentExtraction": stats.get("contentExtraction")
            or _empty_progress("pending", len(content_files)),
            "metadataExtraction": stats.get("metadataExtraction")
            or _empty_progress("pending", len(files)),
            "files": files,
            "rows": _build_rows(files),
            "contentFiles": content_files,
            "logs": stats.get("logs") if isinstance(stats.get("logs"), list) else [],
        }

    def _load_outline_analysis(self, project_id: str, row: Any,
                               files_state: dict) -> dict | None:
        """逐字对齐参考实现 `loadOutlineAnalysis`（:684-771）。"""
        if row is None:
            return None
        common = self._section_common(row)
        stats = common.pop("_stats")

        items_by_file: dict[str, list[dict]] = {}
        for item in self._db.query(
                "SELECT * FROM duplicate_check_outline_items WHERE project_id = ?"
                " ORDER BY file_id ASC, sort_order ASC", (project_id,)):
            items_by_file.setdefault(item["file_id"], []).append({
                "id": _unscoped_item_id(item["item_id"]),
                "level": _as_int(item["level"]) or 1,
                "number": item["number"] or None,
                "title": item["title"],
                "normalized_title": item["normalized_title"],
                "path_titles": _load_json(item["path_titles_json"], []),
                "normalized_path": item["normalized_path"],
                "source": item["source"],
                "confidence": _as_float(item["confidence"]),
                "order": _as_int(item["sort_order"]),
                "parent_id": (_unscoped_item_id(item["parent_item_id"])
                              if item["parent_item_id"] else None),
                "from_tender": _as_bool(item["from_tender"]),
                "matched_tender_sentence": item["matched_tender_sentence"] or None,
                "duplicate_group_ids": [],
                "similar_group_ids": [],
            })

        group_rows = self._db.query(
            "SELECT * FROM duplicate_check_outline_groups WHERE project_id = ?"
            " ORDER BY sort_order ASC", (project_id,))
        duplicate_groups = [
            {
                "id": group["group_id"],
                "type": group["type"],
                "title": group["title"],
                "score": _as_float(group["score"]),
                "file_ids": _load_json(group["file_ids_json"], []),
                "item_ids": _load_json(group["item_ids_json"], {}),
                "paths": _load_json(group["paths_json"], {}),
            }
            for group in group_rows
        ]
        for group in duplicate_groups:
            for file_id, item_ids in (group["item_ids"] or {}).items():
                items = items_by_file.get(file_id) or []
                for item_id in item_ids if isinstance(item_ids, list) else []:
                    item = next((entry for entry in items if entry["id"] == item_id), None)
                    if item is None:
                        continue
                    field = "similar_group_ids" if group["type"] == "similar" else "duplicate_group_ids"
                    if group["id"] not in item[field]:
                        item[field].append(group["id"])

        summary_by_file = {entry["file_id"]: entry for entry in stats.get("files") or []}
        files = []
        for file in files_state["bidFiles"]:
            summary = summary_by_file.get(file["id"]) or {}
            items = items_by_file.get(file["id"]) or []
            files.append({
                "file_id": file["id"],
                "file_name": file["file_name"],
                "status": summary.get("status") or ("success" if items else "pending"),
                "source": summary.get("source") or (items[0]["source"] if items else None),
                "confidence": _as_float(
                    summary["confidence"] if summary.get("confidence") is not None
                    else (items[0]["confidence"] if items else 0)),
                "item_count": _as_int(
                    summary["item_count"] if summary.get("item_count") is not None
                    else len(items)),
                "tender_matched_count": _as_int(
                    summary["tender_matched_count"]
                    if summary.get("tender_matched_count") is not None
                    else len([item for item in items if item["from_tender"]])),
                "items": items,
                "error": summary.get("error"),
            })

        pairwise = [
            {
                "file_a_id": item["file_a_id"],
                "file_b_id": item["file_b_id"],
                "score": _as_float(item["score"]),
                "title_overlap": _as_float(item["title_overlap"]),
                "path_overlap": _as_float(item["path_overlap"]),
                "order_similarity": _as_float(item["order_similarity"]),
                "shared_count": _as_int(item["shared_count"]),
                "risk": item["risk"] or "none",
            }
            for item in self._db.query(
                "SELECT * FROM duplicate_check_outline_pairwise WHERE project_id = ?"
                " ORDER BY score DESC, id ASC", (project_id,))
        ]
        return {
            **common,
            "tenderSentenceCount": _as_int(stats.get("tenderSentenceCount")),
            "tenderMatchedItemCount": _as_int(stats.get("tenderMatchedItemCount")),
            "extraction": stats.get("extraction")
            or _empty_progress("pending", len(files)),
            "files": files,
            "duplicateGroups": duplicate_groups,
            "pairwiseSimilarities": pairwise,
        }

    def _load_content_analysis(self, project_id: str, row: Any,
                               files_state: dict) -> dict | None:
        """逐字对齐参考实现 `loadContentAnalysis`（:773-804）。"""
        if row is None:
            return None
        common = self._section_common(row)
        stats = common.pop("_stats")

        occurrences_by_duplicate: dict[str, dict[str, int]] = {}
        for item in self._db.query(
                "SELECT * FROM duplicate_check_content_occurrences"
                " WHERE project_id = ?", (project_id,)):
            occurrences_by_duplicate.setdefault(item["duplicate_id"], {})[
                item["file_id"]] = _as_int(item["occurrence_count"])

        duplicate_sentences = [
            {
                "id": item["duplicate_id"],
                "sentence": item["sentence"],
                "normalized": item["normalized"],
                "file_ids": _load_json(item["file_ids_json"], []),
                "occurrences": occurrences_by_duplicate.get(item["duplicate_id"], {}),
                "first_order": _as_int(item["first_order"]),
            }
            for item in self._db.query(
                "SELECT * FROM duplicate_check_content_duplicates WHERE project_id = ?"
                " ORDER BY first_order ASC", (project_id,))
        ]
        return {
            **common,
            "tenderSentenceCount": _as_int(stats.get("tenderSentenceCount")),
            "tenderMatchedSentenceCount": _as_int(stats.get("tenderMatchedSentenceCount")),
            "totalSentenceCount": _as_int(stats.get("totalSentenceCount")),
            "extraction": stats.get("extraction")
            or _empty_progress("pending", len(files_state["bidFiles"])),
            "duplicateSentences": duplicate_sentences,
        }

    def _load_image_analysis(self, project_id: str, row: Any, files_state: dict,
                             name_by_id: dict[str, str]) -> dict | None:
        """逐字对齐参考实现 `loadImageAnalysis`（:806-848）。"""
        if row is None:
            return None
        common = self._section_common(row)
        stats = common.pop("_stats")

        files = [
            {
                "file_id": item["file_id"],
                "file_name": name_by_id.get(item["file_id"], item["file_id"]),
                "status": _normalize_status(item["status"]),
                "image_count": _as_int(item["image_count"]),
                "unique_image_count": _as_int(item["unique_image_count"]),
                "error": item["error"] or None,
            }
            for item in self._db.query(
                "SELECT * FROM duplicate_check_image_files WHERE project_id = ?"
                " ORDER BY file_id ASC", (project_id,))
        ]

        occurrences_by_image: dict[str, dict[str, int]] = {}
        locations_by_image: dict[str, dict[str, Any]] = {}
        for item in self._db.query(
                "SELECT * FROM duplicate_check_image_occurrences WHERE project_id = ?",
                (project_id,)):
            occurrences_by_image.setdefault(item["image_id"], {})[
                item["file_id"]] = _as_int(item["occurrence_count"])
            locations_by_image.setdefault(item["image_id"], {})[
                item["file_id"]] = _load_json(item["locations_json"], [])

        duplicate_images = [
            {
                "id": item["image_id"],
                "hash": item["hash"],
                "preview_url": item["preview_url"],
                "file_ids": _load_json(item["file_ids_json"], []),
                "occurrences": occurrences_by_image.get(item["image_id"], {}),
                "locations": locations_by_image.get(item["image_id"], {}),
            }
            for item in self._db.query(
                "SELECT * FROM duplicate_check_duplicate_images WHERE project_id = ?"
                " ORDER BY sort_order ASC", (project_id,))
        ]
        return {
            **common,
            "extraction": stats.get("extraction")
            or _empty_progress("pending", len(files_state["bidFiles"])),
            "totalImageCount": _as_int(stats.get("totalImageCount")),
            "files": files,
            "duplicateImages": duplicate_images,
        }


def _unscoped_item_id(item_id: str) -> str:
    """逐字对齐参考实现 `unscopedOutlineItemId`（:82-84）。"""
    text = str(item_id or "")
    return "::".join(text.split("::")[1:]) if "::" in text else text


def _scope_outline_row(row: dict) -> dict | None:
    """把一行 outline item 转成入库形状。

    父指针只在「父节点确实存在于**同一个文件**」时保留；否则置 `None`，绝不拼一个
    不存在的 id 去撞 FK。两条规则：

    1. `parent_item_id` 已是 scoped 形式（含 `::`）→ 原样保留（父 id 自带作用域）。
       调用方须保证它指向**本次入参内**的行；本函数不做存在性校验。
    2. 否则按**子行自己的** `file_id` 前缀补齐；但若 `parent_file_id` 明示了别的
       文件，则显式判无效置 `None`（跨文件父指针在本 schema 下无意义）。

    本函数是纯函数、不看库内既有状态。『父 id 是否真的存在』交给
    `_parents_first`（保证父行先写）与自引用 FK 兜底，因此调用方必须**一次性传入
    全量行**（见 `replace_outline_items` 的 docstring）。
    """
    file_id = str(row["file_id"])
    item_id = f"{file_id}::{row['item_id']}"
    parent_item_id = None
    raw_parent = row.get("parent_item_id")
    if raw_parent:
        text_parent = str(raw_parent)
        parent_file_id = str(row.get("parent_file_id") or file_id)
        candidate = text_parent if "::" in text_parent else f"{file_id}::{text_parent}"
        if "::" in text_parent:
            parent_item_id = candidate
        elif parent_file_id != file_id:
            parent_item_id = None          # 跨文件父指针：显式判无效
        else:
            parent_item_id = candidate
    return {
        "item_id": item_id,
        "file_id": file_id,
        "parent_item_id": parent_item_id,
        "level": _as_int(row.get("level", 1)) or 1,
        "number": row.get("number"),
        "title": str(row.get("title") or ""),
        "normalized_title": str(row.get("normalized_title") or ""),
        "path_titles_json": _dump_json(row.get("path_titles") or []),
        "normalized_path": str(row.get("normalized_path") or ""),
        "source": str(row.get("source") or "semantic"),
        "confidence": _as_float(row.get("confidence")),
        "sort_order": _as_int(row.get("sort_order")),
        "from_tender": _to_db_bool(row.get("from_tender")),
        "matched_tender_sentence": row.get("matched_tender_sentence"),
    }


def _parents_first(rows: list[dict]) -> list[dict]:
    """父行先于子行（自引用 FK 要求），同层保持原序。"""
    by_id = {row["item_id"]: row for row in rows}
    ordered: list[dict] = []
    placed: set[str] = set()

    def place(row: dict, seen: set[str]) -> None:
        if row["item_id"] in placed:
            return
        parent_id = row["parent_item_id"]
        if parent_id and parent_id in by_id and parent_id not in seen:
            seen.add(row["item_id"])
            place(by_id[parent_id], seen)
        placed.add(row["item_id"])
        ordered.append(row)

    for row in rows:
        place(row, set())
    return ordered
