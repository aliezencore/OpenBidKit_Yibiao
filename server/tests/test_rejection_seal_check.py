"""P4c-1：印章视觉分级与统计（classifySealFinding / visionStatus / unrecognized 清单）。

真值来源 `client/electron/services/subjectCheckTask.cjs`：
`createEmptyVisionStats` :415、`createUnrecognizedEntry` :428、`CONFIRMED_CONFIDENCE` :446、
`classifySealFinding` :448-490、visionStatus 判定 :625-633。
"""

from __future__ import annotations

from app.domain.rejection.seal_check import (
    classify_seal_finding,
    compute_vision_status,
    create_empty_vision_stats,
    create_unrecognized_entry,
)
from app.domain.rejection.subject_body import is_near_match_subject_name

ASSET = {"id": "asset-1", "bidDocumentId": "bid-a", "assetKind": "standalone",
         "sectionTitle": "投标函", "locationHint": "第 2 页",
         "mediaName": "seal.png", "mimeType": "image/png"}


def _provider(**overrides):
    base = {"status": "inspected", "hasSeal": True,
            "detectedSubject": "乙方建设公司", "sealText": "乙方建设公司公章",
            "sealRole": "bidder", "pageRole": "bidder",
            "nameConfidence": 0.9, "roleConfidence": 0.9, "reason": "印章清晰"}
    base.update(overrides)
    return base


def test_classify_confirmed_mismatch_at_threshold():
    finding = classify_seal_finding(ASSET, _provider(), official_name="甲公司",
                                    aliases=["甲"])
    assert finding is not None
    assert finding["level"] == "confirmed-mismatch"
    assert finding["source"] == "seal"
    assert finding["id"] == "subject-seal-asset-1"        # 参考的 id 规则
    assert finding["locationKind"] == "standalone-image"
    assert finding["detectedSubject"] == "乙方建设公司"
    assert finding["confidence"] == 0.9                   # min(name, role)
    assert "乙方建设公司" in finding["summary"]
    assert finding["visualAssetId"] == "asset-1"


def test_classify_scan_page_changes_title_and_location_kind():
    finding = classify_seal_finding(
        {**ASSET, "assetKind": "page-scan"}, _provider(),
        official_name="甲公司")
    assert finding["title"] == "整页扫描件印章主体异常"
    assert finding["locationKind"] == "scanned-page"


def test_classify_official_or_alias_seal_is_silent():
    assert classify_seal_finding(ASSET, _provider(detectedSubject="甲公司"),
                                 official_name="甲公司") is None
    assert classify_seal_finding(ASSET, _provider(detectedSubject="甲"),
                                 official_name="甲公司", aliases=["甲"]) is None


def test_classify_near_match_degrades_to_needs_review():
    """spec §7.4 对称应用：漏字/截断的近名必须人工复核（参考注释的线上实例）。"""
    assert is_near_match_subject_name("乙建设公司", "甲公司") is False
    near = classify_seal_finding(
        ASSET, _provider(detectedSubject="乙方建设有限 公司"),  # 与印章文本不同的近名不可构造——
        official_name="甲公司")                                 # 这里直接验证近名分支：
    assert near is not None
    # 用「检测主体是正式名的近名」构造：印章写着「甲公司」的漏字版
    near_official = classify_seal_finding(
        ASSET, _provider(detectedSubject="甲有限公"), official_name="甲有限公司")
    assert near_official is not None
    assert near_official["level"] == "needs-review"           # 近名不得自动判明确


def test_classify_third_party_suppression_requires_high_confidence():
    """参考 :466-471 注释的线上教训：低置信度 third-party 猜测不得静默吞掉真不一致。"""
    suppressed = classify_seal_finding(
        ASSET, _provider(sealRole="third-party", pageRole="third-party",
                         roleConfidence=0.9), official_name="甲公司")
    assert suppressed is None                                  # 高置信第三方 → 抑制
    kept = classify_seal_finding(
        ASSET, _provider(sealRole="third-party", pageRole="third-party",
                         roleConfidence=0.6), official_name="甲公司")
    assert kept is not None
    assert kept["level"] == "needs-review"                     # 低置信猜测 → 人工复核


def test_classify_unclear_roles_is_silent():
    assert classify_seal_finding(
        ASSET, _provider(sealRole="unclear", pageRole="unclear"),
        official_name="甲公司") is None


def test_classify_skips_non_inspected_or_no_seal():
    assert classify_seal_finding(ASSET, None, official_name="甲公司") is None
    assert classify_seal_finding(ASSET, _provider(status="skipped"),
                                 official_name="甲公司") is None
    assert classify_seal_finding(ASSET, _provider(hasSeal=False),
                                 official_name="甲公司") is None
    # detectedSubject 为空时**回退 sealText**（参考 :456-457）；两者皆空才返回 None
    fallback = classify_seal_finding(ASSET, _provider(detectedSubject=""),
                                     official_name="甲公司")
    assert fallback is not None and fallback["detectedSubject"] == "乙方建设公司公章"
    assert classify_seal_finding(ASSET, _provider(detectedSubject="", sealText=""),
                                 official_name="甲公司") is None


def test_vision_status_matrix():
    """参考 :625-633：全失败→error；部分失败或全跳过→partial；其余 success。"""
    def _stats(**kw):
        return {**create_empty_vision_stats(), **kw}

    assert compute_vision_status(provider_status="success",
                                 stats=_stats(inspectedAssets=3)) == "success"
    assert compute_vision_status(provider_status="error",
                                 stats=_stats(inspectedAssets=2)) == "error"
    assert compute_vision_status(provider_status="success",
                                 stats=_stats(failedAssets=2)) == "error"       # 全失败
    assert compute_vision_status(provider_status="success",
                                 stats=_stats(inspectedAssets=2, failedAssets=1)) == "partial"
    assert compute_vision_status(provider_status="success",
                                 stats=_stats(skippedAssets=3)) == "partial"    # 全跳过
    assert compute_vision_status(provider_status="success",
                                 stats=_stats()) == "success"                   # 零资产矩阵给 success


def test_unrecognized_entry_shape():
    entry = create_unrecognized_entry(ASSET, 2, kind="skipped", reason="格式不支持",
                                      fmt="bmp", label="未能识别")
    assert entry == {
        "assetId": "asset-1", "bidDocumentId": "bid-a", "ordinal": 2,
        "sectionTitle": "投标函", "locationHint": "第 2 页", "mediaName": "seal.png",
        "mimeType": "image/png", "format": "bmp", "label": "未能识别",
        "kind": "skipped", "reason": "格式不支持",
    }


def test_empty_vision_stats_shape():
    assert create_empty_vision_stats() == {
        "totalAssets": 0, "candidateAssets": 0, "inspectedAssets": 0,
        "sealAssets": 0, "skippedAssets": 0, "reviewAssets": 0, "failedAssets": 0,
    }
