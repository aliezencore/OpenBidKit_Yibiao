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

import logging
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, StrictBool

from app.api.deps import AppState, get_state
from app.domain.duplicate.progress import latest_analysis_message, overall_progress
from app.domain.duplicate.signature import stable_file_id
from app.domain.duplicate.state import ANALYSIS_SECTIONS, SectionState
from app.domain.shared.errors import ParsingError
from app.infra.parsing.service import save_uploaded_document
from app.infra.repositories.duplicate import DuplicateFileInput
from app.infra.tasks.duplicate_runner import (
    CONTENT_EXTRACTION,
    DuplicateAnalysisRunner,
    _stub_sections,
)
from app.infra.tasks.duplicate_sections import (
    build_content_extraction,
    build_content_runner,
    build_outline_runner,
    content_dir_for,
)

_logger = logging.getLogger(__name__)

router = APIRouter()

ANALYSIS_DOMAIN = "duplicate"
ANALYSIS_KIND = "duplicate-analysis"

# 两组文件的字段名（brief F-1）。前端 `persistSelectedFiles` 每次都传完整两组。
_GROUP_TENDER = "tender"
_GROUP_BID = "bid"

_EXPORT_NOT_IMPLEMENTED = "导出功能在 P3d 提供（P3a 不实现）。"

# 解析失败行的 `file_path` 编码：`upload-failed:<code>\x1f<file_name>`。
# `\x1f`（ASCII Unit Separator）在正常错误码与文件名里都不可能出现，故分隔无歧义。
_FAILED_PATH_PREFIX = "upload-failed:"
_FAILED_PATH_SEP = "\x1f"

# `POST /files` 的 id 唯一性防御（C-1 兜底）用的错误文案。
_DUPLICATE_ID_DETAIL = "上传的文件 id 冲突（同名文件重复，或文件解析状态不一致）。"

# `POST /analysis` 在「全部行都是解析失败行」时的错误文案（I-1）。
_ALL_FILES_FAILED_DETAIL = "所有文件均解析失败，无可分析内容"


class AnalysisRequest(BaseModel):
    """`POST /analysis` 的请求体。

    `force` 用 **`StrictBool`**（fix-round 1 / M-2，评审 B 登记）：宽松 `bool`
    会把字符串 `"false"` 强转成 `True`（`bool("false") is True`）→
    `body={"force": "false"}` 得到 HTTP 200 且**强制重跑**，与调用方直觉相反。
    用 `StrictBool` 后非真布尔（字符串 / 数字 / 列表）一律 422，语义无歧义。
    选 `StrictBool` 而非「写进 docstring」的理由：这是**契约收紧**，
    让错误请求响亮失败比让调用方读文档更可靠；且参考实现（JS 侧）只在真布尔时
    传该字段，收紧不破坏任何已定语义。
    """

    force: StrictBool = False


# --------------------------------------------------------------------- POST /files


def _strip_empty_uploads(
        uploads: list[UploadFile] | None) -> list[UploadFile] | None:
    """把「字段出现但只带空占位 part」归一化成空列表（fix-round 2 / N-4）。

    multipart 里「字段出现但为空」唯一协议可达的形状是 `filename=""` 的空
    file part —— 这正是浏览器空 `<input type="file">` 的原生提交形状（前端
    表达「该组 0 个文件」）。starlette 会把它解析成一个 `filename=""`、
    0 字节的 `UploadFile`，若不过滤就会走解析流程变成一条失败行垃圾。

    - `None`（字段缺席）原样返回 —— 缺席语义不变（两组都缺席仍是 400）。
    - `filename` 为空串/`None` 的 part 一律视为占位；正常文件名不可能为空
      （`_ingest_upload` 对空名兜底「未命名文件」，正是为这类占位准备的，
      但归一化后它根本走不到那里）。
    """
    if uploads is None:
        return None
    return [upload for upload in uploads if upload.filename]


