from __future__ import annotations

import re

from bs4 import BeautifulSoup
from markdownify import markdownify as _markdownify

_TABLE_PLACEHOLDER = "\n\n<!--TABLE:{}-->\n\n"


def _cell_text(cell) -> str:
    """单元格文本；其中的 `<img>` 渲染成**内联 markdown 图片**，而不是被丢掉。

    接线遗留登记 G1（spec §15）：初版直接用 `cell.get_text()` 取文本，单元格里的
    `<img>` 被整个丢掉 → 表格里的图片**既没有占位、资产又照常落了盘（孤儿）**，
    §8.5「图片占位数量与位置一致」在该形状上不成立（标书里「证书/执照贴在表格里」
    很常见）。

    参考的表格是**原始 HTML 回填**（`convert.mjs:557-560`），所以它的 `<img>` 天然
    活着、并专门有 `htmlImageSrcPattern`（`svc.cjs:109`）去抓；本实现的表格按
    spec §8.3 渲染成 **GFM**，故把 `<img>` 就地换成 markdown 图片语法——与流水线
    其余部分同形态，消费侧（P3c）只需认一种图片形态。

    无 `src` 的 `<img>`（不带图模式已在上游 `decompose` 掉，这里是防御）直接摘掉，
    避免产出空图语法。
    """
    for img in cell.find_all("img"):
        src = str(img.get("src") or "")
        if not src:
            img.decompose()
            continue
        img.replace_with(f"![{img.get('alt') or ''}]({src})")
    return cell.get_text(" ", strip=True)


def extract_tables(html: str) -> tuple[str, list[str]]:
    """把 <table> 抽成 GFM 文本并用占位符替换，返回（去表 HTML, GFM 表格列表）。

    对齐现有 preserveTables/restoreTables（convert.mjs:546-573）：turndown 的
    内联规则会破坏表格结构，必须先摘出来单独渲染。（差异：参考回填**原始 HTML**、
    本实现渲染 **GFM**，见 `_cell_text` 的说明。）
    """
    soup = BeautifulSoup(html, "lxml")
    tables: list[str] = []
    for index, table in enumerate(soup.find_all("table")):
        rows = []
        for tr in table.find_all("tr"):
            cells = [_cell_text(cell) for cell in tr.find_all(["th", "td"])]
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
