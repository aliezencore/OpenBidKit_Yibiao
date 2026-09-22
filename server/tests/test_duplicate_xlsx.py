"""P3d-2 T2 —— 查重导出 xlsx 组装器（duplicate_xlsx）的 openpyxl 读回验证。

流程：oracle full case state → plan（T1 纯函数，已对拍）→ write_duplicate_xlsx
→ openpyxl.load_workbook 读回 → 断言表名序/值/列宽/autofilter/number_format/
截断文本。探针（坐标偏移变异）见本文件提交记录与 ledger。
"""

from __future__ import annotations

import json
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import load_workbook

from app.domain.duplicate.export_workbook import (
    EXCEL_TEXT_PREFIX_LIMIT,
    EXCEL_TEXT_TRUNCATION_SUFFIX,
    build_duplicate_workbook_plan,
)
from app.infra.exporting.duplicate_xlsx import write_duplicate_xlsx

WORKTREE_ROOT = Path(__file__).resolve().parents[2]
ORACLE_PATH = WORKTREE_ROOT / "server" / "tests" / "fixtures" / "oracle-duplicate-export.json"


@pytest.fixture(scope="module")
def full_case():
    return json.loads(ORACLE_PATH.read_text(encoding="utf-8"))["cases"]["full"]


@pytest.fixture(scope="module")
def full_bytes(full_case):
    plan = build_duplicate_workbook_plan(
        full_case["state"], full_case["signature"], datetime.fromisoformat(full_case["exported_at"]))
    return write_duplicate_xlsx(plan)


@pytest.fixture(scope="module")
def full_book(full_bytes):
    return load_workbook(BytesIO(full_bytes))


def test_sheet_name_order(full_book):
    assert full_book.sheetnames == ["查重概览", "元数据", "目录重复", "文件相似度", "重复句子", "重复图片"]


def test_overview_values_and_no_autofilter(full_book, full_case):
    sheet = full_book["查重概览"]
    assert sheet["A1"].value == "导出时间"
    assert sheet["B1"].value == "2026-09-22 14:15:09"
    # 空行 rows[1] 不落 cell
    assert sheet["A2"].value is None
    assert [sheet.cell(row=3, column=c).value for c in range(1, 8)] == \
        ["分析维度", "状态", "结果数量", "更新时间", "招标文件", "投标文件", "说明"]
    # 四维度行：状态/数量/更新时间（ISO 原串）/说明
    assert sheet["B4"].value == "已完成"
    assert sheet["C4"].value == 4
    assert sheet["D4"].value == "2026-09-22T06:00:00.000Z"
    assert sheet["E4"].value == "招标文件.pdf"
    assert sheet["F4"].value == "投标文件A.docx；投标文件B.pdf"
    # 统计块（rows[9..12] → 行 10..13）
    assert sheet["A10"].value == "识别图片总数" and sheet["B10"].value == 25
    assert sheet["A11"].value == "招标目录排除数量" and sheet["B11"].value == 3
    assert sheet["A12"].value == "招标引用句子排除数量" and sheet["B12"].value == 12
    assert sheet["A13"].value == "正文句子总量" and sheet["B13"].value == 800
    # 概览无 autofilter
    assert sheet.auto_filter.ref is None
    # 列宽（wch 映射）
    assert [sheet.column_dimensions[letter].width for letter in "ABCDEFG"] == [16, 14, 12, 22, 30, 34, 42]


def test_metadata_sheet(full_book):
    sheet = full_book["元数据"]
    assert [sheet.cell(row=1, column=c).value for c in range(1, 6)] == \
        ["元数据项", "重复文件", "同日文件", "投标文件1：投标文件A.docx", "投标文件2：投标文件B.pdf"]
    assert sheet["A2"].value == "文件名"
    assert sheet["B2"].value == "投标文件A.docx；投标文件B.pdf"
    assert sheet["C3"].value == "投标文件A.docx"  # same_day_file_ids=[b1] 经 name_by_id 解析
    assert sheet["D5"].value == "某项目投标文件"  # values.b1
    # values.b2 缺键 → 净化成 ""；openpyxl 保留 cell 占位但把空串折叠为 None
    # （SheetJS 写共享空串——Excel 显示等价，登记偏离，spec §15）
    assert sheet["E5"].value is None
    assert sheet.auto_filter.ref == "A1:E5"
    assert [sheet.column_dimensions[letter].width for letter in "ABCDE"] == [24, 32, 32, 32, 32]


