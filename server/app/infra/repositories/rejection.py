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
from typing import Any, Mapping

from app.domain.rejection.normalize import (
    DEFAULT_CHECK_OPTIONS,
    SECTION_STATUSES,
    normalize_status,
    strip_triple_quote_wrapper,
)
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
        """删行并返回被删行的 `markdown_path`（对齐 `clearDocumentRows` :422-443：
        调用方据此清磁盘上的 markdown 副本）。"""
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
