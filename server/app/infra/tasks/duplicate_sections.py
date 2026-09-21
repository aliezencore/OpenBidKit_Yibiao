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
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.domain.duplicate.content import split_content_sentences
from app.domain.duplicate.outline import (
    build_outline_comparison,
    build_outline_items,
    split_tender_sentences,
)
from app.domain.duplicate.tender_matcher import (
    build_duplicate_sentences,
    build_tender_source_matcher,
)
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


# ---------------------------------------------------------------- 段内共享辅助

def _half_up(value: float) -> int:
    """half-up 取整（对齐 JS `Math.round` / `math.floor(x + 0.5)`，计划 R-P3b-7）。"""
    return math.floor(value + 0.5)


def _content_file_rows(section_ctx: SectionRunContext) -> list[dict[str, Any]]:
    """从段上下文取出正文提取产物（`content_files` 行）。

    `section_ctx.content_files` 是 `content_extraction` 角色的 `SectionOutput`
    （经 `with_dependency(CONTENT_FILES_DEPENDENCY, ...)` 注入）；其 `rows`
    携带 `content_files` 列表。缺失/失败 → 空列表（下游 `read_content_markdown`
    据此抛「正文内容尚未成功提取」）。
    """
    cf = section_ctx.content_files
    if isinstance(cf, SectionOutput):
        return list(getattr(cf, "rows", {}).get("content_files") or [])
    return []


def _read_content_markdown(content_rows: Sequence[dict[str, Any]], file: Any) -> str:
    """对齐参考 `readContentMarkdown`（svc.cjs:2405-2410）。

    在 `content_files` 行里找本文件的 success 条目并读其 `content_path`；
    找不到（含非 success / 缺 content_path）→ 抛**逐字**异常，由调用方决定
    「目录分析」或「正文比对」语义下如何处理。
    """
    file_id = _file_id_of(file)
    item = next(
        (entry for entry in content_rows
         if entry.get("file_id") == file_id
         and entry.get("status") == "success"
         and entry.get("content_path")),
        None)
    if item is None:
        raise RuntimeError("正文内容尚未成功提取，无法进行目录分析")
    return Path(item["content_path"]).read_text(encoding="utf-8")


def _read_combined_tender_markdown(content_rows: Sequence[dict[str, Any]],
                                   tender_files: Sequence[Any]) -> str:
    """对齐参考 `readCombinedTenderMarkdown`（svc.cjs:2356-2363）：逐 tender 文件读
    content markdown，`\\n\\n` join 非空片段。任一文件读取抛错 → 向上抛（由调用方 catch
    广播白名单失败、继续比对，R-P3b-4）。"""
    parts: list[str] = []
    for file in tender_files:
        markdown = _read_content_markdown(content_rows, file)
        if str(markdown or "").strip():
            parts.append(str(markdown).strip())
    return "\n\n".join(parts)


def _report_progress(section_ctx: SectionRunContext, percent: int, message: str) -> None:
    """段内进度回调（仅当编排器注入了 `section_progress` 时上报）。"""
    if section_ctx.section_progress is not None:
        section_ctx.section_progress(int(percent), message)


def _broadcast(section_ctx: SectionRunContext, message: str) -> None:
    """广播一条中段文案：经 `ctx.log` 留痕（测试可断言），并写该段 running 行 message。

    参考里中段广播只更新 `message`、不动 `progress`；本端进度恒由后续
    `section_progress` 调用接管，这里固定带一个初始进度 5（与参考初始 `progress: 5`
    同义）仅用于占位。
    """
    if section_ctx.ctx is not None:
        section_ctx.ctx.log(message)
    if section_ctx.section_progress is not None:
        section_ctx.section_progress(5, message)


# ---------------------------------------------------------------- outline 段

