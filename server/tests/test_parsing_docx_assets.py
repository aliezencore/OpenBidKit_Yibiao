import hashlib
import zipfile

import pytest

from app.domain.shared.errors import ParsingError
from app.infra.parsing.docx_assets import list_media_files
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
