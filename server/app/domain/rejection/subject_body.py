"""主体一致性检查的名字工具与正文滚动状态机（P4b-3，纯函数）。

逐字移植 `client/electron/services/subjectNameUtils.cjs`（名字归一/命中判定/公司名启发）
与 `subjectCheckTask.cjs` 的滚动状态机（:146-231）与 findings 归一化（:302-349）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from app.domain.rejection.normalize import normalize_subject_name

SEMANTIC_PRONOUNS = ["我公司", "本公司", "我方", "本投标人", "投标人", "应答人", "供应商", "响应人"]
PRONOUN_SET = set(SEMANTIC_PRONOUNS)
NEAR_MATCH_MAX_DISTANCE = 2


def normalize_subject_name_strict(value: Any) -> str:
    return normalize_subject_name(value)


def matches_allowed_subject(value: Any, official_name: Any,
                            aliases: Sequence[Any] | None = None) -> bool:
    """参考 subjectNameUtils.cjs:24-31：归一化后落在 [正式名, *简称] 内即命中。"""
    normalized = normalize_subject_name(value)
    if not normalized:
        return False
    allowed = [normalize_subject_name(name) for name in
               [official_name, *(aliases or [])]]
    return normalized in [name for name in allowed if name]


def _strip_digit_noise(value: Any) -> str:
    return re.sub(r"\d+", "", normalize_subject_name(value))


def _levenshtein_within(a: str, b: str, max_distance: int) -> int:
    if a == b:
        return 0
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1
    previous = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        current = [i]
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + cost))
        previous = current
    return previous[len(b)]


def is_near_match_subject_name(value: Any, official_name: Any,
                               aliases: Sequence[Any] | None = None) -> bool:
    """近似命中（spec §7.4）：去数字噪声后互为子串或编辑距离 ≤2。
    精确命中**不算**近似；近似只用于降级为人工复核，不用于自动放行。"""
    if matches_allowed_subject(value, official_name, aliases):
        return False
    detected = _strip_digit_noise(value)
    if not detected:
        return False
    allowed = [name for name in (_strip_digit_noise(official_name),
                                 *(_strip_digit_noise(a) for a in (aliases or [])))
               if name]
    return any(name and (name in detected or detected in name
                         or _levenshtein_within(detected, name,
                                                NEAR_MATCH_MAX_DISTANCE)
                         <= NEAR_MATCH_MAX_DISTANCE)
               for name in allowed)


_COMPANY_NAME_RE = re.compile(r"公司|集团|研究院|研究所|事务所|中心|院|所|局|处|队$")


def looks_like_company_name(value: Any) -> bool:
    """参考 :111-115：以公司/集团/院所等后缀或关键词启发（代称不算公司名）。"""
    text = str(value or "").strip()
    if not text or text in PRONOUN_SET:
        return False
    return bool(_COMPANY_NAME_RE.search(text))


def locate_original_text(content: Any, excerpt: Any) -> bool:
    """参考 subjectCheckTask.cjs:70-80：先精确，再忽略空白差异（spec §8.4）。"""
    source = str(content or "")
    target = str(excerpt or "")
    if not source or not target:
        return False
    if target in source:
        return True
    compact_source = re.sub(r"\s+", "", source)
    compact_target = re.sub(r"\s+", "", target)
    return bool(compact_target) and compact_target in compact_source


# ------------------------------------------------------------- 滚动状态机

ROLLING_SEGMENT_CHARS = 12000      # 参考 :146
FINAL_CANDIDATE_BATCH_SIZE = 12    # 参考 :147
_STATE_SUMMARY_TAIL = 40           # 参考 :225-231 的 slice(-40)

_SUBJECT_ROLES = ("bidder", "third-party", "unclear")
_SUBJECT_LOCATION_KINDS = ("body", "table", "signature")


def chunk_items(items: Sequence[Any], batch_size: int) -> list[list[Any]]:
    source = list(items) if isinstance(items, list) else []
    return [source[index:index + batch_size]
            for index in range(0, len(source), batch_size)]


def create_empty_subject_body_state() -> dict[str, Any]:
    """参考 :156-163。`candidate_keys` 是 set——深拷贝时单独处理。"""
    return {"subjectSummary": [], "roleSummary": [], "candidates": [],
            "candidateKeys": set()}


def create_subject_candidate_key(candidate: Mapping[str, Any]) -> str:
    """参考 :165-173：四元组键（归一化主体名 + 去空白原文）。"""
    return "\u0000".join([
        str(candidate.get("bidDocumentId") or ""),
        str(candidate.get("locationKind") or ""),
        normalize_subject_name(candidate.get("detectedSubject")),
        re.sub(r"\s+", "", str(candidate.get("originalText") or "")),
    ])


def _clamp01(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if number != number:
        return 0.0
    return max(0.0, min(1.0, number))


def _subject_candidate_from_raw(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "bidDocumentId": str(raw.get("bid_document_id") or ""),
        "role": (raw.get("role") if raw.get("role") in _SUBJECT_ROLES else "unclear"),
        "detectedSubject": str(raw.get("detected_subject") or ""),
        "locationKind": (raw.get("location_kind")
                         if raw.get("location_kind") in _SUBJECT_LOCATION_KINDS
                         else "body"),
        "title": str(raw.get("title") or ""),
        "summary": str(raw.get("summary") or ""),
        "locationHint": str(raw.get("location_hint") or ""),
        "originalText": str(raw.get("original_text") or ""),
        "roleReason": str(raw.get("role_reason") or ""),
        "mismatchReason": str(raw.get("mismatch_reason") or ""),
        "suggestion": str(raw.get("suggestion") or ""),
        "nameConfidence": _clamp01(raw.get("name_confidence")),
        "roleConfidence": _clamp01(raw.get("role_confidence")),
    }


def apply_subject_body_patch(state: Mapping[str, Any],
                             patch: Mapping[str, Any] | None) -> dict[str, Any]:
    """参考 :174-212：摘要去重追加、candidates 按键去重、confidence 夹取。"""
    patch = patch or {}
    next_state = {
        "subjectSummary": list(state.get("subjectSummary") or []),
        "roleSummary": list(state.get("roleSummary") or []),
        "candidates": list(state.get("candidates") or []),
        "candidateKeys": set(state.get("candidateKeys") or set()),
    }
    for item in (patch.get("subject_summary")
                 if isinstance(patch.get("subject_summary"), list) else []):
        text = str(item or "").strip()
        if text and text not in next_state["subjectSummary"]:
            next_state["subjectSummary"].append(text)
    for item in (patch.get("role_summary")
                 if isinstance(patch.get("role_summary"), list) else []):
        text = str(item or "").strip()
        if text and text not in next_state["roleSummary"]:
            next_state["roleSummary"].append(text)
    for raw in (patch.get("candidates")
                if isinstance(patch.get("candidates"), list) else []):
        candidate = _subject_candidate_from_raw(raw if isinstance(raw, Mapping) else {})
        key = create_subject_candidate_key(candidate)
        if key in next_state["candidateKeys"]:
            continue
        next_state["candidateKeys"].add(key)
        next_state["candidates"].append(candidate)
    return next_state


def create_subject_body_final_candidates(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """参考 :213-223：按键去重后的最终候选。"""
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for candidate in state.get("candidates") or []:
        key = create_subject_candidate_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def create_subject_body_state_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    """参考 :225-231：摘要只带**尾部 40 条** + 候选计数（喂回下一段的上下文）。"""
    return {
        "subject_summary": list(state.get("subjectSummary") or [])[-_STATE_SUMMARY_TAIL:],
        "role_summary": list(state.get("roleSummary") or [])[-_STATE_SUMMARY_TAIL:],
        "candidate_count": len(state.get("candidates") or []),
    }


# ------------------------------------------------------- 消息构造与归一化（:233-349）

SEMANTIC_PRONOUNS_TEXT = "、".join(SEMANTIC_PRONOUNS)


def build_subject_body_segment_messages(*, bid_documents: Sequence[Mapping[str, Any]],
                                        segment: Mapping[str, Any],
                                        state_summary: Mapping[str, Any],
                                        official_name: Any, aliases: Sequence[Any]) -> list[dict[str, str]]:
    """参考 :233-269（滚动审阅，两条 user 消息**逐字**）。"""
    document = next((item for item in bid_documents
                     if item.get("id") == segment.get("documentId")), None)
    return [
        {"role": "user", "content": f"""【投标主体一致性滚动审阅｜已发现状态】
