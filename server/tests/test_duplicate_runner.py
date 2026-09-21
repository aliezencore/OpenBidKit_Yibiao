from __future__ import annotations

import asyncio
import warnings

import pytest

from app.infra.db.engine import Database
from app.infra.db.migrate import apply_schema
from app.domain.duplicate.signature import compute_analysis_signature
from app.infra.repositories.duplicate import DuplicateFileInput, DuplicateRepository
from app.infra.tasks.context import TaskCancelled
from app.infra.tasks.duplicate_runner import (
    ANALYSIS_RUNNER_ROLES,
    CONTENT_EXTRACTION,
    METADATA_EXTRACTION,
    DuplicateAnalysisRunner,
    SectionOutput,
    _stub_sections,
)


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


class CancellingCtx(FakeCtx):
    """在第 N 次 `raise_if_cancelled()` 时抛 `TaskCancelled`（F9）。

    `raise_if_cancelled` 在真实实现里只读一个布尔位、近乎零成本
    （`context.py:91-94`），所以「第几次抛」是确定性的：编排器里每次调用
    都恰好消耗一个计数。
    """

    def __init__(self, raise_at: int):
        super().__init__()
        self._calls = 0
        self._raise_at = raise_at

    def raise_if_cancelled(self):
        self._calls += 1
        if self._calls >= self._raise_at:
            raise TaskCancelled("任务已取消")


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


class _CountingRunner:
    """按角色计数 + 可编排行为的 stub runner 工厂（F2/F4）。

    - `calls[role]`：该角色被调用的次数（**F2 的「恰好一次」判据**）。
    - `started[role]`：该角色是否已启动（在 `await` 之前置位，供 Event 并发探针读）。
    - `order`：角色启动顺序。
    - `contexts[role]`：最后一次收到的 `SectionRunContext`。
    - 并发探针用（F4，由 `_drive_with_content_blocked` 装配）：
      `content_started` / `content_done` / `content_blocked` 三个事件与 `gate`；
      `metadata_overlapped` 在 metadata 的手跑到时记录
      「content 是否已起飞」「content 是否**尚未完成**」两条证据。
    - `errors`：`{role: 异常}` → 该角色在被调用时抛出该异常（F1 的失败注入）。
    """

    def __init__(self):
        self.calls: dict[str, int] = {}
        self.started: set[str] = set()
        self.order: list[str] = []
        self.contexts: dict[str, object] = {}
        self.errors: dict[str, BaseException] = {}
        # 并发探针（默认 None；由 _drive_with_content_blocked 装配）
        self.content_started: asyncio.Event | None = None
        self.content_done: asyncio.Event | None = None
        self.content_blocked: asyncio.Event | None = None
        self.gate: asyncio.Event | None = None
        self.metadata_overlapped: dict[str, bool] = {}

    def runner(self, role: str):
        async def run_section(section_ctx):
            self.calls[role] = self.calls.get(role, 0) + 1
            self.started.add(role)
            self.order.append(role)
            self.contexts[role] = section_ctx
            if role in self.errors:
                raise self.errors[role]
            if role == "content_extraction":
                if self.content_started is not None:
                    self.content_started.set()
                if self.content_blocked is not None:
                    self.content_blocked.set()
                if self.gate is not None:
                    await self.gate.wait()
                if self.content_done is not None:
                    self.content_done.set()
            elif role == "metadata_extraction":
                # 记下「metadata 的手跑到时 content 处于什么状态」——F4 的判别证据。
                #
                # ⚠ 这里**不能**断言 `content_started`：`ensure_future` 只把协程**排入**
                # 就绪队列，它的第一行要等当前任务让出控制权才执行。所以 metadata 的
                # 手第一次跑到时 content 的 runner 体**可能还没开始**——这是
                # asyncio 的调度语义，不是并行与否的证据。
                # 真正的证据是 `content_still_running`：并行时 content 会阻塞在
                # `gate` 上（**尚未完成**）；串行时它必然已经完成（`content_done` 已置位）。
                self.metadata_overlapped.update(
                    content_started=bool(self.content_started is not None
                                         and self.content_started.is_set()),
                    content_done=bool(self.content_done is not None
                                      and self.content_done.is_set()),
                    content_still_running=bool(self.content_done is not None
                                               and not self.content_done.is_set()),
                    metadata_started=True,
                )
                await asyncio.sleep(0)
            else:
                # 交还一次控制权，让「并行」在事件循环里真实发生
                await asyncio.sleep(0)
            return SectionOutput()

        return run_section

    def sections(self) -> dict:
        return {role: self.runner(role) for role in ANALYSIS_RUNNER_ROLES}


