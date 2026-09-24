"""废标项检查的滚动审阅（rolling）——状态机、分段与消息构造（P4b 收尾，纯函数）。

逐字移植 `client/electron/services/rejectionCheckTask.cjs`：
常量 :13-18、`truncatePromptText` :355-360、`dedupeItems` :474、`takeRecentItems` :859、
分段与判定 :387-476、rolling 状态机 :478-687、状态摘要 :864-922 / :924-960、
四个消息构造 :1018-1160、`createRejectionFinalCandidates` :972-978、
`dedupeRejectionFindings` :843-845。

**注意**：状态条目与 JSON 摘要使用参考的 **camelCase 键**（它们会被序列化进
prompt，键名是契约的一部分），与仓储层的 snake_case 无关。
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Callable, Mapping, Sequence

from app.domain.rejection.rejection_check import (
    normalize_finding_type,
    normalize_rejection_check_findings,
    normalize_severity,
)
from app.domain.rejection.typo_check import (
    get_array_payload,
    get_bid_document_id_from_item,
    normalize_text,
)
from app.domain.rejection.user_text_splitter import split_user_text_by_context_limit

ROLLING_SEGMENT_LIMIT_RATIO = 0.55   # 参考 :13
TYPO_SEGMENT_LIMIT_RATIO = 0.7       # 参考 :14
FULL_PROMPT_LIMIT_RATIO = 0.6        # 参考 :13（rejectionCheckTask.cjs）
ROLLING_SUMMARY_EVIDENCE_LIMIT = 60  # 参考 :15
ROLLING_SUMMARY_RESOLVED_LIMIT = 40  # 参考 :16
ROLLING_SUMMARY_CONFIRMED_LIMIT = 60  # 参考 :17
FINAL_CANDIDATE_BATCH_SIZE = 20      # 参考 :18


def truncate_prompt_text(value: Any, max_length: int) -> str:
    """参考 :355-360：normalizeText 后截断，超出补 `...`。"""
    text = normalize_text(value)
    if not text or len(text) <= max_length:
        return text
    return f"{text[:max_length]}..."


def limit_dedupe_items(items: Any, max_count: int,
                       key_builder: Callable[[Any], str]) -> list[Any]:
    """参考 :462-476：按键去重（无键退化为 JSON 串），截到 max_count。"""
    seen: set[str] = set()
    result: list[Any] = []
    for item in (items if isinstance(items, list) else []):
        if not item:
            continue
        key = normalize_text(key_builder(item)) or json.dumps(
            item, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= max_count:
            break
    return result


def dedupe_items(items: Any, key_builder: Callable[[Any], str]) -> list[Any]:
    return limit_dedupe_items(items, 2 ** 53 - 1, key_builder)


def take_recent_items(items: Any, limit: int) -> list[Any]:
    """参考 :859-862：取尾部 limit 条。"""
    source = list(items) if isinstance(items, list) else []
    return source[max(0, len(source) - limit):]


def get_bid_document_display_name(document: Mapping[str, Any], document_index: int) -> str:
    """参考 :443-445：`投标文件N（文件名）`。"""
    file_name = document.get("fileName")
    return (f"投标文件{document_index + 1}"
            + (f"（{file_name}）" if file_name else ""))


def format_bid_document_id_list(bid_documents: Any) -> str:
    """参考 :452-455：`- 投标文件N（文件名）：id` 列表。"""
    documents = list(bid_documents) if isinstance(bid_documents, list) else []
    return "\n".join(
        f"- {get_bid_document_display_name(document, index)}：{document['id']}"
        for index, document in enumerate(documents))


def get_package_bid_document_id(item: Mapping[str, Any], bid_documents: Any,
                                fallback_bid_document_id: str = "") -> str:
    """参考 :457-460：候选键解析，失败回退 fallback（须在集合内）。"""
    bid_document_ids = {document["id"] for document in
                        (bid_documents if isinstance(bid_documents, list) else [])
                        if document.get("id")}
    resolved = get_bid_document_id_from_item(item, bid_document_ids)
    return (resolved if resolved
            else (fallback_bid_document_id if fallback_bid_document_id in bid_document_ids
                  else ""))


# ------------------------------------------------------------- 分段与判定

def create_bid_document_segments(document: Mapping[str, Any], config: Mapping[str, Any],
                                 limit_ratio: float) -> list[dict[str, Any]]:
    """参考 :403-419：单文件分段并携带 startOffset/endOffset（位置校验用）。"""
    segments = split_user_text_by_context_limit(
        document.get("content"), config, {"limitRatio": limit_ratio})
    start_offset = 0
    result: list[dict[str, Any]] = []
    for index, content in enumerate(segments):
        end_offset = start_offset + len(content)
        result.append({
            "content": content,
            "startOffset": start_offset,
            "endOffset": end_offset,
            "segmentIndex": index + 1,
            "totalSegments": len(segments),
        })
        start_offset = end_offset
    return result


def create_bid_package_segments(bid_documents: Sequence[Mapping[str, Any]],
                                config: Mapping[str, Any],
                                limit_ratio: float) -> list[dict[str, Any]]:
    """参考 :420-438：跨文档分段后**整体重编号**，每块带文件头。"""
    local_segments: list[dict[str, Any]] = []
    for document_index, document in enumerate(bid_documents):
        document_label = get_bid_document_display_name(document, document_index)
        for segment in create_bid_document_segments(document, config, limit_ratio):
            local_segments.append({
                "documentId": document["id"],
                "documentLabel": document_label,
                "content": (f"【{document_label}｜bidDocumentId：{document['id']}｜"
                            f"文件名：{document.get('fileName') or document['id']}】\n"
                            f"【当前文件片段：第 {segment['segmentIndex']}/"
                            f"{segment['totalSegments']} 段】\n{segment['content']}"),
            })
    return [{**segment, "segmentIndex": index + 1,
             "totalSegments": len(local_segments)}
            for index, segment in enumerate(local_segments)]


def should_use_segmented_prompt(prompt_text: str, config: Mapping[str, Any],
                                limit_ratio: float = FULL_PROMPT_LIMIT_RATIO) -> bool:
    """参考 :387-390：拼接文本分段后段数 >1 即走分段流程。"""
    return len(split_user_text_by_context_limit(prompt_text, config,
                                                {"limitRatio": limit_ratio})) > 1


def should_use_segmented_rejection_flow(input_params: Mapping[str, Any],
                                        config: Mapping[str, Any]) -> bool:
    """参考 :392-397。"""
    joined = "\n\n".join([
        str(input_params.get("invalidBidAndRejectionItems") or ""),
        str(input_params.get("customCheckItems") or ""),
        format_bid_documents_for_prompt(input_params),
    ])
    return should_use_segmented_prompt(joined, config)


def format_bid_documents_for_prompt(input_params: Mapping[str, Any]) -> str:
    """与 typo_check.format_bid_documents_for_prompt 同构（camelCase 输入）。"""
    from app.domain.rejection.typo_check import format_bid_documents_for_prompt as _fmt
    return _fmt(input_params)


# ------------------------------------------------------------- 滚动状态机

def create_empty_rolling_rejection_state() -> dict[str, Any]:
    """参考 :680-687：evidence/risk 双序号 + 四列表。"""
    return {
        "nextEvidenceSeq": 1,
        "nextRiskSeq": 1,
        "submittedEvidence": [],
        "pendingRisks": [],
        "resolvedRisks": [],
        "confirmedRisks": [],
    }


def normalize_rolling_evidence_item(item: Any, bid_documents: Any,
                                    fallback_bid_document_id: str = "") -> dict[str, Any] | None:
    """参考 :478-493。"""
    if not isinstance(item, Mapping):
        return None
    bid_document_id = get_package_bid_document_id(item, bid_documents,
                                                  fallback_bid_document_id)
    name = truncate_prompt_text(
        _first_of(item, ("name", "title", "material", "item", "requirement")), 80)
    evidence = truncate_prompt_text(
        _first_of(item, ("evidence", "originalText", "original_text", "excerpt",
                         "content", "description")), 600)
    location_hint = truncate_prompt_text(
        _first_of(item, ("locationHint", "location_hint", "location", "position")), 160)
    source = truncate_prompt_text(
        _first_of(item, ("source", "requirement", "reason")), 240)
    if not name and not evidence:
        return None
    return {
        "bidDocumentId": bid_document_id,
        "name": name or truncate_prompt_text(evidence, 80),
        "evidence": evidence,
        "locationHint": location_hint,
        "source": source,
    }


def normalize_rolling_rejection_risk_item(item: Any, bid_documents: Any,
                                          fallback_bid_document_id: str = "") -> dict[str, Any] | None:
    """参考 :495-512。"""
    if not isinstance(item, Mapping):
        return None
    bid_document_id = get_package_bid_document_id(item, bid_documents,
                                                  fallback_bid_document_id)
    title = truncate_prompt_text(
        _first_of(item, ("title", "summary", "requirement")), 80)
    if not title:
        return None
    return {
        "bidDocumentId": bid_document_id,
        "type": normalize_finding_type(item.get("type")),
        "severity": normalize_severity(item.get("severity")),
        "title": title,
        "summary": truncate_prompt_text(item.get("summary") or title, 180),
        "requirement": truncate_prompt_text(
            _first_of(item, ("requirement", "source")), 360),
        "bidEvidence": truncate_prompt_text(
            _first_of(item, ("bidEvidence", "evidence", "bid_evidence")), 600),
        "riskReason": truncate_prompt_text(
            _first_of(item, ("riskReason", "reason", "risk_reason")), 600),
        "suggestion": truncate_prompt_text(
            _first_of(item, ("suggestion", "recommendation")), 300),
        "statusReason": truncate_prompt_text(
            _first_of(item, ("statusReason", "status_reason", "pendingReason",
                             "pending_reason")), 360),
    }


def normalize_rolling_rejection_risk_update(item: Any, bid_documents: Any,
                                            fallback_bid_document_id: str = "") -> dict[str, Any] | None:
    """参考 :562-579：必须带 id；type/severity 只在提供时归一（undefined = 不更新）。"""
    if not isinstance(item, Mapping):
        return None
    finding_id = _patch_reference_id(item, ("id", "riskId", "risk_id",
                                            "pendingRiskId", "pending_risk_id"))
    if not finding_id:
        return None
    update: dict[str, Any] = {
        "id": finding_id,
        "bidDocumentId": get_package_bid_document_id(item, bid_documents,
                                                     fallback_bid_document_id),
    }
    if item.get("type"):
        update["type"] = normalize_finding_type(item.get("type"))
    if item.get("severity"):
        update["severity"] = normalize_severity(item.get("severity"))
    update.update({
        "title": truncate_prompt_text(
            _first_of(item, ("title", "summary", "requirement")), 80),
        "summary": truncate_prompt_text(item.get("summary"), 180),
        "requirement": truncate_prompt_text(
            _first_of(item, ("requirement", "source")), 360),
        "bidEvidence": truncate_prompt_text(
            _first_of(item, ("bidEvidence", "evidence", "bid_evidence")), 600),
        "riskReason": truncate_prompt_text(
            _first_of(item, ("riskReason", "reason", "risk_reason")), 600),
        "suggestion": truncate_prompt_text(
            _first_of(item, ("suggestion", "recommendation")), 300),
        "statusReason": truncate_prompt_text(
            _first_of(item, ("statusReason", "status_reason", "pendingReason",
                             "pending_reason")), 360),
    })
    return update


def normalize_rolling_rejection_resolve(item: Any) -> dict[str, Any] | None:
    """参考 :581-591。"""
    if not isinstance(item, Mapping):
        return None
    finding_id = _patch_reference_id(item, ("id", "riskId", "risk_id",
                                            "pendingRiskId", "pending_risk_id"))
    if not finding_id:
        return None
    return {
        "id": finding_id,
        "title": truncate_prompt_text(
            _first_of(item, ("title", "summary", "name")), 100),
        "reason": truncate_prompt_text(
            _first_of(item, ("reason", "resolvedReason", "resolved_reason",
                             "evidence")), 360),
        "locationHint": truncate_prompt_text(
            _first_of(item, ("locationHint", "location_hint", "location")), 160),
    }


def normalize_rolling_rejection_patch(parsed: Any, bid_documents: Any,
                                      fallback_bid_document_id: str = "") -> dict[str, Any]:
    """参考 :593-607：五类 patch 键（多别名）→ 归一化列表。"""
    source = parsed if isinstance(parsed, Mapping) else {}
    evidence_adds = [
        normalized for normalized in
        (normalize_rolling_evidence_item(item, bid_documents, fallback_bid_document_id)
         for item in get_array_payload(source, (
             "evidenceAdds", "evidence_adds", "submittedEvidenceAdds",
             "submitted_evidence_adds", "submittedEvidence", "submitted_evidence")))
        if normalized]
    pending_risk_adds = [
        normalized for normalized in
        (normalize_rolling_rejection_risk_item(item, bid_documents,
                                               fallback_bid_document_id)
         for item in get_array_payload(source, (
             "pendingRiskAdds", "pending_risk_adds", "pendingRisks", "pending_risks")))
        if normalized]
    pending_risk_updates = [
        normalized for normalized in
        (normalize_rolling_rejection_risk_update(item, bid_documents,
                                                 fallback_bid_document_id)
         for item in get_array_payload(source, (
             "pendingRiskUpdates", "pending_risk_updates", "riskUpdates",
             "risk_updates")))
        if normalized]
    pending_risk_resolves = [
        normalized for normalized in
        (normalize_rolling_rejection_resolve(item)
         for item in get_array_payload(source, (
             "pendingRiskResolves", "pending_risk_resolves", "riskResolves",
             "risk_resolves", "resolvedRisks", "resolved_risks")))
        if normalized]
    confirmed_risk_adds = normalize_rejection_check_findings(
        {"findings": get_array_payload(source, (
            "confirmedRiskAdds", "confirmed_risk_adds", "confirmedRisks",
            "confirmed_risks", "confirmedFindings", "confirmed_findings",
            "findings"))}, bid_documents)
    return {"evidenceAdds": evidence_adds, "pendingRiskAdds": pending_risk_adds,
            "pendingRiskUpdates": pending_risk_updates,
            "pendingRiskResolves": pending_risk_resolves,
            "confirmedRiskAdds": confirmed_risk_adds}


def _patch_reference_id(item: Mapping[str, Any], keys: Sequence[str]) -> str:
    """参考 :553-557。"""
    for key in keys:
        value = normalize_text(item.get(key))
        if value:
            return value
    return ""


def _first_of(item: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = item.get(key)
        if value:
            return value
    return None


def _merge_defined_fields(target: Mapping[str, Any], update: Mapping[str, Any],
                          fields: Sequence[str]) -> dict[str, Any]:
    """参考 :609-615：只合并「提供且非空串」的字段。"""
    merged = dict(target)
    for field in fields:
        if update.get(field) is not None and update.get(field) != "":
            merged[field] = update[field]
    return merged


def _assign_sequential_id(items_key: str, prefix: str, state: dict[str, Any],
                          item: dict[str, Any]) -> dict[str, Any]:
    seq_key = {"submittedEvidence": "nextEvidenceSeq",
               "pendingRisks": "nextRiskSeq"}[items_key]
    item = {**item, "id": f"{prefix}_{state[seq_key]:04d}"}
    state[seq_key] += 1
    return item


def apply_rolling_rejection_patch(state: Mapping[str, Any],
                                  patch: Mapping[str, Any]) -> dict[str, Any]:
    """参考 :624-678：四列表的增/改/移/定稿 + 尾部整体去重。"""
    merged_state = {
        "nextEvidenceSeq": state.get("nextEvidenceSeq", 1),
        "nextRiskSeq": state.get("nextRiskSeq", 1),
        "submittedEvidence": list(state.get("submittedEvidence") or []),
        "pendingRisks": list(state.get("pendingRisks") or []),
        "resolvedRisks": list(state.get("resolvedRisks") or []),
        "confirmedRisks": list(state.get("confirmedRisks") or []),
    }

    def evidence_key(item: Mapping[str, Any]) -> str:
        return "\u0000".join([str(item.get("bidDocumentId") or ""),
                              str(item.get("name") or ""),
                              str(item.get("evidence") or "")])

    def risk_key(item: Mapping[str, Any], evidence_field: str = "requirement") -> str:
        return "\u0000".join([str(item.get("bidDocumentId") or ""),
                              str(item.get("type") or ""),
                              str(item.get("title") or ""),
                              str(item.get(evidence_field) or "")])

    for item in patch.get("evidenceAdds") or []:
        if not any(evidence_key(existing) == evidence_key(item)
                   for existing in merged_state["submittedEvidence"]):
            merged_state["submittedEvidence"].append(_assign_sequential_id(
                "submittedEvidence", "evidence", merged_state, dict(item)))

    for item in patch.get("pendingRiskAdds") or []:
        exists = any(risk_key(existing) == risk_key(item)
                     for existing in merged_state["pendingRisks"]) or \
            any(risk_key(existing, "bidEvidence") == risk_key(item, "bidEvidence")
                for existing in merged_state["confirmedRisks"])
        if not exists:
            merged_state["pendingRisks"].append(_assign_sequential_id(
                "pendingRisks", "risk", merged_state, dict(item)))

    for update in patch.get("pendingRiskUpdates") or []:
        merged_state["pendingRisks"] = [
            _merge_defined_fields(risk, update, (
                "type", "severity", "title", "summary", "requirement", "bidEvidence",
                "riskReason", "suggestion", "statusReason"))
            if risk.get("id") == update.get("id") else risk
            for risk in merged_state["pendingRisks"]]

    for resolve in patch.get("pendingRiskResolves") or []:
        risk_index = next((index for index, risk in enumerate(merged_state["pendingRisks"])
                           if risk.get("id") == resolve.get("id")), None)
        if risk_index is None:
            continue
        risk = merged_state["pendingRisks"].pop(risk_index)
        merged_state["resolvedRisks"].append({
            "id": f"resolved_{risk.get('id')}",
            "riskId": risk.get("id"),
            "title": resolve.get("title") or risk.get("title"),
            "reason": resolve.get("reason") or "后续片段已提供线索，原待确认风险被排除。",
            "locationHint": resolve.get("locationHint") or risk.get("locationHint"),
        })

    for item in patch.get("confirmedRiskAdds") or []:
        if not any(risk_key(existing, "bidEvidence") == risk_key(item, "bidEvidence")
                   for existing in merged_state["confirmedRisks"]):
            merged_state["confirmedRisks"].append(_assign_sequential_id(
                "pendingRisks", "risk", merged_state, dict(item)))

    return {
        **merged_state,
        "submittedEvidence": dedupe_items(merged_state["submittedEvidence"], evidence_key),
        "pendingRisks": dedupe_items(merged_state["pendingRisks"],
                                     lambda item: risk_key(item)),
        "resolvedRisks": dedupe_items(
            merged_state["resolvedRisks"],
            lambda item: "\u0000".join([str(item.get("title") or ""),
                                        str(item.get("reason") or "")])),
        "confirmedRisks": dedupe_items(merged_state["confirmedRisks"],
                                       lambda item: risk_key(item, "bidEvidence")),
    }


def create_rolling_rejection_state_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    """参考 :864-892：counts + 各列表的精简投影（evidence/resolved/confirmed 取尾部）。"""
    submitted = take_recent_items(state.get("submittedEvidence") or [],
                                  ROLLING_SUMMARY_EVIDENCE_LIMIT)
    resolved = take_recent_items(state.get("resolvedRisks") or [],
                                 ROLLING_SUMMARY_RESOLVED_LIMIT)
    confirmed = take_recent_items(state.get("confirmedRisks") or [],
                                  ROLLING_SUMMARY_CONFIRMED_LIMIT)
    return {
        "counts": {
            "submittedEvidence": len(state.get("submittedEvidence") or []),
            "pendingRisks": len(state.get("pendingRisks") or []),
            "resolvedRisks": len(state.get("resolvedRisks") or []),
            "confirmedRisks": len(state.get("confirmedRisks") or []),
        },
        "submittedEvidence": [{
            "id": item["id"], "bidDocumentId": item["bidDocumentId"],
            "name": item["name"],
            "evidence": truncate_prompt_text(item["evidence"], 220),
            "locationHint": item["locationHint"],
            "source": truncate_prompt_text(item["source"], 160),
        } for item in submitted],
        "pendingRisks": [{
            "id": item["id"], "bidDocumentId": item["bidDocumentId"],
            "type": item["type"], "severity": item["severity"], "title": item["title"],
            "requirement": truncate_prompt_text(item["requirement"], 220),
            "bidEvidence": truncate_prompt_text(item["bidEvidence"], 260),
            "riskReason": truncate_prompt_text(item["riskReason"], 260),
            "statusReason": truncate_prompt_text(item["statusReason"], 180),
        } for item in (state.get("pendingRisks") or [])],
        "resolvedRisks": resolved,
        "confirmedRisks": [{
            "id": item["id"], "bidDocumentId": item["bidDocumentId"],
            "type": item["type"], "severity": item["severity"], "title": item["title"],
            "bidEvidence": truncate_prompt_text(item["bidEvidence"], 260),
            "riskReason": truncate_prompt_text(item["riskReason"], 260),
        } for item in confirmed],
    }


def create_final_rejection_state_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    """参考 :924-941：定稿用的全量索引（证据索引 + 已排除列表）。"""
    return {
        "counts": {
            "submittedEvidence": len(state.get("submittedEvidence") or []),
            "pendingRisks": len(state.get("pendingRisks") or []),
            "resolvedRisks": len(state.get("resolvedRisks") or []),
            "confirmedRisks": len(state.get("confirmedRisks") or []),
        },
        "submittedEvidenceIndex": [{
            "id": item["id"], "bidDocumentId": item["bidDocumentId"],
            "name": item["name"],
            "evidence": truncate_prompt_text(item["evidence"], 180),
            "locationHint": item["locationHint"],
            "source": truncate_prompt_text(item["source"], 120),
        } for item in (state.get("submittedEvidence") or [])],
        "resolvedRisks": [{
            "id": item["id"], "riskId": item["riskId"], "title": item["title"],
            "reason": truncate_prompt_text(item["reason"], 220),
            "locationHint": item["locationHint"],
        } for item in (state.get("resolvedRisks") or [])],
    }


def create_rejection_final_candidates(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """参考 :972-978：confirmed + pending（带 candidateStatus）。"""
    return [{**item, "candidateStatus": "confirmed"}
            for item in (state.get("confirmedRisks") or [])] + \
           [{**item, "candidateStatus": "pending"}
            for item in (state.get("pendingRisks") or [])]


def dedupe_rejection_findings(findings: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """参考 :843-845：五元组键去重。"""
    return limit_dedupe_items(
        list(findings), 2 ** 53 - 1,
        lambda item: "\u0000".join([
            str(item.get("bidDocumentId") or ""), str(item.get("type") or ""),
            str(item.get("title") or ""), str(item.get("bidEvidence") or ""),
            str(item.get("riskReason") or "")]))


# ------------------------------------------------------------- 消息构造（逐字）

def build_rolling_rejection_base_messages(
        input_params: Mapping[str, Any]) -> list[dict[str, str]]:
    """参考 :1018-1040：检查依据 + 可选自定义检查项。"""
    messages = [{
        "role": "user",
        "content": f"""【废标项滚动检查输入｜检查依据】
