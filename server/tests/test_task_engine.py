import asyncio

import pytest

from app.config import Settings
from app.infra.db.engine import Database
from app.infra.db.migrate import apply_schema
from app.infra.repositories.projects import ProjectRepository
from app.infra.repositories.task_logs import TaskLogRepository
from app.infra.repositories.tasks import TaskRepository
from app.infra.tasks.context import TaskCancelled, TaskContext
from app.infra.tasks.engine import TaskEngine


class Clock:
    """受控时间源，用于确定性地验证进度节流。"""

    def __init__(self):
        self.seconds = 0.0

    def now(self) -> str:
        return "2026-09-15T00:00:00+00:00"

    def monotonic(self) -> float:
        return self.seconds


class SpyPublisher:
    def __init__(self):
        self.events = []

    def publish(self, task_id, event):
        self.events.append((task_id, event))


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "engine.sqlite")
    apply_schema(db)
    projects = ProjectRepository(db)
    tasks = TaskRepository(db)
    logs = TaskLogRepository(db)
    clock = Clock()
    publisher = SpyPublisher()
    settings = Settings(_env_file=None, progress_write_interval_ms=200)
    engine = TaskEngine(tasks, logs, settings, publisher=publisher,
                        now_fn=clock.now, monotonic_fn=clock.monotonic)
    project_id = projects.create("P")
    yield {"db": db, "projects": projects, "tasks": tasks, "logs": logs,
           "engine": engine, "clock": clock, "publisher": publisher,
           "project_id": project_id}
    db.close()


def read(env, task_id):
    return env["tasks"].get(task_id)


async def test_submit_runs_runner_and_marks_success(env):
    seen = {}

    async def runner(ctx, payload, previous_state):
        seen["payload"] = payload
        seen["previous_state"] = previous_state
        ctx.log("开始")
        ctx.progress(100, "完成")

    task_id = await env["engine"].submit(
        project_id=env["project_id"], domain="duplicate", kind="duplicate-analysis",
        payload={"force": True}, runner=runner,
    )
    await env["engine"].drain()

    task = read(env, task_id)
    assert task["status"] == "success"
    assert task["progress"] == 100
    assert seen["payload"] == {"force": True}
    assert seen["previous_state"] is None
    assert [item["message"] for item in env["logs"].list_for_task(task_id)] == ["开始"]


async def test_runner_failure_marks_error_with_message(env):
    async def runner(ctx, payload, previous_state):
        raise RuntimeError("模型超时")

    task_id = await env["engine"].submit(
        project_id=env["project_id"], domain="duplicate", kind="duplicate-analysis",
        payload={}, runner=runner,
    )
    await env["engine"].drain()

    task = read(env, task_id)
    assert task["status"] == "error"
    assert task["error"] == "模型超时"


async def test_progress_write_is_throttled_but_still_broadcast(env):
    async def runner(ctx, payload, previous_state):
        ctx.progress(10, "a")
        env["clock"].seconds += 0.05
        ctx.progress(20, "b")
        env["clock"].seconds += 0.30
        ctx.progress(25, "c")

    task_id = await env["engine"].submit(
        project_id=env["project_id"], domain="duplicate", kind="duplicate-analysis",
        payload={}, runner=runner,
    )
    await env["engine"].drain()

    progress_events = [event for _, event in env["publisher"].events
                       if event["type"] == "progress"]
    persisted = [event["progress"] for event in progress_events if event["persisted"]]
    broadcast = [event["progress"] for event in progress_events]

    assert read(env, task_id)["progress"] == 25
    assert persisted == [10, 25], "20% 落在节流窗口内，不应落库"
    assert broadcast == [10, 20, 25], "节流只影响落库，不影响实时广播"


