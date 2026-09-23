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
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.deps import AppState, get_state
from app.domain.rejection.documents import documents_view, document_markdown_path
from app.domain.rejection.normalize import (
    DEFAULT_CHECK_OPTIONS,
    TENDER_DOCUMENT_ID,
    combine_tender_markdown,
    create_bid_document_id,
    create_bid_signature,
    create_tender_source_document_id,
    normalize_check_options,
    normalize_check_result_tab,
    normalize_document_role,
    normalize_document_tab,
    normalize_result_tab,
    normalize_status,
    normalize_step,
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


# ------------------------------------------------------------- 状态与 UI 态（T4）

def _documents_payload(data_root: Path, project_id: str,
                       state: AppState) -> dict[str, Any]:
    """状态里的文档部分：主文档 / 招标源文档 / 投标文档（都是完整视图，含 markdown? 否）。

    `documentFromRow`（参考 :367-380）带 `content`；本实现的状态响应**不带正文**
    （与列表端点一致，登记为有意省略）。`tenderDocument` 是主文档或第一份招标。
    """
    rows = state.rejection.list_documents(project_id)
    tender_rows = [row for row in rows if row["role"] == "tender"]
    bid_rows = [row for row in rows if row["role"] == "bid"]
    main_row = next((row for row in tender_rows
                     if row["document_id"] == TENDER_DOCUMENT_ID),
                    tender_rows[0] if tender_rows else None)
    return {
        "tenderDocument": documents_view([main_row])[0] if main_row else None,
        "tenderDocuments": documents_view(
            [row for row in tender_rows if row["document_id"] != TENDER_DOCUMENT_ID]),
        "bidDocuments": documents_view(bid_rows),
    }


def _valid_active_document_tab(meta_tab: str, payload: dict) -> str:
    """参考 :876-886：tab 必须是 'tender'、某个源招标文档 id 或某个投标文档 id；
    否则回落到「有主文档 → 'tender'，否则第一份投标文档 id，否则 'tender'」。"""
    candidate = normalize_document_tab(meta_tab)
    allowed = {"tender"}
    allowed.update(row["id"] for row in payload["tenderDocuments"])
    allowed.update(row["id"] for row in payload["bidDocuments"])
    if candidate in allowed:
        return candidate
    if payload["tenderDocument"]:
        return "tender"
    return payload["bidDocuments"][0]["id"] if payload["bidDocuments"] else "tender"


def _build_state(data_root: Path, project_id: str, state: AppState) -> dict[str, Any]:
    repo = state.rejection
    meta = repo.ensure_meta(project_id)
    documents = _documents_payload(data_root, project_id, state)
    tabs = {
        "activeDocumentTab": _valid_active_document_tab(meta["active_document_tab"], documents),
        "step": normalize_step(meta["step"]),
        "activeResultTab": normalize_result_tab(meta["active_result_tab"]),
        "activeCheckResultTab": normalize_check_result_tab(meta["active_check_result_tab"]),
    }
    check_options = DEFAULT_CHECK_OPTIONS
    if meta.get("check_options_json"):
        try:
            check_options = normalize_check_options(json.loads(meta["check_options_json"]))
        except (TypeError, ValueError):
            check_options = DEFAULT_CHECK_OPTIONS
    return {
        **documents,
        **tabs,
        "invalidBidAndRejectionItems": repo.load_extraction(project_id),
        "customCheckItems": meta["custom_check_items"] or "",
        "checkOptions": check_options,
        "rejectionCheckResult": repo.load_result(project_id, "rejection"),
        "typoCheckResult": repo.load_result(project_id, "typo"),
        "logicCheckResult": repo.load_result(project_id, "logic"),
        "subjectCheckResult": repo.load_result(project_id, "subject"),
        "subjectProfile": repo.load_subject_profile(project_id),
        "tasks": _tasks_payload(project_id, state),
    }


@router.get("/projects/{project_id}/rejection/state")
def get_state_endpoint(project_id: str, state: AppState = Depends(get_state)) -> dict:
    """废标项检查的完整状态（对齐 `loadRejectionCheck` :863-901）。

    函数名刻意叫 `..._endpoint`：**不能叫 `get_state`**——那会遮蔽从 `deps` 导入的
    依赖，后面所有 `Depends(get_state)` 都会解析到这个端点函数本身
    （`duplicate.py` 的 `get_state_endpoint` 同因）。
    """
    return _build_state(Path(state.settings.data_root), project_id, state)


class UiStatePatch(BaseModel):
    """UI 态补丁：**只写显式传入的字段**（对齐 `saveUiState` :1411-1418 的 `hasOwn` 过滤）。"""

    step: str | None = None
    activeDocumentTab: str | None = None
    activeResultTab: str | None = None
    activeCheckResultTab: str | None = None
    customCheckItems: str | None = None
    checkOptions: dict[str, Any] | None = None


@router.put("/projects/{project_id}/rejection/ui")
def save_ui_state(project_id: str, body: UiStatePatch,
                  state: AppState = Depends(get_state)) -> dict:
    """保存 UI 态（步进/页签/自定义检查项/检查开关），返回新状态。"""
    fields: dict[str, Any] = {}
    if body.step is not None:
        fields["step"] = normalize_step(body.step)
    if body.activeDocumentTab is not None:
        fields["active_document_tab"] = normalize_document_tab(body.activeDocumentTab)
    if body.activeResultTab is not None:
        fields["active_result_tab"] = normalize_result_tab(body.activeResultTab)
    if body.activeCheckResultTab is not None:
        fields["active_check_result_tab"] = normalize_check_result_tab(
            body.activeCheckResultTab)
    if body.customCheckItems is not None:
        fields["custom_check_items"] = str(body.customCheckItems)
    if body.checkOptions is not None:
        fields["check_options_json"] = json.dumps(
            normalize_check_options(body.checkOptions), ensure_ascii=False)
    state.rejection.update_meta(project_id, fields)
    return _build_state(Path(state.settings.data_root), project_id, state)


class ConfirmSubjectRequest(BaseModel):
    officialName: str = ""
    aliases: list[str] | None = None
    identificationTarget: dict[str, Any] | None = None


@router.post("/projects/{project_id}/rejection/subject/confirm")
def confirm_subject(project_id: str, body: ConfirmSubjectRequest,
                    state: AppState = Depends(get_state)) -> dict:
    """确认投标主体（对齐 `confirmSubject` :1320-1344）。

    三件事：写档案（status=confirmed + **当前投标签名** + confirmedAt）→
    **作废主体检查结果**（改了正式名称或简称，旧结果不再有效，spec §11.4）。
    `identificationTarget` 走「未携带 = 保留旧值」（用 `model_fields_set` 判是否携带）。
    """
    name = (body.officialName or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="请填写正式投标主体名称")
    aliases = [item.strip() for item in (body.aliases or []) if str(item or "").strip()]
    repo = state.rejection
    data_root = Path(state.settings.data_root)
    previous = repo.load_subject_profile(project_id)
    patch: dict[str, Any] = {
        "status": "confirmed",
        "officialName": name,
        "aliases": aliases,
        "candidates": previous.get("candidates") or [],
        "preselectIndex": previous.get("preselectIndex"),
        "bidSignature": _current_bid_signature(data_root, project_id, state),
        "confirmedAt": _now(),
    }
    if "identificationTarget" in body.model_fields_set:
        patch["identificationTarget"] = body.identificationTarget
    profile = repo.save_subject_profile(project_id, patch)
    # 主体结果整段作废（参考 :1341-1343）
    repo.save_result(project_id, "subject", {
        "status": "idle", "findings": [], "inputSignature": None,
        "activeFindingId": None, "progressMessage": None, "error": None,
        "stats": None, "updatedAt": _now()})
    return {"success": True, "message": "投标主体已确认", "profile": profile}


def _current_bid_signature(data_root: Path, project_id: str, state: AppState) -> str:
    """`getCurrentBidSignature`（:1308-1312）：按 sort_order, imported_at 取投标文档，
    逐份算文档签名再连接。需要正文，故在此读盘。"""
    rows = state.rejection.list_documents(project_id, role="bid")
    documents = [{"id": row["document_id"], "role": "bid", "source": row["source"],
                  "fileName": row["file_name"],
                  "content": _row_markdown(data_root, project_id, row)}
                 for row in rows]
    return create_bid_signature(documents)


def _tasks_payload(project_id: str, state: AppState) -> dict[str, Any]:
    """三类任务的当前态（对齐参考的 `loadTasks` :543-551 → `taskFromRow` :166-179）。

    任务统一存在 `tasks` 表（spec §5.1），按 kind 过滤本项目的最新一条。
    """
    rows = state.tasks.list_for_project(project_id)
    payload: dict[str, Any] = {}
    for kind, field in (("rejection-items-extraction", "extractionTask"),
                        ("rejection-check-run", "checkTask"),
                        ("bid-subject-identification", "subjectIdentificationTask")):
        row = next((item for item in rows if item["kind"] == kind), None)
        if row is None:
            continue
        payload[field] = {
            "task_id": row["task_id"],
            "type": kind,
            "status": normalize_status(row["status"], ("running", "success", "error"),
                                       "running"),
            "progress": int(row["progress"] or 0),
            "started_at": row["started_at"],
            "updated_at": row["finished_at"] or row["started_at"],
        }
        if row.get("error"):
            payload[field]["error"] = row["error"]
    return payload
