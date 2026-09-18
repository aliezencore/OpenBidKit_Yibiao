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


# ------------------------------------------------------------------ 修复环 1


FOUR_SECTIONS = ("metadata", "outline", "content", "image")


def _seed_all_sections(repo, project_id="p1"):
    """四段各写一条带 stats 的状态行（F1 串联用例的前半段）。"""
    for index, section in enumerate(FOUR_SECTIONS):
        repo.save_section(
            project_id, section, status="success", progress=100,
            message=f"{section} 完成",
            signature=f"sig-{section}",
            stats={"section": section, "n": index},
            started_at="2026-01-01T00:00:00Z",
            updated_at=f"2026-01-01T00:00:0{index}Z")


def test_load_analysis_after_save_section_for_all_four_sections(repo):
    """F1 串联用例：`save_section` 写四段 → `load_analysis` 四个 `*Analysis` 非 None。

    修复前 `get_sections` 把 `stats_json` pop 成 `stats`，而 `_section_common`
    仍读 `row["stats_json"]` → 每个 `_load_*_analysis` 都 `KeyError`。
    """
    repo.replace_files("p1", "bid", [_file("b", "b.docx")])
    _seed_all_sections(repo)

    analysis = repo.load_analysis("p1")
    for key in ("metadataAnalysis", "outlineAnalysis", "contentAnalysis", "imageAnalysis"):
        assert analysis[key] is not None, f"{key} 应当存在"
    assert analysis["metadataAnalysis"]["signature"] == "sig-metadata"
    assert analysis["outlineAnalysis"]["signature"] == "sig-outline"
    assert analysis["contentAnalysis"]["signature"] == "sig-content"
    assert analysis["imageAnalysis"]["signature"] == "sig-image"
    assert analysis["metadataAnalysis"]["message"] == "metadata 完成"
    assert analysis["imageAnalysis"]["progress"] == 100


def test_load_analysis_reads_stats_contents_per_section(repo):
    """F1 续：stats 内容必须逐段落到对应分析键上，而不是被清空成默认值。"""
    repo.replace_files("p1", "bid", [_file("b", "b.docx")])
    repo.save_section("p1", "metadata", status="success", progress=100, message="m",
                      stats={"contentExtraction": {"status": "success", "completed": 2,
                                                   "total": 2},
                             "metadataExtraction": {"status": "success", "completed": 1,
                                                    "total": 1},
                             "logs": ["抽取完成"]},
                      updated_at="2026-01-01T00:00:00Z")
    repo.save_section("p1", "content", status="success", progress=100, message="c",
                      stats={"tenderSentenceCount": 7, "tenderMatchedSentenceCount": 3,
                             "totalSentenceCount": 42},
                      updated_at="2026-01-01T00:00:01Z")
    repo.save_section("p1", "image", status="success", progress=100, message="i",
                      stats={"totalImageCount": 5}, updated_at="2026-01-01T00:00:02Z")

    analysis = repo.load_analysis("p1")
    metadata = analysis["metadataAnalysis"]
    assert metadata["contentExtraction"] == {"status": "success", "completed": 2, "total": 2}
    assert metadata["metadataExtraction"] == {"status": "success", "completed": 1, "total": 1}
    assert metadata["logs"] == ["抽取完成"]
    content = analysis["contentAnalysis"]
    assert content["tenderSentenceCount"] == 7
    assert content["tenderMatchedSentenceCount"] == 3
    assert content["totalSentenceCount"] == 42
    assert analysis["imageAnalysis"]["totalImageCount"] == 5


