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
from typing import Any, Mapping

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
    create_rejection_check_input_signature,
    create_tender_source_document_id,
    normalize_check_options,
    normalize_check_result_tab,
    normalize_document_role,
    normalize_document_tab,
    normalize_result_tab,
    create_subject_check_input_signature,
    normalize_status,
    normalize_step,
    stable_hash,
)
from app.domain.rejection.docx_visual_assets import (
    extract_docx_visual_assets,
    is_docx_file_path,
    write_visual_media_files,
)
from app.domain.shared.errors import ParsingError
from app.infra.parsing.service import save_uploaded_document
from app.infra.tasks.rejection_extraction import RejectionExtractionRunner
from app.infra.tasks.rejection_checks import RejectionCheckRunner
from app.infra.tasks.rejection_stub import (
    StubSubjectIdentificationRunner,
)

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
            await asyncio.to_thread(tmp_path.write_bytes, payload)
            saved = await asyncio.to_thread(
                save_uploaded_document,
                data_root=data_root, project_id=project_id,
                source_path=tmp_path, role="rejection", provider=provider,
                mineru_token=mineru_token)
            markdown = await asyncio.to_thread(
                Path(saved.markdown_path).read_text, encoding="utf-8")
            parsed.append({
                "file_name": file_name,
                "markdown": markdown.strip(),
                "parser_label": saved.parser_label,
                "source_path": saved.source_path,
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
        added_bid_ids: list[str] = []
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
            added_bid_ids.append(document_id)
            # 主体检查视觉材料：DOCX 提取图片 → subject-media/<docId>/<hash>/<hash>.<ext>
            # → visual_assets 行（id={docId}-asset-{N}）。单份 DOCX 媒体损坏不阻塞导入（spec §13）。
            if is_docx_file_path(item.get("source_path")):
                try:
                    extracted = extract_docx_visual_assets(item["source_path"])
                    media_dir = (data_root / "projects" / project_id
                                 / "rejection-check" / "subject-media" / document_id)
                    relative_by_name = write_visual_media_files(
                        media_dir, extracted["mediaFiles"])
                    hash_by_name = {media["mediaName"]: media
                                    for media in extracted["mediaFiles"]}
                    rows = []
                    for index, occurrence in enumerate(extracted["occurrences"]):
                        media = hash_by_name.get(occurrence["mediaName"]) or {}
                        rows.append({
                            "id": f"{document_id}-asset-{index + 1}",
                            "mediaName": occurrence["mediaName"],
                            "contentHash": media.get("contentHash", ""),
                            "relativePath": relative_by_name.get(
                                occurrence["mediaName"], ""),
                            "mimeType": media.get("mimeType",
                                                  "application/octet-stream"),
                            "width": occurrence["width"],
                            "height": occurrence["height"],
                            "placement": occurrence["placement"],
                            "assetKind": occurrence["assetKind"],
                            "locationHint": occurrence["locationHint"],
                            "nearbyText": occurrence["nearbyText"],
                            "sectionTitle": occurrence.get("sectionTitle"),
                        })
                    rows = [row for row in rows
                            if row["relativePath"] and row["contentHash"]]
                    if rows:
                        state.rejection.save_visual_assets(project_id, document_id,
                                                           rows)
                except Exception as asset_error:
                    _logger.warning("废标项视觉资产提取失败：%s（%s）",
                                    item["file_name"], asset_error)
            sort_order += 1
        if added_bid_ids:
            # 参考 :1076-1080：新增投标文档 → 作废四项检查结果 + 重置主体档案（无 reason）
            # + 把页签切到第一份新文档。**不重置 extraction**（它吃招标内容）。
            # 参考把它们包在 `db.transaction`（:1071-1080）；本实现同样包一层
            # （评审 B 的 P1：多步写不原子会留下「行已存、结果未作废」的半成品）。
            with state.db.transaction():
                state.rejection.clear_check_results(project_id)
                state.rejection.reset_subject_profile(project_id)
                state.rejection.update_meta(project_id,
                                            {"active_document_tab": added_bid_ids[0]})
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
            all_sources = [*sources, *added]
            # 主文档（id 常量 'tender'）：多份时文件名是「N 份招标文件」、label 置空
            main_markdown = combine_tender_markdown(all_sources)
            main_document_id = TENDER_DOCUMENT_ID
            main_name = (f"{len(all_sources)} 份招标文件" if len(all_sources) > 1
                         else (all_sources[0]["fileName"] if all_sources else "招标文件"))
            main_label = (None if len(all_sources) > 1
                          else all_sources[0].get("parserLabel"))
            main_path = document_markdown_path("tender", main_document_id)
            # 文件先落盘：文件系统不可回滚——DB 失败时盘上可能留孤儿副本（与参考同局限，登记）
            _write_markdown(data_root, project_id, main_path, main_markdown)
            for source in all_sources:
                _write_markdown(data_root, project_id,
                                document_markdown_path("tender", source["id"]),
                                source["content"])
            # 「清行 → 重插主文档与源文档 → 作废解析与结果」必须**原子**：参考把它整段包在
            # `db.transaction`（:1073-1080）；不包则中途失败会留下「行已删、文档没插回」的空项目
            # （评审 B 的 P1）。
            with state.db.transaction():
                state.rejection.clear_documents(project_id, role="tender")
                state.rejection.save_document(project_id, _document_values(
                    project_id=project_id, document_id=main_document_id, role="tender",
                    file_name=main_name, markdown=main_markdown, parser_label=main_label,
                    sort_order=0, imported_at=imported_at, relative_path=main_path))
                for index, source in enumerate(all_sources):
                    state.rejection.save_document(project_id, _document_values(
                        project_id=project_id, document_id=source["id"], role="tender",
                        file_name=source["fileName"], markdown=source["content"],
                        parser_label=source.get("parserLabel"), sort_order=index + 1,
                        imported_at=imported_at,
                        relative_path=document_markdown_path("tender", source["id"])))
                # 招标内容变了 → 解析结果与四项检查都作废（对齐 clearDocumentRows 的 tender 分支）
                state.rejection.save_extraction(project_id, None)
                state.rejection.clear_check_results(project_id)

    return _project_documents(state, project_id)


@router.get("/projects/{project_id}/rejection/documents")
def list_documents(project_id: str, state: AppState = Depends(get_state)) -> dict:
    return _project_documents(state, project_id)


def _apply_removal_side_effects(state: AppState, project_id: str, document_role: str,
                                document_id: str | None) -> None:
    """删文档后的连带副作用（参考 :1208-1231 / :1210），调用方负责包事务。

    - **投标**：清该文档（或全部）视觉资产 + **重置主体档案**（带 reason）+ 页签切到下一份
      投标（没有则 `'tender'`）；
    - **招标**：只把页签切回 `'tender'`——**不**重置主体档案（它依赖投标签名）。
    """
    if document_role == "bid":
        if document_id:
            state.rejection.clear_visual_assets(project_id, str(document_id))
        else:
            state.rejection.clear_visual_assets(project_id)
        state.rejection.reset_subject_profile(project_id, "投标文件已更新，请重新确认投标主体")
        remaining = state.rejection.list_documents(project_id, role="bid")
        state.rejection.update_meta(project_id, {
            "active_document_tab": remaining[0]["document_id"] if remaining else "tender"})
    else:
        state.rejection.update_meta(project_id, {"active_document_tab": "tender"})


@router.delete("/projects/{project_id}/rejection/documents")
def delete_documents(project_id: str, role: str, documentId: str | None = None,
                     state: AppState = Depends(get_state)) -> dict:
    """删文档（`role=tender` 删全部招标并作废解析；`role=bid` 可只删一份）。"""
    document_role = normalize_document_role(role)
    # 参考 :1204-1213 把「删行 + 切页签 + 清视觉资产 + 重置档案」整段包在 `db.transaction`；
    # 不包则中途失败会留下「行删了、结果/档案没清」的半成品（评审 B 的 P1）。
    with state.db.transaction():
        removed = state.rejection.clear_document_rows(project_id, role, documentId)
        _apply_removal_side_effects(state, project_id, document_role, documentId)
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
        # 参考 :897 把三类任务**平铺**在状态顶层（extractionTask/checkTask/
        # subjectIdentificationTask），不是嵌在 "tasks" 键下。
        **_tasks_payload(project_id, state),
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


# --------------------------------------------------- 任务提交（T5，占位算法）

ANALYSIS_DOMAIN = "rejection"
EXTRACTION_KIND = "rejection-items-extraction"
IDENTIFY_KIND = "bid-subject-identification"
RUN_KIND = "rejection-check-run"


class RunChecksRequest(BaseModel):
    """执行检查的可选项（对齐参考 `runRejectionCheckTask` 的 `options`）。"""

    checkOptions: dict[str, Any] | None = None


def _enabled_checks(options: Mapping[str, Any]) -> list[str]:
    """参考 :1624-1628 的启用集合与**顺序**。"""
    normalized = normalize_check_options(options)
    return [kind for kind in ("rejection", "typo", "logic", "subject")
            if normalized.get(f"{kind}Check")]


def _main_tender_markdown(data_root: Path, project_id: str, state: AppState) -> str:
    row = state.rejection.get_document(project_id, TENDER_DOCUMENT_ID)
    return _row_markdown(data_root, project_id, row) if row else ""


def _bid_documents_with_content(data_root: Path, project_id: str,
                                state: AppState) -> list[dict[str, Any]]:
    """参考 :1615-1617：只保留「有 id 且正文非空」的投标文档。"""
    documents = []
    for row in state.rejection.list_documents(project_id, role="bid"):
        content = _row_markdown(data_root, project_id, row)
        if row["document_id"] and content.strip():
            documents.append({"document_id": row["document_id"], "role": "bid",
                              "source": row["source"], "file_name": row["file_name"],
                              "content": content})
    return documents


@router.post("/projects/{project_id}/rejection/extract")
async def start_extraction(project_id: str, state: AppState = Depends(get_state)) -> dict:
    """步骤 2：解析无效与废标项（占位实现）。前置：招标内容非空（参考 :1521 逐字）。"""
    data_root = Path(state.settings.data_root)
    tender_content = _main_tender_markdown(data_root, project_id, state)
    tender_signature = state.rejection.get_document(project_id, TENDER_DOCUMENT_ID)
    if not tender_content.strip() or tender_signature is None:
        raise HTTPException(status_code=400, detail="缺少招标文件内容，无法解析无效与废标项")
    tender_row = state.rejection.get_document(project_id, TENDER_DOCUMENT_ID)
    payload = {"projectId": project_id,
               "tenderPath": str(tender_row["markdown_path"] or "") if tender_row else "",
               "tenderSignature": stable_hash(tender_content.strip())}
    task_id = await state.engine.submit(
        project_id=project_id, domain=ANALYSIS_DOMAIN, kind=EXTRACTION_KIND,
        payload=payload,
        runner=RejectionExtractionRunner(
            state.rejection, state.ai, data_root=data_root,
            config={"context_length_limit": state.settings.ai_context_length_limit}),
    )
    return {"taskId": task_id}


@router.post("/projects/{project_id}/rejection/subject/identify")
async def start_subject_identification(project_id: str,
                                       state: AppState = Depends(get_state)) -> dict:
    """步骤 3：识别投标主体候选（占位实现）。前置：有投标内容（参考 :647 逐字）。"""
    data_root = Path(state.settings.data_root)
    documents = _bid_documents_with_content(data_root, project_id, state)
    if not documents:
        raise HTTPException(status_code=400, detail="缺少投标文件内容，无法识别投标主体")
    task_id = await state.engine.submit(
        project_id=project_id, domain=ANALYSIS_DOMAIN, kind=IDENTIFY_KIND,
        payload={"projectId": project_id, "bidDocumentIds": [d["document_id"] for d in documents]},
        runner=StubSubjectIdentificationRunner(state.rejection),
    )
    return {"taskId": task_id}


@router.post("/projects/{project_id}/rejection/run")
async def start_rejection_check(project_id: str, body: RunChecksRequest | None = None,
                                state: AppState = Depends(get_state)) -> dict:
    """步骤 5：执行检查（占位实现）。

    四条前置**按参考顺序**逐字校验（参考 :1619-1666）：

    1. 投标文档（有内容）与投标签名齐备 → 「缺少投标文件内容，无法开始检查」
    2. 至少启用一种检查 → 「请至少启用一种检查」
    3. 启用废标项检查时必须有解析内容 → 「请先完成无效与废标项解析」
    4. 启用主体检查时必须已确认主体 → 「请先确认投标主体后再开始主体一致性检查」

    **有意偏离（登记）**：参考在任务内抛错（失败落 `task.error`），本实现在端点前置校验
    并返回 400 + 同一句话——语义相同（都阻止任务启动并给出同一文案），错误通道不同。
    """
    data_root = Path(state.settings.data_root)
    repo = state.rejection
    meta = repo.ensure_meta(project_id)

    documents = _bid_documents_with_content(data_root, project_id, state)
    bid_signature = create_bid_signature([
        {"id": d["document_id"], "role": "bid", "source": d["source"],
         "fileName": d["file_name"], "content": d["content"]} for d in documents])
    if not documents or not bid_signature:
        raise HTTPException(status_code=400, detail="缺少投标文件内容，无法开始检查")

    options = (body.checkOptions if body and body.checkOptions is not None
               else json.loads(meta["check_options_json"] or "{}"))
    enabled = _enabled_checks(options)
    if not enabled:
        raise HTTPException(status_code=400, detail="请至少启用一种检查")

    extraction = repo.load_extraction(project_id)
    custom_items = meta["custom_check_items"] or ""
    rejection_input_signature = create_rejection_check_input_signature(
        [{"document_id": d["document_id"], "role": "bid", "source": d["source"],
          "file_name": d["file_name"], "content": d["content"]} for d in documents],
        extraction.get("content") or "", custom_items)
    if "rejection" in enabled and (not (extraction.get("content") or "").strip()
                                   or not rejection_input_signature):
        raise HTTPException(status_code=400, detail="请先完成无效与废标项解析")

    profile = repo.load_subject_profile(project_id)
    # 参考 :1653-1662：**仅在档案已 confirmed** 时才算得出签名（未确认 → 空串 → 前置报错）
    subject_input_signature = (
        create_subject_check_input_signature(
            bid_signature=bid_signature,
            official_name=profile.get("officialName"),
            aliases=profile.get("aliases"),
            subject_seal_check=normalize_check_options(options).get("subjectSealCheck", False))
        if profile.get("status") == "confirmed" else "")
    if "subject" in enabled and (not profile.get("officialName") or not subject_input_signature):
        raise HTTPException(status_code=400,
                            detail="请先确认投标主体后再开始主体一致性检查")

    extraction = repo.load_extraction(project_id)
    payload = {
        "projectId": project_id,
        "enabledChecks": enabled,
        # 元数据（内容由 runner 按行读盘，payload_json 不放大段正文）
        "bidDocuments": [{"id": d["document_id"], "fileName": d["file_name"]}
                         for d in documents],
        "subjectSealCheck": normalize_check_options(options).get("subjectSealCheck", False),
        "signatures": {
            "rejection": rejection_input_signature,
            "typo": bid_signature,
            "logic": bid_signature,
            "subject": subject_input_signature,
        },
    }
    task_id = await state.engine.submit(
        project_id=project_id, domain=ANALYSIS_DOMAIN, kind=RUN_KIND,
        payload=payload,
        runner=RejectionCheckRunner(
            state.rejection, state.ai, state.settings.data_root,
            invalid_items=extraction.get("content") or "",
            custom_items=meta["custom_check_items"] or "",
            official_name=profile.get("officialName") or "",
            aliases=profile.get("aliases") or [],
            config={"context_length_limit": state.settings.ai_context_length_limit}),
    )
    return {"taskId": task_id}

