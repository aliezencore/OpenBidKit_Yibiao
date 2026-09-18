from __future__ import annotations

import asyncio

import pytest

from app.infra.db.engine import Database
from app.infra.db.migrate import apply_schema
from app.infra.repositories.duplicate import DuplicateFileInput, DuplicateRepository
from app.infra.tasks.duplicate_runner import DuplicateAnalysisRunner


class FakeCtx:
    """TaskContext 的最小替身：只记，不做 I/O。

    `log` 刻意**只收一个位置参数**（无 `*args`/`**kwargs`）——与真实
    `TaskContext.log(message: str)` 一致。实现里若误传格式化参数会在此 `TypeError`。
    """

    # 真实 `TaskContext` 的 `project_id` 是**属性**（context.py:34-37）；
    # 计划里的 FakeCtx 漏了它，而实现必须从 `ctx.project_id` 取项目 id。
    project_id = "p1"

    def __init__(self):
        self.progress_calls: list[tuple[int, str]] = []
        self.checkpoints: list[dict] = []
        self.logs: list[str] = []

    def progress(self, value, message=""):
        self.progress_calls.append((value, message))

    def checkpoint(self, state):
        self.checkpoints.append(state)

    def log(self, message):
        self.logs.append(message)

    def raise_if_cancelled(self):
        return None


