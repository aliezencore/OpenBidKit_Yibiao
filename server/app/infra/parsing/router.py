from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.domain.shared.errors import ParsingError
from app.infra.parsing.docx import docx_to_markdown
from app.infra.parsing.encoding import read_text_with_encoding
from app.infra.parsing.format import FileFormat, detect_file_format
from app.infra.parsing.legacy_doc import doc_to_markdown as legacy_doc_to_markdown
from app.infra.parsing.mineru import SUPPORTED_EXTENSIONS as _MINERU_EXTENSIONS
from app.infra.parsing.mineru import build_mineru_parser
from app.infra.parsing.pdf import pdf_to_markdown
from app.infra.parsing.spreadsheet import spreadsheet_to_markdown

# 注意：这里必须是 **kind**（不带点的格式名），不是扩展名。
# 判据是 `fmt.kind`，写成 {".docx", ...} 会恒为 False（裁定 R1）。
_LOCAL_SUPPORTED_KINDS = frozenset({"docx", "doc", "txt", "markdown", "pdf", "xlsx"})


class RemoteParser(Protocol):
    """远端解析器的最小契约（P2c）：只要求能把文件解析成 markdown。"""

    def parse_file(self, path: Path) -> str: ...


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
    """对齐现有 `resolveFileParser`（fileService.cjs:70-83）的字段与回退语义。

    **P2c 恢复参考语义**（推翻 P2a 的阶段性收窄「MinerU 一律回退本地」）：

    - 非 local provider：其**扩展名支持表**命中 → 就用它
      （`provider=requested, fallback_to_local=False`）——这正是 MinerU 的价值所在：
      `.ppt`/图片/`.html` 这些本地读不了的格式交给远端；
    - 未命中但本地能读 → 回退本地（`provider='local', fallback_to_local=True`）；
    - 两边都不支持 → `supported=False`（由 `parse_document` 抛 `parser-unsupported`）。

    判定用的是**扩展名**（与参考一致），不是 `detect_file_format` 的 kind——
    因为 MinerU 支持的格式（pptx/png/html…）本地格式识别根本不认，先识别会
    在路由之前就抛 `unsupported-extension`。local 路径仍走格式识别（保持 P2a 校验）。
    """
    path = Path(path)
    requested = provider or "local"
    if requested == "local":
        fmt = detect_file_format(path)
        return ParserDecision("local", "local", fmt.extension,
                              fmt.kind in _LOCAL_SUPPORTED_KINDS, False)

    extension = path.suffix.lower()
    if extension in _MINERU_EXTENSIONS.get(requested, frozenset()):
        return ParserDecision(requested, requested, extension, True, False)

    try:
        fmt = detect_file_format(path)
    except ParsingError:
        # 本地也读不了（如 `.html` 给 agent）：两边都不支持
        return ParserDecision(requested, requested, extension, False, False)
    return ParserDecision("local", requested, fmt.extension,
                          fmt.kind in _LOCAL_SUPPORTED_KINDS, True)


def parse_document(path: Path, *, provider: str = "local", include_images: bool = False,
                   mineru_token: str = "",
                   remote_parser: RemoteParser | None = None) -> ParsedDocument:
    path = Path(path)
    decision = resolve_parser(path, provider)
    if not decision.supported:
        raise ParsingError(
            f"所选解析器（{decision.requested_provider}）不支持 {decision.extension}",
            code="parser-unsupported",
            path=str(path),
        )
    fmt = _format_for(path, decision)
    try:
        if decision.provider == "local":
            markdown = _parse_locally(path, fmt, include_images=include_images)
        else:
            markdown = _parse_remotely(path, decision.provider, mineru_token,
                                       remote_parser)
    except ParsingError:
        raise
    except Exception as error:
        # 裁定 R12：各格式解析器只负责解析，不各自包 ParsingError；归一化在这里
        # 一处收口。否则「合法 zip 但不是有效 xlsx」（如 docx 改名成 .xlsx）会漏出
        # 裸 zipfile.BadZipFile / openpyxl 异常，绕过 API 的错误信封。
        # P2c：MinerU 的 MineruError 同样在这里被包成「文件解析失败：…」
        # ——与参考 `formatImportError`（:133-140）的包装形态一致。
        raise ParsingError(f"文件解析失败：{error}", code="parse-failed", path=str(path)) from error
    label = "local" if decision.fallback_to_local else decision.provider
    return ParsedDocument(markdown=markdown, format_kind=fmt.kind,
                          extension=fmt.extension, parser_label=label)


def _format_for(path: Path, decision: ParserDecision) -> FileFormat:
    """取格式信息；**远端专属格式**（pptx/png/html…）本地识别不了，按扩展名兜底。

    `detect_file_format` 对 `SUPPORTED_EXTENSIONS` 之外的扩展名抛
    `unsupported-extension`——若在远端路径上直接调用，`.pptx` 这类「只有 MinerU
    能读」的文件会在路由成功后、解析之前就被拒（自相矛盾）。故远端路径容错：
    兜底的 `kind` 取扩展名去点（`mime` 置空），仅用于结果记录。
    """
    try:
        return detect_file_format(path)
    except ParsingError:
        if decision.provider == "local":
            raise  # 本地路径必须要有真格式（P2a 校验不变）
        return FileFormat(kind=decision.extension.lstrip(".") or "unknown",
                          extension=decision.extension, mime="")


def _parse_remotely(path: Path, provider: str, token: str,
                    remote_parser: RemoteParser | None) -> str:
    """远端解析（P2c）。`remote_parser` 是测试注入点（假件），生产路径按 provider 造。"""
    if remote_parser is not None:
        return remote_parser.parse_file(path)
    with build_mineru_parser(provider, token=token) as parser:
        return parser.parse_file(path)


def _parse_locally(path: Path, fmt: FileFormat, *, include_images: bool = False) -> str:
    if fmt.kind == "txt":
        return read_text_with_encoding(path) + "\n"
    if fmt.kind == "markdown":
        return read_text_with_encoding(path) + "\n"
    if fmt.kind == "xlsx":
        return spreadsheet_to_markdown(path)
    if fmt.kind == "docx":
        # 裁定 R23：`include_images` 只在 docx 路径有意义——不转发就等于静默丢弃该参数，
        # 调用方会误以为已请求到图片。其余格式解析器的签名刻意收窄，故不加该参数。
        # （**裁定史**：R2 曾在 `docx_to_markdown` 里对 `include_images=True` 显式拒绝，
        # 于是当年「转发」的可观测量是「抛 not-implemented」；P2a↔P2b 接线撤销了该拒绝，
        # 现在转发的结果是 markdown 里出现 `assets/<hash>` 占位。）
        return docx_to_markdown(path, include_images=include_images)
    if fmt.kind == "doc":
        return legacy_doc_to_markdown(path)
    if fmt.kind == "pdf":
        return pdf_to_markdown(path)
    raise ParsingError(f"尚未支持 {fmt.kind}", code="not-implemented", path=str(path))
