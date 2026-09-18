"""四段分析进度与最新文案（纯函数，零 I/O）。

逐字对齐参考实现 `client/electron/services/duplicateCheckService.cjs`：

- `analysisProgress`（:2229-2233）
- `overallProgress`（:2235-2242）
- `latestAnalysisMessage`（:2245-2251）
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from app.domain.duplicate.state import ANALYSIS_SECTIONS, SectionState

DEFAULT_ANALYSIS_MESSAGE = "标书查重分析运行中。"


def _parse_progress(value: Any) -> int:
    """按 JS `Number(value.progress) || 0` 的语义解析进度。

    **有意偏离（登记在案）**：参考实现在 `:2232` 对不可解析的值（如 `"abc"`）会让
    `Number(...)` 得到 `NaN`，`Math.min(NaN, 99)` 与 `Math.max(0, NaN)` 都继续传播
    `NaN` —— 即 JS 返回的是 `NaN`。Python 的 `int` 返回契约装不下 `NaN`，故回落 `0`
    （最接近「无进度」的语义）。这是**接受的有意偏离**，不是逐字对齐。
    """
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def analysis_progress(state: SectionState | None) -> int:
    """单段进度：`None -> 0`；`success`/`error -> 100`；否则 clamp 到 [0, 99]。

    对齐 :2229-2233：

    ```js
    if (!value) return 0;
    if (value.status === 'success' || value.status === 'error') return 100;
    return Math.max(0, Math.min(Number(value.progress) || 0, 99));
    ```

    两处易错点按锚点保留：`error` 与 `success` **同为 100**（失败也是「跑完了」），
    未完成状态的上限是 **99** 而非 100。
    """
    if state is None:
        return 0
    if state.status in ("success", "error"):
        return 100
    return max(0, min(_parse_progress(state.progress), 99))


def overall_progress(states: Mapping[str, SectionState | None]) -> int:
    """四段等权平均后按 **half-up** 取整。

    对齐 :2235-2242：

    ```js
    return Math.round(values.reduce((sum, value) => sum + value, 0) / values.length);
    ```

    JS 的 `Math.round` 是 half-up（`Math.round(0.5) === 1`），Python 内建 `round()`
    是 half-even（`round(0.5) === 0`），故必须用 `math.floor(x + 0.5)`。
    与 P2b 的 `half_up` 保持同一写法。
    """
    values = [analysis_progress(states.get(section)) for section in ANALYSIS_SECTIONS]
    return math.floor(sum(values) / len(values) + 0.5)


def latest_analysis_message(states: Mapping[str, SectionState | None]) -> str:
    """按 `image -> content -> outline -> metadata -> 默认文案` 取第一条非空文案。

    对齐 :2245-2251 的 `||` 链：JS 里空字符串是 **falsy**，因此 `message == ""`
    会被跳过继续往下找（不是命中空串）。Python 的 `or` 语义一致。
    """
    ordered = (
        states.get("image"),
        states.get("content"),
        states.get("outline"),
        states.get("metadata"),
    )
    for state in ordered:
        message = state.message if state is not None else ""
        if message:
            return message
    return DEFAULT_ANALYSIS_MESSAGE
