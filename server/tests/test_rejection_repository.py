"""P4a T2a：废标项检查仓储（meta / documents / extraction）。

真值来源 `client/electron/services/rejectionCheckStore.cjs`：
`ensureMetaRow` :191-204、`updateMeta` :205-215、`loadDocumentRow` :251-264、
`savePreparedDocument` :305-326、`saveExtraction` :552-575、`loadExtraction` :577-589。
"""

from __future__ import annotations

import json

import pytest

from app.domain.rejection.normalize import DEFAULT_CHECK_OPTIONS
from app.infra.db.engine import Database
from app.infra.db.migrate import apply_schema
from app.infra.repositories.projects import ProjectRepository
from app.infra.repositories.rejection import RejectionRepository


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "rejection.sqlite")
    apply_schema(db)
    projects = ProjectRepository(db)
    project = projects.create("废标项项目")
    other = projects.create("另一个项目")
    yield db, RejectionRepository(db), project, other
    db.close()


def _document_values(**overrides):
    values = {
        "document_id": "bid-abc",
        "role": "bid",
        "source": "upload",
        "file_name": "投标文件.docx",
        "markdown_path": "rejection-check/bids/bid-abc-1-deadbeef.md",
        "content_hash": "h1",
        "content_chars": 10,
        "parser_label": "local",
        "sort_order": 0,
        "imported_at": "2026-09-23T00:00:00+00:00",
        "updated_at": "2026-09-23T00:00:00+00:00",
    }
    values.update(overrides)
    return values


# ------------------------------------------------------------------------ meta

def test_ensure_meta_creates_reference_defaults(env):
    _, repo, project, _ = env
    meta = repo.ensure_meta(project)
    assert meta["step"] == "documents"
    assert meta["active_document_tab"] == "tender"
    assert meta["active_result_tab"] == "analysis"
    assert meta["active_check_result_tab"] == "rejection"
    assert meta["custom_check_items"] == ""
    assert json.loads(meta["check_options_json"]) == DEFAULT_CHECK_OPTIONS


def test_ensure_meta_is_idempotent_and_project_scoped(env):
    _, repo, project, other = env
    first = repo.ensure_meta(project)
    again = repo.ensure_meta(project)
    assert first["created_at"] == again["created_at"]
    assert repo.get_meta(other) is None
    repo.ensure_meta(other)
    assert repo.get_meta(other) is not None


def test_update_meta_writes_only_provided_keys(env):
    _, repo, project, _ = env
    repo.ensure_meta(project)
    before = repo.get_meta(project)
    repo.update_meta(project, {"step": "items", "active_result_tab": "custom"})
    after = repo.get_meta(project)
    assert (after["step"], after["active_result_tab"]) == ("items", "custom")
    # 未提供的键保持原值
    assert after["active_document_tab"] == before["active_document_tab"]
    assert after["custom_check_items"] == before["custom_check_items"]
    assert after["updated_at"] >= before["updated_at"]


def test_update_meta_ignores_unknown_keys(env):
    """列名白名单：未知键不拼进 SQL（也防注入）。"""
    _, repo, project, _ = env
    repo.ensure_meta(project)
    repo.update_meta(project, {"step": "results", "evil = 1; --": "x"})
    assert repo.get_meta(project)["step"] == "results"


def test_update_meta_without_known_keys_is_noop(env):
    _, repo, project, _ = env
    repo.ensure_meta(project)
    repo.update_meta(project, {"unknown": "x"})
    assert repo.get_meta(project)["step"] == "documents"


# ---------------------------------------------------------------- documents

def test_save_document_upserts_on_same_id(env):
    _, repo, project, _ = env
    repo.save_document(project, _document_values())
    repo.save_document(project, _document_values(
        content_hash="h2", content_chars=99,
        markdown_path="rejection-check/bids/bid-abc-2-cafebabe.md"))
    rows = repo.list_documents(project)
    assert len(rows) == 1
    assert (rows[0]["content_hash"], rows[0]["content_chars"]) == ("h2", 99)
    assert rows[0]["markdown_path"].endswith("cafebabe.md")


