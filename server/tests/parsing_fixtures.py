from __future__ import annotations

from pathlib import Path

from docx import Document


def build_docx(
    path: Path,
    *,
    blocks: list[tuple[str, str]] | None = None,
    table: list[list[str]] | None = None,
) -> Path:
    """blocks: [(kind, text)]，kind ∈ {'h1','h2','p','bold-p'}。"""
    doc = Document()
    for kind, text in blocks or []:
        if kind == "h1":
            doc.add_heading(text, level=1)
        elif kind == "h2":
            doc.add_heading(text, level=2)
        elif kind == "bold-p":
            para = doc.add_paragraph()
            run = para.add_run(text)
            run.bold = True
        else:
            doc.add_paragraph(text)
    if table:
        # 注意：python-docx 的 add_table(rows=0, cols=0) 后 add_row() 会得到
        # 0 列的空行，cells[index] 越界。必须按首行列数建表，首行现填、其余追加。
        ncols = len(table[0])
        rendered = doc.add_table(rows=1, cols=ncols)
        header_cells = rendered.rows[0].cells
        for index, value in enumerate(table[0]):
            header_cells[index].text = value
        for row in table[1:]:
            cells = rendered.add_row().cells
            for index, value in enumerate(row):
                cells[index].text = value
    doc.save(path)
    return path
