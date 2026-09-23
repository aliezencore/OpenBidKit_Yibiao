"""P4b-1：分段器与合并消息构造（逐字移植 userTextSplitter.cjs / segmentedAiResultMerger.cjs）。

分割真值来自 **oracle**（参考实现直跑，`tests/fixtures/segment-split-oracle.json`
随仓库提交——.superpowers 被 gitignore，夹具必须放外面，P3d-2 的教训）。
样本只用 BMP 字符（UTF-16 码元 vs Python 码点的登记偏离见
`user_text_splitter.py` 模块 docstring）。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.rejection.segment_merger import (
    build_merge_messages,
    format_segment_results,
)
from app.domain.rejection.user_text_splitter import split_user_text_by_context_limit

FIXTURE = Path(__file__).parent / "fixtures" / "segment-split-oracle.json"


def test_splitter_matches_oracle_segments():
    """Python 移植版与参考实现直跑的**分段结果逐段相等**（18 组 / 71 段）。"""
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert len(fixture["cases"]) == 18 and len(fixture["inputs"]) == 6
    for name, case in fixture["cases"].items():
        actual = split_user_text_by_context_limit(
            fixture["inputs"][case["inputKey"]], {},
            {"contextLengthLimit": case["contextLengthLimit"]})
        assert actual == case["segments"], f"oracle 不一致：{name}"


import pytest  # noqa: E402  (放在 skip 用例上方亦可)


def test_split_short_text_returns_single_segment():
    assert split_user_text_by_context_limit("", {}, {"contextLengthLimit": 300}) == [""]
    assert split_user_text_by_context_limit(
        "一句话。", {}, {"contextLengthLimit": 300}) == ["一句话。"]


def test_split_preserves_every_character():
    """无损切分：所有段拼接 == 原文（任何 limit 下都成立）。"""
    text = "第一章 总则。本条规定了；各项要求，包括：资格、业绩、人员。\n\n## 附则\n" * 40
    for limit in (200, 400, 1000, 5000):
        parts = split_user_text_by_context_limit(text, {}, {"contextLengthLimit": limit})
        assert "".join(parts) == text
        assert len(parts) >= 2 or limit >= len(text)


def test_split_avoids_cutting_inside_code_fence():
    """围栏内不可切——**前提是围栏能装进一个最大段**（超过最大段长时参考也只能硬切）。"""
    fence = "```text\n" + "keep together\n" * 5 + "```"
    text = ("前言。这种要求很常见。" * 10) + "\n" + fence + "\n" + ("后记。内容继续。" * 10)
    parts = split_user_text_by_context_limit(text, {}, {"contextLengthLimit": 300})
    fence_parts = [part for part in parts if "keep together" in part]
    assert len(fence_parts) == 1                      # 围栏整体落在同一段
    assert "```text" in fence_parts[0] and "```" in fence_parts[0]


def test_format_segment_results_is_reference_verbatim():
    results = [
        {"segmentIndex": 2, "totalSegments": 3, "content": " 乙 "},
        {"content": "丙"},
        None,
    ]
    assert format_segment_results(results) == (
        "## 第 2/3 段解析结果\n乙\n\n"
        "## 第 2/3 段解析结果\n丙\n\n"        # 缺 index/total 时兜底为序号/len(items)（含 None 项）
        "## 第 3/3 段解析结果\n"
    )


def test_build_merge_messages_is_reference_verbatim():
    messages = build_merge_messages(
        segment_results=[{"segmentIndex": 1, "totalSegments": 2, "content": "甲"}],
        task_prompt="原始任务要求全文",
        output="markdown",
        system_prompt="系统提示",
        section_hint="标段提示",
        task_label="无效投标与废标项",
    )
    assert [m["role"] for m in messages] == ["system", "system", "user"]
    assert messages[0]["content"] == "系统提示"
    assert messages[1]["content"] == "标段提示"
    user = messages[2]["content"]
    assert user.startswith("以下内容来自同一份招标文件按段分别解析后的结果。")
    assert "## 第 1/2 段解析结果\n甲" in user
    assert "原始任务要求全文" in user
    assert "当前合并任务：无效投标与废标项" in user
    assert "最终只返回整理后的 Markdown 内容" in user   # markdown 分支的输出要求

    json_messages = build_merge_messages(
        segment_results=[], task_prompt="p", output="json",
        system_prompt="", section_hint="", task_label="")
    assert [m["role"] for m in json_messages] == ["user"]   # system/sectionHint 为空不出现
    assert "最终只返回一个 JSON 对象" in json_messages[0]["content"]
