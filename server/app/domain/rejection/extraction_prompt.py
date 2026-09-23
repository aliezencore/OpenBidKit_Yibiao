"""招标解析的 prompt 与消息构造（P4b-1，纯函数）。

常量与函数逐字移植 `client/electron/services/bidAnalysisTask.cjs`：
- `MARKDOWN_MISSING_RESULT` :6、`stableSystemPrompt` :12-17
- `buildInvalidBidAndRejectionItemsPrompt` :36-63（discardedBids 任务的 prompt）
- `buildTaskPrompt` :194-201（markdown 任务追加「整体无结果规则」）
- `buildTenderContextMessages` :210-219 / `buildMessages` :221-227

**注意**：本文件只放 `discardedBids` 任务需要的部分；`tasks` 里其他解析项的 prompt
属 P4 的后续范围，不要顺手搬。
"""

from __future__ import annotations

from typing import Any, Mapping

MARKDOWN_MISSING_RESULT = "未提取到"

STABLE_SYSTEM_PROMPT = """你是专业的投标资料分析助手。请严格基于用户提供的上下文完成提取和总结。

通用要求：
1. 保持信息全面、准确，优先使用用户提供上下文中的内容；除非具体任务明确要求或允许根据经验补充，否则不要自行编造
2. 已提取到相关内容但局部信息没有提及时，明确写“没有提及”
3. 只输出最终结果，不输出过程、提示语或客套话
4. 始终使用简体中文"""

SECTION_HINT_MODE_MULTIPLE = "multiple"


def build_invalid_bid_and_rejection_items_prompt() -> str:
    """参考 :36-63（discardedBids，`output: 'markdown'`，required: false）。"""
    return """任务：提取并分析招标文件中的“无效投标”和“废标项”。

概念边界：
1. “无效投标”指投标人、投标文件、签章密封、递交时间、报价、保证金、资格条件、实质性响应等原因导致投标被认定为无效、否决、不予受理或按无效响应处理的情形。
2. “废标项”指可能导致项目废标、采购失败、重新招标、终止评审、有效投标人不足或实质性响应不足的条款或风险项。
3. 招标文件使用“否决投标”“投标无效”“不予受理”“无效响应”“重大偏差”“实质性偏离”“废标情形”等同义表达时，也要按上述边界归类。

输出要求：
1. 必须明确区分“无效投标”和“废标项”。
2. “招标文件中明确提到的”只能提取招标文件中明确出现或同义表达的内容，尽量保留招标文件中的关键句；如果没有提及，写“招标文件未提及”。
3. “此类标书还可能涉及的”需要根据你的经验，补充招标文件中未明确提及、但结合本招标文件类型和招投标经验判断非常重要的高风险遗漏项。
4. 不要罗列所有常见可能项，不要输出泛泛的通用清单；每个小节最多输出 3-5 条。
5. 不要使用表格，使用 Markdown 列表。
6. 仅输出下方格式，不要输出解释、过程或额外段落。
7. 不要输出三重引号、代码块标记或其他格式包裹符。

输出格式：
# 招标文件中明确提到的

## 无效投标
- ...

## 废标项
- ...

# 此类标书还可能涉及的

## 无效投标
- ...

## 废标项
- ..."""


def build_task_prompt(task_prompt: str, output: str) -> str:
    """参考 :194-201：markdown 任务追加「整体无结果规则」（**只对 markdown**）。"""
    if output != "markdown":
        return task_prompt
    return f"""{task_prompt}

整体无结果规则：仅当当前任务完全未提取到任何相关内容时，只返回“{MARKDOWN_MISSING_RESULT}”，不要附加标题、标点、解释或其他文字。只要提取到任何有效内容，就正常返回结果；局部字段或局部分类缺失时写“没有提及”，不要使用“{MARKDOWN_MISSING_RESULT}”。"""


def is_missing_markdown_result(output: str, content: Any) -> bool:
    """参考 :203-205：markdown 任务且结果恰好等于「未提取到」。"""
    return output == "markdown" and str(content or "").strip() == MARKDOWN_MISSING_RESULT


def build_tender_context_messages(file_content: Any, section_hint: Any = None) -> list[dict[str, str]]:
    """参考 :210-219。`section_hint` 为空时不出现（多标段属后续范围）。"""
    messages = [{"role": "system", "content": STABLE_SYSTEM_PROMPT}]
    if section_hint:
        messages.append({"role": "system", "content": section_hint})
    messages.append({
        "role": "user",
        "content": "以下是完整招标文件。后续任务需要基于这份招标文件完成；"
                   "如后续消息提供补充上下文，请按具体任务要求综合使用：\n\n"
                   f"{file_content}",
    })
    return messages


def build_messages(file_content: Any, task_prompt: str, output: str,
                   section_hint: Any = None) -> list[dict[str, str]]:
    """参考 `buildMessages` :221-227。"""
    messages = build_tender_context_messages(file_content, section_hint)
    messages.append({"role": "user", "content": build_task_prompt(task_prompt, output)})
    return messages


def discard_bid_payload_ok(payload: Mapping[str, Any] | None) -> bool:
    """占位守卫（无业务含义）：P4b-2 会替换为按 payload 分派的逻辑。"""
    return bool(payload)
