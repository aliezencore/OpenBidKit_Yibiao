"""P4b-2：废标项三轮检查（prompt / 归一化 / 真链 runner）。

真值来源 `client/electron/services/rejectionCheckTask.cjs`：
`normalizeFindingType` :66-69、`normalizeSeverity` :71-75、
`buildCommonRejectionCheckMessages` :76-104、三轮 builder :112-195、
`normalizeRejectionCheckFindings` :243-267。
rolling 变体（:392/:420/:680/:1323）未搬，登记：直连三轮无条件使用。
"""

from __future__ import annotations

from app.domain.rejection.rejection_check import (
    build_rejection_check_analysis_messages,
    build_rejection_check_final_messages,
    build_rejection_check_inspection_messages,
    normalize_finding_type,
    normalize_rejection_check_findings,
    normalize_severity,
)

DOC_A = {"id": "bid-a", "fileName": "甲.docx", "content": "投标正文：工期 60 天。"}
INPUT = {"bidDocuments": [DOC_A], "invalidBidAndRejectionItems": "检查项内容",
         "customCheckItems": ""}


def test_normalize_finding_type_maps_chinese_keyword():
    assert normalize_finding_type("invalidBid") == "invalidBid"
    assert normalize_finding_type("涉及无效投标条款") == "invalidBid"   # 含「无效」
    assert normalize_finding_type("废标项") == "rejectionItem"
    assert normalize_finding_type("") == "rejectionItem"


def test_normalize_severity_maps_chinese_keyword():
    assert normalize_severity("high") == "high"
    assert normalize_severity("高风险") == "high"        # 含「高」
    assert normalize_severity("低风险") == "low"          # 含「低」
    assert normalize_severity("medium") == "medium"
    assert normalize_severity("") == "medium"
    assert normalize_severity("HIGH") == "high"           # 先 lower


def test_analysis_messages_contain_common_parts_and_round_one():
    messages = build_rejection_check_analysis_messages(INPUT)
    roles = [m["role"] for m in messages]
    assert roles == ["user", "user", "user"]              # 检查项 / 原文 / 第一轮
    assert "【废标项检查输入 v1｜检查项】" in messages[0]["content"]
    assert "检查项内容" in messages[0]["content"]
    assert "【废标项检查输入 v2｜投标文件原文】" in messages[1]["content"]
    assert "重要限制：当前原文由文本解析得到" in messages[1]["content"]
    assert "【投标文件1｜bidDocumentId：bid-a" in messages[1]["content"]
    assert messages[2]["content"].startswith("【废标项检查任务 v1｜第一轮：分析】")
    assert "请先分析检查范围，不要输出最终风险列表" in messages[2]["content"]


def test_custom_check_items_message_appears_only_when_present():
    with_custom = build_rejection_check_analysis_messages(
        {**INPUT, "customCheckItems": " 自定义关注点 "})
    assert any("【废标项检查输入 v1｜自定义检查项】" in m["content"]
               for m in with_custom)
    assert "自定义关注点" in "".join(m["content"] for m in with_custom)
    without = build_rejection_check_analysis_messages(
        {**INPUT, "customCheckItems": "   "})
    assert not any("自定义检查项" in m["content"] for m in without)


def test_inspection_and_final_messages_carry_previous_rounds():
    inspection = build_rejection_check_inspection_messages(INPUT, "第一轮分析结论")
    assert any("第一轮分析结论" in m["content"] for m in inspection)
    assert any("第二轮：检查" in m["content"] for m in inspection)

    final = build_rejection_check_final_messages(INPUT, "分析结论", "初步列表")
    assert any("分析结论" in m["content"] for m in final)
    assert any("初步列表" in m["content"] for m in final)
    final_task = [m for m in final if "第三轮：补充与定稿" in m["content"]][0]
    assert '"type": "invalidBid"' in final_task["content"]
    assert '{"findings":[]}' in final_task["content"]


def test_normalize_rejection_findings_maps_and_filters():
    payload = {"findings": [
        {"bidDocumentId": "bid-a", "type": "涉及无效投标", "severity": "高",
         "title": "资质不符", "summary": "一句话",
         "bidEvidence": "正文未见资质证书", "riskReason": "可能构成无效投标"},
        {"bidDocumentId": "bid-a", "type": "rejectionItem", "severity": "",
         "title": "缺 summary 与 requirement", "bidEvidence": "证据", "riskReason": "原因"},
        {"bidDocumentId": "bid-a", "title": "缺 evidence 与 riskReason"},  # 丢弃（二者必填）
        {"title": "缺 bidDocumentId（多文档）"},                            # 丢弃
    ]}
    findings = normalize_rejection_check_findings(payload, [DOC_A])
    assert len(findings) == 2
    assert findings[0]["type"] == "invalidBid"
    assert findings[0]["severity"] == "high"
    assert findings[1]["severity"] == "medium"
    assert findings[1]["summary"] == "缺 summary 与 requirement"   # 回落 title
    assert findings[1]["requirement"] == "未明确引用具体检查依据，请人工复核。"
    assert findings[1]["suggestion"] == "请结合招标文件要求和投标文件原文人工复核后处理。"
    assert findings[0]["id"].startswith("rejection_finding_")


def test_runner_rejection_rounds_produce_findings(tmp_path):
    import asyncio

    from app.infra.db.engine import Database
    from app.infra.db.migrate import apply_schema
    from app.infra.repositories.projects import ProjectRepository
    from app.infra.repositories.rejection import RejectionRepository
    from app.infra.tasks.rejection_checks import RejectionCheckRunner
    from tests.ai_fakes import ScriptedAI

    db = Database(tmp_path / "rejection.sqlite")
    apply_schema(db)
    project = ProjectRepository(db).create("rej")
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
    ai._responses = ["第一轮分析", "第二轮初步列表", '{"findings":['
                     '{"bidDocumentId":"bid-a","type":"invalidBid","severity":"高",'
                     '"title":"资质不符","bidEvidence":"未见证书","riskReason":"风险"}]}']

    class Ctx:
        def __init__(self) -> None:
            self.logs: list[str] = []

        def progress(self, percent, label=None):
            pass

        def log(self, message):
            self.logs.append(message)

    asyncio.run(RejectionCheckRunner(
        repo, ai, tmp_path / "data", invalid_items="检查项内容")(
            Ctx(), {"projectId": project, "enabledChecks": ["rejection"],
                    "signatures": {"rejection": "s"},
                    "bidDocuments": [{"id": "bid-a", "fileName": "甲.docx"}]}, None))

    assert ai.chat_count == 3                          # 三轮 = 三次调用
    assert len(ai.calls[0]) == 3                       # 第一轮消息：检查项/原文/任务
    assert "第一轮分析结果" in ai.calls[1][-2]["content"]  # 第二轮带第一轮分析结果（倒数第二条）
    findings = repo.load_findings(project, "rejection")
    assert len(findings) == 1
    assert findings[0]["type"] == "invalidBid"
    assert findings[0]["severity"] == "high"           # 「高」→ high
    assert repo.load_result(project, "rejection")["status"] == "success"
    db.close()
