"""P4b-2：错别字检查（prompt / 原文位置校验 / findings 归一化 / 真链 runner）。

真值来源 `client/electron/services/rejectionCheckTask.cjs`：
`buildTypoCheckMessages` :196-214、`normalizeTypoCheckFindings` :317-349、
`findVerifiedTypoPosition` :268-285、`createVerifiedTypoExcerpt` :294-310、
`createLineLocationHint` :312-315、`getBidDocumentIdFromItem` :41-49。
"""

from __future__ import annotations

import asyncio

from app.domain.rejection.typo_check import (
    build_typo_check_messages,
    create_line_location_hint,
    create_verified_typo_excerpt,
    find_verified_typo_position,
    format_bid_documents_for_prompt,
    get_bid_document_id_from_item,
    normalize_typo_check_findings,
)

DOC_A = {"id": "bid-a", "fileName": "甲.docx",
         "content": "投标保证金为伍万元整。本公司具有相关资质，负责联系：张三。\n第二行内容。"}


# ---------------------------------------------------------------- prompt

def test_build_typo_check_messages_structure():
    messages = build_typo_check_messages([DOC_A])
    assert [m["role"] for m in messages] == ["user", "user"]
    assert messages[0]["content"].startswith("【错别字检查输入 v2｜投标文件原文】")
    assert "【投标文件1｜bidDocumentId：bid-a｜文件名：甲.docx】" in messages[0]["content"]
    assert "--- 投标文件分隔线 ---" not in messages[0]["content"]  # 单文件无分隔线
    assert messages[1]["content"].startswith("【错别字检查任务 v1】")
    assert '{"findings":[]}' in messages[1]["content"]            # 空结果约定


def test_format_bid_documents_joins_with_separator():
    text = format_bid_documents_for_prompt(
        {"bidDocuments": [{"id": "bid-a", "fileName": "甲.docx", "content": "甲内容"},
                          {"id": "bid-b", "content": "乙内容"}]})
    assert "【投标文件1｜bidDocumentId：bid-a｜文件名：甲.docx】\n甲内容" in text
    assert "【投标文件2｜bidDocumentId：bid-b｜文件名：bid-b】\n乙内容" in text  # 缺文件名回退 id
    assert "--- 投标文件分隔线 ---" in text


# ------------------------------------------------- 位置校验 / 摘录 / 行号

def test_find_verified_typo_position_prefers_excerpt():
    content = "前文。投标保证金为伍万元整。后文。"
    position = find_verified_typo_position(content, "伍万元", "投标保证金为伍万元整")
    assert content[position:position + 3] == "伍万元"


def test_find_verified_typo_position_falls_back_to_wrong_text_only():
    content = "摘要里没有的写法，但原文有：伍万元整。"
    assert find_verified_typo_position(content, "伍万元", "不存在的摘要") == \
        content.find("伍万元")
    assert find_verified_typo_position(content, "不存在的字", "") == -1


def test_create_verified_typo_excerpt_and_line_hint():
    content = "第一行\n第二行有投标保证金为伍万元整的内容\n第三行"
    position = content.find("伍万元")
    excerpt = create_verified_typo_excerpt(content, position, "伍万元")
    assert "伍万元" in excerpt
    hint = create_line_location_hint(content, position)
    assert hint == "原文第 2 行附近"


# ------------------------------------------------------------- 归一化

def test_normalize_typo_check_findings_verification_and_dedupe():
    ai_payload = {"findings": [
        {"bidDocumentId": "bid-a", "wrongText": "伍万元", "correctText": "五万元",
         "originalExcerpt": "投标保证金为伍万元整", "reason": "数字大写错误"},
        {"bidDocumentId": "bid-a", "wrongText": "伍万元", "correctText": "五万元",
         "originalExcerpt": "投标保证金为伍万元整", "reason": "重复项应被去重"},
        {"bidDocumentId": "bid-a", "wrongText": "不存在的错字", "correctText": "任何",
         "reason": "原文校验失败应丢弃"},
        {"bidDocumentId": "bid-a", "wrongText": "伍万元", "correctText": "伍万元",
         "reason": "错对相同应丢弃"},
        {"bidDocumentId": "不存在的文档", "wrongText": "伍万元", "correctText": "五万元",
         "reason": "未知文档应丢弃"},
    ]}
    findings = normalize_typo_check_findings(ai_payload, [DOC_A])
    assert len(findings) == 1
    finding = findings[0]
    assert finding["bidDocumentId"] == "bid-a"
    assert finding["wrongText"] == "伍万元"
    assert finding["correctText"] == "五万元"
    assert "投标保证金为伍万元整" in finding["originalExcerpt"]
    assert finding["locationHint"] == "原文第 1 行附近"
    assert finding["id"].startswith("typo_finding_")
    assert "position" in finding                      # 临时字段（不落表）


