"""废标项检查仓储（P4a T2a：meta / documents / extraction）。

语义逐条对齐参考 `client/electron/services/rejectionCheckStore.cjs`：
- `ensureMetaRow` :191-204 / `updateMeta` :205-215
- `loadDocumentRow` :251-264 / `savePreparedDocument` :305-326（upsert）
- `saveExtraction` :552-575 / `loadExtraction` :577-589

与参考的**结构性差异**只有一处（spec §5.1 裁定）：全部按 `project_id` 作用域，
单例表由 `id = 1` 变成 `project_id` 主键；文档 upsert 的冲突目标相应变成
`(project_id, document_id)`。

`results` / 四类 findings / `visual_assets` / `subject_profile` 属 T2b，本模块暂不提供。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.domain.rejection.assets import visual_assets_values, visual_assets_view
from app.domain.rejection.findings import (
    FINDING_TABLES,
    RESULT_TYPES,
    findings_values,
    findings_view,
)
from app.domain.rejection.normalize import (
    DEFAULT_CHECK_OPTIONS,
    SECTION_STATUSES,
    normalize_document_role,
    normalize_status,
    strip_triple_quote_wrapper,
)
from app.domain.rejection.profile import subject_profile_values, subject_profile_view
from app.infra.db.engine import Database

_META_COLUMNS = frozenset({
    "step", "active_document_tab", "active_result_tab",
    "active_check_result_tab", "custom_check_items", "check_options_json",
})

_DOCUMENT_COLUMNS = (
    "document_id", "role", "source", "file_name", "markdown_path",
    "content_hash", "content_chars", "parser_label", "sort_order",
    "imported_at", "updated_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RejectionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ meta

    def get_meta(self, project_id: str) -> dict | None:
        row = self._db.query_one(
            "SELECT * FROM rejection_check_meta WHERE project_id = ?", (project_id,))
        return dict(row) if row else None

    def ensure_meta(self, project_id: str) -> dict:
        """缺行则按参考的默认值建（:196-203），已有则原样返回。"""
        existing = self.get_meta(project_id)
        if existing is not None:
            return existing
        timestamp = _now()
        self._db.execute(
            "INSERT INTO rejection_check_meta (project_id, step, active_document_tab,"
            " active_result_tab, active_check_result_tab, custom_check_items,"
            " check_options_json, created_at, updated_at)"
            " VALUES (?, 'documents', 'tender', 'analysis', 'rejection', '', ?, ?, ?)",
            (project_id, json.dumps(DEFAULT_CHECK_OPTIONS, ensure_ascii=False),
             timestamp, timestamp),
        )
        created = self.get_meta(project_id)
        assert created is not None  # 刚插入，不可能缺失
        return created

    def update_meta(self, project_id: str, fields: Mapping[str, Any]) -> None:
        """只写**显式传入**的键，并刷新 `updated_at`（对齐 :205-215）。

        参考用 `value !== undefined` 过滤；Python 侧「未提供」即键不存在，
        故不需要额外过滤——调用方省略键就等价于 JS 的 undefined。
        列名走白名单：键直接拼进 SQL，白名单既防注入也防拼写错误。
        """
        self.ensure_meta(project_id)
        entries = {key: value for key, value in fields.items() if key in _META_COLUMNS}
        if not entries:
            return
        assignments = ", ".join(f"{key} = ?" for key in entries)
        params = [*entries.values(), _now(), project_id]
        self._db.execute(
            f"UPDATE rejection_check_meta SET {assignments}, updated_at = ?"
            " WHERE project_id = ?", params)

    # ------------------------------------------------------------- documents

    def list_documents(self, project_id: str, role: str | None = None) -> list[dict]:
        if role is None:
            rows = self._db.query(
                "SELECT * FROM rejection_check_documents WHERE project_id = ?"
                " ORDER BY role ASC, sort_order ASC", (project_id,))
        else:
            rows = self._db.query(
                "SELECT * FROM rejection_check_documents WHERE project_id = ? AND role = ?"
                " ORDER BY sort_order ASC", (project_id, role))
        return [dict(row) for row in rows]

    def get_document(self, project_id: str, role_or_document_id: str,
                     document_id: str | None = None) -> dict | None:
        """对齐 `loadDocumentRow`（:251-264）的三种寻址方式。

        - 给了 `document_id` → 按 `(document_id, role)` 定位；
        - 否则 `role_or_document_id` 是 `'tender'` / `'bid'` → 该角色的第一份（按 sort_order）；
        - 否则当作 `document_id` 全局找。
        """
        if document_id:
            row = self._db.query_one(
                "SELECT * FROM rejection_check_documents WHERE project_id = ?"
                " AND document_id = ? AND role = ?",
                (project_id, str(document_id), role_or_document_id))
            return dict(row) if row else None
        value = str(role_or_document_id or "").strip()
        if value in ("tender", "bid"):
            row = self._db.query_one(
                "SELECT * FROM rejection_check_documents WHERE project_id = ? AND role = ?"
                " ORDER BY sort_order ASC LIMIT 1", (project_id, value))
            return dict(row) if row else None
        row = self._db.query_one(
            "SELECT * FROM rejection_check_documents WHERE project_id = ? AND document_id = ?",
            (project_id, value))
        return dict(row) if row else None

    def save_document(self, project_id: str, values: Mapping[str, Any]) -> str:
        """upsert（参考 :305-326 的 ON CONFLICT DO UPDATE）。返回 document_id。"""
        missing = [column for column in _DOCUMENT_COLUMNS if column not in values]
        if missing:
            raise ValueError(f"保存文档缺少列：{missing}")
        payload = {column: values[column] for column in _DOCUMENT_COLUMNS}
        columns = ", ".join(payload)
        placeholders = ", ".join("?" for _ in payload)
        updates = ", ".join(
            f"{column} = excluded.{column}" for column in _DOCUMENT_COLUMNS
            if column != "document_id")
        self._db.execute(
            f"INSERT INTO rejection_check_documents (project_id, {columns})"
            f" VALUES (?, {placeholders})"
            f" ON CONFLICT(project_id, document_id) DO UPDATE SET {updates}",
            (project_id, *payload.values()),
        )
        return str(payload["document_id"])

    def clear_documents(self, project_id: str, *, role: str | None = None) -> list[str]:
        """删行并返回被删行的 `markdown_path`（调用方据此清磁盘上的 markdown 副本）。

        **不连带清理结果**——「导入/删除文档要作废哪些结果」是 `clear_document_rows`
        的职责（参考把两件事分开：`removeMarkdownRows` 只管文件，`clearDocumentRows`
        管行 + 结果）。本方法是 T2a 的簿记原语。
        """
        if role is None:
            rows = self.list_documents(project_id)
        else:
            rows = self.list_documents(project_id, role=role)
        if not rows:
            return []
        if role is None:
            self._db.execute(
                "DELETE FROM rejection_check_documents WHERE project_id = ?", (project_id,))
        else:
            self._db.execute(
                "DELETE FROM rejection_check_documents WHERE project_id = ? AND role = ?",
                (project_id, role))
        return [str(row["markdown_path"]) for row in rows if row.get("markdown_path")]

    def resequence_bid_documents(self, project_id: str) -> None:
        """把投标文档的 `sort_order` 重排成 0..n-1（对齐 `resequenceBidDocuments` :381-387：
        按 `sort_order, imported_at` 取序，再逐行回写序号与 `updated_at`）。"""
        rows = self._db.query(
            "SELECT document_id FROM rejection_check_documents"
            " WHERE project_id = ? AND role = 'bid'"
            " ORDER BY sort_order ASC, imported_at ASC", (project_id,))
        timestamp = _now()
        for index, row in enumerate(rows):
            self._db.execute(
                "UPDATE rejection_check_documents SET sort_order = ?, updated_at = ?"
                " WHERE project_id = ? AND document_id = ?",
                (index, timestamp, project_id, row["document_id"]))

    def clear_document_rows(self, project_id: str, role: str,
                            document_id: str | None = None) -> list[dict]:
        """删文档行**并作废相应结果**（对齐 `clearDocumentRows` :422-443），返回被删的行。

        两条不对称（最易写错的地方）：

        - **tender**：删**所有** tender 行（忽略 `document_id`）→ 同时清
          `extraction` 与四项检查结果（招标内容变了，解析结果整段作废）；
        - **bid**：删指定行（或全部）→ 重排序号 → **只清四项检查结果**
          （不动 extraction：它吃的是招标内容）。
        """
        document_role = normalize_document_role(role)
        if document_role == "tender":
            rows = self.list_documents(project_id, role="tender")
            if not rows:
                return []
            self._db.execute(
                "DELETE FROM rejection_check_documents WHERE project_id = ? AND role = 'tender'",
                (project_id,))
            self.save_extraction(project_id, None)
            self.clear_check_results(project_id)
            return rows
        rows = (self.list_documents(project_id, role="bid") if not document_id
                else [row for row in self.list_documents(project_id, role="bid")
                      if row["document_id"] == str(document_id)])
        if not rows:
            return []
        if document_id:
            self._db.execute(
                "DELETE FROM rejection_check_documents"
                " WHERE project_id = ? AND role = 'bid' AND document_id = ?",
                (project_id, str(document_id)))
        else:
            self._db.execute(
                "DELETE FROM rejection_check_documents WHERE project_id = ? AND role = 'bid'",
                (project_id,))
        self.resequence_bid_documents(project_id)
        self.clear_check_results(project_id)
        return rows

    # ------------------------------------------------------------ extraction

    def save_extraction(self, project_id: str, extraction: Mapping[str, Any] | None) -> None:
        """`None` = 删除该行（参考 :553-556）；否则 upsert。

        `content` 过 `stripTripleQuoteWrapper`、`status` 归一到
        `idle/running/success/error`（参考 :565-575）。
        """
        if extraction is None:
            self._db.execute(
                "DELETE FROM rejection_check_extraction WHERE project_id = ?", (project_id,))
            return
        status = normalize_status(extraction.get("status"), SECTION_STATUSES, "idle")
        content = strip_triple_quote_wrapper(extraction.get("content") or "")
        source = extraction.get("source")
        tender_signature = extraction.get("tender_signature",
                                          extraction.get("tenderSignature"))
        error = extraction.get("error")
        updated_at = extraction.get("updated_at", extraction.get("updatedAt")) or _now()
        self._db.execute(
            "INSERT INTO rejection_check_extraction"
            " (project_id, status, content, source, tender_signature, error, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(project_id) DO UPDATE SET"
            " status = excluded.status, content = excluded.content,"
            " source = excluded.source, tender_signature = excluded.tender_signature,"
            " error = excluded.error, updated_at = excluded.updated_at",
            (project_id, status, content,
             str(source) if source else None,
             str(tender_signature) if tender_signature else None,
             str(error) if error else None,
             updated_at),
        )

    def load_extraction(self, project_id: str) -> dict:
        """缺行返回 `{status:'idle', content:''}`（参考 :578-579 的默认形状）。

        返回**参考的 camelCase 形状**（`tenderSignature` / `updatedAt`）——它就是前端
        消费的契约；`content` 在读取侧再过一次三引号剥除（参考 :584 亦然）。
        """
        row = self._db.query_one(
            "SELECT * FROM rejection_check_extraction WHERE project_id = ?", (project_id,))
        if row is None:
            return {"status": "idle", "content": ""}
        result: dict[str, Any] = {
            "status": normalize_status(row["status"], SECTION_STATUSES, "idle"),
            "content": strip_triple_quote_wrapper(row["content"] or ""),
        }
        for key, column in (("source", "source"),
                            ("tenderSignature", "tender_signature"),
                            ("error", "error"),
                            ("updatedAt", "updated_at")):
            if row[column]:
                result[key] = row[column]
        return result

    # ------------------------------------------------- results / findings（T2b-1）

    def save_result(self, project_id: str, result_type: str,
                    result: Mapping[str, Any] | None) -> None:
        """写一个检查结果（对齐 `saveResult` :590-636）。

        `None` = 清空该结果（删 findings + 删结果行）。

        其余字段是 **patch 语义**（参考的 `hasOwn` 逐字段判定）：**提供了**该键就写
        （falsy → NULL），**没提供**就保留旧值。这正是「更新进度不该把
        inputSignature 抹掉」的机制，与 `saveSubjectProfile` 的
        `identificationTarget` 是同一族语义。
        """
        if result_type not in RESULT_TYPES:
            raise ValueError(f"未知的 result_type：{result_type}")
        if result is None:
            self.clear_findings(project_id, result_type)
            self._db.execute(
                "DELETE FROM rejection_check_results"
                " WHERE project_id = ? AND result_type = ?", (project_id, result_type))
            return

        existing = self.get_result_row(project_id, result_type)
        timestamp = _now()

        def patched(key: str, column: str, *, normalize=None):
            if key in result:
                value = result.get(key)
                if normalize is not None:
                    return normalize(value)
                return str(value) if value else None
            return existing[column] if existing else None

        status = (normalize_status(result.get("status"), SECTION_STATUSES, "idle")
                  if "status" in result
                  else (existing["status"] if existing else None) or "idle")
        stats_json = (None if result.get("stats") is None
                      else json.dumps(result.get("stats"), ensure_ascii=False)) \
            if "stats" in result else (existing["stats_json"] if existing else None)
        updated_at = result.get("updatedAt") or timestamp
        self._db.execute(
            "INSERT INTO rejection_check_results"
            " (project_id, result_type, status, input_signature, active_finding_id,"
            "  progress_message, error, stats_json, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(project_id, result_type) DO UPDATE SET"
            "  status = excluded.status,"
            "  input_signature = excluded.input_signature,"
            "  active_finding_id = excluded.active_finding_id,"
            "  progress_message = excluded.progress_message,"
            "  error = excluded.error,"
            "  stats_json = excluded.stats_json,"
            "  updated_at = excluded.updated_at",
            (project_id, result_type, status,
             patched("inputSignature", "input_signature"),
             patched("activeFindingId", "active_finding_id"),
             patched("progressMessage", "progress_message"),
             patched("error", "error"),
             stats_json, updated_at),
        )
        if "findings" in result:
            self.clear_findings(project_id, result_type)
            self.save_findings(project_id, result_type, result.get("findings") or [])

    def get_result_row(self, project_id: str, result_type: str) -> dict | None:
        row = self._db.query_one(
            "SELECT * FROM rejection_check_results WHERE project_id = ? AND result_type = ?",
            (project_id, result_type))
        return dict(row) if row else None

    def clear_findings(self, project_id: str, result_type: str) -> None:
        table = FINDING_TABLES[result_type]
        self._db.execute(
            f"DELETE FROM {table} WHERE project_id = ?", (project_id,))

    def save_findings(self, project_id: str, result_type: str,
                      findings: Sequence[Mapping[str, Any]]) -> int:
        """整批插入（调用方先 `clear_findings`）。行归一在 domain 层。"""
        rows = findings_values(result_type, findings, _now())
        if not rows:
            return 0
        table = FINDING_TABLES[result_type]
        columns = list(rows[0])
        placeholders = ", ".join("?" for _ in columns)
        self._db.executemany(
            f"INSERT INTO {table} (project_id, {', '.join(columns)})"
            f" VALUES (?, {placeholders})",
            [(project_id, *[row[column] for column in columns]) for row in rows],
        )
        return len(rows)

    def load_findings(self, project_id: str, result_type: str) -> list[dict]:
        table = FINDING_TABLES[result_type]
        rows = self._db.query(
            f"SELECT * FROM {table} WHERE project_id = ? ORDER BY sort_order ASC",
            (project_id,))
        first_bid = self.get_document(project_id, "bid")
        fallback = str(first_bid["document_id"]) if first_bid else ""
        return findings_view(result_type, [dict(row) for row in rows], fallback)

    def load_result(self, project_id: str, result_type: str) -> dict:
        """参考形状（`loadResult` :746-765）：缺行 → `{status:'idle', findings:[]}`。

        可选键只在**有值**时出现（参考用 `|| undefined`），故 Python 侧省略键。
        """
        base: dict[str, Any] = {
            "status": "idle",
            "findings": self.load_findings(project_id, result_type),
        }
        row = self.get_result_row(project_id, result_type)
        if row is None:
            return base
        base["status"] = normalize_status(row["status"], SECTION_STATUSES, "idle")
        for key, column in (("inputSignature", "input_signature"),
                            ("activeFindingId", "active_finding_id"),
                            ("progressMessage", "progress_message"),
                            ("error", "error"),
                            ("updatedAt", "updated_at")):
            if row[column]:
                base[key] = row[column]
        if row["stats_json"]:
            try:
                base["stats"] = json.loads(row["stats_json"])
            except (TypeError, ValueError):
                pass  # 参考 safeJsonParse 的 fallback=undefined
        return base

    def clear_check_results(self, project_id: str) -> None:
        """清四个结果（对齐 `clearCheckResults` :822-829 的库内部分）。"""
        self._db.execute(
            "DELETE FROM rejection_check_results WHERE project_id = ?", (project_id,))
        for result_type in RESULT_TYPES:
            self.clear_findings(project_id, result_type)

    # ------------------------------------------ 投标主体档案 / 视觉资产（T2b-2）

    def load_subject_profile(self, project_id: str) -> dict:
        row = self._db.query_one(
            "SELECT * FROM rejection_check_subject_profile WHERE project_id = ?",
            (project_id,))
        return subject_profile_view(dict(row) if row else None)

    def save_subject_profile(self, project_id: str,
                             profile: Mapping[str, Any]) -> dict:
        """写档案（对齐 `saveSubjectProfile` :1263-1297）。

        **未携带 `identificationTarget` 键时保留旧值**——参考 :1264-1268 的注释写明
        目的是「避免任务 checkpoint 把它冲掉」；显式传 `None` 才清空
        （`reset_subject_profile` 就是这么干的）。该字段存在 `candidates_json` 里。
        """
        target = (profile.get("identificationTarget")
                  if "identificationTarget" in profile
                  else self.load_subject_profile(project_id).get("identificationTarget"))
        values = subject_profile_values(profile, target, _now())
        columns = list(values)
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(f"{column} = excluded.{column}" for column in columns)
        self._db.execute(
            f"INSERT INTO rejection_check_subject_profile (project_id, {', '.join(columns)})"
            f" VALUES (?, {placeholders})"
            f" ON CONFLICT(project_id) DO UPDATE SET {updates}",
            (project_id, *values.values()),
        )
        return self.load_subject_profile(project_id)

    def reset_subject_profile(self, project_id: str, reason: str | None = None) -> dict:
        """对齐 `resetSubjectProfile` :1298-1310：清到 idle 并显式清掉识别范围。"""
        return self.save_subject_profile(project_id, {
            "status": "idle",
            "officialName": "",
            "aliases": [],
            "candidates": [],
            "identificationTarget": None,
            "error": reason,
        })

    def delete_subject_finding(self, project_id: str, finding_id: str) -> bool:
        """对齐 `deleteSubjectFinding` :1406-1409（返回是否真的删到了行）。"""
        cursor = self._db.execute(
            "DELETE FROM rejection_check_subject_findings"
            " WHERE project_id = ? AND finding_id = ?", (project_id, str(finding_id or "")))
        return cursor.rowcount > 0

    def list_visual_assets(self, project_id: str,
                           bid_document_id: str | None = None) -> list[dict]:
        if bid_document_id:
            rows = self._db.query(
                "SELECT * FROM rejection_check_visual_assets WHERE project_id = ?"
                " AND bid_document_id = ? ORDER BY sort_order ASC",
                (project_id, str(bid_document_id)))
        else:
            rows = self._db.query(
                "SELECT * FROM rejection_check_visual_assets WHERE project_id = ?"
                " ORDER BY bid_document_id ASC, sort_order ASC", (project_id,))
        return visual_assets_view([dict(row) for row in rows])

    def save_visual_assets(self, project_id: str, bid_document_id: str,
                           assets: Sequence[Mapping[str, Any]] | None = None) -> int:
        """**按投标文件整批替换**（对齐 `saveVisualAssets` :1368-1405 的先删后插）。"""
        document_id = str(bid_document_id or "")
        self.clear_visual_assets(project_id, document_id)
        rows = visual_assets_values(document_id, assets, _now())
        if not rows:
            return 0
        columns = list(rows[0])
        placeholders = ", ".join("?" for _ in columns)
        self._db.executemany(
            "INSERT INTO rejection_check_visual_assets (project_id,"
            f" {', '.join(columns)}) VALUES (?, {placeholders})",
            [(project_id, *[row[column] for column in columns]) for row in rows],
        )
        return len(rows)

    def clear_visual_assets(self, project_id: str,
                            bid_document_id: str | None = None) -> None:
        if bid_document_id:
            self._db.execute(
                "DELETE FROM rejection_check_visual_assets"
                " WHERE project_id = ? AND bid_document_id = ?",
                (project_id, str(bid_document_id)))
            return
        self._db.execute(
            "DELETE FROM rejection_check_visual_assets WHERE project_id = ?", (project_id,))
