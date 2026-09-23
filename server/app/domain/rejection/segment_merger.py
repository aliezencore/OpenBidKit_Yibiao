"""分段结果合并的消息构造（P4b-1，纯函数）。

逐字移植 `client/electron/utils/segmentedAiResultMerger.cjs`。
AI 调用本身不在本模块（消息组装完交给 `AiClient.chat`）。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def format_segment_results(segment_results: Sequence[Mapping[str, Any]] | None) -> str:
    """参考 :1-10：缺 index/total 时兜底为序号/总数；None 项计入但不取值。"""
    items = list(segment_results) if isinstance(segment_results, list) else []
    formatted = []
    for index, item in enumerate(items):
        segment_index = (item or {}).get("segmentIndex") or index + 1
        total_segments = (item or {}).get("totalSegments") or len(items)
        content = str((item or {}).get("content") or "").strip()
        formatted.append(f"## 第 {segment_index}/{total_segments} 段解析结果\n{content}")
    return "\n\n".join(formatted)


def build_merge_messages(*, segment_results: Sequence[Mapping[str, Any]] | None,
                         task_prompt: Any, output: str,
                         system_prompt: Any = None, section_hint: Any = None,
                         task_label: Any = None) -> list[dict[str, str]]:
    """参考 `buildMergeMessages` :12-44（合并要求七条**逐字**，不得改写）。"""
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if section_hint:
        messages.append({"role": "system", "content": section_hint})

    output_requirement = (
        "最终只返回一个 JSON 对象，不要输出 Markdown、代码块、解释或额外文字。"
        if output == "json" else
        "最终只返回整理后的 Markdown 内容，不要输出解释、过程或额外提示语。")

    messages.append({
        "role": "user",
        "content": f"""以下内容来自同一份招标文件按段分别解析后的结果。每段结果只代表该片段内的信息，不代表整份文件的完整结论。

当前合并任务：{task_label or '招标文件解析结果合并'}

合并要求：
1. 如果某段写“没有提及”“原文未提及”“本段未提及”或整段只有“未提取到”，只表示该片段没有相关信息；如果其他片段提供了有效信息，应以有效信息为准。
2. 删除重复、空泛、冲突的片段性表述，保留更完整、更具体的信息。
3. 保留所有有价值、可用于最终结果的信息，不要遗漏分段结果中的明确内容。
4. 不要新增分段结果中没有的信息，不要自行编造。
5. 如果所有分段都没有有效信息，遵守原始任务中的整体无结果规则。
6. 最终输出必须符合原始任务要求。
7. {output_requirement}

原始任务要求：
{task_prompt}

分段解析结果：
{format_segment_results(segment_results)}""",
    })
    return messages
