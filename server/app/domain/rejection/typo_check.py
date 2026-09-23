"""错别字检查的 prompt、原文位置校验与 findings 归一化（P4b-2，纯函数）。

逐字移植 `client/electron/services/rejectionCheckTask.cjs`：
`createId` :25 / `normalizeText` :37 / `getBidDocumentIdFromItem` :41 /
`formatBidDocumentsForPrompt` :51 / `getArrayPayload` :56 /
`buildTypoCheckMessages` :196-214 / `findVerifiedTypoPosition` :268-285 /
`createVerifiedTypoExcerpt` :294-310（`typoExcerptRadius = 8`，:12）/
`createLineLocationHint` :312-315 / `normalizeTypoCheckFindings` :317-349。
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Mapping, Sequence

TYPO_EXCERPT_RADIUS = 8  # 参考 :12


def create_id(prefix: str) -> str:
    """参考 :25-27：`{prefix}_{uuid 去 - 前 12}`。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


_BID_ID_KEYS = ("bidDocumentId", "bid_document_id", "documentId", "document_id",
                "fileId", "file_id", "sourceFile", "source_file")


def get_bid_document_id_from_item(item: Mapping[str, Any],
                                  bid_document_ids: set[str]) -> str:
    """参考 :41-49：按候选键顺序取第一个**存在于集合**的 id；
    集合只有一个 id 时直接回退（单文件检查的宽容语义）。"""
    candidates = [normalize_text(item.get(key)) for key in _BID_ID_KEYS]
    for candidate in candidates:
        if candidate and candidate in bid_document_ids:
            return candidate
    if len(bid_document_ids) == 1:
        return next(iter(bid_document_ids))
    return ""


def format_bid_documents_for_prompt(input_params: Mapping[str, Any]) -> str:
    """参考 :51-54：多份投标文件拼成带分隔线的原文块。"""
    documents = input_params.get("bidDocuments")
    documents = documents if isinstance(documents, list) else []
    blocks = [
        f"【投标文件{index + 1}｜bidDocumentId：{document['id']}｜"
        f"文件名：{document.get('fileName') or document['id']}】\n{document['content']}"
        for index, document in enumerate(documents)
    ]
    return "\n\n--- 投标文件分隔线 ---\n\n".join(blocks)


def get_array_payload(parsed: Any, keys: Sequence[str]) -> list[Any]:
    """参考 :56-63：根是数组直接用；否则按序找第一个数组值的键。"""
    if isinstance(parsed, list):
        return parsed
    if not isinstance(parsed, Mapping):
        return []
    for key in keys:
        if isinstance(parsed.get(key), list):
            return parsed[key]
    return []


