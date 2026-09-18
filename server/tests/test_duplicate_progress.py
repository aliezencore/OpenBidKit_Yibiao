from __future__ import annotations

import pytest

from app.domain.duplicate.progress import (
    analysis_progress,
    latest_analysis_message,
    overall_progress,
)
from app.domain.duplicate.state import SectionState

_SECTION_KEYS = ("metadata", "outline", "content", "image")


def _s(status="running", progress=0, message=""):
    return SectionState(status=status, progress=progress, message=message)


def test_analysis_progress_none_is_zero():
    assert analysis_progress(None) == 0


def test_analysis_progress_success_is_100_regardless_of_progress_field():
    assert analysis_progress(_s("success", progress=3)) == 100


def test_analysis_progress_error_is_100_regardless_of_progress_field():
    assert analysis_progress(_s("error", progress=3)) == 100


def test_analysis_progress_running_clamps_to_99():
    assert analysis_progress(_s("running", progress=100)) == 99


def test_analysis_progress_negative_clamps_to_zero():
    assert analysis_progress(_s("running", progress=-5)) == 0


def test_analysis_progress_pending_keeps_progress():
    assert analysis_progress(_s("pending", progress=7)) == 7


def test_analysis_progress_parses_numeric_string():
    """`Number(value.progress) || 0` 对 "7" 得 7。"""
    assert analysis_progress(_s("running", progress="7")) == 7


def test_analysis_progress_matches_reference_on_garbage_progress():
    """参考实现 :2232 对非数字 progress 返回 NaN（Math 链条传播）。

    Python 无法返回 NaN 给 int 契约，故本实现对不可解析值回落 0 —— 这是
    **有意偏离**，须在报告与 docstring 里写明。
    """
    assert analysis_progress(_s("running", progress="abc")) == 0


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan"), "1e400"])
def test_analysis_progress_non_finite_falls_back_without_raising(value):
    """非有限值不得抛异常。

    `int(float("inf"))` 抛的是 `OverflowError`，**不是** `ValueError`/`TypeError`；
    只捕后两者的实现会在这里崩掉。参考实现的 `Math.min(Infinity, 99)` 得 99、不抛，
    故我们至少不能抛，统一回落 0（并登记为偏离的一部分）。
    """
    assert analysis_progress(_s("running", progress=value)) == 0


def test_analysis_progress_parseable_huge_value_clamps_to_99():
    """F6：`"1e308"` 是**可解析**的有限巨大值，clamp 后得 99 —— 不是 0。

    这条容易与上面的「非有限回落 0」混淆：`"1e400"` 溢出成 `Infinity`，
    而 `"1e308"` 仍在 double 范围内（约 `1.8e308` 才是上界），
    `float("1e308")` 是有限值，`int()` 也不抛。

    参考实现（Node 实测）：`Math.min(1e308, 99) === 99`，故本实现必须给 **99**。
    钉住它，避免有人看到「巨大值」就把阈值判断顺手写成回落 0 ——
    那样会和参考实现分叉（99 vs 0），且 99 才是「进度拉满但未完成」的正确语义。
    """
    assert analysis_progress(_s("running", progress="1e308")) == 99
    assert analysis_progress(_s("running", progress=1e308)) == 99


def test_analysis_progress_lenient_parsing_is_registered():
    """F5：登记并钉住「我们比 JS `Number()` 更宽容」这条**反向偏离**。

    `float()` 接受 `Unicode Nd` 数字与下划线分隔符，JS `Number()` 不接受：

    | 输入 | JS `Number()`（Node 实测） | 参考实现（`|| 0` 后） | 本实现 |
    |---|---|---|---|
    | `"７"`（全角 7） | `NaN` | `0` | `7` |
    | `"１２"` | `NaN` | `0` | `12` |
    | `"1_0"` | `NaN` | `0` | `10` |

    我们**有意保留**这个宽容行为（理由见 `_parse_progress` docstring 的裁定）：
    无副作用、无数据风险，且参考实现自己的严格性并不自洽
    （它接受 `Number("0x10") === 16`）。此用例把该行为**固定下来**，
    任何一边的收紧/放开都会在 CI 里被看见。
    """
    assert analysis_progress(_s("running", progress="７")) == 7
    assert analysis_progress(_s("running", progress="１２")) == 12
    assert analysis_progress(_s("running", progress="1_0")) == 10


