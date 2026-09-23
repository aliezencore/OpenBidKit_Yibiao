"""废标项检查的三轮 prompt 与 findings 归一化（P4b-2，纯函数）。

逐字移植 `client/electron/services/rejectionCheckTask.cjs`：
`normalizeFindingType` :66-69 / `normalizeSeverity` :71-75 /
`buildCommonRejectionCheckMessages` :76-104 / 三轮 builder :112-195 /
`normalizeRejectionCheckFindings` :243-267。

**rolling 变体（`shouldUseSegmentedRejectionFlow` → `runRollingRejectionItemCheck`，
:392 / :420 / :680 / :1323）未搬**——本模块只实现直连三轮，调用方无条件走直连
（登记偏离：超长投标包的行为与参考不同，P4b 后续补 rolling）。
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Mapping, Sequence

from app.domain.rejection.typo_check import (
    format_bid_documents_for_prompt,
    get_array_payload,
    get_bid_document_id_from_item,
    normalize_text,
)

SEVERITY_ORDER = ("high", "medium", "low")


def normalize_finding_type(value: Any) -> str:
    """参考 :66-69：含「无效」字样 → invalidBid，否则 rejectionItem。"""
    raw = str(value or "").strip()
    if raw == "invalidBid" or "无效" in raw:
        return "invalidBid"
    return "rejectionItem"


def normalize_severity(value: Any) -> str:
    """参考 :71-75：**中文关键词也映射**（含「高」→ high、「低」→ low）。"""
    raw = str(value or "").strip().lower()
    if raw == "high" or "高" in raw:
        return "high"
    if raw == "low" or "低" in raw:
        return "low"
    return "medium"


def build_common_rejection_check_messages(
        input_params: Mapping[str, Any]) -> list[dict[str, str]]:
    """参考 :76-104：检查项 + 可选自定义检查项 + 投标原文（含「重要限制」段）。"""
    messages = [{
        "role": "user",
        "content": f"""【废标项检查输入 v1｜检查项】
以下内容来自招标文件“无效投标”和“废标项”解析结果。后续任务必须优先基于这些检查口径，不要自行扩大到无法从电子投标文件判断的事项。

{input_params.get('invalidBidAndRejectionItems')}""",
    }]
    custom = str(input_params.get("customCheckItems") or "").strip()
    if custom:
        messages.append({
            "role": "user",
            "content": f"""【废标项检查输入 v1｜自定义检查项】
以下是用户补充的电子投标文件检查关注点。仅在能从电子投标文件正文、目录、附件文本或材料内容中判断时使用；如果涉及签字、盖章、密封、现场递交、纸质正副本等纸质或线下事项，必须忽略。

{custom}""",
        })
    documents = input_params.get("bidDocuments")
    documents = documents if isinstance(documents, list) else []
    messages.append({
        "role": "user",
        "content": f"""【废标项检查输入 v2｜投标文件原文】
以下是本次需要一起检查的多份投标文件 Markdown 原文。每份文件都有唯一 bidDocumentId。后续每条风险必须明确返回所属 bidDocumentId，只能引用对应投标文件中可见的内容作为证据。

重要限制：当前原文由文本解析得到，图片、扫描件、截图、附件页等非文本内容可能已被过滤或无法完整呈现。检查材料缺失时，不得要求必须看到图片内容、扫描件正文或附件正文；如果投标文件中已经出现某项材料的章节标题、目录项、附件标题、材料清单项、表格条目、页码线索、图片占位线索或其他可表明该材料已插入/已提交的结构性文本线索，应视为该材料至少存在提交线索。

{format_bid_documents_for_prompt({"bidDocuments": documents})}""",
    })
    return messages


def build_rejection_check_analysis_messages(
        input_params: Mapping[str, Any]) -> list[dict[str, str]]:
    """参考 :112-131：第一轮（分析检查范围，不输出风险列表）。"""
    return [*build_common_rejection_check_messages(input_params), {
        "role": "user",
        "content": """【废标项检查任务 v1｜第一轮：分析】
请先分析检查范围，不要输出最终风险列表。

分析要求：
1. 梳理“无效投标”和“废标项”中哪些能通过电子投标文件内容判断。
2. 明确排除签字、盖章、密封、纸质正副本、现场递交、开标现场授权到场、纸质文件封装等纸质或线下事项。
3. 结合各投标文件目录和正文结构，指出重点核查章节、附件、报价、资格材料、技术/商务响应位置，并说明是否存在不同文件需要分别关注的风险。
4. 判断材料是否缺失时，先识别章节标题、目录项、附件标题、材料清单项、表格条目、页码线索、图片占位线索等结构性文本线索；只要存在这类线索，就不能因为图片或扫描件正文不可见而判定缺失。
5. 如果某项检查需要外部事实、现场行为或纸质原件才能判断，标记为“不纳入电子文件检查”。
6. 仅输出分析结论，使用简体中文。""",
    }]


def build_rejection_check_inspection_messages(
        input_params: Mapping[str, Any], analysis: Any) -> list[dict[str, str]]:
    """参考 :131-151：第二轮（基于第一轮分析逐项检查，输出初步 Markdown 列表）。"""
    return [*build_common_rejection_check_messages(input_params), {
        "role": "user", "content": f"""【废标项检查任务 v1｜第一轮分析结果】
{analysis}"""}, {
        "role": "user",
        "content": """【废标项检查任务 v1｜第二轮：检查】
