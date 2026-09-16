from __future__ import annotations

from pathlib import Path

from app.domain.shared.errors import ParsingError
from app.infra.parsing.docx_html import docx_to_html
from app.infra.parsing.markdown import extract_tables, html_to_markdown, normalize_markdown, restore_tables


def docx_to_markdown(path: Path, *, include_images: bool = False) -> str:
    """docx → markdown。表格先摘出单独渲染再回填，避免被内联规则破坏。"""
    if include_images:
        # 裁定 R2：P2a 不产出图片。mammoth 的默认图片转换会把整张图编码成
        # base64 data URL 塞进 HTML→markdown，体积与噪音都会污染 LLM 提示词
        # 与正文比对。真正的图片占位 + 资产提取属 P2b，此处显式拒绝而不是
        # 留一条「能用但会泄漏 base64」的路径。
        raise ParsingError(
            "docx 图片占位与资产提取属 P2b 范围，P2a 不支持 include_images=True",
            code="not-implemented",
            path=str(path),
        )
    html = docx_to_html(Path(path))
    html_without_tables, tables = extract_tables(html)
    markdown = html_to_markdown(html_without_tables)
    markdown = restore_tables(markdown, tables)
    return normalize_markdown(markdown) + "\n"