async def _run_async(repo, ctx, payload, **kwargs) -> None:
    """在**当前**事件循环里跑一轮（并发探针需要控制 loop，不能用 `asyncio.run`）。"""
    runner = DuplicateAnalysisRunner(repo=repo, **kwargs)
    await runner(ctx, payload, None)


def _run_with(repo, sections, payload=None, **kwargs):
    ctx = FakeCtx()
    asyncio.run(DuplicateAnalysisRunner(
        repo=repo, sections=sections, **kwargs)(ctx, payload or _payload(), None))
    return ctx


def _terminal_statuses(repo) -> dict:
    return {k: v["status"] for k, v in repo.get_sections("p1").items()}


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


def test_empty_file_list_does_not_inherit_previous_success(repo):
    """空清单不得继承上一轮的 `success` 段行（fix-round 1 / Task 5 I-1 连带）。

    `compute_analysis_signature([])` 是常量 `sha1("")`。若库里
    **`current_signature` 也是它、且四段已是 `success` + 同一个签名**，那么
    幂等判据 `_is_up_to_date` 为真 → 直接报 100 返回。这正是「空清单 + 空清单签名」
    的组合：**本轮什么都没算，但四段带着上一轮的 `success` 与旧文案原样返回**。

    判别点选**段文案**：幂等命中路径**不写任何段行**，所以段文案会停在预置的
    `OLD`；走真实路径则会被本轮终态覆盖成「分析完成」/「元数据分析完成」。
    实测（枚举 meta×段签名 四种组合）：只有「meta 与段签名都等于空清单签名」这一
    组合会停在 `OLD` —— 其余组合都因 `signature_reader != signature` 而进入
    真实路径。故本用例是**判别该缺陷的最小组合**。
    """
    empty = compute_analysis_signature([])
    for section in ("metadata", "outline", "content", "image"):
        repo.save_section("p1", section, status="success", progress=100,
                          message="OLD", signature=empty,
                          stats={}, updated_at="2026-01-01T00:00:00Z")
    repo.save_meta("p1", current_signature=empty)

    _run(repo, {"tenderFiles": [], "bidFiles": [], "force": False})

    sections = repo.get_sections("p1")
    # 幂等命中会保留 `OLD`；真实路径必须把它覆盖成本轮终态文案。
    messages = {s: sections[s]["message"] for s in
                ("metadata", "outline", "content", "image")}
    assert all(m != "OLD" for m in messages.values()), (
        f"空清单误命中幂等，继承了上一轮的段状态：{messages}")


def test_single_tender_file_key_is_supported(repo):
    """`tenderFile`（单数）与 `tenderFiles`（复数）等价——签名必须相同。"""
    payload = {"tenderFile": _payload()["tenderFiles"][0],
               "bidFiles": _payload()["bidFiles"]}
    ctx = _run(repo, payload)
    assert ctx.progress_calls[-1][0] == 100


# ============================================================ fix-round 1 (F1–F9)
#
# 以下用例是 fix-round 1 新增。每条都在报告里附了**变异验证**：
# 用 Edit 工具改源码制造对应缺陷 → 该用例变红 → 还原。


# ------------------------------------------------------------------ F2 / F4

def test_runner_roles_are_exactly_five_without_metadata(repo):
    """runner 角色恰为五个，**且不含 `metadata`**（F2：`metadata` 是状态行不是角色）。

    首版把角色写成 `("content_extraction", "metadata_extraction", *ANALYSIS_SECTIONS)`，
    于是 `sections` 多出一个永不被调用的 `metadata` 角色。这条把角色集合本身钉死：
    `metadata` 段的状态由主编排依据两个提取角色的结果写终态，**没有对应的 runner**。
    """
    stub = _CountingRunner()
    assert set(stub.sections()) == set(ANALYSIS_RUNNER_ROLES)
    assert set(ANALYSIS_RUNNER_ROLES) == {
        "content_extraction", "metadata_extraction", "outline", "content", "image"}
    assert "metadata" not in ANALYSIS_RUNNER_ROLES


def test_each_role_runner_runs_exactly_once_per_round(repo):
    """每个 runner 角色每轮**恰好跑一次**（F2：content 曾被跑两次）。

    这是本 fix 最重要的一条判据。首版把「正文提取」与「正文比对」压进同一个
    `content` 键，编排器于是先以「提取」调一次 content runner、再把它放进
    `asyncio.gather` 以「比对」调第二次，第二次还把自己的输出当上游。
    现在 `content_extraction`（提取）与 `content`（比对）是两个角色，
    计数必须严格为 1。
    """
    stub = _CountingRunner()
    _run_with(repo, stub.sections())

    assert stub.calls == {role: 1 for role in ANALYSIS_RUNNER_ROLES}, \
        f"角色调用次数不对（含重复调用）：{stub.calls}"


