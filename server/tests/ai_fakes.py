"""AI 假件（spec §11.3：AI 层用假 provider）。

`ScriptedAI` 按脚本顺序返回内容并记录全部调用（messages 原样保存，供断言
消息结构）；返回队列耗尽时**重复最后一个**（方便多段用例只写一段的返回）。
"""

from __future__ import annotations

from typing import Any


class ScriptedAI:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses) or [""]
        self._index = 0
        self.calls: list[list[dict[str, Any]]] = []

    @property
    def chat_count(self) -> int:
        return len(self.calls)

    async def chat(self, messages: list[dict[str, Any]], *, use_vision: bool = False) -> str:
        self.calls.append([dict(m) for m in messages])
        response = self._responses[min(self._index, len(self._responses) - 1)]
        self._index += 1
        return response

    async def aclose(self) -> None:
        """对齐 AiClient.aclose（lifespan 会调用）。"""
        return None

    async def collect_json_response(
        self, messages: list[dict[str, Any]], *, use_vision: bool = False
    ) -> dict[str, Any]:
        import json as _json

        content = await self.chat(messages, use_vision=use_vision)
        return _json.loads(content)
