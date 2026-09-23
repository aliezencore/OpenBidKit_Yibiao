"""逻辑谬误检查的 prompt 与 findings 归一化（P4b-2，纯函数）。

逐字移植 `client/electron/services/rejectionCheckTask.cjs`：
`buildLogicCheckMessages` :218-240 / `normalizeLogicCheckFindings` :352-373。
共享助手（normalizeText / getBidDocumentIdFromItem / getArrayPayload /
formatBidDocumentsForPrompt）来自 `typo_check.py`。

与错别字的**关键差异**：逻辑谬误**没有原文位置校验**——originalText/locationHint
缺省时填提示语而不是丢弃；必填字段是 `bidDocumentId/title/fallacyReason`。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.domain.rejection.typo_check import (
    format_bid_documents_for_prompt,
    get_array_payload,
    get_bid_document_id_from_item,
    normalize_text,
)

_MISSING_ORIGINAL_TEXT = "未提供明确原文摘录，请结合位置线索复核。"
_MISSING_LOCATION_HINT = "未明确具体位置，请结合原文摘录复核。"
_MISSING_SUGGESTION = "请结合投标文件上下文人工复核后修改。"


def build_logic_check_messages(
        bid_documents: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """参考 :218-240（两条 user 消息**逐字**）。"""
    input_params = {"bidDocuments": list(bid_documents)}
    return [
        {"role": "user", "content": f"""【逻辑谬误检查输入 v2｜投标文件原文】
以下是本次需要一起检查的多份投标文件 Markdown 原文。每份文件都有唯一 bidDocumentId。后续只能基于这些投标文件内容进行逻辑一致性检查，每条结果必须返回所属 bidDocumentId。

{format_bid_documents_for_prompt(input_params)}"""},
        {"role": "user", "content": """【逻辑谬误检查任务 v1】
请检查投标文件中的逻辑谬误和前后不一致问题，并输出 JSON。

检查范围：
1. 句子本身存在逻辑漏洞、因果不成立、条件互相矛盾或结论无法由前文推出。
2. 全文前后不一致，包括但不限于处理相同工作的人员名单、设备型号、工期、金额、数量、服务期限、项目名称、技术参数等应高度一致的内容前后不一致。

输出要求：
1. 只保留有明确文本依据的问题，避免泛泛而谈。
2. 问题可能涉及同一份投标文件内的多处原文，originalText 可摘录关键原文，locationHint 写明大概位置、章节、表格或上下文线索，bidDocumentId 必须是输入中提供的真实 ID。
3. title 必须简短明确，便于作为折叠列表标题。
4. 如果没有明确逻辑谬误，返回 {"findings":[]}。

JSON 格式：{"findings":[{"bidDocumentId":"对应投标文件的 bidDocumentId","title":"不超过 28 个中文字符的简短标题","originalText":"关键原文摘录，可包含同一份文件内多处摘录","locationHint":"大概位置、章节、表格或上下文线索","fallacyReason":"谬误原因或前后不一致原因","suggestion":"修改建议"}]}

仅输出 JSON，不要输出 Markdown、代码块或解释。"""},
    ]


def normalize_logic_check_findings(
        parsed: Any,
        bid_documents: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """参考 :352-373：必填 `bidDocumentId/title/fallacyReason`，缺任一丢弃；
    原文/位置/建议缺省时填提示语（**不校验原文**）。去重键 `id+title+fallacyReason`。"""
    bid_document_ids = {document["id"] for document in bid_documents
                        if document.get("id")}
    seen: set[str] = set()
    findings: list[dict[str, Any]] = []
    for item in get_array_payload(parsed, ("findings", "items", "risks", "issues")):
        if not isinstance(item, Mapping) or isinstance(item, list):
            continue
        bid_document_id = get_bid_document_id_from_item(item, bid_document_ids)
        title = normalize_text(_first_of(item, ("title", "summary")))[:80]
        original_text = (normalize_text(_first_of(item, ("originalText", "original_text",
                                                         "evidence", "bidEvidence")))
                         or _MISSING_ORIGINAL_TEXT)
        location_hint = (normalize_text(_first_of(item, ("locationHint", "location_hint",
                                                         "location", "position")))
                         or _MISSING_LOCATION_HINT)
        fallacy_reason = normalize_text(_first_of(item, ("fallacyReason", "fallacy_reason",
                                                         "reason", "riskReason")))
        suggestion = (normalize_text(_first_of(item, ("suggestion", "recommendation")))
                      or _MISSING_SUGGESTION)
        if not bid_document_id or not title or not fallacy_reason:
            continue
        key = f"{bid_document_id}\u0000{title}\u0000{fallacy_reason}"
        if key in seen:
            continue
        seen.add(key)
        findings.append({
            "id": normalize_text(item.get("id")) or _create_logic_id(),
            "bidDocumentId": bid_document_id,
            "title": title,
            "originalText": original_text,
            "locationHint": location_hint,
            "fallacyReason": fallacy_reason,
            "suggestion": suggestion,
        })
    return findings


def _first_of(item: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = item.get(key)
        if value:
            return value
    return None


def _create_logic_id() -> str:
    import uuid

    return f"logic_finding_{uuid.uuid4().hex[:12]}"