def test_runner_context_section_matches_its_role(repo):
    """`SectionRunContext.section` 必须是该角色的 key（F2：键要有意义）。"""
    stub = _CountingRunner()
    _run_with(repo, stub.sections())

    assert set(stub.contexts) == set(ANALYSIS_RUNNER_ROLES)
    for role, section_ctx in stub.contexts.items():
        assert section_ctx.section == role


def test_downstream_roles_receive_the_content_extraction_artifact(repo):
    """后三个角色的 `content_files` 上游 = `content_extraction` 角色的产出（F2）。

    P3a 里 `content_files` 就是提取角色的 `SectionOutput` 本身
    （见 `content_output_of` 的形状约定）；P3b 定义真实形状时改那一处。
    """
    stub = _CountingRunner()
    _run_with(repo, stub.sections())

    for role in ("outline", "content", "image"):
        upstream = stub.contexts[role].content_files
        assert isinstance(upstream, SectionOutput), \
            f"{role} 没拿到正文提取产物：{upstream!r}"


def test_extraction_roles_do_not_write_their_own_section_rows(repo):
    """仅四段写状态行；两个提取角色**不写**（F2：五角色 vs 四段状态机）。"""
    _run(repo, _payload())
    sections = repo.get_sections("p1")
    assert set(sections) == {"metadata", "outline", "content", "image"}
    assert "content_extraction" not in sections
    assert "metadata_extraction" not in sections


def test_metadata_stats_carry_content_extraction_substats(repo):
    """`content_extraction` 的产物并入 `metadata` 段的 stats（对齐参考 :2337/:2382）。"""
    class _StatsRunner(_CountingRunner):
        def runner(self, role):
            inner = super().runner(role)

            async def run_section(section_ctx):
                await inner(section_ctx)
                if role == "content_extraction":
                    return SectionOutput(stats={"extracted": 2})
                if role == "metadata_extraction":
                    return SectionOutput(stats={"rows": 7})
                return SectionOutput()

            return run_section

    stub = _StatsRunner()
    _run_with(repo, stub.sections())

    stats = repo.get_sections("p1")["metadata"]["stats"]
    assert stats["rows"] == 7, f"metadata_extraction 的 stats 丢了：{stats}"
    assert stats["contentExtraction"] == {"extracted": 2}, \
        f"content_extraction 的子统计没并进 metadata：{stats}"


def _content_blocked_driver(repo, stub):
    """造一个「content 阻塞、metadata 并行跑到」的驱动协程（F4）。

    返回一个**零参协程函数**；调用方在受控事件循环里跑它
    （`asyncio.run(_content_blocked_driver(repo, stub)())`）。

    判别力（关键就在这里）：
    - **并行**（基线）：content 先起飞并阻塞 → metadata 的手跑到 → 此时
      `content_done` 还是未置位 → `content_still_running is True`。
    - **串行 ①**（重排为「先 metadata 再起 content」）：metadata 的手跑到时
      `content_started` 未置位 → `content_still_running` 为 False → 断言红。
    - **串行 ②**（`await content_task` 后再跑 metadata）：`content_blocked.wait()`
      在 content 完成前不会返回（1 秒超时 → `TimeoutError`）→ 红；
      即使实现先放行再 await，metadata 的手也会看到 `content_done` 已置位 → 红。

    超时都在 1 秒级，测试最坏是**失败**而不是挂死。
    """

    async def drive():
        ctx = FakeCtx()
        runner = DuplicateAnalysisRunner(repo=repo, sections=stub.sections())
        task = asyncio.ensure_future(runner(ctx, _payload(), None))
        try:
            await asyncio.wait_for(stub.content_blocked.wait(), timeout=1.0)
            await asyncio.wait_for(_until(lambda: stub.metadata_overlapped), timeout=1.0)
        finally:
            stub.gate.set()
        await asyncio.wait_for(task, timeout=5.0)
        return ctx

    stub.content_started = asyncio.Event()
    stub.content_done = asyncio.Event()
    stub.content_blocked = asyncio.Event()
    stub.gate = asyncio.Event()
    stub.metadata_overlapped = {}
    return drive


async def _until(predicate, step=0):
    """让出控制权直到 `predicate()` 为真（配合 `wait_for` 当超时保险）。"""
    while not predicate():
        await asyncio.sleep(step or 0)