def test_load_analysis_is_scoped_to_project(repo):
    """F2 双项目隔离：p1/p2 各写不同结果行，各自读回只含本项目数据。

    修复前读路径只按 `file_id`/`sort_order` 排序、无 `project_id` 过滤，
    `load_analysis("p1")` 会读到 `p2` 的行。
    """
    repo._db.execute("INSERT INTO projects(id, name, created_at) VALUES ('p2','b','now')")

    repo.replace_files("p1", "bid", [_file("p1-bid", "p1b.docx")])
    repo.replace_files("p2", "bid", [_file("p2-bid", "p2b.docx")])
    _seed_all_sections(repo, "p1")
    _seed_all_sections(repo, "p2")

    repo.replace_content_files("p1", [
        {"file_id": "p1-bid", "status": "success", "content_path": "/p1.md",
         "content_length": 1, "parser_label": "p1-parser", "error": None,
         "updated_at": "now"}])
    repo.replace_content_files("p2", [
        {"file_id": "p2-bid", "status": "error", "content_path": "/p2.md",
         "content_length": 2, "parser_label": "p2-parser", "error": "boom",
         "updated_at": "now"}])

    repo.replace_metadata_items("p1", [
        {"file_id": "p1-bid", "key": "k1", "label": "L1", "value": "v1"}])
    repo.replace_metadata_items("p2", [
        {"file_id": "p2-bid", "key": "k2", "label": "L2", "value": "v2"}])

    analysis_p1 = repo.load_analysis("p1")
    analysis_p2 = repo.load_analysis("p2")

    # 顶层 contentFiles（brief 测试依赖的键）必须同样隔离
    assert [f["file_id"] for f in analysis_p1["contentFiles"]] == ["p1-bid"]
    assert [f["file_id"] for f in analysis_p2["contentFiles"]] == ["p2-bid"]

    assert [f["id"] for f in analysis_p1["bidFiles"]] == ["p1-bid"]
    assert [f["id"] for f in analysis_p2["bidFiles"]] == ["p2-bid"]

    # 元数据行只能进本项目的 rows/values
    assert analysis_p1["metadataAnalysis"] is not None
    assert analysis_p2["metadataAnalysis"] is not None
    rows_p1 = {row["key"]: row for row in analysis_p1["metadataAnalysis"]["rows"]}
    rows_p2 = {row["key"]: row for row in analysis_p2["metadataAnalysis"]["rows"]}
    assert set(rows_p1) == {"k1"}
    assert set(rows_p2) == {"k2"}
    assert rows_p1["k1"]["values"] == {"p1-bid": "v1"}
    assert rows_p2["k2"]["values"] == {"p2-bid": "v2"}
    assert analysis_p1["metadataAnalysis"]["files"][0]["metadata"][0]["key"] == "k1"
    assert analysis_p1["metadataAnalysis"]["files"][0]["metadata"][0]["value"] == "v1"
    assert analysis_p2["metadataAnalysis"]["files"][0]["metadata"][0]["key"] == "k2"

    # contentAnalysis 的 occurrences 也按项目聚合
    assert analysis_p1["contentAnalysis"]["duplicateSentences"] == []
    assert analysis_p2["contentAnalysis"]["duplicateSentences"] == []
    repo.replace_content_duplicates(
        "p2", [{"duplicate_id": "d-p2", "sentence": "p2 句", "normalized": "p2 句"}],
        [{"duplicate_id": "d-p2", "file_id": "p2-bid", "occurrence_count": 3}])
    content_p1 = repo.load_analysis("p1")["contentAnalysis"]
    content_p2 = repo.load_analysis("p2")["contentAnalysis"]
    assert content_p1["duplicateSentences"] == []
    assert content_p2["duplicateSentences"][0]["id"] == "d-p2"
    assert content_p2["duplicateSentences"][0]["occurrences"] == {"p2-bid": 3}


def test_image_analysis_is_scoped_to_project(repo):
    """F2 细节：`image_files` / `image_occurrences` / `duplicate_images` 三条读路径
    都要按项目过滤。"""
    repo._db.execute("INSERT INTO projects(id, name, created_at) VALUES ('p2','b','now')")
    repo.replace_files("p1", "bid", [_file("p1-bid", "p1b.docx")])
    repo.replace_files("p2", "bid", [_file("p2-bid", "p2b.docx")])
    _seed_all_sections(repo, "p1")
    _seed_all_sections(repo, "p2")
    repo.replace_image_files("p2", [
        {"file_id": "p2-bid", "status": "success", "image_count": 4,
         "unique_image_count": 2, "error": None, "updated_at": "now"}])
    repo.replace_duplicate_images(
        "p2", [{"image_id": "img-p2", "hash": "h-p2", "preview_url": "u-p2"}],
        [{"image_id": "img-p2", "file_id": "p2-bid", "occurrence_count": 2}])

    image_p1 = repo.load_analysis("p1")["imageAnalysis"]
    image_p2 = repo.load_analysis("p2")["imageAnalysis"]

    assert image_p1["files"] == []
    assert image_p1["duplicateImages"] == []
    assert image_p1["totalImageCount"] == 0

    assert [file["image_count"] for file in image_p2["files"]] == [4]
    assert [image["id"] for image in image_p2["duplicateImages"]] == ["img-p2"]
    assert image_p2["duplicateImages"][0]["occurrences"] == {"p2-bid": 2}


