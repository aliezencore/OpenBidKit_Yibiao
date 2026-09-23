"""废标项检查「文档」的路径布局与行视图（纯函数）。

路径对齐参考 `getDocumentMarkdownRelativePath`（rejectionCheckStore.cjs:233-244）：
`markdown_path` 存**相对 data_root** 的路径，落盘时再拼 `projects/<pid>/`。

| role | 相对路径 |
| --- | --- |
| bid | `rejection-check/bids/<safeDocumentId>.md` |
| tender 主文档（id 为常量 `tender`） | `rejection-check/tender.md` |
| tender 源文档 | `rejection-check/tenders/<safeDocumentId>.md` |

**有意偏离（P4a T3 登记）**：参考的 `createPreparedDocument` :273-303 给文件名加
`<timestamp>-<uuid8>`（每次导入写新文件、旧文件随后回收）。本实现用上面的**规范路径**：
文档 id 是内容派生的（`bid-<sha16>` / `tender-<nn>-<sha12>`），同内容重复导入 upsert 到
同一路径，不产生孤儿文件，P4e 迁移导入也能直接按路径复制。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from app.domain.rejection.normalize import TENDER_DOCUMENT_ID, normalize_document_role

_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_-]")


def safe_document_id(value: Any) -> str:
    """对齐参考的 `String(x).replace(/[^a-zA-Z0-9_-]/g, '_')`。"""
    return _SAFE_ID_RE.sub("_", str(value or ""))


def document_markdown_path(role: Any, document_id: Any) -> str:
    document_role = normalize_document_role(role)
    if document_role == "bid":
        return f"rejection-check/bids/{safe_document_id(document_id or 'bid')}.md"
    raw_id = str(document_id or "")
    if raw_id and raw_id != TENDER_DOCUMENT_ID:
        return f"rejection-check/tenders/{safe_document_id(raw_id)}.md"
    return "rejection-check/tender.md"


def document_view(row: Mapping[str, Any]) -> dict[str, Any]:
    """列表/响应里的文档行（**不含 markdown 正文**——正文可能很大，且前端列表只展示元信息）。

    与参考 `documentFromRow`（:367-380）的差别：少了 `content`（登记为有意省略），
    多了 `contentHash` / `contentChars` / `markdownPath` / `sortOrder`，供前端展示与排错。
    """
    return {
        "id": row.get("document_id") or normalize_document_role(row.get("role")),
        "role": normalize_document_role(row.get("role")),
        "fileName": row.get("file_name"),
        "source": "technical-plan" if row.get("source") == "technical-plan" else "upload",
        "parserLabel": row.get("parser_label") or None,
        "contentHash": row.get("content_hash"),
        "contentChars": int(row.get("content_chars") or 0),
        "markdownPath": row.get("markdown_path"),
        "sortOrder": int(row.get("sort_order") or 0),
        "importedAt": row.get("imported_at"),
        "updatedAt": row.get("updated_at"),
    }


def documents_view(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [document_view(row) for row in rows]
