"""P4b-3：主体正文检查（滚动状态机 + spec §8.4 过滤 + §10.1 level 门槛）。

真值来源 `client/electron/services/subjectCheckTask.cjs`（:146-231 / :233-349 / :351-412）
与 `subjectNameUtils.cjs`（:24-31 / :111-115）。
"""

from __future__ import annotations

import asyncio

from app.domain.rejection.subject_body import (
    apply_subject_body_patch,
    build_subject_body_final_messages,
    build_subject_body_segment_messages,
    create_empty_subject_body_state,
    create_subject_body_final_candidates,
    create_subject_body_state_summary,
    looks_like_company_name,
    locate_original_text,
    matches_allowed_subject,
    normalize_subject_body_findings,
)

DOC_A = {"id": "bid-a", "fileName": "甲.docx",
         "content": "投标正文：乙方建设公司承担施工。工期 60 天。"}


# ------------------------------------------------------------ 名字工具

def test_matches_allowed_subject_and_company_heuristic():
    assert matches_allowed_subject(" 甲公司 ", "甲公司", ["甲"]) is True
    assert matches_allowed_subject("甲", "甲公司", ["甲"]) is True
    assert matches_allowed_subject("乙方建设公司", "甲公司", ["甲"]) is False
    assert matches_allowed_subject("", "甲公司") is False
    assert looks_like_company_name("乙方建设公司") is True
    assert looks_like_company_name("我公司") is False          # 代称不算公司名
    assert looks_like_company_name("") is False


def test_locate_original_text_ignores_whitespace():
    assert locate_original_text("工期 60 天。\n第二行。", "工期 60 天。") is True
    assert locate_original_text("工期60天。", "工期 60 天。") is True   # 忽略空白差异
    assert locate_original_text("工期 60 天。", "不存在的摘录") is False


# ------------------------------------------------------------ 滚动状态机

def test_state_patch_dedupes_and_clamps():
    state = create_empty_subject_body_state()
    state = apply_subject_body_patch(state, {
        "subject_summary": ["已确认甲公司为投标人", "已确认甲公司为投标人"],  # 重复不追加
        "role_summary": ["第三方：代理机构"],
        "candidates": [{"bid_document_id": "bid-a", "role": "bidder",
                        "detected_subject": "乙方建设公司", "original_text": "原文",
                        "name_confidence": 1.7, "role_confidence": -1},
                       {"bid_document_id": "bid-a", "detected_subject": "乙方建设公司",
                        "original_text": "原文"}],                       # 同键去重
    })
    assert state["subjectSummary"] == ["已确认甲公司为投标人"]
    assert state["roleSummary"] == ["第三方：代理机构"]
    assert len(state["candidates"]) == 1                 # 四元组键去重
    candidate = state["candidates"][0]
    assert candidate["nameConfidence"] == 1.0            # 夹取到 [0,1]
    assert candidate["roleConfidence"] == 0.0
    assert candidate["role"] == "bidder"                 # 去重保留**第一条**（bidder）
    assert candidate["detectedSubject"] == "乙方建设公司"


def test_state_summary_tail_40():
    state = create_empty_subject_body_state()
    state = apply_subject_body_patch(state, {
        "subject_summary": [f"条目{i}" for i in range(45)]})
    summary = create_subject_body_state_summary(state)
    assert len(summary["subject_summary"]) == 40
    assert summary["subject_summary"][-1] == "条目44"
    assert summary["candidate_count"] == 0


# ------------------------------------------------------------ 归一化（§8.4/§10.1）

def _payload(findings: list[dict]) -> dict:
    return {"findings": findings}


def test_subject_normalize_four_filters():
    good = {"bid_document_id": "bid-a", "role": "bidder",
            "detected_subject": "乙方建设公司",
            "original_text": "投标正文：乙方建设公司承担施工。",
            "title": "主体不一致", "name_confidence": 0.9, "role_confidence": 0.9}
    # §8.4-1（文档不存在）/§8.4-2（摘录不在原文）/§8.4-3（主体不在摘录内）各一条，全部丢弃
    payload = _payload([
        {"bid_document_id": "bid-x", "detected_subject": "某公司",
         "original_text": "投标正文"},
        {"bid_document_id": "bid-a", "detected_subject": "某公司",
         "original_text": "原文里没有这句"},
        {"bid_document_id": "bid-a", "detected_subject": "",
         "original_text": "投标正文：乙方建设公司承担施工。"},
        good,
    ])
    findings = normalize_subject_body_findings(payload, bid_documents=[DOC_A],
                                               official_name="甲公司", aliases=["甲"])
    assert len(findings) == 1
    assert findings[0]["level"] == "confirmed-mismatch"   # bidder + 0.9/0.9 ≥ 0.85
    assert findings[0]["source"] == "text"
    assert findings[0]["expectedSubject"] == "甲公司"
    assert findings[0]["id"] == "subject-text-1"          # **序号 id，从 1 起**


