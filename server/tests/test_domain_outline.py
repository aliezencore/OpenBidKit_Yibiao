"""P3b Task 1 —— 目录纯函数（domain/outline.py）的 14 条行为测试。

全部预期值经 Node 直接运行参考实现
`client/electron/services/duplicateCheckService.cjs`（:1134-1451 + normalizeValue :133-143）
取地面真值核对，非手工推演。
"""

from __future__ import annotations

from app.domain.duplicate.outline import (
    bigram_similarity,
    build_outline_comparison,
    build_outline_items,
    clean_outline_title,
    extract_catalog_outline,
    extract_heading_outline,
    extract_semantic_outline,
    infer_outline_level,
    intersect_size,
    lcs_similarity,
    match_tender_sentence,
    normalize_outline_title,
    parse_catalog_line,
    parse_outline_marker,
    risk_from_score,
    split_tender_sentences,
    strip_markdown_for_outline,
    to_fixed_2,
)


def _mk_file(file_id: str, markdown: str) -> dict:
    """按参考 buildOutlineComparison 的输入形状构造文件结果。"""
    return {
        "file_id": file_id,
        "status": "success",
        "items": build_outline_items(markdown, [])["items"],
    }


# ---------------------------------------------------------------------------
# 1. stripMarkdownForOutline（:1134）
# ---------------------------------------------------------------------------


def test_strip_markdown_for_outline_variants():
    # 图片 → 空格（:1136）
    assert strip_markdown_for_outline("前![alt text](img.png)后") == "前 后"
    # 链接保文字（:1137）
    assert strip_markdown_for_outline("[文字](http://x.com)") == "文字"
    # HTML 标签 → 空格（:1138）
    assert strip_markdown_for_outline("a<div>b</div>c") == "a b c"
    # `*_~ 剥除（:1139）
    assert strip_markdown_for_outline("**粗** _斜_ `码` ~~删~~") == "粗 斜 码 删"
    # HTML 实体（:1140-1143）
    assert strip_markdown_for_outline("A&nbsp;B&lt;C&gt;D&amp;E") == "A B<C>D&E"
    # 实体大小写不敏感（gi）
    assert strip_markdown_for_outline("x&nbsp;Y") == "x Y"


# ---------------------------------------------------------------------------
# 2. normalizeOutlineTitle（:1146）
# ---------------------------------------------------------------------------


def test_normalize_outline_title_strips_numbering_and_punctuation():
    assert normalize_outline_title("1.2.3 技术要求") == "技术要求"
    assert normalize_outline_title("第一章 总则") == "总则"
    # 参考实现先经 normalizeValue 的 NFKC（:139）：全角括号被转成半角，
    # 编号前缀的全角括号分支（:1150）不再命中，标点剥除（:1152）后剩「一概况」。
    # 这是 Node 实测的参考行为（计划初稿预期「概况」与参考不符，以参考为准）。
    assert normalize_outline_title("（一）概况") == "一概况"
    # ① 被 NFKC 转成 ASCII 数字 1，走数字编号前缀分支，结果一致。
    assert normalize_outline_title("① 背景") == "背景"
    assert normalize_outline_title("一、总则") == "总则"
    # 空白剥除 + 标点剥除 + lower（:1151-1153）
    assert normalize_outline_title("A B，要求。") == "ab要求"
    assert normalize_outline_title("Hello, World! 要求") == "helloworld要求"
    assert normalize_outline_title("  ") == ""


# ---------------------------------------------------------------------------
# 3. cleanOutlineTitle（:1156）
# ---------------------------------------------------------------------------


def test_clean_outline_title_strips_page_trails():
    # …{2,} + 页码（:1160）
    assert clean_outline_title("项目概述……12") == "项目概述"
    # \s+\d{1,4} 页尾（:1161）
    assert clean_outline_title("服务要求 8") == "服务要求"
    # 非页尾数字保留
    assert clean_outline_title("第 3 章") == "第 3 章"


# ---------------------------------------------------------------------------
# 4. splitTenderSentences（:1165）
# ---------------------------------------------------------------------------


