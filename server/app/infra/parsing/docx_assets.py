from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.domain.shared.errors import ParsingError

# 对齐现有 docxVisualAssetExtractor.cjs:13 的 MIME_BY_EXTENSION
MIME_BY_EXTENSION = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".emf": "image/emf",
    ".wmf": "image/wmf",
}


@dataclass(frozen=True)
class MediaFile:
    media_name: str      # 如 "media/image1.png"（去掉前导 word/，对齐 Node）
    data: bytes
    content_hash: str
    mime_type: str
    extension: str


def open_docx_zip(path: Path) -> zipfile.ZipFile:
    """打开 docx。失败一律归一为 ParsingError（本模块唯一的 error code 出口）。"""
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        raise ParsingError(
            "文件解析失败：不是有效的 DOCX 文档",
            code="docx-open-failed",
            path=str(path),
        ) from error


def list_media_files(docx_path: Path) -> list[MediaFile]:
    """列出 `word/media/*` 下的每个文件（对齐 extractDocxVisualAssets 的 mediaFiles）。"""
    docx_path = Path(docx_path)
    with open_docx_zip(docx_path) as archive:
        if "word/document.xml" not in archive.namelist():
            raise ParsingError(
                "DOCX 缺少 word/document.xml，无法提取图片",
                code="docx-open-failed",
                path=str(docx_path),
            )
        media: list[MediaFile] = []
        for name in archive.namelist():
            # 只收一级目录下的文件：`word/media/a/b.png` 不是标准布局
            if not name.startswith("word/media/") or "/" in name[len("word/media/"):]:
                continue
            data = archive.read(name)
            extension = Path(name).suffix.lower()
            media.append(
                MediaFile(
                    media_name=name.replace("word/", "", 1),
                    data=data,
                    content_hash=hashlib.sha256(data).hexdigest(),
                    mime_type=MIME_BY_EXTENSION.get(extension, "application/octet-stream"),
                    extension=extension,
                )
            )
    return media
