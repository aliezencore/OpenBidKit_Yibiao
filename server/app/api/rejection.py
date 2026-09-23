"""废标项检查的文档簿记 API（P4a T3）。

导入语义逐条对齐参考 `rejectionCheckStore.cjs` 的 `importDocument` :949-1030 /
`clearDocumentRows` :422-443：

- **招标文档是「主文档 + 源文档」双层**：主文档行 `document_id = 'tender'`、
  `sort_order = 0`、内容是源文档们的合并（`combineTenderMarkdown`）；源文档才是用户
  导入的每一份（`tender-<两位序号>-<sha12>`，`sort_order` 从 **1** 起）。
  `loadTenderSourceDocuments`（:361-366）明确排除主文档——解析步骤吃主文档。
- **去重键** `f"{file_name}\\u0000{sha256(markdown)}"`（:978-980 / :1007-1011），
  只在同角色已有行之间判重，命中即跳过。
- **tender 导入是整体重写**（读现有源文档 → 合并新增 → 先清后插）；bid 导入是追加
  （`sort_order` 从现有行数递增）。

与参考的两处差异（均已登记）：
1. markdown 走**规范路径**（`rejection-check/{bids,tenders}/<id>.md`、主文档
   `rejection-check/tender.md`）而非 `<ts>-<uuid8>` 命名；
2. 列表响应**不含 markdown 正文**（只给元信息）——前端展示与排错够用，正文很大。
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.deps import AppState, get_state
from app.domain.rejection.documents import documents_view, document_markdown_path
from app.domain.rejection.normalize import (
    TENDER_DOCUMENT_ID,
    combine_tender_markdown,
    create_bid_document_id,
    create_tender_source_document_id,
    normalize_document_role,
    stable_hash,
)
from app.domain.shared.errors import ParsingError
from app.infra.parsing.service import save_uploaded_document

router = APIRouter()
_logger = logging.getLogger(__name__)


def _dedupe_key(file_name: str, markdown: str) -> str:
    """对齐参考的 ``${row.file_name}\\u0000${row.content_hash}``。"""
    return f"{file_name}\u0000{stable_hash(markdown)}"


def _markdown_target(data_root: Path, project_id: str, relative_path: str) -> Path:
    """相对路径 → 盘上绝对路径（`projects/<pid>/<relative>`，与 P3 的布局同族）。"""
    return Path(data_root) / "projects" / project_id / Path(*relative_path.split("/"))


def _project_documents(state: AppState, project_id: str) -> dict:
    return {
        "tender": documents_view(state.rejection.list_documents(project_id, role="tender")),
        "bid": documents_view(state.rejection.list_documents(project_id, role="bid")),
    }


async def _parse_uploads(uploads: list[UploadFile], *, project_id: str, data_root: Path,
                         provider: str, mineru_token: str) -> list[dict]:
    """逐文件解析（复用 P2a 的 `save_uploaded_document`），返回
    `{file_name, markdown, parser_label}`；单文件失败只记日志并跳过（对齐参考逐文件容错）。"""
    parsed: list[dict] = []
    for upload in uploads:
        file_name = upload.filename or "未命名文件"
        payload = await upload.read()
        if not payload:
            continue
        tmp_dir = tempfile.mkdtemp(prefix="yibiao-rejection-")
        tmp_path = Path(tmp_dir) / file_name
        try:
            tmp_path.write_bytes(payload)
            saved = await asyncio.to_thread(
                save_uploaded_document,
                data_root=data_root, project_id=project_id,
                source_path=tmp_path, role="rejection", provider=provider,
                mineru_token=mineru_token)
            parsed.append({
                "file_name": file_name,
                "markdown": Path(saved.markdown_path).read_text(encoding="utf-8").strip(),
                "parser_label": saved.parser_label,
            })
        except ParsingError as error:
            _logger.warning("废标项文档解析失败：name=%s code=%s message=%s",
                            file_name, error.code, error)
        finally:
            for path in sorted(Path(tmp_dir).rglob("*"), reverse=True):
                try:
                    path.unlink() if path.is_file() else path.rmdir()
                except OSError:
                    pass
    return parsed


def _write_markdown(data_root: Path, project_id: str, relative_path: str,
                    markdown: str) -> None:
    target = _markdown_target(data_root, project_id, relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{markdown}\n", encoding="utf-8", newline="\n")


def _document_values(*, project_id: str, document_id: str, role: str, file_name: str,
                     markdown: str, parser_label: str | None, sort_order: int,
                     imported_at: str, relative_path: str) -> dict:
    return {
        "document_id": document_id,
        "role": role,
        "source": "upload",
        "file_name": file_name,
        "markdown_path": relative_path,
        "content_hash": stable_hash(markdown),
        "content_chars": len(markdown),
        "parser_label": parser_label or None,
        "sort_order": sort_order,
        "imported_at": imported_at,
        "updated_at": imported_at,
    }


@router.post("/projects/{project_id}/rejection/documents")
async def upload_documents(
    project_id: str,
    role: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    state: AppState = Depends(get_state),
) -> dict:
    """导入一篇/多篇文档（`role=tender|bid`）。返回导入后的两组文档列表。"""
    document_role = normalize_document_role(role)
    if not files:
        raise HTTPException(status_code=400, detail="未导入文件")

    data_root = Path(state.settings.data_root)
    parsed = await _parse_uploads(
        files, project_id=project_id, data_root=data_root,
        provider=state.settings.file_parser_provider,
        mineru_token=state.settings.mineru_token)
    if not parsed:
        raise HTTPException(status_code=400, detail="未导入文件")

    imported_at = _now()
    existing = state.rejection.list_documents(project_id, role=document_role)
    seen = {_dedupe_key(row["file_name"], _row_markdown(data_root, project_id, row))
            for row in existing}

    if document_role == "bid":
        sort_order = len(existing)
        for item in parsed:
            key = _dedupe_key(item["file_name"], item["markdown"])
            if key in seen:
                continue
            seen.add(key)
            document_id = create_bid_document_id(item["file_name"], item["markdown"])
            relative_path = document_markdown_path("bid", document_id)
            _write_markdown(data_root, project_id, relative_path, item["markdown"])
            state.rejection.save_document(project_id, _document_values(
                project_id=project_id, document_id=document_id, role="bid",
                file_name=item["file_name"], markdown=item["markdown"],
                parser_label=item["parser_label"], sort_order=sort_order,
                imported_at=imported_at, relative_path=relative_path))
            sort_order += 1
    else:
        # 招标：读现有**源文档**（排除主文档）→ 合并新增 → 整体重写
        source_rows = [row for row in existing
                       if row["document_id"] != TENDER_DOCUMENT_ID]
        sources = [{"id": row["document_id"], "role": "tender",
                    "fileName": row["file_name"], "source": row["source"],
                    "parserLabel": row["parser_label"],
                    "content": _row_markdown(data_root, project_id, row)}
                   for row in source_rows]
        added: list[dict] = []
        for item in parsed:
            key = _dedupe_key(item["file_name"], item["markdown"])
            if key in seen:
                continue
            seen.add(key)
            added.append({
                "id": create_tender_source_document_id(
                    item["file_name"], item["markdown"], len(sources) + len(added)),
                "role": "tender", "fileName": item["file_name"], "source": "upload",
                "parserLabel": item["parser_label"], "content": item["markdown"],
            })
        if added:
            state.rejection.clear_documents(project_id, role="tender")
            all_sources = [*sources, *added]
            # 主文档（id 常量 'tender'）：多份时文件名是「N 份招标文件」、label 置空
            main_markdown = combine_tender_markdown(all_sources)
            main_document_id = TENDER_DOCUMENT_ID
            main_name = (f"{len(all_sources)} 份招标文件" if len(all_sources) > 1
                         else (all_sources[0]["fileName"] if all_sources else "招标文件"))
            main_label = (None if len(all_sources) > 1
                          else all_sources[0].get("parserLabel"))
            main_path = document_markdown_path("tender", main_document_id)
            _write_markdown(data_root, project_id, main_path, main_markdown)
            state.rejection.save_document(project_id, _document_values(
                project_id=project_id, document_id=main_document_id, role="tender",
                file_name=main_name, markdown=main_markdown, parser_label=main_label,
                sort_order=0, imported_at=imported_at, relative_path=main_path))
            for index, source in enumerate(all_sources):
                relative_path = document_markdown_path("tender", source["id"])
                _write_markdown(data_root, project_id, relative_path, source["content"])
                state.rejection.save_document(project_id, _document_values(
                    project_id=project_id, document_id=source["id"], role="tender",
                    file_name=source["fileName"], markdown=source["content"],
                    parser_label=source.get("parserLabel"), sort_order=index + 1,
                    imported_at=imported_at, relative_path=relative_path))
            # 招标内容变了 → 解析结果与四项检查都作废（对齐 clearDocumentRows 的 tender 分支）
            state.rejection.save_extraction(project_id, None)
            state.rejection.clear_check_results(project_id)

    return _project_documents(state, project_id)


@router.get("/projects/{project_id}/rejection/documents")
def list_documents(project_id: str, state: AppState = Depends(get_state)) -> dict:
    return _project_documents(state, project_id)


@router.delete("/projects/{project_id}/rejection/documents")
def delete_documents(project_id: str, role: str, documentId: str | None = None,
                     state: AppState = Depends(get_state)) -> dict:
    """删文档（`role=tender` 删全部招标并作废解析；`role=bid` 可只删一份）。"""
    removed = state.rejection.clear_document_rows(project_id, role, documentId)
    data_root = Path(state.settings.data_root)
    for row in removed:
        target = _markdown_target(data_root, project_id, str(row.get("markdown_path") or ""))
        try:
            target.unlink(missing_ok=True)
        except OSError as error:  # best-effort：文件清理失败不影响响应（对齐参考 :408-421）
            _logger.warning("清理废标项 markdown 失败：%s（%s）", target, error)
    return _project_documents(state, project_id)


def _row_markdown(data_root: Path, project_id: str, row: dict) -> str:
    """读行对应的 markdown（去重判据与招标合并都要用）。缺失即空串。"""
    relative = str(row.get("markdown_path") or "")
    if not relative:
        return ""
    target = _markdown_target(data_root, project_id, relative)
    try:
        return target.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