请基于第一轮分析逐项检查所有电子投标文件，输出初步风险列表。

检查要求：
1. 每条风险必须有某一份投标文件中的明确证据，并写明 bidDocumentId；证据不足不要输出。
2. 不检查签字、盖章、密封、纸质正副本、现场递交、纸质原件等事项。
3. 重点关注实质性条款未响应、必要章节或附件缺失、资格材料明显缺失/过期、报价或关键承诺前后矛盾、技术/商务偏离未说明等电子正文可判断风险。
4. 判断“材料缺失”时，只有在目录、章节标题、附件标题、材料清单、正文、表格和其他结构性线索中均找不到对应材料痕迹，才可以输出疑似缺失；不得仅因图片内容、扫描件正文或附件正文不可见而输出缺失风险。
5. 如果投标文件中已有对应材料的结构性文本线索，应视为至少有提交线索，可提示人工复核内容完整性，但不要判定为缺失。
6. 区分风险类型：无效标使用 invalidBid，废标项使用 rejectionItem。
7. 暂不要求 JSON，可用结构化 Markdown 输出初步结果。""",
    }]


def build_rejection_check_final_messages(input_params: Mapping[str, Any], analysis: Any,
                                         draft_findings: Any) -> list[dict[str, str]]:
    """参考 :153-195：第三轮（去重合并补漏，最终 JSON）。"""
    return [*build_common_rejection_check_messages(input_params), {
        "role": "user", "content": f"""【废标项检查任务 v1｜第一轮分析结果】
{analysis}"""}, {
        "role": "user", "content": f"""【废标项检查任务 v1｜第二轮初步检查结果】
{draft_findings}"""}, {
        "role": "user",
        "content": """【废标项检查任务 v1｜第三轮：补充与定稿】
请对第二轮结果去重、合并、补漏，并删除不符合要求的条目，最终只输出 JSON。

定稿规则：
1. 只保留能从电子投标文件原文判断且有明确证据的风险。
2. 删除签字、盖章、密封、纸质正副本、现场递交、纸质原件、开标现场行为等纸质或线下事项。
3. 删除只有猜测、没有投标文件证据、或仅凭常识无法确认的条目。
4. 删除仅因图片内容、扫描件正文或附件正文不可见而产生的材料缺失条目；如果投标文件中存在对应材料的章节标题、目录项、附件标题、材料清单项、表格条目、页码线索、图片占位线索或其他结构性文本线索，不得将该材料定稿为缺失。
5. 同一问题合并为一条，标题简短明确。
6. severity 只能是 high、medium、low；type 只能是 invalidBid 或 rejectionItem。
7. 如果没有符合条件的风险，返回 {"findings":[]}。

JSON 格式：
{
  "findings": [
    {
      "bidDocumentId": "对应投标文件的 bidDocumentId，例如 bid-xxxx",
      "type": "invalidBid",
      "severity": "high",
      "title": "不超过 28 个中文字符的风险标题",
      "summary": "一句话概括风险",
      "requirement": "对应检查依据或招标要求，尽量引用原检查项",
      "bidEvidence": "投标文件中的明确证据、章节、原文摘录或缺失位置说明",
      "riskReason": "为什么该证据可能构成无效标或废标项风险",
      "suggestion": "建议用户如何处理或复核"
    }
  ]
}

仅输出 JSON，不要输出 Markdown、代码块或解释。"""}]


def normalize_rejection_check_findings(
        parsed: Any,
        bid_documents: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """参考 :243-267：type/severity 归一（含中文关键词）、summary 回落 title、
    requirement/suggestion 缺省提示语；必填 `bidDocumentId/title/bidEvidence/riskReason`。"""
    bid_document_ids = {document["id"] for document in bid_documents
                        if document.get("id")}
    findings: list[dict[str, Any]] = []
    for item in get_array_payload(parsed, ("findings", "items", "risks")):
        if not isinstance(item, Mapping) or isinstance(item, list):
            continue
        bid_document_id = get_bid_document_id_from_item(item, bid_document_ids)
        title = normalize_text(item.get("title"))[:80]
        bid_evidence = normalize_text(
            _first_of(item, ("bidEvidence", "evidence", "bid_evidence")))
        risk_reason = normalize_text(
            _first_of(item, ("riskReason", "reason", "risk_reason")))
        finding = {
            "id": normalize_text(item.get("id")) or _create_rejection_id(),
            "bidDocumentId": bid_document_id,
            "type": normalize_finding_type(item.get("type")),
            "severity": normalize_severity(item.get("severity")),
            "title": title,
            "summary": normalize_text(item.get("summary")) or title,
            "requirement": (normalize_text(_first_of(item, ("requirement", "source")))
                            or "未明确引用具体检查依据，请人工复核。"),
            "bidEvidence": bid_evidence,
            "riskReason": risk_reason,
            "suggestion": (normalize_text(item.get("suggestion"))
                           or "请结合招标文件要求和投标文件原文人工复核后处理。"),
        }
        if not (finding["bidDocumentId"] and finding["title"]
                and finding["bidEvidence"] and finding["riskReason"]):
            continue
        findings.append(finding)
    return findings


_REJECTION_ID_RE = re.compile(r"-")


def _create_rejection_id() -> str:
    return f"rejection_finding_{_REJECTION_ID_RE.sub('', uuid.uuid4().hex)[:12]}"


def _first_of(item: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = item.get(key)
        if value:
            return value
    return None
