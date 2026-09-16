from __future__ import annotations

from typing import Any

from app.domain.shared.protocols import EventPublisherProtocol
from app.infra.repositories.task_logs import TaskLogRepository
from app.infra.repositories.tasks import TaskRepository


class TaskCancelled(Exception):
    """协作式取消。领域函数在每个耗时步骤前调用 raise_if_cancelled()。"""


class _NullPublisher:
    def publish(self, task_id: str, event: dict[str, Any]) -> None:
        return None


class TaskContext:
    def __init__(
        self,
        *,
        task_id: str,
        project_id: str,
        domain: str,
        kind: str,
        repo: TaskRepository,
        log_repo: TaskLogRepository,
        publisher: EventPublisherProtocol | None = None,
        progress_interval_ms: int = 200,
        now_fn,
        monotonic_fn,
    ) -> None:
        self.task_id = task_id
        self.project_id = project_id
        self.domain = domain
        self.kind = kind
        self._repo = repo
        self._log_repo = log_repo
        self._publisher = publisher or _NullPublisher()
        self._interval = max(0, progress_interval_ms) / 1000.0
        self._now = now_fn
        self._monotonic = monotonic_fn
        self._last_write_at = float("-inf")
        self._last_percent: int | None = None
        self._cancelled = False

    def progress(self, percent: int, label: str | None = None) -> None:
        value = max(0, min(100, int(percent)))
        now = self._monotonic()
        changed = value != self._last_percent
        elapsed = now - self._last_write_at
        persisted = False
        # 节流：仅在「百分比变化」且「距上次落库已满间隔」时才写库。
        # 百分比未变化（例如逐文件循环重复落到同一整数）一律不写库，
        # 但仍要保留「始终广播」这一副作用，仅把 persisted 标记为 False。
        if changed and elapsed >= self._interval:
            self._repo.set_progress(self.task_id, value, label)
            self._last_write_at = now
            self._last_percent = value
            persisted = True
        self._publisher.publish(self.task_id, {
            "type": "progress", "taskId": self.task_id,
            "progress": value, "stageLabel": label, "persisted": persisted,
        })

    def log(self, message: str) -> None:
        self._log_repo.append(self.project_id, self.domain, self.kind, self.task_id,
                              message, self._now())
        self._publisher.publish(self.task_id, {
            "type": "log", "taskId": self.task_id, "message": message,
        })

    def checkpoint(self, state: dict[str, Any]) -> None:
        self._repo.set_checkpoint(self.task_id, state)

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise TaskCancelled(self.task_id)

    def mark_cancelled(self) -> None:
        """由 TaskEngine.cancel() 调用；领域函数在下一个 raise_if_cancelled() 处退出。"""
        self._cancelled = True