def build_outline_runner(data_root: Path | str):
    """构造 `outline` 段的 runner 闭包（对齐 `runOutlineAnalysis` svc.cjs:2412-2502）。

    行为（R-P3b-4 / 逐字文案）：
    - tender 白名单：`readCombinedTenderMarkdown` → `split_tender_sentences`；读不到/失败 →
      广播「招标文件句子白名单生成失败，继续对比投标文件目录：{error}」（:2426）继续。
    - 逐 bid：`read_content_markdown`（缺 success 条目抛「正文内容尚未成功提取，
      无法进行目录分析」:2408）→ `build_outline_items` → 成功/错误行（错误行 `items: []`，继续）。
    - 逐文件后 `section_progress(round((completed/total)*80), "目录提取 {completed}/{total}")`；
      total=0 → 80（:2469 三元式）。
    - `build_outline_comparison` → `outline_items` / `outline_groups` / `outline_pairwise`
      三类落库行；`failed = any(文件 error)`；`message = 部分文件目录分析失败/目录分析完成`
      （:2483）；`extraction = {status, completed: len(results), total: len(bid_files)}`（:2485）；
      stats 含 **camelCase** 的 `tenderSentenceCount` / `tenderMatchedItemCount`（:2487-2488）
      与 `files`（逐文件摘要，:2486）——键名以参考写入侧与仓储读取侧为准，非计划措辞的 snake。
    """

    async def run_outline(section_ctx: SectionRunContext) -> SectionOutput:
        bid_files = list(section_ctx.bid_files)
        tender_files = list(section_ctx.tender_files)
        content_rows = _content_file_rows(section_ctx)
        total = len(bid_files)

        # 1) 招标白名单
        tender_sentences: list[dict[str, str]] = []
        if tender_files:
            try:
                tender_markdown = _read_combined_tender_markdown(content_rows, tender_files)
                tender_sentences = split_tender_sentences(tender_markdown)
            except Exception as error:  # noqa: BLE001
                _broadcast(
                    section_ctx,
                    f"招标文件句子白名单生成失败，继续对比投标文件目录：{error}")

        # 2) 逐 bid 文件
        results: list[dict[str, Any]] = []
        for file in bid_files:
            file_id = _file_id_of(file)
            file_name = _file_name_of(file)
            try:
                markdown = _read_content_markdown(content_rows, file)
                extracted = build_outline_items(markdown, tender_sentences)
                tender_matched_count = sum(
                    1 for item in extracted["items"] if item.get("from_tender"))
                results.append({
                    "file_id": file_id,
                    "file_name": file_name,
                    "status": "success",
                    "source": extracted["source"],
                    "confidence": extracted["confidence"],
                    "item_count": len(extracted["items"]),
                    "tender_matched_count": tender_matched_count,
                    "items": extracted["items"],
                })
            except Exception as error:  # noqa: BLE001 —— 单文件失败不连坐
                results.append({
                    "file_id": file_id, "file_name": file_name, "status": "error",
                    "item_count": 0, "tender_matched_count": 0, "items": [],
                    "error": str(error) or "目录提取失败"})
            # 段内进度（参考 :2469）
            completed = len(results)
            progress = _half_up((completed / total) * 80) if total else 80
            _report_progress(section_ctx, progress, f"目录提取 {completed}/{total}")

        # 3) 跨文件比对
        comparison = build_outline_comparison(results)
        failed = any(r["status"] == "error" for r in results)
        message = "部分文件目录分析失败" if failed else "目录分析完成"

        outline_items_rows: list[dict[str, Any]] = []
        for r in results:
            for item in r.get("items") or []:
                outline_items_rows.append({
                    # 落库行键名与 `replace_outline_items` 一致；`item_id` / `parent_item_id`
                    # 传**未 scoped** 的 id，scoped 形式由仓储 `_scope_outline_row` 负责。
                    "item_id": item["id"],
                    "file_id": r["file_id"],
                    "parent_item_id": item.get("parent_id"),
                    "level": item.get("level"),
                    "number": item.get("number"),
                    "title": item.get("title"),
                    "normalized_title": item.get("normalized_title"),
                    "path_titles": item.get("path_titles"),
                    "normalized_path": item.get("normalized_path"),
                    "source": item.get("source"),
                    "confidence": item.get("confidence"),
                    "sort_order": item.get("order"),
                    "from_tender": bool(item.get("from_tender")),
                    "matched_tender_sentence": item.get("matched_tender_sentence"),
                })

        outline_groups_rows: list[dict[str, Any]] = []
        for group in comparison["duplicateGroups"]:
            outline_groups_rows.append({
                "group_id": group["id"],
                "type": group["type"],
                "title": group["title"],
                "score": group["score"],
                "file_ids": group["file_ids"],
                "item_ids": group["item_ids"],
                "paths": group["paths"],
            })

        outline_pairwise_rows: list[dict[str, Any]] = []
        for pair in comparison["pairwiseSimilarities"]:
            outline_pairwise_rows.append({
                "file_a_id": pair["file_a_id"],
                "file_b_id": pair["file_b_id"],
                "score": pair["score"],
                "title_overlap": pair["title_overlap"],
                "path_overlap": pair["path_overlap"],
                "order_similarity": pair["order_similarity"],
                "shared_count": pair["shared_count"],
                "risk": pair["risk"],
            })

        extraction = {
            "status": "error" if failed else "success",
            "completed": len(results),
            "total": total,
        }
        stats = {
            # 键名必须与**参考写入侧**（:2487-2488）和**仓储读取侧**
            # （`_load_outline_analysis` 的 `stats.get("tenderSentenceCount")`）一致：
            # camelCase。计划 Task 5 :334 写的 snake_case 是计划措辞的偏差——
            # 该 stats 会被 `load_analysis` 原样读成 `/state` 的
            # `outlineAnalysis.tenderSentenceCount`，snake 键读出来恒 0。
            "tenderSentenceCount": len(tender_sentences),
            "tenderMatchedItemCount": sum(
                r.get("tender_matched_count", 0) for r in results),
            # `files`：逐文件摘要（参考 :2486 终态的 `files: results`）。读取侧
            # `_load_outline_analysis`（repositories/duplicate.py:931）用它建
            # `summaryByFile`，逐文件决定 `/state` 的 `status`/`error`/`source`/
            # `confidence`。**缺这个键的后果**：兜底是 store.cjs:740 的
            # `summary.status || (items.length ? 'success' : 'pending')` ——
            # 失败文件（items 为空）会被误报成 `pending` 且丢掉错误信息。
            #
            # **有意偏离**：省略每条记录的 `items` 字段。参考把整份 items 数组塞进
            # stats（:2471/:2486），但读取侧是从 `duplicate_check_outline_items`
            # 表重建的（store.cjs:725 的 `itemsByFile`），stats 里的 `items` 从不被读
            # ——属 wire-invisible；保留它会把这批数据在 stats_json 里重复写一遍。
            "files": [
                {k: v for k, v in r.items() if k != "items"} for r in results
            ],
        }
        return SectionOutput(
            stats=stats,
            rows={
                "outline_items": outline_items_rows,
                "outline_groups": outline_groups_rows,
                "outline_pairwise": outline_pairwise_rows,
            },
            failed=failed,
            message=message,
            extraction=extraction,
        )

    return run_outline


