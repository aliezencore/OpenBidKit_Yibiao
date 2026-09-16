from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class CpuExecutor:
    """CPU 密集段的受控线程池。

    查重指纹提取、相似度计算、docx 解析都是同步且吃 CPU 的，直接放在事件循环
    里会堵住整个服务（spec §6.3）。不用 asyncio.to_thread：它走事件循环的默认
    执行器，max_workers 高达 min(32, cpu+4)，对 CPU 密集任务过宽 —— 这里显式
    持有自己的池并设上限。
    """

    def __init__(self, max_workers: int) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, max_workers), thread_name_prefix="yibiao-cpu"
        )

    async def run(self, fn: Callable[..., T], *args: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, fn, *args)

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)