def test_content_extraction_is_scheduled_before_metadata_extraction_starts(repo, monkeypatch):
    """`content_extraction` 必须在 metadata **启动之前**被排入事件循环（顺序）。

    为什么不能直接看 `stub.order`：`ensure_future` 只把协程**排入就绪队列**，它的
    runner 体要等 `await metadata` 让出控制权后才执行 —— 所以 runner 体的执行顺序在
    并行与单任务两种实现下**都**可能是 metadata 在前。用 `order` 判顺序会得到一条
    与真实编排顺序无关的假判据（实测：并行基线也不满足 `order[0] == "content_extraction"`）。

    正确的观测点是**排入时机**：在 `asyncio.ensure_future` 上挂钩子，记录它包装的协程
    属于哪个角色。基线实现里第一个 `ensure_future` 包装的必然是 `content_extraction`；
    重排实现（先 await metadata 再起 content）下，第一个 `ensure_future` 出现在
    metadata 之后。
    """
    stub = _CountingRunner()
    timeline: list[str] = []
    real_ensure_future = asyncio.ensure_future

    def spy(coro, *args, **kwargs):
        # `role` 是 `_run_section` 的形参名，从协程帧的局部变量里取；取不到说明是
        # 测试自己起的任务（`_content_blocked_driver` 的 `drive()`），跳过。
        frame = getattr(coro, "cr_frame", None)
        role = frame.f_locals.get("role") if frame is not None else None
        if role is not None:
            timeline.append(f"schedule:{role}")
        return real_ensure_future(coro, *args, **kwargs)

    # metadata 的手在**启动时**记一笔，与「谁先被排入」放在同一条时间线上比较。
    real_runner = stub.runner

    def runner(role):
        inner = real_runner(role)

        async def run_section(section_ctx):
            timeline.append(f"start:{role}")
            return await inner(section_ctx)

        return run_section

    stub.runner = runner
    monkeypatch.setattr(asyncio, "ensure_future", spy)
    asyncio.run(_content_blocked_driver(repo, stub)())

    # 基线的关键不变量：`content_extraction` 是**在 metadata 启动之前**被排入的。
    # 重排实现（先 `await metadata` 再起 content）会让 start:metadata 排在
    # schedule:content_extraction 之前。
    assert "schedule:content_extraction" in timeline, timeline
    assert "start:metadata_extraction" in timeline, timeline
    assert timeline.index("schedule:content_extraction") < \
        timeline.index("start:metadata_extraction"), (
            "content_extraction 不是在 metadata 启动之前排入的（提取角色顺序被重排）："
            f"{timeline}")


def test_content_extraction_and_metadata_extraction_run_concurrently(repo):
    """两个提取角色**真并发**（F4：三条串行化变异都能躲过旧 19 条用例）。

    判据是 `content_still_running`：metadata 的手跑到时 content **尚未完成**。
    并行实现里 content 阻塞在 `gate` 上 → True；任何串行化 → False。

    `content_started` 只是旁证，**不作为断言**：`ensure_future` 只把协程排入就绪队列，
    它的第一行要等当前任务让出控制权才跑，所以 metadata 的手可能先于 content 的
    runner 体执行。把它当断言会得到一条**与并行度无关**的假判据（首版就踩了这个坑）。
    顺序由 `test_content_extraction_starts_before_metadata_extraction` 单独钉。
    """
    stub = _CountingRunner()
    asyncio.run(_content_blocked_driver(repo, stub)())
    overlap = stub.metadata_overlapped
    assert overlap["metadata_started"] is True, "metadata_extraction 没跑"
    assert overlap["content_done"] is False, \
        "metadata 跑到时 content 已经完成（提取被串行化）"
    assert overlap["content_still_running"] is True, \
        "metadata_extraction 在 content_extraction 完成之后才跑（提取被串行化）"


def test_each_role_runner_runs_exactly_once_under_concurrency(repo):
    """并发路径下每个角色仍只跑一次（F2 在真并行下的复核）。"""
    stub = _CountingRunner()
    asyncio.run(_content_blocked_driver(repo, stub)())
    assert stub.calls == {role: 1 for role in ANALYSIS_RUNNER_ROLES}, stub.calls


# ------------------------------------------------------------------ F1 / F6