以下内容来自招标文件“无效投标”和“废标项”解析结果。必须优先基于这些检查口径，不要自行扩大到无法从电子投标文件判断的事项。

{input_params.get('invalidBidAndRejectionItems')}""",
    }]
    custom = str(input_params.get("customCheckItems") or "").strip()
    if custom:
        messages.append({
            "role": "user",
            "content": f"""【废标项滚动检查输入｜自定义检查项】
以下是用户补充的电子投标文件检查关注点。仅在能从电子投标文件正文、目录、附件文本或材料内容中判断时使用；如果涉及签字、盖章、密封、现场递交、纸质正副本等纸质或线下事项，必须忽略。

{custom}""",
        })
    return messages


def build_rolling_rejection_segment_messages(
        input_params: Mapping[str, Any], segment: Mapping[str, Any],
        state_summary: Mapping[str, Any]) -> list[dict[str, str]]:
    """参考 :1042-1095：滚动审阅的四条 user 消息（**逐字**）。"""
    bid_document_id_list = format_bid_document_id_list(input_params.get("bidDocuments"))
    return [
        *build_rolling_rejection_base_messages(input_params),
        {"role": "user", "content": f"""【废标项滚动检查｜当前状态摘要】
你正在按顺序审阅同一个投标包。完整权威状态由程序维护，你只能基于当前片段返回增量 patch。
下面是前序片段累计状态的精简摘要；如需更新或排除 pendingRisks，只能引用摘要中已有的 id。