@router.post("/projects/{project_id}/duplicate/files")
async def upload_files(
    project_id: str,
    tender: list[UploadFile] | None = File(default=None),
    bid: list[UploadFile] | None = File(default=None),
    state: AppState = Depends(get_state),
) -> dict:
    """全量替换两组文件（对齐 `saveFiles`，brief F-1/F-2）。

    两个分组字段**都不出现**是歧义请求 → 400（「全清空」应走 `DELETE /files`）。
    「该组为空」有两处显式表达（fix-round 2 / N-4）：

    - **字段出现但为空**：该组只带 `filename=""` 的空 part —— 这是浏览器空
      `<input type="file">` 的原生提交形状，也是 multipart 里「空列表」唯一
      协议可达的形状。它会被归一化成空列表（`_strip_empty_uploads`）。
      修复前任何「出现但空」的形状都 422（`Expected UploadFile`），而参考
      实现「删到 0 个文件」走的就是空列表（`DuplicateCheckPage.tsx:1101`
      的 `persistSelectedFiles(tenderFiles, [])`），P3d 前端对接会撞死。
      为何不用 `File(default=[])`：实测它既救不了「出现但空」（FastAPI 对
      字符串 part 的 `UploadFile` 验证与 default 无关，仍 422），又把「字段
      缺席」的取值从 `None` 变 `[]`，直接瓦解上面的 400 歧义防线（用例 2）。
    - **单组字段缺席** = 清空该组（全量替换语义，`or []`，用例 3 钉住）。
      两组都缺席 ≠ 两组都空：前者没说清意图 → 400，后者是显式意图 → 200。

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

    **失败整批时的磁盘一致性（fix-round 1 / C-1 兜底）**：本路径对每个文件**先
    落盘**，而 `save_files` 的事务只回滚 DB。故本端点在异常路径上删掉**本次已落盘
    的副本**（`_discard_saved_copies`），不留下「库回滚了、盘上还留着」的孤儿。
    """
    if tender is None and bid is None:
        raise HTTPException(
            status_code=400,
            detail="必须至少出现 tender 或 bid 分组字段（两个都不出现是歧义请求）")
    # N-4：把「字段出现但只带空占位 part」归一化成空列表（显式清空该组）。
    # 占位 part 若流到 ingest 会被当成 0 字节文件解析，变成一条
    # `filename=""` 的失败行垃圾。过滤不改变 `None` 性（字段缺席仍是缺席），
    # 故上面的 400 歧义判断不受影响。
    tender = _strip_empty_uploads(tender)
    bid = _strip_empty_uploads(bid)

    data_root = Path(state.settings.data_root)
    tender_inputs: list[DuplicateFileInput] = []
    bid_inputs: list[DuplicateFileInput] = []
    # 本次请求**已落盘**的产物（`markdown_path` / `source_path`）。异常路径靠它
    # 枚举出要删的副本 —— C-1 兜底要求的「已落盘文件列表在异常路径上可枚举」。
    saved_paths: list[str] = []
    try:
        for upload in tender or []:
            item = await _ingest_upload(upload, project_id=project_id,
                                        role=_GROUP_TENDER, data_root=data_root,
                                        saved_paths=saved_paths)
            if item is not None:
                tender_inputs.append(item)
        for upload in bid or []:
            item = await _ingest_upload(upload, project_id=project_id,
                                        role=_GROUP_BID, data_root=data_root,
                                        saved_paths=saved_paths)
            if item is not None:
                bid_inputs.append(item)

        # C-1 兜底：入口的 id 唯一性校验。`save_files` 的主键是
        # `(project_id, file_id)`，任何 id 生成路径不单射都会让**整批**撞
        # `IntegrityError`（500 + 事务回滚）。这里提前判，降级为 400 + 明确 detail。
        _assert_unique_file_ids(*tender_inputs, *bid_inputs)

        state.duplicate.save_files(project_id, tender_inputs, bid_inputs,
                                   step="upload")
        # R-P3b-6：上传全量替换成功后清 content 目录（换文件则旧正文产物作废）。
        # 复用 `_remove_tree` 的可观测清理（与参考 `saveFiles` 事务后调
        # `clearDuplicateContentArtifacts` 语义对齐，:924）。
        _remove_tree(content_dir_for(state.settings.data_root, project_id))
    except BaseException:
        # 滚回：DB 回滚由仓储的事务负责，这里补上「盘上不留孤儿」。
        # 兜 `BaseException` 而非 `Exception`：取消（`asyncio.CancelledError`）
        # 同样会让 DB 回滚，磁盘必须跟着滚回，否则留下孤儿。
        _discard_saved_copies(saved_paths)
        raise

    listing = state.duplicate.list_files(project_id)
    return {
        _GROUP_TENDER: _files_for_role(listing, _GROUP_TENDER),
        _GROUP_BID: _files_for_role(listing, _GROUP_BID),
    }