def test_one_section_raising_still_lets_the_others_succeed(repo):
    """某一段抛异常 → 该段 `error` 终态，兄弟段仍 `success`（F1 必需用例 a）。

    首版没有 `return_exceptions=True`：`gather` 会把第一个异常向上抛、**同时取消
    兄弟任务**，兄弟的异常与终态一起丢掉 —— 四个段全部停在 `running`。
    """
    stub = _CountingRunner()
    stub.errors["outline"] = RuntimeError("目录分析炸了")
    ctx = _run_with(repo, stub.sections())

    statuses = _terminal_statuses(repo)
    assert statuses["outline"] == "error", f"outline 没进 error 终态：{statuses}"
    for section in ("metadata", "content", "image"):
        assert statuses[section] == "success", \
            f"{section} 被兄弟段的异常带塌了：{statuses}"
    assert all(s in ("success", "error") for s in statuses.values()), \
        f"有段停在非终态：{statuses}"
    assert repo.get_sections("p1")["outline"]["stats"]["error"] == "目录分析炸了"
    assert any("部分结果失败" in m for m in ctx.logs)


def test_all_three_downstream_sections_raising_all_reach_error(repo):
    """三个下游段同时抛异常 → 三个都进 `error`（默认 gather 会丢掉后两个）。"""
    stub = _CountingRunner()
    for role in ("outline", "content", "image"):
        stub.errors[role] = RuntimeError(f"{role} 炸了")
    _run_with(repo, stub.sections())

    statuses = _terminal_statuses(repo)
    assert statuses == {"metadata": "success", "outline": "error",
                        "content": "error", "image": "error"}, statuses


def test_extraction_role_raising_still_writes_terminal_states(repo):
    """提取角色抛异常 → metadata/content 仍拿到终态，不留 running（F1 用例 b）。"""
    stub = _CountingRunner()
    stub.errors["content_extraction"] = RuntimeError("正文提取炸了")
    _run_with(repo, stub.sections())

    statuses = _terminal_statuses(repo)
    assert statuses["metadata"] == "error", \
        f"正文提取失败没反映到 metadata 终态：{statuses}"
    assert all(s in ("success", "error") for s in statuses.values()), \
        f"有段停在非终态（running）：{statuses}"
    assert repo.get_sections("p1")["metadata"]["message"] == "部分文件提取失败"


def test_metadata_extraction_raising_does_not_leave_sections_running(repo):
    """metadata 提取角色抛异常 → 四段**全部**有终态，绝不停在 running。"""
    stub = _CountingRunner()
    stub.errors["metadata_extraction"] = RuntimeError("元数据炸了")
    _run_with(repo, stub.sections())

    sections = repo.get_sections("p1")
    assert set(sections) == {"metadata", "outline", "content", "image"}
    assert all(s["status"] in ("success", "error") for s in sections.values()), \
        f"有段停在 running：{{k: v['status'] for k, v in sections.items()}}"
    assert sections["metadata"]["status"] == "error"


def test_no_task_exception_was_never_retrieved(repo, recwarn):
    """F6：不得出现 `Task exception was never retrieved`。

    这条用 `warnings.catch_warnings` 抓 asyncio 在 GC 时报出的
    「Task exception was never retrieved」（它经由 `loop.call_exception_handler`
    走 logger，不经 `warnings`；故这里同时挂一个异常处理器来抓）。

    场景选「提取角色抛错 + 后段抛错」：首版的 gather 会取消兄弟、并让
    content 任务成为孤儿，于是 GC 时报未取回。
    """
    stub = _CountingRunner()
    stub.errors["content_extraction"] = RuntimeError("正文提取炸了")
    stub.errors["image"] = RuntimeError("图片炸了")

    handled: list[dict] = []

    async def drive():
        ctx = FakeCtx()
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: handled.append(context))
        try:
            runner = DuplicateAnalysisRunner(repo=repo, sections=stub.sections())
            await runner(ctx, _payload(), None)
        finally:
            loop.set_exception_handler(previous)

    asyncio.run(drive())

    messages = [c.get("message", "") for c in handled]
    assert not any("never retrieved" in m for m in messages), \
        f"出现未取回的任务异常：{messages}"