def build_typo_check_messages(bid_documents: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """参考 `buildTypoCheckMessages` :196-214（两条 user 消息**逐字**）。"""
    input_params = {"bidDocuments": list(bid_documents)}
    return [
        {"role": "user", "content": f"""【错别字检查输入 v2｜投标文件原文】
以下是本次需要一起检查的多份投标文件 Markdown 原文。每份文件都有唯一 bidDocumentId。后续只能检查这些原文中真实存在的文字，每条结果必须返回所属 bidDocumentId。

{format_bid_documents_for_prompt(input_params)}"""},
        {"role": "user", "content": """【错别字检查任务 v1】
请检查投标文件中的错别字、明显别字、同音错字、形近错字和明显录入错误，并输出 JSON。

检查要求：
1. 只输出你高度确信的错别字，不输出风格建议、标点偏好、表达优化或术语争议。
2. 每条必须来自某一份投标文件原文，wrongText 必须是原文中出现的原始错字或短词，bidDocumentId 必须是输入中提供的真实 ID。
3. correctText 是建议改成的正确字词。
4. originalExcerpt 尽量摘录包含 wrongText 的原文短片段，便于程序校验；不要改写原文。
5. 如果没有明确错别字，返回 {"findings":[]}。

JSON 格式：{"findings":[{"bidDocumentId":"对应投标文件的 bidDocumentId","wrongText":"原文中的错别字或短词","correctText":"建议正确字词","originalExcerpt":"包含错别字的原文短片段","reason":"为什么判断为错别字"}]}

仅输出 JSON，不要输出 Markdown、代码块或解释。"""},
    ]


def find_verified_typo_position(bid_content: str, wrong_text: str,
                                original_excerpt: str,
                                options: Mapping[str, Any] | None = None) -> int:
    """参考 :268-285：优先「摘要内定位 + 段内偏移」，逐级回退到全文 indexOf；-1 = 校验失败。"""
    if not wrong_text:
        return -1
    options = options or {}
    segment_start = options.get("segmentStartOffset")
    segment_end = options.get("segmentEndOffset")
    start = (max(0, int(segment_start)) if _is_finite_number(segment_start) else 0)
    end = (min(len(bid_content), max(start, int(segment_end)))
           if _is_finite_number(segment_end) else len(bid_content))
    if start > 0 or end < len(bid_content):
        segment_content = bid_content[start:end]
        if original_excerpt:
            excerpt_index = segment_content.find(original_excerpt)
            wrong_index_in_excerpt = original_excerpt.find(wrong_text)
            if excerpt_index >= 0 and wrong_index_in_excerpt >= 0:
                return start + excerpt_index + wrong_index_in_excerpt
        wrong_index_in_segment = segment_content.find(wrong_text)
        if wrong_index_in_segment >= 0:
            return start + wrong_index_in_segment
    if original_excerpt:
        excerpt_index = bid_content.find(original_excerpt)
        wrong_index_in_excerpt = original_excerpt.find(wrong_text)
        if excerpt_index >= 0 and wrong_index_in_excerpt >= 0:
            return excerpt_index + wrong_index_in_excerpt
    return bid_content.find(wrong_text)


def _is_finite_number(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number


def create_verified_typo_excerpt(bid_content: str, position: int, wrong_text: str) -> str:
    """参考 :294-310：位置前后取 `typoExcerptRadius` 半径，并**避开 HTML 标签截断**。"""
    start = max(0, position - TYPO_EXCERPT_RADIUS)
    end = min(len(bid_content), position + len(wrong_text) + TYPO_EXCERPT_RADIUS)
    start_tag_open = bid_content.rfind("<", 0, start + 1)
    start_tag_close = bid_content.rfind(">", 0, start + 1)
    if start_tag_open > start_tag_close:
        tag_end = bid_content.find(">", start)
        if 0 <= tag_end < position:
            start = tag_end + 1
    end_tag_open = bid_content.rfind("<", 0, end + 1)
    end_tag_close = bid_content.rfind(">", 0, end + 1)
    if end_tag_open > end_tag_close:
        tag_end = bid_content.find(">", end)
        if tag_end >= 0:
            end = min(len(bid_content), tag_end + 1)
    return bid_content[start:end].strip()


def create_line_location_hint(bid_content: str, position: int) -> str:
    """参考 :312-315：按换行数算「原文第 N 行附近」。"""
    before = bid_content[:max(0, position)]
    line_count = len(re.split(r"\r\n|\r|\n", before))
    return f"原文第 {line_count} 行附近"


def normalize_typo_check_findings(parsed: Any,
                                  bid_documents: Sequence[Mapping[str, Any]],
                                  options: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """参考 `normalizeTypoCheckFindings` :317-349。

    返回**带 position** 的临时行（position 参与**去重键**与摘录/行号推导，
    不落 `rejection_check_typo_findings` 表——该表没有 position 列）。
    """
    documents = list(bid_documents) if isinstance(bid_documents, list) else []
    bid_document_ids = {document["id"] for document in documents
                        if document.get("id")}
    document_map = {document["id"]: document for document in documents
                    if document.get("id")}
    seen: set[str] = set()
    findings: list[dict[str, Any]] = []
    for item in get_array_payload(parsed, ("findings", "items", "typos")):
        if not isinstance(item, Mapping) or isinstance(item, list):
            continue
        bid_document_id = get_bid_document_id_from_item(item, bid_document_ids)
        bid_document = document_map.get(bid_document_id)
        if not bid_document or not bid_document.get("content"):
            continue
        wrong_text = normalize_text(_first_of(
            item, ("wrongText", "wrong_text", "wrong", "typo")))[:60]
        correct_text = normalize_text(_first_of(
            item, ("correctText", "correct_text", "correct", "suggestion")))[:60]
        original_excerpt = normalize_text(_first_of(
            item, ("originalExcerpt", "original_excerpt", "excerpt", "context")))
        reason = (normalize_text(_first_of(item, ("reason", "riskReason", "detail")))
                  or "疑似错别字，请结合原文复核。")
        if not wrong_text or not correct_text or wrong_text == correct_text:
            continue
        position = find_verified_typo_position(bid_document["content"], wrong_text,
                                               original_excerpt, options)
        if position < 0:
            continue                                   # **原文校验失败 → 丢弃**
        key = f"{bid_document_id}\u0000{wrong_text}\u0000{correct_text}\u0000{position}"
        if key in seen:
            continue
        seen.add(key)
        findings.append({
            "id": normalize_text(item.get("id")) or create_id("typo_finding"),
            "bidDocumentId": bid_document_id,
            "wrongText": wrong_text,
            "correctText": correct_text,
            "originalExcerpt": create_verified_typo_excerpt(
                bid_document["content"], position, wrong_text),
            "reason": reason,
            "locationHint": create_line_location_hint(bid_document["content"], position),
            "position": position,
        })
    return findings


def _first_of(item: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = item.get(key)
        if value:
            return value
    return None
