from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TaskContextProtocol(Protocol):
    """领域函数看到的任务上下文。

    对应现有实现 runRejectionCheckTask 的 { updateTask, checkpointTask } 两个参数。
    """

    def progress(self, percent: int, label: str | None = None) -> None: ...

    def log(self, message: str) -> None: ...

    def checkpoint(self, state: dict[str, Any]) -> None: ...

    def raise_if_cancelled(self) -> None: ...


@runtime_checkable
class AiClientProtocol(Protocol):
    """领域函数看到的模型客户端，对应现有 aiService。"""

    async def chat(self, messages: list[dict[str, Any]], *, use_vision: bool = False) -> str: ...

    async def collect_json_response(
        self, messages: list[dict[str, Any]], *, use_vision: bool = False
    ) -> dict[str, Any]: ...


@runtime_checkable
class EventPublisherProtocol(Protocol):
    """任务事件发布口，对应现有 taskService 推给 Renderer 的 tasks:event。"""

    def publish(self, task_id: str, event: dict[str, Any]) -> None: ...
