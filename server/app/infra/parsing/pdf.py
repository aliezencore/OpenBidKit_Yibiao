from __future__ import annotations

from pathlib import Path

import fitz

from app.domain.shared.errors import ParsingError


def _escape_cell(value) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def _render_table(rows: list[list]) -> str:
    if not rows:
        return ""
    header = [_escape_cell(cell) for cell in rows[0]]
    lines = ["| " + " | ".join(header) + " |",
             "| " + " | ".join("---" for _ in header) + " |"]
    for row in rows[1:]:
        padded = [_escape_cell(cell) for cell in row] + [""] * (len(header) - len(row))
        lines.append("| " + " | ".join(padded[: len(header)]) + " |")
    return "\n".join(lines)


def pdf_to_markdown(path: Path) -> str:
    """PDF → markdown（逐页，正文 + GFM 表格）。

    现有 Node 侧用 pdf-parse 取正文、pdfjs-dist 分析图形矩形还原表格
    （convert.mjs:596-737）。这里改用 PyMuPDF 的 find_tables()：算法不同，
    但 spec §8.5 只要求结构等价（表格数量与行列一致），不要求逐字节一致。
    """
    path = Path(path)
    try:
        document = fitz.open(path)
    except Exception as error:  # fitz 抛的是自身的 RuntimeError 子类
        raise ParsingError("文件解析失败：不是有效的 PDF", code="pdf-open-failed", path=str(path)) from error

    sections: list[str] = []
    try:
        for page in document:
            parts: list[str] = []
            text = page.get_text("text").strip()
            if text:
                parts.append(text)
            for table in (page.find_tables() or ()):
                rendered = _render_table(table.extract())
                if rendered:
                    parts.append(rendered)
            sections.append("\n\n".join(parts) if parts else "（本页无可提取文本，可能是扫描件）")
    finally:
        document.close()

    return "\n\n---\n\n".join(sections) + "\n"