def test_analysis_progress_hex_string_diverges_from_reference():
    """F5 的第二个实例：`"0x10"` 两端**方向相反**，一并钉住。

    Node 实测参考实现：`Number("0x10") === 16` → `analysisProgress` 得 **16**；
    本实现 `float("0x10")` 抛 `ValueError` → 回落 **0**。
    这是**唯一一处我们比参考实现更严格**的输入，登记在案（不为它引入十六进制解析）。
    """
    assert analysis_progress(_s("running", progress="0x10")) == 0
    assert analysis_progress(_s("running", progress="16")) == 16


@pytest.mark.parametrize("value,expected", [
    (0, 0), (25, 25), (50, 50), (75, 75),
    # 修正计划笔误：`running` 的 progress 在 analysisProgress 里被 clamp 到上限 99
    # （:2232 的 `Math.min(..., 99)`），四段等权平均仍是 99，**不是 100**。
    # 参考实现（Node 实测 `Math.round(396/4) === 99`）与逐字对齐一致。
    # 想拿 100 必须四段 status 都是 success/error——见 test_overall_progress_all_success_is_100。
    (100, 99),
])
def test_overall_progress_equal_weight_four_sections(value, expected):
    states = {k: _s("running", progress=value) for k in _SECTION_KEYS}
    assert overall_progress(states) == expected


def test_overall_progress_averages_and_rounds_half_up():
    """三个 0 一个 2 → 2/4 = 0.5 → Math.round 上取为 1（不是 Python 的 round→0）。"""
    states = {
        "metadata": _s("running", progress=2),
        "outline": _s("pending", progress=0),
        "content": _s("pending", progress=0),
        "image": _s("pending", progress=0),
    }
    assert overall_progress(states) == 1


@pytest.mark.parametrize("values,expected", [
    ((1, 0, 0, 0), 0),    # 0.25 → floor(0.75) = 0（不是 1，防"一律上取"）
    ((1, 1, 1, 0), 1),    # 0.75 → floor(1.25) = 1（不是 0，防"一律下取"）
    ((3, 3, 3, 2), 3),    # 2.75 → floor(3.25) = 3
])
def test_overall_progress_quarter_boundaries(values, expected):
    states = {k: _s("running", progress=v) for k, v in zip(_SECTION_KEYS, values)}
    assert overall_progress(states) == expected


def test_overall_progress_treats_missing_section_as_zero():
    assert overall_progress({"metadata": _s("success")}) == 25


def test_overall_progress_all_success_is_100():
    states = {k: _s("success") for k in _SECTION_KEYS}
    assert overall_progress(states) == 100


def test_latest_message_priority_image_first():
    states = {
        "metadata": _s(message="m"), "outline": _s(message="o"),
        "content": _s(message="c"), "image": _s(message="i"),
    }
    assert latest_analysis_message(states) == "i"


def test_latest_message_falls_back_in_order():
    states = {"metadata": _s(message="m"), "outline": _s(message="o"),
              "content": _s(message="c")}
    assert latest_analysis_message(states) == "c"


def test_latest_message_default():
    assert latest_analysis_message({}) == "标书查重分析运行中。"


def test_latest_message_skips_empty_strings_js_truthiness():
    """:2246-2250 是 `||` 链：JS 里空字符串 falsy，"" 的 message 会被跳过。

    钉住实现不能用 `is not None` 判定。
    """
    states = {"metadata": _s(message="m"), "image": _s(message="")}
    assert latest_analysis_message(states) == "m"