def test_subject_normalize_official_name_is_dropped():
    """§8.4-4：正式主体名称出现在原文属正常，必须删除。"""
    payload = _payload([{"bid_document_id": "bid-a", "role": "bidder",
                         "detected_subject": "甲公司",
                         "original_text": "投标正文：甲公司承担施工。"}])
    assert normalize_subject_body_findings(
        payload, bid_documents=[DOC_A], official_name="甲公司", aliases=["甲"]) == []


def test_subject_normalize_third_party_and_unclear_rules():
    excerpt = "投标正文：乙方建设公司承担施工。"
    third_party = _payload([{"bid_document_id": "bid-a", "role": "third-party",
                             "detected_subject": "乙方建设公司",
                             "original_text": excerpt}])
    assert normalize_subject_body_findings(
        third_party, bid_documents=[DOC_A], official_name="甲公司") == []  # §8.2 third-party

    unclear_company = _payload([{"bid_document_id": "bid-a", "role": "unclear",
                                 "detected_subject": "乙方建设公司",
                                 "original_text": excerpt,
                                 "name_confidence": 0.5, "role_confidence": 0.5}])
    assert len(normalize_subject_body_findings(
        unclear_company, bid_documents=[DOC_A], official_name="甲公司")) == 1  # 像公司名 → 保留

    unclear_pronoun = _payload([{"bid_document_id": "bid-a", "role": "unclear",
                                 "detected_subject": "我公司",
                                 "original_text": excerpt}])
    assert normalize_subject_body_findings(
        unclear_pronoun, bid_documents=[DOC_A], official_name="甲公司") == []  # 代称非公司名


def test_subject_normalize_needs_review_below_threshold():
    payload = _payload([{"bid_document_id": "bid-a", "role": "bidder",
                         "detected_subject": "乙方建设公司",
                         "original_text": "投标正文：乙方建设公司承担施工。",
                         "name_confidence": 0.6, "role_confidence": 0.9}])
    findings = normalize_subject_body_findings(
        payload, bid_documents=[DOC_A], official_name="甲公司")
    assert findings[0]["level"] == "needs-review"          # 双置信度未同时 ≥0.85
    assert findings[0]["confidence"] == 0.6                # 取较小值


def test_subject_normalize_aliases_allow_official():
    payload = _payload([{"bid_document_id": "bid-a", "role": "bidder",
                         "detected_subject": "甲",
                         "original_text": "投标正文：甲承担施工。"}])
    assert normalize_subject_body_findings(
        payload, bid_documents=[DOC_A], official_name="甲公司",
        aliases=["甲"]) == []                              # 允许简称 → 删除


# ------------------------------------------------------------ 真链 runner

def test_runner_subject_branch_persists_findings(tmp_path):
    from app.infra.db.engine import Database
    from app.infra.db.migrate import apply_schema
    from app.infra.repositories.projects import ProjectRepository
    from app.infra.repositories.rejection import RejectionRepository
    from app.infra.tasks.rejection_checks import RejectionCheckRunner
    from tests.ai_fakes import ScriptedAI

    db = Database(tmp_path / "rejection.sqlite")
    apply_schema(db)
    project = ProjectRepository(db).create("subject")
    repo = RejectionRepository(db)
    repo.save_document(project, {
        "document_id": "bid-a", "role": "bid", "source": "upload",
        "file_name": "甲.docx", "markdown_path": "rejection-check/bids/bid-a.md",
        "content_hash": "h", "content_chars": 10, "parser_label": None,
        "sort_order": 0, "imported_at": "t", "updated_at": "t"})
    md = repo.get_document(project, "bid", "bid-a")["markdown_path"]
    target = tmp_path / "data" / "projects" / project / md
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(DOC_A["content"], encoding="utf-8")

    ai = ScriptedAI([])
    ai._responses = [
        '{"subject_summary":[],"role_summary":[],"candidates":['
        '{"bid_document_id":"bid-a","role":"bidder","detected_subject":"乙方建设公司",'
        '"location_kind":"body","title":"主体不一致",'
        '"original_text":"投标正文：乙方建设公司承担施工。",'
        '"name_confidence":0.9,"role_confidence":0.9}]}',
        '{"findings":[{"bid_document_id":"bid-a","role":"bidder",'
        '"detected_subject":"乙方建设公司","location_kind":"body","title":"主体不一致",'
        '"original_text":"投标正文：乙方建设公司承担施工。",'
        '"name_confidence":0.9,"role_confidence":0.9}]}']

    class Ctx:
        def __init__(self) -> None:
            self.logs: list[str] = []

        def progress(self, percent, label=None):
            pass

        def log(self, message):
            self.logs.append(message)

    asyncio.run(RejectionCheckRunner(
        repo, ai, tmp_path / "data", official_name="甲公司", aliases=["甲"])(
            Ctx(), {"projectId": project, "enabledChecks": ["subject"],
                    "signatures": {"subject": "s"},
                    "bidDocuments": [{"id": "bid-a", "fileName": "甲.docx"}]}, None))

    findings = repo.load_findings(project, "subject")
    assert len(findings) == 1
    assert findings[0]["level"] == "confirmed-mismatch"
    assert findings[0]["source"] == "text"
    assert findings[0]["detectedSubject"] == "乙方建设公司"
    assert repo.load_result(project, "subject")["status"] == "success"
    db.close()
