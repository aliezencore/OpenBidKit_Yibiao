from __future__ import annotations

from pathlib import Path

from app.infra.parsing.docx_html import docx_to_html
from app.infra.parsing.markdown import extract_tables, html_to_markdown, normalize_markdown, restore_tables


def docx_to_markdown(path: Path, *, include_images: bool = False) -> str:
    """docx → markdown。表格先摘出单独渲染再回填，避免被内联规则破坏。

    - `include_images=False`（默认）：图片一律丢弃（P2a 原行为）。
    - `include_images=True`：图片保留为 `![](assets/<sha256>)` 占位
      （P2a↔P2b 接线，裁定 R-DW-1/2）。

    **资产落盘不在这里**（裁定 R-DW-4）：本函数只读源文件、只返回 markdown
    （P2a 的既有契约）；落盘需要 `data_root` + `project_id`，由
    `content_extraction` 段调 `asset_store.save_docx_assets` 完成。两侧的哈希
    按构造对齐（mammoth 回调读到的字节 == `word/media/*` 的字节）。

    裁定史：P2a 的 R2 曾在此显式拒绝 `include_images=True`（理由：mammoth 的默认
    图片转换会把整张图 base64 塞进 HTML→markdown，污染 LLM 提示词与正文比对）。
    该拒绝的前提「P2b 的图片占位 + 资产提取未落地」已消失，且新实现**不走 base64**
    （回调只产出 `assets/<hash>` 占位），故撤销拒绝。
    """
    html = docx_to_html(Path(path), include_images=include_images)
    html_without_tables, tables = extract_tables(html)
    markdown = html_to_markdown(html_without_tables)
    markdown = restore_tables(markdown, tables)
    return normalize_markdown(markdown) + "\n"