def test_split_tender_sentences_length_window_and_dedupe():
    long_160 = "字" * 160
    long_161 = "字" * 161
    markdown = f"质量保证措施。技术。\n{long_160}。\n{long_161}。\n质量保证措施"
    sentences = split_tender_sentences(markdown)
    # 「技术」normalized 长度 2 < 6 被滤；161 > 160 被滤；重复 normalized 只留一条。
    assert [s["normalized"] for s in sentences] == ["质量保证措施", long_160]
    assert [s["text"] for s in sentences] == ["质量保证措施", long_160]


# ---------------------------------------------------------------------------
# 5. matchTenderSentence（:1185）
# ---------------------------------------------------------------------------


def test_match_tender_sentence_exact_contains_and_ratio():
    tender = split_tender_sentences("建筑工程施工质量验收统一标准。安全生产管理条例。")
    expected = {"text": "建筑工程施工质量验收统一标准",
                "normalized": "建筑工程施工质量验收统一标准"}
    # 相等命中（:1189）
    assert match_tender_sentence("建筑工程施工质量验收统一标准", tender) == expected
    # 本方 ≥10 且被招标句包含（:1190）
    assert match_tender_sentence("工程施工质量验收统一", tender) == expected
    # 对方 ≥10 且包含本方且长度比 ≥0.8（14/15）（:1191）
    assert match_tender_sentence("建筑工程施工质量验收统一标准表", tender) == expected
    # normalized < 6 拒（:1187）
    assert match_tender_sentence("技术", tender) is None
    # 无命中
    assert match_tender_sentence("完全无关的内容很长的一个标题", tender) is None


# ---------------------------------------------------------------------------
# 6. parseOutlineMarker（:1196）
# ---------------------------------------------------------------------------


def test_parse_outline_marker_five_patterns_and_reject():
    assert parse_outline_marker("1.2.3 技术要求") == {
        "number": "1.2.3", "title": "技术要求", "level": 3,
    }
    assert parse_outline_marker("第三章 质量保证") == {
        "number": "第三章", "title": "质量保证", "level": 1,
    }
    assert parse_outline_marker("一、总则") == {
        "number": "一、", "title": "总则", "level": 1,
    }
    # 输入先经 cleanOutlineTitle（内含 NFKC）：全角括号变半角，模式 4（:1202）
    # 不可达 → None。Node 实测的参考行为（计划初稿预期命中模式 4，以参考为准）。
    assert parse_outline_marker("（二）项目概况") is None
    # ⑨ 被 NFKC 转成 9，实际由模式 1（:1199）命中，level 1（非模式 5 的 level 2）。
    assert parse_outline_marker("⑨ 注意事项") == {
        "number": "9", "title": "注意事项", "level": 1,
    }
    assert parse_outline_marker("1. 技术") == {
        "number": "1", "title": "技术", "level": 1,
    }
    # 无编号行拒
    assert parse_outline_marker("项目概况说明") is None
    # title 剥净后 normalized < 2 拒（「第三章」会被 normalizeOutlineTitle 全剥为空）
    assert parse_outline_marker("第三章") is None


# ---------------------------------------------------------------------------
# 7. inferOutlineLevel（:1216）
# ---------------------------------------------------------------------------


def test_infer_outline_level_rules():
    assert infer_outline_level("1.2.3") == 3
    assert infer_outline_level("1") == 1
    assert infer_outline_level("第三章") == 1
    assert infer_outline_level("（二）") == 2
    assert infer_outline_level("⑨") == 2
    assert infer_outline_level("12.3") == 2
    assert infer_outline_level("abc") == 1


# ---------------------------------------------------------------------------
# 8. extractCatalogOutline（:1239）
# ---------------------------------------------------------------------------

_NOISE = "普通说明内容一行"


