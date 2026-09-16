from __future__ import annotations

import re

from bs4 import BeautifulSoup
from markdownify import markdownify as _markdownify

_TABLE_PLACEHOLDER = "\n\n<!--TABLE:{}-->\n\n"


def extract_tables(html: str) -> tuple[str, list[str]]:
    """把 <table> 抽成 GFM 文本并用占位符替换，返回（去表 HTML, GFM 表格列表）。

    对齐现有 preserveTables/restoreTables（convert.mjs:546-573）：turndown 的
    内联规则会破坏表格结构，必须先摘出来单独渲染。
    """
    soup = BeautifulSoup(html, "lxml")
    tables: list[str] = []
    for index, table in enumerate(soup.find_all("table")):
        rows = []
        for tr in table.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
            if cells:
                rows.append(cells)
        if not rows:
            table.decompose()
            continue
        header = rows[0]
        lines = ["| " + " | ".join(header) + " |",
                 "| " + " | ".join("---" for _ in header) + " |"]
        for row in rows[1:]:
            row = row + [""] * (len(header) - len(row))
            lines.append("| " + " | ".join(row[: len(header)]) + " |")
        tables.append("\n".join(lines))
        table.replace_with(_TABLE_PLACEHOLDER.format(index).strip())
    return str(soup), tables


def restore_tables(markdown: str, tables: list[str]) -> str:
    for index, table in enumerate(tables):
        markdown = markdown.replace(f"<!--TABLE:{index}-->", table)
    return markdown


def html_to_markdown(html: str) -> str:
    return _markdownify(html, heading_style="ATX", bullets="-")


def normalize_markdown(text: str) -> str:
    """归一化：压掉连续空行、去行尾空白、统一标题前的空行数。

    对齐现有 normalizeGeneratedMarkdown 的意图；具体规则以「可读、无噪音」为准，
    不追求与 turndown 输出逐字节一致（spec §8.5 结构等价）。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+$", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
