"""P4a T2b-2：投标主体档案与视觉资产的仓储语义。

真值来源 `client/electron/services/rejectionCheckStore.cjs`：
`normalizeIdentificationTarget` :1234-1242、`loadSubjectProfile` :1244-1262、
`saveSubjectProfile` :1263-1297、`resetSubjectProfile` :1298-1310、
`deleteSubjectFinding` :1406-1409、`listVisualAssets`/`saveVisualAssets`/`clearVisualAssets` :1347-1405。

主考点：**`identificationTarget` 存在 `candidates_json` 里**，且
「未携带该键 = 保留旧值，显式 None = 清空」——这是项目记忆里挂了很久的那条。
"""

from __future__ import annotations

import json

import pytest

from app.infra.db.engine import Database
from app.infra.db.migrate import apply_schema
from app.infra.repositories.projects import ProjectRepository
from app.infra.repositories.rejection import RejectionRepository


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "rejection.sqlite")
    apply_schema(db)
    projects = ProjectRepository(db)
    project = projects.create("主体项目")
    other = projects.create("另一个项目")
    yield db, RejectionRepository(db), project, other
    db.close()


def _candidates_json(repo, project):
    row = repo._db.query_one(
        "SELECT candidates_json FROM rejection_check_subject_profile WHERE project_id = ?",
        (project,))
    return json.loads(row["candidates_json"]) if row else None


# ----------------------------------------------------------------- 主体档案

def test_profile_default_when_absent(env):
    _, repo, project, _ = env
    assert repo.load_subject_profile(project) == {
        "status": "idle", "officialName": "", "aliases": [], "candidates": []}


def test_save_profile_carries_identification_target_inside_candidates_json(env):
    _, repo, project, _ = env
    saved = repo.save_subject_profile(project, {
        "status": "pending-confirmation", "officialName": "甲公司",
        "aliases": ["甲", "甲公司"], "candidates": [{"name": "甲公司"}],
        "preselectIndex": 0,
        "identificationTarget": {"mode": "single", "bidDocumentId": "bid-a"},
    })
    assert saved["status"] == "pending-confirmation"
    assert saved["aliases"] == ["甲", "甲公司"]
    assert saved["candidates"] == [{"name": "甲公司"}]
    assert saved["preselectIndex"] == 0
    assert saved["identificationTarget"] == {"mode": "single", "bidDocumentId": "bid-a"}
    payload = _candidates_json(repo, project)
    assert payload["identificationTarget"] == {"mode": "single", "bidDocumentId": "bid-a"}


def test_save_profile_without_target_key_keeps_previous(env):
    """**核心语义**：任务 checkpoint 回写档案时不携带该键 → 识别范围必须保留。"""
    _, repo, project, _ = env
    repo.save_subject_profile(project, {
        "status": "identifying", "identificationTarget": {"mode": "all"}})
    repo.save_subject_profile(project, {"status": "pending-confirmation"})
    assert repo.load_subject_profile(project)["identificationTarget"] == {"mode": "all"}


def test_save_profile_with_explicit_none_clears_target(env):
    _, repo, project, _ = env
    repo.save_subject_profile(project, {"status": "identifying",
                                        "identificationTarget": {"mode": "all"}})
    repo.save_subject_profile(project, {"status": "idle", "identificationTarget": None})
    assert "identificationTarget" not in repo.load_subject_profile(project)


def test_identification_target_normalization(env):
    _, repo, project, _ = env
    # 非法形状一律视为未设置（参考 :1234-1242）
    for bad in ({"mode": "single"}, {"mode": "乱写"}, "all", 3, None):
        repo.save_subject_profile(project, {"identificationTarget": bad})
        assert "identificationTarget" not in repo.load_subject_profile(project)


def test_profile_status_vocabulary_differs_from_results(env):
    """档案的状态词表是 idle/identifying/pending-confirmation/confirmed/error。"""
    _, repo, project, _ = env
    repo.save_subject_profile(project, {"status": "confirmed"})
    assert repo.load_subject_profile(project)["status"] == "confirmed"
    # running/success 不属于该词表 → 回落 idle
    repo.save_subject_profile(project, {"status": "success"})
    assert repo.load_subject_profile(project)["status"] == "idle"


def test_preselect_index_write_default_is_minus_one_and_reads_back(env):
    """保存侧缺省写 -1（整数），读取侧只回传整数 → 因此会读到 -1（逐字对齐参考）。"""
    _, repo, project, _ = env
    saved = repo.save_subject_profile(project, {"status": "idle"})
    assert saved["preselectIndex"] == -1
    repo.save_subject_profile(project, {"status": "idle", "preselectIndex": 2})
    assert repo.load_subject_profile(project)["preselectIndex"] == 2


