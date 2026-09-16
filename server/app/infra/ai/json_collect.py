from __future__ import annotations

import json
from typing import Any


def _strip_wrappers(text: str) -> str:
    trimmed = text.strip()
    if trimmed.startswith("'''") and trimmed.endswith("'''") and len(trimmed) >= 6:
        trimmed = trimmed[3:-3].strip()
    if trimmed.startswith("```"):
        lines = trimmed.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        trimmed = "\n".join(lines).strip()
    return trimmed


def _first_balanced_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出里抽出第一个完整 JSON 对象。

    依次尝试：原文直解 → 去三引号/代码围栏 → 括号配平切片。
    三条路径对齐现有 aiService 的 JSON 抽取行为。
    """
    if not text:
        return None
    for candidate in (_strip_wrappers(text), _first_balanced_object(_strip_wrappers(text))):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None
