from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook


def _escape_cell(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\r\n", " ").replace("\n", " ")


def _render_table(rows: list[list]) -> str:
    if not rows:
        return "（空表）"
    header = rows[0]
    body = rows[1:]
    lines = ["| " + " | ".join(_escape_cell(cell) for cell in header) + " |"]
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in body:
        padded = row + [""] * (len(header) - len(row))
        lines.append("| " + " | ".join(_escape_cell(cell) for cell in padded[: len(header)]) + " |")
    return "\n".join(lines)


def spreadsheet_to_markdown(path: Path) -> str:
    """每个 sheet 一节：`## <sheet 名>` + GFM 表格。

    对齐现有 renderSpreadsheetTables（convert.mjs:440）的输出形态；
    数值按 openpyxl 原样转字符串（100 → `100`、200.5 → `200.5`），
    不做千分位或货币格式化——那是显示层的事，比对层不需要。
    """
    wb = None
    sections: list[str] = []
    try:
        wb = load_workbook(path, data_only=True, read_only=True)
        for sheet in wb.worksheets:
            rows = [list(row) for row in sheet.iter_rows(values_only=True)]
            body = f"## {sheet.title}\n\n{_render_table(rows)}"
            sections.append(body)
    finally:
        # 若 load_workbook 本身抛异常，wb 仍为 None——此时不能引用 wb.close()，
        # 否则会把真实的 openpyxl 异常掩盖成 NameError。
        if wb is not None:
            wb.close()
    return "\n\n".join(sections) + "\n"
