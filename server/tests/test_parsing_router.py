import pytest

from app.infra.parsing.format import detect_file_format
from app.infra.parsing.mineru import PARSER_LABELS
from app.infra.parsing.mineru_common import MineruError
from app.infra.parsing.router import ParsedDocument, parse_document, resolve_parser
from tests.parsing_fixtures import build_docx


def test_local_provider_supports_docx(tmp_path):
    p = build_docx(tmp_path / "a.docx", blocks=[("p", "正文")])
    decision = resolve_parser(p, provider="local")
    assert (decision.provider, decision.supported, decision.fallback_to_local) == ("local", True, False)


def test_mineru_agent_supports_docx_no_fallback(tmp_path):
    """P2c 恢复参考语义：`.docx` 在 agent 的支持表里 → **就用远端**，不回退。

    （P2a 曾阶段性收窄为「MinerU 一律回退本地」，见计划
    `2026-09-23-...-p2c-mineru.md` 的 R-P2c-2。）
    """
    p = build_docx(tmp_path / "a.docx", blocks=[("p", "正文")])
    decision = resolve_parser(p, provider="mineru-agent-api")
    assert (decision.provider, decision.supported, decision.fallback_to_local) == (
        "mineru-agent-api", True, False)


def test_mineru_agent_supports_pdf_no_fallback(tmp_path):
    """`.pdf` 同属 MinerU 支持表（fileService.cjs:17-19），路由到远端。"""
    p = tmp_path / "a.pdf"
    p.write_bytes(b"%PDF-1.4\n%fake\n")
    decision = resolve_parser(p, provider="mineru-agent-api")
    assert (decision.provider, decision.supported, decision.fallback_to_local) == (
        "mineru-agent-api", True, False)


def test_mineru_falls_back_to_local_for_txt(tmp_path):
    """非 agent/accurate 支持表的扩展名但本地能读（`.txt`）→ 回退本地。"""
    p = tmp_path / "a.txt"
    p.write_text("正文内容", encoding="utf-8")
    decision = resolve_parser(p, provider="mineru-agent-api")
    assert (decision.provider, decision.supported, decision.fallback_to_local) == (
        "local", True, True)
    result = parse_document(p, provider="mineru-agent-api")
    assert result.parser_label == "local"
    assert "正文内容" in result.markdown


def test_accurate_supports_html_but_agent_does_not(tmp_path):
    """`.html` 只在 accurate 表里（fileService.cjs:20-22）。"""
    p = tmp_path / "a.html"
    p.write_text("<html></html>", encoding="utf-8")
    accurate = resolve_parser(p, provider="mineru-accurate-api")
    assert (accurate.provider, accurate.supported, accurate.fallback_to_local) == (
        "mineru-accurate-api", True, False)

    agent = resolve_parser(p, provider="mineru-agent-api")
    assert (agent.provider, agent.supported) == ("mineru-agent-api", False)


def test_unsupported_by_both_raises_parser_unsupported(tmp_path):
    from app.domain.shared.errors import ParsingError

    p = tmp_path / "a.html"
    p.write_text("<html></html>", encoding="utf-8")
    with pytest.raises(ParsingError) as info:
        parse_document(p, provider="mineru-agent-api")
    assert info.value.code == "parser-unsupported"


class _FakeRemote:
    """假远端解析器（P2c 注入点）：记录调用并回固定 markdown。"""

    def __init__(self, markdown: str = "# 远端正文\n", error: Exception | None = None):
        self.markdown = markdown
        self.error = error
        self.calls: list[str] = []
        self.closed = False

    def parse_file(self, path):
        self.calls.append(str(path))
        if self.error is not None:
            raise self.error
        return self.markdown

    def close(self) -> None:
        self.closed = True


def test_routes_to_remote_parser_when_provider_supports(tmp_path):
    """请求 MinerU 且格式受支持 → 走远端（本地解析器不参与）。"""
    p = build_docx(tmp_path / "a.docx", blocks=[("p", "本地正文")])
    fake = _FakeRemote()
    result = parse_document(p, provider="mineru-agent-api", remote_parser=fake)
    assert result.markdown == "# 远端正文\n"
    assert result.parser_label == "mineru-agent-api"
    assert result.format_kind == "docx"
    assert fake.calls == [str(p)]


