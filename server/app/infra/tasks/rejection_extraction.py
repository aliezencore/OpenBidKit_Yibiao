"""废标项提取任务的真实链（P4b-1）：单段 / 多段并发 / 合并 / 无结果重跑。

逐字对齐 `client/electron/services/bidAnalysisTask.cjs` 的
`runSingleBidAnalysisPromptTask` :230-235 / `runBidAnalysisPromptTaskOnce` :237-259 /
`runBidAnalysisPromptTask` :263-266（「提取走的是 runBidAnalysisPromptTask，不是 runText」）。

**登记偏离（P4b-1）**：服务端 `AiClient.chat(messages, use_vision)` 没有参考的
`response_format` / `logTitle` 参数——markdown 提取在参考里本来就不传 response_format，
故本链无缺口；`logTitle`（诊断日志标题）暂不可达，`aiConfig`（context_length_limit）
由调用方从 Settings 传入。JSON 任务的 response_format 缺口在 P4b-2 处理。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Mapping

from app.domain.rejection.extraction_prompt import (
    STABLE_SYSTEM_PROMPT,
    build_invalid_bid_and_rejection_items_prompt,
    build_messages,
    build_task_prompt,
    is_missing_markdown_result,
)
from app.domain.rejection.segment_merger import build_merge_messages
from app.domain.rejection.user_text_splitter import split_user_text_by_context_limit

EXTRACTION_TASK_LABEL = "无效标与废标项"
EXTRACTION_OUTPUT = "markdown"


def extraction_task_prompt() -> str:
    return build_task_prompt(build_invalid_bid_and_rejection_items_prompt(),
                             EXTRACTION_OUTPUT)


class RejectionExtractionRunner:
    """`rejection-items-extraction` 的真实实现（替换 P4a 的 stub）。"""

    def __init__(self, repo: Any, ai: Any, config: Mapping[str, Any] | None = None,
                 data_root: Any = None) -> None:
        self._repo = repo
        self._ai = ai
        self._config = dict(config or {})
        self._data_root = Path(data_root) if data_root is not None else None

    async def _chat(self, messages: list[dict[str, str]]) -> str:
        return await self._ai.chat(messages)

    async def _run_single(self, file_content: str, section_hint: Any) -> str:
        return await self._chat(
            build_messages(file_content, extraction_task_prompt(),
                           EXTRACTION_OUTPUT, section_hint))

    async def _run_once(self, file_content: str, file_segments: list[str] | None,
                        section_hint: Any) -> str:
        segments = (file_segments if isinstance(file_segments, list) and file_segments
                    else split_user_text_by_context_limit(file_content, self._config))
        if len(segments) <= 1:
            return await self._run_single(segments[0] if segments else file_content,
                                          section_hint)

        # 多段并发（参考 :244-254 的 Promise.all；logTitle 带「-第N段」→ 服务端暂不可达）
        segment_results = await asyncio.gather(*(
            self._run_single(segment_content, section_hint)
            for segment_content in segments))
        indexed = [{"segmentIndex": index + 1, "totalSegments": len(segments),
                    "content": content}
                   for index, content in enumerate(segment_results)]
        return await self._ai.chat(build_merge_messages(
            segment_results=indexed,
            task_prompt=extraction_task_prompt(),
            output=EXTRACTION_OUTPUT,
            system_prompt=STABLE_SYSTEM_PROMPT,   # 参考 :252 显式传 stableSystemPrompt
            section_hint=section_hint,
            task_label=EXTRACTION_TASK_LABEL,
        ))
        # 注：单段路径的 system 提示由 build_messages 承载（参考 :221-227 同构）。

    def _read_tender(self, project_id: str, relative_path: str) -> str:
        if not (self._data_root and relative_path):
            return ""
        target = (self._data_root / "projects" / project_id
                  / Path(*relative_path.split("/")))
        try:
            return target.read_text(encoding="utf-8")
        except OSError:
            return ""

    async def __call__(self, ctx: Any, payload: Mapping[str, Any],
                       previous_state: dict | None) -> None:
        project_id = str(payload.get("projectId") or "")
        # 有 tenderPath → 按 data_root 读盘（API 契约，payload 不放大段正文）；
        # 无路径时回退 tenderContent（单元测试/调用方直传的便利出口）。
        tender_content = await asyncio.to_thread(
            self._read_tender, project_id, str(payload.get("tenderPath") or ""))
        if not tender_content:
            tender_content = str(payload.get("tenderContent") or "")
        try:
            await self._run(ctx, payload, project_id, tender_content)
        except Exception as error:
            # 参考 :1547-1565：解析失败要把 extraction 落成 error 行（content 清空、
            # source='ai'、带 tenderSignature 与 error 文案），再让任务失败。
            message = str(error) or "无效与废标项解析失败"
            self._repo.save_extraction(project_id, {
                "status": "error", "content": "", "source": "ai",
                "tenderSignature": payload.get("tenderSignature"),
                "error": message,
            })
            raise

    async def _run(self, ctx: Any, payload: Mapping[str, Any], project_id: str,
                   tender_content: str) -> None:
        section_hint = payload.get("sectionHint")

        ctx.progress(5, "开始解析无效与废标项。")
        file_segments = split_user_text_by_context_limit(tender_content, self._config)
        content = await self._run_once_with_retry(tender_content, file_segments,
                                                  section_hint, ctx)
        if not str(content or "").strip():
            raise ValueError("无效标与废标项解析结果为空，请重新解析")
        self._repo.save_extraction(project_id, {
            "status": "success",
            "content": str(content or "").strip(),
            "source": "ai",
            "tenderSignature": payload.get("tenderSignature"),
        })
        ctx.progress(100, "解析完成")

    async def _run_once_with_retry(self, file_content: str, file_segments: list[str],
                                   section_hint: Any, ctx: Any) -> str:
        """参考 :263-266：markdown 整项无结果（==「未提取到」）时完整重跑一次。"""
        content = await self._run_once(file_content, file_segments, section_hint)
        if not is_missing_markdown_result(EXTRACTION_OUTPUT, content):
            return content
        ctx.log("解析结果为整体无结果，按参考语义重跑一次。")
        return await self._run_once(file_content, file_segments, section_hint)
