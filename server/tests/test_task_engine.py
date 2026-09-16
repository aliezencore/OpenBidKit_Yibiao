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
