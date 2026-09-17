import pytest
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


def test_load_workbook_failure_propagates_original_error(tmp_path, monkeypatch):
    """裁定 P2a 终审 F4：load_workbook 抛异常时，必须透传原始异常而非 NameError。

    原代码 `finally: wb.close()` 在 load_workbook 失败时因 wb 未绑定而抛 NameError，
    掩盖真实异常。修复后 wb 先置 None，finally 仅在非 None 时关闭。
    """
    class _CustomOpenError(Exception):
        pass

    def _boom(*args, **kwargs):
        raise _CustomOpenError("cannot open workbook")

    monkeypatch.setattr(
        "app.infra.parsing.spreadsheet.load_workbook", _boom
    )
    # 入口文件存在即可；load_workbook 已被替换，不会真正读取内容。
    p = tmp_path / "broken.xlsx"
    p.write_bytes(b"")

    with pytest.raises(_CustomOpenError, match="cannot open workbook"):
        spreadsheet_to_markdown(p)
