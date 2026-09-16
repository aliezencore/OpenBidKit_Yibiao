import pytest

from app.infra.parsing.docx import docx_to_markdown
from app.infra.parsing.markdown import html_to_markdown, normalize_markdown
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


def test_include_images_true_is_rejected(tmp_path):
    """裁定 R2：P2a 不产出图片。mammoth 默认会把整图编成 base64 data URL
    塞进 markdown，污染 LLM 提示词与正文比对；真正实现属 P2b。
    本用例把「拒绝」这一行为钉住，避免它被无声移除。"""
    from app.domain.shared.errors import ParsingError

    p = build_docx(tmp_path / "img.docx", blocks=[("p", "正文")])

    with pytest.raises(ParsingError) as info:
        docx_to_markdown(p, include_images=True)

    assert info.value.code == "not-implemented"
