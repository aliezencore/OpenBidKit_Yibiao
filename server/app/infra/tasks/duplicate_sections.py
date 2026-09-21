"""查重各真实算法段的实现（P3b 起逐任务填充）。

本模块是 P3a 的 `_stub_sections()` 的对应物：**段实现（编排接线）**进这里，
由 `create_app` 的 runner 构造处注入 `sections` 映射替换对应 stub 键。算法
纯函数进 `app/domain/duplicate/`（零 I/O 零框架），本模块只做「读上下文 →
调纯函数/解析器 → 写产物 → 收成 SectionOutput」的接线。

本任务（Task 4）先放 `content_extraction` 角色：重新解析正文、写独立 content
目录、产出 `content_files` 行、并把逐文件提取统计收进 `SectionOutput`。

对齐参考实现 `client/electron/services/duplicateCheckService.cjs` 的
`runContentExtraction`（:2302-2354）。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.infra.parsing.router import parse_document
from app.infra.tasks.duplicate_runner import (
    CONTENT_EXTRACTION,
    SectionOutput,
    SectionRunContext,
)


def content_dir_for(data_root: Path | str, project_id: str) -> Path:
    """content_extraction 的产物目录：`data_root/projects/{pid}/duplicate_content/`。

    对齐参考 `getDuplicateCheckContentDir(app)`（:2304）——参考是 userData 全局
    `duplicate-check-content/`，服务端随 `data_root` 结构落在项目目录下。
    `file_id` 全局唯一，行为等价；路径形状随 server 的 data_root 结构（R-P3b-1
    登记偏离，不追究）。
    """
    return Path(data_root) / "projects" / project_id / "duplicate_content"


def build_content_extraction(data_root: Path | str):
    """构造 `content_extraction` 角色的 runner 闭包。

    闭包捕获 `data_root`（来自 `settings.data_root`），按 `project_id` 在每次
    运行时拼出 content 目录。返回签名即 `SectionRunner`
    （`Callable[[SectionRunContext], Awaitable[SectionOutput]]`）。

    行为（对齐 `runContentExtraction` :2302-2354 + R-P3b-1 / R-P3b-4）：

    - 输入 = `SectionRunContext.files`（tender + bid 全量，参考 `:2315` 的
      `allFiles`）。逐文件：
      - `parse_document(Path(file_path), provider="local", include_images=True)`
        **经 `asyncio.to_thread`**（参考是 await 的异步解析；同步解析器用
        to_thread 包一层避免阻塞事件循环，真并发用例依赖它）。
      - `markdown = parsed.markdown.strip()`（参考 `:2321` `.trim()`）。
      - 写 `data_root/projects/{pid}/duplicate_content/{file_id}.md`
        （utf-8；目录不存在则建）。
      - success 行：`{file_id, file_name, status:"success", content_path,
        content_length: len(markdown), content_hash: sha256(utf8(markdown))}`。
      - 解析/写盘异常 → error 行 `{file_id, file_name, status:"error",
        error: str(error)}`，**继续**下一文件（参考 `:2336-2342` 的 catch）。
    - 任一 error → `SectionOutput(failed=True)`（R-P3b-4）。
    - stats 携带 `{"status", "completed", "total"}` 子对象，经主编排的
      `_merge_metadata_stats` 落在 `metadata` 段 stats 的 `content_extraction`
      键下（对齐参考 `:2313` / `:2347` 的 `contentExtraction` 子对象）。
    - 同时设 `extraction` 字段（P3b 冻结的第 5 字段），为同一子对象，供 T5 的
      段内 stats 归并使用；`message` 本任务留 None（T5 用）。
    """

    async def run_content_extraction(section_ctx: SectionRunContext) -> SectionOutput:
        project_id = section_ctx.project_id
        files = list(section_ctx.files)
        content_dir = content_dir_for(data_root, project_id)
        content_dir.mkdir(parents=True, exist_ok=True)

        results: list[dict[str, Any]] = []
        for file in files:
            file_id = _file_id_of(file)
            file_name = _file_name_of(file)
            file_path = _file_path_of(file)
            try:
                # R-P3b-1：重新解析（不复用上传的 documents/ markdown），
                # 同步解析器经 to_thread 包一层（避免阻塞事件循环）。
                # 注意 parse_document 的 provider/include_images 是 keyword-only。
                parsed = await asyncio.to_thread(
                    parse_document, Path(file_path),
                    provider="local", include_images=True)
                markdown = parsed.markdown.strip()
                content_path = content_dir / f"{file_id}.md"
                content_path.write_text(markdown, encoding="utf-8", newline="\n")
                results.append({
                    "file_id": file_id,
                    "file_name": file_name,
                    "status": "success",
                    "content_path": str(content_path),
                    "content_length": len(markdown),
                    # P8：hashText = sha256(utf8).hexdigest() 全文（参考 :266-268）
                    "content_hash": hashlib.sha256(
                        markdown.encode("utf-8")).hexdigest(),
                })
            except Exception as error:  # noqa: BLE001 —— 逐文件容错（R-P3b-4）
                # 参考 :2337 逐字文案回退：`error.message || '正文提取失败'`
                results.append({
                    "file_id": file_id,
                    "file_name": file_name,
                    "status": "error",
                    "error": str(error) or "正文提取失败",
                })

        status = "error" if any(r["status"] == "error" for r in results) else "success"
        completed = len(results)
        total = len(files)
        sub_stats = {"status": status, "completed": completed, "total": total}
        return SectionOutput(
            # 子对象：主编排 `_merge_metadata_stats` 会把它挂到
            # `metadata` 段 stats 的 `content_extraction` 键（与参考的
            # `metadataAnalysis.stats.contentExtraction` 同形）。
            stats=sub_stats,
            rows={"content_files": results},
            failed=status == "error",
            extraction=sub_stats,
        )

    return run_content_extraction


# ---------------------------------------------------------------- 文件项读取

def _file_id_of(file: Any) -> str:
    """文件 id：对齐参考 `stableFileId`（:118-120）——
    `file.id || sha1(file.file_path || file.file_name || '')`。

    正常路径下 `file_id` 恒存在（P3a 的 `list_files()` 产物 = `duplicate_check_files.file_id`），
    直接作 content 文件名与 `content_files` 行键，与 `duplicate_check_files.file_id`
    对齐 —— 下游 outline/content 段据此 join。**兜底分支**（id 缺失时对路径/文件名
    求 sha1）与参考逐字对齐：若沿用「直接用路径串当 id」会让路径分隔符进入文件名
    （可能逃逸 content_dir）且与参考 id 不一致。
    """
    if isinstance(file, Mapping):
        raw_id = file.get("file_id")
        if raw_id:
            return str(raw_id)
        seed = file.get("file_path") or file.get("file_name") or ""
    else:
        raw_id = getattr(file, "file_id", None)
        if raw_id:
            return str(raw_id)
        seed = getattr(file, "file_path", None) or getattr(file, "file_name", None) or ""
    return hashlib.sha1(str(seed).encode("utf-8")).hexdigest()


def _file_name_of(file: Any) -> str:
    if isinstance(file, Mapping):
        return file.get("file_name") or file.get("file_id") or ""
    return getattr(file, "file_name", None) or ""


def _file_path_of(file: Any) -> str:
    if isinstance(file, Mapping):
        return file.get("file_path")
    return getattr(file, "file_path", None)