{json.dumps(dict(state_summary), ensure_ascii=False, indent=2)}"""},
        {"role": "user", "content": f"""【废标项滚动检查｜当前投标包片段】
投标包片段：第 {segment.get('segmentIndex')}/{segment.get('totalSegments')} 段
当前片段所属文件：{segment.get('documentLabel')}
当前片段默认 bidDocumentId：{segment.get('documentId')}

本投标包有效 bidDocumentId：
{bid_document_id_list}

重要限制：当前内容只是整个投标包的一段，不是全部投标文件。不得因为当前段或当前文件没有出现某项材料、附件、承诺或响应，就确认该材料缺失；这类问题只能先放入 pendingRisks，后续片段或其他投标文件可能会补充或推翻。

{segment.get('content')}"""},
        {"role": "user", "content": """【废标项滚动检查任务】
请基于当前片段输出增量 patch，只输出 JSON。不要返回完整累计状态，程序会负责合并和保留历史状态。

Patch 要求：
1. evidenceAdds 只放当前片段新增的章节标题、目录项、附件标题、材料清单项、表格条目、页码线索、图片占位线索、承诺或响应线索。
2. pendingRiskAdds 只放当前片段发现、但需要后续片段或其他文件继续确认的问题。
3. pendingRiskUpdates 只能引用状态摘要中 pendingRisks 的 id，用于补充或修正该待确认项。
4. pendingRiskResolves 只能引用状态摘要中 pendingRisks 的 id；如果当前片段证明某个“缺失/未响应”不成立，就在这里给出排除原因。
5. confirmedRiskAdds 只放当前片段或累计摘要已经提供明确投标文件证据，且不依赖纸质、线下或外部事实的风险。
6. 不检查签字、盖章、密封、纸质正副本、现场递交、纸质原件等事项。
7. 每条风险、证据和事实必须保留 bidDocumentId，且只能使用上方有效 bidDocumentId；如果当前片段没有明确切换文件，默认使用 """ + str(segment.get("documentId")) + """。

