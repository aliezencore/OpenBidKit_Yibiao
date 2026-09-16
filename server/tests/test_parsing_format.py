import pytest

from app.infra.parsing.format import SUPPORTED_EXTENSIONS, detect_file_format
from app.infra.parsing import ParsingError


def test_detects_docx_by_magic(tmp_path):
    p = tmp_path / "a.docx"
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 32)
    fmt = detect_file_format(p)
    assert fmt.kind == "docx"
    assert fmt.extension == ".docx"


def test_detects_xlsx_by_magic(tmp_path):
    p = tmp_path / "a.xlsx"
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 32)
    assert detect_file_format(p).kind == "xlsx"


def test_detects_pdf_by_magic(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(b"%PDF-1.7\n" + b"\x00" * 32)
    assert detect_file_format(p).kind == "pdf"


def test_txt_and_markdown_are_text(tmp_path):
    assert detect_file_format(tmp_path / "a.txt").kind == "txt"
    assert detect_file_format(tmp_path / "a.md").kind == "markdown"


def test_doc_detected_by_extension_only(tmp_path):
    # OLE 魔数 D0CF11E0 会与 .xls/.ppt 混淆，.doc 一律按扩展名识别。
    p = tmp_path / "a.doc"
    p.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 32)
    assert detect_file_format(p).kind == "doc"


def test_unknown_extension_raises_parsing_error(tmp_path):
    p = tmp_path / "a.exe"
    p.write_bytes(b"MZ")
    with pytest.raises(ParsingError) as info:
        detect_file_format(p)
    assert info.value.code == "unsupported-extension"
    assert ".exe" in str(info.value)


def test_magic_mismatch_wins_over_extension(tmp_path):
    # 扩展名是 .docx 但内容不是 zip：以魔数为准，判 unknown 并报错。
    p = tmp_path / "fake.docx"
    p.write_bytes(b"not a zip at all")
    with pytest.raises(ParsingError) as info:
        detect_file_format(p)
    assert info.value.code == "format-mismatch"


def test_supported_extensions_covers_brief_formats():
    assert SUPPORTED_EXTENSIONS == frozenset(
        {".docx", ".doc", ".pdf", ".xlsx", ".txt", ".md"}
    )
