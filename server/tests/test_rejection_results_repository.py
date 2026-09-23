"""P4a T2b-1：检查结果与四类 findings 的仓储语义。

真值来源 `client/electron/services/rejectionCheckStore.cjs`：
`saveResult` :590-636、`clearFindingRows` :637-643、`saveFindingRows` :644-745、
`loadResult` :746-765、`loadFindingRows` :766-820、`clearCheckResults` :822-829。

主考点是**逐字段 patch 语义**（参考的 `hasOwn`）：提供了键就写（falsy→NULL），
没提供就保留旧值——这决定了「推进度不会抹掉 inputSignature」。
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
    project = projects.create("结果项目")
    other = projects.create("另一个项目")
    yield db, RejectionRepository(db), project, other
    db.close()


def _findings_rejection():
    return [
        {"id": "f-1", "bidDocumentId": "bid-a", "type": "invalidBid", "severity": "high",
         "title": "缺盖章", "requirement": "须盖章", "bidEvidence": "第 3 页",
         "riskReason": "未盖章", "suggestion": "补盖章"},
        {"bidDocumentId": "bid-a", "type": "奇怪类型", "severity": "爆炸",
         "title": "标题即摘要"},
    ]


# ------------------------------------------------------------------- 结果行

def test_save_result_patch_keeps_unprovided_fields(env):
    _, repo, project, _ = env
    repo.save_result(project, "rejection", {
        "status": "running", "inputSignature": "sig-1",
        "activeFindingId": "f-9", "progressMessage": "第 1/4 项", "stats": {"total": 2}})
    # 只推进度：不该动 inputSignature / activeFindingId / stats
    repo.save_result(project, "rejection", {"progressMessage": "第 2/4 项"})
    loaded = repo.load_result(project, "rejection")
    assert loaded["status"] == "running"
    assert loaded["inputSignature"] == "sig-1"
    assert loaded["activeFindingId"] == "f-9"
    assert loaded["progressMessage"] == "第 2/4 项"
    assert loaded["stats"] == {"total": 2}


def test_save_result_falsy_value_clears_field(env):
    """提供了键但值为 falsy → 置 NULL（参考 `x ? String(x) : null`）。"""
    _, repo, project, _ = env
    repo.save_result(project, "rejection", {"status": "success", "error": "旧错误"})
    repo.save_result(project, "rejection", {"error": ""})
    assert "error" not in repo.load_result(project, "rejection")


def test_save_result_status_normalization_and_defaults(env):
    _, repo, project, _ = env
    repo.save_result(project, "typo", {})
    assert repo.load_result(project, "typo")["status"] == "idle"
    repo.save_result(project, "typo", {"status": "爆炸"})
    assert repo.load_result(project, "typo")["status"] == "idle"
    # updatedAt 显式提供时用它
    repo.save_result(project, "typo", {"updatedAt": "2026-09-23T05:00:00Z"})
    assert repo.load_result(project, "typo")["updatedAt"] == "2026-09-23T05:00:00Z"


def test_save_result_none_clears_row_and_findings(env):
    _, repo, project, _ = env
    repo.save_result(project, "logic", {"status": "success", "findings": [
        {"id": "l-1", "title": "t", "originalText": "o", "locationHint": "h",
         "fallacyReason": "f", "suggestion": "s"}]})
    assert repo.load_findings(project, "logic")
    repo.save_result(project, "logic", None)
    assert repo.get_result_row(project, "logic") is None
    assert repo.load_findings(project, "logic") == []


def test_save_result_rejects_unknown_type(env):
    _, repo, project, _ = env
    with pytest.raises(ValueError, match="result_type"):
        repo.save_result(project, "乱七八糟", {"status": "idle"})


def test_results_are_project_scoped(env):
    _, repo, project, other = env
    repo.save_result(project, "rejection", {"status": "success"})
    repo.save_result(other, "rejection", {"status": "error", "error": "别家失败"})
    assert repo.load_result(project, "rejection")["status"] == "success"
    assert repo.load_result(other, "rejection")["error"] == "别家失败"


# ------------------------------------------------------------------ findings

def test_rejection_findings_row_mapping_and_defaults(env):
    _, repo, project, _ = env
    repo.save_result(project, "rejection",
                     {"status": "success", "findings": _findings_rejection()})
    findings = repo.load_result(project, "rejection")["findings"]
    assert [item["id"] for item in findings] == ["f-1", "rejection-finding-2"]
    assert findings[1]["severity"] == "medium"       # 非法 severity 兜底
    assert findings[1]["type"] == "rejectionItem"    # 非法 type 兜底
    assert findings[1]["summary"] == "标题即摘要"     # summary 回落 title
    assert findings[0]["requirement"] == "须盖章"


def test_typo_and_logic_findings_mapping(env):
    _, repo, project, _ = env
    repo.save_result(project, "typo", {"status": "success", "findings": [
        {"id": "t-1", "wrongText": "以经", "correctText": "已经",
         "originalExcerpt": "以经完成", "reason": "错别字", "locationHint": "第 2 段"}]})
    typo = repo.load_result(project, "typo")["findings"][0]
    assert (typo["wrongText"], typo["correctText"]) == ("以经", "已经")
    assert typo["locationHint"] == "第 2 段"

    repo.save_result(project, "logic", {"status": "success", "findings": [
        {"title": "前后矛盾", "originalText": "工期 30/60 天",
         "locationHint": "第 5 段", "fallacyReason": "自相矛盾", "suggestion": "统一"}]})
    logic = repo.load_result(project, "logic")["findings"][0]
    assert logic["id"] == "logic-finding-1"
    assert logic["fallacyReason"] == "自相矛盾"


def test_subject_findings_normalization_and_clamp(env):
    _, repo, project, _ = env
    repo.save_result(project, "subject", {"status": "success", "findings": [
        {"id": "s-1", "level": "confirmed-mismatch", "source": "seal",
         "locationKind": "signature", "confidence": 1.7,
         "visualAssetId": "a-1", "expectedSubject": "甲公司", "detectedSubject": "乙公司"},
        {"id": "s-2", "level": "乱写", "source": "text", "locationKind": "乱写",
         "confidence": -3},
    ]})
    findings = repo.load_result(project, "subject")["findings"]
    assert findings[0]["confidence"] == 1.0          # 夹取到 [0,1]
    assert findings[0]["visualAssetId"] == "a-1"
    assert findings[1]["confidence"] == 0.0
    assert findings[1]["level"] == "needs-review"    # 非法 level 兜底
    assert findings[1]["locationKind"] == "body"     # 非法 locationKind 兜底
    assert "visualAssetId" not in findings[1]        # 空值不出现在读数里


def test_findings_bid_document_fallback_to_first_bid(env):
    """参考 :767：`bid_document_id` 为空时回落到本项目第一份投标文件。"""
    _, repo, project, _ = env
    repo.save_document(project, {
        "document_id": "bid-first", "role": "bid", "source": "upload",
        "file_name": "投标.docx", "markdown_path": "rejection-check/bids/a.md",
        "content_hash": "h", "content_chars": 1, "parser_label": None,
        "sort_order": 0, "imported_at": "t", "updated_at": "t"})
    repo.save_result(project, "typo", {"status": "success", "findings": [
        {"wrongText": "错", "correctText": "对", "originalExcerpt": "摘", "reason": "因"}]})
    assert repo.load_result(project, "typo")["findings"][0]["bidDocumentId"] == "bid-first"


def test_save_findings_replaces_previous_rows(env):
    _, repo, project, _ = env
    repo.save_result(project, "rejection", {"status": "success", "findings": _findings_rejection()})
    repo.save_result(project, "rejection", {"status": "success", "findings": [
        {"id": "only", "type": "rejectionItem", "severity": "low", "title": "新的"}]})
    findings = repo.load_result(project, "rejection")["findings"]
    assert [item["id"] for item in findings] == ["only"]
    assert findings[0]["title"] == "新的"


def test_findings_are_project_scoped(env):
    _, repo, project, other = env
    repo.save_result(project, "typo", {"status": "success", "findings": [
        {"wrongText": "甲", "correctText": "甲甲", "originalExcerpt": "x", "reason": "r"}]})
    repo.save_result(other, "typo", {"status": "success", "findings": [
        {"wrongText": "乙", "correctText": "乙乙", "originalExcerpt": "y", "reason": "r"},
        {"wrongText": "丙", "correctText": "丙丙", "originalExcerpt": "z", "reason": "r"}]})
    assert len(repo.load_findings(project, "typo")) == 1
    assert len(repo.load_findings(other, "typo")) == 2


def test_clear_check_results_clears_rows_and_findings(env):
    _, repo, project, _ = env
    repo.save_result(project, "rejection", {"status": "success", "findings": _findings_rejection()})
    repo.save_result(project, "subject", {"status": "success", "findings": [
        {"level": "needs-review", "source": "seal"}]})
    repo.clear_check_results(project)
    assert repo.get_result_row(project, "rejection") is None
    for result_type in ("rejection", "typo", "logic", "subject"):
        assert repo.load_findings(project, result_type) == []


def test_load_result_stats_parsing_and_missing_row(env):
    _, repo, project, _ = env
    assert repo.load_result(project, "logic") == {"status": "idle", "findings": []}
    repo.save_result(project, "logic", {"status": "success", "stats": {"total": 3, "中": 1}})
    assert repo.load_result(project, "logic")["stats"] == {"total": 3, "中": 1}
    # 坏 JSON 不抛（参考 safeJsonParse → undefined）
    repo._db.execute(
        "UPDATE rejection_check_results SET stats_json = ? WHERE project_id = ?",
        ("{不是 JSON", project))
    assert "stats" not in repo.load_result(project, "logic")
