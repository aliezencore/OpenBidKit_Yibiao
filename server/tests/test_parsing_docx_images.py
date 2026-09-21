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
    """表格**之外**再放一张图（`image=None` → 只有表格，用于「无图表格不受影响」）。"""
    path = tmp_path / "table-and-image.docx"
    doc = Document()
    doc.add_heading("第一章 技术要求", level=1)
    doc.add_paragraph("表格之前的正文。")
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "项目"
    table.rows[0].cells[1].text = "要求"
    table.rows[1].cells[0].text = "工期"
    table.rows[1].cells[1].text = "30 天"
    if image is not None:
        doc.add_picture(str(image))
    doc.save(path)
    return path


def _docx_with_table_image(tmp_path, image):
    """图片**放在表格单元格里**（G1 的形状：标书里「证书贴在表格里」）。"""
    path = tmp_path / "table-image.docx"
    doc = Document()
    doc.add_paragraph("表格之前的正文。")
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "项目"
    table.rows[0].cells[1].text = "示意图"
    table.rows[1].cells[0].text = "营业执照"
    table.rows[1].cells[1].paragraphs[0].add_run().add_picture(str(image))
    doc.save(path)
    return path


# ------------------------------------------------------------ 红线：不带图不变


def test_markdown_without_images_has_no_image_traces(tmp_path):
    """R-DW-3 红线：`include_images=False` 的输出不得出现任何图片痕迹。

    **必须用「有图」的 docx**（T4-T7 评审 A 的 F1）：初版这里用的是**无图** docx，
    断言恒真——把 `docx_html.py` 的 `if not include_images:` 改成 `if False:`
    （永不剥空 `<img>`），全量 544 仍全绿。现在两种模式对同一个带图 docx 断言。
    """
    image = make_image(tmp_path / "a.png", size=(40, 30), color=(200, 30, 30))
    source = build_docx_with_images(
        tmp_path / "img.docx", blocks=[("p", "正文段落。")], images=[image])

    markdown = docx_to_markdown(source)

    assert "<img" not in markdown
    assert "assets/" not in markdown
    # 空 src 占位必须被剥干净——mammoth 会留下 `<img>`，只剥标签不剥残留就会
    # 产出 `![]( )` / `![]()` 这类空图语法（变异探针实测的失败形态）。
    assert "![]" not in markdown, markdown
    assert "正文段落。" in markdown


def test_image_presence_does_not_change_the_no_image_output(tmp_path):
    """更强的不变式：`include_images=False` 时，**有没有图**必须产出同一段文本。

    比「default == 显式 False」强：那两条只证明开关一致，证明不了「图片真的被剥干净
    且没留残余」。这里拿「同段落文本 + 一张图」与「同段落文本、无图」对拍。
    """
    image = make_image(tmp_path / "a.png", size=(40, 30), color=(200, 30, 30))
    with_image = build_docx_with_images(
        tmp_path / "with.docx", blocks=[("p", "正文段落。")], images=[image])
    without_image = build_docx(tmp_path / "without.docx", blocks=[("p", "正文段落。")])

    assert docx_to_markdown(with_image) == docx_to_markdown(without_image)


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


def test_table_embedded_image_keeps_its_placeholder(tmp_path):
    """表格单元格里的图片**也必须留下占位**（spec §15 接线遗留登记 G1）。

    根因：`markdown.extract_tables` 用 `cell.get_text()` 取单元格文本 → 单元格里的
    `<img>` 被整个丢掉（0 占位），而资产照常落盘（**孤儿**）→ §8.5「图片占位数量与
    位置一致」在该形状上不成立。标书里「营业执照/资质证书贴在表格里」很常见。

    **两种渲染路线的差别**：参考把**原始表格 HTML** 回填（`convert.mjs:557-560`），
    所以它的 `<img>` 天然活着、并专门有 `htmlImageSrcPattern` 去抓；本实现的表格按
    spec §8.3 渲染成 **GFM**，故这里把单元格内的 `<img>` 渲染成**内联 markdown 图片**
    ——与流水线其余部分形态统一，消费侧（P3c）只需认一种图片形态。
    """
    image = make_image(tmp_path / "a.png", size=(40, 30), color=(200, 30, 30))
    source = _docx_with_table_image(tmp_path, image)

    markdown = docx_to_markdown(source, include_images=True)

    assert len(PLACEHOLDER_RE.findall(markdown)) == 1, markdown
    # 占位必须**留在表格行里**（不是掉到表格外的某处）
    assert "| 营业执照 | ![](assets/" in markdown, markdown
    assert "| 项目 | 示意图 |" in markdown


def test_table_embedded_image_hash_matches_media(tmp_path):
    """表内图片的哈希同样取自图片字节（与文档级图片同一契约）。"""
    image = make_image(tmp_path / "a.png", size=(40, 30), color=(7, 90, 40))
    source = _docx_with_table_image(tmp_path, image)

    markdown = docx_to_markdown(source, include_images=True)

    assert set(PLACEHOLDER_RE.findall(markdown)) == {
        media.content_hash for media in list_media_files(source)}


def test_table_without_images_is_unaffected(tmp_path):
    """无图表格的输出**逐字节不变**（G1 的修复只该影响含图表格）。"""
    source = _docx_with_table_and_image(tmp_path, None)

    assert docx_to_markdown(source, include_images=True) == docx_to_markdown(source)


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