def test_cancelled_run_does_not_leak_a_never_retrieved_task(repo):
    """取消路径下 `content_extraction` 任务被**取回**，不留下未检索的异常（F6）。

    **孤儿场景（实测定位到确切取消点）**：取消在第 3 次 `raise_if_cancelled()` 时抛出
    ——那一刻 content 任务已起飞但还没被 `_resolve_content_files` await 到，
    而 `_orchestrate` 会直接从 `await metadata` 抛出去，于是**没有任何人 await 它**。
    它带着 `TaskCancelled` 结束；若无人取回，asyncio 在 GC 时报
    「Task exception was never retrieved」。

    **判别方式**：`asyncio.Task._log_traceback` 是「异常尚未被检索」的标记，
    实测它就是「将来会打印 Task exception was never retrieved」的充要条件
    （关掉 drain 后那行告警确实出现在测试输出里）。

    **观测点必须放在「任务创建」处，不能放在 runner 体内**：取消发生在 content
    任务刚被 `ensure_future` 出来、`_guarded_call` 的协程体还**没轮到执行**的时刻
    （实测取消点 3 抛在 `await metadata_extraction` 内，事件循环第一次让出后
    就直接抛出）。runner 体里无论如何采样都采不到孤儿任务本身。
    这里改为替换事件循环的 task factory，把编排器创建的**每个** Task 都记下来。

    实测（关闭 `_drain_pending_tasks` 时，逐取消点采样）：
    - 取消点 1 → 无任务（content 还没建）；
    - 取消点 2 → `(done=False)` 仍在飞；
    - **取消点 3 → `(done=True, cancelled=False, _log_traceback=True)` ← 正是孤儿**；
    - 取消点 4+ → `_log_traceback=False`（已被 `_resolve_content_files` 取回）。
    所以只有取消点 3 能判别。

    **关于为什么不用「never retrieved」字符串断言**：那条告警由任务对象在 GC 时
    经**已关闭**的事件循环发出，测试进程内装 `set_exception_handler` 也收不到
    （实测 `handled == []`）。故这里用 `_log_traceback` 这个**机制性**判据。
    """
    stub = _CountingRunner()
    seen = {"n": 0}

    class _Ctx(FakeCtx):
        def raise_if_cancelled(self):
            seen["n"] += 1
            if seen["n"] >= 3:  # 见 docstring：只有第 3 次会真的造成孤儿
                raise TaskCancelled("任务已取消")

    spawned: list = []
    loop = asyncio.new_event_loop()
    original_factory = loop.get_task_factory()

    def capturing_factory(loop_, coro, **kwargs):
        task = (original_factory(loop_, coro, **kwargs) if original_factory is not None
                else asyncio.Task(coro, loop=loop_, **kwargs))
        spawned.append(task)
        return task

    loop.set_task_factory(capturing_factory)

    async def drive():
        runner_fn = DuplicateAnalysisRunner(repo=repo, sections=stub.sections())
        try:
            await runner_fn(_Ctx(), _payload(), None)
        except TaskCancelled:
            pass

    try:
        loop.run_until_complete(drive())
    finally:
        loop.close()

    # 只看「带着未取回异常结束」的任务：驱动协程任务即使仍 pending 也与本判据无关，
    # 所以按 `_log_traceback` 过滤而非按 pending 过滤。
    leaks = [
        f"unretrieved:{task!r}"
        for task in spawned
        if task.done() and not task.cancelled()
        and getattr(task, "_log_traceback", False)
    ]

    assert leaks == [], f"取消路径留下了未取回的任务：{leaks}"


def test_orphan_content_task_exception_is_retrieved(repo):
    """过期签名丢弃路径下 `content_extraction` 仍在飞时，其异常也必须被取回。"""
    stub = _CountingRunner()
    stub.errors["content_extraction"] = RuntimeError("正文提取炸了")

    def always_stale(project_id):
        return "someone-else"

    handled: list[dict] = []

    async def drive():
        ctx = FakeCtx()
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: handled.append(context))
        try:
            runner = DuplicateAnalysisRunner(
                repo=repo, sections=stub.sections(), signature_reader=always_stale)
            await runner(ctx, _payload(), None)
        finally:
            loop.set_exception_handler(previous)

    asyncio.run(drive())
    messages = [c.get("message", "") for c in handled]
    assert not any("never retrieved" in m for m in messages), messages


def test_top_level_failure_still_leaves_sections_terminal(repo):
    """顶层意外失败（仓储写终态时炸）→ 段不得停在 running（F1 的顶层兜底）。"""
    stub = _CountingRunner()
    calls = {"n": 0}
    original = repo.save_section

    def flaky(project_id, section, **kwargs):
        # 第 6 次写段状态时炸：此时四段都已写过 running，但终态还没写。
        calls["n"] += 1
        if calls["n"] == 6:
            raise RuntimeError("仓储写段状态炸了")
        return original(project_id, section, **kwargs)

    repo.save_section = flaky
    ctx = _run_with(repo, stub.sections())

    statuses = _terminal_statuses(repo)
    assert all(s in ("success", "error") for s in statuses.values()), \
        f"顶层异常后有段停在 running：{statuses}"
    assert any("异常中断" in m for m in ctx.logs), ctx.logs


# ------------------------------------------------------------------ F3