async def test_progress_repeated_identical_percent_does_not_write(env):
    """R13：逐文件循环重复落到同一整数百分比时，不得重复写库。

    旧逻辑在 changed 为 False 时会「穿透」到写库分支，导致每个重复百分比
    都写一遍数据库，违背节流初衷。本测试用受控 Clock 复现该场景，并断言
    persisted 序列——只有真正的首次落库标记为 True，其余重复广播均为 False。
    """

    async def runner(ctx, payload, previous_state):
        ctx.progress(50, "x")          # 首次：changed + 间隔无限 → 落库
        env["clock"].seconds += 0.30    # 超过 0.2s 间隔，但百分比未变
        ctx.progress(50, "x")          # 重复同一整数：不应写库
        env["clock"].seconds += 0.30
        ctx.progress(50, "x")          # 再次重复：不应写库

    task_id = await env["engine"].submit(
        project_id=env["project_id"], domain="duplicate", kind="duplicate-analysis",
        payload={}, runner=runner,
    )
    await env["engine"].drain()

    progress_events = [event for _, event in env["publisher"].events
                       if event["type"] == "progress"]
    persisted = [event["persisted"] for event in progress_events]
    broadcast = [event["progress"] for event in progress_events]

    # 三帧都广播了，但只有第一帧真正写库；后续重复百分比被节流拦截。
    assert broadcast == [50, 50, 50], "节流只拦落库，不拦广播"
    assert persisted == [True, False, False], \
        "重复的相同百分比不应触发第二次落库（R13 缺陷：旧逻辑会漏写条件而回退到写库）"
    # 落库行停在首次写入的 50，未被重复写入覆盖（值相同，仅作一致性核验）。
    assert read(env, task_id)["progress"] == 50


async def test_checkpoint_persists_state(env):
    async def runner(ctx, payload, previous_state):
        ctx.checkpoint({"stage": "outline", "done": [1, 2]})

    task_id = await env["engine"].submit(
        project_id=env["project_id"], domain="duplicate", kind="duplicate-analysis",
        payload={}, runner=runner,
    )
    await env["engine"].drain()

    assert read(env, task_id)["checkpoint_json"] == '{"stage": "outline", "done": [1, 2]}'


async def test_cancel_marks_cancelled_and_stops_runner(env):
    started = asyncio.Event()

    async def runner(ctx, payload, previous_state):
        started.set()
        for _ in range(100):
            ctx.raise_if_cancelled()
            await asyncio.sleep(0.01)

    task_id = await env["engine"].submit(
        project_id=env["project_id"], domain="duplicate", kind="duplicate-analysis",
        payload={}, runner=runner,
    )
    await started.wait()
    assert env["engine"].cancel(task_id) is True
    await env["engine"].drain()

    assert read(env, task_id)["status"] == "cancelled"


def test_raise_if_cancelled_only_raises_after_mark_cancelled(env):
    ctx = TaskContext(
        task_id="t1", project_id=env["project_id"], domain="duplicate",
        kind="duplicate-analysis", repo=env["tasks"], log_repo=env["logs"],
        now_fn=env["clock"].now, monotonic_fn=env["clock"].monotonic,
    )

    ctx.raise_if_cancelled()

    ctx.mark_cancelled()
    with pytest.raises(TaskCancelled):
        ctx.raise_if_cancelled()


def test_recover_interrupted_marks_running_tasks(env):
    project_id = env["project_id"]
    for task_id in ("t1", "t2", "t3"):
        env["tasks"].create(task_id=task_id, project_id=project_id, domain="duplicate",
                            kind="duplicate-analysis", payload={},
                            created_at="2026-09-15T00:00:00+00:00")
    env["tasks"].mark_running("t1", "2026-09-15T00:00:00+00:00")
    env["tasks"].mark_running("t2", "2026-09-15T00:00:00+00:00")
    env["tasks"].mark_finished("t3", "success", "2026-09-15T00:00:00+00:00")

    recovered = env["engine"].recover_interrupted()

    assert set(recovered) == {"t1", "t2"}
    assert env["tasks"].get("t1")["status"] == "interrupted"
    assert env["tasks"].get("t2")["status"] == "interrupted"
    assert env["tasks"].get("t3")["status"] == "success"


def test_recover_interrupted_is_noop_when_nothing_running(env):
    assert env["engine"].recover_interrupted() == []


async def test_has_active_task_reports_running_task(env):
    release = asyncio.Event()

    async def runner(ctx, payload, previous_state):
        await release.wait()

    assert env["engine"].has_active_task(env["project_id"]) is False
    task_id = await env["engine"].submit(
        project_id=env["project_id"], domain="duplicate", kind="duplicate-analysis",
        payload={}, runner=runner,
    )
    await asyncio.sleep(0)
    assert env["engine"].has_active_task(env["project_id"]) is True

    release.set()
    await env["engine"].drain()
    assert env["engine"].has_active_task(env["project_id"]) is False


