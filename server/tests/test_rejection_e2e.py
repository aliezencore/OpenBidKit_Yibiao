"""P4a T6：废标项检查端到端闭环。

覆盖「导入 → 提取 → 识别 → 确认 → 检查 → 结果齐」这条主链，以及两类**作废**：
换投标文件 → 只作废检查结果（extraction 保留）；换招标文件 → extraction 与检查结果都作废
（`clearDocumentRows` 的 tender/bid 不对称，参考 :422-443）。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

DOCX_MIME = ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document")


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, data_root=tmp_path / "data")
    with TestClient(create_app(settings)) as client:
        project = client.post("/projects", json={"name": "闭环"}).json()["id"]
        yield client, project, tmp_path


def _docx(tmp_path: Path, name: str, text: str) -> bytes:
    from tests.parsing_fixtures import build_docx

    return build_docx(tmp_path / name, blocks=[("p", text)]).read_bytes()


def _upload(client, project, role, name, text, tmp_path, index=0):
    return client.post(
        f"/projects/{project}/rejection/documents",
        data={"role": role},
        files=[("files", (name, _docx(tmp_path, f"n{role}{index}.docx", text), DOCX_MIME))])


def _wait(client, task_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = client.get(f"/tasks/{task_id}").json()
        if task["status"] in ("success", "error", "cancelled", "interrupted"):
            return task
        time.sleep(0.02)
    return client.get(f"/tasks/{task_id}").json()


def _state(client, project):
    return client.get(f"/projects/{project}/rejection/state").json()


def _run_full_flow(client, project, tmp_path, *, check_options=None):
    """跑完整主链，返回最终 state。"""
    _upload(client, project, "tender", "招标文件.docx", "招标正文：须加盖公章。", tmp_path, 0)
    _upload(client, project, "bid", "投标文件.docx", "投标正文：我公司参加投标。", tmp_path, 1)

    extract = client.post(f"/projects/{project}/rejection/extract")
    assert _wait(client, extract.json()["taskId"])["status"] == "success"
    identify = client.post(f"/projects/{project}/rejection/subject/identify")
    assert _wait(client, identify.json()["taskId"])["status"] == "success"
    confirm = client.post(f"/projects/{project}/rejection/subject/confirm",
                          json={"officialName": "甲公司"})
    assert confirm.json()["profile"]["status"] == "confirmed"
    run = client.post(f"/projects/{project}/rejection/run",
                      json={"checkOptions": check_options or {"subjectCheck": True}})
    assert run.status_code == 200, run.text
    assert _wait(client, run.json()["taskId"])["status"] == "success"
    return _state(client, project)


def test_full_flow_reaches_four_success_results(env):
    client, project, tmp_path = env
    state = _run_full_flow(client, project, tmp_path)

    assert state["step"] == "documents"          # 主链不改步进（由前端/UI 端点驱动）
    assert state["invalidBidAndRejectionItems"]["status"] == "success"
    assert state["subjectProfile"]["status"] == "confirmed"
    for key in ("rejectionCheckResult", "typoCheckResult", "logicCheckResult",
                "subjectCheckResult"):
        assert state[key]["status"] == "success", key
        assert state[key]["findings"] == []          # 占位算法：无 findings
    # 四类任务的最近一条都在状态里
    assert state["extractionTask"]["status"] == "success"
    assert state["subjectIdentificationTask"]["status"] == "success"
    assert state["checkTask"]["status"] == "success"


def test_rerun_keeps_result_shape_and_does_not_duplicate(env):
    """重复执行：结果行形状不变、不累积（参考的替换语义）。"""
    client, project, tmp_path = env
    _run_full_flow(client, project, tmp_path)
    first = _state(client, project)

    run = client.post(f"/projects/{project}/rejection/run",
                      json={"checkOptions": {"subjectCheck": True}})
    assert _wait(client, run.json()["taskId"])["status"] == "success"
    second = _state(client, project)

    for key in ("rejectionCheckResult", "typoCheckResult", "logicCheckResult",
                "subjectCheckResult"):
        assert second[key]["status"] == first[key]["status"] == "success"
        assert second[key]["findings"] == first[key]["findings"] == []
    repo = client.app.state.yibiao.rejection
    assert repo.list_documents(project, role="bid")  # 文档没有被重复导入


def test_reuploading_bid_clears_check_results_but_keeps_extraction(env):
    """换投标文件 → 只作废四项检查结果；extraction（吃招标内容）保留。"""
    client, project, tmp_path = env
    _run_full_flow(client, project, tmp_path)
    repo = client.app.state.yibiao.rejection
    assert repo.load_extraction(project)["status"] == "success"

    _upload(client, project, "bid", "新的投标文件.docx", "投标正文二。", tmp_path, 2)

    state = _state(client, project)
    assert len(state["bidDocuments"]) == 2
    assert repo.load_extraction(project)["status"] == "success"      # 保留
    for key in ("rejectionCheckResult", "typoCheckResult", "logicCheckResult",
                "subjectCheckResult"):
        assert repo.get_result_row(project, key) is None, key
    # 参考 :1076-1080：新增投标文档还要**重置主体档案**（confirmed 失效）并**切页签**
    assert repo.load_subject_profile(project)["status"] == "idle"
    assert state["activeDocumentTab"] == state["bidDocuments"][-1]["id"]


def test_reuploading_tender_clears_extraction_and_check_results(env):
    """换招标文件 → extraction 与四项检查结果**都**作废（tender 分支的连带语义）。"""
    client, project, tmp_path = env
    _run_full_flow(client, project, tmp_path)
    repo = client.app.state.yibiao.rejection

    _upload(client, project, "tender", "新招标文件.docx", "招标正文二。", tmp_path, 3)

    state = _state(client, project)
    assert len(state["tenderDocuments"]) == 2        # 源文档两条（+ 主文档）
    assert repo.load_extraction(project) == {"status": "idle", "content": ""}
    for key in ("rejectionCheckResult", "typoCheckResult", "logicCheckResult",
                "subjectCheckResult"):
        assert repo.get_result_row(project, key) is None, key
    # 主体档案不受文档变更影响（只有主体检查结果被清）
    assert repo.load_subject_profile(project)["status"] == "confirmed"


def test_deleting_one_bid_resets_subject_profile_and_switches_tab(env):
    """参考 :1223-1231：删投标 → 清该文档视觉资产 + 重置主体档案（带 reason）+ 切页签。"""
    client, project, tmp_path = env
    _run_full_flow(client, project, tmp_path)
    repo = client.app.state.yibiao.rejection
    first_bid = _state(client, project)["bidDocuments"][0]["id"]
    repo.save_visual_assets(project, first_bid, [{"id": "a-1"}])
    assert repo.list_visual_assets(project, first_bid)

    client.delete(f"/projects/{project}/rejection/documents",
                  params={"role": "bid", "documentId": first_bid})

    profile = repo.load_subject_profile(project)
    assert profile["status"] == "idle"
    assert profile["error"] == "投标文件已更新，请重新确认投标主体"
    assert repo.list_visual_assets(project, first_bid) == []
    assert _state(client, project)["activeDocumentTab"] == "tender"


def test_deleting_tender_keeps_subject_profile(env):
    """删招标不动主体档案（它依赖投标签名），只把页签切回 'tender'。"""
    client, project, tmp_path = env
    _run_full_flow(client, project, tmp_path)
    repo = client.app.state.yibiao.rejection

    client.delete(f"/projects/{project}/rejection/documents", params={"role": "tender"})

    assert repo.load_subject_profile(project)["status"] == "confirmed"
    assert _state(client, project)["activeDocumentTab"] == "tender"


def test_deleting_all_bid_documents_blocks_run(env):
    """删光投标文档后，检查前置重新拦住（投标内容缺失）。"""
    client, project, tmp_path = env
    _run_full_flow(client, project, tmp_path)
    client.delete(f"/projects/{project}/rejection/documents", params={"role": "bid"})

    response = client.post(f"/projects/{project}/rejection/run", json={})
    assert response.status_code == 400
    assert response.json()["detail"] == "缺少投标文件内容，无法开始检查"


def test_state_after_documents_import_exposes_counts(env):
    client, project, tmp_path = env
    _upload(client, project, "tender", "招标文件.docx", "招标正文。", tmp_path, 0)
    _upload(client, project, "bid", "投标A.docx", "投标甲。", tmp_path, 1)
    _upload(client, project, "bid", "投标B.docx", "投标乙。", tmp_path, 2)
    state = _state(client, project)
    assert state["tenderDocument"]["id"] == "tender"
    assert len(state["tenderDocuments"]) == 1
    assert [row["fileName"] for row in state["bidDocuments"]] == ["投标A.docx", "投标B.docx"]
    assert [row["sortOrder"] for row in state["bidDocuments"]] == [0, 1]
