from __future__ import annotations

import pytest

from app.infra.db.engine import Database
from app.infra.db.migrate import apply_schema
from app.infra.repositories.duplicate import DuplicateFileInput, DuplicateRepository


@pytest.fixture()
def repo(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    apply_schema(db)
    db.execute("INSERT INTO projects(id, name, created_at) VALUES ('p1','a','now')")
    return DuplicateRepository(db)


def _file(fid, name, *, role_path=None, size=100, modified="2026-01-01T00:00:00Z"):
    return DuplicateFileInput(
        file_id=fid, file_name=name,
        file_path=role_path or f"/tmp/{name}", extension="docx",
        size=size, modified_at=modified,
    )


def test_replace_files_assigns_sort_order_by_input_order(repo):
    repo.replace_files("p1", "bid", [_file("b", "b.docx"), _file("a", "a.docx")])
    rows = repo.list_files("p1", "bid")
    assert [r["file_id"] for r in rows] == ["b", "a"]
    assert [r["sort_order"] for r in rows] == [0, 1]


def test_replace_files_scoped_by_role(repo):
    repo.replace_files("p1", "tender", [_file("t", "t.docx")])
    repo.replace_files("p1", "bid", [_file("b", "b.docx")])
    assert [r["file_id"] for r in repo.list_files("p1", "tender")] == ["t"]
    assert [r["file_id"] for r in repo.list_files("p1", "bid")] == ["b"]


def test_replace_files_replaces_not_appends(repo):
    repo.replace_files("p1", "bid", [_file("a", "a.docx")])
    repo.replace_files("p1", "bid", [_file("b", "b.docx")])
    assert [r["file_id"] for r in repo.list_files("p1", "bid")] == ["b"]


def test_save_section_preserves_omitted_fields(repo):
    repo.save_section("p1", "metadata", status="running", progress=10,
                      message="跑", signature="sig1", stats={"a": 1},
                      started_at="2026-01-01T00:00:00Z",
                      updated_at="2026-01-01T00:00:01Z")
    # 只更新 progress/message，不带 signature/stats/started_at
    repo.save_section("p1", "metadata", status="running", progress=20,
                      message="跑2", updated_at="2026-01-01T00:00:02Z")
    row = repo.get_sections("p1")["metadata"]
    assert row["progress"] == 20
    assert row["message"] == "跑2"
    assert row["signature"] == "sig1"          # 未被清空
    assert row["stats"] == {"a": 1}            # 未被清空
    assert row["started_at"] == "2026-01-01T00:00:00Z"


def test_save_section_can_null_signature_explicitly(repo):
    repo.save_section("p1", "metadata", status="running", progress=1, message="",
                      signature="sig1", stats={}, started_at=None,
                      updated_at="2026-01-01T00:00:00Z")
    repo.save_section("p1", "metadata", status="pending", progress=0, message="",
                      signature=None, updated_at="2026-01-01T00:00:01Z")
    assert repo.get_sections("p1")["metadata"]["signature"] is None


def test_get_sections_starts_empty(repo):
    assert repo.get_sections("p1") == {}


def test_save_meta_preserves_omitted_fields(repo):
    repo.save_meta("p1", step="analysis", active_analysis_tab="content",
                   current_signature="s1")
    repo.save_meta("p1", step="upload")
    meta = repo.get_meta("p1")
    assert meta["step"] == "upload"
    assert meta["active_analysis_tab"] == "content"
    assert meta["current_signature"] == "s1"


def test_next_row_id_does_not_collide_across_replaces(repo):
    """同一项目连续两次 replace_* 写入多行时，每次写入内部 id 不得重号。

    断言的是「单次 replace 内 id 互不相同」（`COUNT(DISTINCT id) == COUNT(*)`）。
    `duplicate_check_metadata_items` 的主键是 `(project_id, id)`，若 `_next_row_id`
    恒返回常量（变异探针把它改成 1），同一次 replace 的第 2 行必然撞主键抛
    IntegrityError，本用例变红。

    不断言「两次 replace 之间 id 不重叠」——replace 是先删后插，删空后
    `MAX(id)` 归零，两次分到同一组 id 是正确行为（Node 侧 AUTOINCREMENT 因
    `sqlite_sequence` 不随 DELETE 回退而恰好不同，属实现细节差异，不作契约）。

    `repo._db` 是测试对私有属性的读取：id 列不出现在任何公开读回接口上
    （`load_analysis` 的 items/files 形状不含 id），只能直查表来验分配。
    """
    repo.replace_files("p1", "bid", [_file("a", "a.docx"), _file("b", "b.docx")])

    repo.replace_metadata_items("p1", [
        {"file_id": "a", "key": "k1", "label": "L1", "value": "v1"},
        {"file_id": "a", "key": "k2", "label": "L2", "value": "v2"},
    ])
    first = repo._db.query_one(
        "SELECT COUNT(*) AS total, COUNT(DISTINCT id) AS distinct_ids"
        " FROM duplicate_check_metadata_items WHERE project_id = 'p1'")
    assert first["total"] == 2
    assert first["distinct_ids"] == first["total"]

    # 第二次 replace（同样 2 行）：先删后插，插入时仍要各自分到不重号的 id
    repo.replace_metadata_items("p1", [
        {"file_id": "b", "key": "k1", "label": "L1", "value": "v3"},
        {"file_id": "b", "key": "k2", "label": "L2", "value": "v4"},
    ])
    second = repo._db.query_one(
        "SELECT COUNT(*) AS total, COUNT(DISTINCT id) AS distinct_ids"
        " FROM duplicate_check_metadata_items WHERE project_id = 'p1'")
    assert second["total"] == 2
    assert second["distinct_ids"] == second["total"]


def test_delete_files_cascades_scoped_rows(repo):
    repo.replace_files("p1", "bid", [_file("b", "b.docx")])
    repo.replace_content_files("p1", [
        {"file_id": "b", "status": "success", "content_path": "/x/b.md",
         "content_length": 5, "parser_label": "local", "error": None,
         "updated_at": "now"}])
    assert repo.load_analysis("p1")["contentFiles"]
    repo.delete_files("p1")
    assert repo.list_files("p1") == []
    assert repo.load_analysis("p1")["contentFiles"] == []
