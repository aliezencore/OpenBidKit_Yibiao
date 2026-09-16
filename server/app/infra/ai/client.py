from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import ModelConfig, Settings
from app.infra.ai.json_collect import extract_json
from app.infra.ai.logging import log_ai_failure

REPAIR_PROMPT = (
    "上一次输出不是合法 JSON。请只输出一个合法的 JSON 对象，不要任何解释、"
    "不要 Markdown 代码围栏、不要前后缀文字。"
)


class AiRequestError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AiClient:
    """OpenAI 兼容客户端。

    刻意不引 openai SDK：现有实现（aiService.cjs）是裸 fetch，
    引 SDK 会引入请求体默认值、重试、超时等行为差异，破坏与现状的对照。
    """

    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.text_model.timeout_seconds),
            transport=transport,
        )
        self._gate = asyncio.Semaphore(settings.ai_max_concurrency)

    async def aclose(self) -> None:
        await self._http.aclose()

    def _resolve(self, use_vision: bool) -> ModelConfig:
        config = self._settings.vision_model if use_vision else self._settings.text_model
        if not config.base_url.strip():
            label = "视觉模型" if use_vision else "文本模型"
            raise AiRequestError(f"请先配置{label} Base URL")
        return config

    async def chat(self, messages: list[dict[str, Any]], *, use_vision: bool = False) -> str:
        config = self._resolve(use_vision)
        url = f"{config.base_url.rstrip('/')}/chat/completions"
        body = {"model": config.model_name, "messages": messages}
        headers = {"Authorization": f"Bearer {config.api_key}"}
        async with self._gate:
            try:
                response = await self._http.post(
                    url, json=body, headers=headers,
                    timeout=httpx.Timeout(config.timeout_seconds),
                )
            except httpx.HTTPError as error:
                log_ai_failure("chat", {"type": "transport-error", "error": str(error)})
                raise AiRequestError(f"模型请求失败：{error}") from error
        if response.status_code >= 400:
            log_ai_failure("chat", {"type": "http-error", "status": response.status_code,
                                    "model": config.model_name})
            raise AiRequestError(
                f"模型返回 HTTP {response.status_code}：{response.text[:200]}",
                status_code=response.status_code,
            )
        try:
            return response.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError, TypeError) as error:
            log_ai_failure("chat", {"type": "shape-error", "body": response.text[:200]})
            raise AiRequestError("模型响应结构不符合预期") from error

    async def collect_json_response(
        self, messages: list[dict[str, Any]], *, use_vision: bool = False
    ) -> dict[str, Any]:
        content = await self.chat(messages, use_vision=use_vision)
        parsed = extract_json(content)
        if parsed is not None:
            return parsed
        log_ai_failure("collect-json", {"type": "invalid-json", "attempt": 1,
                                        "preview": content[:120]})
        repair_messages = [
            *messages,
            {"role": "assistant", "content": content},
            {"role": "user", "content": REPAIR_PROMPT},
        ]
        repaired = await self.chat(repair_messages, use_vision=use_vision)
        parsed = extract_json(repaired)
        if parsed is None:
            log_ai_failure("collect-json", {"type": "invalid-json", "attempt": 2,
                                            "preview": repaired[:120]})
            raise AiRequestError("模型两次都未返回合法 JSON")
        return parsed
