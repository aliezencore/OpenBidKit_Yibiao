from __future__ import annotations

import dataclasses

import pytest

from app.domain.duplicate.state import (
    ANALYSIS_SECTIONS,
    SECTION_STATUSES,
    SectionState,
    build_section_stats_patch,
    normalize_section_status,
)


def test_analysis_sections_order_is_fixed():
    assert ANALYSIS_SECTIONS == ("metadata", "outline", "content", "image")


def test_section_constants_are_consistent():
    assert set(ANALYSIS_SECTIONS) == {"metadata", "outline", "content", "image"}
    assert SECTION_STATUSES == frozenset({"pending", "running", "success", "error"})


def test_normalize_section_status_accepts_known_values():
    for value in ("pending", "running", "success", "error"):
        assert normalize_section_status(value) == value


def test_normalize_section_status_falls_back_on_unknown():
    assert normalize_section_status("weird") == "pending"
    assert normalize_section_status(None) == "pending"
    assert normalize_section_status(123) == "pending"


def test_normalize_section_status_honors_custom_fallback():
    assert normalize_section_status("weird", fallback="error") == "error"


def test_section_state_defaults():
    s = SectionState()
    assert (s.status, s.progress, s.message) == ("pending", 0, "")
    assert s.signature is None and s.stats == {}


def test_section_state_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        SectionState().status = "x"


def test_section_state_frozen_is_shallow_only():
    """F4：`frozen=True` 只拦属性重新绑定，拦不住 `stats` 的**就地改写**。

    这条不是「应该发生」的理想行为，而是**现状的精确刻画**——写下来是为了
    防止下游把 `SectionState` 当成深不可变的共享值（那样会踩到静默的串改）。
    同时也钉住它**不可哈希**（`dict` 字段），避免有人误以为能塞进 `set`/当 key。
    """
    state = SectionState()
    state.stats["injected"] = 1  # frozen 不拦这一手
    assert state.stats == {"injected": 1}
    with pytest.raises(TypeError):
        hash(state)


def test_section_state_instances_do_not_share_stats_dict():
    """`default_factory=dict` 的意义：每个实例拿到**各自**的空 dict。

    若误写成 `stats: Mapping[str, Any] = {}`（可变默认值），两个实例会共享同一个
    dict，一处改到处变。这条钉住工厂语义。
    """
    a, b = SectionState(), SectionState()
    a.stats["x"] = 1
    assert b.stats == {} and a.stats is not b.stats


def test_stats_patch_metadata_whitelist():
    analysis = {"contentExtraction": {"a": 1}, "metadataExtraction": {"b": 2},
                "logs": ["x"], "files": [{"file_id": "f", "file_name": "n",
                                          "status": "success", "error": None,
                                          "extra": "dropped"}],
                "unknownKey": "dropped"}
    patch = build_section_stats_patch("metadata", analysis)
    assert set(patch) == {"contentExtraction", "metadataExtraction", "logs", "files"}
    assert patch["files"] == [{"file_id": "f", "file_name": "n",
                               "status": "success", "error": None}]


def test_stats_patch_omits_absent_keys():
    """"hasOwn" 语义：键不存在则不产出（区别于存在但为 None）。"""
    patch = build_section_stats_patch("metadata", {})
    assert patch == {}


def test_stats_patch_includes_key_present_with_none_value():
    patch = build_section_stats_patch("metadata", {"logs": None})
    assert patch == {"logs": None}


def test_stats_patch_non_list_files_becomes_empty_list():
    """键存在但值不是列表 → 产出空列表（不是跳过该键）。

    对齐 :186-187 的 `Array.isArray(analysis.files) ? analysis.files : []`。
    """
    patch = build_section_stats_patch("metadata", {"files": "oops"})
    assert patch == {"files": []}


def test_stats_patch_outline_whitelist():
    analysis = {"tenderSentenceCount": 3, "tenderMatchedItemCount": 1,
                "extraction": {"e": 1}, "nope": 1,
                "files": [{"file_id": "f", "file_name": "n", "status": "success",
                           "source": "heading", "confidence": 0.8,
                           "item_count": 2, "tender_matched_count": 1,
                           "error": None, "extra": "dropped"}]}
    patch = build_section_stats_patch("outline", analysis)
    assert set(patch) == {"tenderSentenceCount", "tenderMatchedItemCount",
                          "extraction", "files"}
    assert patch["files"][0] == {"file_id": "f", "file_name": "n",
                                 "status": "success", "source": "heading",
                                 "confidence": 0.8, "item_count": 2,
                                 "tender_matched_count": 1, "error": None}


def test_stats_patch_content_whitelist():
    analysis = {"tenderSentenceCount": 1, "tenderMatchedSentenceCount": 2,
                "totalSentenceCount": 3, "extraction": {"e": 1}, "nope": 1}
    patch = build_section_stats_patch("content", analysis)
    assert set(patch) == {"tenderSentenceCount", "tenderMatchedSentenceCount",
                          "totalSentenceCount", "extraction"}


def test_stats_patch_image_whitelist():
    analysis = {"extraction": {"e": 1}, "totalImageCount": 5, "nope": 1}
    patch = build_section_stats_patch("image", analysis)
    assert set(patch) == {"extraction", "totalImageCount"}


def test_stats_patch_unknown_section_is_empty():
    assert build_section_stats_patch("nope", {"a": 1}) == {}


def test_stats_patch_empty_files_list_stays_empty_list():
    assert build_section_stats_patch("metadata", {"files": []}) == {"files": []}
