"""P4a T3：废标项文档导入／列表／删除 API。

真值来源 `client/electron/services/rejectionCheckStore.cjs`：
`importDocument` :949-1030、`clearDocumentRows` :422-443、
`getDocumentMarkdownRelativePath` :233-244、`resequenceBidDocuments` :381-387。

三处最易错、本文件重点打的点：
1. 招标是**主文档 + 源文档**双层（主文档 `id='tender'`、sort_order=0、内容是合并；源文档从 1 起）；
2. 同内容重复导入按 `文件名\\u0000sha256` 去重；
3. 清 tender **连带清 extraction 与四项结果**，清 bid **只清四项结果**。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, data_root=tmp_path / "data")
    with TestClient(create_app(settings)) as client:
        project = client.post("/projects", json={"name": "废标项"}).json()["id"]
        yield client, project, tmp_path / "data"


DOCX_MIME = ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document")


def _upload(client, project, role, files):
    return client.post(
        f"/projects/{project}/rejection/documents",
        data={"role": role},
        files=[("files", (name, content, DOCX_MIME)) for name, content in files])


def _docx(tmp_path, name, text):
    from tests.parsing_fixtures import build_docx

    return build_docx(tmp_path / name, blocks=[("p", text)]).read_bytes()


# ---------------------------------------------------------------- 导入：bid

def test_import_bid_creates_rows_and_markdown(env):
    client, project, data_root = env
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as tmp:
        body = _upload(client, project, "bid", [
            ("投标A.docx", _docx(Path(tmp), "a.docx", "投标甲正文")),
            ("投标B.docx", _docx(Path(tmp), "b.docx", "投标乙正文")),
        ])
    assert body.status_code == 200
    rows = body.json()["bid"]
    assert [row["fileName"] for row in rows] == ["投标A.docx", "投标B.docx"]
    assert [row["sortOrder"] for row in rows] == [0, 1]
    assert all(row["id"].startswith("bid-") and len(row["id"]) == 20 for row in rows)
    assert all(row["role"] == "bid" for row in rows)
    # markdown 落在规范路径且内容可读
    for row in rows:
        assert row["markdownPath"] == f"rejection-check/bids/{row['id']}.md"
        target = data_root / "projects" / project / row["markdownPath"]
        assert target.is_file()
    assert body.json()["tender"] == []


def test_import_bid_dedupes_same_name_and_content(env):
    client, project, _ = env
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as tmp:
        payload = _docx(Path(tmp), "same.docx", "同一份内容")
        _upload(client, project, "bid", [("投标A.docx", payload)])
        again = _upload(client, project, "bid", [("投标A.docx", payload)])
    assert again.status_code == 200
    assert len(again.json()["bid"]) == 1  # 去重：没有新增行


def test_import_requires_files(env):
    client, project, _ = env
    response = client.post(f"/projects/{project}/rejection/documents",
                           data={"role": "bid"}, files=[])
    assert response.status_code == 400
    assert response.json()["detail"] == "未导入文件"


# ------------------------------------------------------------- 导入：tender

def test_import_single_tender_creates_main_and_source(env):
    client, project, data_root = env
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as tmp:
        body = _upload(client, project, "tender", [
            ("招标文件.docx", _docx(Path(tmp), "t.docx", "招标正文"))])
    rows = body.json()["tender"]
    by_id = {row["id"]: row for row in rows}
    assert set(by_id) == {"tender", next(iter(set(by_id) - {"tender"}))}
    main = by_id["tender"]
    assert main["sortOrder"] == 0
    assert main["fileName"] == "招标文件.docx"      # 单份时用该份文件名
    assert main["parserLabel"] == "local"           # 单份时带 label
    assert main["markdownPath"] == "rejection-check/tender.md"
    source = by_id[[key for key in by_id if key != "tender"][0]]
    assert source["sortOrder"] == 1                 # 源文档从 1 起
    assert source["id"].startswith("tender-01-")
    assert source["markdownPath"].startswith("rejection-check/tenders/")
    # 主文档内容是源文档的合并
    main_text = (data_root / "projects" / project / "rejection-check/tender.md").read_text(
        encoding="utf-8")
    assert "招标正文" in main_text


def test_import_second_tender_rewrites_main_document(env):
    client, project, data_root = env
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as tmp:
        _upload(client, project, "tender", [("招标A.docx", _docx(Path(tmp), "a.docx", "甲正文"))])
        body = _upload(client, project, "tender",
                       [("招标B.docx", _docx(Path(tmp), "b.docx", "乙正文"))])
    rows = body.json()["tender"]
    main = next(row for row in rows if row["id"] == "tender")
    assert main["fileName"] == "2 份招标文件"        # 多份时的文件名规则
    assert main["parserLabel"] is None              # 多份时 label 置空
    sources = sorted((row for row in rows if row["id"] != "tender"),
                     key=lambda row: row["sortOrder"])
    assert len(sources) == 2
    assert [row["sortOrder"] for row in sources] == [1, 2]
    main_text = (data_root / "projects" / project / "rejection-check/tender.md").read_text(
        encoding="utf-8")
    assert "甲正文" in main_text and "乙正文" in main_text   # 合并了两份


# ------------------------------------------------------------ 列表与删除

def test_list_documents_returns_both_groups(env):
    client, project, _ = env
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as tmp:
        _upload(client, project, "bid", [("投标.docx", _docx(Path(tmp), "b.docx", "投标正文"))])
        _upload(client, project, "tender", [("招标.docx", _docx(Path(tmp), "t.docx", "招标正文"))])
    body = client.get(f"/projects/{project}/rejection/documents").json()
    assert len(body["bid"]) == 1
    assert len(body["tender"]) == 2  # 主文档 + 源文档


def test_delete_tender_clears_extraction_and_results(env):
    client, project, data_root = env
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as tmp:
        _upload(client, project, "tender", [("招标.docx", _docx(Path(tmp), "t.docx", "招标正文"))])
        _upload(client, project, "bid", [("投标.docx", _docx(Path(tmp), "b.docx", "投标正文"))])
    # 造出解析结果与检查结果
    repo = client.app.state.yibiao.rejection
    repo.save_extraction(project, {"status": "success", "content": "解析内容"})
    repo.save_result(project, "typo", {
        "status": "success", "findings": [
            {"wrongText": "错", "correctText": "对", "originalExcerpt": "摘", "reason": "因"}]})

    body = client.delete(f"/projects/{project}/rejection/documents",
                         params={"role": "tender"}).json()

    assert body["tender"] == []
    assert len(body["bid"]) == 1                       # 投标不受影响
    assert repo.load_extraction(project) == {"status": "idle", "content": ""}
    assert repo.load_findings(project, "typo") == []    # 四项结果一并作废
    # 招标的 markdown 文件被清掉（best-effort）
    assert not (data_root / "projects" / project / "rejection-check/tender.md").exists()


def test_delete_single_bid_resequences_and_keeps_extraction(env):
    client, project, _ = env
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as tmp:
        _upload(client, project, "tender", [("招标.docx", _docx(Path(tmp), "t.docx", "招标正文"))])
        first = _upload(client, project, "bid", [
            ("投标A.docx", _docx(Path(tmp), "a.docx", "甲")),
            ("投标B.docx", _docx(Path(tmp), "b.docx", "乙")),
        ]).json()["bid"]
    repo = client.app.state.yibiao.rejection
    repo.save_extraction(project, {"status": "success", "content": "解析内容"})
    repo.save_result(project, "logic", {"status": "success", "findings": [
        {"title": "t", "originalText": "o", "locationHint": "h",
         "fallacyReason": "f", "suggestion": "s"}]})

    body = client.delete(f"/projects/{project}/rejection/documents",
                         params={"role": "bid", "documentId": first[0]["id"]}).json()

    remaining = body["bid"]
    assert [row["fileName"] for row in remaining] == ["投标B.docx"]
    assert remaining[0]["sortOrder"] == 0               # 重排序号
    assert repo.load_extraction(project)["content"] == "解析内容"   # extraction 保留
    assert repo.load_findings(project, "logic") == []              # 四项结果作废
