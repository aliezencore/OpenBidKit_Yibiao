"""P4b-1：废标项提取链单测（AI 假件 + 消息结构 + 分段/合并/重试）。

真值来源 `client/electron/services/bidAnalysisTask.cjs` 的
`buildMessages` :221-227 / `runSingleBidAnalysisPromptTask` :230-235 /
`runBidAnalysisPromptTaskOnce` :237-259 / `runBidAnalysisPromptTask` :263-266。
"""

from __future__ import annotations

import asyncio

import pytest

from app.domain.rejection.extraction_prompt import (
    MARKDOWN_MISSING_RESULT,
    STABLE_SYSTEM_PROMPT,
    build_invalid_bid_and_rejection_items_prompt,
    build_messages,
    build_task_prompt,
)
from app.infra.tasks.rejection_extraction import (
    RejectionExtractionRunner,
    extraction_task_prompt,
)
from tests.ai_fakes import ScriptedAI




def _run(coro):
    """pytest-asyncio 的 auto 模式让本套件里始终有 loop 在跑——sync 测试里用
    独立线程开新 loop 执行协程（与 P3a 时代「asyncio.run 直用」的差别所在）。"""
    import concurrent.futures

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class _Ctx:
    def __init__(self) -> None:
        self.progress_values: list[int] = []
        self.logs: list[str] = []

    def progress(self, percent: int, label: str | None = None) -> None:
        self.progress_values.append(percent)

    def log(self, message: str) -> None:
        self.logs.append(message)


def test_prompt_constants_are_reference_verbatim():
    assert MARKDOWN_MISSING_RESULT == "未提取到"
    prompt = build_invalid_bid_and_rejection_items_prompt()
    assert prompt.startswith("任务：提取并分析招标文件中的“无效投标”和“废标项”。")
    assert "概念边界：" in prompt and "输出格式：" in prompt
    assert "# 招标文件中明确提到的" in prompt and "# 此类标书还可能涉及的" in prompt


def test_build_task_prompt_appends_missing_rule_only_for_markdown():
    markdown = build_task_prompt("P", "markdown")
    assert markdown.startswith("P\n\n整体无结果规则：")
    assert f"只返回“{MARKDOWN_MISSING_RESULT}”" in markdown
    assert build_task_prompt("P", "json") == "P"      # json 任务不追加


def test_build_messages_structure():
    messages = build_messages("招标全文", "任务P", "markdown", None)
    assert [m["role"] for m in messages] == ["system", "user", "user"]
    assert messages[0]["content"] == STABLE_SYSTEM_PROMPT
    assert messages[1]["content"].startswith("以下是完整招标文件。")
    assert messages[1]["content"].endswith("\n\n招标全文")
    assert messages[2]["content"] == build_task_prompt("任务P", "markdown")


async def test_single_segment_makes_one_call_with_reference_messages():
    ai = ScriptedAI(["解析结果"])
    runner = RejectionExtractionRunner(_Repo(), ai, {}, data_root=None)
    ctx = _Ctx()
    _run(runner(ctx, {"projectId": "p1", "tenderContent": "短招标文件",
                      "tenderSignature": "sig"}, None))
    assert ai.chat_count == 1                          # 短文本不分段
    first = ai.calls[0]
    assert [m["role"] for m in first] == ["system", "user", "user"]
    assert first[1]["content"].endswith("\n\n短招标文件")
    assert "整体无结果规则：" in first[2]["content"]
    assert "## 无效投标" in first[2]["content"]        # discardedBids 的输出格式


async def test_multi_segment_runs_per_segment_then_merge():
    ai = ScriptedAI(["第1段结果", "第2段结果", "合并后的结果"])
    runner = RejectionExtractionRunner(_Repo(), ai, {"context_length_limit": 100})
    ctx = _Ctx()
    long_text = "第一章 要求。" * 60                    # ≈ 420 字 → 至少 3 段？由 limit 决定
    _run(runner(ctx, {"projectId": "p1", "tenderContent": long_text,
                                 "tenderSignature": "sig"}, None))
    # 每段一次 + 合并一次
    assert ai.chat_count >= 3
    # 每段消息的 user[1]（全文）都是**单段内容**（不是全文）
    segment_messages = ai.calls[:-1]
    for call in segment_messages:
        full_text_message = call[1]["content"]
        assert "以下是完整招标文件" in full_text_message
        assert len(full_text_message) < len(long_text) + 200
    # 合并调用：user 消息带分段块与合并要求
    merge_user = ai.calls[-1][-1]["content"]
    assert "分段解析结果：" in merge_user
    assert "## 第 1/" in merge_user and "## 第 " in merge_user
    assert "合并要求：" in merge_user
    # 最终落库的是合并结果
    ctx.progress_values[-1] == 100


async def test_markdown_missing_result_retries_once():
    ai = ScriptedAI([MARKDOWN_MISSING_RESULT, "第二次有内容"])
    runner = RejectionExtractionRunner(_Repo(), ai, {})
    ctx = _Ctx()
    _run(runner(ctx, {"projectId": "p1", "tenderContent": "招标内容",
                                 "tenderSignature": "sig"}, None))
    assert ai.chat_count == 2                          # 无结果 → 完整重跑一次
    assert "按参考语义重跑一次" in "".join(ctx.logs)


async def test_runner_saves_extraction_success_with_ai_source():
    repo = _Repo()
    ai = ScriptedAI(["  # 招标文件中明确提到的\n## 无效投标\n- 无资质  "])
    runner = RejectionExtractionRunner(repo, ai, {})
    _run(runner(_Ctx(), {"projectId": "p1", "tenderContent": "招标内容",
                                 "tenderSignature": "sig-1"}, None))
    assert repo.saved == {
        "status": "success",
        "content": "# 招标文件中明确提到的\n## 无效投标\n- 无资质",  # 已 strip
        "source": "ai",                                 # 参考 :1584 的 source
        "tenderSignature": "sig-1",
    }


async def test_runner_raises_on_empty_content():
    ai = ScriptedAI(["   "])
    runner = RejectionExtractionRunner(_Repo(), ai, {})
    with pytest.raises(ValueError, match="解析结果为空"):
        _run(runner(_Ctx(), {"projectId": "p1", "tenderContent": "招标内容",
                            "tenderSignature": "s"}, None))


class _Repo:
    def __init__(self) -> None:
        self.saved: dict | None = None

    def save_extraction(self, project_id: str, extraction: dict) -> None:
        self.saved = extraction