def test_extract_catalog_outline_window_and_miss_limit():
    # 窗口 180 行：目录行后索引 1..179 可见；有效行放在索引 180 之外不收。
    window_md = "\n".join(
        ["目录", *([_NOISE] * 178), "1 项目概况……1", "2 技术要求……5", "尾部说明"]
    )
    items = extract_catalog_outline(window_md)
    assert [i["title"] for i in items] == ["项目概况"]
    assert items[0]["source"] == "catalog"
    assert items[0]["confidence"] == 0.92
    assert items[0]["number"] == "1"
    assert items[0]["level"] == 1

    # 连续 10 个未命中（有 items 后）断开：后续有效行不收。
    miss10_md = "\n".join(
        ["目录", "1 项目概况……1", "2 技术要求……5", *([_NOISE] * 10), "3 施工组织……9", "其他"]
    )
    assert [i["title"] for i in extract_catalog_outline(miss10_md)] == ["项目概况", "技术要求"]

    # 9 个未命中不断开：后续有效行仍收。
    miss9_md = "\n".join(["目录", "1 项目概况……1", *([_NOISE] * 9), "2 技术要求……5"])
    assert [i["title"] for i in extract_catalog_outline(miss9_md)] == ["项目概况", "技术要求"]

    # 无目录行 → 空
    assert extract_catalog_outline("普通文档没有目录") == []


# ---------------------------------------------------------------------------
# 9. extractHeadingOutline（:1258） / extractSemanticOutline（:1272）
# ---------------------------------------------------------------------------


def test_extract_heading_and_semantic_confidence_and_caps():
    heading = extract_heading_outline("# 总体要求\n## 1.2.3 技术标准\n目录\n### 附加说明\n## 目录")
    assert [(i["number"], i["title"], i["level"], i["confidence"]) for i in heading] == [
        (None, "总体要求", 1, 0.82),
        ("1.2.3", "技术标准", 2, 0.82),
        (None, "附加说明", 3, 0.82),
    ]
    # 目录标题行（含 ## 目录）被拒
    assert all(i["title"] != "目录" for i in heading)

    semantic = extract_semantic_outline(
        "**重点项目要求**\n一、总体部署\n普通行没有特征。\n| 表格 | 行 |\n目录\n" + "x" * 91 + "长行"
    )
    # 粗体 0.55 / 编号 0.68；句尾标点、表行、超长行、目录行被滤。
    assert [(i["number"], i["title"], i["level"], i["confidence"]) for i in semantic] == [
        (None, "重点项目要求", 2, 0.55),
        ("一、", "总体部署", 1, 0.68),
    ]

    # 截断 260（:1283）
    capped = extract_semantic_outline("\n".join(f"**条目{i}**" for i in range(261)))
    assert len(capped) == 260
    assert capped[0]["title"] == "条目0"
    assert capped[259]["title"] == "条目259"


# ---------------------------------------------------------------------------
# 10. buildOutlineItems（:1286）
# ---------------------------------------------------------------------------


def test_build_outline_items_source_priority_and_stack_levels():
    # catalog ≥3 优先于 heading
    result = build_outline_items("目录\n1 项目概况……1\n2 技术要求……5\n3 施工方案……9\n# 顶层标题\n", [])
    assert result["source"] == "catalog"
    assert result["confidence"] == 0.92
    assert [i["id"] for i in result["items"]] == ["O00001", "O00002", "O00003"]
    assert [i["order"] for i in result["items"]] == [0, 1, 2]
    assert [i["level"] for i in result["items"]] == [1, 1, 1]
    assert [i["parent_id"] for i in result["items"]] == [None, None, None]
    assert [i["normalized_path"] for i in result["items"]] == ["项目概况", "技术要求", "施工方案"]
    assert result["items"][0]["from_tender"] is False
    assert result["items"][0]["duplicate_group_ids"] == []
    assert result["items"][0]["similar_group_ids"] == []

    # 跳跃 level 夹到 stack+1（:1294）
    stacked = build_outline_items("# 一级\n#### 四级\n## 二级\n", [])
    assert [i["level"] for i in stacked["items"]] == [1, 2, 2]
    assert [i["parent_id"] for i in stacked["items"]] == [None, "O00001", "O00001"]
    assert [i["normalized_path"] for i in stacked["items"]] == ["一级", "一级>四级", "一级>二级"]

    # from_tender / matched_tender_sentence
    tender = split_tender_sentences("施工组织设计的要求。")
    matched = build_outline_items("# 施工组织设计的要求\n# 其他标题", tender)
    assert matched["items"][0]["from_tender"] is True
    assert matched["items"][0]["matched_tender_sentence"] == "施工组织设计的要求"
    assert matched["items"][1]["from_tender"] is False
    assert matched["items"][1]["matched_tender_sentence"] is None

    # 三来源全空 → items 空、source None、confidence 0
    empty = build_outline_items("没有目录\n普通内容", [])
    assert empty["items"] == []
    assert empty["source"] is None
    assert empty["confidence"] == 0

    # confidence 平均 half-up（:1325）：heading 2 条（<3）不选中，semantic 4 条选中；
    # (0.68+0.55+0.68+0.55)/4 → toFixed(2) = 0.61（Node 实测）。
    avg = build_outline_items(
        "# 一、总体部署\n**重点项目要求**\n# 二、保障措施\n**次要项目说明**\n", []
    )
    assert avg["source"] == "semantic"
    assert avg["confidence"] == 0.61
    assert [i["level"] for i in avg["items"]] == [1, 2, 1, 2]
    assert [i["parent_id"] for i in avg["items"]] == [None, "O00001", None, "O00003"]


