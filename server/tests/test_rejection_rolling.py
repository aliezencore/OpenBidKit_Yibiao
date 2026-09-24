"""P4b 收尾：废标项 rolling 滚动审阅（状态机 / 分段 / 选路 / 真链 e2e）。

真值来源 `client/electron/services/rejectionCheckTask.cjs`：
`shouldUseSegmentedPrompt` :387-390、`createBidDocumentSegments` :403-419、
`createBidPackageSegments` :420-438、rolling 状态机 :478-687、
状态摘要 :864-892、定稿索引 :924-941、消息 :1018-1160、runner :1323-1400。
"""

from __future__ import annotations

import asyncio

from app.domain.rejection.rolling_rejection import (
    apply_rolling_rejection_patch,
    build_rejection_final_batch_messages,
    build_rolling_rejection_segment_messages,
    create_bid_package_segments,
    create_empty_rolling_rejection_state,
    create_final_rejection_state_summary,
    create_rejection_final_candidates,
    create_rolling_rejection_state_summary,
    dedupe_rejection_findings,
    normalize_rolling_rejection_patch,
    should_use_segmented_rejection_flow,
)

DOC_A = {"id": "bid-a", "fileName": "甲.docx", "content": "甲公司投标正文。" * 10}
DOC_B = {"id": "bid-b", "fileName": "乙.docx", "content": "乙公司投标正文。" * 10}
INPUT = {"bidDocuments": [DOC_A, DOC_B],
         "invalidBidAndRejectionItems": "检查口径", "customCheckItems": ""}
SMALL_CONFIG = {"context_length_limit": 200}


# ---------------------------------------------------------------- 分段

def test_bid_package_segments_renumbers_across_documents():
    long_a = {"id": "bid-a", "fileName": "甲.docx", "content": "甲公司投标正文。" * 40}
    long_b = {"id": "bid-b", "fileName": "乙.docx", "content": "乙公司投标正文。" * 40}
    segments = create_bid_package_segments([long_a, long_b], SMALL_CONFIG,
                                           0.55)
    assert len(segments) >= 3
    assert [segment["segmentIndex"] for segment in segments] == \
        list(range(1, len(segments) + 1))              # 跨文档整体重编号
    assert all(segment["totalSegments"] == len(segments) for segment in segments)
    assert segments[0]["documentId"] == "bid-a"
    assert segments[0]["content"].startswith("【投标文件1（甲.docx）｜bidDocumentId：bid-a")
    assert "【当前文件片段：第 1/3 段】" in segments[0]["content"]   # 文件内片段号（doc 拆 3 段）


def test_bid_document_segments_carry_offsets():
    """偏移量在**单文件分段**上（参考 :403-419）；包分段（:420-438）不带偏移。"""
    from app.domain.rejection.rolling_rejection import create_bid_document_segments

    segments = create_bid_document_segments(DOC_A, SMALL_CONFIG, 0.55)
    if len(segments) == 1:
        assert segments[0]["startOffset"] == 0
        assert segments[0]["endOffset"] == len(DOC_A["content"])
        return
    for first, second in zip(segments, segments[1:]):
        assert first["endOffset"] == second["startOffset"]   # 偏移连续
    assert segments[-1]["endOffset"] == len(DOC_A["content"])


# ---------------------------------------------------------------- 状态机

def test_patch_assigns_sequential_ids_and_dedupes():
    state = create_empty_rolling_rejection_state()
    patch = normalize_rolling_rejection_patch({
        "evidenceAdds": [{"name": "资质证书章节", "evidence": "第 3 章资质证书"}],
        "pendingRiskAdds": [{"title": "资质待确认", "type": "invalidBid"}],
        "confirmedRiskAdds": [{"bidDocumentId": "bid-a", "title": "报价低于成本",
                               "bidEvidence": "报价表", "riskReason": "低于成本"}],
    }, [DOC_A], "bid-a")
    state = apply_rolling_rejection_patch(state, patch)

    assert [item["id"] for item in state["submittedEvidence"]] == ["evidence_0001"]
    assert state["submittedEvidence"][0]["bidDocumentId"] == "bid-a"  # 段级 fallback
    assert [item["id"] for item in state["pendingRisks"]] == ["risk_0001"]
    assert [item["id"] for item in state["confirmedRisks"]] == ["risk_0002"]

    # 同键重复 → 不新增（序号也不前进）
    state = apply_rolling_rejection_patch(state, normalize_rolling_rejection_patch({
        "evidenceAdds": [{"name": "资质证书章节", "evidence": "第 3 章资质证书"}],
    }, [DOC_A], "bid-a"))
    assert len(state["submittedEvidence"]) == 1
    assert state["nextEvidenceSeq"] == 2


def test_patch_updates_and_resolves():
    state = create_empty_rolling_rejection_state()
    state = apply_rolling_rejection_patch(state, normalize_rolling_rejection_patch({
        "pendingRiskAdds": [{"title": "资质待确认"}],
    }, [DOC_A], "bid-a"))
    risk_id = state["pendingRisks"][0]["id"]

    state = apply_rolling_rejection_patch(state, normalize_rolling_rejection_patch({
        "pendingRiskUpdates": [{"id": risk_id, "bidEvidence": "补充了章节线索"}],
        "pendingRiskResolves": [{"id": risk_id, "reason": "后续片段出现资质证书"}],
    }, [DOC_A], "bid-a"))

    assert state["pendingRisks"] == []                    # 已被移入 resolved
    assert len(state["resolvedRisks"]) == 1
    resolved = state["resolvedRisks"][0]
    assert resolved["id"] == f"resolved_{risk_id}"
    assert resolved["riskId"] == risk_id
    assert resolved["reason"] == "后续片段出现资质证书"