正式投标主体：{official_name}
允许简称：{"、".join(aliases) or "（无）"}
语义代称：{SEMANTIC_PRONOUNS_TEXT}

以下是 Main 侧维护的已发现主体与角色摘要，用于跨片段去重和避免早期信息丢失：
{json.dumps(dict(state_summary), ensure_ascii=False, indent=2)}"""},
        {"role": "user", "content": f"""【投标主体一致性滚动审阅任务】
当前文件：{document.get("fileName") if document else segment.get("documentId")}（bidDocumentId：{segment.get("documentId")}）
当前片段：第 {segment.get("segmentIndex")}/{segment.get("totalSegments")} 段

请通读本片段，找出所有可能出现的主体名称，并判断其在当前上下文中的角色：
- bidder：当前上下文代表投标方发言、承诺、声明、签署、落款或承担责任；
- third-party：招标人、代理机构、客户、制造商、认证机构、案例单位等第三方；
- unclear：无法确定。

规则：
1. 只有“我公司/本公司”等代称且没有出现其他具名公司时，不要生成候选。
2. 第三方角色不要生成候选，只在 role_summary 里记录，避免后续重复分析。
3. 不得因为片段里没有解释，就把第三方主体直接定为投标主体错误。
4. original_text 必须是本片段中逐字出现的原文摘录。