def test_reset_profile_clears_everything_and_sets_reason(env):
    _, repo, project, _ = env
    repo.save_subject_profile(project, {
        "status": "confirmed", "officialName": "甲公司", "aliases": ["甲"],
        "candidates": [{"name": "甲公司"}], "bidSignature": "sig",
        "confirmedAt": "2026-09-23T00:00:00Z",
        "identificationTarget": {"mode": "all"}})
    reset = repo.reset_subject_profile(project, "投标文件已更新，请重新确认投标主体")
    assert "updatedAt" in reset  # 参考 loadSubjectProfile 会回传（:1259）
    reset.pop("updatedAt")
    assert reset == {
        "status": "idle", "officialName": "", "aliases": [], "candidates": [],
        "preselectIndex": -1,
        "error": "投标文件已更新，请重新确认投标主体",
    }
    assert "bidSignature" not in reset and "confirmedAt" not in reset
    assert "identificationTarget" not in reset


def test_profiles_are_project_scoped(env):
    _, repo, project, other = env
    repo.save_subject_profile(project, {"status": "confirmed", "officialName": "甲"})
    repo.save_subject_profile(other, {"status": "idle", "officialName": "乙"})
    assert repo.load_subject_profile(project)["officialName"] == "甲"
    assert repo.load_subject_profile(other)["officialName"] == "乙"


def test_delete_subject_finding(env):
    _, repo, project, _ = env
    repo.save_result(project, "subject", {"status": "success", "findings": [
        {"id": "s-1", "level": "needs-review", "source": "seal"},
        {"id": "s-2", "level": "needs-review", "source": "text"}]})
    assert repo.delete_subject_finding(project, "s-1") is True
    assert [item["id"] for item in repo.load_findings(project, "subject")] == ["s-2"]
    assert repo.delete_subject_finding(project, "s-1") is False


# ----------------------------------------------------------------- 视觉资产

def _assets():
    return [
        {"id": "a-1", "mediaName": "image1.png", "contentHash": "h1",
         "relativePath": "assets/h1.png", "mimeType": "image/png",
         "width": 100.4, "height": 50.6, "placement": "anchor",
         "assetKind": "standalone", "locationHint": "第 3 页", "nearbyText": "盖章处",
         "sectionTitle": "投标函"},
        {"mediaName": "image2.png", "contentHash": "h2", "relativePath": "assets/h2.png",
         "mimeType": "", "width": -5, "height": "abc", "placement": "乱写",
         "assetKind": "乱写"},
    ]


def test_save_visual_assets_mapping_and_defaults(env):
    _, repo, project, _ = env
    assert repo.save_visual_assets(project, "bid-a", _assets()) == 2
    assets = repo.list_visual_assets(project, "bid-a")
    assert [item["id"] for item in assets] == ["a-1", "bid-a-asset-2"]
    first, second = assets
    assert (first["width"], first["height"]) == (100, 51)   # Math.round + 夹取
    assert first["sectionTitle"] == "投标函"
    assert (second["width"], second["height"]) == (0, 0)     # 负数/不可解析 → 0
    assert second["placement"] == "unknown"
    assert second["assetKind"] == "unknown"
    assert second["mimeType"] == "application/octet-stream"
    assert "sectionTitle" not in second                     # 空值不出现在读数里


def test_save_visual_assets_replaces_only_that_document(env):
    _, repo, project, _ = env
    repo.save_visual_assets(project, "bid-a", _assets())
    repo.save_visual_assets(project, "bid-b", [{"id": "b-1", "mediaName": "b.png"}])
    repo.save_visual_assets(project, "bid-a", [{"id": "a-9", "mediaName": "new.png"}])
    assert [item["id"] for item in repo.list_visual_assets(project, "bid-a")] == ["a-9"]
    assert [item["id"] for item in repo.list_visual_assets(project, "bid-b")] == ["b-1"]
    assert len(repo.list_visual_assets(project)) == 2


def test_clear_visual_assets_by_document_and_all(env):
    _, repo, project, _ = env
    repo.save_visual_assets(project, "bid-a", [{"id": "a-1"}])
    repo.save_visual_assets(project, "bid-b", [{"id": "b-1"}])
    repo.clear_visual_assets(project, "bid-a")
    assert repo.list_visual_assets(project, "bid-a") == []
    assert len(repo.list_visual_assets(project)) == 1
    repo.clear_visual_assets(project)
    assert repo.list_visual_assets(project) == []


def test_visual_assets_are_project_scoped(env):
    _, repo, project, other = env
    repo.save_visual_assets(project, "bid-a", [{"id": "a-1"}])
    repo.save_visual_assets(other, "bid-a", [{"id": "a-1"}, {"id": "a-2"}])
    assert len(repo.list_visual_assets(project)) == 1
    assert len(repo.list_visual_assets(other)) == 2