# ---------------------------------------------------------------- content 段

def build_content_runner(data_root: Path | str):
    """构造 `content` 段的 runner 闭包（对齐 `runContentDuplicateAnalysis`
    svc.cjs:2504-2630）。

    行为（R-P3b-4 / 逐字文案/公式）：
    - tender matcher：`readCombinedTenderMarkdown` → `split_content_sentences` →
      `build_tender_source_matcher`；失败 → 广播「招标文件句子白名单生成失败，
      继续比对投标正文：{error}」（:2519）。
    - 逐 bid（按序全局递增 `first_order`）：`split_content_sentences` → 逐句
      `matcher.match`（`reason` 计数）；未命中 → 收集进 local map。
      每 500 句 `section_progress(min(89, half_up(5 + ((file_index + processed/len)/
      bid_count)*80)), "正文比对 {i+1}/{n}（{processed}/{len} 句）")`（:2562，`Math.round`
      = half-up）+ `await asyncio.sleep(0)`（:2570 让出）。**文件级 catch → 只广播
      `{file_name} 正文比对失败：{error}`（:2588），不产 error 行、不置 failed**。文件尾
      `section_progress(half_up((10 if global else 5) + (i+1)/n*80), "正文比对 {i+1}/{n}")`（:2598）。
    - `build_duplicate_sentences(global)` → `content_duplicates` / `content_occurrences`；
      `message = 正文比对完成`（:2611）；`failed = False` 恒；`extraction =
      {status: success, completed: bid_count, total: bid_count}`；stats 含 **camelCase** 的
      `tenderSentenceCount` / `tenderMatchedSentenceCount` / `totalSentenceCount`（:2613-2615）
      + `tender_match_reason_counts`（:2625，**参考此处自己就是 snake，逐字保留**）。
    """

    async def run_content(section_ctx: SectionRunContext) -> SectionOutput:
        bid_files = list(section_ctx.bid_files)
        tender_files = list(section_ctx.tender_files)
        content_rows = _content_file_rows(section_ctx)
        bid_count = len(bid_files)

        # 1) 招标 matcher
        tender_matcher = build_tender_source_matcher([])
        tender_match_reason_counts: dict[str, int] = {}
        if tender_files:
            try:
                tender_markdown = _read_combined_tender_markdown(content_rows, tender_files)
                tender_matcher = build_tender_source_matcher(
                    split_content_sentences(tender_markdown))
            except Exception as error:  # noqa: BLE001
                _broadcast(
                    section_ctx,
                    f"招标文件句子白名单生成失败，继续比对投标正文：{error}")

        global_sentences: dict[str, dict[str, Any]] = {}
        total_sentence_count = 0
        tender_matched_sentence_count = 0
        first_order = 0

        for file_index, file in enumerate(bid_files):
            file_id = _file_id_of(file)
            file_name = _file_name_of(file)
            try:
                markdown = _read_content_markdown(content_rows, file)
                sentences = split_content_sentences(markdown)
                total_sentence_count += len(sentences)
                local: dict[str, dict[str, Any]] = {}
                for sentence_index, sentence in enumerate(sentences):
                    match_result = tender_matcher.match({
                        "sentence": sentence["sentence"],
                        "normalized": sentence["normalized"],
                    })
                    if match_result:
                        tender_matched_sentence_count += 1
                        reason = match_result.get("reason") or "unknown"
                        tender_match_reason_counts[reason] = (
                            tender_match_reason_counts.get(reason, 0) + 1)
                    else:
                        normalized = sentence["normalized"]
                        current = local.get(normalized) or {
                            "sentence": sentence["sentence"], "count": 0,
                            "order": first_order}
                        current["count"] += 1
                        if normalized not in local:
                            first_order += 1  # order 在首次出现时分配（对齐 :2553 firstOrder++）
                        local[normalized] = current

                    processed = sentence_index + 1
                    if processed % 500 == 0 and processed < len(sentences):
                        progress = _half_up(
                            5 + ((file_index + processed / len(sentences)) / bid_count) * 80)
                        progress = min(89, progress)
                        _report_progress(
                            section_ctx, progress,
                            f"正文比对 {file_index + 1}/{bid_count}"
                            f"（{processed}/{len(sentences)} 句）")
                        await asyncio.sleep(0)  # 让出事件循环（对应 :2570 setImmediate）

                # local → global（按 normalized 合并；file_ids 去重、occurrences 计数）
                for normalized, item in local.items():
                    global_item = global_sentences.get(normalized) or {
                        "sentence": item["sentence"], "normalized": normalized,
                        "file_ids": [], "occurrences": {}, "first_order": item["order"]}
                    if file_id not in global_item["file_ids"]:
                        global_item["file_ids"].append(file_id)
                    global_item["occurrences"][file_id] = item["count"]
                    global_sentences[normalized] = global_item
            except Exception as error:  # noqa: BLE001 —— 单文件失败只广播，不连坐
                _broadcast(section_ctx, f"{file_name} 正文比对失败：{error}")

            # 文件尾进度（参考 :2598）
            progress = _half_up(
                (10 if len(global_sentences) else 5)
                + (file_index + 1) / bid_count * 80) if bid_count else 85
            _report_progress(
                section_ctx, progress,
                f"正文比对 {file_index + 1}/{bid_count}")

        duplicates = build_duplicate_sentences(global_sentences)
        content_duplicates_rows = [{
            "duplicate_id": d["id"],
            "sentence": d["sentence"],
            "normalized": d["normalized"],
            "file_ids": d["file_ids"],
            "first_order": d["first_order"],
        } for d in duplicates]
        content_occurrences_rows = []
        for d in duplicates:
            for fid in d["file_ids"]:
                content_occurrences_rows.append({
                    "duplicate_id": d["id"],
                    "file_id": fid,
                    "occurrence_count": d.get("occurrences", {}).get(fid, 0),
                })

        message = "正文比对完成"
        extraction = {"status": "success", "completed": bid_count, "total": bid_count}
        stats = {
            # camelCase 对齐参考 :2613-2615 与仓储读取侧（同上）。
            "tenderSentenceCount": tender_matcher.tender_sentence_count,
            "tenderMatchedSentenceCount": tender_matched_sentence_count,
            "totalSentenceCount": total_sentence_count,
            # 例外：参考 :2625 自己这里就是 snake_case（逐字保留，不"修正"）。
            "tender_match_reason_counts": tender_match_reason_counts,
        }
        return SectionOutput(
            stats=stats,
            rows={
                "content_duplicates": content_duplicates_rows,
                "content_occurrences": content_occurrences_rows,
            },
            failed=False,
            message=message,
            extraction=extraction,
        )

    return run_content
