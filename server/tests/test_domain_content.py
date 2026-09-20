"""P3b Task 2 —— 正文纯函数（domain/content.py）的 10 条行为测试。

全部预期值经 Node 直接运行参考实现
`client/electron/services/duplicateCheckService.cjs`（:108-112 + :1453-1754）
取地面真值核对，非手工推演。对拍脚本：
`.superpowers/sdd/2026-09-19-fastapi-check-service-p3b-content-outline-compare/oracle-probe-task2.cjs`
"""

from __future__ import annotations

from app.domain.duplicate.content import (
    clean_markdown_inline_text,
    decode_basic_html_entities,
    extract_content_text_blocks,
    extract_markdown_text_blocks,
    is_informative_content_sentence,
    normalize_content_sentence,
    split_content_block_sentences,
    split_content_sentences,
    split_markdown_table_row,
    strip_images_from_markdown,
    strip_leading_content_sequence,
)


# ---------------------------------------------------------------------------
# 1. decodeBasicHtmlEntities 顺序 + 数值实体（:1478-1487）
# ---------------------------------------------------------------------------


def test_decode_basic_html_entities_order_and_numeric():
    # amp 在最后（:1485）→ &amp;lt; 解码一次得 &lt;，不二次解码（Node 真值）。
    assert decode_basic_html_entities("&amp;lt;") == "&lt;"
    # &#x…; 十六进制（中 = U+4E2D）
    assert decode_basic_html_entities("&#x4e2d;") == "中"
    # &#…; 十进制（中 = 20013）
    assert decode_basic_html_entities("&#20013;") == "中"
    # 非法数值实体保留原文
    assert decode_basic_html_entities("&#xyz;") == "&#xyz;"
    # amp 单独
    assert decode_basic_html_entities("&amp;") == "&"
    # nbsp → 空格
    assert decode_basic_html_entities("A&nbsp;B") == "A B"
    # 链式 &lt;a&gt; → <a>
    assert decode_basic_html_entities("&lt;a&gt;") == "<a>"


def test_clean_markdown_inline_text_decodes_last_and_cjk_paren_pattern():
    """评审 A F1/F2 修复的回归钉子（全部 Node oracle 真值，2026-09-20 实测）。

    F1：参考把 decodeBasicHtmlEntities 包在 replace 链最外层（:1569-1583），
    先剥完 markdown/HTML 语法**再**解码——编码实体包装的语法（如
    ``&lt;img…&gt;``）解码后的裸语法不会被二次剥除，按字面残留。
    F2：stripLeading 模式 3（:1678）的 CJK 数字集含「百千万」。
    """
    # F1：解码发生在剥标签之后 → 裸标签字面量残留（Node 真值 '<img src="x">'）
    assert clean_markdown_inline_text("&lt;img src=&quot;x&quot;&gt;") == '<img src="x">'
    # 编码反引号不再触发行内代码剥（解码后已无结构可剥）
    assert clean_markdown_inline_text("&#x60;code&#x60;") == "`code`"
    assert clean_markdown_inline_text("&lt;b&gt;bold&lt;/b&gt;") == "<b>bold</b>"
    assert clean_markdown_inline_text("&#x5b;link&#x5d;(http://x)") == "[link](http://x)"
    # 强调剥除仍发生在解码前 → **&amp;lt;x&amp;gt;** 剥 ** 得 &amp;lt;x&amp;gt; 再解码
    assert clean_markdown_inline_text("**&amp;lt;x&amp;gt;**") == "&lt;x&gt;"
    # 普通实体正常解码
    assert clean_markdown_inline_text("a&amp;b") == "a&b"
    # F2：模式 3 半角括号 CJK 集含「百千万」（Node 真值：剥净）
    assert strip_leading_content_sequence("（百）工程概况") == "工程概况"
    assert strip_leading_content_sequence("(百千) 工程概况") == "工程概况"


# ---------------------------------------------------------------------------
# 2. splitMarkdownTableRow 转义/首尾管道/空单元格（:1534-1557）
# ---------------------------------------------------------------------------


def test_split_markdown_table_row_escapes_and_edges():
    # 转义 \| 保留为普通竖线（:1543-1556 状态机）
    assert split_markdown_table_row("a \\| b | c") == ["a | b", "c"]
    # 首尾管道剥除
    assert split_markdown_table_row("| a | b |") == ["a", "b"]
    # 中间空单元格保留为空串
    assert split_markdown_table_row("| a | | c |") == ["a", "", "c"]
    # 无首尾管道也按竖线分列
    assert split_markdown_table_row("a | b") == ["a", "b"]


# ---------------------------------------------------------------------------
# 3. GFM 表格三元组 → 每格独立块、分隔行不入块（:1592-1619）
# ---------------------------------------------------------------------------


def test_markdown_table_triple_becomes_per_cell_blocks():
    md = "| 姓名 | 年龄 |\n| --- | --- |\n| 张三 | 25 |\n| 李四 | 30 |"
    # Node 真值：每格独立成块，顺序 姓名/年龄/张三/25/李四/30；分隔行不出现。
    assert extract_markdown_text_blocks(md) == ["姓名", "年龄", "张三", "25", "李四", "30"]


# ---------------------------------------------------------------------------
# 4. 标题/列表/编号/字段/句尾 各自独立；段落聚段；代码块与图片剥除（:1584-1642）
# ---------------------------------------------------------------------------


