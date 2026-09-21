"""P2a↔P2b 接线 Task 2 —— docx 带图解析产出 `assets/<hash>` 占位。

全部断言对照**计划**的裁定（`docs/superpowers/plans/2026-09-21-fastapi-check-service-docx-image-wiring.md`）：
- R-DW-1：target 形态 `assets/<64 位小写十六进制>`，不带扩展名；
- R-DW-3：`include_images=False` 的输出**逐字节不变**（P2a 的解析 golden 红线）；
- R-DW-5：非 docx 的 `include_images=True` 是 no-op；
- §8.5「图片占位数量与位置一致」：数量 = 出现次数，位置 = 图片真正所在处。
"""

from __future__ import annotations

import re

from docx import Document

from app.infra.parsing.docx import docx_to_markdown
from app.infra.parsing.docx_assets import list_media_files
from app.infra.parsing.router import parse_document
from tests.parsing_fixtures import build_docx, build_docx_with_images, make_image

PLACEHOLDER_RE = re.compile(r"!\[[^\]]*\]\(assets/([0-9a-f]{64})\)")


def _plain_docx(tmp_path):
    return build_docx(tmp_path / "plain.docx", blocks=[("h1", "标题"), ("p", "正文段落。")])


def _interleaved_docx(tmp_path, image, name="interleaved.docx"):
    """图片**夹在两段正文之间**——这样才检验得了「位置」而不只是「存在」。"""
    path = tmp_path / name
    doc = Document()
    doc.add_heading("第一章 技术要求", level=1)
    doc.add_paragraph("图片之前的正文段落。")
    doc.add_picture(str(image))
    doc.add_paragraph("图片之后的正文段落。")
    doc.save(path)
    return path


def _docx_with_table_and_image(tmp_path, image):
    path = tmp_path / "table-and-image.docx"
    doc = Document()
    doc.add_heading("第一章 技术要求", level=1)
    doc.add_paragraph("表格之前的正文。")
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "项目"
    table.rows[0].cells[1].text = "要求"
    table.rows[1].cells[0].text = "工期"
    table.rows[1].cells[1].text = "30 天"
    doc.add_picture(str(image))
    doc.save(path)
    return path


# ------------------------------------------------------------ 红线：不带图不变


def test_markdown_without_images_has_no_image_traces(tmp_path):
    """R-DW-3 红线：`include_images=False` 的输出不得出现任何图片痕迹。"""
    markdown = docx_to_markdown(_plain_docx(tmp_path))

    assert "<img" not in markdown
    assert "assets/" not in markdown
    assert "标题" in markdown and "正文段落。" in markdown


def test_explicit_false_is_the_same_path_as_default(tmp_path):
    """默认值与显式 `False` 必须走同一条路径——不给未来留分叉。"""
    source = _plain_docx(tmp_path)

    assert docx_to_markdown(source, include_images=False) == docx_to_markdown(source)


# ------------------------------------------------------------------ 占位与位置


def test_placeholder_sits_between_the_surrounding_paragraphs(tmp_path):
    """**位置**断言：占位必须落在文档里图片真正所在的那一处（§8.5）。"""
    image = make_image(tmp_path / "a.png", size=(40, 30), color=(200, 30, 30))
    markdown = docx_to_markdown(_interleaved_docx(tmp_path, image), include_images=True)

    assert len(PLACEHOLDER_RE.findall(markdown)) == 1
    before = markdown.index("图片之前的正文段落。")
    after = markdown.index("图片之后的正文段落。")
    placeholder = markdown.index("](assets/")
    assert before < placeholder < after, markdown


def test_one_placeholder_per_occurrence_and_shared_target_for_repeats(tmp_path):
    """数量 = **出现次数**；同一张图插两次 → 两个占位、**同一个** target（W4）。"""
    a = make_image(tmp_path / "a.png", size=(40, 30), color=(200, 30, 30))
    b = make_image(tmp_path / "b.png", size=(60, 20), color=(10, 120, 200))
    source = build_docx_with_images(
        tmp_path / "multi.docx", blocks=[("p", "正文。")], images=[a, b, a])

    markdown = docx_to_markdown(source, include_images=True)

    hashes = PLACEHOLDER_RE.findall(markdown)
    assert len(hashes) == 3, markdown
    assert len(set(hashes)) == 2, hashes


def test_placeholder_hash_equals_media_content_hash(tmp_path):
    """**跨模块一致性**：占位里的哈希必须就是 `list_media_files` 的 `content_hash`。

    这条是「markdown 的 target 与 `save_docx_assets` 落盘的文件名按构造对齐」的
    直接证据（W3）——mammoth 回调拿到的字节与 `word/media/*` 是同一份。
    """
    a = make_image(tmp_path / "a.png", size=(40, 30), color=(200, 30, 30))
    b = make_image(tmp_path / "b.png", size=(60, 20), color=(10, 120, 200))
    source = build_docx_with_images(tmp_path / "multi.docx", blocks=None, images=[a, b])

    markdown = docx_to_markdown(source, include_images=True)

    assert set(PLACEHOLDER_RE.findall(markdown)) == {
        media.content_hash for media in list_media_files(source)}


# ---------------------------------------------------------------- 不破坏既有解析


def test_tables_and_headings_survive_with_images(tmp_path):
    """图片不该破坏既有解析：标题层级与表格行列数仍然正确。"""
    image = make_image(tmp_path / "a.png", size=(40, 30), color=(200, 30, 30))
    markdown = docx_to_markdown(
        _docx_with_table_and_image(tmp_path, image), include_images=True)

    assert "# 第一章 技术要求" in markdown
    assert "| 项目 | 要求 |" in markdown
    assert "| 工期 | 30 天 |" in markdown
    assert len(PLACEHOLDER_RE.findall(markdown)) == 1


def test_non_docx_include_images_is_a_noop(tmp_path):
    """R-DW-5：P2b 的资产能力只覆盖 docx，其它格式照旧。

    这条走 `parse_document`（router 按格式分派），因为 `docx_to_markdown` 按定义
    只处理 docx——它的 `include_images=True` 分支对其它格式本就不该被调用。
    """
    source = tmp_path / "a.txt"
    source.write_text("纯文本正文。", encoding="utf-8")

    parsed = parse_document(source, provider="local", include_images=True)

    assert "assets/" not in parsed.markdown
    assert "纯文本正文。" in parsed.markdown
