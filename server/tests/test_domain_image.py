r"""P3c Task 1 —— 图片出现位置纯函数（`domain/duplicate/image.py`）的行为测试。

**全部预期值经 Node 直接运行参考实现**取地面真值（不是手工推演、也不是照抄实现）：

```
"C:/Users/eagle/.workbuddy/binaries/node/versions/22.22.2-3/node.exe" \
  .superpowers/sdd/2026-09-21-p3c-image-compare/oracle-task1.cjs
```

参考源：`client/electron/services/duplicateCheckService.cjs`
`extractImageOccurrences`(:2048-2072) / `extractLineImageTargets`(:2006-2017) /
`parseImageContextHeading`(:2019-2037) / `updateImageContextHeadings`(:2034-2037) /
`getPreviousImageSentence`(:2039-2046)。

**target 形态的偏离（计划裁定 R-P3c-7）**：参考接受**任意** target（它的 target 是
`yibiao-asset://` URL），本实现只认自己产出的 `assets/<64 位小写十六进制>`；参考的
`!\[(?<alt>[^\]]*)\]\(…` 在 alt 含 `]` 时命中 0（G4），故不照搬。
"""

from __future__ import annotations

from app.domain.duplicate.image import (
    extract_image_occurrences,
    line_image_targets,
    parse_image_context_heading,
    previous_image_sentence,
)

H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64


def _md(*lines: str) -> str:
    return "\n".join(lines)


# --------------------------------------------------------------- 行内 target 抽取


def test_line_targets_finds_markdown_and_html_forms_in_position_order():
    line = f'文字 <img src="assets/{H2}" alt="图" /> 中间 ![](assets/{H1}) 结尾'
    # index = 匹配起点在本行内的字符偏移（参考用 `match.index`）
    assert line_image_targets(line) == [
        {"target": f"assets/{H2}", "index": line.index("<img")},
        {"target": f"assets/{H1}", "index": line.index("![](")},
    ]


def test_line_targets_ignores_foreign_targets():
    """只认自己产出的形态（R-P3c-7）：外来 scheme 与非法哈希都不算。"""
    line = ("![](yibiao-asset://imported-images/x/y.png) "
            "![](https://a/b.png) ![](assets/not-a-hash) ![](assets/short)")
    assert line_image_targets(line) == []


def test_line_targets_accepts_angle_wrapped_target():
    """参考会把 `<…>` 包裹剥掉（`replace(/^<|>$/g,'')`），本实现同样接受。"""
    assert line_image_targets(f"![](<assets/{H1}>)") == [
        {"target": f"assets/{H1}", "index": 0}]


# ------------------------------------------------------------------- 标题栈


def test_heading_stack_builds_directory_with_gt_separator():
    markdown = _md(
        "# 第五章 投标文件格式",
        "本章说明投标文件的组成。",
        f"![](assets/{H1})",
        "## 5.1 营业执照",
        "投标人应当提供有效的营业执照副本复印件。",
        f"![](assets/{H1})",
    )
    assert extract_image_occurrences(markdown) == [
        {"target": f"assets/{H1}", "index": 1,
         "directory": "第五章 投标文件格式",
         "previous_sentence": "本章说明投标文件的组成"},
        {"target": f"assets/{H1}", "index": 2,
         "directory": "第五章 投标文件格式 > 5.1 营业执照",
         "previous_sentence": "投标人应当提供有效的营业执照副本复印件"},
    ]


def test_heading_stack_pops_same_or_deeper_level():
    r"""`updateImageContextHeadings` 先弹出 `level >= 新 level` 的项再入栈（参考 :2034-2037）。

    **判别力来自「层级回落」**：初版这条用例用的是 `# → ## → ###`（层号单调递增），
    弹栈分支**从未触发**——把 `while` 改成恒假，用例仍绿（探针实测）。下面的样本
    特意让层级回落，真值取自 oracle。
    """
    same_level = _md("# 甲", "## 甲一", "### 甲一1", "## 甲二", "正文。",
                     f"![](assets/{H1})")
    assert extract_image_occurrences(same_level)[0]["directory"] == "甲 > 甲二"

    back_to_top = _md("# 甲", "## 甲一", "### 甲一1", "# 乙", "正文。",
                      f"![](assets/{H1})")
    assert extract_image_occurrences(back_to_top)[0]["directory"] == "乙"


def test_bold_short_line_acts_as_level_two_heading():
    markdown = _md("# 一级", "**二级加粗标题**", "前置文本。", f"![](assets/{H1})")
    assert extract_image_occurrences(markdown)[0]["directory"] == "一级 > 二级加粗标题"


def test_no_heading_yields_empty_directory_and_sentence():
    assert extract_image_occurrences(f"![](assets/{H2})") == [
        {"target": f"assets/{H2}", "index": 1, "directory": "",
         "previous_sentence": ""}]


def test_table_cell_image_is_found_and_uses_cell_text_as_previous_sentence():
    """表内图片（G1 修复后我们产出的形态）必须被抽到；前句就是单元格里的文本。"""
    markdown = _md(
        "# 资格证明",
        "| 项目 | 示意图 |",
        "| --- | --- |",
        f"| 营业执照 | ![](assets/{H2}) |",
    )
    assert extract_image_occurrences(markdown) == [
        {"target": f"assets/{H2}", "index": 1, "directory": "资格证明",
         "previous_sentence": "营业执照"}]


def test_html_img_form_is_found_and_heading_text_counts_as_previous_text():
    markdown = _md("## 附件", f'<img src="assets/{H3}" alt="图" />')
    assert extract_image_occurrences(markdown) == [
        {"target": f"assets/{H3}", "index": 1, "directory": "附件",
         "previous_sentence": "附件"}]


def test_fenced_code_block_content_is_stripped():
    """参考先 `replace(/```[\\s\\S]*?```/g, '\\n')`：围栏里的图片不算出现位置。"""
    markdown = _md("# 代码", "```", f"![](assets/{H1})", "```", "正文。",
                   f"![](assets/{H3})")
    assert extract_image_occurrences(markdown) == [
        {"target": f"assets/{H3}", "index": 1, "directory": "代码",
         "previous_sentence": "正文"}]


# ------------------------------------------------------------------- 前句口径


def test_previous_sentence_takes_the_last_sentence_only():
    assert previous_image_sentence("第一句。第二句！第三句？") == "第三句"


def test_previous_sentence_strips_trailing_punctuation_and_pipes():
    assert previous_image_sentence("甲|乙。  ") == "甲 乙"


def test_previous_sentence_truncates_to_500_chars():
    sentence = previous_image_sentence("甲" * 600 + "。")
    assert len(sentence) == 500
    assert set(sentence) == {"甲"}


def test_previous_sentence_collapses_tabs_and_spaces():
    assert previous_image_sentence("甲\t\t 乙。") == "甲 乙"


# --------------------------------------------------------------- 标题解析直调


def test_parse_heading_returns_level_and_cleaned_title():
    assert parse_image_context_heading("### 5.1 营业执照") == {
        "level": 3, "title": "5.1 营业执照"}


def test_parse_heading_rejects_sentence_like_line():
    r"""≤90 字、不以句末标点结尾、不以 `\|` 开头——否则不算标题（参考 :2030）。"""
    assert parse_image_context_heading("这是一句普通的正文说明文字。") is None


def test_parse_heading_accepts_numbered_and_bold_lines_at_level_two():
    assert parse_image_context_heading("**加粗标题**") == {
        "level": 2, "title": "加粗标题"}