def test_markdown_blocks_standalone_field_sentence_and_paragraph():
    md = "\n".join([
        "# 标题",
        "- 列表项",
        "1. 编号项",
        "价格：100元",
        "这是一句完整的话。",
        "第一段第一行",
        "第一段第二行",
        "```code block```",
        "![alt](img.png)",
        "普通尾巴。",
    ])
    # Node 真值顺序：标题/列表项/1. 编号项/价格：100元/这是一句完整的话。/
    # 第一段第一行 第一段第二行（段落空格 join）/普通尾巴。
    # 代码块被替换成换行（无块）；图片行被 cleanMarkdownInlineText 剥成空（无块）。
    assert extract_markdown_text_blocks(md) == [
        "标题",
        "列表项",
        "1. 编号项",
        "价格：100元",
        "这是一句完整的话。",
        "第一段第一行 第一段第二行",
        "普通尾巴。",
    ]


# ---------------------------------------------------------------------------
# 5. splitContentBlockSentences 强边界即时切 / 弱边界累计阈值（:1708-1727）
# ---------------------------------------------------------------------------


def test_split_content_block_sentences_strong_and_clause_boundary():
    # 强边界 [。！？!?] 即时切
    assert split_content_block_sentences("甲。乙！丙？") == ["甲。", "乙！", "丙？"]
    # 弱边界 ； 累计非空白 < 20 不切，整段保留
    assert split_content_block_sentences("一二三四五；六七") == ["一二三四五；六七"]
    # 弱边界 ； 累计非空白 ≥ 20 才切（25 字 + ； 成第一段，尾段保留）
    assert split_content_block_sentences("字" * 25 + "；尾段") == ["字" * 25 + "；", "尾段"]
    # 无边界 → 整段作为尾段
    assert split_content_block_sentences("只有一句没有标点") == ["只有一句没有标点"]


# ---------------------------------------------------------------------------
# 6. isInformativeContentSentence 信息量矩阵（:1730-1740）
# ---------------------------------------------------------------------------


def test_is_informative_content_sentence_matrix():
    # 纯数字拒
    assert is_informative_content_sentence("12345") is False
    # 内容字符 < 4 拒（纯字母）
    assert is_informative_content_sentence("ab") is False
    # 内容字符 < 4 拒（纯汉字）
    assert is_informative_content_sentence("甲乙丙") is False
    # 紧凑长度 ≥ 12 收
    assert is_informative_content_sentence("这是一个具有足够长度的招标要求说明文本") is True
    # 短句带冒号且含 ≥2 连续字母/汉字 收
    assert is_informative_content_sentence("价格：100元") is True
    # 短句带单位词（天）收
    assert is_informative_content_sentence("本合同工期365天") is True
    # 无特征短句拒
    assert is_informative_content_sentence("甲乙") is False


# ---------------------------------------------------------------------------
# 7. stripLeadingContentSequence 七模式循环剥（:1673-1697）
# ---------------------------------------------------------------------------


def test_strip_leading_content_sequence_loops_all_patterns():
    # 1.2 （一）⑨ 连环剥净（循环 restart 语义）→ 内容
    assert strip_leading_content_sequence("1.2 （一）⑨ 内容") == "内容"
    # 第一章 第一节 连环剥净 → 空
    assert strip_leading_content_sequence("第一章 第一节") == ""
    # 单一数字编号前缀剥除
    assert strip_leading_content_sequence("1. 简单条款") == "简单条款"
    # （二）括号编号前缀剥除
    assert strip_leading_content_sequence("（二）项目概况") == "项目概况"


# ---------------------------------------------------------------------------
# 8. normalizeContentSentence 控制字符/零宽/BOM/全角空格（:1665-1671）
# ---------------------------------------------------------------------------


def test_normalize_content_sentence_control_chars_and_spaces():
    # 全角空格压缩 + trim
    assert normalize_content_sentence("　内容　") == "内容"
    # BOM 与零宽空格剥除
    assert normalize_content_sentence("﻿内容​尾") == "内容尾"
    # 控制字符类剥除
    assert normalize_content_sentence("a\u0001b\u200bc") == "abc"
    # 先导编号序列剥除（1. ）
    assert normalize_content_sentence("1. 条款内容") == "条款内容"


# ---------------------------------------------------------------------------
# 9. splitContentSentences 600 截断 + 非 informative 过滤（:1742-1753）
# ---------------------------------------------------------------------------


def test_split_content_sentences_600_truncation_and_filter():
    md = "字" * 700 + "。\n甲乙\n价格：100元。"
    sentences = split_content_sentences(md)
    # 700 字句超 600 → sentence 截断为 600 字 + "..."，normalized 保留全量（701=700+。）
    # 甲乙 内容字符 <4 被滤；价格：100元。 收。
    assert len(sentences) == 2
    assert sentences[0]["sentence"] == "字" * 600 + "..."
    assert sentences[0]["normalized"] == "字" * 700 + "。"
    assert sentences[1] == {"sentence": "价格：100元。", "normalized": "价格：100元。"}


# ---------------------------------------------------------------------------
# 10. stripImagesFromMarkdown 三种图片形态（:1453-1458）
# ---------------------------------------------------------------------------


def test_strip_images_three_pattern_forms():
    # markdown 图片 ![]()
    assert strip_images_from_markdown("前![alt](img.png)后") == "前 后"
    # HTML <img src=…>
    assert strip_images_from_markdown("前<img src=\"x.png\">后") == "前 后"
    # HTML <img …>（无 src）
    assert strip_images_from_markdown("前<img title=\"y\">后") == "前 后"
    # 大小写不敏感（gi）：<IMG SRC=…> 同样剥除
    assert strip_images_from_markdown("前<IMG SRC=\"x.PNG\">后") == "前 后"