def _assert_unique_file_ids(*items: DuplicateFileInput) -> None:
    """入口的 id 唯一性防御（C-1 兜底，评审 B 建议）。

    把「撞主键 → `IntegrityError` → 500 + 整批回滚」降级为
    「400 + 明确 detail」。

    **为什么需要这道兜底**：即使 `_parse_failed_file` 的塌缩已修好，未来任何新增的
    id 生成路径（P3b 的 content / image 派生文件）都可能再次把 500 带回来；
    而 `save_files` 是**全量替换**语义 —— 一个 id 冲突掀掉的是整批，不是一行。
    在入口判一次，代价是一次 O(n) 扫描，收益是「响亮且可解释的失败」。

    **同名文件重复**是这条校验会拦下的真实输入：同一批次里两个同名文件即使 code
    不同，`upload-failed:<code>\\x1f<name>` 仍可能相同；若 code 也相同则必然相同。
    """
    seen: dict[str, str] = {}
    for item in items:
        name = seen.setdefault(item.file_id, item.file_name)
        if name != item.file_name:
            raise HTTPException(status_code=400, detail=_DUPLICATE_ID_DETAIL)
    # 同 id 同名（逐字节相同的重复项）也要拦：同样撞主键。
    if len(seen) != len(items):
        raise HTTPException(status_code=400, detail=_DUPLICATE_ID_DETAIL)


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
    # R-P3b-6：参考 `clearDuplicateCheck`（:929-956）事务后删 content 目录与
    # imported image batches（content 产物路径清理）。服务端此处同样清
    # `duplicate_content/` 目录，与 POST /files 的清理保持一致。
    _remove_tree(content_dir_for(state.settings.data_root, project_id))
    return {"tender": [], "bid": []}


# --------------------------------------------------------------------- GET /state


