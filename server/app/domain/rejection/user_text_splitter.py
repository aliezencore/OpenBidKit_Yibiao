"""超长文本按上下文窗口分段（P4b-1）。

逐字移植 `client/electron/utils/userTextSplitter.cjs`（215 行）：
围栏感知（不切在 ``` / ~~~ 代码块内）、边界打分（标题/换行/句号/分号/逗号五档）、
代理对安全、段长上下限约束。

**登记偏离（P4b-1）**：JS 字符串按 **UTF-16 码元**计长，Python 按**码点**计长——
两者对 BMP 字符（含全部中文）完全一致；仅当文本含非 BMP 字符（如 emoji）时，
段界可能相差 1。代理对保护在 Python 中恒为空操作（不存在半代理）。测试用 BMP 样本
做 oracle 对拍（`tests/fixtures/segment-split-oracle.json`，由参考实现直跑生成）。

另一个移植陷阱：JS `Math.round` 是**半数向上**（toward +∞），Python `round` 是
银行家舍入——P3b 已踩过（`toFixed(2)`），这里用 `_js_round = floor(x + 0.5)`。
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

DEFAULT_CONTEXT_LENGTH_LIMIT = 400000
DEFAULT_CONTEXT_LIMIT_RATIO = 0.8
STRICT_WINDOW_RATIO = 0.12
RELAXED_WINDOW_RATIO = 0.25
MIN_SEGMENT_RATIO = 0.35
MAX_SEGMENT_LIMIT_RATIO = 1.1

# 参考 :7-13 的五档边界（正则逐字；JS g 标志 → Python finditer）
_BOUNDARY_GROUPS: tuple[tuple[re.Pattern[str], ...], ...] = (
    (re.compile(r"\r?\n(?=\s{0,3}#{1,6}\s+)"), re.compile(r"\r?\n[ \t]*\r?\n")),
    (re.compile(r"\r?\n"),),
    (re.compile(r"[。！？!?]"),),
    (re.compile(r"[；;]"),),
    (re.compile(r"[，,、：:]"),),
)

_FENCE_RE = re.compile(r"(^|\n)(```|~~~)")


def _js_round(value: float) -> int:
    """JS `Math.round`：半数向上（Python `round` 是银行家舍入，不能直接用）。"""
    return math.floor(value + 0.5)


def _normalize_positive_integer(value: Any, fallback: int) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return math.floor(number) if number == number and number > 0 else fallback


def _normalize_positive_ratio(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number == number and number > 0 else fallback


def _context_length_limit(config: Mapping[str, Any] | None,
                          options: Mapping[str, Any]) -> int:
    source = (options.get("contextLengthLimit")
              if options.get("contextLengthLimit") is not None
              else (config or {}).get("context_length_limit"))
    return _normalize_positive_integer(source, DEFAULT_CONTEXT_LENGTH_LIMIT)


def _collect_markdown_fence_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    opened_at = -1
    opened_marker = ""
    for match in _FENCE_RE.finditer(text):
        marker_start = match.start() + len(match.group(1))
        marker = match.group(2)
        if opened_at < 0:
            opened_at = marker_start
            opened_marker = marker
        elif marker == opened_marker:
            ranges.append((opened_at, marker_start + len(marker)))
            opened_at = -1
            opened_marker = ""
    if opened_at >= 0:
        ranges.append((opened_at, len(text)))
    return ranges


def _is_inside_range(index: int, ranges: list[tuple[int, int]]) -> bool:
    for start, end in ranges:
        if index <= start:
            return False
        if index < end:
            return True
    return False


def _avoids_breaking_surrogate_pair(text: str, cut: int) -> int:
    """参考 :61-72 的代理对保护。Python 字符串是码点序列，**不存在半代理**——
    保留本函数只为与参考结构对齐（恒返回原值）。"""
    return cut


def _clamp_hard_cut(text: str, ideal_cut: float, previous_cut: int,
                    remaining_segments: int) -> int:
    minimum_cut = previous_cut + 1
    maximum_cut = len(text) - remaining_segments
    if maximum_cut < minimum_cut:
        return min(len(text), minimum_cut)
    clamped = min(maximum_cut, max(minimum_cut, _js_round(ideal_cut)))
    return _avoids_breaking_surrogate_pair(text, clamped)


class _CutState:
    __slots__ = ("ideal_cut", "previous_cut", "total_length", "remaining_segments",
                 "minimum_segment_length", "maximum_segment_length")

    def __init__(self, *, ideal_cut: float, previous_cut: int, total_length: int,
                 remaining_segments: int, minimum_segment_length: int,
                 maximum_segment_length: int) -> None:
        self.ideal_cut = ideal_cut
        self.previous_cut = previous_cut
        self.total_length = total_length
        self.remaining_segments = remaining_segments
        self.minimum_segment_length = minimum_segment_length
        self.maximum_segment_length = maximum_segment_length


def _can_use_candidate(candidate: int, state: _CutState) -> bool:
    if candidate <= state.previous_cut or candidate >= state.total_length:
        return False
    current_length = candidate - state.previous_cut
    if current_length < state.minimum_segment_length:
        return False
    if current_length > state.maximum_segment_length:
        return False
    if state.remaining_segments > 0:
        remaining_length = state.total_length - candidate
        if remaining_length < state.remaining_segments:
            return False
        if remaining_length / state.remaining_segments > state.maximum_segment_length:
            return False
    return True


def _find_boundary_for_group(text: str, from_index: int, to_index: int,
                             group: tuple[re.Pattern[str], ...], state: _CutState,
                             fence_ranges: list[tuple[int, int]]) -> int:
    best: tuple[int, float] | None = None
    content = text[from_index:to_index]
    for pattern in group:
        for match in pattern.finditer(content):
            candidate = from_index + match.start() + len(match.group(0))
            if not _is_inside_range(candidate, fence_ranges) and \
                    _can_use_candidate(candidate, state):
                score = abs(candidate - state.ideal_cut)
                if best is None or score < best[1]:
                    best = (candidate, score)
    return best[0] if best else 0


def _find_natural_cut(text: str, ideal_cut: float, previous_cut: int, state: _CutState,
                      fence_ranges: list[tuple[int, int]], radius: int) -> int:
    from_index = max(previous_cut + 1, math.floor(ideal_cut - radius))
    to_index = min(len(text) - state.remaining_segments, math.ceil(ideal_cut + radius))
    if to_index <= from_index:
        return 0
    for group in _BOUNDARY_GROUPS:
        cut = _find_boundary_for_group(text, from_index, to_index, group, state,
                                       fence_ranges)
        if cut:
            return cut
    return 0


def split_user_text_by_context_limit(text: Any,
                                     config: Mapping[str, Any] | None = None,
                                     options: Mapping[str, Any] | None = None) -> list[str]:
    """参考 `splitUserTextByContextLimit`（userTextSplitter.cjs:131-196）。

    `config` 取 AI 配置的 `context_length_limit`（snake_case，与服务端一致）；
    `options` 覆盖 camelCase 键（`contextLengthLimit` 等，与参考一致）。
    """
    source = str(text if text is not None else "")
    options = options or {}
    context_length_limit = _context_length_limit(config, options)
    limit_ratio = _normalize_positive_ratio(options.get("limitRatio"),
                                            DEFAULT_CONTEXT_LIMIT_RATIO)
    segment_limit = max(1, math.floor(context_length_limit * limit_ratio))

    if len(source) <= segment_limit:
        return [source]

    segment_count = math.ceil(len(source) / segment_limit)
    target_size = max(1, math.ceil(len(source) / segment_count))
    strict_radius = max(1, math.floor(
        target_size * _normalize_positive_ratio(options.get("strictWindowRatio"),
                                                STRICT_WINDOW_RATIO)))
    relaxed_radius = max(strict_radius, math.floor(
        target_size * _normalize_positive_ratio(options.get("relaxedWindowRatio"),
                                                RELAXED_WINDOW_RATIO)))
    maximum_segment_length = max(target_size, math.ceil(
        segment_limit * _normalize_positive_ratio(options.get("maxSegmentLimitRatio"),
                                                  MAX_SEGMENT_LIMIT_RATIO)))
    minimum_segment_length = max(1, math.floor(
        target_size * _normalize_positive_ratio(options.get("minSegmentRatio"),
                                                MIN_SEGMENT_RATIO)))
    fence_ranges = _collect_markdown_fence_ranges(source)
    cuts: list[int] = []
    previous_cut = 0

    for segment_index in range(1, segment_count):
        ideal_cut = (len(source) * segment_index) / segment_count
        remaining_segments = segment_count - segment_index
        state = _CutState(
            ideal_cut=ideal_cut, previous_cut=previous_cut, total_length=len(source),
            remaining_segments=remaining_segments,
            minimum_segment_length=minimum_segment_length,
            maximum_segment_length=maximum_segment_length,
        )
        natural_cut = (
            _find_natural_cut(source, ideal_cut, previous_cut, state, fence_ranges,
                              strict_radius)
            or _find_natural_cut(source, ideal_cut, previous_cut, state, fence_ranges,
                                 relaxed_radius))
        cut = natural_cut or _clamp_hard_cut(source, ideal_cut, previous_cut,
                                             remaining_segments)
        cuts.append(cut)
        previous_cut = cut

    parts: list[str] = []
    start = 0
    for cut in cuts:
        parts.append(source[start:cut])
        start = cut
    parts.append(source[start:])
    return parts
