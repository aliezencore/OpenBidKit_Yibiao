import re

import pytest

from app.domain.shared.errors import ParsingError
from app.infra.parsing.docx import docx_to_markdown
from app.infra.parsing.markdown import (
    extract_tables,
    html_to_markdown,
    normalize_markdown,
    restore_tables,
)
from tests.parsing_fixtures import build_docx


def test_headings_and_paragraphs(tmp_path):
    p = build_docx(tmp_path / "a.docx", blocks=[
        ("h1", "第一章 总体方案"), ("p", "本项目采用分层架构。"),
        ("h2", "1.1 建设目标"), ("p", "实现自动化检查。"),
    ])

    text = docx_to_markdown(p)

    assert "# 第一章 总体方案" in text
    assert "本项目采用分层架构。" in text
    assert "## 1.1 建设目标" in text


def test_table_is_preserved_as_gfm(tmp_path):
    p = build_docx(tmp_path / "t.docx", table=[["项目", "金额"], ["土建", "100"]])

    text = docx_to_markdown(p)

    assert "| 项目 | 金额 |" in text
    assert "| --- | --- |" in text
    assert "| 土建 | 100 |" in text


def test_no_base64_or_xml_fragments_leak(tmp_path):
    p = build_docx(tmp_path / "x.docx", blocks=[("p", "正文")])

    text = docx_to_markdown(p)

    for bad in ("base64", "w:document", "<w:p", "xmlns"):
        assert bad not in text


def test_normalize_collapses_blank_runs(tmp_path):
    assert normalize_markdown("a\n\n\n\nb") == "a\n\nb"
    assert normalize_markdown("  标题  ") == "标题"


def test_html_to_markdown_keeps_bold(tmp_path):
    assert "**加粗**" in html_to_markdown("<p><strong>加粗</strong></p>")


def test_empty_document_yields_empty_string(tmp_path):
    p = build_docx(tmp_path / "empty.docx", blocks=[])
    assert docx_to_markdown(p).strip() == ""


def test_include_images_true_keeps_asset_placeholders(tmp_path):
    """P2a↔P2b 接线：`include_images=True` 产出 `![](assets/<sha256>)` 占位。

    本用例原先钉的是**裁定 R2 的拒绝**（`not-implemented`）。R2 的拒绝前提
    「P2b 的图片占位 + 资产提取未落地」已消失，且新实现不走 base64（mammoth 的
    默认转换才会塞 data URL，这里回调只写 `assets/<hash>`），故拒绝被撤销——
    本用例改为钉**新行为**，同时用同源的 `include_images=False` 做对照，
    保证「开关真的在起作用」而不是两条路径恰好同形。
    """
    from tests.parsing_fixtures import build_docx_with_images, make_image

    image = make_image(tmp_path / "a.png", size=(40, 30), color=(200, 30, 30))
    p = build_docx_with_images(tmp_path / "img.docx", blocks=[("p", "正文。")], images=[image])

    with_images = docx_to_markdown(p, include_images=True)
    without_images = docx_to_markdown(p)

    assert re.search(r"!\[[^\]]*\]\(assets/[0-9a-f]{64}\)", with_images), with_images
    assert "assets/" not in without_images
    assert "data:image" not in with_images, "不得退回 base64 内联（R2 的原始顾虑）"


def test_extract_tables_returns_gfm_and_escaped_marker():
    """裁定 R19：extract_tables 摘表后，HTML 侧的占位符是「转义形态」。

    这里出现 `&lt;!--TABLE:0--&gt;` 而不是 `<!--TABLE:0-->`，是因为
    `table.replace_with("<--TABLE:0-->")` 写入的是一个**文本节点**，bs4 序列化
    文本节点时会转义其中的 `<` `>`。这是预期契约，不是缺陷：真正的还原发生在
    markdown 侧（见 test_placeholder_survives_html_to_markdown_as_raw_marker）。
    """
    html = (
        "<p>前</p>"
        "<table><tr><th>项目</th><th>金额</th></tr>"
        "<tr><td>土建</td><td>100</td></tr></table>"
        "<p>后</p>"
    )

    html_without_tables, tables = extract_tables(html)

    assert "<table>" not in html_without_tables
    assert tables == ["| 项目 | 金额 |\n| --- | --- |\n| 土建 | 100 |"]
    assert "前" in html_without_tables
    assert "后" in html_without_tables
    assert "&lt;!--TABLE:0--&gt;" in html_without_tables