def test_outline_pairwise_is_scoped_to_project(repo):
    """F2 细节：outline 的 items/groups/pairwise 三条读路径都要按项目过滤。

    两边都写 outline 段状态，`p1` 的派生行留空；若读路径缺 `project_id` 过滤，
    `load_analysis("p1")` 会读到 `p2` 的 items/groups/pairwise。
    """
    repo._db.execute("INSERT INTO projects(id, name, created_at) VALUES ('p2','b','now')")
    repo.replace_files("p1", "bid", [_file("p1-bid", "p1b.docx")])
    repo.replace_files("p2", "bid", [_file("p2-bid", "p2b.docx")])
    _seed_all_sections(repo, "p1")
    _seed_all_sections(repo, "p2")
    repo.replace_outline_items("p2", [
        {"item_id": "i1", "file_id": "p2-bid", "title": "P2 标题"}])
    repo.replace_outline_groups("p2", [
        {"group_id": "g1", "type": "duplicate", "title": "P2 组",
         "item_ids": {"p2-bid": ["i1"]}}])
    repo.replace_outline_pairwise("p2", [
        {"file_a_id": "p2-bid", "file_b_id": "p2-bid", "score": 0.9}])

    outline_p1 = repo.load_analysis("p1")["outlineAnalysis"]
    outline_p2 = repo.load_analysis("p2")["outlineAnalysis"]

    assert outline_p1["pairwiseSimilarities"] == []
    assert outline_p1["duplicateGroups"] == []
    assert all(not file["items"] for file in outline_p1["files"])

    assert len(outline_p2["pairwiseSimilarities"]) == 1
    assert [g["id"] for g in outline_p2["duplicateGroups"]] == ["g1"]
    items_p2 = [item for file in outline_p2["files"] for item in file["items"]]
    assert [item["id"] for item in items_p2] == ["i1"]
    assert items_p2[0]["duplicate_group_ids"] == ["g1"]


def test_delete_sections_cascades_detail_tables(repo):
    """F4：`delete_sections` 要按项目一并清掉四段的 detail 表（对齐参考
    `clearSection`，:386-407），不能只删 `duplicate_check_analysis_sections`。"""
    repo.replace_files("p1", "bid", [_file("b", "b.docx")])
    _seed_all_sections(repo)
    repo.replace_content_files("p1", [
        {"file_id": "b", "status": "success", "content_path": "/x/b.md",
         "content_length": 5, "parser_label": "local", "error": None,
         "updated_at": "now"}])
    repo.replace_metadata_items("p1", [
        {"file_id": "b", "key": "k1", "label": "L1", "value": "v1"}])
    repo.replace_outline_items("p1", [
        {"item_id": "i1", "file_id": "b", "title": "标题"}])
    repo.replace_outline_groups("p1", [
        {"group_id": "g1", "type": "duplicate", "title": "组"}])
    repo.replace_outline_pairwise("p1", [
        {"file_a_id": "b", "file_b_id": "b", "score": 0.5}])
    repo.replace_content_duplicates(
        "p1", [{"duplicate_id": "d1", "sentence": "s", "normalized": "s"}],
        [{"duplicate_id": "d1", "file_id": "b", "occurrence_count": 2}])
    repo.replace_image_files("p1", [
        {"file_id": "b", "status": "success", "image_count": 1,
         "unique_image_count": 1, "error": None, "updated_at": "now"}])
    repo.replace_duplicate_images(
        "p1", [{"image_id": "img1", "hash": "h", "preview_url": "u"}],
        [{"image_id": "img1", "file_id": "b", "occurrence_count": 1}])

    detail_tables = (
        "duplicate_check_content_files",
        "duplicate_check_metadata_items",
        "duplicate_check_outline_items",
        "duplicate_check_outline_groups",
        "duplicate_check_outline_pairwise",
        "duplicate_check_content_duplicates",
        "duplicate_check_content_occurrences",
        "duplicate_check_image_files",
        "duplicate_check_duplicate_images",
        "duplicate_check_image_occurrences",
    )
    before = {table: repo._db.query_one(
        f"SELECT COUNT(*) AS n FROM {table} WHERE project_id = 'p1'")["n"]
        for table in detail_tables}
    assert all(count > 0 for count in before.values()), f"前置数据未写全：{before}"

    repo.delete_sections("p1")

    assert repo.get_sections("p1") == {}
    for table in detail_tables:
        remaining = repo._db.query_one(
            f"SELECT COUNT(*) AS n FROM {table} WHERE project_id = 'p1'")["n"]
        assert remaining == 0, f"{table} 未被级联清空（剩 {remaining} 行）"


