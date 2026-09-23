"""P4a T4：废标项检查的状态、UI 态与主体确认 API。

真值来源 `client/electron/services/rejectionCheckStore.cjs`：
`loadRejectionCheck` :863-901、`saveUiState` :1411-1418、
`normalize*` :90-110、`confirmSubject` :1320-1344、`getCurrentBidSignature` :1308-1312、
`loadTasks`/`taskFromRow` :543-551 / :166-179。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.rejection.normalize import BID_SIGNATURE_SEPARATOR, DEFAULT_CHECK_OPTIONS
from app.main import create_app

DOCX_MIME = ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document")


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, data_root=tmp_path / "data")
    with TestClient(create_app(settings)) as client:
        project = client.post("/projects", json={"name": "状态"}).json()["id"]
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


# ------------------------------------------------------------------ GET state

def test_state_shape_and_defaults(env):
    client, project, _ = env
    state = client.get(f"/projects/{project}/rejection/state").json()
    assert state["step"] == "documents"
    assert state["activeDocumentTab"] == "tender"
    assert state["activeResultTab"] == "analysis"
    assert state["activeCheckResultTab"] == "rejection"
    assert state["customCheckItems"] == ""
    assert state["checkOptions"] == DEFAULT_CHECK_OPTIONS
    assert state["tenderDocument"] is None
    assert state["tenderDocuments"] == [] and state["bidDocuments"] == []
    assert state["invalidBidAndRejectionItems"] == {"status": "idle", "content": ""}
    for key in ("rejectionCheckResult", "typoCheckResult", "logicCheckResult",
                "subjectCheckResult"):
        assert state[key] == {"status": "idle", "findings": []}
    assert state["subjectProfile"] == {
        "status": "idle", "officialName": "", "aliases": [], "candidates": []}
    assert state["tasks"] == {}


def test_state_exposes_documents_and_rejects_stale_active_tab(env):
    client, project, tmp_path = env
    _upload(client, project, "tender", [("招标.docx", "招标正文")], tmp_path)
    _upload(client, project, "bid", [("投标.docx", "投标正文")], tmp_path)
    # 写入一个不存在的页签 id → 状态里必须回落到 'tender'（参考 :876-886）
    client.put(f"/projects/{project}/rejection/ui",
               json={"activeDocumentTab": "bid-不存在"})
    state = client.get(f"/projects/{project}/rejection/state").json()
    assert state["activeDocumentTab"] == "tender"
    assert state["tenderDocument"]["id"] == "tender"
    assert len(state["tenderDocuments"]) == 1
    assert [row["fileName"] for row in state["bidDocuments"]] == ["投标.docx"]


# --------------------------------------------------------------- PUT ui 态

def test_ui_patch_writes_only_provided_fields_and_normalizes(env):
    client, project, _ = env
    response = client.put(f"/projects/{project}/rejection/ui",
                          json={"step": "乱写", "activeResultTab": "custom",
                                "customCheckItems": "自定义项"})
    state = response.json()
    assert state["step"] == "documents"          # normalizeStep 回落
    assert state["activeResultTab"] == "custom"
    assert state["customCheckItems"] == "自定义项"
    assert state["activeCheckResultTab"] == "rejection"   # 未提供 → 保持默认

    # 未提供的字段在新一次 patch 后保持原值
    again = client.put(f"/projects/{project}/rejection/ui",
                       json={"activeCheckResultTab": "typo"}).json()
    assert again["activeCheckResultTab"] == "typo"
    assert again["customCheckItems"] == "自定义项"


def test_ui_patch_normalizes_check_options(env):
    client, project, _ = env
    state = client.put(f"/projects/{project}/rejection/ui",
                       json={"checkOptions": {"subjectCheck": False,
                                              "subjectSealCheck": True,
                                              "typoCheck": False}}).json()
    assert state["checkOptions"] == {
        "rejectionCheck": True, "typoCheck": False, "logicCheck": True,
        "subjectCheck": False, "subjectSealCheck": False}  # 印章跟随主体关闭


# ----------------------------------------------------------- 主体确认

def test_confirm_subject_requires_name(env):
    client, project, _ = env
    response = client.post(f"/projects/{project}/rejection/subject/confirm",
                           json={"officialName": "   "})
    assert response.status_code == 400
    assert response.json()["detail"] == "请填写正式投标主体名称"


def test_confirm_subject_writes_profile_and_bid_signature(env):
    client, project, tmp_path = env
    _upload(client, project, "bid", [("投标.docx", "投标正文")], tmp_path)
    body = client.post(f"/projects/{project}/rejection/subject/confirm",
                       json={"officialName": " 甲公司 ", "aliases": ["甲", " ", "甲公司"]}).json()
    assert body["success"] is True and body["message"] == "投标主体已确认"
    profile = body["profile"]
    assert profile["status"] == "confirmed"
    assert profile["officialName"] == "甲公司"       # 已 trim
    assert profile["aliases"] == ["甲", "甲公司"]     # 去空
    assert BID_SIGNATURE_SEPARATOR not in profile["bidSignature"]  # 单份无分隔符
    assert profile["bidSignature"]
    assert profile["confirmedAt"]


def test_confirm_subject_invalidates_subject_result(env):
    client, project, tmp_path = env
    _upload(client, project, "bid", [("投标.docx", "投标正文")], tmp_path)
    repo = client.app.state.yibiao.rejection
    repo.save_result(project, "subject", {"status": "success", "findings": [
        {"id": "s-1", "level": "needs-review", "source": "seal"}]})
    assert repo.load_findings(project, "subject")

    client.post(f"/projects/{project}/rejection/subject/confirm",
                json={"officialName": "甲公司"})

    result = repo.load_result(project, "subject")
    assert result["status"] == "idle"
    assert result["findings"] == []
    assert "inputSignature" not in result      # 参考显式写 null → 读侧不出现


def test_confirm_subject_identification_target_keep_and_clear(env):
    client, project, tmp_path = env
    _upload(client, project, "bid", [("投标.docx", "投标正文")], tmp_path)
    first = client.post(f"/projects/{project}/rejection/subject/confirm",
                        json={"officialName": "甲公司",
                              "identificationTarget": {"mode": "all"}}).json()
    assert first["profile"]["identificationTarget"] == {"mode": "all"}
    # 未携带该键 → 保留
    kept = client.post(f"/projects/{project}/rejection/subject/confirm",
                       json={"officialName": "甲公司"}).json()
    assert kept["profile"]["identificationTarget"] == {"mode": "all"}
    # 显式 null → 清空
    cleared = client.post(f"/projects/{project}/rejection/subject/confirm",
                          json={"officialName": "甲公司",
                                "identificationTarget": None}).json()
    assert "identificationTarget" not in cleared["profile"]


def test_state_includes_rejection_tasks_when_present(env):
    """任务统一在 tasks 表：状态里按 kind 暴露三类任务的最新一条。"""
    client, project, _ = env
    tasks_repo = client.app.state.yibiao.tasks
    tasks_repo.create(task_id="t-1", project_id=project, domain="rejection",
                      kind="rejection-check-run", payload={}, created_at="2026-09-23T00:00:00Z")
    state = client.get(f"/projects/{project}/rejection/state").json()
    assert state["tasks"]["checkTask"]["task_id"] == "t-1"
    assert state["tasks"]["checkTask"]["type"] == "rejection-check-run"
    assert state["tasks"]["checkTask"]["status"] == "running"  # queued → normalize 到 running
    assert "extractionTask" not in state["tasks"]