def test_placeholder_survives_html_to_markdown_as_raw_marker():
    """裁定 R19：占位符跨 HTML→markdown 边界，两侧表示形态不同——这是唯一可行机制。

    实测依据（markdownify `__init__.py:247-250`：
    "Comment and Doctype elements are always ignored"）：
      - HTML 侧：占位符是文本节点，序列化成 `&lt;!--TABLE:0--&gt;`。
      - markdownify 重新解析该实体后拿到的是**文本节点**（不是 Comment），
        文本节点原样输出，于是 markdown 侧重新出现裸的 `<!--TABLE:0-->`。
      - 若改用真正的注释节点，markdownify 会**丢弃**它，表格随之永久丢失。

    所以 restore_tables 能匹配的只有 markdown 侧的裸标记。本用例把这条隐式依赖
    钉成显式契约，避免后人「顺手清理」掉转义而无声破坏表格。
    """
    html = (
        "<p>前</p>"
        "<table><tr><th>项目</th><th>金额</th></tr>"
        "<tr><td>土建</td><td>100</td></tr></table>"
        "<p>后</p>"
    )

    html_without_tables, _ = extract_tables(html)
    markdown = html_to_markdown(html_without_tables)

    assert "<!--TABLE:0-->" in markdown


def test_restore_tables_replaces_raw_marker_in_markdown():
    """裁定 R19：还原必须作用在 markdown（裸标记）上。

    对 extract_tables 的 HTML 输出做还原是无意义的：那里的标记已被转义成
    `&lt;!--TABLE:0--&gt;`，匹配不到，表格永远回不来。
    """
    html = "<p>前</p><table><tr><th>A</th></tr><tr><td>1</td></tr></table><p>后</p>"

    html_without_tables, tables = extract_tables(html)
    markdown = html_to_markdown(html_without_tables)
    restored = restore_tables(markdown, tables)

    assert "<!--TABLE:0-->" not in restored
    assert "| A |" in restored
    assert "| --- |" in restored
    assert "| 1 |" in restored


def test_extract_tables_ignores_table_without_rows():
    """裁定 R19：无行的空表既不产出 GFM，也不留下占位符。"""
    html_without_tables, tables = extract_tables("<table></table><p>x</p>")

    assert tables == []
    assert "x" in html_without_tables


def test_corrupt_docx_raises_conversion_error(tmp_path, monkeypatch):
    """裁定 P2a 终审 F3：mammoth 对损坏 docx 不抛异常、只在 messages 报 error。

    无法确定性地造出 mammoth 必然报 error 的 docx，故按裁定允许的口径：monkeypatch
    `mammoth.convert_to_html`（即实现实际调用的名字）返回一个带 error message 的结果，
    钉住「有 error 就抛 ParsingError(code="docx-conversion-failed")」的守卫。
    """
    import mammoth
    from types import SimpleNamespace

    class _FakeResult:
        value = ""
        messages = [SimpleNamespace(type="error", message="corrupt document")]

    monkeypatch.setattr(mammoth, "convert_to_html", lambda *a, **k: _FakeResult())

    p = build_docx(tmp_path / "corrupt.docx", blocks=[("p", "正文")])

    with pytest.raises(ParsingError) as info:
        docx_to_markdown(p)

    assert info.value.code == "docx-conversion-failed"
