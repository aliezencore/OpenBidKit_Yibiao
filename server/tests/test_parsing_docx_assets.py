import hashlib
import zipfile

import pytest

from app.domain.shared.errors import ParsingError
from app.infra.parsing.docx_assets import list_media_files
from app.infra.parsing.docx_assets import (
    MAX_SECTION_TITLE_LENGTH,
    build_paragraph_index,
    extract_nearby_text,
    extract_section_title,
    find_paragraph_position,
    is_heading_paragraph,
)
from tests.parsing_fixtures import build_docx_with_images, make_image


def _docx_with_two_images(tmp_path):
    first = make_image(tmp_path / "a.png", size=(40, 30), color=(200, 30, 30))
    second = make_image(tmp_path / "b.jpg", size=(60, 20), color=(30, 30, 200), fmt="JPEG")
    return build_docx_with_images(
        tmp_path / "with-images.docx",
        blocks=[("p", "正文段落")],
        images=[first, second],
    )


def test_lists_every_media_entry(tmp_path):
    source = _docx_with_two_images(tmp_path)

    media = list_media_files(source)

    assert len(media) == 2
    assert sorted(item.extension for item in media) == [".jpg", ".png"]
    assert all(item.media_name.startswith("media/") for item in media)


def test_content_hash_is_sha256_of_bytes(tmp_path):
    source = _docx_with_two_images(tmp_path)

    media = list_media_files(source)

    assert media, "夹具应产出至少一张图片，空循环不算覆盖"
    # 独立从 zip 读出原始字节做基准，不复用被测模块给出的 item.data/hash，
    # 否则断言退化成「自己等自己」（裁定 R4）。
    with zipfile.ZipFile(source) as archive:
        for item in media:
            stored = archive.read("word/" + item.media_name)
            assert item.content_hash == hashlib.sha256(item.data).hexdigest()
            assert item.content_hash != ""
            assert item.data == stored
            assert item.content_hash == hashlib.sha256(stored).hexdigest()


def test_mime_type_comes_from_extension(tmp_path):
    source = _docx_with_two_images(tmp_path)

    by_extension = {item.extension: item.mime_type for item in list_media_files(source)}

    assert by_extension[".png"] == "image/png"
    assert by_extension[".jpg"] == "image/jpeg"


def test_no_media_means_empty_list(tmp_path):
    from tests.parsing_fixtures import build_docx

    plain = build_docx(tmp_path / "plain.docx", blocks=[("p", "没有图")])

    assert list_media_files(plain) == []


def test_missing_document_xml_raises(tmp_path):
    """裁定：docx 不是有效 zip、或缺 word/document.xml 时，抛 ParsingError 而非裸异常。"""
    broken = tmp_path / "broken.docx"
    with zipfile.ZipFile(broken, "w") as archive:
        archive.writestr("not-word.txt", "x")

    with pytest.raises(ParsingError) as info:
        list_media_files(broken)

    assert info.value.code == "docx-open-failed"


def test_not_a_zip_raises(tmp_path):
    """裁定 R4：扩展名是 .docx 但内容根本不是 zip 时，同样归一为 ParsingError。"""
    impostor = tmp_path / "not-a-zip.docx"
    impostor.write_bytes(b"this is not a zip archive, just plain text")

    with pytest.raises(ParsingError) as info:
        list_media_files(impostor)

    assert info.value.code == "docx-open-failed"


def test_nested_media_paths_are_skipped(tmp_path):
    """裁定 R4：word/media/a/b.png 不是标准布局要跳过，同一 zip 里的一级条目仍要保留。"""
    source = _docx_with_two_images(tmp_path)
    nested_bytes = make_image(tmp_path / "deep.png", size=(8, 8)).read_bytes()

    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr("word/media/nested/deep.png", nested_bytes)

    # 先确认追加真的落进 zip，否则下面的「不在清单里」会因为夹具失效而假绿。
    with zipfile.ZipFile(source) as archive:
        assert "word/media/nested/deep.png" in archive.namelist()

    names = [item.media_name for item in list_media_files(source)]

    assert "media/nested/deep.png" not in names
    assert "media/image1.png" in names


from app.infra.parsing.docx_assets import extract_run_text, parse_relationships, unescape_xml


def test_parses_relationship_id_to_target():
    xml = (
        '<Relationships>'
        '<Relationship Id="rId4" Type="t" Target="media/image1.png"/>'
        '<Relationship Id="rId5" Type="t" Target="/word/media/image2.jpeg"/>'
        '</Relationships>'
    )

    rels = parse_relationships(xml)

    assert rels["rId4"] == "media/image1.png"
    assert rels["rId5"] == "word/media/image2.jpeg"  # 前导 / 被去掉


def test_ignores_relationship_without_id_or_target():
    xml = '<Relationship Type="t" Target="x"/><Relationship Id="rId1"/>'

    assert parse_relationships(xml) == {}


def test_unescapes_the_five_entities():
    assert unescape_xml("a&lt;b&gt;c&amp;d&quot;e&apos;f") == "a<b>c&d\"e'f"


def test_entity_replacement_is_single_pass():
    """裁定 R5：**替换顺序必须被钉住**。

    上一条用例在正反两种顺序下都通过（`c&amp;d` 两种顺序结果相同），所以
    「`&lt;`/`&gt;` 先于 `&amp;`」这一关键顺序此前无任何保护。顺序反了会让
    `&amp;lt;` 二次反转义成 `<`（本应是字面量 `&lt;`），是把文档里的
    「&lt;」显示成「<」这类静默损坏。这条断言就是那个钉子。
    """
    assert unescape_xml("&amp;lt;") == "&lt;"


