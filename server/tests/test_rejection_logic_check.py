"""P4b-2：逻辑谬误检查（prompt / 归一化 / 真链 runner）。

真值来源 `client/electron/services/rejectionCheckTask.cjs`：
`buildLogicCheckMessages` :218-240、`normalizeLogicCheckFindings` :352-373。
"""

from __future__ import annotations

import asyncio

from app.domain.rejection.logic_check import (
    build_logic_check_messages,
    normalize_logic_check_findings,
)

DOC_A = {"id": "bid-a", "fileName": "甲.docx",
         "content": "工期：60 天。项目经理：张三。\n后文提到工期为 90 天。"}


def test_build_logic_check_messages_structure():
    messages = build_logic_check_messages([DOC_A])
    assert [m["role"] for m in messages] == ["user", "user"]
    assert messages[0]["content"].startswith("【逻辑谬误检查输入 v2｜投标文件原文】")
    assert messages[1]["content"].startswith("【逻辑谬误检查任务 v1】")
    assert "全文前后不一致" in messages[1]["content"]          # 检查范围第 2 条
    assert '{"findings":[]}' in messages[1]["content"]


def test_normalize_logic_findings_required_fields_and_defaults():
    # **两个文档**：缺 bidDocumentId 无法回退 → 丢弃（单文档时会宽容回退，见下一条用例）
    docs = [DOC_A, {"id": "bid-b", "fileName": "乙.docx", "content": "乙内容"}]
    payload = {"findings": [
        {"bidDocumentId": "bid-a", "title": "工期前后不一致",
         "originalText": "工期：60 天", "locationHint": "第 1 段",
         "fallacyReason": "与后文 90 天矛盾", "suggestion": "统一为 60 天"},
        {"title": "缺 bidDocumentId", "fallacyReason": "r"},           # 丢弃
        {"bidDocumentId": "bid-a", "fallacyReason": "缺 title"},       # 丢弃
        {"bidDocumentId": "bid-a", "title": "缺 reason"},              # 丢弃
        {"bidDocumentId": "bid-a", "title": "只有必填",
         "fallacyReason": "r"},                                        # 缺省值填充
    ]}
    findings = normalize_logic_check_findings(payload, docs)
    assert len(findings) == 2
    assert findings[0]["suggestion"] == "统一为 60 天"
    assert findings[1]["originalText"] == "未提供明确原文摘录，请结合位置线索复核。"
    assert findings[1]["locationHint"] == "未明确具体位置，请结合原文摘录复核。"
    assert findings[1]["suggestion"] == "请结合投标文件上下文人工复核后修改。"
    assert findings[1]["id"].startswith("logic_finding_")


def test_normalize_logic_falls_back_to_only_document():
    """参考 :48-49：**只有一个文档**时，缺 bidDocumentId 的条目回退到它（宽容语义）。"""
    payload = {"findings": [{"title": "t", "fallacyReason": "r"}]}
    findings = normalize_logic_check_findings(payload, [DOC_A])
    assert len(findings) == 1
    assert findings[0]["bidDocumentId"] == "bid-a"


def test_normalize_logic_truncates_title_to_80_and_dedupes():
    payload = {"findings": [
        {"bidDocumentId": "bid-a", "title": "标" * 100, "fallacyReason": "同因"},
        {"bidDocumentId": "bid-a", "title": "标" * 100, "fallacyReason": "同因"},
        {"bidDocumentId": "bid-a", "summary": "被回落成标题",
         "fallacyReason": "另一因"},
    ]}
    findings = normalize_logic_check_findings(payload, [DOC_A])
    assert len(findings) == 2                          # 前两条去重为一条
    assert len(findings[0]["title"]) == 80
    assert findings[1]["title"] == "被回落成标题"       # summary → title 的回落


def test_normalize_logic_falls_back_between_key_variants():
    payload = {"issues": [                              # issues 也在候选键里（参考 :358）
        {"document_id": "bid-a", "title": "t", "evidence": "原文证据",
         "location": "表 2", "riskReason": "风险原因", "recommendation": "建议"},
    ]}
    findings = normalize_logic_check_findings(payload, [DOC_A])
    assert len(findings) == 1
    assert findings[0]["originalText"] == "原文证据"
    assert findings[0]["fallacyReason"] == "风险原因"


def test_runner_logic_branch_persists_findings(tmp_path):
    from app.infra.db.engine import Database
    from app.infra.db.migrate import apply_schema
    from app.infra.repositories.projects import ProjectRepository
    from app.infra.repositories.rejection import RejectionRepository
    from app.infra.tasks.rejection_checks import RejectionCheckRunner
    from tests.ai_fakes import ScriptedAI

    db = Database(tmp_path / "rejection.sqlite")
    apply_schema(db)
    project = ProjectRepository(db).create("logic")
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
    ai._responses = ['{"findings":[{"bidDocumentId":"bid-a","title":"工期不一致",'
                     '"originalText":"60 天","locationHint":"第 1 段",'
                     '"fallacyReason":"与 90 天矛盾","suggestion":"统一"}]}']

    class Ctx:
        def __init__(self) -> None:
            self.progress_values: list[int] = []

        def progress(self, percent, label=None):
            self.progress_values.append(percent)

        def log(self, message):
            pass

    asyncio.run(RejectionCheckRunner(repo, ai, tmp_path / "data")(
        Ctx(), {"projectId": project, "enabledChecks": ["logic"],
                "signatures": {"logic": "s"},
                "bidDocuments": [{"id": "bid-a", "fileName": "甲.docx"}]}, None))

    findings = repo.load_findings(project, "logic")
    assert len(findings) == 1
    assert findings[0]["title"] == "工期不一致"
    assert findings[0]["fallacyReason"] == "与 90 天矛盾"
    assert repo.load_result(project, "logic")["status"] == "success"
    db.close()