def test_state_summary_takes_recent_items():
    state = create_empty_rolling_rejection_state()
    for index in range(65):
        state = apply_rolling_rejection_patch(state, normalize_rolling_rejection_patch({
            "evidenceAdds": [{"name": f"证据{index}", "evidence": f"内容{index}"}],
        }, [DOC_A], "bid-a"))
    summary = create_rolling_rejection_state_summary(state)
    assert summary["counts"]["submittedEvidence"] == 65
    assert len(summary["submittedEvidence"]) == 60        # 摘要取尾部 60
    assert summary["submittedEvidence"][-1]["name"] == "证据64"


def test_final_candidates_pending_and_confirmed():
    state = create_empty_rolling_rejection_state()
    state = apply_rolling_rejection_patch(state, normalize_rolling_rejection_patch({
        "pendingRiskAdds": [{"title": "待确认"}],
        "confirmedRiskAdds": [{"bidDocumentId": "bid-a", "title": "已确认",
                               "bidEvidence": "证据", "riskReason": "原因"}],
    }, [DOC_A], "bid-a"))
    candidates = create_rejection_final_candidates(state)
    assert [item["candidateStatus"] for item in candidates] == ["confirmed", "pending"]


def test_dedupe_rejection_findings_five_key():
    findings = [{"bidDocumentId": "bid-a", "type": "invalidBid", "title": "t",
                 "bidEvidence": "e", "riskReason": "r"},
                {"bidDocumentId": "bid-a", "type": "invalidBid", "title": "t",
                 "bidEvidence": "e", "riskReason": "r"},
                {"bidDocumentId": "bid-a", "type": "invalidBid", "title": "t",
                 "bidEvidence": "e2", "riskReason": "r"}]
    assert len(dedupe_rejection_findings(findings)) == 2


# ---------------------------------------------------------------- 选路

def test_should_use_segmented_rejection_flow():
    assert should_use_segmented_rejection_flow(INPUT, SMALL_CONFIG) is True
    assert should_use_segmented_rejection_flow(INPUT, {"context_length_limit": 400000}) \
        is False


# ---------------------------------------------------------------- 真链 e2e

def test_runner_rolling_path_end_to_end(tmp_path):
    from app.infra.db.engine import Database
    from app.infra.db.migrate import apply_schema
    from app.infra.repositories.projects import ProjectRepository
    from app.infra.repositories.rejection import RejectionRepository
    from app.infra.tasks.rejection_checks import RejectionCheckRunner
    from tests.ai_fakes import ScriptedAI

    long_doc = {"id": "bid-a", "fileName": "甲.docx", "content": "投标正文。" * 200}
    db = Database(tmp_path / "rejection.sqlite")
    apply_schema(db)
    project = ProjectRepository(db).create("rolling")
    repo = RejectionRepository(db)
    repo.save_document(project, {
        "document_id": "bid-a", "role": "bid", "source": "upload",
        "file_name": "甲.docx", "markdown_path": "rejection-check/bids/bid-a.md",
        "content_hash": "h", "content_chars": 10, "parser_label": None,
        "sort_order": 0, "imported_at": "t", "updated_at": "t"})
    md = repo.get_document(project, "bid", "bid-a")["markdown_path"]
    target = tmp_path / "data" / "projects" / project / md
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(long_doc["content"], encoding="utf-8")

    ai = ScriptedAI([])
    ai._responses = [
        # 逐段审阅 patch ×N（每段一次）：返回一条 confirmed 风险
        *(['{"confirmedRiskAdds":[{"bidDocumentId":"bid-a","type":"invalidBid",'
           '"severity":"高","title":"资质不符","bidEvidence":"未见证书",'
           '"riskReason":"构成无效投标"}]}']),
        # 定稿批次（confirmed 候选只有 1 个 → 1 批）
        '{"findings":[{"bidDocumentId":"bid-a","type":"invalidBid","severity":"高",'
        '"title":"资质不符","bidEvidence":"未见证书","riskReason":"构成无效投标"}]}',
        # 全局合稿
        '{"findings":[{"bidDocumentId":"bid-a","type":"invalidBid","severity":"高",'
        '"title":"资质不符","bidEvidence":"未见证书","riskReason":"构成无效投标"}]}',
    ]

    class Ctx:
        def __init__(self) -> None:
            self.logs: list[str] = []

        def progress(self, percent, label=None):
            pass

        def log(self, message):
            self.logs.append(message)

    asyncio.run(RejectionCheckRunner(
        repo, ai, tmp_path / "data", invalid_items="检查口径",
        config={"context_length_limit": 300})(
            Ctx(), {"projectId": project, "enabledChecks": ["rejection"],
                    "signatures": {"rejection": "s"},
                    "bidDocuments": [{"id": "bid-a", "fileName": "甲.docx"}]}, None))

    assert ai.chat_count >= 3                            # 段审阅 ≥2 + 定稿 + 合稿
    findings = repo.load_findings(project, "rejection")
    assert len(findings) == 1
    assert findings[0]["type"] == "invalidBid"
    assert findings[0]["severity"] == "high"             # 「高」→ high
    db.close()
