import pytest

from app.infra.parsing.format import detect_file_format
from app.infra.parsing.router import ParsedDocument, parse_document, resolve_parser
from tests.parsing_fixtures import build_docx


def test_local_provider_supports_docx(tmp_path):
    p = build_docx(tmp_path / "a.docx", blocks=[("p", "正文")])
    decision = resolve_parser(p, provider="local")
    assert (decision.provider, decision.supported, decision.fallback_to_local) == ("local", True, False)


def test_mineru_falls_back_to_local_for_docx(tmp_path):
    p = build_docx(tmp_path / "a.docx", blocks=[("p", "正文")])
    decision = resolve_parser(p, provider="mineru-agent-api")
    assert decision.provider == "local"
    assert decision.fallback_to_local is True


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

    转发后由 docx 侧的 R2 守卫拒绝，调用方拿到 not-implemented；
    若参数被丢弃，这里会「成功返回 markdown」而不是抛错——该用例即失败。
    """
    from app.domain.shared.errors import ParsingError

    p = build_docx(tmp_path / "a.docx", blocks=[("p", "正文")])

    with pytest.raises(ParsingError) as info:
        parse_document(p, provider="local", include_images=True)

    assert info.value.code == "not-implemented"


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