JSON 格式：
{
  "evidenceAdds": [{"bidDocumentId":"有效 bidDocumentId","name":"材料或响应线索名称","evidence":"原文线索或摘要","locationHint":"章节/表格/位置线索","source":"对应检查项或说明"}],
  "pendingRiskAdds": [{"bidDocumentId":"有效 bidDocumentId","type":"invalidBid","severity":"medium","title":"待确认问题","summary":"摘要","requirement":"检查依据","bidEvidence":"当前证据或缺口","riskReason":"为什么需要继续确认","suggestion":"建议","statusReason":"仍需后续片段确认的原因"}],
  "pendingRiskUpdates": [{"id":"pendingRisks 中已有 id","bidEvidence":"补充证据或缺口","riskReason":"更新原因","statusReason":"当前仍需确认的原因"}],
  "pendingRiskResolves": [{"id":"pendingRisks 中已有 id","reason":"被当前片段或累计线索排除的原因","locationHint":"位置线索"}],
  "confirmedRiskAdds": [{"bidDocumentId":"有效 bidDocumentId","type":"invalidBid","severity":"high","title":"风险标题","summary":"摘要","requirement":"检查依据","bidEvidence":"明确投标文件证据","riskReason":"风险原因","suggestion":"建议"}]
}"""},
    ]


def build_rejection_final_batch_messages(
        input_params: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]],
        state_summary: Mapping[str, Any], batch_index: int,
        total_batches: int) -> list[dict[str, str]]:
    """参考 :1097-1129：定稿批次消息（**逐字**）。"""
    bid_document_id_list = format_bid_document_id_list(input_params.get("bidDocuments"))
    return [
        *build_rolling_rejection_base_messages(input_params),
        {"role": "user", "content": f"""【废标项最终定稿｜状态摘要】
