"""查重导出 xlsx 序列化组装器（P3d-2 T2，R-P3d2-7）。

openpyxl 形态映射（对齐参考 SheetJS 用法）：

- SheetJS ``!cols`` 的 ``{wch}`` → ``column_dimensions[letter].width``；
- SheetJS ``!autofilter.ref`` → ``ws.auto_filter.ref``；
- SheetJS ``cell.z`` → ``cell.number_format``（``0.00%``）；
- ``XLSX.write(compression: true)`` → openpyxl 默认 zip 压缩；
- 行值在 plan 构造时已净化（number 直写、文本已剥控制字符）——openpyxl 的
  ``IllegalCharacterError`` 不会触发；``append`` 对 None/空列表只推进行号
  不落 cell（对齐 ``aoa_to_sheet`` 的 null 跳过语义）。

保真锚点是**工作表内容结构**（rows/列宽/autofilter/数字格式），不是文件
字节（openpyxl 与 SheetJS 的默认字体/样式差异不在保真范围，spec §15）。
"""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from app.domain.duplicate.export_workbook import DuplicateWorkbookPlan


def write_duplicate_xlsx(plan: DuplicateWorkbookPlan) -> bytes:
    """把 plan 序列化为 xlsx 字节（HTTP 下载响应体）。"""
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.properties.title = plan.title
    workbook.properties.created = plan.created_at

    for sheet in plan.sheets:
        worksheet = workbook.create_sheet(title=sheet.name)
        for row in sheet.rows:
            worksheet.append(row)
        for index, width in enumerate(sheet.widths, start=1):
            worksheet.column_dimensions[get_column_letter(index)].width = width
        if sheet.autofilter_ref:
            worksheet.auto_filter.ref = sheet.autofilter_ref
        for coordinate, number_format in sheet.percent_cells.items():
            worksheet[coordinate].number_format = number_format

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
