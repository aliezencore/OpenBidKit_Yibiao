from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.domain.shared.errors import ParsingError

_MAGIC = b"PK\x03\x04"
_PDF_MAGIC = b"%PDF-"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0"


@dataclass(frozen=True)
class FileFormat:
    kind: str
    extension: str
    mime: str


_MIME = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "markdown": "text/markdown",
    "txt": "text/plain",
}

SUPPORTED_EXTENSIONS = frozenset({".docx", ".doc", ".pdf", ".xlsx", ".txt", ".md"})


def _head(path: Path, size: int) -> bytes:
    """读文件头 `size` 字节；文件缺失或不可读时返回空串，由调用方判为格式不匹配。"""
    try:
        with path.open("rb") as handle:
            return handle.read(size)
    except OSError:
        return b""


def _has_magic(path: Path, magic: bytes) -> bool:
    """纯魔数比对，与现有 isZipHeader/isPdfHeader/isOleCompoundHeader 同构。"""
    return _head(path, len(magic)) == magic


def detect_file_format(path: Path) -> FileFormat:
    """按魔数识别，扩展名只用于区分 zip 里的 docx/xlsx 与文本类。

    对齐现有 detectFileFormat（convert.mjs:128）：zip 魔数 + `.docx`/`.xlsx`
    后缀区分 Office 子类型；OLE 魔数与 `.doc`/`.xls`/`.ppt` 都会命中，故
    `.doc` 一律按扩展名识别。
    """
    path = Path(path)
    extension = path.suffix.lower()

    if extension in (".md", ".markdown"):
        return FileFormat("markdown", extension, _MIME["markdown"])
    if extension == ".txt":
        return FileFormat("txt", extension, _MIME["txt"])

    if extension not in SUPPORTED_EXTENSIONS:
        raise ParsingError(
            f"不支持的文件格式：{extension or '（无扩展名）'}，"
            f"支持：{', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            code="unsupported-extension",
            path=str(path),
        )

    if extension in (".docx", ".xlsx"):
        if not _has_magic(path, _MAGIC):
            raise ParsingError(
                "文件解析失败：该文件不是有效的 Office 文档，请用 Word/WPS 另存为标准格式后重试",
                code="format-mismatch",
                path=str(path),
            )
        kind = extension.lstrip(".")
        return FileFormat(kind, extension, _MIME[kind])

    if extension == ".pdf":
        if not _has_magic(path, _PDF_MAGIC):
            raise ParsingError("文件解析失败：不是有效的 PDF", code="format-mismatch", path=str(path))
        return FileFormat("pdf", extension, _MIME["pdf"])

    if extension == ".doc":
        if not _has_magic(path, _OLE_MAGIC):
            raise ParsingError("文件解析失败：不是有效的 Word 97-2003 文档", code="format-mismatch", path=str(path))
        return FileFormat("doc", extension, _MIME["doc"])