本投标包有效 bidDocumentId：
{bid_document_id_list}

{json.dumps(dict(state_summary), ensure_ascii=False, indent=2)}"""},
        {"role": "user", "content": f"""【废标项最终定稿｜候选批次 {batch_index}/{total_batches}】
请只基于下面这一批候选输出最终废标项检查结果，只输出 JSON。

候选风险：
{json.dumps(list(candidates), ensure_ascii=False, indent=2)}

定稿规则：
1. 只保留能从电子投标文件原文或累计状态判断且有明确证据的风险。
2. candidateStatus 为 pending 且仍未形成完整证据闭环的问题不得输出为最终风险。
3. 如果某项“缺失/未响应”已经在 submittedEvidenceIndex 中出现章节、目录、附件标题、材料清单、表格条目、页码线索、图片占位线索或其他提交线索，不能定稿为缺失。
4. 删除签字、盖章、密封、纸质正副本、现场递交、纸质原件、开标现场行为等纸质或线下事项。
5. 同一问题合并为一条，bidDocumentId 必须来自上方有效 bidDocumentId。
6. 如果没有符合条件的风险，返回 {{"findings":[]}}。

JSON 格式：{{"findings":[{{"bidDocumentId":"有效 bidDocumentId","type":"invalidBid","severity":"high","title":"不超过 28 个中文字符的风险标题","summary":"一句话概括风险","requirement":"对应检查依据或招标要求","bidEvidence":"投标文件中的明确证据、章节、原文摘录或缺失位置说明","riskReason":"为什么该证据可能构成无效标或废标项风险","suggestion":"建议用户如何处理或复核"}}]}}"""},
    ]


def build_rejection_global_merge_messages(
        input_params: Mapping[str, Any], findings: Sequence[Mapping[str, Any]],
        final_summary: Mapping[str, Any]) -> list[dict[str, str]]:
    """参考 :1130-1160：全局合稿消息（**逐字**）。"""
    bid_document_id_list = format_bid_document_id_list(input_params.get("bidDocuments"))
    return [
        *build_rolling_rejection_base_messages(input_params),
        {"role": "user", "content": f"""【废标项全局合稿｜证据索引】
