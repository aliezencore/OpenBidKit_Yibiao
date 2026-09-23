"""视觉资产的行映射（纯函数，对齐 `rejectionCheckStore.cjs:1347-1405`）。

注意 `relative_path` 在参考里是「相对媒体根目录」的路径（落盘在
`subjectMediaFiles` 管理的目录里）；服务端的盘上布局由 P4c 决定，本模块只做行映射。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

PLACEMENTS = ("anchor", "inline", "unknown")
ASSET_KINDS = ("standalone", "page-scan", "unknown")


def _round_clamped(value: Any) -> int:
    """参考 `Math.max(0, Math.round(Number(x || 0)))`。"""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0
    if number != number:  # NaN
        return 0
    return max(0, round(number))


def visual_asset_values(bid_document_id: str, asset: Mapping[str, Any], index: int,
                        timestamp: str) -> dict[str, Any]:
    return {
        "asset_id": str(asset.get("id") or f"{bid_document_id}-asset-{index + 1}"),
        "bid_document_id": bid_document_id,
        "media_name": str(asset.get("mediaName") or ""),
        "content_hash": str(asset.get("contentHash") or ""),
        "relative_path": str(asset.get("relativePath") or ""),
        "mime_type": str(asset.get("mimeType") or "application/octet-stream"),
        "width": _round_clamped(asset.get("width")),
        "height": _round_clamped(asset.get("height")),
        "placement": (asset.get("placement") if asset.get("placement") in PLACEMENTS
                      else "unknown"),
        "asset_kind": (asset.get("assetKind") if asset.get("assetKind") in ASSET_KINDS
                       else "unknown"),
        "location_hint": str(asset.get("locationHint") or ""),
        "nearby_text": str(asset.get("nearbyText") or ""),
        "section_title": str(asset["sectionTitle"]) if asset.get("sectionTitle") else None,
        "sort_order": index,
        "created_at": timestamp,
    }


def visual_assets_values(bid_document_id: str, assets: Sequence[Mapping[str, Any]] | None,
                         timestamp: str) -> list[dict[str, Any]]:
    return [visual_asset_values(bid_document_id, asset, index, timestamp)
            for index, asset in enumerate(assets or [])]


def visual_asset_view(row: Mapping[str, Any]) -> dict[str, Any]:
    view = {
        "id": row["asset_id"],
        "bidDocumentId": row["bid_document_id"],
        "mediaName": row["media_name"],
        "contentHash": row["content_hash"],
        "relativePath": row["relative_path"],
        "mimeType": row["mime_type"],
        "width": int(row["width"] or 0),
        "height": int(row["height"] or 0),
        "placement": row["placement"],
        "assetKind": row["asset_kind"],
        "locationHint": row["location_hint"],
        "nearbyText": row["nearby_text"],
    }
    # 参考 :1363：sectionTitle 为空时**不出现**该键
    if row["section_title"]:
        view["sectionTitle"] = row["section_title"]
    return view


def visual_assets_view(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [visual_asset_view(row) for row in rows]