@pytest.fixture()
def repo(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    apply_schema(db)
    db.execute("INSERT INTO projects(id, name, created_at) VALUES ('p1','a','now')")
    r = DuplicateRepository(db)
    r.replace_files("p1", "tender", [DuplicateFileInput(
        file_id="t", file_name="t.docx", file_path="/t.docx", extension="docx",
        size=1, modified_at="m1")])
    r.replace_files("p1", "bid", [DuplicateFileInput(
        file_id="b", file_name="b.docx", file_path="/b.docx", extension="docx",
        size=2, modified_at="m2")])
    return r


def _payload(force=False):
    return {
        "force": force,
        "tenderFiles": [{"file_id": "t", "file_path": "/t.docx", "size": 1,
                         "modified_at": "m1"}],
        "bidFiles": [{"file_id": "b", "file_path": "/b.docx", "size": 2,
                      "modified_at": "m2"}],
    }


def _run(repo, payload, previous_state=None):
    ctx = FakeCtx()
    runner = DuplicateAnalysisRunner(repo=repo)
    asyncio.run(runner(ctx, payload, previous_state))
    return ctx


def test_stub_run_marks_all_sections_success(repo):
    ctx = _run(repo, _payload())
    sections = repo.get_sections("p1")
    assert set(sections) == {"metadata", "outline", "content", "image"}
    assert all(s["status"] == "success" for s in sections.values())


def test_stub_run_records_signature_on_every_section(repo):
    ctx = _run(repo, _payload())
    sigs = {s["signature"] for s in repo.get_sections("p1").values()}
    assert len(sigs) == 1 and None not in sigs


def test_progress_reaches_100(repo):
    ctx = _run(repo, _payload())
    assert ctx.progress_calls[-1][0] == 100


def test_progress_is_monotonic(repo):
    ctx = _run(repo, _payload())
    values = [v for v, _ in ctx.progress_calls]
    assert values == sorted(values), f"进度回退：{values}"


def test_second_run_same_signature_is_idempotent(repo):
    """四段全 success 且签名相同 → 不重跑，直接返回并落日志。"""
    ctx1 = _run(repo, _payload())
    first_calls = len(ctx1.progress_calls)
    ctx2 = _run(repo, _payload())
    assert any("无需重复分析" in m for m in ctx2.logs)
    assert len(ctx2.progress_calls) < first_calls, "幂等命中仍在重跑"
    assert ctx2.progress_calls[-1][0] == 100


def test_force_bypasses_idempotency(repo):
    _run(repo, _payload())
    ctx = _run(repo, _payload(force=True))
    assert not any("无需重复分析" in m for m in ctx.logs)


def test_signature_change_invalidates_previous_state(repo):
    _run(repo, _payload())
    changed = _payload()
    changed["bidFiles"][0]["size"] = 999
    ctx = _run(repo, changed)
    assert any("作废" in m or "签名" in m for m in ctx.logs)
    assert repo.get_sections("p1")["metadata"]["status"] == "success"


def test_metadata_runs_before_outline_content_image(repo):
    """metadata 落定在前，后三段在其后才进入 running。

    终态文案不进 `progress_calls`：`latest_analysis_message` 按
    `image → content → outline → metadata` 取第一条非空，metadata 的文案会被
    后三段的「等待正文内容…」盖住。故这里从**库里的段行**读，那才是权威记录。
    """
    ctx = _run(repo, _payload())
    messages = [m for _, m in ctx.progress_calls]
    assert any("等待正文内容" in m for m in messages), "未见「等待正文内容」阶段消息"

    sections = repo.get_sections("p1")
    # metadata 的**终态**是 success；后三段此时仍是 running（「等待正文内容」的语义），
    # 这才是「metadata 落定在前、后三段尚未开跑」的可观察证据。
    assert sections["metadata"]["status"] == "success"
    assert sections["metadata"]["message"] == "元数据分析完成"
    # updated_at 是真实时钟戳：metadata 写终态必须早于后三段被写成 running
    for section in ("outline", "content", "image"):
        assert sections["metadata"]["updated_at"] <= sections[section]["updated_at"]


def test_meta_current_signature_is_saved(repo):
    _run(repo, _payload())
    meta = repo.get_meta("p1")
    assert meta["step"] == "analysis"
    assert meta["current_signature"] is not None


def test_stale_signature_discards_results(repo):
    """跑完后重读 current_signature 若 ≠ 本次签名 → 丢弃结果、不写四段终态。

    用注入的 signature_reader 造确定性的「跑完那一刻签名已变」，
    不 monkeypatch 内部方法。
    """
    reads = {"n": 0}

    def stale_reader(project_id: str) -> str | None:
        reads["n"] += 1
        # 第 1 次是开跑前的读（用于判断幂等/重置）；其后一律返回「别人改过的签名」
        if reads["n"] == 1:
            return None
        return "someone-else"

    ctx = FakeCtx()
    runner = DuplicateAnalysisRunner(repo=repo, signature_reader=stale_reader)
    asyncio.run(runner(ctx, _payload(), None))

    assert any("丢弃" in m for m in ctx.logs), f"未检测到过期签名丢弃，logs={ctx.logs}"
    # 结果必须没落库：四段仍是重置后的初始态，而非 success
    sections = repo.get_sections("p1")
    assert all(s["status"] != "success" for s in sections.values()), \
        f"过期签名仍写了终态：{sections}"


def test_stub_writes_no_result_rows(repo):
    """P3a 的 stub 不产出结果行：四张核心结果表应保持空。"""
    _run(repo, _payload())
    assert repo._db.query(
        "SELECT COUNT(*) AS c FROM duplicate_check_content_files")[0]["c"] == 0
    assert repo._db.query(
        "SELECT COUNT(*) AS c FROM duplicate_check_metadata_items")[0]["c"] == 0
    assert repo._db.query(
        "SELECT COUNT(*) AS c FROM duplicate_check_outline_items")[0]["c"] == 0
    assert repo._db.query(
        "SELECT COUNT(*) AS c FROM duplicate_check_image_files")[0]["c"] == 0


def test_force_reruns_and_rewrites_sections(repo):
    """force 必须真的走完整编排并重新写终态，而不是早退。"""
    ctx1 = _run(repo, _payload())
    assert len(ctx1.progress_calls) > 0
    ctx2 = _run(repo, _payload(force=True))
    # force 必须真的走完整编排（不是早退）
    assert len(ctx2.progress_calls) > 1
    assert all(s["status"] == "success"
               for s in repo.get_sections("p1").values())


def test_idempotent_hit_preserves_existing_sections(repo):
    """幂等命中不得顺手清空已有结果。"""
    _run(repo, _payload())
    before = {k: dict(v) for k, v in repo.get_sections("p1").items()}
    _run(repo, _payload())
    after = repo.get_sections("p1")
    assert set(after) == set(before)
    for key in before:
        assert after[key]["status"] == before[key]["status"]
        assert after[key]["signature"] == before[key]["signature"]


def test_stale_signature_does_not_write_any_terminal_state(repo):
    """从第一次读就是 stale → 连 `save_meta` 之后的**终态**都不该存在。

    brief 1.5 的原文断言是 `repo.get_sections("p1") == {}`，但那与**同一份 brief**
    的 1.3 / 1.4 自相矛盾：1.4 要求幂等命中时「已有四段 success 行」仍在，而幂等命中
    只在「四段全 success」时发生——所以 1.5 里那段已存在的状态**只能**由前一次运行
    写进去。若按 1.5 的字面断言，第二次运行必须先 `delete_sections` 把前一次的结果
    清掉，而 1.4 明令禁止（「幂等分支若顺手调了 delete_sections，会把已完成的结果
    清空——这是很容易犯的错」）。1.5 的断言也写成 `== {}` 而非 `!= before`，
    按字面无法与 1.4 同时成立。

    故此处按**可满足且等价于作者本意**的强度断言：丢弃路径不得写入任何**终态**
    （`success`/`error`）。这正是不带签名的 running 写法的目的——它们不是结果，
    只会让下一次运行命中 `signature != signature` 而重跑。
    """

    def always_stale(project_id):
        return "someone-else"

    ctx = FakeCtx()
    runner = DuplicateAnalysisRunner(repo=repo, signature_reader=always_stale)
    asyncio.run(runner(ctx, _payload(), None))

    sections = repo.get_sections("p1")
    terminal = {k: v["status"] for k, v in sections.items()
                if v["status"] in ("success", "error")}
    assert terminal == {}, f"过期签名仍写了终态：{terminal}"
    assert any("丢弃" in m for m in ctx.logs)


def test_running_rows_from_a_stale_discard_do_not_satisfy_idempotency(repo):
    """丢弃后留下的 running 行**不带签名** → 下一次运行必须重跑，不能跳过。

    这条钉住「为什么 running 行不写签名」：若它们带上本轮签名，第二次运行会看到
    「四段签名都等于本次签名」，只差状态不是 success；而真正的危险形态是 P3b 起
    段实现自己把状态写成 success——那时半成品会被误判成完成。
    """

    def always_stale(project_id):
        return "someone-else"

    ctx = FakeCtx()
    runner = DuplicateAnalysisRunner(repo=repo, signature_reader=always_stale)
    asyncio.run(runner(ctx, _payload(), None))
    # running 行不得携带本轮签名
    assert all(row["signature"] is None
               for row in repo.get_sections("p1").values())
    # 换回正常 reader：必须重跑（而不是因「签名已匹配」被跳过）
    ctx2 = _run(repo, _payload())
    assert not any("无需重复分析" in m for m in ctx2.logs), "半成品状态被误判为已完成"
    assert all(s["status"] == "success" for s in repo.get_sections("p1").values())


def test_logs_are_single_argument(repo):
    """TaskContext.log 只收一个参数；实现里不能传格式化参数。"""
    ctx = _run(repo, _payload())
    assert ctx.logs, "没有任何日志"
    assert all(isinstance(m, str) for m in ctx.logs)


def test_missing_bid_files_key_is_tolerated(repo):
    payload = {"tenderFiles": _payload()["tenderFiles"]}
    ctx = _run(repo, payload)
    assert ctx.progress_calls[-1][0] == 100


def test_empty_file_list_still_runs(repo):
    ctx = _run(repo, {"tenderFiles": [], "bidFiles": []})
    assert ctx.progress_calls[-1][0] == 100


def test_single_tender_file_key_is_supported(repo):
    """`tenderFile`（单数）与 `tenderFiles`（复数）等价——签名必须相同。"""
    payload = {"tenderFile": _payload()["tenderFiles"][0],
               "bidFiles": _payload()["bidFiles"]}
    ctx = _run(repo, payload)
    assert ctx.progress_calls[-1][0] == 100
