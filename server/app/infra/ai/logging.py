from __future__ import annotations

from typing import Any


def _render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    text = str(value)
    return text.replace("\n", " ").replace("\r", " ")


def log_ai_failure(scope: str, payload: dict[str, Any]) -> None:
    """始终打印的单行 key=value 日志，不受任何 debug 开关控制。

    对齐现有 utils/aiLog.cjs 的 logAiFailure 语义：这是排查线上 AI 调用
    唯一的常开通道，因此不做级别过滤、不做多行输出。
    """
    parts = [f"ai_failure scope={_render(scope)}"]
    for key, value in payload.items():
        parts.append(f"{key}={_render(value)}")
    print(" ".join(parts), flush=True)