def test_replace_content_files_writes_back_content_hash(repo):
    """F5：`replace_content_files` 要把行里的 `content_hash` 回写到
    `duplicate_check_files.content_hash`（对齐参考 `saveMetadataAnalysisDetails`
    :418 先清 NULL、:435-439 再回写）。"""
    repo.replace_files("p1", "bid", [_file("b", "b.docx")])

    repo.replace_content_files("p1", [
        {"file_id": "b", "status": "success", "content_path": "/x/b.md",
         "content_length": 5, "content_hash": "hash-b", "updated_at": "now"}])
    assert repo.list_files("p1", "bid")[0]["content_hash"] == "hash-b"

    # 再传一批不带 hash 的行：先把本项目 hash 清 NULL，未携带 hash 的行不再回写
    repo.replace_content_files("p1", [
        {"file_id": "b", "status": "pending", "content_path": None,
         "content_length": 0, "updated_at": "now2"}])
    assert repo.list_files("p1", "bid")[0]["content_hash"] is None


def test_replace_outline_items_tolerates_cross_file_parent(repo):
    """F3：子行的父节点位于另一个文件时不得撞 FK，按裁定**置 None**。

    拼父 id 时用**子行自己的** `file_id` 去 scoped 传入的 `parent_item_id`
    会得到一个不存在的 id → `FOREIGN KEY constraint failed`。
    跨文件父子在服务端 schema 下无意义（`item_id` 本身已 scoped），故显式判无效。
    调用方用 `parent_file_id` 声明父节点所属文件（缺省即父在子行同文件）。
    """
    repo.replace_files("p1", "bid", [_file("fa", "a.docx"), _file("fb", "b.docx")])
    repo.replace_outline_items("p1", [
        {"item_id": "a-1", "file_id": "fa", "title": "A 的父"},
        {"item_id": "b-1", "file_id": "fb", "parent_item_id": "a-1",
         "parent_file_id": "fa", "title": "B 的孤儿"},
    ])

    rows = {row["item_id"]: row for row in repo._db.query(
        "SELECT * FROM duplicate_check_outline_items WHERE project_id = 'p1'")}
    assert rows["fb::b-1"]["parent_item_id"] is None
    assert rows["fa::a-1"]["parent_item_id"] is None


def test_replace_outline_items_keeps_same_file_parent(repo):
    """F3 对照：父节点在同一文件内时父指针照常保留（scoped 形式）。"""
    repo.replace_files("p1", "bid", [_file("fa", "a.docx")])
    repo.replace_outline_items("p1", [
        {"item_id": "child", "file_id": "fa", "parent_item_id": "parent", "title": "子"},
        {"item_id": "parent", "file_id": "fa", "title": "父"},
    ])

    rows = {row["item_id"]: row for row in repo._db.query(
        "SELECT * FROM duplicate_check_outline_items WHERE project_id = 'p1'")}
    assert rows["fa::child"]["parent_item_id"] == "fa::parent"
    assert rows["fa::parent"]["parent_item_id"] is None
    # 读回时 scoped 前缀被剥掉
    repo.save_section("p1", "outline", status="success", progress=100, message="o",
                      updated_at="2026-01-01T00:00:00Z")
    items = repo.load_analysis("p1")["outlineAnalysis"]["files"][0]["items"]
    assert {item["id"]: item["parent_id"] for item in items} == {"child": "parent",
                                                                 "parent": None}