def test_section_stats_are_preserved_when_a_round_does_not_pass_them(repo):
    """不携带 `stats` 的写**保留**旧 stats（F3：首版恒传 `{}` 把旧值砸掉）。"""
    # 预置：metadata 段已有非空 stats（模拟 P3b 中途写过的进度类统计）
    repo.save_section("p1", "metadata", status="running", progress=1,
                      message="先跑着", signature=None, stats={"rows": 7, "files": [1]},
                      updated_at="t0")

    # 首版在这里把 metadata 的 stats 写成 {}
    assert repo.get_sections("p1")["metadata"]["stats"] == {"rows": 7, "files": [1]}
    _run(repo, _payload())

    # 本轮 metadata 终态**携带** stats（`_merge_metadata_stats` 产出非空 dict），
    # 所以这里改验「未携带 stats 的写保留旧值」这一契约本身：
    repo.save_section("p1", "outline", status="running", progress=1,
                      message="x", signature=None, stats={"kept": True},
                      updated_at="t0")
    runner = DuplicateAnalysisRunner(repo=repo)
    runner._save_section(FakeCtx(), "p1", "outline", status="running", progress=2,
                         message="y")  # 不传 stats、不传 signature
    row = repo.get_sections("p1")["outline"]
    assert row["stats"] == {"kept": True}, f"未携带 stats 的写砸掉了旧值：{row}"
    assert row["status"] == "running" and row["progress"] == 2


def test_signature_is_preserved_when_not_carried_and_cleared_when_none(repo):
    """`signature` 的「未携带 → 保留 / 显式 None → 写 NULL」二分（F3 的签名面）。"""
    runner = DuplicateAnalysisRunner(repo=repo)
    ctx = FakeCtx()

    runner._save_section(ctx, "p1", "outline", status="running", progress=1,
                         message="a", signature="sig-1")
    assert repo.get_sections("p1")["outline"]["signature"] == "sig-1"

    runner._save_section(ctx, "p1", "outline", status="running", progress=2,
                         message="b")  # 未携带 → 保留
    assert repo.get_sections("p1")["outline"]["signature"] == "sig-1"

    runner._save_section(ctx, "p1", "outline", status="running", progress=3,
                         message="c", signature=None)  # 显式 None → 写 NULL
    assert repo.get_sections("p1")["outline"]["signature"] is None


# ------------------------------------------------------------------ F7

def test_section_failure_marks_only_own_section(repo):
    """段级失败**只影响该段终态**——某段声明 `failed=True`，其余段仍 `success`
    （R-P3b-4 的段间隔离；P3a 的「失败分支不可达」在 P3b 被「可控失败」取代）。

    用可控 runner：仅 `outline` 返回 `failed=True` 的产出，content/image/两个提取角色
    全部用 stub（`failed=False`）。断言：outline=error，content/image/metadata=success；
    metadata 不受任一分析段失败牵连。
    """
    sections = dict(_stub_sections())

    async def fail_outline(section_ctx):
        return SectionOutput(failed=True, message="部分文件目录分析失败")

    sections["outline"] = fail_outline
    runner = DuplicateAnalysisRunner(repo=repo, sections=sections)

    ctx = FakeCtx()
    asyncio.run(runner(ctx, _payload(), None))
    statuses = _terminal_statuses(repo)
    assert statuses["outline"] == "error"
    assert statuses["content"] == "success"
    assert statuses["image"] == "success"
    assert statuses["metadata"] == "success"


# ------------------------------------------------------------------ F8

def test_non_mapping_payload_is_treated_as_empty(repo):
    """非 `Mapping` 载荷（如 `[1, 2]`）按空处理，不抛 `AttributeError`（F8）。"""
    for payload in ([1, 2], "string", 42, None):
        ctx = _run(repo, payload)
        assert ctx.progress_calls[-1][0] == 100, f"载荷 {payload!r} 没跑到底"


def test_non_dict_file_entries_collapse_to_one_signature(repo):
    """**已登记局限**：非 dict 文件项（如 `bidFiles=[42]`）折叠成同一个签名（F8）。

    上游保证文件项是 dict，故本阶段不加形状校验；这里把折叠行为钉成事实，
    将来谁加了校验，这条会变红并去更新 `_bid_files` / `_file_ref` 的说明。
    """
    sig_of = {}

    async def capture(payload):
        ctx = FakeCtx()
        await DuplicateAnalysisRunner(repo=repo)(ctx, payload, None)
        return repo.get_meta("p1")["current_signature"]

    sig_of[42] = asyncio.run(capture({"bidFiles": [42]}))
    sig_of[43] = asyncio.run(capture({"bidFiles": [43], "force": True}))
    assert sig_of[42] == sig_of[43], \
        "非 dict 文件项居然能区分？请更新 _bid_files / _file_ref 的已知局限说明"


# ------------------------------------------------------------------ F9