# ---------------------------------------------------------------------------
# 11. buildOutlineComparison —— duplicate 组（:1368-1404）
# ---------------------------------------------------------------------------


def test_build_outline_comparison_duplicate_groups_by_path_then_title():
    # 同 normalized_path → score 1；title 组因 alreadyGrouped 全员已入组而跳过。
    f_a = _mk_file("fA", "# 总体要求\n# 施工组织设计\n")
    f_b = _mk_file("fB", "# 总体要求\n# 施工组织设计\n")
    result = build_outline_comparison([f_a, f_b])
    assert len(result["duplicateGroups"]) == 2
    g1, g2 = result["duplicateGroups"]
    assert (g1["id"], g1["type"], g1["score"]) == ("G0001", "duplicate", 1)
    assert (g2["id"], g2["type"], g2["score"]) == ("G0002", "duplicate", 1)
    assert g1["title"] == "总体要求"
    assert g1["file_ids"] == ["fA", "fB"]
    assert g1["item_ids"] == {"fA": ["O00001"], "fB": ["O00001"]}
    assert g1["paths"] == {"fA": ["总体要求"], "fB": ["总体要求"]}
    assert f_a["items"][0]["duplicate_group_ids"] == ["G0001"]
    assert f_b["items"][1]["duplicate_group_ids"] == ["G0002"]

    # 同 normalized_title、不同 normalized_path → score 0.95。
    f_c = _mk_file("fC", "# 项目概况\n## 总体要求\n")
    f_d = _mk_file("fD", "# 前置说明\n## 总体要求\n")
    by_title = build_outline_comparison([f_c, f_d])
    assert len(by_title["duplicateGroups"]) == 1
    group = by_title["duplicateGroups"][0]
    assert (group["id"], group["type"], group["score"]) == ("G0001", "duplicate", 0.95)
    assert group["file_ids"] == ["fC", "fD"]
    assert group["item_ids"] == {"fC": ["O00002"], "fD": ["O00002"]}
    assert group["paths"] == {"fC": ["项目概况 > 总体要求"], "fD": ["前置说明 > 总体要求"]}

    # alreadyGrouped 判据（every）：部分入组时 title 组仍建，且包含全部三文件的条目。
    f_e1 = _mk_file("fE1", "# 共同标题\n")
    f_e2 = _mk_file("fE2", "# 共同标题\n")
    f_e3 = _mk_file("fE3", "# 其他\n## 共同标题\n")
    partial = build_outline_comparison([f_e1, f_e2, f_e3])
    assert [(g["id"], g["score"], g["file_ids"]) for g in partial["duplicateGroups"]] == [
        ("G0001", 1, ["fE1", "fE2"]),
        ("G0002", 0.95, ["fE1", "fE2", "fE3"]),
    ]
    assert partial["duplicateGroups"][0]["item_ids"] == {"fE1": ["O00001"], "fE2": ["O00001"]}
    assert partial["duplicateGroups"][1]["item_ids"] == {
        "fE1": ["O00001"], "fE2": ["O00001"], "fE3": ["O00002"],
    }
    # 已入 path 组的条目同时入 title 组（参考语义：dup_ids 追加两个组 id）
    assert f_e1["items"][0]["duplicate_group_ids"] == ["G0001", "G0002"]
    assert f_e3["items"][1]["duplicate_group_ids"] == ["G0002"]


