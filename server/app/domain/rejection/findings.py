"""四类 findings 的行映射（纯函数）。

逐字对齐 `client/electron/services/rejectionCheckStore.cjs`：
- 写入侧 `saveFindingRows` :644-745（含默认 id、枚举兜底、confidence 夹取）
- 读取侧 `loadFindingRows` :766-820（camelCase + `bidDocumentId` 的兜底）
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

RESULT_TYPES = ("rejection", "typo", "logic", "subject")
FINDING_DEFAULT_PREFIX = {
    "rejection": "rejection-finding",
    "typo": "typo-finding",
    "logic": "logic-finding",
    "subject": "subject-finding",
}
RISK_TYPES = ("invalidBid", "rejectionItem")
SEVERITIES = ("high", "medium", "low")
SUBJECT_LEVELS = ("confirmed-mismatch", "needs-review")
SUBJECT_LOCATION_KINDS = ("body", "table", "signature", "standalone-image", "scanned-page")


def _finding_id(item: Mapping[str, Any], result_type: str, index: int) -> str:
    """参考用 `item.id || default`（**falsy 也算缺失**，如空串/0）。"""
    return str(item.get("id") or f"{FINDING_DEFAULT_PREFIX[result_type]}-{index + 1}")


def _optional(value: Any) -> str | None:
    """参考的 `x ? String(x) : null`：falsy（含空串）→ NULL。"""
    return str(value) if value else None


def _clamp_confidence(value: Any) -> float:
    """`Math.max(0, Math.min(1, Number(x || 0)))`。

    偏离（登记）：JS 对不可解析值产出 **NaN** 并交给驱动绑定；本实现回落到 0.0
    （NaN 不是有意义的置信度，且 SQLite REAL 无法表达它）。
    """
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return max(0.0, min(1.0, number))


def risk_finding_values(item: Mapping[str, Any], index: int, timestamp: str) -> dict[str, Any]:
    return {
        "finding_id": _finding_id(item, "rejection", index),
        "bid_document_id": _optional(item.get("bidDocumentId")),
        "type": item.get("type") if item.get("type") in RISK_TYPES else "rejectionItem",
        "severity": item.get("severity") if item.get("severity") in SEVERITIES else "medium",
        "title": str(item.get("title") or ""),
        # 参考 :666：summary 回落到 title
        "summary": str(item.get("summary") or item.get("title") or ""),
        "requirement": str(item.get("requirement") or ""),
        "bid_evidence": str(item.get("bidEvidence") or ""),
        "risk_reason": str(item.get("riskReason") or ""),
        "suggestion": str(item.get("suggestion") or ""),
        "sort_order": index,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def typo_finding_values(item: Mapping[str, Any], index: int, timestamp: str) -> dict[str, Any]:
    return {
        "finding_id": _finding_id(item, "typo", index),
        "bid_document_id": _optional(item.get("bidDocumentId")),
        "wrong_text": str(item.get("wrongText") or ""),
        "correct_text": str(item.get("correctText") or ""),
        "original_excerpt": str(item.get("originalExcerpt") or ""),
        "reason": str(item.get("reason") or ""),
        "location_hint": _optional(item.get("locationHint")),
        "sort_order": index,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def logic_finding_values(item: Mapping[str, Any], index: int, timestamp: str) -> dict[str, Any]:
    return {
        "finding_id": _finding_id(item, "logic", index),
        "bid_document_id": _optional(item.get("bidDocumentId")),
        "title": str(item.get("title") or ""),
        "original_text": str(item.get("originalText") or ""),
        "location_hint": str(item.get("locationHint") or ""),
        "fallacy_reason": str(item.get("fallacyReason") or ""),
        "suggestion": str(item.get("suggestion") or ""),
        "sort_order": index,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def subject_finding_values(item: Mapping[str, Any], index: int, timestamp: str) -> dict[str, Any]:
    return {
        "finding_id": _finding_id(item, "subject", index),
        "bid_document_id": _optional(item.get("bidDocumentId")),
        "level": item.get("level") if item.get("level") in SUBJECT_LEVELS else "needs-review",
        # 参考 :725：只有 'seal' 是印章，其余一律 'text'
        "source": "seal" if item.get("source") == "seal" else "text",
        "expected_subject": str(item.get("expectedSubject") or ""),
        "detected_subject": str(item.get("detectedSubject") or ""),
        "location_kind": (item.get("locationKind")
                          if item.get("locationKind") in SUBJECT_LOCATION_KINDS else "body"),
        "title": str(item.get("title") or ""),
        "summary": str(item.get("summary") or ""),
        "location_hint": str(item.get("locationHint") or ""),
        "original_text": str(item.get("originalText") or ""),
        "role_reason": str(item.get("roleReason") or ""),
        "mismatch_reason": str(item.get("mismatchReason") or ""),
        "suggestion": str(item.get("suggestion") or ""),
        "confidence": _clamp_confidence(item.get("confidence")),
        "visual_asset_id": _optional(item.get("visualAssetId")),
        "asset_relative_path": _optional(item.get("assetRelativePath")),
        "seal_crop_relative_path": _optional(item.get("sealCropRelativePath")),
        "sort_order": index,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


_BUILDERS = {
    "rejection": risk_finding_values,
    "typo": typo_finding_values,
    "logic": logic_finding_values,
    "subject": subject_finding_values,
}


def findings_values(result_type: str, findings: Sequence[Mapping[str, Any]] | None,
                    timestamp: str) -> list[dict[str, Any]]:
    builder = _BUILDERS[result_type]
    return [builder(item, index, timestamp) for index, item in enumerate(findings or [])]


def risk_finding_view(row: Mapping[str, Any], fallback_document_id: str) -> dict[str, Any]:
    return {
        "id": row["finding_id"],
        # 参考 :771：bid_document_id 为空时回落到「本项目第一份投标文件」
        "bidDocumentId": row["bid_document_id"] or fallback_document_id,
        "type": row["type"],
        "severity": row["severity"],
        "title": row["title"],
        "summary": row["summary"],
        "requirement": row["requirement"],
        "bidEvidence": row["bid_evidence"],
        "riskReason": row["risk_reason"],
        "suggestion": row["suggestion"],
    }


def typo_finding_view(row: Mapping[str, Any], fallback_document_id: str) -> dict[str, Any]:
    return {
        "id": row["finding_id"],
        "bidDocumentId": row["bid_document_id"] or fallback_document_id,
        "wrongText": row["wrong_text"],
        "correctText": row["correct_text"],
        "originalExcerpt": row["original_excerpt"],
        "reason": row["reason"],
        "locationHint": row["location_hint"] or None,
    }


def logic_finding_view(row: Mapping[str, Any], fallback_document_id: str) -> dict[str, Any]:
    return {
        "id": row["finding_id"],
        "bidDocumentId": row["bid_document_id"] or fallback_document_id,
        "title": row["title"],
        "originalText": row["original_text"],
        "locationHint": row["location_hint"],
        "fallacyReason": row["fallacy_reason"],
        "suggestion": row["suggestion"],
    }


def subject_finding_view(row: Mapping[str, Any], fallback_document_id: str) -> dict[str, Any]:
    view = {
        "id": row["finding_id"],
        "bidDocumentId": row["bid_document_id"] or fallback_document_id,
        "level": row["level"],
        "source": row["source"],
        "expectedSubject": row["expected_subject"],
        "detectedSubject": row["detected_subject"],
        "locationKind": row["location_kind"],
        "title": row["title"],
        "summary": row["summary"],
        "locationHint": row["location_hint"],
        "originalText": row["original_text"],
        "roleReason": row["role_reason"],
        "mismatchReason": row["mismatch_reason"],
        "suggestion": row["suggestion"],
        "confidence": float(row["confidence"] or 0),
    }
    for key, column in (("visualAssetId", "visual_asset_id"),
                        ("assetRelativePath", "asset_relative_path"),
                        ("sealCropRelativePath", "seal_crop_relative_path")):
        if row[column]:
            view[key] = row[column]
    return view


_VIEWS = {
    "rejection": risk_finding_view,
    "typo": typo_finding_view,
    "logic": logic_finding_view,
    "subject": subject_finding_view,
}

FINDING_TABLES = {
    "rejection": "rejection_check_risk_findings",
    "typo": "rejection_check_typo_findings",
    "logic": "rejection_check_logic_findings",
    "subject": "rejection_check_subject_findings",
}


def findings_view(result_type: str, rows: Sequence[Mapping[str, Any]],
                  fallback_document_id: str) -> list[dict[str, Any]]:
    build = _VIEWS[result_type]
    return [build(row, fallback_document_id) for row in rows]
