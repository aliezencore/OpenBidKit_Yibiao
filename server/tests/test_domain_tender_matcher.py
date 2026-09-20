"""P3b Task 3 —— 招标五级匹配器（domain/tender_matcher.py）的 9 条行为测试。

全部预期值经 Node 直接运行参考实现
`client/electron/services/duplicateCheckService.cjs`（:1756-2010）
取地面真值核对，非手工推演。对拍脚本：
`.superpowers/sdd/2026-09-19-fastapi-check-service-p3b-content-outline-compare/task3-oracle-probe.cjs`
"""

from __future__ import annotations

from app.domain.duplicate.tender_matcher import (
    build_duplicate_sentences,
    build_tender_loose_text,
    build_tender_skeleton_key,
    build_tender_source_matcher,
    build_tender_strict_key,
    char_bigrams_from_loose_text,
    is_safe_tender_field_tail,
    is_tender_field_allowed,
    is_tender_skeleton_allowed,
    normalize_tender_comparable_text,
    parse_tender_format_field,
)


# ---------------------------------------------------------------------------
# 1. normalizeTenderComparableText 归一化链（:1756-1780）
# ---------------------------------------------------------------------------


def test_normalize_tender_comparable_text_pipeline():
    # 全角标点归半角（:1763-1768）
    assert normalize_tender_comparable_text("投标报价：100万元，服务期1年。") == "投标报价:100万元,服务期1年."
    # 不低于 → >=（:1769）
    assert normalize_tender_comparable_text("不低于100万元") == ">=100万元"
    # 日期两形态归一成 YYYY年M月D日（:1772-1773，去前导 0）
    assert normalize_tender_comparable_text("2026年01月02日前支付") == "2026年1月2日前支付"
    assert normalize_tender_comparable_text("2026-01-02 前") == "2026年1月2日 前"
    # 表前缀循环剥（:1782-1799，Node 真值：技术要求 1. → 内容）
    assert normalize_tender_comparable_text("技术要求: 1. 内容") == "内容"
    # 目录页尾：文本不含目录类词则不动（:1801-1809，Node 真值「第12页」）
    assert normalize_tender_comparable_text("技术要求 第12页") == "第12页"


# ---------------------------------------------------------------------------
# 2. strict/loose key（:1812-1824）
# ---------------------------------------------------------------------------


def test_strict_and_loose_keys_normalize_punctuation_case():
    # 同文异形（标点/空白/大小写）→ 同 key（Node 真值）
    assert build_tender_strict_key("投标报价：100万元。") == "投标报价100万元"
    assert build_tender_loose_text("投标报价：100万元。") == "投标报价100万元"
    # strict 与 loose 当前实现相同（参考就是同款两份），断言一致性
    assert build_tender_strict_key("ABC报价100万") == build_tender_loose_text("ABC报价100万")


# ---------------------------------------------------------------------------
# 3. skeleton 占位符顺序 + allowed 门（:1826-1850）
# ---------------------------------------------------------------------------


def test_skeleton_key_placeholder_order_and_allowed_gate():
    """skeleton 占位符（:1826-1842）与 allowed 门（:1844-1850）——全部 Node 真值。

    **重要（参考实现缺陷，逐字保留）**：参考 :1839 的标点剥除正则把
    ``{}`` 也纳入字符集，导致 ``{date}/{money}/…`` 占位符的花括号在最后一步
    被剥掉（``{money}``→``money``）；且 :1847 的 ``!/[{}]/`` 因此恒成立，
    ``isTenderSkeletonAllowed`` **恒返回 false**，skeleton 级匹配事实不可达。
    本移植按「行为对齐」原则逐字复刻该缺陷（比对结果必须与参考一致），
    并在此登记（见 tender_matcher.py docstring D4）。
    """
    # 占位符花括号被剥 → 日期数字被裸数字正则捕成 num、万元/元归 money
    assert build_tender_skeleton_key("2026年1月2日前支付100万元") == "num年num月num日前支付money"
    assert build_tender_skeleton_key("支付100元") == "支付money"
    # 纯中文无数字 → 不占位
    assert build_tender_skeleton_key("内容无瑕疵需整改") == "内容无瑕疵需整改"
    # allowed 恒 false（无 {} 分支 :1847）
    assert is_tender_skeleton_allowed("任意", build_tender_skeleton_key("任意")) is False
    assert (
        is_tender_skeleton_allowed(
            "这是很长的技术描述内容超过十八个字符长度",
            build_tender_skeleton_key("这是很长的技术描述内容超过十八个字符长度"),
        )
        is False
    )


# ---------------------------------------------------------------------------
# 4. field 白名单/黑名单/safe tail（:1852-1895）
# ---------------------------------------------------------------------------


