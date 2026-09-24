"""P4c-3：DOCX 视觉资产提取（导入时落库 visual_assets 行 + subject-media 图片文件）。

真值来源 `client/electron/services/docxVisualAssetExtractor.cjs`（全文）与
`rejectionCheckStore.cjs` 的导入媒体段（:1042-1066）、`subjectMediaFiles.cjs` :30-45。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from app.domain.rejection.docx_visual_assets import (
    build_paragraph_index,
    classify_asset_kind,
    extract_docx_visual_assets,
    extract_nearby_text,
    extract_section_title,
    is_docx_file_path,
    write_visual_media_files,
)


def _build_docx_with_image(tmp_path: Path, title: str, body: str) -> Path:
    """python-docx 造带图 DOCX（inline 图片 + 标题 + 正文段落）。"""
    import io

    from docx import Document
    from PIL import Image

    image_buffer = io.BytesIO()
    Image.new("RGB", (300, 200), (180, 40, 40)).save(image_buffer, format="PNG")
    image_buffer.seek(0)

    document = Document()
    document.add_heading(title, level=1)
    document.add_paragraph(body)
    document.add_picture(image_buffer)
    target = tmp_path / "带图.docx"
    document.save(str(target))
    return target


def test_is_docx_file_path():
    assert is_docx_file_path("C:/x/y/投标.docx") is True
    assert is_docx_file_path("C:/x/y/投标.DOCX") is True
    assert is_docx_file_path("C:/x/y/投标.pdf") is False


def test_extract_docx_visual_assets_from_real_docx(tmp_path):
    docx = _build_docx_with_image(tmp_path, "第三章 投标要求", "响应招标文件全部要求。")
    extracted = extract_docx_visual_assets(docx)

    assert extracted["mediaFiles"], "应至少提取到一张图片"
    media = extracted["mediaFiles"][0]
    assert media["mediaName"].startswith("media/")
    assert media["mimeType"] == "image/png"
    assert len(media["contentHash"]) == 64                 # sha256 hex

    occurrences = extracted["occurrences"]
    assert len(occurrences) == 1
    occurrence = occurrences[0]
    assert occurrence["placement"] == "inline"
    assert occurrence["assetKind"] == "standalone"         # 300x200 不达页扫阈值
    assert occurrence["sectionTitle"] == "第三章 投标要求"   # 标题样式回溯
    assert "响应招标文件全部要求" in occurrence["nearbyText"]  # 邻近文本（图片独占段落取前后）


def test_section_title_falls_back_to_bold_paragraph(tmp_path):
    """无标题样式时回退「加粗短段落」（参考 :117-125 的第二轮回溯）。"""
    import io

    from docx import Document
    from docx.shared import Pt

    document = Document()
    paragraph = document.add_paragraph("")
    run = paragraph.add_run("商务部分")
    run.bold = True
    run.font.size = Pt(12)
    buffer = io.BytesIO()
    from PIL import Image

    Image.new("RGB", (300, 200), (40, 90, 180)).save(buffer, format="PNG")
    buffer.seek(0)
    document.add_picture(buffer)
    path = tmp_path / "bold.docx"
    document.save(str(path))

    extracted = extract_docx_visual_assets(path)
    assert extracted["occurrences"]
    assert extracted["occurrences"][0]["sectionTitle"] == "商务部分"


def test_paragraph_index_and_nearby_text_rules():
    """图片独占段落 → 取前后各最多 2 个有文本段落；无 CJK 段落不当地命中编号规则。"""
    from app.domain.rejection.docx_visual_assets import is_heading_paragraph

    assert is_heading_paragraph({"text": "第三章 投标要求", "styleVal": "", "bold": False})
    assert is_heading_paragraph({"text": "三、资格要求", "styleVal": "", "bold": False})
    assert not is_heading_paragraph({"text": "123456", "styleVal": "", "bold": False})
    assert is_heading_paragraph({"text": "第三章", "styleVal": "heading 1", "bold": False})


def test_classify_asset_kind_thresholds():
    assert classify_asset_kind(600, 700, "inline") == "page-scan"   # ≥500x650
    assert classify_asset_kind(300, 200, "inline") == "standalone"
    assert classify_asset_kind(600, 700, "unknown") == "page-scan"
    assert classify_asset_kind(300, 200, "floating") == "unknown"


def test_write_visual_media_files_by_content_hash(tmp_path):
    media_dir = tmp_path / "subject-media" / "bid-a"
    media_files = [{"mediaName": "media/image1.png", "buffer": b"abc",
                    "contentHash": "hash1", "mimeType": "image/png"}]
    written = write_visual_media_files(media_dir, media_files)
    assert written == {"media/image1.png": "hash1/hash1.png"}
    assert (media_dir / "hash1" / "hash1.png").read_bytes() == b"abc"

    # 同 contentHash 再次写入：不重复写文件，仍返回相对路径
    again = write_visual_media_files(media_dir, media_files)
    assert again == {"media/image1.png": "hash1/hash1.png"}
    assert len([p for p in media_dir.rglob("*") if p.is_file()]) == 1  # 只有一个文件（rglob 含目录）


def test_extract_handles_docx_without_media(tmp_path):
    """无图片的 DOCX：occurrences 与 mediaFiles 均为空（不报错）。"""
    from docx import Document

    document = Document()
    document.add_paragraph("纯文本投标文件")
    path = tmp_path / "plain.docx"
    document.save(str(path))
    extracted = extract_docx_visual_assets(path)
    assert extracted == {"occurrences": [], "mediaFiles": []}


def test_extract_raises_without_document_xml(tmp_path):
    """缺 word/document.xml 的 zip → 明确报错（参考 :219-220）。"""
    path = tmp_path / "broken.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("other.xml", "<x/>")
    import pytest

    with pytest.raises(ValueError, match="DOCX 缺少 word/document.xml"):
        extract_docx_visual_assets(path)
