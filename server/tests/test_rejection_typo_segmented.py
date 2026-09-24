"""P4b 收尾：错别字分段检查（runSegmentedTypoCheck :1404-1430 移植验证）。"""

from __future__ import annotations

import asyncio

from app.domain.rejection.typo_check import (
    create_segment_prompt_document,
    dedupe_typo_findings,
)
from app.infra.db.engine import Database
from app.infra.db.migrate import apply_schema
from app.infra.repositories.projects import ProjectRepository
from app.infra.repositories.rejection import RejectionRepository
from app.infra.tasks.rejection_checks import RejectionCheckRunner
from tests.ai_fakes import ScriptedAI

_captured_logs: list[str] = []

LONG_CONTENT = "第一章 资质要求。投标保证金为伍万元整。" * 30


def test_create_segment_prompt_document_uses_segment_content():
    document = {"id": "bid-a", "fileName": "甲.docx", "content": "全文"}
    assert create_segment_prompt_document(
        document, {"content": "本段内容"}) == {
        "id": "bid-a", "fileName": "甲.docx", "content": "本段内容"}


def test_dedupe_typo_findings_prefers_position_key():
    findings = [
        {"bidDocumentId": "bid-a", "wrongText": "甲", "correctText": "乙",
         "position": 5, "originalExcerpt": "摘录一"},
        {"bidDocumentId": "bid-a", "wrongText": "甲", "correctText": "乙",
         "position": 5, "originalExcerpt": "摘录一（重复）"},
        {"bidDocumentId": "bid-a", "wrongText": "甲", "correctText": "乙",
         "position": 9, "originalExcerpt": "摘录二"},
    ]
    assert len(dedupe_typo_findings(findings)) == 2


def test_dedupe_typo_findings_falls_back_without_position():
    findings = [
        {"bidDocumentId": "bid-a", "wrongText": "甲", "correctText": "乙",
         "locationHint": "第 1 行", "originalExcerpt": "摘录"},
        {"bidDocumentId": "bid-a", "wrongText": "甲", "correctText": "乙",
         "locationHint": "第 1 行", "originalExcerpt": "摘录"},
    ]
    assert len(dedupe_typo_findings(findings)) == 1


def test_runner_typo_segmented_path_end_to_end(tmp_path):
    """超长投标文档 → 分段路径（每段一次 collect），normalize 带
    segmentStart/EndOffset 做位置校验，跨段去重后落库。"""
    db = Database(tmp_path / "rejection.sqlite")
    apply_schema(db)
    project = ProjectRepository(db).create("typo-seg")
    repo = RejectionRepository(db)
    repo.save_document(project, {
        "document_id": "bid-a", "role": "bid", "source": "upload",
        "file_name": "甲.docx", "markdown_path": "rejection-check/bids/bid-a.md",
        "content_hash": "h", "content_chars": 10, "parser_label": None,
        "sort_order": 0, "imported_at": "t", "updated_at": "t"})
    md = repo.get_document(project, "bid", "bid-a")["markdown_path"]
    target = tmp_path / "data" / "projects" / project / md
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(LONG_CONTENT, encoding="utf-8")

    ai = ScriptedAI([])
    ai._responses = [
        '{"findings":[{"bidDocumentId":"bid-a","wrongText":"伍万元",'
        '"correctText":"五万元","originalExcerpt":"投标保证金为伍万元整",'
        '"reason":"数字大写错误"}]}']

    class Ctx:
        def __init__(self) -> None:
            self.logs: list[str] = []

        def progress(self, percent, label=None):
            pass

        def log(self, message):
            self.logs.append(message)
            _captured_logs.append(message)

    asyncio.run(RejectionCheckRunner(
        repo, ai, tmp_path / "data",
        config={"context_length_limit": 300})(
            Ctx(), {"projectId": project, "enabledChecks": ["typo"],
                    "signatures": {"typo": "s"},
                    "bidDocuments": [{"id": "bid-a", "fileName": "甲.docx"}]}, None))

    # 分段路径生效（日志出现「正在识别第 N/M 段」）
    assert any("正在识别第" in log for log in _captured_logs)
    findings = repo.load_findings(project, "typo")
    # 「伍万元」在原文出现 30 次，横跨 3 个分段——每段各命中一次（position 不同
    # → 去重键不同），这是参考的**逐位置 findings**语义（参考 :847-853 去重键含 position）
    assert len(findings) == 3
    assert {finding["wrongText"] for finding in findings} == {"伍万元"}
    # position 是临时字段不落表（参考 findings 表无 position 列）；
    # 跨段去重发生在落库前（dedupeTypoFindings 的键含 position）
