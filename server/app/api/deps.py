from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.config import Settings
from app.infra.ai.client import AiClient
from app.infra.db.engine import Database
from app.infra.db.migrate import apply_schema
from app.infra.repositories.projects import ProjectRepository
from app.infra.repositories.task_logs import TaskLogRepository
from app.infra.repositories.tasks import TaskRepository
from app.infra.tasks.cpu import CpuExecutor
from app.infra.tasks.engine import TaskEngine
from app.infra.tasks.events import EventBus


@dataclass
class AppState:
    settings: Settings
    db: Database
    projects: ProjectRepository
    tasks: TaskRepository
    logs: TaskLogRepository
    bus: EventBus
    engine: TaskEngine
    cpu: CpuExecutor
    ai: AiClient

    async def aclose(self) -> None:
        # 任意一个资源的释放失败都不能阻止其余资源被释放。
        # 用嵌套 finally 保证 ai / cpu / db 三者最终都被关闭。
        try:
            await self.ai.aclose()
        finally:
            try:
                self.cpu.shutdown(wait=False)
            finally:
                self.db.close()


def build_state(settings: Settings) -> AppState:
    # 构造失败时必须回滚已建资源，否则 lifespan 顶部的 build_state 抛出后
    # 关闭逻辑（aclose）永远不会执行，SQLite 连接与 CpuExecutor 线程池会随
    # 每次失败重启累积泄漏（裁定 R23）。释放顺序与 aclose 一致，且逐个释放
    # 互不影响。AiClient 无需在此释放：httpx.AsyncClient 惰性建连、构造期不持有
    # OS/网络资源，且它是最后一步（详见报告 R23 的裁定说明）。
    db: Database | None = None
    cpu: CpuExecutor | None = None
    try:
        db = Database(settings.db_path)
        apply_schema(db)
        projects = ProjectRepository(db)
        tasks = TaskRepository(db)
        logs = TaskLogRepository(db)
        bus = EventBus()
        engine = TaskEngine(tasks, logs, settings, publisher=bus)
        # 上次进程被强杀时遗留的 running 任务标为 interrupted（spec §6.4）
        engine.recover_interrupted()
        cpu = CpuExecutor(settings.cpu_workers)
        ai = AiClient(settings)
    except BaseException:
        # 用嵌套 finally 保证 cpu 释放失败时 db 仍会被关闭；随后原样重抛原异常。
        try:
            if cpu is not None:
                cpu.shutdown(wait=False)
        finally:
            if db is not None:
                db.close()
        raise
    return AppState(settings=settings, db=db, projects=projects, tasks=tasks,
                    logs=logs, bus=bus, engine=engine, cpu=cpu, ai=ai)


def get_state(request: Request) -> AppState:
    return request.app.state.yibiao