JSON 格式：{{"subject_summary":["已确认的主体与证据"],"role_summary":["角色结论"],"candidates":[{{"bid_document_id":"{segment.get("documentId")}","role":"bidder","detected_subject":"检测到的公司名称","location_kind":"body","title":"不超过 24 字的标题","summary":"简要说明","location_hint":"位置线索","original_text":"原文摘录","role_reason":"角色判断依据","mismatch_reason":"不一致原因","suggestion":"修改建议","name_confidence":0.9,"role_confidence":0.9}}]}}

本片段正文：
{segment.get("text")}"""},
    ]


def build_subject_body_final_messages(*, official_name: Any, aliases: Sequence[Any],
                                      candidates: Sequence[Mapping[str, Any]],
                                      state_summary: Mapping[str, Any]) -> list[dict[str, str]]:
    """参考 :271-300（定稿，两条 user 消息**逐字**）。"""
    return [
        {"role": "user", "content": f"""【投标主体一致性定稿｜事实索引】
正式投标主体：{official_name}
允许简称：{"、".join(aliases) or "（无）"}

Main 侧全量状态索引：
{json.dumps(dict(state_summary), ensure_ascii=False, indent=2)}"""},
        {"role": "user", "content": f"""【投标主体一致性定稿任务】
以下 candidates 来自滚动审阅的初步结果。请只保留确实代表投标方身份、且与正式主体和允许简称均不匹配的问题，输出最终 JSON。

定稿规则：
1. 正式投标主体名称和允许简称出现在原文中属于正常，必须删除。
2. 第三方主体（招标人、代理机构、客户、制造商、认证机构、案例单位）不得输出。
3. 只有代称而没有错误具名主体的位置不得输出。
4. 相似但未经用户确认的公司名至少保留为需人工复核，不得直接丢弃。
5. bidDocumentId 必须来自上方有效列表，original_text 必须逐字来自对应原文。

