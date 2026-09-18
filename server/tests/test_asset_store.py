import hashlib
import zipfile

import pytest

from app.domain.shared.errors import ParsingError
from app.infra.parsing.asset_store import save_docx_assets
from tests.parsing_fixtures import build_docx_with_images, make_image


def _docx(tmp_path, images, name="a.docx"):
    return build_docx_with_images(tmp_path / name, blocks=[("p", "正文")], images=images)


def _stray_docx(tmp_path, *, real_png):
    """手工造一个 docx：一张真实图片 + 一条指向 word/media 里不存在目标的出现位置。

    用于 R24——关系表 `rIdGhost` 指向 `media/ghost.png`，但 zip 里没有这个条目。
    """
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document'
        ' xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<w:body>'
        '<w:p><w:r><w:t>正文段落</w:t></w:r></w:p>'
        '<w:p><w:r><w:drawing>'
        '<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
        '<wp:extent cx="9525" cy="9525"/>'
        '<wp:docPr id="1" name="Picture 1"/>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:blipFill><a:blip r:embed="rId1"/></pic:blipFill>'
        '</pic:pic></a:graphicData></a:graphic></wp:inline>'
        '</w:drawing></w:r></w:p>'
        '<w:p><w:r><w:drawing>'
        '<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
        '<wp:extent cx="9525" cy="9525"/>'
        '<wp:docPr id="2" name="Picture 2"/>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:blipFill><a:blip r:embed="rIdGhost"/></pic:blipFill>'
        '</pic:pic></a:graphicData></a:graphic></wp:inline>'
        '</w:drawing></w:r></w:p>'
        '</w:body></w:document>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>'
        '<Relationship Id="rIdGhost" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/ghost.png"/>'
        '</Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="png" ContentType="image/png"/>'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '</Types>'
    )
    path = tmp_path / "stray.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/_rels/document.xml.rels", rels_xml)
        archive.writestr("word/media/image1.png", real_png.read_bytes())
    return path


def test_stores_one_file_per_distinct_image(tmp_path):
    a = make_image(tmp_path / "a.png", size=(40, 30), color=(255, 0, 0))
    b = make_image(tmp_path / "b.png", size=(40, 30), color=(0, 255, 0))
    source = _docx(tmp_path, [a, b])

    assets = save_docx_assets(data_root=tmp_path / "data", project_id="p1", docx_path=source)

    assert len(assets) == 2
    for asset in assets:
        # 注意：extension 已含前导点，路径是 <hash><ext>，不要再加一个点。
        assert (tmp_path / "data" / "projects" / "p1" / "assets" / f"{asset.content_hash}{asset.extension}").exists()


def test_same_image_twice_is_stored_once_with_all_occurrences(tmp_path):
    image = make_image(tmp_path / "same.png")
    source = _docx(tmp_path, [image, image])

    assets = save_docx_assets(data_root=tmp_path / "data", project_id="p1", docx_path=source)

    assert len(assets) == 1
    assert len(assets[0].occurrences) == 2
    assert assets[0].occurrences[0].media_name == assets[0].occurrences[1].media_name


def test_content_hash_matches_the_stored_file(tmp_path):
    image = make_image(tmp_path / "a.png")
    source = _docx(tmp_path, [image])

    asset = save_docx_assets(data_root=tmp_path / "data", project_id="p1", docx_path=source)[0]

    stored = (tmp_path / "data" / "projects" / "p1" / "assets" / f"{asset.content_hash}{asset.extension}").read_bytes()
    assert asset.size_bytes == len(stored)


def test_prepared_image_is_carried_on_the_asset(tmp_path):
    image = make_image(tmp_path / "a.png", size=(50, 40))
    source = _docx(tmp_path, [image])

    asset = save_docx_assets(data_root=tmp_path / "data", project_id="p1", docx_path=source)[0]

    assert asset.prepared.action == "compress"
    assert asset.prepared.data_url.startswith("data:")


def test_skipped_image_is_kept_as_an_asset_with_its_code(tmp_path):
    tiff = make_image(tmp_path / "a.tiff", size=(20, 20), fmt="TIFF")
    source = _docx(tmp_path, [tiff])

    assets = save_docx_assets(data_root=tmp_path / "data", project_id="p1", docx_path=source)

    assert len(assets) == 1
    assert assets[0].prepared.action == "skip"
    assert assets[0].prepared.skip_code == "UNSUPPORTED_IMAGE_FORMAT"
    # 决策：skip 的资产写**原图字节**（extension 退回 media.extension）。
    # 钉住这件事——否则 P4 的「未能识别的图片」清单就看不到原图。
    stored = (tmp_path / "data" / "projects" / "p1" / "assets" / f"{assets[0].content_hash}{assets[0].extension}").read_bytes()
    assert stored == tiff.read_bytes()


def test_docx_without_images_yields_no_assets(tmp_path):
    from tests.parsing_fixtures import build_docx

    source = build_docx(tmp_path / "plain.docx", blocks=[("p", "没有图")])

    assert save_docx_assets(data_root=tmp_path / "data", project_id="p1", docx_path=source) == []


def test_unsafe_project_id_is_rejected(tmp_path):
    """复用 P2a 的同一道守卫，不再复制一份。"""
    image = make_image(tmp_path / "a.png")
    source = _docx(tmp_path, [image])

    with pytest.raises(ParsingError) as info:
        save_docx_assets(data_root=tmp_path / "data", project_id="../evil", docx_path=source)

    assert info.value.code == "invalid-project-id"


def test_stored_identity_tracks_the_media_bytes(tmp_path):
    """落盘实体是**媒体文件本身**：`content_hash` 必须等于原图的 sha256。

    裁定：原先这条写成 `all(isinstance(asset.occurrences, list))` 是平凡真的
    （任何列表都成立），什么也没钉住。改为钉真正的契约——哈希取自媒体字节。
    """
    image = make_image(tmp_path / "a.png")
    source = _docx(tmp_path, [image])

    assets = save_docx_assets(data_root=tmp_path / "data", project_id="p1", docx_path=source)

    assert [asset.content_hash for asset in assets] == [hashlib.sha256(image.read_bytes()).hexdigest()]


def test_stray_occurrence_without_matching_media_is_not_attached(tmp_path):
    """R24 — 关系表指向 word/media 里不存在的目标时：

    该出现位置**不**挂到任何资产上、**不**抛异常，其余资产照常产出（不连坐）。
    信息不丢：`extract_docx_visual_assets()` 本来就同时返回 occurrences 与 media_files。
    """
    real = make_image(tmp_path / "real.png", size=(40, 30), color=(0, 0, 255))
    source = _stray_docx(tmp_path, real_png=real)

    assets = save_docx_assets(data_root=tmp_path / "data", project_id="p1", docx_path=source)

    # 其余资产照常产出（真实图片一份）
    assert len(assets) == 1
    assert assets[0].content_hash == hashlib.sha256(real.read_bytes()).hexdigest()

    # 悬空引用（ghost.png）不出现在任何资产的 occurrences 里
    all_occurrence_media = [o.media_name for asset in assets for o in asset.occurrences]
    assert "media/ghost.png" not in all_occurrence_media
    # 真实图片的出现位置仍然被挂上
    assert "media/image1.png" in all_occurrence_media