@router.get("/projects/{project_id}/duplicate/state")
def get_state_endpoint(project_id: str,
                       state: AppState = Depends(get_state)) -> dict:
    """`load_analysis` 的形状 + `sections` / `overallProgress` / `message`。

    `load_analysis` 自己**不含**后三个键（brief F-3）——它们由本层用 T3 的纯函数
    现算；其余 camelCase 键原样透传（前端按它消费）。

    **单次读（fix-round 1 / I-3，评审 B 登记）**：原实现对
    `repo.get_sections(project_id)` 发 **6 次**独立 SELECT —— `load_analysis` 内 1 次、
    `_section_states` 1 次、`sections` 字典推导里每段各 1 次（×4）。6 次读**跨不同
    事务**，并发写时同一响应体内可能 `sections` 与 `overallProgress` 来自不同时刻
    而自相矛盾（「四段全绿但进度条没满」）。现在开头读**一次** `section_rows`，
    两个 helper 都吃同一份 —— 纯重构，不改契约。

    注意 `load_analysis` 内部**仍会自己读一次**段状态（它是 T2 已评审的接口，
    本任务不改）。因此本端点的读次数从 6 降到 **2**（1 次 `load_analysis` 内部 +
    1 次本层的共享 `section_rows`），且**本层新增的三个键之间**不再有跨读窗口。
    把 `load_analysis` 也收进同一份快照需要改它的签名，超出本任务 diff 边界
    （已在此登记）。
    """
    section_rows = state.duplicate.get_sections(project_id)
    analysis = state.duplicate.load_analysis(project_id)
    states = _section_states(section_rows)
    return {
        **analysis,
        "sections": {
            section: _section_response(section_rows.get(section))
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

    **拒绝「全失败行」（fix-round 1 / I-1，评审 B 登记的 Important）**：
    原实现的唯一前置校验是「文件表非空」，而**全 `upload-failed:*` 的行同样非空**
    → 任务被提交、跑完、四段全 `success`、`task.error` 为 `None`：
    一个字节可分析内容都没有，却报「查重已完成」（评审 B 实测坐实）。
    现在追加校验「至少存在一行**非失败**文件」，否则 400 + 明确 detail。
    部分失败仍被允许（那是有可分析内容、结果不完整的正常情形）。
    """
    listing = state.duplicate.list_files(project_id)
    if not listing:
        raise HTTPException(status_code=400, detail="请先上传招标或投标文件")
    if not any(_error_of(row) is None for row in listing):
        raise HTTPException(status_code=400, detail=_ALL_FILES_FAILED_DETAIL)

    tender_files = _files_for_role(listing, _GROUP_TENDER)
    bid_files = _files_for_role(listing, _GROUP_BID)
    payload = {
        "tenderFiles": tender_files,
        "bidFiles": bid_files,
        "force": bool(body.force) if body is not None else False,
    }
    # P3b：把 stub 的角色替换为真实 runner。content_extraction（Task 4）与
    # outline / content 两段（Task 5）替换；metadata_extraction 与 image 段保持 P3a stub。
    # 改动最小化——只替换对应键。
    sections = dict(_stub_sections())
    sections[CONTENT_EXTRACTION] = build_content_extraction(state.settings.data_root)
    sections["outline"] = build_outline_runner(state.settings.data_root)
    sections["content"] = build_content_runner(state.settings.data_root)
    task_id = await state.engine.submit(
        project_id=project_id, domain=ANALYSIS_DOMAIN, kind=ANALYSIS_KIND,
        payload=payload,
        runner=DuplicateAnalysisRunner(repo=state.duplicate, sections=sections),
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
                         data_root: Path,
                         saved_paths: list[str] | None = None) -> DuplicateFileInput | None:
    """把一次上传落盘成一个 `DuplicateFileInput`。

    参照 R24：`save_uploaded_document` **先解析后落盘**，解析失败抛 `ParsingError`
    且一个字节都没写。此处必须先把上传字节写到**临时文件**（`parse_document` 要的是
    磁盘路径，`UploadFile.file` 是 `SpooledTemporaryFile` 不是路径），并在 `finally`
    里清掉。

    解析失败时返回带 `error` 痕迹的行（见 `_parse_failed_file`），**不抛**
    —— 单文件失败不能拖垮整批（对齐参考实现的逐文件 try）。

    **`saved_paths`**（C-1 兜底）：落盘成功时把 `markdown_path` 与 `source_path`
    追加进去，供调用方在异常路径上枚举并删除 —— 这是「已落盘文件列表在异常路径上
    可枚举」的实现。
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

    if saved_paths is not None:
        saved_paths.append(saved.markdown_path)
        saved_paths.append(saved.source_path)

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
      选前缀而非 NULL 是因为 `file_path` 列是 `NOT NULL`。
    - `modified_at` = `None` —— 解析失败没有落盘副本，拿不到 mtime。（`None` 在签名
      里渲染成 JS 的 `"null"`，仍是稳定值。）
    - `size` = 上传字节数（这个是真的，来自请求体）。
    - `content_hash` = `None`。

    **`file_id` 必须对失败行单射（fix-round 1 / C-1，语义裁决）**

    原实现是 `stable_file_id(file_path, file_name)`，而 `stable_file_id` 的内部是
    `file_path or file_name or ""` —— `file_path` 恒为 `"upload-failed:<code>"`
    （与文件名无关），**短路使 `file_name` 根本不参与 id 计算**。后果：同一项目内
    两个 `error.code` 相同的坏文件算出逐字节相同的 id → 撞
    `PRIMARY KEY (project_id, file_id)` → 整个 `save_files` 事务回滚 → HTTP 500，
    连好文件都进不去；且事务只回滚 DB，已落盘的副本变成不可回收的孤儿。

    **裁决：让 id 单射**（而不是「同 code 的失败行共用 id 以便重复上传覆盖」）。
    理由：**「同批两个不同坏文件共存」是真实且常见的场景**（真实标书目录里
    `.pdf`/`.doc`/`.wps` 混杂，一次上传里多个文件落到同一失败码是常态），而
    「重复上传覆盖」在 `save_files` 是**全量替换**语义的前提下**不需要 id 承担**
    —— 全量替换先 `DELETE ... WHERE project_id = ?` 再重插，同 code 同名的文件
    重复上传本来就是「先删后插」，id 相同与否都不影响结果。两个需求因此不互斥：
    单射不会牺牲任何真实能力，而共用 id 会牺牲共存。

    id 口径选 `upload-failed:<code>\x1f<file_name>`：`<code>` 保持机器可读（与
    `_error_of` 的解码口径一致），文件名提供工程上恒成立的单射性（同一 multipart
    批次里同名文件会被 `save_files` 的 id 校验降级为 400，见那里）。`\x1f`
    （ASCII Unit Separator）在正常错误码与文件名里都不可能出现，故分隔无歧义。

    **失败行的签名（fix-round 1 / I-2）**：签名的第三个分量是 `modified_at`，
    失败行恒为 `None` → 渲染成 `"null"`；若 `file_path` 只含 code，则**不同失败行
    只要 code 与字节数相同，签名就完全相同**（评审 B 实测
    `sig(bad1.docx) == sig(another-name.docx): True`）→ 换掉失败行后签名不变，
    可能命中幂等而跳过重跑。因此 `file_path` 里也带上文件名 —— 它与 `file_id`
    同源，让签名对「失败行集合的任何变化」敏感。

    - **`error` 痕迹**：`duplicate_check_files` 没有 `error` 列，故 error 文本编码在
      `file_path` 的 `upload-failed:<code>` 里；HTTP 响应由 `_error_of` 解出 `code`
      放进 `error` 键（`"format-mismatch"` / `"parser-unsupported"` 等）。
      前缀嗅探的已知风险见 `_error_of`。
    - `role` 由调用方的分组字段决定（本函数不收 role，`save_files` 按组写入）。
    """
    file_path = f"{_FAILED_PATH_PREFIX}{error.code}{_FAILED_PATH_SEP}{file_name}"
    return DuplicateFileInput(
        # id 与 file_path **同口径**：单射性由 `file_name` 提供。
        # `explicit_id` 直接给出该口径的 sha1，避免再经 `stable_file_id` 的
        # `or` 短路（那正是本 bug 的成因）。
        file_id=stable_file_id(file_path, file_name, explicit_id=_failed_file_id(
            file_path)),
        file_name=file_name,
        file_path=file_path,
        extension=extension,
        size=len(payload),
        modified_at=None,
        content_hash=None,
    )


def _failed_file_id(file_path: str) -> str:
    """失败行的 id：对 `file_path` 口径做 sha1（与 `stable_file_id` 同算法）。"""
    import hashlib

    return hashlib.sha1(file_path.encode("utf-8")).hexdigest()


def _error_of(row: Mapping[str, Any]) -> str | None:
    """失败行的 error 痕迹：`file_path` 的 `upload-failed:<code>[\\x1f<name>]` 前缀。

    形状由 `_parse_failed_file` 决定。`<name>` 段是 fix-round 1 / C-1、I-2 为了让
    `file_id` 与签名对失败行单射而加进去的，解码时按第一个分隔符切掉即可，
    对**旧形状**（只有 code、没有分隔符）也仍然成立。

    **已知局限（M-5，评审 B 登记）**：这是「把语义塞进字符串前缀」的嗅探 ——
    若真实路径恰好以 `upload-failed:` 开头会被误判。评审判定为极低概率、低影响。
    本轮**不移除**它：独立 `error` 列需要改 `duplicate_check_files` 的 schema，
    而该表属 T1（已评审）且改 schema 会突破「本任务只允许新增 `save_files` 一个
    仓储方法」的边界。改为在 P3b 有迁移机会时加列 —— 已在修复报告里登记。
    """
    file_path = row.get("file_path") or ""
    if isinstance(file_path, str) and file_path.startswith(_FAILED_PATH_PREFIX):
        return file_path[len(_FAILED_PATH_PREFIX):].split(_FAILED_PATH_SEP, 1)[0]
    return None


def _remove_tree(path: Path) -> None:
    """删临时目录；**失败不再静默**（fix-round 1 / M-3，评审 B 登记）。

    原实现是 `shutil.rmtree(path, ignore_errors=True)`，会把删除失败
    （Windows 上文件被占用 / 权限不足）完全吞掉，`%TEMP%` 缓慢累积且无迹可查。
    改成 `ignore_errors=False` + 记日志：清理失败变成**可观测事件**而不是静默泄漏。

    这里**不把异常抛给调用方**：临时目录的清理失败不应该让一次本来成功的上传变成
    500（那比泄漏更糟）。所以是「记录并继续」而不是「抛出」。

    P3b Task 7 补充：**路径不存在**是正常的「无需删」（如 content 目录尚未产出），
    不属于清理失败——先 `exists()` 短路，避免每次上传都刷一条误导性 warning。
    """
    import shutil

    if not path.exists():
        return
    try:
        shutil.rmtree(path, ignore_errors=False)
    except OSError as error:
        _logger.warning("清理临时上传目录失败：%s（%s）", path, error)


def _discard_saved_copies(paths: Sequence[str]) -> list[str]:
    """滚回时删掉本次已落盘的副本，返回「删除失败」的路径（C-1 的孤儿清理）。

    上传路径对每个文件**先落盘**（`save_uploaded_document` 写 `uploads/` 与
    `documents/`），而 `save_files` 的事务**只回滚 DB** —— 一旦事务失败，磁盘上
    就留下无主副本，且 `DELETE /files` 也不删盘（P3b 才处理），这些孤儿**永远不会
    被任何路径回收**（评审 B 实测 2 个）。

    故上传路径把已落盘的产物收集成列表，在异常路径上**显式删除**。
    删除失败只记日志（尽力而为）：回滚路径的正确性不依赖磁盘清理成功。
    """
    failed: list[str] = []
    for raw in paths:
        try:
            Path(raw).unlink(missing_ok=True)
        except OSError as error:
            failed.append(raw)
            _logger.warning("回滚时删除落盘副本失败：%s（%s）", raw, error)
    return failed


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

    取值一律用 `row.get(...)`（fix-round 1 / M-4，评审 B 登记）：原实现是
    `row["role"]` 直接下标，与同模块 `_error_of` 的防御式风格不一致。
    DB 层 `role` 是 `NOT NULL`，故正常路径不触发；统一风格后，
    「入参由仓储保证形状」这个隐含前提不再被静默依赖。
    """
    return [
        {
            "file_id": row.get("file_id"),
            "file_name": row.get("file_name"),
            "file_path": row.get("file_path"),
            "extension": row.get("extension"),
            "size": row.get("size"),
            "modified_at": row.get("modified_at"),
            "role": row.get("role"),
            "error": _error_of(row),
        }
        for row in listing
        if row.get("role") == role
    ]


def _section_states(rows: Mapping[str, Mapping[str, Any]]
                    ) -> dict[str, SectionState | None]:
    """把仓储行读成 T3 的 `SectionState`，供进度函数使用（缺段为 `None`）。

    入参是**已读出的行**而不是 `state`（fix-round 1 / I-3）：调用方读一次、
    两处共用，消除同一响应体内的跨读不一致窗口。
    """
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