def test_fallback_to_local_does_not_touch_remote_parser(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("本地内容", encoding="utf-8")
    fake = _FakeRemote()
    result = parse_document(p, provider="mineru-agent-api", remote_parser=fake)
    assert fake.calls == []
    assert result.parser_label == "local"
    assert "本地内容" in result.markdown


def test_remote_failure_is_wrapped_like_reference(tmp_path):
    """远端失败包成 `parse-failed`，文案前缀与参考 formatImportError 一致。"""
    from app.domain.shared.errors import ParsingError

    p = build_docx(tmp_path / "a.docx", blocks=[("p", "正文")])
    fake = _FakeRemote(error=MineruError("申请 MinerU-Agent 上传链接失败：HTTP 400，{}"))
    with pytest.raises(ParsingError) as info:
        parse_document(p, provider="mineru-agent-api", remote_parser=fake)
    assert info.value.code == "parse-failed"
    assert str(info.value).startswith(
        "文件解析失败：申请 MinerU-Agent 上传链接失败：HTTP 400，")


def test_remote_only_format_gets_extension_kind(tmp_path):
    """本地识别不了的远端专属格式（`.pptx`）也能走通，kind 兜底为扩展名去点。"""
    p = tmp_path / "a.pptx"
    p.write_bytes(b"PK\x03\x04fake")
    fake = _FakeRemote("# 幻灯片\n")
    result = parse_document(p, provider="mineru-agent-api", remote_parser=fake)
    assert result.format_kind == "pptx"
    assert result.extension == ".pptx"
    assert result.markdown == "# 幻灯片\n"


def test_parser_labels_are_reference_verbatim():
    assert PARSER_LABELS == {
        "local": "本地解析",
        "mineru-accurate-api": "MinerU 精准解析 API",
        "mineru-agent-api": "MinerU-Agent 轻量解析 API",
    }


def test_unsupported_extension_raises(tmp_path):
    from app.domain.shared.errors import ParsingError

    p = tmp_path / "a.exe"
    p.write_bytes(b"MZ")
    # 裁定 R22：用 pytest.raises(Exception) 不能钉住「必须是 ParsingError」——
    # 任何异常（连 NameError）都能让它变绿。断言到具体类型与码。
    with pytest.raises(ParsingError) as info:
        resolve_parser(p, provider="local")
    assert info.value.code == "unsupported-extension"


def test_parse_document_routes_docx(tmp_path):
    p = build_docx(tmp_path / "a.docx", blocks=[("h1", "标题"), ("p", "正文")])

    result = parse_document(p, provider="local")

    assert isinstance(result, ParsedDocument)
    assert result.format_kind == "docx"
    assert result.parser_label == "local"
    assert "标题" in result.markdown


# 注（裁定 R22）：`supported=False` 这条分支**当前不可达**——`detect_file_format`
# 只返回 docx/doc/txt/markdown/pdf/xlsx 六种 kind，而它们恰好构成
# `_LOCAL_SUPPORTED_KINDS` 的全部；于是 provider == "local" 时 supported 必为真，
# provider != "local" 时必走「回退本地」，结果为真。故 `parse_document` 里
# `if not decision.supported: raise ParsingError(code="parser-unsupported")`
# 是**防御性死分支**，保留作为 `ParserDecision` 不变式的守卫，但**不写用例**
# （写了必然失败）。若 P2c 引入一个「本地不支持、它才支持」的格式，该分支自然可达。


def test_parse_document_routes_txt(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("纯文本内容", encoding="utf-8")

    result = parse_document(p, provider="local")

    assert result.markdown.strip() == "纯文本内容"
    assert result.format_kind == "txt"


def test_parse_document_detects_format(tmp_path):
    p = build_docx(tmp_path / "a.docx", blocks=[("p", "x")])
    assert parse_document(p, provider="local").format_kind == detect_file_format(p).kind


def test_parse_document_forwards_include_images_to_docx(tmp_path):
    """裁定 R23：include_images 必须转发到 docx 路径，而不是被静默丢弃。

    本用例原先靠「docx 侧的 R2 守卫抛 not-implemented」来**间接**观测转发；
    R2 已随 P2a↔P2b 接线撤销，故改为直接观测**转发结果**：同一个带图 docx，
    转发 `True` 时 markdown 有 `assets/<hash>` 占位、不转发时没有。
    若参数被丢弃，两者会相同 → 该用例失败。判别力不依赖「抛错」这个副作用。
    """
    import re

    from tests.parsing_fixtures import build_docx_with_images, make_image

    image = make_image(tmp_path / "a.png", size=(40, 30), color=(200, 30, 30))
    p = build_docx_with_images(tmp_path / "a.docx", blocks=[("p", "正文。")], images=[image])

    forwarded = parse_document(p, provider="local", include_images=True)
    dropped = parse_document(p, provider="local")

    assert re.search(r"!\[[^\]]*\]\(assets/[0-9a-f]{64}\)", forwarded.markdown), \
        forwarded.markdown
    assert "assets/" not in dropped.markdown


def test_parser_exception_is_normalised_to_parsing_error(tmp_path):
    """裁定 R12：合法 zip 但不是有效 xlsx（如 docx 改名成 .xlsx）时，
    解析器内部的异常必须被归一为 ParsingError，而不是漏给调用方。"""
    import zipfile

    from app.domain.shared.errors import ParsingError

    p = tmp_path / "fake.xlsx"
    with zipfile.ZipFile(p, "w") as archive:
        archive.writestr("not-a-sheet.txt", "x")

    with pytest.raises(ParsingError) as info:
        parse_document(p, provider="local")

    assert info.value.code == "parse-failed"