# ---------------------------------------------------------------------------
# 12. buildOutlineComparison —— similar 组（:1406-1420）
# ---------------------------------------------------------------------------


def test_build_outline_comparison_similar_groups_threshold_and_level_gap():
    # bigram 相似度 0.9（10 bigram 中共享 9）≥ 0.86 → 组建。
    f_s1 = _mk_file("fS1", "# 甲乙丙丁戊己庚辛壬癸甲\n")
    f_s2 = _mk_file("fS2", "# 甲乙丙丁戊己庚辛壬癸乙\n")
    hi = build_outline_comparison([f_s1, f_s2])
    assert len(hi["duplicateGroups"]) == 1
    group = hi["duplicateGroups"][0]
    assert (group["id"], group["type"], group["score"]) == ("G0001", "similar", 0.9)
    assert group["file_ids"] == ["fS1", "fS2"]
    assert group["item_ids"] == {"fS1": ["O00001"], "fS2": ["O00001"]}
    assert f_s1["items"][0]["similar_group_ids"] == ["G0001"]
    assert f_s1["items"][0]["duplicate_group_ids"] == []

    # 相似度 0.8（共享 8）< 0.86 → 不组建。
    f_s4 = _mk_file("fS4", "# 甲乙丙丁戊己庚辛壬癸甲\n")
    f_s5 = _mk_file("fS5", "# 甲乙丙丁戊己庚辛壬乙甲\n")
    assert build_outline_comparison([f_s4, f_s5])["duplicateGroups"] == []

    # level 差 2 > 1 → 不组建（右侧文件垫 1→2→3 层级链避免 level 被夹取）。
    f_l1 = _mk_file("fL1", "# 甲乙丙丁戊己庚辛壬癸甲\n")
    f_l3 = _mk_file("fL3", "# 甲一\n## 乙二\n### 甲乙丙丁戊己庚辛壬癸乙\n")
    assert [i["level"] for i in f_l3["items"]] == [1, 2, 3]
    assert build_outline_comparison([f_l1, f_l3])["duplicateGroups"] == []

    # level 差 1 → 组建；右侧目标项是第 3 条（O00003）。
    f_l1b = _mk_file("fL1b", "# 甲乙丙丁戊己庚辛壬癸甲\n")
    f_l2b = _mk_file("fL2b", "# 甲一\n## 乙二\n## 甲乙丙丁戊己庚辛壬癸乙\n")
    ok = build_outline_comparison([f_l1b, f_l2b])
    assert len(ok["duplicateGroups"]) == 1
    ok_group = ok["duplicateGroups"][0]
    assert (ok_group["id"], ok_group["type"], ok_group["score"]) == ("G0001", "similar", 0.9)
    assert ok_group["item_ids"] == {"fL1b": ["O00001"], "fL2b": ["O00003"]}


# ---------------------------------------------------------------------------
# 13. buildOutlineComparison —— pairwise（:1422-1448） + riskFromScore（:1361）
# ---------------------------------------------------------------------------


