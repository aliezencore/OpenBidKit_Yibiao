from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator


class EventBus:
    """进程内任务事件广播。方案 A 下 SSE 直接走这里，不查库。"""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, task_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(task_id, set()).add(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        queues = self._subscribers.get(task_id)
        if not queues:
            return
        queues.discard(queue)
        if not queues:
            self._subscribers.pop(task_id, None)

    def publish(self, task_id: str, event: dict[str, Any]) -> None:
        for queue in self._subscribers.get(task_id, set()):
            queue.put_nowait(event)

    def subscriber_count(self, task_id: str) -> int:
        return len(self._subscribers.get(task_id, set()))


async def sse_event_stream(
    bus: EventBus, task_id: str, *, heartbeat_seconds: float = 15.0
) -> AsyncIterator[str]:
    queue = bus.subscribe(task_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    finally:
        bus.unsubscribe(task_id, queue)
