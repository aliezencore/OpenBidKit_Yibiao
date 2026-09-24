"""P4a T5：四检查编排（前置校验逐字 + 零算法 stub 闭环 + 进度口径）。

真值来源 `client/electron/services/rejectionCheckTask.cjs`：
前置 :1519-1521 / :1619-1666，进度 :1679，
初始 running 文案 :1649-1653；`subjectCheckTask.cjs:647` 的识别前置。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.infra.tasks.rejection_stub import compute_progress
from app.main import create_app

DOCX_MIME = ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document")


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, data_root=tmp_path / "data")
    with TestClient(create_app(settings)) as client:
        project = client.post("/projects", json={"name": "编排"}).json()["id"]
        _inject_fake_ai(client)
        yield client, project, tmp_path


def _docx(tmp_path: Path, name: str, text: str) -> bytes:
    from tests.parsing_fixtures import build_docx

    return build_docx(tmp_path / name, blocks=[("p", text)]).read_bytes()


def _upload(client, project, role, files, tmp_path):
    return client.post(
        f"/projects/{project}/rejection/documents",
        data={"role": role},
        files=[("files", (name, _docx(tmp_path, f"u{index}.docx", text), DOCX_MIME))
               for index, (name, text) in enumerate(files)])


def _wait_task(client, task_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = client.get(f"/tasks/{task_id}").json()
        if task["status"] in ("success", "error", "cancelled", "interrupted"):
            return task
        time.sleep(0.02)
    return client.get(f"/tasks/{task_id}").json()


# ------------------------------------------------------------ 进度公式

def test_progress_formula_matches_reference():
    """参考 :1679：min(95, round(5 + completed/enabled × 90))。"""
    assert compute_progress(0, 4) == 5
    assert compute_progress(1, 4) == round(5 + 0.25 * 90)
    assert compute_progress(2, 4) == 50
    assert compute_progress(4, 4) == 95          # 封顶 95，不到 100
    assert compute_progress(3, 0) == 5           # 除零保护


# ------------------------------------------------------------ 前置校验

def test_extract_requires_tender_content(env):
    client, project, _ = env
    response = client.post(f"/projects/{project}/rejection/extract")
    assert response.status_code == 400
    assert response.json()["detail"] == "缺少招标文件内容，无法解析无效与废标项"


def test_identify_requires_bid_content(env):
    client, project, _ = env
    response = client.post(f"/projects/{project}/rejection/subject/identify")
    assert response.status_code == 400
    assert response.json()["detail"] == "缺少投标文件内容，无法识别投标主体"


def test_run_requires_bid_content(env):
    client, project, _ = env
    response = client.post(f"/projects/{project}/rejection/run", json={})
    assert response.status_code == 400
    assert response.json()["detail"] == "缺少投标文件内容，无法开始检查"


def test_rejection_check_is_always_enabled_so_empty_set_is_unreachable(env):
    """`normalizeCheckOptions` 把 `rejectionCheck` **恒置 true**（参考 :112-113）。

    因此「请至少启用一种检查」这条前置（参考 :1627-1628）在参考与实现里**都不可达**——
    把其余三项全关掉后，启用集合仍是 `['rejection']`，于是撞上的是**提取前置**。
    本用例把这个「恒真 + 死分支」的事实钉住（照参考复刻，不「修复」）。
    """
    client, project, tmp_path = env
    _upload(client, project, "bid", [("投标.docx", "投标正文")], tmp_path)
    response = client.post(f"/projects/{project}/rejection/run", json={
        "checkOptions": {"typoCheck": False, "logicCheck": False, "subjectCheck": False}})
    assert response.status_code == 400
    assert response.json()["detail"] == "请先完成无效与废标项解析"


def test_run_requires_extraction_for_rejection_check(env):
    client, project, tmp_path = env
    _upload(client, project, "bid", [("投标.docx", "投标正文")], tmp_path)
    response = client.post(f"/projects/{project}/rejection/run",
                           json={"checkOptions": {"rejectionCheck": True}})
    assert response.status_code == 400
    assert response.json()["detail"] == "请先完成无效与废标项解析"


def test_run_requires_confirmed_subject(env):
    """四前置按参考顺序：投标 → 启用集合 → 提取 → 主体；故先跑提取才能到主体那条。"""
    client, project, tmp_path = env
    _upload(client, project, "tender", [("招标.docx", "招标正文")], tmp_path)
    _upload(client, project, "bid", [("投标.docx", "投标正文")], tmp_path)
    extract = client.post(f"/projects/{project}/rejection/extract")
    assert _wait_task(client, extract.json()["taskId"])["status"] == "success"
    response = client.post(f"/projects/{project}/rejection/run",
                           json={"checkOptions": {"subjectCheck": True}})
    assert response.status_code == 400
    assert response.json()["detail"] == "请先确认投标主体后再开始主体一致性检查"


# ------------------------------------------------------- stub 闭环（成功路径）

def test_extract_then_run_completes_with_stub_results(env):
    """闭环：导入 → 提取（占位）→ 关掉主体检查 → 跑检查 → 三项结果成功。"""
    client, project, tmp_path = env
    _upload(client, project, "tender", [("招标.docx", "招标正文")], tmp_path)
    _upload(client, project, "bid", [("投标.docx", "投标正文")], tmp_path)

    extract = client.post(f"/projects/{project}/rejection/extract")
    assert extract.status_code == 200
    assert _wait_task(client, extract.json()["taskId"])["status"] == "success"

    run = client.post(f"/projects/{project}/rejection/run", json={
        "checkOptions": {"rejectionCheck": True, "typoCheck": True,
                         "logicCheck": True, "subjectCheck": False}})
    assert run.status_code == 200
    task_id = run.json()["taskId"]
    assert _wait_task(client, task_id)["status"] == "success"

    state = client.get(f"/projects/{project}/rejection/state").json()
    assert state["invalidBidAndRejectionItems"]["status"] == "success"
    for key in ("rejectionCheckResult", "typoCheckResult", "logicCheckResult"):
        assert state[key]["status"] == "success"
    assert state["rejectionCheckResult"]["findings"] == []      # 占位：无 findings
    assert state["subjectCheckResult"]["status"] == "idle"      # 未启用
    assert state["checkTask"]["status"] == "success"


def test_identify_then_confirm_then_subject_check(env):
    """闭环：识别（占位候选）→ 确认主体 → 主体检查（占位）跑通。"""
    client, project, tmp_path = env
    _upload(client, project, "tender", [("招标.docx", "招标正文")], tmp_path)
    _upload(client, project, "bid", [("投标.docx", "投标正文")], tmp_path)
    # 主体前置在提取前置之后，故先完成提取（占位）
    extract = client.post(f"/projects/{project}/rejection/extract")
    assert _wait_task(client, extract.json()["taskId"])["status"] == "success"
    identify = client.post(f"/projects/{project}/rejection/subject/identify")
    assert _wait_task(client, identify.json()["taskId"])["status"] == "success"

    profile = client.get(f"/projects/{project}/rejection/state").json()["subjectProfile"]
    assert profile["status"] == "pending-confirmation"
    assert profile["candidates"]

    confirm = client.post(f"/projects/{project}/rejection/subject/confirm",
                          json={"officialName": "甲公司"})
    assert confirm.json()["profile"]["status"] == "confirmed"

    run = client.post(f"/projects/{project}/rejection/run", json={
        "checkOptions": {"subjectCheck": True}})
    assert run.status_code == 200
    assert _wait_task(client, run.json()["taskId"])["status"] == "success"
    result = client.get(f"/projects/{project}/rejection/state").json()["subjectCheckResult"]
    assert result["status"] == "success"
    # 无 visual assets（导入未提取图片）→ 印章分支 success 空结果
    assert result["stats"]["visionStatus"] == "success"


def _inject_fake_ai(client):
    """提取已接真实链（P4b-1）：测试注入 AI 假件（spec §11.3）。

    **必须在 yield 之前调用**（fixture 体内），否则测试期间还是真 AiClient。
    """
    from tests.ai_fakes import ScriptedAI

    # 第一段给提取（markdown），其后所有 collect_json_response 给空 findings 的 JSON
    client.app.state.yibiao.ai = ScriptedAI([
        "# 招标文件中明确提到的\n\n## 无效投标\n- 资质不符",
        '{"findings":[]}',
    ])
    return client
