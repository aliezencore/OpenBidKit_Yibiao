import fitz
import pytest

from app.infra.parsing.pdf import pdf_to_markdown


def _make_pdf(path, pages):
    """pages: [list[str]]，每页一组文本行。"""
    doc = fitz.open()
    for lines in pages:
        page = doc.new_page()
        y = 72
        for line in lines:
            page.insert_text((72, y), line, fontsize=12)
            y += 20
    doc.save(path)
    doc.close()
    return path


def test_extracts_text_per_page(tmp_path):
    p = _make_pdf(tmp_path / "a.pdf", [["Chapter One", "body text"]])

    text = pdf_to_markdown(p)

    assert "Chapter One" in text
    assert "body text" in text


def test_pages_are_separated(tmp_path):
    p = _make_pdf(tmp_path / "b.pdf", [["first page"], ["second page"]])

    text = pdf_to_markdown(p)

    assert text.index("first page") < text.index("---") < text.index("second page")


def test_blank_page_gets_placeholder(tmp_path):
    p = _make_pdf(tmp_path / "c.pdf", [[]])

    text = pdf_to_markdown(p)

    assert "本页无可提取文本" in text


def test_no_xml_or_base64_leak(tmp_path):
    p = _make_pdf(tmp_path / "d.pdf", [["plain"]])

    text = pdf_to_markdown(p)

    for bad in ("base64", "<w:", "xmlns"):
        assert bad not in text


def test_missing_file_raises_parsing_error(tmp_path):
    from app.infra.parsing import ParsingError

    with pytest.raises(ParsingError) as info:
        pdf_to_markdown(tmp_path / "nope.pdf")

    # R12 把 `pdf-open-failed` 定为唯一允许在解析模块里归一化的用户可见失败，
    # 那 code 就是契约的一部分，必须被钉住（与 Task 5 的 libreoffice-missing 同例）。
    assert info.value.code == "pdf-open-failed"


def test_find_tables_none_degrades_to_text_only(tmp_path, monkeypatch):
    """R21：find_tables() 内部异常时返回 None，不得拖垮整份 PDF 解析。

    pymupdf/table.py 用宽 except Exception 包住整条找表 pipeline，失败即 `return None`。
    降级口径是「本页没有表格」，正文本照常输出。
    """
    p = _make_pdf(tmp_path / "e.pdf", [["Chapter One", "body text"]])

    monkeypatch.setattr(fitz.Page, "find_tables", lambda self: None)

    text = pdf_to_markdown(p)

    assert "Chapter One" in text
    assert "body text" in text
    assert "|" not in text