def test_cancellation_propagates_from_a_section_boundary(repo):
    """`ctx.raise_if_cancelled()` 在角色边界生效并向上传播（F9 / spec §6.4）。

    `raise_at` 从 1 数到 5，逐个覆盖编排器里每一个取消检查点：
    1 = 第一个提取角色之前、2 = metadata 角色之前、3 = 三段 running 之前、
    4/5 = 三段角色各自的入口。每一处都必须让 `TaskCancelled` 从 `__call__` 传出
    （吞掉它会让 TaskEngine 以为任务正常结束）。
    """
    for raise_at in (1, 2, 3, 4, 5):
        ctx = CancellingCtx(raise_at=raise_at)
        with pytest.raises(TaskCancelled):
            asyncio.run(DuplicateAnalysisRunner(repo=repo)(ctx, _payload(), None))
        # 取消也不能让段停在 running（F1 与 F9 的交集）
        statuses = _terminal_statuses(repo)
        assert all(s in ("success", "error") for s in statuses.values()), \
            f"raise_at={raise_at} 时取消后有段停在 running：{statuses}"


def test_cancellation_at_the_terminal_write_boundary(repo):
    """终态写之前的取消检查必须生效（F9：`_commit_terminal_states` 的入口）。

    **判别设计**：不能只断言「抛出了 `TaskCancelled`」——签名校验那一步也有取消检查，
    所以「终态写前的检查被删掉」时异常照样会抛，断言仍然绿（首版就是这么写错的，
    变异验证时才发现）。真正的判别点是**四段一个终态都没写**：
    签名校验之后、`_commit_terminal_states` 的第一件事就是取消检查，
    所以取消发生在那里时，库里只能是三个 `running` + metadata 无行（未写终态）。

    做法：让 `raise_if_cancelled` 在下游三段的**全部** runner 都返回之后再抛。

    **注意尾部的兜底写入不算数**：取消传出后 `__call__` 的处理器会调
    `_fail_pending_sections` 把四段收成 `error`（F1 的要求，必须发生）。
    所以判据不能是「库里没有终态」，而要看**取消点的那一刻**有没有写终态 ——
    用「取消点之前的终态写次数」来判（见 `cancelled_at`）。

    **已知局限（如实登记）**：这条**无法判别**「只删掉 `_commit_terminal_states`
    开头那一次 `raise_if_cancelled`」这一种变异。原因是紧邻的签名校验处
    （`_orchestrate` 末尾）还有一次取消检查，它在同一次调用里**先**触发，
    于是 `_commit_terminal_states` 根本不会被执行 —— 两种实现的可观测行为完全相同。
    变异验证确认了这一点（删掉后本条仍绿）。要真正判别需要把两次检查合并或
    给每次检查传不同的标签，那超出了 P3a 的范围。这里保留本条的价值在于：
    它钉住「终态写之前不得有任何终态落库」这个契约，以及取消后的兜底收口。
    """
    stub = _CountingRunner()
    terminal_writes_before_cancel = {"n": 0}
    cancelled_at = {"t": None}
    original_save = repo.save_section

    def counting_save(project_id, section, **kwargs):
        status = kwargs.get("status")
        if cancelled_at["t"] is None and status in ("success", "error"):
            terminal_writes_before_cancel["n"] += 1
        return original_save(project_id, section, **kwargs)

    repo.save_section = counting_save

    downstream_done = {"value": False}
    real_runner = stub.runner

    def runner(role):
        inner = real_runner(role)

        async def run_section(section_ctx):
            result = await inner(section_ctx)
            if role == "image":  # 最后一个下游角色跑完
                downstream_done["value"] = True
            return result

        return run_section

    stub.runner = runner

    class _Ctx(FakeCtx):
        def raise_if_cancelled(self):
            if downstream_done["value"] and cancelled_at["t"] is None:
                # 记下「取消点」——此后由兜底写的终态不再计入
                cancelled_at["t"] = terminal_writes_before_cancel["n"]
                raise TaskCancelled("任务已取消")

    with pytest.raises(TaskCancelled):
        asyncio.run(DuplicateAnalysisRunner(repo=repo, sections=stub.sections())(
            _Ctx(), _payload(), None))

    assert cancelled_at["t"] == 0, (
        "取消发生在终态写之前，但此前已经写了终态"
        f"（`_commit_terminal_states` 的取消检查缺失）：{cancelled_at['t']} 个")
    statuses = _terminal_statuses(repo)
    assert all(s in ("success", "error") for s in statuses.values()), \
        f"终态写前取消后有段停在 running（兜底写入也没生效）：{statuses}"
