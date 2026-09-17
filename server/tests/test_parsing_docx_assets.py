import hashlib

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

    for item in media:
        assert item.content_hash == hashlib.sha256(item.data).hexdigest()
        assert item.content_hash != ""


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
    import zipfile

    broken = tmp_path / "broken.docx"
    with zipfile.ZipFile(broken, "w") as archive:
        archive.writestr("not-word.txt", "x")

    with pytest.raises(ParsingError) as info:
        list_media_files(broken)

    assert info.value.code == "docx-open-failed"
