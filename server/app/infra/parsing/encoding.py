from __future__ import annotations

from pathlib import Path

import charset_normalizer


def detect_encoding(path: Path) -> str:
    """编码探测，对齐现有 chardet 用法（convert.mjs:9）。"""
    raw = Path(path).read_bytes()
    best = charset_normalizer.from_bytes(raw).best()
    if best is None:
        return "utf-8"
    return best.encoding


def read_text_with_encoding(path: Path, *, max_bytes: int | None = None) -> str:
    """按探测到的编码读全文，统一剥掉 UTF-8 BOM。

    Node 侧用 chardet + iconv-lite；这里用 charset-normalizer（同一探测思路，
    维护更活跃）。BOM 由 read_text 的 'utf-8-sig' 处理，其余编码手动剥。
    """
    raw = Path(path).read_bytes()
    if max_bytes is not None:
        raw = raw[:max_bytes]
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace")
    best = charset_normalizer.from_bytes(raw).best()
    encoding = best.encoding if best is not None else "utf-8"
    return raw.decode(encoding, errors="replace")