def test_outline_sheet_number_format(full_book):
    sheet = full_book["目录重复"]
    assert sheet["A2"].value == 1 and sheet["B2"].value == "重复"
    assert sheet["D2"].value == 1
    assert sheet["D3"].value == 0.875
    assert sheet["E2"].value == "投标文件A.docx；投标文件B.pdf"
    assert sheet["F2"].value == \
        "投标文件A.docx：第一章 项目概述；投标文件A.docx：1.1 背景；投标文件B.pdf：第一章 项目概述"
    assert sheet.auto_filter.ref == "A1:F4"
    # D2-D4 百分比格式；表头行/数据行外不受影响
    for coordinate in ("D2", "D3", "D4"):
        assert sheet[coordinate].number_format == "0.00%", coordinate
    assert sheet["D1"].number_format != "0.00%"
    assert [sheet.column_dimensions[letter].width for letter in "ABCDEF"] == [8, 10, 32, 12, 34, 64]


def test_pairwise_sheet_number_format_and_risk(full_book):
    sheet = full_book["文件相似度"]
    assert sheet["A2"].value == "投标文件A.docx"
    assert sheet["C2"].value == pytest.approx(0.6667)
    assert sheet["H2"].value == "高"
    # risk 全枚举：行 2..6 → 高/中/低/无/无（缺省）
    assert [sheet.cell(row=row, column=8).value for row in range(2, 7)] == ["高", "中", "低", "无", "无"]
    assert sheet.auto_filter.ref == "A1:H6"
    for row in range(2, 7):
        for column in "CDEF":
            assert sheet[f"{column}{row}"].number_format == "0.00%", f"{column}{row}"
    assert sheet["B2"].number_format != "0.00%"


def test_content_sheet_truncated_sentence(full_book):
    sheet = full_book["重复句子"]
    assert sheet["A2"].value == 1
    assert sheet["B2"].value == "本项目工期为九十（90）日历天。"
    assert sheet["D2"].value == "投标文件A.docx × 3；投标文件B.pdf × 2"
    assert sheet["E2"].value == 5
    # d3：控制字符剥除 + 超限截断（plan 层已净化）
    sentence = sheet["B4"].value
    assert sentence == "前中后" + "A" * (EXCEL_TEXT_PREFIX_LIMIT - 3) + EXCEL_TEXT_TRUNCATION_SUFFIX
    assert sheet["D4"].value == "ghost-9 × 0"
    assert sheet["E4"].value == 0
    assert sheet.auto_filter.ref == "A1:E4"


def test_image_sheet_locations(full_book):
    sheet = full_book["重复图片"]
    assert sheet["B2"].value == "abc123def456"
    assert sheet["D2"].value == "投标文件A.docx × 2；投标文件B.pdf × 1"
    assert sheet["E2"].value == \
        "投标文件A.docx：word/media（图片 3）；投标文件A.docx：word/media（图片 5）；投标文件B.pdf：word/embeddings（图片 2）"
    assert sheet["F2"].value == "投标文件A.docx：如下图所示，；投标文件A.docx：；投标文件B.pdf：详见图"
    # i2：无 image_index → 无序号尾；无 directory → 未识别目录
    assert sheet["E3"].value == "投标文件A.docx：未识别目录"
    assert sheet.auto_filter.ref == "A1:F3"


def test_workbook_properties(full_book, full_case):
    assert full_book.properties.title == "标书查重结果"
    # openpyxl 往返丢 tzinfo（xlsx XML 存无时区 ISO）→ naive UTC 墙钟比较
    created_utc = datetime.fromisoformat(full_case["exported_at"]).replace(tzinfo=None)
    assert full_book.properties.created == created_utc


def test_bytes_are_zip_container(full_bytes):
    # openpyxl 默认 zip 压缩（对齐 XLSX.write compression:true）；PK 魔数
    assert full_bytes[:2] == b"PK"
