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
    return AppState(settings=settings, db=db, projects=projects, tasks=tasks,
                    logs=logs, bus=bus, engine=engine, cpu=cpu, ai=ai)


def get_state(request: Request) -> AppState:
    return request.app.state.yibiao
