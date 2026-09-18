"""查重文件簿记 API（P3a 的对外契约面，P3d 前端直接对接）。

路由前缀 `/projects/{project_id}/duplicate`（`main.py` 的 `include_router` 无全局
前缀）。语义逐条对齐参考实现
`client/electron/services/duplicateCheckStore.cjs`：

- `POST /files`  = `saveFiles`（:913-927）：**全量替换两组文件** + 作废四段 +
  重写签名，三件事在一个事务里（见仓储 `save_files` 的 docstring）。
- `GET /files`   = `loadFiles`（:250-260）的 `{tender, bid}` 两键形状。
- `DELETE /files`= `clearDuplicateCheck`（:929-950）的库内部分（文件系统副作用
  延后 P3b）。
- `GET /state`   = `loadDuplicateCheck`（:604-632）的 analysis 形状 + 本层补的
  `sections` / `overallProgress` / `message`（brief F-3）。
- `POST /analysis` = 提交分析任务，返回 `{"taskId": ...}`（Ruling P3a-3：任务进度
  由 P1 的 `tasks` 表承载，客户端订 `GET /tasks/{task_id}/events`）。

**不实现任何分析算法**（P3b–P3d 的边界）。
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.deps import AppState, get_state
from app.domain.duplicate.progress import latest_analysis_message, overall_progress
from app.domain.duplicate.signature import stable_file_id
from app.domain.duplicate.state import ANALYSIS_SECTIONS, SectionState
from app.domain.shared.errors import ParsingError
from app.infra.parsing.service import save_uploaded_document
from app.infra.repositories.duplicate import DuplicateFileInput
from app.infra.tasks.duplicate_runner import DuplicateAnalysisRunner

router = APIRouter()

ANALYSIS_DOMAIN = "duplicate"
ANALYSIS_KIND = "duplicate-analysis"

# 两组文件的字段名（brief F-1）。前端 `persistSelectedFiles` 每次都传完整两组。
_GROUP_TENDER = "tender"
_GROUP_BID = "bid"

_EXPORT_NOT_IMPLEMENTED = "导出功能在 P3d 提供（P3a 不实现）。"


class AnalysisRequest(BaseModel):
    force: bool = False


# --------------------------------------------------------------------- POST /files


@router.post("/projects/{project_id}/duplicate/files")
async def upload_files(
    project_id: str,
    tender: list[UploadFile] | None = File(default=None),
    bid: list[UploadFile] | None = File(default=None),
    state: AppState = Depends(get_state),
) -> dict:
    """全量替换两组文件（对齐 `saveFiles`，brief F-1/F-2）。

    两个分组字段**至少出现一个**；两个都不出现是歧义请求 → 400（空列表是合法的
    显式「清空该组」，字段缺席不是）。

    **上传是破坏性操作**：换文件的同时作废全部已有分析结果并重写签名——由仓储的
    `save_files` 在**一个事务**里完成（brief F-2）。

    **F-5（已知的孤儿任务）**：本端点**不取消**该项目下 running 的旧 duplicate
    任务。旧任务跑完时拿旧签名写段状态，会被 T4 编排器的签名守卫
    （`_is_up_to_date` 的合取式，`duplicate_runner.py:627-642`）拦下，**不会污染**
    本次上传后的状态；四段已被 `save_files` 清空，旧任务写入的签名与
    `current_signature` 不等，故幂等也不会误命中。选择「不取消」而非
    「取消」的理由见 Task 5 报告：不取消不引入新契约（`DELETE /files` 与
    `POST /files` 行为对该缺口保持一致），且取消会与引擎的闸门语义
    （`_project_gates` 串行化）产生不必要的耦合。

    **逐文件容错**：单个文件解析失败 → 该行记 `error` 痕迹并继续，整批仍返回 200
    （对齐参考实现 `duplicateCheckService` 的逐文件 try）。失败行的形状见
    `_parse_failed_file`。
    """
    if tender is None and bid is None:
        raise HTTPException(
            status_code=400,
            detail="必须至少出现 tender 或 bid 分组字段（两个都不出现是歧义请求）")

    data_root = Path(state.settings.data_root)
    tender_inputs: list[DuplicateFileInput] = []
    for upload in tender or []:
        item = await _ingest_upload(upload, project_id=project_id,
                                    role=_GROUP_TENDER, data_root=data_root)
        if item is not None:
            tender_inputs.append(item)
    bid_inputs: list[DuplicateFileInput] = []
    for upload in bid or []:
        item = await _ingest_upload(upload, project_id=project_id,
                                    role=_GROUP_BID, data_root=data_root)
        if item is not None:
            bid_inputs.append(item)

    state.duplicate.save_files(project_id, tender_inputs, bid_inputs,
                               step="upload")

    listing = state.duplicate.list_files(project_id)
    return {
        _GROUP_TENDER: _files_for_role(listing, _GROUP_TENDER),
        _GROUP_BID: _files_for_role(listing, _GROUP_BID),
    }


# --------------------------------------------------------------------- GET /files


@router.get("/projects/{project_id}/duplicate/files")
def list_files(project_id: str, state: AppState = Depends(get_state)) -> dict:
    """`{tender: [...], bid: [...]}`（对齐 `loadFiles`，:250-260）。"""
    listing = state.duplicate.list_files(project_id)
    return {
        _GROUP_TENDER: _files_for_role(listing, _GROUP_TENDER),
        _GROUP_BID: _files_for_role(listing, _GROUP_BID),
    }


# ------------------------------------------------------------------ DELETE /files


@router.delete("/projects/{project_id}/duplicate/files")
def delete_files(project_id: str, state: AppState = Depends(get_state)) -> dict:
    """清空该项目的文件与全部结果表（`clearDuplicateCheck` 的库内部分）。

    **本任务不删文件系统目录**——参考实现 :929-950 事务后还删 content 目录与
    imported image batches，那是 P3b 的产物路径清理（登记为已知缺口）。

    四段状态，以及 5 张子表，不靠新加 `ON DELETE CASCADE`（brief F-4：无触发
    路径、无法被变异探针判别、且参考实现没有删项目路径），而是**经 HTTP 面钉住
    既有 FK 级的实际行为**——`test_delete_files_cascades_through_api` 直查 5 张
    子表断言无行。
    """
    state.duplicate.delete_files(project_id)
    state.duplicate.delete_sections(project_id)
    return {"tender": [], "bid": []}


# --------------------------------------------------------------------- GET /state


@router.get("/projects/{project_id}/duplicate/state")
def get_state_endpoint(project_id: str,
                       state: AppState = Depends(get_state)) -> dict:
    """`load_analysis` 的形状 + `sections` / `overallProgress` / `message`。

    `load_analysis` 自己**不含**后三个键（brief F-3）——它们由本层用 T3 的纯函数
    现算；其余 camelCase 键原样透传（前端按它消费）。
    """
    analysis = state.duplicate.load_analysis(project_id)
    states = _section_states(state, project_id)
    return {
        **analysis,
        "sections": {
            section: _section_response(state.duplicate.get_sections(project_id)
                                       .get(section))
            for section in ANALYSIS_SECTIONS
        },
        "overallProgress": overall_progress(states),
        "message": latest_analysis_message(states),
    }


# ---------------------------------------------------------------- POST /analysis


@router.post("/projects/{project_id}/duplicate/analysis")
async def start_analysis(project_id: str, body: AnalysisRequest | None = None,
                         state: AppState = Depends(get_state)) -> dict:
    """提交查重分析任务，返回 `{"taskId": ...}`。

    **payload 必须用 `repo.list_files()` 取回的入库行**：编排器
    `DuplicateAnalysisRunner.__call__:326-327` 从 payload 算签名，
    `_MappingFileRef` 只读 `file_path`/`size`/`modified_at` 三个键；且 payload 顺序
    必须是 `[...tenderFiles, ...bidFiles]`。这与 `save_files` 写签名用的入参
    同形同序 —— 两处签名逐字节一致是幂等能命中的**硬约束**（brief F-2）。

    `force` 默认 `False`（对齐参考实现 `:2799` 的 `payload.get("force")`）。
    """
    listing = state.duplicate.list_files(project_id)
    if not listing:
        raise HTTPException(status_code=400, detail="请先上传招标或投标文件")

    tender_files = _files_for_role(listing, _GROUP_TENDER)
    bid_files = _files_for_role(listing, _GROUP_BID)
    payload = {
        "tenderFiles": tender_files,
        "bidFiles": bid_files,
        "force": bool(body.force) if body is not None else False,
    }
    task_id = await state.engine.submit(
        project_id=project_id, domain=ANALYSIS_DOMAIN, kind=ANALYSIS_KIND,
        payload=payload, runner=DuplicateAnalysisRunner(repo=state.duplicate),
    )
    return {"taskId": task_id}


# -------------------------------------------------------------------- GET /export


@router.get("/projects/{project_id}/duplicate/export")
def export_analysis(project_id: str,
                    state: AppState = Depends(get_state)) -> dict:
    """导出属 P3d → P3a 返回 501（不实现）。"""
    raise HTTPException(status_code=501, detail=_EXPORT_NOT_IMPLEMENTED)


# ---------------------------------------------------------------------- 内部实现


async def _ingest_upload(upload: UploadFile, *, project_id: str, role: str,
                         data_root: Path) -> DuplicateFileInput | None:
    """把一次上传落盘成一个 `DuplicateFileInput`。

    参照 R24：`save_uploaded_document` **先解析后落盘**，解析失败抛 `ParsingError`
    且一个字节都没写。此处必须先把上传字节写到**临时文件**（`parse_document` 要的是
    磁盘路径，`UploadFile.file` 是 `SpooledTemporaryFile` 不是路径），并在 `finally`
    里清掉。

    解析失败时返回带 `error` 痕迹的行（见 `_parse_failed_file`），**不抛**
    —— 单文件失败不能拖垮整批（对齐参考实现的逐文件 try）。
    """
    payload = await upload.read()
    file_name = upload.filename or "未命名文件"
    # 与参考实现 `path.extname(filePath).toLowerCase()` 一致：**带前导点**。
    extension = Path(file_name).suffix.lower()

    tmp_dir = tempfile.mkdtemp(prefix="yibiao-upload-")
    tmp_path = Path(tmp_dir) / file_name
    try:
        tmp_path.write_bytes(payload)
        saved = save_uploaded_document(
            data_root=data_root, project_id=project_id,
            source_path=tmp_path, role=role, provider="local")
    except ParsingError as error:
        return _parse_failed_file(project_id=project_id, role=role,
                                  file_name=file_name, payload=payload,
                                  extension=extension, error=error)
    finally:
        _remove_tree(tmp_path.parent)

    file_id = stable_file_id(saved.source_path, file_name,
                             explicit_id=saved.document_id)
    return DuplicateFileInput(
        file_id=file_id,
        file_name=file_name,
        file_path=saved.source_path,
        extension=saved.extension or extension,
        size=len(payload),
        # `modified_at` 是**文件修改时间**（对齐参考实现 `fileService.cjs:93/329`
        # 的 `stats.mtime.toISOString()`）。它参与签名（`path|size|modified`），
        # 所以「换文件后签名要变」由它承担 —— 用落盘副本的 mtime，语义与参考一致。
        modified_at=_mtime_iso(Path(saved.source_path)),
        content_hash=saved.content_hash,
    )


def _parse_failed_file(*, project_id: str, role: str, file_name: str,
                       payload: bytes, extension: str,
                       error: ParsingError) -> DuplicateFileInput:
    """解析失败行的形状（brief 报告要求 8）。

    参考实现里文件项是 `{file_path, size, modified_at}` 三键，失败时参考实现
    （`duplicateCheckService` 的逐文件 try）**不落文件行**，只在 content_files 里
    记 error。服务端要在同一张 `duplicate_check_files` 里表达「这一行存在但内容
    不可用」，故：

    - `file_path` = `"upload-failed:<code>"` —— 稳定的机器可读占位。**不是真路径**：
      `save_uploaded_document` 失败时**一个字节都没写**（R24），没有真路径可给。
      选前缀而非 NULL 是因为 `file_path` 列是 `NOT NULL`，且 `stable_file_id` 要拿
      它算 id。
    - `modified_at` = `None` —— 解析失败没有落盘副本，拿不到 mtime。（`None` 在签名
      里渲染成 JS 的 `"null"`，仍是稳定值。）
    - `size` = 上传字节数（这个是真的，来自请求体）。
    - `content_hash` = `None`。
    - `file_id` 由 `stable_file_id("upload-failed:<code>", file_name)` 算出 ——
      同一失败原因 + 同一文件名 → 同一 id（确定性，便于重复上传覆盖）。
    - **`error` 痕迹**：`duplicate_check_files` 没有 `error` 列，故 error 文本编码在
      `file_path` 的 `upload-failed:<code>` 里；HTTP 响应由 `_error_of` 解出 `code`
      放进 `error` 键（`"format-mismatch"` / `"parser-unsupported"` 等）。
    - `role` 由调用方的分组字段决定（本函数不收 role，`save_files` 按组写入）。
    """
    file_path = f"upload-failed:{error.code}"
    return DuplicateFileInput(
        file_id=stable_file_id(file_path, file_name),
        file_name=file_name,
        file_path=file_path,
        extension=extension,
        size=len(payload),
        modified_at=None,
        content_hash=None,
    )


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _mtime_iso(path: Path) -> str:
    """文件修改时间的 ISO 8601（UTC），对齐 JS `Date.prototype.toISOString()`。

    JS 的 `toISOString()` 恒为 UTC、毫秒精度、`Z` 结尾（如
    `2026-01-01T00:00:00.000Z`）；Python 的 `datetime.isoformat()` 默认给
    `+00:00` 且可能省略毫秒。为了与参考实现写进签名的字符串**逐字对齐**，
    这里显式格式化。取不到时回落空串（对齐 `fileService.cjs:96` 的 catch 分支
    `summary.modified_at = ''`）。
    """
    from datetime import datetime, timezone

    try:
        stamp = path.stat().st_mtime
    except OSError:
        return ""
    moment = datetime.fromtimestamp(stamp, tz=timezone.utc)
    millis = moment.microsecond // 1000
    return (moment.strftime("%Y-%m-%dT%H:%M:%S")
            + f".{millis:03d}Z")


def _files_for_role(listing: Sequence[Mapping[str, Any]], role: str) -> list[dict]:
    """按 role 过滤并剥掉内部列，只留前端契约键（对齐 `fileFromRow`）。

    保留 `file_path` / `size` / `modified_at` —— `POST /analysis` 的 payload 必须
    带这三键（T4 靠它们算签名）。
    """
    return [
        {
            "file_id": row["file_id"],
            "file_name": row["file_name"],
            "file_path": row["file_path"],
            "extension": row["extension"],
            "size": row["size"],
            "modified_at": row["modified_at"],
            "role": row["role"],
            "error": _error_of(row),
        }
        for row in listing
        if row["role"] == role
    ]


def _error_of(row: Mapping[str, Any]) -> str | None:
    """失败行的 error 痕迹：`file_path` 的 `upload-failed:<code>` 前缀。"""
    file_path = row.get("file_path") or ""
    if isinstance(file_path, str) and file_path.startswith("upload-failed:"):
        return file_path.split(":", 1)[1]
    return None


def _section_states(state: AppState, project_id: str) -> dict[str, SectionState | None]:
    """把仓储行读成 T3 的 `SectionState`，供进度函数使用（缺段为 `None`）。"""
    rows = state.duplicate.get_sections(project_id)
    states: dict[str, SectionState | None] = {}
    for section in ANALYSIS_SECTIONS:
        row = rows.get(section)
        states[section] = None if row is None else SectionState(
            status=row.get("status") or "pending",
            progress=row.get("progress") or 0,
            message=row.get("message") or "",
            signature=row.get("signature"),
        )
    return states


def _section_response(row: Mapping[str, Any] | None) -> dict:
    """单段的响应形状；**缺段也出现**（F-3：用默认 `pending` 形状）。"""
    if row is None:
        return {"status": "pending", "progress": 0, "message": "",
                "signature": None, "started_at": None, "updated_at": None,
                "stats": {}}
    return {
        "status": row.get("status") or "pending",
        "progress": int(row.get("progress") or 0),
        "message": row.get("message") or "",
        "signature": row.get("signature"),
        "started_at": row.get("started_at"),
        "updated_at": row.get("updated_at"),
        "stats": row.get("stats") or {},
    }