def test_extract_run_text_joins_only_visible_runs():
    block = (
        '<w:p><w:r><w:t>第一</w:t></w:r>'
        '<w:r><w:t xml:space="preserve"> 段 </w:t></w:r>'
        '<w:r><w:t>文字</w:t></w:r></w:p>'
    )

    assert extract_run_text(block) == "第一 段 文字"


def test_extract_run_text_ignores_position_offsets():
    """坐标是元素文本，绝不能被当成可见文字——否则会污染上下文并被误判成章节编号。"""
    block = (
        '<w:p><wp:posOffset>1143000</wp:posOffset>'
        '<w:r><w:t>真正的正文</w:t></w:r>'
        '<wp:posOffset>132715</wp:posOffset></w:p>'
    )

    text = extract_run_text(block)

    assert text == "真正的正文"
    assert "1143000" not in text


def test_extract_run_text_collapses_whitespace():
    block = "<w:p><w:r><w:t>a   \n  b</w:t></w:r></w:p>"
    assert extract_run_text(block) == "a b"


def _paragraphs(*specs):
    """specs: [(text, style_val, bold)] -> 一段 docx XML。"""
    blocks = []
    for text, style, bold in specs:
        style_xml = f'<w:pStyle w:val="{style}"/>' if style else ""
        bold_xml = "<w:b/>" if bold else ""
        blocks.append(f"<w:p><w:pPr>{style_xml}</w:pPr><w:r><w:rPr>{bold_xml}</w:rPr><w:t>{text}</w:t></w:r></w:p>")
    return "<w:body>" + "".join(blocks) + "</w:body>"


def test_builds_one_entry_per_paragraph():
    index = build_paragraph_index(_paragraphs(("甲", "", False), ("乙", "", False)))
    assert [p.text for p in index.paragraphs] == ["甲", "乙"]
    assert len(index.positions) == 2


def test_heading_by_style_beats_length():
    long_heading = "很长的一级标题" * 10
    index = build_paragraph_index(_paragraphs((long_heading, "Heading1", False)))
    assert is_heading_paragraph(index.paragraphs[0]) is True


def test_chinese_numbering_counts_as_heading():
    index = build_paragraph_index(
        _paragraphs(("第一章 总体要求", "", False), ("1.2.3 建设目标", "", False),
                    ("（一）一般要求", "", False), ("一、总则", "", False))
    )
    assert all(is_heading_paragraph(p) for p in index.paragraphs)


def test_plain_sentence_is_not_a_heading():
    index = build_paragraph_index(_paragraphs(("本项目采用公开招标方式，投标人须按要求编制投标文件。", "", False)))
    assert is_heading_paragraph(index.paragraphs[0]) is False


def test_coordinates_without_cjk_are_not_headings():
    """纯数字/无中文的段落不得命中编号规则。"""
    index = build_paragraph_index(_paragraphs(("1143000 132715", "", False), ("1.2", "", False)))
    assert all(is_heading_paragraph(p) is False for p in index.paragraphs)


def test_over_long_paragraph_is_not_a_heading():
    text = "第" + "一" * (MAX_SECTION_TITLE_LENGTH + 5)
    index = build_paragraph_index(_paragraphs((text, "", False)))
    assert is_heading_paragraph(index.paragraphs[0]) is False


def test_section_title_prefers_nearest_heading():
    index = build_paragraph_index(_paragraphs(("第一章 总则", "", False), ("正文", "", False)))
    assert extract_section_title(index, 1) == "第一章 总则"


def test_section_title_falls_back_to_bold_short_paragraph():
    index = build_paragraph_index(_paragraphs(("加粗小标题", "", True), ("正文", "", False)))
    assert extract_section_title(index, 1) == "加粗小标题"


def test_section_title_is_none_without_any_candidate():
    index = build_paragraph_index(_paragraphs(("普通的一段话，不算标题。", "", False)))
    assert extract_section_title(index, 1) is None


def test_nearby_text_prefers_own_paragraph():
    index = build_paragraph_index(_paragraphs(("上一段", "", False), ("自己的文字", "", False), ("下一段", "", False)))
    assert extract_nearby_text(index, 1) == "自己的文字"


def test_nearby_text_takes_two_before_and_two_after_when_own_is_empty():
    """顺序是**先近后远**（距离 1、2），不是文档顺序——对齐参考实现的循环。

    参考 `cjs:126-136` 用 `for offset = 1..2` 先取 `position - 1` 再取 `position - 2`，
    产出 `前二 更前`；照抄即得下面的期望。**不要**因为「文档顺序读起来更顺」
    就把它改成 `更前 前二`——那会偏离参考，且这条 200 字的上下文提示串
    顺序差异对下游没有实质影响，没有理由为此产生分歧。
    """
    index = build_paragraph_index(_paragraphs(
        ("更前", "", False), ("前二", "", False), ("", "", False), ("后一", "", False), ("后二", "", False),
    ))
    assert extract_nearby_text(index, 2) == "前二 更前 后一 后二"


def test_nearby_text_is_truncated():
    long_text = "字" * 500
    index = build_paragraph_index(_paragraphs(("", "", False), (long_text, "", False)))
    assert len(extract_nearby_text(index, 0)) == 200


def test_find_paragraph_position_locates_char_index():
    index = build_paragraph_index(_paragraphs(("甲", "", False), ("乙", "", False)))
    second_start = index.positions[1][0]
    assert find_paragraph_position(index, second_start) == 1
    assert find_paragraph_position(index, index.positions[0][0]) == 0
    assert find_paragraph_position(index, 10 ** 9) == -1