def test_normalize_allows_snake_case_and_single_document_fallback():
    """AI 返回 snake_case 键 + 省略 bidDocumentId → 单文档时宽容回退（参考 :48-49）。"""
    findings = normalize_typo_check_findings(
        {"findings": [{"wrong_text": "伍万元", "correct_text": "五万元",
                       "excerpt": "投标保证金为伍万元整"}]}, [DOC_A])
    assert len(findings) == 1
    assert findings[0]["bidDocumentId"] == "bid-a"


def test_normalize_truncates_long_text_and_fills_default_reason():
    # 原文里没有 80 个「错」→ 原文位置校验失败，**整条丢弃**
    dropped = normalize_typo_check_findings(
        {"findings": [{"wrongText": "错" * 80, "correctText": "对" * 80}]}, [DOC_A])
    assert dropped == []
    # 长串必须**真实存在于原文**（校验用全文匹配），截断发生在校验之后
    doc = {"id": "bid-b", "fileName": "乙.docx",
           "content": "重要提示：" + "保证金条款需要逐项核对，" * 20}
    long_wrong = "保证金条款需要逐项核对，" * 20
    findings = normalize_typo_check_findings(
        {"findings": [{"bidDocumentId": "bid-b", "wrongText": long_wrong,
                       "correctText": "对"}]}, [doc])
    assert len(findings) == 1
    assert len(findings[0]["wrongText"]) == 60         # 截断到 60
    assert findings[0]["reason"] == "疑似错别字，请结合原文复核。"  # 缺省 reason


# ------------------------------------------------------- 真链 runner（含 e2e）

def test_runner_typo_branch_persists_findings(tmp_path):
    from app.infra.db.engine import Database
    from app.infra.db.migrate import apply_schema
    from app.infra.repositories.projects import ProjectRepository
    from app.infra.repositories.rejection import RejectionRepository
    from app.infra.tasks.rejection_checks import TYPO_LABEL, RejectionCheckRunner
    from tests.ai_fakes import ScriptedAI

    db = Database(tmp_path / "rejection.sqlite")
    apply_schema(db)
    project = ProjectRepository(db).create("typo")
    repo = RejectionRepository(db)
    repo.save_document(project, {
        "document_id": "bid-a", "role": "bid", "source": "upload",
        "file_name": "甲.docx", "markdown_path": "rejection-check/bids/bid-a.md",
        "content_hash": "h", "content_chars": 10, "parser_label": None,
        "sort_order": 0, "imported_at": "t", "updated_at": "t"})
    md = repo.get_document(project, "bid", "bid-a")["markdown_path"]
    target = tmp_path / "data" / "projects" / project / md   # 与 runner 的 data_root 一致
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(DOC_A["content"], encoding="utf-8")

    ai = ScriptedAI([])
    ai._responses = ['{"findings":[{"bidDocumentId":"bid-a","wrongText":"伍万元",'
                     '"correctText":"五万元","originalExcerpt":"投标保证金为伍万元整",'
                     '"reason":"数字大写错误"}]}']
    runner = RejectionCheckRunner(repo, ai, tmp_path / "data")
    ctx = _Ctx()
    asyncio.run(runner(ctx, {
        "projectId": project, "enabledChecks": ["typo"],
        "signatures": {"typo": "sig"}, "bidDocuments": [{"id": "bid-a", "fileName": "甲.docx"}],
    }, None))

    findings = repo.load_findings(project, "typo")
    assert len(findings) == 1
    assert findings[0]["wrongText"] == "伍万元"
    assert findings[0]["bidDocumentId"] == "bid-a"
    result = repo.load_result(project, "typo")
    assert result["status"] == "success"
    assert result["findings"][0]["wrongText"] == "伍万元"
    # 参考 :1707-1708：完成文案「发现 N 项」+ activeFindingId 置首条
    assert result["progressMessage"] == f"{TYPO_LABEL}发现 1 项"
    assert result["activeFindingId"] == findings[0]["id"]
    assert ctx.progress_values[-1] == 100              # 任务收尾 100
    assert 95 in ctx.progress_values                    # 过程中封顶 95
    db.close()


class _Ctx:
    def __init__(self) -> None:
        self.progress_values: list[int] = []
        self.logs: list[str] = []

    def progress(self, percent: int, label: str | None = None) -> None:
        self.progress_values.append(percent)

    def log(self, message: str) -> None:
        self.logs.append(message)
