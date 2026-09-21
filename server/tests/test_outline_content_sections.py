"""Task 5：outline / content 两段 runner 接线测试（P3b）。

覆盖计划 :361-370 的 9 条用例。夹具沿用 T4 的 `.txt` 真文件约定（P2a 的 docx
解析器在 include_images=True 时抛 not-implemented，与 T4 报告登记的环境偏离一致）：
`content_extraction` 用一个写盘桩替代（直接把受控 markdown 落盘并产出
`content_files` 行），从而完全控制 outline/content 段读到的正文，不依赖解析器。

对齐参考实现 svc.cjs:2412-2502（runOutlineAnalysis）与 :2504-2630
（runContentDuplicateAnalysis）。逐字文案/失败语义/R-P3b-4 由 runner 实现保证，
本文件只做可观察断言。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from app.infra.db.engine import Database
from app.infra.db.migrate import apply_schema
from app.infra.repositories.duplicate import (
    DuplicateFileInput,
    DuplicateRepository,
)
from app.infra.tasks.duplicate_runner import (
    CONTENT_EXTRACTION,
    DuplicateAnalysisRunner,
    SectionOutput,
)
from app.infra.tasks.duplicate_sections import (
    build_content_runner,
    build_outline_runner,
    content_dir_for,
)


class FakeCtx:
    """TaskContext 最小替身（同 test_duplicate_runner.py 的 FakeCtx）。"""

    project_id = "p1"

    def __init__(self):
        self.progress_calls: list = []
        self.logs: list[str] = []

    def progress(self, value, message=""):
        self.progress_calls.append((value, message))

    def log(self, message):
        self.logs.append(message)

    def raise_if_cancelled(self):
        return None


@pytest.fixture()
def repo(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    apply_schema(db)
    db.execute("INSERT INTO projects(id, name, created_at) VALUES ('p1','a','now')")
    return DuplicateRepository(db)


# 受控正文：outline 用相同标题结构（两份文件 → 重复组）；content 用相同句子。
OUTLINE_MD = (
    "# 第一章 项目概述\n"
    "## 1.1 项目背景\n"
    "# 第二章 技术方案\n"
    "## 2.1 技术路线\n"
)
DUP_SENTENCE = "本项目的投标总报价为人民币壹佰贰拾叁万肆仟伍佰陆拾柒元整。"


def _write_content_files(data_root, project_id, spec):
    """写 md 落盘并返回 content_extraction 风格的 SectionOutput（不依赖解析器）。

    spec: list of (file_id, file_name, markdown)。markdown 用 bytes 落盘避免 Windows
    把 ``\\n`` 变 ``\\r\\n``（与 T4 的 write_bytes 约定一致）。
    """
    content_dir = content_dir_for(data_root, project_id)
    content_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for file_id, file_name, markdown in spec:
        path = content_dir / f"{file_id}.md"
        path.write_bytes(markdown.encode("utf-8"))
        results.append({
            "file_id": file_id,
            "file_name": file_name,
            "status": "success",
            "content_path": str(path),
            "content_length": len(markdown),
            "content_hash": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        })
    sub = {"status": "success", "completed": len(results), "total": len(results)}
    return SectionOutput(
        stats=sub, rows={"content_files": results}, failed=False, extraction=sub)


def _make_content_extraction_stub(data_root, project_id, spec):
    async def run(section_ctx):
        return _write_content_files(data_root, project_id, spec)
    return run


def _drive(data_root, repo, spec, *, tenders=(), bids=(), force=False):
    """构造注入真实 outline/content runner 的编排器并跑一轮，返回 FakeCtx。

    注意：编排器本身**不**写 `duplicate_check_files`（那是 HTTP 入口
    `save_files` 的职责）。直接驱 runner 测试时，段产出的 `file_id` 外键需要这张
    表里有对应行，否则 `_persist_rows` 的 `replace_*` 会撞 FOREIGN KEY。此处按
    T4 的 `_run` 约定用 `repo.replace_files` 预置文件行（与真实上传等价）。
    """
    # 预置 duplicate_check_files（tender / bid 两组），满足落库外键。
    if tenders:
        repo.replace_files("p1", "tender", [
            DuplicateFileInput(
                file_id=f, file_name=n, file_path=f"/{f}.txt", extension="txt",
                size=1, modified_at="m") for f, n in tenders])
    if bids:
        repo.replace_files("p1", "bid", [
            DuplicateFileInput(
                file_id=f, file_name=n, file_path=f"/{f}.txt", extension="txt",
                size=2, modified_at="m") for f, n in bids])

    sections = dict(__import__(
        "app.infra.tasks.duplicate_runner", fromlist=["_stub_sections"])._stub_sections())
    sections[CONTENT_EXTRACTION] = _make_content_extraction_stub(data_root, "p1", spec)
    sections["outline"] = build_outline_runner(data_root)
    sections["content"] = build_content_runner(data_root)
    runner = DuplicateAnalysisRunner(repo=repo, sections=sections)
    payload = {
        "tenderFiles": [
            {"file_id": f, "file_name": n, "file_path": f"/{f}.txt",
             "size": 1, "modified_at": "m"} for f, n in tenders],
        "bidFiles": [
            {"file_id": f, "file_name": n, "file_path": f"/{f}.txt",
             "size": 2, "modified_at": "m"} for f, n in bids],
        "force": force,
    }
    ctx = FakeCtx()
    asyncio.run(runner(ctx, payload, None))
    return ctx


def _rows(db, table, project_id="p1"):
    return db.query(f"SELECT * FROM {table} WHERE project_id = ?", (project_id,))


# ----------------------------------------------------------- 用例 1：outline 组与落库

def test_outline_section_extracts_groups_and_persists(repo, tmp_path):
    """两份投标含相同目录标题 → outline_items（scoped id、from_tender）/ outline_groups
    （score=1 path 组）/ outline_pairwise（score/risk）全部落库可读；且 metadata 段
    stats 的 content_extraction 子统计被填实（Task 5 评审 B 补强）。
    """
    spec = [
        ("b1", "b1.txt", OUTLINE_MD),
        ("b2", "b2.txt", OUTLINE_MD),
    ]
    _drive(tmp_path, repo, spec, bids=[("b1", "b1.txt"), ("b2", "b2.txt")])

    items = _rows(repo._db, "duplicate_check_outline_items")
    assert len(items) >= 8  # 两份文件各 4 个标题项
    # scoped id（file_id::Oxxxxx）且 from_tender 为 0
    for row in items:
        assert "::" in row["item_id"]
        assert row["file_id"] in ("b1", "b2")
        assert row["from_tender"] == 0

    groups = _rows(repo._db, "duplicate_check_outline_groups")
    assert groups, "应产出至少一个重复组"
    # 存在 score=1 的 path 重复组，且 file_ids 含两份投标
    path_group = next(g for g in groups if g["type"] == "duplicate" and g["score"] == 1)
    assert set(json.loads(path_group["file_ids_json"])) == {"b1", "b2"}

    pairwise = _rows(repo._db, "duplicate_check_outline_pairwise")
    assert len(pairwise) == 1
    assert isinstance(pairwise[0]["score"], (int, float))
    assert pairwise[0]["risk"] in ("high", "medium", "low", "none")

    # metadata stats 的 content_extraction 子统计被填实
    meta = repo.get_sections("p1")["metadata"]["stats"]
    assert meta["content_extraction"]["completed"] == 2
    assert meta["content_extraction"]["total"] == 2


# ----------------------------------------------------------- 用例 2：白名单失败继续

def test_outline_section_tender_whitelist_failure_continues(repo, tmp_path):
    """tender 的 content 条目缺失 → 广播逐字文案、分析继续（outline 仍产出 bid 项）。"""
    # 仅写 bid 的 content；tender 在 payload 存在但无 content 条目
    spec = [("b1", "b1.txt", OUTLINE_MD)]
    ctx = _drive(tmp_path, repo, spec,
                 tenders=[("t1", "t1.txt")], bids=[("b1", "b1.txt")])

    assert any("招标文件句子白名单生成失败，继续对比投标文件目录：" in log
               for log in ctx.logs)

    # 分析继续：bid 的目录项仍落库
    items = _rows(repo._db, "duplicate_check_outline_items")
    assert {row["file_id"] for row in items} == {"b1"}


# ----------------------------------------------------------- 用例 3：单文件错误 → failed

def test_outline_section_file_error_marks_failed_and_keeps_rest(repo, tmp_path):
    """一个 bid 的 content 条目缺失 → 该文件 error 行 + failed=True，其余文件 items 保留。"""
    spec = [("b1", "b1.txt", OUTLINE_MD)]  # b2 缺失 content
    _drive(tmp_path, repo, spec, bids=[("b1", "b1.txt"), ("b2", "b2.txt")])

    state = repo.get_sections("p1")["outline"]
    assert state["status"] == "error"
    assert state["message"] == "部分文件目录分析失败"

    items = _rows(repo._db, "duplicate_check_outline_items")
    # 只有 b1 的项落库（b2 错误行 items 为空）
    assert {row["file_id"] for row in items} == {"b1"}


# ----------------------------------------------------------- 用例 4：content 重复句落库

def test_content_section_duplicate_sentences_persisted(repo, tmp_path):
    """两份投标含同一句 → content_duplicates 一行、file_ids 两元素、
    content_occurrences 两行；failed=False（恒 success）。"""
    spec = [
        ("b1", "b1.txt", DUP_SENTENCE),
        ("b2", "b2.txt", DUP_SENTENCE),
    ]
    _drive(tmp_path, repo, spec, bids=[("b1", "b1.txt"), ("b2", "b2.txt")])

    dup = _rows(repo._db, "duplicate_check_content_duplicates")
    assert len(dup) == 1
    assert len(json.loads(dup[0]["file_ids_json"])) == 2

    occ = _rows(repo._db, "duplicate_check_content_occurrences")
    assert len(occ) == 2
    assert all(o["occurrence_count"] >= 1 for o in occ)

    assert repo.get_sections("p1")["content"]["status"] == "success"


# ----------------------------------------------------------- 用例 5：招标句不算重复

def test_content_section_tender_sentences_are_not_duplicates(repo, tmp_path):
    """招标含句 S、两投标都含 S → matcher 命中 → S 不出现在 duplicates。"""
    spec = [
        ("t1", "t1.txt", DUP_SENTENCE),
        ("b1", "b1.txt", DUP_SENTENCE),
        ("b2", "b2.txt", DUP_SENTENCE),
    ]
    _drive(tmp_path, repo, spec,
           tenders=[("t1", "t1.txt")], bids=[("b1", "b1.txt"), ("b2", "b2.txt")])

    dup = _rows(repo._db, "duplicate_check_content_duplicates")
    assert len(dup) == 0

    stats = repo.get_sections("p1")["content"]["stats"]
    assert stats["tender_matched_sentence_count"] >= 2  # 两个投标句都被招标命中


# ----------------------------------------------------------- 用例 6：content 单文件错误 → success

def test_content_section_file_error_keeps_status_success(repo, tmp_path):
    """一个 bid 的 content 条目缺失 → 该文件被跳过（广播）、failed=False、
    其余两个文件的重复句照常产出。"""
    spec = [
        ("b1", "b1.txt", DUP_SENTENCE),
        ("b2", "b2.txt", DUP_SENTENCE),
        # b3 缺失 content
    ]
    ctx = _drive(tmp_path, repo, spec,
                 bids=[("b1", "b1.txt"), ("b2", "b2.txt"), ("b3", "b3.txt")])

    assert any("b3.txt 正文比对失败：" in log for log in ctx.logs)
    assert repo.get_sections("p1")["content"]["status"] == "success"

    dup = _rows(repo._db, "duplicate_check_content_duplicates")
    assert len(dup) == 1
    assert len(json.loads(dup[0]["file_ids_json"])) == 2  # 仅 b1、b2


# ----------------------------------------------------------- 用例 7：running 行无签名

def test_section_progress_running_rows_without_signature(repo, tmp_path, monkeypatch):
    """段状态行在运行中以 running 写出且 signature 为 NULL（P3a 裁定 P12）；
    终态后为 success 且带签名。"""
    spec = [("b1", "b1.txt", OUTLINE_MD), ("b2", "b2.txt", OUTLINE_MD)]
    bids = [("b1", "b1.txt"), ("b2", "b2.txt")]

    seen = []
    orig = repo.save_section

    def recorder(project_id, section, *, status, progress, message, updated_at, **extra):
        seen.append((section, status, "signature" in extra))
        return orig(project_id, section, status=status, progress=progress,
                    message=message, updated_at=updated_at, **extra)

    monkeypatch.setattr(repo, "save_section", recorder)
    _drive(tmp_path, repo, spec, bids=bids)

    # 至少一条 running 写出且未携带签名（即 signature 列保持 NULL）
    running_without_sig = [
        (sec, st, has_sig) for (sec, st, has_sig) in seen
        if st == "running" and not has_sig]
    assert running_without_sig, "应存在不带签名的 running 段状态行"

    final = repo.get_sections("p1")["outline"]
    assert final["status"] == "success"
    assert final["signature"] is not None


# ----------------------------------------------------------- 用例 8：_persist_rows 已退役占位

def test_persist_rows_is_implemented_now(repo):
    """`_persist_rows` 对带 rows 的产出不再抛 NotImplementedError（P3a 占位正式退役）。"""
    runner = DuplicateAnalysisRunner(repo=repo)
    # content_files 落库有外键约束：先用 T4 约定的 replace_files 预置 file_id=x。
    repo.replace_files("p1", "bid", [DuplicateFileInput(
        file_id="x", file_name="x.txt", file_path="/x.txt", extension="txt",
        size=1, modified_at="m")])
    out = SectionOutput(rows={"content_files": [
        {"file_id": "x", "status": "success", "content_path": "/tmp/x.md",
         "content_length": 1}]})
    # 不应抛 NotImplementedError
    runner._persist_rows("p1", "metadata", out)
    # 空 rows 直接 return（P3a 行为不变）
    runner._persist_rows("p1", "metadata", SectionOutput())


# ----------------------------------------------------------- 用例 9：终态文案来自 output

def test_terminal_message_from_output(repo, tmp_path):
    """outline 全成 → 终态 message「目录分析完成」（非 P3a 统一「分析完成」）；
    部分失败 →「部分文件目录分析失败」。"""
    # 全成功
    spec = [("b1", "b1.txt", OUTLINE_MD), ("b2", "b2.txt", OUTLINE_MD)]
    _drive(tmp_path, repo, spec, bids=[("b1", "b1.txt"), ("b2", "b2.txt")])
    assert repo.get_sections("p1")["outline"]["message"] == "目录分析完成"

    # 部分失败（b2 缺失 content）：force 重跑，避免幂等命中上一轮的 success 签名。
    spec2 = [("b1", "b1.txt", OUTLINE_MD)]
    _drive(tmp_path, repo, spec2, bids=[("b1", "b1.txt"), ("b2", "b2.txt")], force=True)
    assert repo.get_sections("p1")["outline"]["message"] == "部分文件目录分析失败"