def test_save_document_requires_all_columns(env):
    _, repo, project, _ = env
    values = _document_values()
    values.pop("content_hash")
    with pytest.raises(ValueError, match="content_hash"):
        repo.save_document(project, values)


def test_documents_are_project_scoped_and_ordered(env):
    _, repo, project, other = env
    repo.save_document(project, _document_values(document_id="bid-b", sort_order=1))
    repo.save_document(project, _document_values(document_id="bid-a", sort_order=0))
    repo.save_document(other, _document_values(document_id="bid-a"))
    assert [row["document_id"] for row in repo.list_documents(project)] == ["bid-a", "bid-b"]
    assert len(repo.list_documents(other)) == 1


def test_get_document_three_addressing_modes(env):
    _, repo, project, _ = env
    repo.save_document(project, _document_values(
        document_id="tender-01-x", role="tender", sort_order=0))
    repo.save_document(project, _document_values(document_id="bid-b", sort_order=1))
    repo.save_document(project, _document_values(document_id="bid-a", sort_order=0))
    # ① 给 (document_id, role)
    assert repo.get_document(project, "bid", "bid-a")["document_id"] == "bid-a"
    # ② 只给 role → 该角色 sort_order 最小的一份
    assert repo.get_document(project, "bid")["document_id"] == "bid-a"
    assert repo.get_document(project, "tender")["document_id"] == "tender-01-x"
    # ③ 给 document_id → 全局找
    assert repo.get_document(project, "bid-b")["document_id"] == "bid-b"
    assert repo.get_document(project, "不存在") is None


def test_clear_documents_returns_markdown_paths(env):
    _, repo, project, _ = env
    repo.save_document(project, _document_values(document_id="bid-a"))
    repo.save_document(project, _document_values(
        document_id="tender-01-x", role="tender",
        markdown_path="rejection-check/tenders/tender-01-x-1-aa.md"))
    paths = repo.clear_documents(project, role="bid")
    assert paths == ["rejection-check/bids/bid-abc-1-deadbeef.md"]
    assert [row["document_id"] for row in repo.list_documents(project)] == ["tender-01-x"]
    # 全清
    assert repo.clear_documents(project) == ["rejection-check/tenders/tender-01-x-1-aa.md"]
    assert repo.list_documents(project) == []
    # 空表再清不抛
    assert repo.clear_documents(project) == []


# --------------------------------------------------------------- extraction

def test_extraction_default_when_row_absent(env):
    _, repo, project, _ = env
    assert repo.load_extraction(project) == {"status": "idle", "content": ""}


def test_extraction_upsert_normalizes_status_and_strips_triple_quotes(env):
    _, repo, project, _ = env
    repo.save_extraction(project, {
        "status": "success", "content": "'''解析内容'''",
        "tenderSignature": "sig-1", "source": "tender", "updatedAt": "2026-09-23T01:00:00Z"})
    loaded = repo.load_extraction(project)
    assert loaded == {
        "status": "success", "content": "解析内容",
        "source": "tender", "tenderSignature": "sig-1",
        "updatedAt": "2026-09-23T01:00:00Z"}
    # 非法 status → idle（参考 :565 的 normalizeStatus）
    repo.save_extraction(project, {"status": "爆炸", "content": "x"})
    assert repo.load_extraction(project)["status"] == "idle"


def test_extraction_save_none_deletes_row(env):
    _, repo, project, _ = env
    repo.save_extraction(project, {"status": "success", "content": "x"})
    repo.save_extraction(project, None)
    assert repo.load_extraction(project) == {"status": "idle", "content": ""}


def test_extraction_is_project_scoped(env):
    _, repo, project, other = env
    repo.save_extraction(project, {"status": "success", "content": "甲"})
    repo.save_extraction(other, {"status": "success", "content": "乙"})
    assert repo.load_extraction(project)["content"] == "甲"
    assert repo.load_extraction(other)["content"] == "乙"
