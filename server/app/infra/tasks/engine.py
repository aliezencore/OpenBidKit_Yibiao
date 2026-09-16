from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable

from app.config import Settings
from app.domain.shared.protocols import EventPublisherProtocol
from app.infra.repositories.task_logs import TaskLogRepository
from app.infra.repositories.tasks import TaskRepository
from app.infra.tasks.context import TaskCancelled, TaskContext

Runner = Callable[[TaskContext, dict, dict | None], Awaitable[None]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskEngine:
    def __init__(
        self,
        task_repo: TaskRepository,
        log_repo: TaskLogRepository,
        settings: Settings,
        *,
        publisher: EventPublisherProtocol | None = None,
        now_fn=_utc_now,
        monotonic_fn=None,
    ) -> None:
        self._repo = task_repo
        self._log_repo = log_repo
        self._settings = settings
        self._publisher = publisher
        self._now = now_fn
        self._monotonic = monotonic_fn or (lambda: asyncio.get_running_loop().time())
        self._running: dict[str, asyncio.Task] = {}
        self._contexts: dict[str, TaskContext] = {}
        self._global_gate = asyncio.Semaphore(settings.task_global_concurrency)
        self._project_gates: dict[str, asyncio.Semaphore] = {}

    async def submit(self, *, project_id: str, domain: str, kind: str,
                     payload: dict, runner: Runner) -> str:
        task_id = f"{kind}_{uuid.uuid4().hex[:12]}"
        self._repo.create(task_id=task_id, project_id=project_id, domain=domain,
                          kind=kind, payload=payload, created_at=self._now())
        previous_state = self._load_previous_state(project_id, kind)
        ctx = TaskContext(
            task_id=task_id, project_id=project_id, domain=domain, kind=kind,
            repo=self._repo, log_repo=self._log_repo, publisher=self._publisher,
            progress_interval_ms=self._settings.progress_write_interval_ms,
            now_fn=self._now, monotonic_fn=self._monotonic,
        )
        self._contexts[task_id] = ctx
        self._publish(task_id, {"type": "status", "taskId": task_id, "status": "queued"})
        self._running[task_id] = asyncio.create_task(
            self._run_gated(task_id, ctx, runner, payload, previous_state, project_id)
        )
        return task_id

    def _project_gate(self, project_id: str) -> asyncio.Semaphore:
        gate = self._project_gates.get(project_id)
        if gate is None:
            gate = asyncio.Semaphore(1)
            self._project_gates[project_id] = gate
        return gate

    async def _run_gated(self, task_id: str, ctx: TaskContext, runner: Runner,
                         payload: dict, previous_state: dict | None,
                         project_id: str) -> None:
        async with self._global_gate:
            async with self._project_gate(project_id):
                await self._run(task_id, ctx, runner, payload, previous_state)

    async def _run(self, task_id: str, ctx: TaskContext, runner: Runner,
                   payload: dict, previous_state: dict | None) -> None:
        self._repo.mark_running(task_id, self._now())
        self._publish(task_id, {"type": "status", "taskId": task_id, "status": "running"})
        try:
            await runner(ctx, payload, previous_state)
        except (TaskCancelled, asyncio.CancelledError):
            self._repo.mark_finished(task_id, "cancelled", self._now())
            self._publish(task_id, {"type": "status", "taskId": task_id, "status": "cancelled"})
        except Exception as error:  # noqa: BLE001 —— 任务边界必须记录所有失败
            self._repo.mark_finished(task_id, "error", self._now(), error=str(error))
            self._publish(task_id, {"type": "status", "taskId": task_id,
                                    "status": "error", "error": str(error)})
        else:
            self._repo.mark_finished(task_id, "success", self._now())
            self._publish(task_id, {"type": "status", "taskId": task_id, "status": "success"})
        finally:
            self._contexts.pop(task_id, None)
            self._running.pop(task_id, None)

    def cancel(self, task_id: str) -> bool:
        ctx = self._contexts.get(task_id)
        task = self._running.get(task_id)
        if ctx is None or task is None:
            return False
        ctx.mark_cancelled()
        return True

    def has_active_task(self, project_id: str) -> bool:
        for task_id in list(self._running):
            task = self._repo.get(task_id)
            if task and task["project_id"] == project_id:
                return True
        return False

    def recover_interrupted(self) -> list[str]:
        """进程启动时调用：把上次遗留的 running 任务标记为 interrupted。

        对齐 spec §6.4 —— 进程重启会中断进行中的任务，靠 checkpoint_json 续跑。
        """
        interrupted: list[str] = []
        for task in self._repo.list_active():
            if task["status"] != "running":
                continue
            self._repo.mark_finished(task["task_id"], "interrupted", self._now())
            interrupted.append(task["task_id"])
        return interrupted

    def _load_previous_state(self, project_id: str, kind: str) -> dict | None:
        rows = self._repo.list_for_project(project_id)
        for row in rows:
            if row["kind"] == kind and row["checkpoint_json"]:
                return json.loads(row["checkpoint_json"])
        return None

    def _publish(self, task_id: str, event: dict) -> None:
        if self._publisher is not None:
            self._publisher.publish(task_id, event)

    async def drain(self) -> None:
        while self._running:
            await asyncio.gather(*list(self._running.values()), return_exceptions=True)
