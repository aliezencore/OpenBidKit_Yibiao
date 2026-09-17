import hashlib
from pathlib import Path

import pytest

from app.domain.shared.errors import ParsingError
from app.infra.parsing.service import SavedDocument, save_uploaded_document
from tests.parsing_fixtures import build_docx

ROLE_TENDER = "tender"
ROLE_BID = "bid"


def _upload(tmp_path, *, name="招标文件.docx", blocks=(("h1", "第一章"), ("p", "正文"))):
    source_dir = tmp_path / "inbox"
    source_dir.mkdir(exist_ok=True)
    return build_docx(source_dir / name, blocks=list(blocks))


def test_returns_saved_document_value_object(tmp_path):
    data_root = tmp_path / "data"
    source = _upload(tmp_path)

    saved = save_uploaded_document(
        data_root=data_root, project_id="p1", source_path=source, role=ROLE_TENDER
    )

    assert isinstance(saved, SavedDocument)
    assert saved.project_id == "p1"
    assert saved.role == ROLE_TENDER
    assert saved.source == "upload"
    assert saved.file_name == "招标文件.docx"
    assert saved.parser_label == "local"
    assert saved.format_kind == "docx"
    assert saved.extension == ".docx"
    assert saved.document_id, "document_id 不能为空"


def test_writes_markdown_under_project_scoped_path(tmp_path):
    data_root = tmp_path / "data"
    source = _upload(tmp_path)

    saved = save_uploaded_document(
        data_root=data_root, project_id="p1", source_path=source, role=ROLE_TENDER
    )

    expected = data_root / "projects" / "p1" / "documents" / f"{saved.document_id}.md"
    assert Path(saved.markdown_path) == expected
    assert expected.exists()
    assert "第一章" in expected.read_text(encoding="utf-8")


def test_content_hash_and_chars_describe_the_markdown(tmp_path):
    data_root = tmp_path / "data"
    source = _upload(tmp_path)

    saved = save_uploaded_document(
        data_root=data_root, project_id="p1", source_path=source, role=ROLE_TENDER
    )

    markdown = Path(saved.markdown_path).read_text(encoding="utf-8")
    assert saved.content_hash == hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    assert saved.content_chars == len(markdown)


def test_content_hash_is_recomputable_from_file_bytes(tmp_path):
    """裁定 R25：产物哈希必须能从产物自身的**字节**复算，且在任意平台都成立。

    必须用 `read_bytes()` 取哈希，不能用 `read_text()`：`read_text` 的通用换行会把
    CRLF 翻回 LF，从而把 Windows 上 `write_text` 的换行翻译问题掩盖掉——那正是
    查重（P3）直接对 `.md` 取字节哈希时会踩的坑。
    """
    data_root = tmp_path / "data"
    source = _upload(tmp_path)

    saved = save_uploaded_document(
        data_root=data_root, project_id="p1", source_path=source, role=ROLE_TENDER
    )

    raw = Path(saved.markdown_path).read_bytes()
    hash_from_bytes = hashlib.sha256(raw).hexdigest()
    assert hash_from_bytes == saved.content_hash, (
        f"文件字节哈希 {hash_from_bytes} != 记录的 content_hash {saved.content_hash}"
    )


def test_copies_the_original_upload(tmp_path):
    data_root = tmp_path / "data"
    source = _upload(tmp_path)

    saved = save_uploaded_document(
        data_root=data_root, project_id="p1", source_path=source, role=ROLE_TENDER
    )

    copied = Path(saved.source_path)
    assert copied.exists()
    assert copied.parent == data_root / "projects" / "p1" / "uploads"
    assert copied.name == f"{saved.document_id}.docx"
    assert copied.read_bytes() == source.read_bytes()


def test_two_uploads_of_the_same_file_get_distinct_ids(tmp_path):
    data_root = tmp_path / "data"
    source = _upload(tmp_path)

    first = save_uploaded_document(
        data_root=data_root, project_id="p1", source_path=source, role=ROLE_TENDER
    )
    second = save_uploaded_document(
        data_root=data_root, project_id="p1", source_path=source, role=ROLE_BID
    )

    assert first.document_id != second.document_id
    assert Path(first.markdown_path).exists()
    assert Path(second.markdown_path).exists()
    # 内容相同 → 哈希相同：哈希是本体的指纹，不是槽位编号
    assert first.content_hash == second.content_hash
    assert second.role == ROLE_BID


def test_projects_are_isolated(tmp_path):
    data_root = tmp_path / "data"
    source = _upload(tmp_path)

    a = save_uploaded_document(
        data_root=data_root, project_id="p1", source_path=source, role=ROLE_TENDER
    )
    b = save_uploaded_document(
        data_root=data_root, project_id="p2", source_path=source, role=ROLE_TENDER
    )

    assert Path(a.markdown_path).parent == data_root / "projects" / "p1" / "documents"
    assert Path(b.markdown_path).parent == data_root / "projects" / "p2" / "documents"


def test_parse_failure_leaves_no_artifacts(tmp_path):
    """裁定 R24：落盘前必须先把解析做完。解析失败时不得留下半成品目录或文件。"""
    data_root = tmp_path / "data"
    bad = tmp_path / "broken.xlsx"
    bad.write_bytes(b"PK\x03\x04" + b"\x00" * 32)  # zip 魔数正确、内容不是 xlsx

    with pytest.raises(ParsingError) as info:
        save_uploaded_document(
            data_root=data_root, project_id="p1", source_path=bad, role=ROLE_TENDER
        )

    assert info.value.code == "parse-failed"
    documents_dir = data_root / "projects" / "p1" / "documents"
    assert not documents_dir.exists() or list(documents_dir.iterdir()) == []
    uploads_dir = data_root / "projects" / "p1" / "uploads"
    assert not uploads_dir.exists() or list(uploads_dir.iterdir()) == []


def test_parse_failure_does_not_even_create_project_dirs(tmp_path):
    """对 R24 的严格钉子。

    上面那条 `not exists() or iterdir() == []` 是**弱断言**：先建目录再解析的实现
    失败时留下的是**空目录**，`iterdir() == []` 照样成立，用例会静默通过。这里把
    「目录根本不存在」直接钉死，才真正锁住「先解析、后落盘」的顺序。
    """
    data_root = tmp_path / "data"
    bad = tmp_path / "broken.xlsx"
    bad.write_bytes(b"PK\x03\x04" + b"\x00" * 32)

    with pytest.raises(ParsingError):
        save_uploaded_document(
            data_root=data_root, project_id="p1", source_path=bad, role=ROLE_TENDER
        )

    assert not (data_root / "projects" / "p1" / "documents").exists()
    assert not (data_root / "projects" / "p1" / "uploads").exists()
    assert not (data_root / "projects" / "p1").exists()


def test_missing_source_raises_and_leaves_no_artifacts(tmp_path):
    data_root = tmp_path / "data"

    with pytest.raises(Exception):
        save_uploaded_document(
            data_root=data_root,
            project_id="p1",
            source_path=tmp_path / "nope.docx",
            role=ROLE_TENDER,
        )

    documents_dir = data_root / "projects" / "p1" / "documents"
    assert not documents_dir.exists() or list(documents_dir.iterdir()) == []