待定稿 candidates：
{json.dumps(list(candidates), ensure_ascii=False, indent=2)}

JSON 格式：{{"findings":[{{"bid_document_id":"有效 bidDocumentId","role":"bidder","detected_subject":"检测到的公司名称","location_kind":"body","title":"不超过 24 字的标题","summary":"简要说明","location_hint":"位置线索","original_text":"原文摘录","role_reason":"角色判断依据","mismatch_reason":"不一致原因","suggestion":"修改建议","name_confidence":0.9,"role_confidence":0.9}}]}}"""},
    ]


def normalize_subject_body_findings(payload: Any, *, bid_documents: Sequence[Mapping[str, Any]],
                                    official_name: Any,
                                    aliases: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    """参考 :302-349：spec §8.4 的四道过滤 + §8.2 的角色过滤 + §10.1 的 level 门槛。"""
    documents = list(bid_documents) if isinstance(bid_documents, list) else []
    by_id = {document["id"]: document for document in documents if document.get("id")}
    raw_findings = payload.get("findings") if isinstance(payload, Mapping) else None
    raw_findings = raw_findings if isinstance(raw_findings, list) else []
    seen: set[str] = set()
    findings: list[dict[str, Any]] = []
    for raw in raw_findings:
        if not isinstance(raw, Mapping):
            continue
        bid_document_id = str(raw.get("bid_document_id") or "")
        document = by_id.get(bid_document_id)
        if document is None:
            continue                                   # spec §8.4-1
        original_text = str(raw.get("original_text") or "").strip()
        if not original_text or not locate_original_text(document.get("content"),
                                                         original_text):
            continue                                   # spec §8.4-2
        detected_subject = str(raw.get("detected_subject") or "").strip()
        if not detected_subject or detected_subject not in original_text:
            continue                                   # spec §8.4-3
        if matches_allowed_subject(detected_subject, official_name, aliases):
            continue                                   # spec §8.4-4
        role = raw.get("role") if raw.get("role") in _SUBJECT_ROLES else "unclear"
        if role == "third-party":
            continue                                   # spec §8.2
        if role == "unclear" and not looks_like_company_name(detected_subject):
            continue                                   # spec §8.2
        name_confidence = _clamp01(raw.get("name_confidence"))
        role_confidence = _clamp01(raw.get("role_confidence"))
        location_kind = (raw.get("location_kind")
                         if raw.get("location_kind") in _SUBJECT_LOCATION_KINDS else "body")
        key = "\u0000".join([
            bid_document_id, location_kind,
            normalize_subject_name(detected_subject),
            re.sub(r"\s+", "", original_text)])
        if key in seen:
            continue                                   # spec §8.4-5
        seen.add(key)
        # spec §10.1：bidder + 双置信度 ≥0.85 → confirmed-mismatch；否则需人工复核
        level = ("confirmed-mismatch"
                 if role == "bidder" and name_confidence >= 0.85 and role_confidence >= 0.85
                 else "needs-review")
        findings.append({
            "id": f"subject-text-{len(findings) + 1}",  # 参考 :340：**序号 id，从 1 起**
            "bidDocumentId": bid_document_id,
            "level": level,
            "source": "text",
            "expectedSubject": str(official_name or ""),
            "detectedSubject": detected_subject,
            "locationKind": location_kind,
            "title": str(raw.get("title") or "投标主体不一致"),
            "summary": str(raw.get("summary") or ""),
            "locationHint": str(raw.get("location_hint") or ""),
            "originalText": original_text,
            "roleReason": str(raw.get("role_reason") or ""),
            "mismatchReason": str(raw.get("mismatch_reason") or ""),
            "suggestion": str(raw.get("suggestion") or ""),
            "confidence": min(name_confidence, role_confidence),
        })
    return findings
