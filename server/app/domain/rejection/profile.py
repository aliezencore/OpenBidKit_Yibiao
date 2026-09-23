"""投标主体档案的行映射（纯函数，对齐 `rejectionCheckStore.cjs:1234-1310`）。

**关键事实**：`identificationTarget` 没有独立列——它和 `items` / `preselectIndex`
一起塞在 **`candidates_json`** 里（`{items, preselectIndex, identificationTarget}`）。
`saveSubjectProfile` 的「未携带 identificationTarget = 保留旧值」就是在组装这个
JSON 之前把它补回去（:1263-1268）。
"""

from __future__ import annotations

import json
from typing import Any, Mapping

# 参考 :1250：主体档案的状态词表与 extraction/results **不同**
SUBJECT_PROFILE_STATUSES = (
    "idle", "identifying", "pending-confirmation", "confirmed", "error",
)

DEFAULT_SUBJECT_PROFILE: dict[str, Any] = {
    "status": "idle",
    "officialName": "",
    "aliases": [],
    "candidates": [],
}


def normalize_identification_target(target: Any) -> dict[str, Any] | None:
    """参考 :1234-1242：只认 `{mode:'all'}` 与「带非空 bidDocumentId 的 single」。"""
    if not isinstance(target, Mapping):
        return None
    if target.get("mode") == "all":
        return {"mode": "all"}
    if target.get("mode") != "single":
        return None
    bid_document_id = target.get("bidDocumentId")
    if isinstance(bid_document_id, str) and bid_document_id:
        return {"mode": "single", "bidDocumentId": bid_document_id}
    return None


def _json_or_none(value: Any) -> str | None:
    """参考 `jsonOrNull`：None/undefined → NULL，其余 JSON 序列化（不转义非 ASCII）。"""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _candidates_payload(candidates_json: Any) -> Mapping[str, Any]:
    """参考 :1247-1248 的容错：坏 JSON → `{}`；payload 本身是数组也认。"""
    if not candidates_json:
        return {}
    try:
        payload = json.loads(candidates_json)
    except (TypeError, ValueError):
        return {}
    if isinstance(payload, Mapping):
        return payload
    return {"items": payload} if isinstance(payload, list) else {}


def subject_profile_view(row: Mapping[str, Any] | None) -> dict[str, Any]:
    """参考 `loadSubjectProfile` :1244-1262；缺行 → 默认档案。"""
    if row is None:
        return dict(DEFAULT_SUBJECT_PROFILE)
    payload = _candidates_payload(row.get("candidates_json"))
    items = payload.get("items")
    if not isinstance(items, list):
        items = []
    aliases = []
    if row.get("aliases_json"):
        try:
            parsed = json.loads(row["aliases_json"])
        except (TypeError, ValueError):
            parsed = []
        aliases = parsed if isinstance(parsed, list) else []
    preselect = payload.get("preselectIndex")
    view: dict[str, Any] = {
        "status": (row["status"] if row.get("status") in SUBJECT_PROFILE_STATUSES else "idle"),
        "officialName": row.get("official_name") or "",
        "aliases": aliases,
        "candidates": items,
    }
    # 参考：只有整数才回传（注意保存侧缺省写的是 -1，也是整数 → 会回传 -1）
    if isinstance(preselect, int) and not isinstance(preselect, bool):
        view["preselectIndex"] = preselect
    target = normalize_identification_target(payload.get("identificationTarget"))
    if target is not None:
        view["identificationTarget"] = target
    for key, column in (("bidSignature", "bid_signature"),
                        ("confirmedAt", "confirmed_at"),
                        ("error", "error"),
                        ("updatedAt", "updated_at")):
        if row.get(column):
            view[key] = row[column]
    return view


def subject_profile_values(profile: Mapping[str, Any] | None,
                           identification_target: Any, timestamp: str) -> dict[str, Any]:
    """参考 `saveSubjectProfile` :1269-1285 的写侧组装。

    `identification_target` 由调用方决定（repository 负责「未携带则保留旧值」的补回）。
    """
    source = profile or {}
    preselect = source.get("preselectIndex")
    payload = {
        "items": source.get("candidates") if isinstance(source.get("candidates"), list) else [],
        "preselectIndex": preselect if isinstance(preselect, int) and not isinstance(preselect, bool) else -1,
        "identificationTarget": identification_target or None,
    }
    return {
        "status": (source.get("status") if source.get("status") in SUBJECT_PROFILE_STATUSES
                   else "idle"),
        "official_name": str(source.get("officialName") or ""),
        "aliases_json": _json_or_none(source.get("aliases")
                                      if isinstance(source.get("aliases"), list) else []),
        "candidates_json": _json_or_none(payload),
        "bid_signature": str(source["bidSignature"]) if source.get("bidSignature") else None,
        "confirmed_at": str(source["confirmedAt"]) if source.get("confirmedAt") else None,
        "error": str(source["error"]) if source.get("error") else None,
        "updated_at": timestamp,
    }
