r"""查重元数据的信号嗅探纯函数（P3d T2，R-P3d-11）。

逐字对齐参考实现 `client/electron/services/duplicateCheckService.cjs`
:620-655（rawSignalPattern / collectSignalSnippets / isReadableSignalSnippet /
collectBinarySignalSnippets）与 :693-701（addWpsSignalFields）。

docx（WPS 痕迹）与 OLE（流名/二进制痕迹）两路提取共用本模块。

**JS → Python 的移植要点**：

- `\b` / `\w` 一律 `re.ASCII`（JS 语义；中文备选词「账号/金山」是字面量，
  不受 ASCII 旗标影响）；
- `\p{Script=Han}` 用 Unicode Script=Han 的码点区间表近似（BMP 主块 + 扩展区；
  覆盖中文标书语料全部实际取值，见 `_HAN_RANGES`）；
- `Array.from(text)` 按**码点**切分——Python str 迭代天然同语义；
- JS `\s`（Unicode 空白，含 \u3000/\ufeff）用显式集合 `_JS_WHITESPACE`
  而非 Python 的 `\s`（后者多匹配 \x1c-\x1f、\x85，少匹配 \ufeff）。
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.duplicate.metadata_base import (
    add_list_field,
    safe_metadata_key,
)

# ---------------------------------------------------------------- 信号正则（:620）

_RAW_SIGNAL_RE = re.compile(
    r"(kingsoft|wps office|\bwps\b|\bkso\b|account|e-mail|email|mail|userid"
    r"|user id|user_id|uid|账号|金山)",
    re.IGNORECASE | re.ASCII,
)

# collectSignalSnippets 预处理：\u0000 直接剥除；其余控制符区间折叠为单空格
_SNAPSHOT_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+")
_WINDOW_WS_RE = re.compile(r"\s+")

# ---------------------------------------------------------------- Script=Han 区间表

# Unicode Script=Han 的码点区间（含扩展区；Unicode 15 数据）。
_HAN_RANGES: tuple[tuple[int, int], ...] = (
    (0x2E80, 0x2E99), (0x2E9B, 0x2EF3),   # CJK Radicals Supplement
    (0x2F00, 0x2FD5),                     # Kangxi Radicals
    (0x3005, 0x3005), (0x3007, 0x3007),   # 々 〇
    (0x3021, 0x3029), (0x3038, 0x303B),   # 苏州码子
    (0x3400, 0x4DBF),                     # Ext A
    (0x4E00, 0x9FFF),                     # URO
    (0xF900, 0xFA6D), (0xFA70, 0xFAD9),   # 兼容表意文字
    (0x20000, 0x2A6DF), (0x2A700, 0x2B739), (0x2B740, 0x2B81D),
    (0x2B820, 0x2CEA1), (0x2CEB0, 0x2EBE0),
    (0x2F800, 0x2FA1D), (0x30000, 0x3134A),
)

# JS `\s` 的精确字符集（MDN：\s = [\f\n\r\t\v\u0020\u00a0\u1680\u2000-\u200a
# \u2028\u2029\u202f\u205f\u3000\ufeff]）
_JS_WHITESPACE = frozenset(
    "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff")

# 可读性字符类里的显式标点（:640 类内除 \p{Han}/A-Za-z0-9/\s 之外的部分）
_READABLE_PUNCT = frozenset(".,:;_@/\\-()[]{}\"'"
                            "，。：；（）【】《》、")


def _is_han(char: str) -> bool:
    code = ord(char)
    return any(lo <= code <= hi for lo, hi in _HAN_RANGES)


def _is_readable_char(char: str) -> bool:
    r"""`:640` 可读性字符类 `[\p{Script=Han}A-Za-z0-9\s…标点…]` 的逐字对应。"""
    return (_is_han(char)
            or ("a" <= char <= "z") or ("A" <= char <= "Z")
            or ("0" <= char <= "9")
            or char in _JS_WHITESPACE
            or char in _READABLE_PUNCT)


# ---------------------------------------------------------------- 片段收集（:622-655）


def is_readable_signal_snippet(value: Any) -> bool:
    """对齐 `isReadableSignalSnippet`（svc.cjs :636-642）：可读字符占比 ≥ 0.75。"""
    text = str(value or "").strip()
    if not text or "\ufffd" in text:
        return False
    chars = list(text)  # 按码点（= JS Array.from 的代理对合并语义）
    readable = sum(1 for char in chars if _is_readable_char(char))
    return readable / max(len(chars), 1) >= 0.75


def collect_signal_snippets(value: Any, limit: int = 5) -> list[str]:
    """对齐 `collectSignalSnippets`（svc.cjs :622-634）。

    窗口 = 命中位置前 40 / 后 80 字符；片段折叠空白并 strip；
    不可读或重复的命中继续向后找（limit 只约束**产出**数）。
    """
    text = str(value or "").replace("\u0000", "")
    text = _SNAPSHOT_CTRL_RE.sub(" ", text)
    snippets: list[str] = []
    for match in _RAW_SIGNAL_RE.finditer(text):
        if len(snippets) >= limit:
            break
        start = max(0, match.start() - 40)
        end = min(len(text), match.end() + 80)
        snippet = _WINDOW_WS_RE.sub(" ", text[start:end]).strip()
        if is_readable_signal_snippet(snippet) and snippet not in snippets:
            snippets.append(snippet)
    return snippets


def collect_binary_signal_snippets(content: bytes) -> list[str]:
    """对齐 `collectBinarySignalSnippets`（svc.cjs :644-655）。

    三种候选解码（utf-16le / gb18030 / utf-8，各取前 3 条）合流去重，封顶 5 条。
    """
    buffer = bytes(content or b"")[: 1024 * 1024]
    candidates = (
        buffer[: len(buffer) - len(buffer) % 2].decode("utf-16-le", errors="replace"),
        buffer.decode("gb18030", errors="replace"),
        buffer.decode("utf-8", errors="replace"),
    )
    snippets: list[str] = []
    for text in candidates:
        for snippet in collect_signal_snippets(text, 3):
            if snippet not in snippets:
                snippets.append(snippet)
            if len(snippets) >= 5:
                return snippets
    return snippets


# ---------------------------------------------------------------- WPS 痕迹（:693-701）


def add_wps_signal_fields(fields: dict[str, str]) -> None:
    """对齐 `addWpsSignalFields`（svc.cjs :693-701）：快照遍历，`wps:` 键跳过。"""
    for key, value in list(fields.items()):
        if key.startswith("wps:"):
            continue
        haystack = f"{key} {value}"
        if not collect_signal_snippets(haystack, 1):
            continue
        add_list_field(fields, f"wps:{safe_metadata_key(key)}", value)
