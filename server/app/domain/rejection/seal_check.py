"""印章视觉检查的分级与统计（P4c-1，纯函数）。

逐字移植 `client/electron/services/subjectCheckTask.cjs`：
`createEmptyVisionStats` :415-426、`createUnrecognizedEntry` :428-443、
`CONFIRMED_CONFIDENCE` :446、`classifySealFinding` :448-490、
`runSubjectSealCheck` 的 visionStatus 判定（:625-633）。

VLM provider 本体（subjectVisionProvider.cjs 的两阶段协议）与 docx 图片提取
另行交付（P4c-2/3）；本模块只承载「拿到 providerAsset 之后」的全部分级语义。
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from app.domain.rejection.subject_body import is_near_match_subject_name

# 明确不一致的置信度门槛（spec §10.1 / §10.2-5）
CONFIRMED_CONFIDENCE = 0.85

_SEAL_ROLES = ("third-party", "bidder", "unclear", "")


def create_empty_vision_stats() -> dict[str, Any]:
    """参考 :415-426。"""
    return {
        "totalAssets": 0,
        "candidateAssets": 0,
        "inspectedAssets": 0,
        "sealAssets": 0,
        "skippedAssets": 0,
        "reviewAssets": 0,
        "failedAssets": 0,
    }


def create_unrecognized_entry(asset: Mapping[str, Any], ordinal: int, *,
                              kind: str, reason: Any = None,
                              fmt: Any = None, label: Any = None) -> dict[str, Any]:
    """参考 :428-443：Excel「未能识别的图片」清单项。"""
    return {
        "assetId": asset.get("id"),
        "bidDocumentId": asset.get("bidDocumentId"),
        "ordinal": ordinal,
        "sectionTitle": str(asset.get("sectionTitle") or ""),
        "locationHint": asset.get("locationHint") or "",
        "mediaName": asset.get("mediaName") or "",
        "mimeType": asset.get("mimeType") or "",
        "format": fmt or "",
        "label": label or "",
        "kind": kind,
        "reason": str(reason or ""),
    }


def classify_seal_finding(asset: Mapping[str, Any], provider_asset: Mapping[str, Any] | None,
                          *, official_name: Any,
                          aliases: Sequence[Any] | None = None) -> dict[str, Any] | None:
    """参考 :448-490：单资产分级（含线上教训的抑制与降级规则，注释见参考源码）。

    - 只看 `status='inspected'` 且 `hasSeal` 的资产；
    - 印章文字命中正式名/简称 → 投标方自身印章，不报警；
    - third-party 且**角色置信度 ≥0.85** 才允许抑制（低置信度猜测降级进需人工复核）；
    - seal/page 角色均为 unclear → 不报警；
    - 近名（编辑距离/子串/数字噪声）或双置信度未达 0.85 → needs-review；
    - level=confirmed-mismatch 需 bidder 之外：双置信 ≥0.85 且非近名。
    """
    if (not provider_asset or provider_asset.get("status") != "inspected"
            or not provider_asset.get("hasSeal")):
        return None
    detected = str(provider_asset.get("detectedSubject")
                   or provider_asset.get("sealText") or "").strip()
    if not detected:
        return None
    if matches_allowed_subject(detected, official_name, aliases):
        return None                                   # 投标方自身印章
    role_confidence = _number(provider_asset.get("roleConfidence"))
    if ((provider_asset.get("sealRole") == "third-party"
         or provider_asset.get("pageRole") == "third-party")
            and role_confidence >= CONFIRMED_CONFIDENCE):
        return None
    if provider_asset.get("pageRole") == "unclear" and \
            provider_asset.get("sealRole") == "unclear":
        return None                                   # 角色不清且无投标方要求证据
    near_match_official = is_near_match_subject_name(detected, official_name, aliases)
    name_confidence = _number(provider_asset.get("nameConfidence"))
    level = ("confirmed-mismatch"
             if not near_match_official and name_confidence >= CONFIRMED_CONFIDENCE
             and role_confidence >= CONFIRMED_CONFIDENCE else "needs-review")
    is_scan = asset.get("assetKind") == "page-scan"
    return {
        "id": f"subject-seal-{asset.get('id')}",
        "bidDocumentId": asset.get("bidDocumentId"),
        "level": level,
        "source": "seal",
        "expectedSubject": official_name,
        "detectedSubject": detected,
        "locationKind": "scanned-page" if is_scan else "standalone-image",
        "title": "整页扫描件印章主体异常" if is_scan else "图片印章主体异常",
        "summary": (f"{asset.get('sectionTitle') or asset.get('locationHint') or '图片位置'}"
                    f"处的印章识别为“{detected}”"),
        "locationHint": " / ".join([part for part in
                                    (str(asset.get("sectionTitle") or ""),
                                     str(asset.get("locationHint") or "")) if part]),
        "originalText": str(provider_asset.get("sealText") or detected),
        "roleReason": str(provider_asset.get("reason") or ""),
        "mismatchReason": f"印章主体与正式投标主体“{official_name}”不一致",
        "suggestion": "请核对该位置是否应为投标方盖章，必要时替换为投标方公章",
        "confidence": min(name_confidence, role_confidence),
        "visualAssetId": asset.get("id"),
        "assetRelativePath": asset.get("relativePath"),
    }


def compute_vision_status(*, provider_status: Any, stats: Mapping[str, Any]) -> str:
    """参考 :625-633：全失败（含 provider error）→ error；部分失败或全跳过 → partial；
    其余 success。unavailable/disabled 在调用方提前返回。"""
    failed = int(stats.get("failedAssets") or 0)
    inspected = int(stats.get("inspectedAssets") or 0)
    skipped = int(stats.get("skippedAssets") or 0)
    all_failed = failed > 0 and inspected + skipped == 0
    none_inspected = inspected == 0 and skipped > 0
    return ("error" if provider_status == "error" or all_failed
            else ("partial" if failed > 0 or none_inspected else "success"))


def merge_vision_findings(text_findings: Sequence[Mapping[str, Any]],
                          seal_findings: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """正文 + 印章 findings 合并（id 各自独立，按来源排序：text 先、seal 后——
    与参考 `runSubjectCheck` 的合并顺序一致）。"""
    return [dict(finding) for finding in [*text_findings, *seal_findings]]


def _number(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return max(0.0, min(1.0, number))


def matches_allowed_subject(value: Any, official_name: Any,
                            aliases: Sequence[Any] | None = None) -> bool:
    """转发 subject_body 的同名判定（避免 seal 模块重复实现）。"""
    from app.domain.rejection.subject_body import matches_allowed_subject as _impl

    return _impl(value, official_name, aliases)


_STRIP_NON_WORD = re.compile(r"\s+")