def test_build_outline_comparison_pairwise_formula_and_risk():
    # 左 titles/paths {aaa,bbb,ccc}，右 {aaa,ddd}：
    # titleOverlap = 1/2、pathOverlap = 1/2、orderSimilarity = 1/3
    # score = round(0.45*0.5 + 0.35*0.5 + 0.2*(1/3), 2) = 0.47（Node 实测）
    f_p1 = _mk_file("fP1", "# aaa\n# bbb\n# ccc\n")
    f_p2 = _mk_file("fP2", "# aaa\n# ddd\n")
    result = build_outline_comparison([f_p1, f_p2])
    assert result["pairwiseSimilarities"] == [
        {
            "file_a_id": "fP1",
            "file_b_id": "fP2",
            "score": 0.47,
            "title_overlap": 0.5,
            "path_overlap": 0.5,
            "order_similarity": 0.33,
            "shared_count": 1,
            "risk": "low",
        }
    ]

    # 完全一致 → 全部 1.0、shared_count = max(2,2) = 2、risk high。
    f_q1 = _mk_file("fQ1", "# aaa\n# bbb\n")
    f_q2 = _mk_file("fQ2", "# aaa\n# bbb\n")
    hi = build_outline_comparison([f_q1, f_q2])
    assert hi["pairwiseSimilarities"][0]["score"] == 1
    assert hi["pairwiseSimilarities"][0]["shared_count"] == 2
    assert hi["pairwiseSimilarities"][0]["risk"] == "high"

    # risk 四档逐字阈值（:1362-1365）
    assert risk_from_score(0.75) == "high"
    assert risk_from_score(0.7499) == "medium"
    assert risk_from_score(0.55) == "medium"
    assert risk_from_score(0.5499) == "low"
    assert risk_from_score(0.35) == "low"
    assert risk_from_score(0.3499) == "none"
    assert risk_from_score(0) == "none"
    assert risk_from_score(1.0) == "high"


# ---------------------------------------------------------------------------
# 14. intersectSize（:1328） / bigramSimilarity（:1334） / lcsSimilarity（:1350）
# ---------------------------------------------------------------------------


def test_bigram_and_lcs_similarity_values():
    # 相等串短路 1（:1338）
    assert bigram_similarity("abc", "abc") == 1
    # 空串 0（:1337）
    assert bigram_similarity("", "abc") == 0
    assert bigram_similarity("abc", "") == 0
    # 单字符集合无共享
    assert bigram_similarity("a", "b") == 0
    # 2*1/(3+3) = 1/3
    assert bigram_similarity("abcd", "abxy") == 1 / 3
    # astral 字符按码点切 bigram（R-P3b-5：参考 Array.from 与 Python list 同语义）
    assert bigram_similarity("😀ab", "😀ac") == 0.5
    assert bigram_similarity("😀a", "😀a") == 1

    # lcs
    assert lcs_similarity("abc", "abc") == 1.0
    assert lcs_similarity("", "abc") == 0
    assert lcs_similarity("abc", "") == 0
    assert lcs_similarity("abcdef", "acf") == 0.5
    assert lcs_similarity("abcd", "xy") == 0
    # pairwise 里传入的是 normalized_title 列表（:1435）
    assert lcs_similarity(["a", "b", "c"], ["a", "c"]) == 2 / 3

    # intersect_size 直接断言
    assert intersect_size(["a", "b", "c"], {"a", "c", "d"}) == 2
    assert intersect_size([], {"a"}) == 0

    # to_fixed_2：half-up 语义（R-P3b-7）。0.125 二进制精确，half-up → 0.13
    # （Python 内建 round() 是 half-even 会给 0.12，此处必须 0.13）。
    assert to_fixed_2(0) == 0
    assert to_fixed_2(1.0) == 1.0
    assert to_fixed_2(1 / 3) == 0.33
    assert to_fixed_2(0.125) == 0.13


# ---------------------------------------------------------------------------
# 附：parseCatalogLine 直调（目录行解析的编号路径 / 拒绝路径）
# ---------------------------------------------------------------------------


def test_parse_catalog_line_marker_and_rejects():
    # 有编号 → 走 marker 路径
    assert parse_catalog_line("1 项目概况……1") == {"number": "1", "title": "项目概况", "level": 1}
    # 管道剥除 + 分隔行/目录行拒（:1229-1230）
    assert parse_catalog_line("| 2 技术要求……5 |") == {"number": "2", "title": "技术要求", "level": 1}
    assert parse_catalog_line("| --- | --- |") is None
    assert parse_catalog_line("目录") is None
    # 无编号无页尾拒
    assert parse_catalog_line("普通说明内容一行") is None