本投标包有效 bidDocumentId：
{bid_document_id_list}

以下是 Main 侧维护的全量精简状态索引，用于跨批次去重、排除误判和避免早期证据丢失：
{json.dumps(dict(final_summary), ensure_ascii=False, indent=2)}"""},
        {"role": "user", "content": f"""【废标项全局合稿任务】
以下 findings 来自多个候选批次的初步定稿结果。请进行全局合稿，只输出最终 JSON。

待合稿 findings：
{json.dumps({"findings": list(findings)}, ensure_ascii=False, indent=2)}

合稿规则：
1. 合并跨批次重复或高度相似的风险，保留证据更明确、表述更完整的一条。
2. 如果 submittedEvidenceIndex 已经出现对应材料、章节、附件标题、目录、表格、页码线索、图片占位线索或其他提交线索，不得把该材料定稿为缺失。
3. 删除 resolvedRisks 已排除或证据不足的风险。
4. 删除签字、盖章、密封、纸质正副本、现场递交、纸质原件、开标现场行为等纸质或线下事项。
5. bidDocumentId 必须来自上方有效 bidDocumentId。
6. 如果没有符合条件的风险，返回 {{"findings":[]}}。

JSON 格式：{{"findings":[{{"bidDocumentId":"有效 bidDocumentId","type":"invalidBid","severity":"high","title":"不超过 28 个中文字符的风险标题","summary":"一句话概括风险","requirement":"对应检查依据或招标要求","bidEvidence":"投标文件中的明确证据、章节、原文摘录或缺失位置说明","riskReason":"为什么该证据可能构成无效标或废标项风险","suggestion":"建议用户如何处理或复核"}}]}}"""},
    ]
