"""四段分析进度与最新文案（纯函数，零 I/O）。

逐字对齐参考实现 `client/electron/services/duplicateCheckService.cjs`：

- `analysisProgress`（:2229-2233）
- `overallProgress`（:2235-2242）
- `latestAnalysisMessage`（:2245-2251）

**偏离登记**（详见 `_parse_progress` 的 docstring 与报告 fix-round 1）：

- D1 `NaN` 不可用 `int` 表达 → 不可解析值回落 `0`（可观察结果与参考一致）。
- D2 Python `float()` 比 JS `Number()` **宽松**（如全角数字 `"７"`）：保留宽容行为。
- D3 `"1e308"` 等有限巨大值 → clamp 到 `99`（与参考 `Math.min(1e308, 99)` 一致）。
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from app.domain.duplicate.state import ANALYSIS_SECTIONS, SectionState

DEFAULT_ANALYSIS_MESSAGE = "标书查重分析运行中。"


def _parse_progress(value: Any) -> int:
    """按 JS `Number(value.progress) || 0` 的语义解析进度。

    **偏离 D1（登记在案）—— 不可解析值回落 0**：参考实现在 `:2232` 对不可解析的
    值会让 `Number(...)` 得到 `NaN`。经 Node 实测，`NaN` 在 `|| 0` 处即被兜住
    （`NaN` 是 falsy），而 `Number("abc")` 同样先走 `|| 0`，故**可观察结果两端一致
    都是 0**；真正的差异只在「中间值是不是 `NaN`」。Python 的 `int` 返回契约装不下
    `NaN`（`int(float("nan"))` 抛 `ValueError`），故回落 `0`。**接受的有意偏离。**

    **偏离 D2（fix-round 1 / F5 登记）—— Python `float()` 比 JS `Number()` 宽松**：
    这是**反向偏离**（我们比参考实现**更宽容**），即解析出了参考实现视为垃圾的值：

    | 输入 | JS `Number()` | 参考实现（`|| 0` 后） | 本实现 |
    |---|---|---|---|
    | `"７"`（全角数字） | `NaN` | `0` | **`7`** |
    | `"１２"` | `NaN` | `0` | **`12`** |
    | `"1_0"`（下划线分隔符） | `NaN` | `0` | **`10`** |

    （Node 实测：`Number("７")` 为 `NaN`；Python `float("７")` 为 `7.0`。
    这些字符是 `Unicode Nd` 数字、`float()` 认，而 JS 只认 ASCII。）

    **裁定：选择保留宽容行为（不改实现）**，理由：
    1. 影响面为零 —— `progress` 由本服务自己写入，正常取值是 `int`/ASCII 数字串；
       全角数字只可能来自手工改库或异常客户端，届时「显示 7%」比「显示 0%」
       更接近数据本意，不会造成数据损坏或安全问题；
    2. 收紧成 ASCII 正则（形如 `^[+-]?[0-9]+([.][0-9]+)?$`）会**新增**自己的解析规则，
       而 `float()` 已经覆盖了参考实现接受的 `""`→0（见下）、空白、`+7` 等
       `Number()` 同样接受的集合；
    3. 参考实现在这里的「严格」本身也**不自洽**：它接受 `"0x10"`（`Number("0x10")`
       为 `16`），若我们真的逐字对齐 `Number()`，就得实现包括十六进制、`Infinity`
       字面量在内的整套 JS 数字文法 —— 成本远大于收益。
    结论：宽容方向**无害**，保留并登记。用例 `test_analysis_progress_lenient_parsing_is_registered`。

    注意 `""` 与 `"   "`：`float()` 抛 `ValueError` → 回落 `0`；
    JS `Number("")` 为 `0`、`Number("   ")` 也为 `0` —— 两端都是 0，一致。

    顺带登记（fix-round 1 / F6）：**`"1e308"` 解析为有限巨大值，clamp 到 `99`**，
    并不落在「不可解析 → 0」的叙事里。它**与参考实现一致**
    （`Math.min(1e308, 99) === 99`），用例
    `test_analysis_progress_parseable_huge_value_clamps_to_99` 钉住。
    """
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        # `OverflowError`：`float("inf")` / `float("1e400")` 无法转成 int。
        # 参考实现里 `Math.min(Infinity, 99)` 得 99（不是 NaN，也不抛），
        # 但 Python 侧无法无异常地表达，故并入「不可解析」回落 0。
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