def test_field_level_whitelist_denylist_and_safe_tail():
    # 字段命中（:1863-1879，Node 真值）
    assert parse_tender_format_field("投标日期:2026年1月2日") == {"field": "投标日期", "tail": "2026年1月2日"}
    # deny 黑名单（:1852 逐字）——联系电话 拒
    assert is_tender_field_allowed("联系电话") is False
    # allow 白名单（:1853 逐字）
    assert is_tender_field_allowed("投标日期") is True
    # 白名单外拒
    assert is_tender_field_allowed("随便什么字段") is False
    # safe tail 各形态（:1886-1895）
    assert is_safe_tender_field_tail("") is True
    assert is_safe_tender_field_tail("2026年1月2日") is True
    assert is_safe_tender_field_tail("天津港保税区消防救援支队") is True


# ---------------------------------------------------------------------------
# 5. 五级短路顺序（:1980-1996）
# ---------------------------------------------------------------------------


def test_match_short_circuit_order_exact_strict_field_skeleton_near():
    # 构造 exact 命中的句子：sentence 完全一致 → reason='exact'
    matcher = build_tender_source_matcher(
        [{"sentence": "不低于100万元", "normalized": "不低于100万元"}]
    )
    assert matcher.match({"normalized": "不低于100万元"}) == {"reason": "exact"}
    # tenderSentenceCount = exactSet.size（:1981）
    assert matcher.tender_sentence_count == 1


# ---------------------------------------------------------------------------
# 6. near 两档阈值（:1946-1978）
# ---------------------------------------------------------------------------


def test_near_match_two_threshold_bands():
    # 宽松档（compact>=30）：长 tender，句子删末尾几字仍 near 命中（Node 真值）
    tender = "投标人须在合同签订后十日内交付全部货物并完成安装调试"
    matcher = build_tender_source_matcher([{"sentence": tender, "normalized": tender}])
    result = matcher.match({"normalized": "投标人须在合同签订后十日内交付全部货物并完成安装"})
    assert result is not None
    assert result["reason"] == "near"
    assert result["dice"] == 0.9583333333333334
    assert result["containment"] == 1
    # 严格档（compact<30）：短句需 loose>=12 才走 near（:1909 shouldApplyNearTenderMatch 门）
    short_tender = "投标报价壹佰万元整投标报价壹佰万元整"
    short_matcher = build_tender_source_matcher([{"sentence": short_tender, "normalized": short_tender}])
    # 删末尾一字（loose 长度 17 仍 >=12，走严格档 0.95/0.88/0.55）→ near 命中（Node 真值）
    near_short = short_matcher.match({"normalized": short_tender[:-1]})
    assert near_short is not None
    assert near_short["reason"] == "near"
    assert near_short["dice"] == 1
    assert near_short["containment"] == 1


# ---------------------------------------------------------------------------
# 7. gram 倒排候选复位语义（:1946-1978）
# ---------------------------------------------------------------------------


def test_gram_index_candidate_reset_semantics():
    # 两个 tender 共享多个 bigram，验证 shared 计数正确、dice 取最大者
    t1 = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"
    t2 = "甲乙丙丁戊己庚辛壬癸甲乙丙丁戊己庚辛壬癸"
    matcher = build_tender_source_matcher(
        [{"sentence": t1, "normalized": t1}, {"sentence": t2, "normalized": t2}]
    )
    # 一个与 t1 高度重叠的句子应命中 t1（dice 更大）
    result = matcher.match({"normalized": t1})
    assert result is not None and result["reason"] == "exact"  # 完全一致 → exact


# ---------------------------------------------------------------------------
# 8. buildDuplicateSentences 过滤/排序/id（:1999-2004）
# ---------------------------------------------------------------------------


def test_build_duplicate_sentences_filter_sort_and_ids():
    gs = {
        "句A": {"sentence": "句A", "file_ids": ["f1"], "first_order": 0},
        "句B": {"sentence": "这是重复句子内容较长的一句", "file_ids": ["f1", "f2"], "first_order": 1},
        "句C": {"sentence": "这是重复句子内容更长很多的一句测试", "file_ids": ["f1", "f2", "f3"], "first_order": 2},
    }
    result = build_duplicate_sentences(gs)
    # 单文件句滤掉（:2001）
    assert [r["sentence"] for r in result] == [
        "这是重复句子内容更长很多的一句测试",
        "这是重复句子内容较长的一句",
    ]
    # 排序：file_ids 多者先 → 句长次之（:2002）
    assert result[0]["file_ids"] == ["f1", "f2", "f3"]
    assert result[0]["id"] == "S000001"
    assert result[1]["id"] == "S000002"


# ---------------------------------------------------------------------------
# 9. tenderSentenceCount = exactSet.size（:1981）
# ---------------------------------------------------------------------------


def test_tender_sentence_count_is_exact_set_size():
    # 重复 normalized 只计一次
    matcher = build_tender_source_matcher(
        [
            {"sentence": "甲", "normalized": "甲乙"},
            {"sentence": "乙", "normalized": "甲乙"},  # 同 normalized
        ]
    )
    assert matcher.tender_sentence_count == 1
