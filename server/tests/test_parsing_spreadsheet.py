from openpyxl import Workbook

from app.infra.parsing.spreadsheet import spreadsheet_to_markdown


def _make_workbook(path, sheets):
    wb = Workbook()
    first = wb.active
    first.title = sheets[0][0]
    for row in sheets[0][1]:
        first.append(row)
    for name, rows in sheets[1:]:
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    wb.save(path)


def test_renders_header_and_rows(tmp_path):
    p = tmp_path / "报价.xlsx"
    _make_workbook(p, [("报价", [["项目", "金额"], ["土建", 100], ["安装", 200.5]])])

    text = spreadsheet_to_markdown(p)

    assert "## 报价" in text
    assert "| 项目 | 金额 |" in text
    assert "| --- | --- |" in text
    assert "| 土建 | 100 |" in text
    assert "| 安装 | 200.5 |" in text


def test_multiple_sheets_each_get_heading(tmp_path):
    p = tmp_path / "表.xlsx"
    _make_workbook(p, [("A", [["x"]]), ("B", [["y"]])])

    text = spreadsheet_to_markdown(p)

    assert text.index("## A") < text.index("## B")
    assert "| x |" in text and "| y |" in text


def test_empty_sheet_gets_placeholder(tmp_path):
    p = tmp_path / "空.xlsx"
    _make_workbook(p, [("空白", [])])

    text = spreadsheet_to_markdown(p)

    assert "## 空白" in text
    assert "（空表）" in text


def test_pipe_in_cell_is_escaped(tmp_path):
    p = tmp_path / "管道.xlsx"
    _make_workbook(p, [("S", [["a|b", "c"]])])

    text = spreadsheet_to_markdown(p)

    assert "a\\|b" in text