async def test_project_gate_serializes_second_task(tmp_path):
    db = Database(tmp_path / "gate.sqlite")
    apply_schema(db)
    projects = ProjectRepository(db)
    tasks = TaskRepository(db)
    logs = TaskLogRepository(db)
    settings = Settings(_env_file=None, task_global_concurrency=4)
    engine = TaskEngine(tasks, logs, settings)
    project_id = projects.create("P")

    order = []
    release = asyncio.Event()

    async def runner(ctx, payload, previous_state):
        order.append(payload["name"])
        await release.wait()

    await engine.submit(project_id=project_id, domain="duplicate",
                        kind="duplicate-analysis", payload={"name": "first"}, runner=runner)
    await asyncio.sleep(0)
    await engine.submit(project_id=project_id, domain="duplicate",
                        kind="duplicate-analysis", payload={"name": "second"}, runner=runner)
    await asyncio.sleep(0.05)

    assert order == ["first"], "同一项目同时只允许一个写任务"

    release.set()
    await engine.drain()
    assert order == ["first", "second"]
    db.close()


async def test_cancelled_while_waiting_on_gate_is_cleaned_up(tmp_path):
    """R15：在闸门排队期间被取消的任务，其登记必须被清理。

    取消只发生在排队阶段（还没进入 _run），而清理逻辑若只挂在 _run 的
    finally 上，task_id 就会永久滞留 _running：has_active_task() 永远为真，
    drain() 也永远不结束。本用例按行为断言（无活跃任务 + drain 能结束），
    不依赖私有字典的内容。
    """
    db = Database(tmp_path / "gate_cancel.sqlite")
    apply_schema(db)
    projects = ProjectRepository(db)
    tasks = TaskRepository(db)
    logs = TaskLogRepository(db)
    settings = Settings(_env_file=None, task_global_concurrency=4)
    engine = TaskEngine(tasks, logs, settings)
    project_id = projects.create("P")

    release = asyncio.Event()
    first_started = asyncio.Event()
    order = []

    async def runner(ctx, payload, previous_state):
        order.append(payload["name"])
        first_started.set()
        await release.wait()

    # 第一个任务占住项目闸门。
    first_id = await engine.submit(
        project_id=project_id, domain="duplicate", kind="duplicate-analysis",
        payload={"name": "first"}, runner=runner)
    await asyncio.sleep(0)
    assert first_started.is_set()

    # 第二个任务排在同一项目闸门后面（只阻塞在闸门上，尚未进入 _run）。
    second_id = await engine.submit(
        project_id=project_id, domain="duplicate", kind="duplicate-analysis",
        payload={"name": "second"}, runner=runner)
    for _ in range(5):
        await asyncio.sleep(0)
    assert order == ["first"], "第二个任务应仍阻塞在闸门上，未开始执行"

    # 取第二个任务自身的 asyncio.Task 句柄以便取消（仅作操作句柄，不作断言）。
    second_task = engine._running[second_id]

    # 取消仍阻塞在闸门上的第二个任务，模拟外部 CancelledError（如事件循环关闭）。
    second_task.cancel()
    try:
        await second_task
    except asyncio.CancelledError:
        pass

    # 放行第一个任务，并等它真正收尾（只用 sleep(0) 握手，不读私有状态）。
    release.set()
    for _ in range(50):
        if tasks.get(first_id)["status"] == "success":
            break
        await asyncio.sleep(0)
    assert tasks.get(first_id)["status"] == "success"

    # 行为断言一（同步、fail fast）：排队期被取消的任务不得让项目永久“有活跃任务”。
    # 必须放在 drain() 之前：CPython 3.13 的 gather 对“子任务全部已完成”走同步快
    # 路径，未修复时 drain() 会同步死循环、连 wait_for 都无法超时，先断言才能保证
    # 回归是「失败」而不是「挂起」。
    assert engine.has_active_task(project_id) is False, \
        "排队期被取消的任务不得让 has_active_task 永久为真"

    # 行为断言二：drain() 必须能结束（超时保护，防止任何形式的永不收敛）。
    await asyncio.wait_for(engine.drain(), timeout=2)
    db.close()


async def test_global_gate_limits_across_projects(tmp_path):
    db = Database(tmp_path / "gate2.sqlite")
    apply_schema(db)
    projects = ProjectRepository(db)
    tasks = TaskRepository(db)
    logs = TaskLogRepository(db)
    settings = Settings(_env_file=None, task_global_concurrency=2)
    engine = TaskEngine(tasks, logs, settings)

    running = 0
    peak = 0
    release = asyncio.Event()

    async def runner(ctx, payload, previous_state):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await release.wait()
        running -= 1

    for index in range(5):
        await engine.submit(project_id=projects.create(f"P{index}"), domain="duplicate",
                            kind="duplicate-analysis", payload={}, runner=runner)
    for _ in range(10):
        await asyncio.sleep(0.01)

    assert peak == 2, "全局并发上限应生效"

    release.set()
    await engine.drain()
    db.close()
