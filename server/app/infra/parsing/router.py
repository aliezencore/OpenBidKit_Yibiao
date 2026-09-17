from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.domain.shared.errors import ParsingError
from app.infra.parsing.docx import docx_to_markdown
from app.infra.parsing.encoding import read_text_with_encoding
from app.infra.parsing.format import FileFormat, detect_file_format
from app.infra.parsing.legacy_doc import doc_to_markdown as legacy_doc_to_markdown
from app.infra.parsing.pdf import pdf_to_markdown
from app.infra.parsing.spreadsheet import spreadsheet_to_markdown

_MINERU_PROVIDERS = frozenset({"mineru-agent-api", "mineru-accurate-api"})
# 注意：这里必须是 **kind**（不带点的格式名），不是扩展名。
# 判据是 `fmt.kind`，写成 {".docx", ...} 会恒为 False（裁定 R1）。
_LOCAL_SUPPORTED_KINDS = frozenset({"docx", "doc", "txt", "markdown", "pdf", "xlsx"})


@dataclass(frozen=True)
class ParserDecision:
    provider: str
    requested_provider: str
    extension: str
    supported: bool
    fallback_to_local: bool


@dataclass(frozen=True)
class ParsedDocument:
    markdown: str
    format_kind: str
    extension: str
    parser_label: str


def resolve_parser(path: Path, provider: str) -> ParserDecision:
    """对齐现有 resolveFileParser（fileService.cjs:70）的字段与回退语义。"""
    path = Path(path)
    fmt = detect_file_format(path)
    provider = provider or "local"
    mineru_supports = provider in _MINERU_PROVIDERS and fmt.kind in ("pdf",)
    local_supports = provider == "local" and fmt.kind in _LOCAL_SUPPORTED_KINDS
    requested_supported = local_supports or mineru_supports
    if requested_supported:
        return ParserDecision(provider, provider, fmt.extension, True, False)
    if provider != "local" and fmt.kind in _LOCAL_SUPPORTED_KINDS:
        return ParserDecision("local", provider, fmt.extension, True, True)
    return ParserDecision(provider, provider, fmt.extension, False, False)


def parse_document(path: Path, *, provider: str = "local", include_images: bool = False) -> ParsedDocument:
    path = Path(path)
    decision = resolve_parser(path, provider)
    if not decision.supported:
        raise ParsingError(
            f"所选解析器（{decision.requested_provider}）不支持 {decision.extension}",
            code="parser-unsupported",
            path=str(path),
        )
    fmt: FileFormat = detect_file_format(path)
    try:
        if decision.provider == "local":
            markdown = _parse_locally(path, fmt)
        else:
            # MinerU 客户端属 P2c；P2a 阶段请求它会被路由回退到本地，故此处不可达。
            raise ParsingError("MinerU 解析属 P2c 范围", code="not-implemented", path=str(path))
    except ParsingError:
        raise
    except Exception as error:
        # 裁定 R12：各格式解析器只负责解析，不各自包 ParsingError；归一化在这里
        # 一处收口。否则「合法 zip 但不是有效 xlsx」（如 docx 改名成 .xlsx）会漏出
        # 裸 zipfile.BadZipFile / openpyxl 异常，绕过 API 的错误信封。
        raise ParsingError(f"文件解析失败：{error}", code="parse-failed", path=str(path)) from error
    label = "local" if decision.fallback_to_local else decision.provider
    return ParsedDocument(markdown=markdown, format_kind=fmt.kind,
                          extension=fmt.extension, parser_label=label)


def _parse_locally(path: Path, fmt: FileFormat) -> str:
    if fmt.kind == "txt":
        return read_text_with_encoding(path) + "\n"
    if fmt.kind == "markdown":
        return read_text_with_encoding(path) + "\n"
    if fmt.kind == "xlsx":
        return spreadsheet_to_markdown(path)
    if fmt.kind == "docx":
        return docx_to_markdown(path)
    if fmt.kind == "doc":
        return legacy_doc_to_markdown(path)
    if fmt.kind == "pdf":
        return pdf_to_markdown(path)
    raise ParsingError(f"尚未支持 {fmt.kind}", code="not-implemented", path=str(path))
