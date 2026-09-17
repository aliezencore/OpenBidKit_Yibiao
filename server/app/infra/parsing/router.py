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
    local_supports = provider == "local" and fmt.kind in _LOCAL_SUPPORTED_KINDS
    if local_supports:
        return ParserDecision(provider, provider, fmt.extension, True, False)
    if provider != "local" and fmt.kind in _LOCAL_SUPPORTED_KINDS:
        # P2a 阶段：MinerU 等非 local 提供方统一回退到本地（计划既定行为）。
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
            markdown = _parse_locally(path, fmt, include_images=include_images)
        else:
            # MinerU 客户端属 P2c，是 P2c 的官方接入点。P2a 阶段所有 supported 的
            # 请求都会被 `resolve_parser` 路由回本地（`decision.provider == "local"`），
            # 故此处不可达；保留作为 P2c 的占位与不变式守卫。若 P2c 引入「本地不支持、
            # 仅它支持」的格式，这条分支才会真正触发。
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


def _parse_locally(path: Path, fmt: FileFormat, *, include_images: bool = False) -> str:
    if fmt.kind == "txt":
        return read_text_with_encoding(path) + "\n"
    if fmt.kind == "markdown":
        return read_text_with_encoding(path) + "\n"
    if fmt.kind == "xlsx":
        return spreadsheet_to_markdown(path)
    if fmt.kind == "docx":
        # 裁定 R23：include_images 只在 docx 路径有意义——R2 在 docx_to_markdown
        # 里对 include_images=True 显式拒绝；不转发就等于静默丢弃该参数，调用方
        # 会误以为已请求到图片。其余格式解析器的签名刻意收窄，故不加该参数。
        return docx_to_markdown(path, include_images=include_images)
    if fmt.kind == "doc":
        return legacy_doc_to_markdown(path)
    if fmt.kind == "pdf":
        return pdf_to_markdown(path)
    raise ParsingError(f"尚未支持 {fmt.kind}", code="not-implemented", path=str(path))
