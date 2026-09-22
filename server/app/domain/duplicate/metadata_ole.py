r"""查重元数据的 OLE/CFB 提取纯函数（P3d T3，R-P3d-11）。

逐字对齐参考实现 `client/electron/services/duplicateCheckService.cjs`
:397-618（属性集二进制解析全家）、:657-691（结构/信号）、:743-760
（extractOleMetadata）。

**分层**：本模块只吃 CFB 条目形状（`CfbEntry`，由 infra 适配层从 olefile
构建）与属性集流字节；CFB 文件 I/O 在 `app/infra/parsing/metadata_extract.py`。

**cfb → 适配层形状的实测契约**（oracle-ole.out.json 的 cfb_shape 钉定）：

- FullPaths 无前导 `/`：root 是 ``Root Entry/``、storage 尾部带 `/`、流不带；
  因此参考 :676 的 `path.replace(/^\/Root Entry\/?/i, '')` **不生效**
  （ole_stream_paths 保留 `Root Entry/` 前缀，oracle 实证）；
- 除 root（type 5，content undefined）外，storage/流/空槽（type 0）都有
  content（可为空 Buffer）→ 全部进入参考的 streamEntries
  （oracle：12 条 FileIndex → ole_stream_count = 11）；
- 空槽（type 0）path 为 `/`，且**参与** streamPaths 计数与指纹。

**findCfbEntry 的收敛**（oracle find_probe 钉定）：四个候选（原名、`/` 前缀、
`!` 替换、`/!` 前缀）在 `\u0005SummaryInformation` 两个已知流上的行为等价于
「basename 做 `\u0005` ↔ `!` 归一后的精确匹配」；适配层只暴露这两个流，
据此收敛。
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.domain.duplicate.js_date import to_iso_z
from app.domain.duplicate.metadata_base import (
    add_field,
    add_list_field,
    normalize_value,
    summarize_values,
    hash_text,
    safe_metadata_key,
    unique_sorted_values,
    yes_no,
)
from app.domain.duplicate.metadata_signals import (
    add_wps_signal_fields,
    collect_binary_signal_snippets,
    collect_signal_snippets,
)

# ---------------------------------------------------------------- CFB 条目形状


@dataclass(frozen=True)
class CfbEntry:
    """cfb.FileIndex[i] + cfb.FullPaths[i] 的等价形状（见模块 docstring 契约）。"""

    path: str                      # cfb FullPaths 形状（'Root Entry/…'）
    entry_type: int                # 1=storage 2=stream 5=root 0=空槽
    size: int                      # 目录项声明的流大小
    content: bytes | None          # root 为 None；storage/空槽为空 bytes


def _entry_name(path: str) -> str:
    if path in ("", "/"):
        return ""
    return path.rstrip("/").rsplit("/", 1)[-1]


def _norm_bang(name: str) -> str:
    """属性集流名的**单向正典化**：`\u0005` → `!`，`!` 原样。

    findCfbEntry（:610-618）的四个候选（原名、`/` 前缀、`!` 替换、`/!` 前缀）
    在 cfb.find 上等价于「basename 正典化后精确匹配」——双向交换会在 wanted
    为 `!` 形时把两侧都折回 `\u0005` 形而失配（oracle find_probe 钉定）。
    """
    if name.startswith("\x05"):
        return "!" + name[1:]
    return name


def find_cfb_stream(entries: Sequence[CfbEntry], stream_name: str) -> bytes | None:
    """`findCfbEntry`（svc.cjs :610-618）在这两个已知流上的等价收敛。

    basename 归一匹配（`\u0005` ↔ `!`）；命中即返回 content。
    """
    wanted = _norm_bang(_entry_name(stream_name))
    for entry in entries:
        # :615 `if (entry?.content)` —— Buffer 是对象，空 Buffer 也 truthy
        #（与 :663 的 `content?.length` 长度判定不同），故用 is not None
        if _norm_bang(_entry_name(entry.path)) == wanted and entry.content is not None:
            return entry.content
    return None


# ---------------------------------------------------------------- 属性集映射表

SUMMARY_PROPERTY_MAP: dict[int, dict[str, Any]] = {
    0x01: {"key": "code_page"},
    0x02: {"key": "title"},
    0x03: {"key": "subject"},
    0x04: {"key": "author"},
    0x05: {"key": "keywords"},
    0x06: {"key": "description"},
    0x07: {"key": "template"},
    0x08: {"key": "last_modified_by"},
    0x09: {"key": "revision"},
    0x0A: {"key": "total_time", "kind": "duration_filetime"},
    0x0B: {"key": "last_printed"},
    0x0C: {"key": "created"},
    0x0D: {"key": "modified"},
    0x0E: {"key": "pages"},
    0x0F: {"key": "words"},
    0x10: {"key": "characters"},
    0x12: {"key": "application"},
    0x13: {"key": "doc_security"},
}

DOC_SUMMARY_PROPERTY_MAP: dict[int, dict[str, Any]] = {
    0x01: {"key": "code_page"},
    0x02: {"key": "category"},
    0x03: {"key": "presentation_format"},
    0x04: {"key": "bytes"},
    0x05: {"key": "lines"},
    0x06: {"key": "paragraphs"},
    0x07: {"key": "slides"},
    0x08: {"key": "notes"},
    0x09: {"key": "hidden_slides"},
    0x0A: {"key": "multimedia_clips"},
    0x0B: {"key": "scale_crop"},
    0x0C: {"key": "heading_pairs"},
    0x0D: {"key": "titles_of_parts"},
    0x0E: {"key": "manager"},
    0x0F: {"key": "company"},
    0x10: {"key": "links_dirty"},
    0x11: {"key": "characters_with_spaces"},
    0x13: {"key": "shared_doc"},
    0x16: {"key": "hlinks_changed"},
    0x17: {"key": "app_version", "kind": "version"},
    0x1A: {"key": "content_type"},
    0x1B: {"key": "content_status"},
    0x1C: {"key": "language"},
    0x1D: {"key": "document_version"},
}

# ---------------------------------------------------------------- 边界读取（:397-415）


def align4(value: int) -> int:
    return value + ((4 - (value % 4)) % 4)


def read_uint16_le(buffer: bytes, offset: int) -> int:
    return int.from_bytes(buffer[offset:offset + 2], "little") if offset + 2 <= len(buffer) else 0


def read_int16_le(buffer: bytes, offset: int) -> int:
    return int.from_bytes(buffer[offset:offset + 2], "little", signed=True) if offset + 2 <= len(buffer) else 0


def read_uint32_le(buffer: bytes, offset: int) -> int:
    return int.from_bytes(buffer[offset:offset + 4], "little") if offset + 4 <= len(buffer) else 0


def read_int32_le(buffer: bytes, offset: int) -> int:
    return int.from_bytes(buffer[offset:offset + 4], "little", signed=True) if offset + 4 <= len(buffer) else 0


def read_double_le(buffer: bytes, offset: int) -> float:
    # 参考 :521 无边界检查——越界抛异常（JS RangeError ↔ struct.error）
    return struct.unpack("<d", buffer[offset:offset + 8])[0]


# ---------------------------------------------------------------- 代码页解码（:417-436）

_CODE_PAGE_TABLE = {
    936: "gb18030", 54936: "gb18030", 950: "big5", 932: "shift_jis",
    949: "euc-kr", 65001: "utf-8",
    # 参考 :424 把 1200 与 1201 **都**映射到 utf16le（大端码页也按小端解，
    # 已知怪癖，逐字保留）；Python 用 utf-16-le 表达
    1200: "utf-16-le", 1201: "utf-16-le",
}


def _js_number(value: Any) -> float:
    """JS `Number()` 的对应物（只覆盖属性值域里会出现的类型）。

    str 规则：空白串/空串 → 0；`0x` 前缀 → 十六进制；其余走 float
    （含十进制/指数/Infinity）；非法 → NaN。bool → 1/0。
    """
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            if text.lower().startswith(("0x", "-0x", "+0x")):
                return float(int(text, 16))
            return float(text)
        except ValueError:
            return float("nan")
    return float("nan")


def code_page_to_encoding(code_page: Any) -> str:
    """对齐 `codePageToEncoding`（:417-427）：`Number(codePage) || 1252` 后查表。

    Python 无 'windows1252' 别名 → 用 'windows-N'（iconv 两种名字都能解）。
    """
    value = _js_number(code_page)
    if value != value or value == 0:  # NaN 或 0 → falsy → 1252
        value = 1252.0
    if value in (936, 54936):
        return "gb18030"
    if value == 950:
        return "big5"
    if value == 932:
        return "shift_jis"
    if value == 949:
        return "euc-kr"
    if value == 65001:
        return "utf-8"
    if value in (1200, 1201):
        return "utf-16-le"
    if 1250 <= value <= 1258:
        return f"windows-{int(value)}" if float(value).is_integer() else f"windows-{value}"
    return "latin1"


def decode_code_page_buffer(buffer: bytes, code_page: Any) -> str:
    encoding = code_page_to_encoding(code_page)
    try:
        return buffer.decode(encoding, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return buffer.decode("latin1")


def _node_utf16le_decode(raw: bytes) -> str:
    """Node `Buffer.toString('utf16le')`：奇数长度时**静默丢弃**末尾不完整字节
    （Python 的 utf-16-le 解码没有这个容忍，须先截偶）。"""
    if len(raw) % 2:
        raw = raw[:-1]
    return raw.decode("utf-16-le", errors="replace")


def clean_ole_string(value: Any) -> str:
    """`cleanOleString`（svc.cjs :438-440）：剥尾部/中间 NUL 再 trim。"""
    text = str(value or "")
    text = text.rstrip("\x00").replace("\x00", "")
    return text.strip()


# ---------------------------------------------------------------- FILETIME（:442-466）

_EPOCH_DELTA_MS = 11644473600000  # 1601-01-01 → 1970-01-01


def parse_file_time_value(buffer: bytes, offset: int) -> str:
    low = read_uint32_le(buffer, offset)
    high = read_uint32_le(buffer, offset + 4)
    if not low and not high:
        return ""
    ticks = (high << 32) + low
    unix_ms = ticks // 10000 - _EPOCH_DELTA_MS
    try:
        moment = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=unix_ms)
    except (OverflowError, OSError, ValueError):
        return ""  # JS new Date(越界 ms) → Invalid Date → ''
    return to_iso_z(moment)


def parse_file_time_duration(buffer: bytes, offset: int) -> str:
    low = read_uint32_le(buffer, offset)
    high = read_uint32_le(buffer, offset + 4)
    ticks = (high << 32) + low
    if not ticks:
        return ""
    seconds = ticks // 10000000
    if seconds <= 0:
        return ""
    if seconds < 60:
        return f"{seconds} 秒"
    minutes = int(seconds / 60 + 0.5)  # JS Math.round（half-up，正数域）
    if minutes < 60:
        return f"{minutes} 分钟"
    hours = minutes // 60
    rest_minutes = minutes % 60
    return f"{hours} 小时 {rest_minutes} 分钟" if rest_minutes else f"{hours} 小时"


# ---------------------------------------------------------------- 类型化属性（:468-536）


def parse_lpstr(buffer: bytes, offset: int, code_page: Any, padded: bool = True):
    length = read_uint32_le(buffer, offset)
    start = offset + 4
    byte_length = max(0, min(length, len(buffer) - start))
    raw = buffer[start:start + byte_length]
    return {
        "value": clean_ole_string(decode_code_page_buffer(raw, code_page)),
        "nextOffset": align4(start + byte_length) if padded else start + byte_length,
    }


def parse_lpwstr(buffer: bytes, offset: int, padded: bool = True):
    char_length = read_uint32_le(buffer, offset)
    start = offset + 4
    byte_length = max(0, min(char_length * 2, len(buffer) - start))
    raw = buffer[start:start + byte_length]
    return {
        "value": clean_ole_string(_node_utf16le_decode(raw)),
        "nextOffset": align4(start + byte_length) if padded else start + byte_length,
    }


def parse_vector_string_value(buffer: bytes, offset: int, prop_type: int, code_page: Any):
    count = read_uint32_le(buffer, offset)
    values: list[str] = []
    cursor = offset + 4
    index = 0
    while index < count and cursor < len(buffer):
        parsed = (parse_lpwstr(buffer, cursor, True) if prop_type == 0x101F
                  else parse_lpstr(buffer, cursor, code_page, False))
        if parsed["value"]:
            values.append(parsed["value"])
        cursor = parsed["nextOffset"]
        index += 1
    return {"value": values, "nextOffset": cursor}


def parse_vector_variant_value(buffer: bytes, offset: int, code_page: Any):
    count = read_uint32_le(buffer, offset)
    cursor = offset + 4
    values: list[Any] = []
    index = 0
    while index < count and cursor < len(buffer):
        parsed = parse_typed_property_value(buffer, cursor, code_page)
        if parsed["value"] != "":
            values.append(parsed["value"])
        cursor = parsed["nextOffset"]
        index += 1
    return {"value": values, "nextOffset": cursor}


def parse_typed_property_value(buffer: bytes, offset: int,
                               code_page: Any = 1252) -> dict[str, Any]:
    prop_type = read_uint16_le(buffer, offset)
    value_offset = offset + 4
    if not prop_type or value_offset > len(buffer):
        return {"type": prop_type, "value": "", "nextOffset": value_offset}

    if prop_type == 0x02:
        return {"type": prop_type, "value": read_int16_le(buffer, value_offset),
                "nextOffset": align4(value_offset + 2)}
    if prop_type == 0x03:
        return {"type": prop_type, "value": read_int32_le(buffer, value_offset),
                "nextOffset": value_offset + 4}
    if prop_type == 0x05:
        return {"type": prop_type, "value": read_double_le(buffer, value_offset),
                "nextOffset": value_offset + 8}
    if prop_type == 0x0B:
        return {"type": prop_type, "value": read_uint32_le(buffer, value_offset) != 0,
                "nextOffset": value_offset + 4}
    if prop_type == 0x13:
        return {"type": prop_type, "value": read_uint32_le(buffer, value_offset),
                "nextOffset": value_offset + 4}
    if prop_type == 0x1E:
        parsed = parse_lpstr(buffer, value_offset, code_page, True)
        return {"type": prop_type, **parsed}
    if prop_type == 0x1F:
        parsed = parse_lpwstr(buffer, value_offset, True)
        return {"type": prop_type, **parsed}
    if prop_type == 0x40:
        return {"type": prop_type, "value": parse_file_time_value(buffer, value_offset),
                "nextOffset": value_offset + 8}
    if prop_type == 0x50:
        parsed = parse_lpwstr(buffer, value_offset, True)
        return {"type": prop_type, **parsed}
    if prop_type == 0x51:
        parsed = parse_lpwstr(buffer, value_offset, False)
        return {"type": prop_type, **parsed}
    if prop_type in (0x101E, 0x101F):
        parsed = parse_vector_string_value(buffer, value_offset, prop_type, code_page)
        return {"type": prop_type, **parsed}
    if prop_type == 0x100C:
        parsed = parse_vector_variant_value(buffer, value_offset, code_page)
        return {"type": prop_type, **parsed}
    if prop_type == 0x41:
        size = read_uint32_le(buffer, value_offset)
        return {"type": prop_type, "value": f"BLOB {size} bytes" if size else "",
                "nextOffset": align4(value_offset + 4 + size)}
    return {"type": prop_type, "value": "", "nextOffset": value_offset + 4}


def parse_property_dictionary(buffer: bytes, offset: int, code_page: Any) -> dict[int, str]:
    count = read_uint32_le(buffer, offset)
    dictionary: dict[int, str] = {}
    cursor = offset + 4
    index = 0
    while index < count and cursor + 8 <= len(buffer):
        property_id = read_uint32_le(buffer, cursor)
        length = read_uint32_le(buffer, cursor + 4)
        cursor += 8
        byte_length = length * 2 if code_page == 1200 else length
        if cursor + byte_length > len(buffer):
            # 参考的 clamp 用原始 length（1200 时是字符数）——已知怪癖，逐字保留
            byte_length = max(0, min(length, len(buffer) - cursor))
        raw = buffer[cursor:cursor + byte_length]
        value = (clean_ole_string(_node_utf16le_decode(raw))
                 if code_page == 1200
                 else clean_ole_string(decode_code_page_buffer(raw, code_page)))
        if value:
            dictionary[property_id] = value.replace("\x05", "!", 1) \
                if value.startswith("\x05") else value
        cursor = align4(cursor + byte_length)
        index += 1
    return dictionary


def format_version_number(value: Any) -> Any:
    """对齐 `formatVersionNumber`（:556-560）。

    `>>> 16` 与 `& 0xffff` 都先做 ToUint32（截断小数 + mod 2^32，负值回绕），
    用 `int(number) & 0xFFFFFFFF` 复刻。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    if number != number or number in (float("inf"), float("-inf")):
        return value  # Number.isFinite 为假 → 原样返回
    uint32 = int(number) & 0xFFFFFFFF
    return f"{uint32 >> 16}.{uint32 & 0xffff:04d}"


def parse_property_set(buffer: bytes, offset: int,
                       property_map: Mapping[int, Mapping[str, Any]] | None = None) -> dict[str, str]:
    property_map = property_map or {}
    size = read_uint32_le(buffer, offset)
    count = read_uint32_le(buffer, offset + 4)
    entries: list[dict[str, int]] = []
    index = 0
    while index < count and offset + 8 + index * 8 + 8 <= len(buffer):
        entries.append({
            "id": read_uint32_le(buffer, offset + 8 + index * 8),
            "offset": offset + read_uint32_le(buffer, offset + 12 + index * 8),
        })
        index += 1

    code_page: Any = 1252
    code_page_entry = next((e for e in entries if e["id"] == 0x01), None)
    if code_page_entry is not None:
        parsed_code_page = parse_typed_property_value(
            buffer, code_page_entry["offset"], code_page)["value"]
        if parsed_code_page:  # JS truthy：0/''/False 不进
            # `Number(parsedCodePage) || codePage`：NaN/0 → 保留旧值；
            # 保留 float 形态即可（后续比较/查表对 int/float 等价）
            numeric = _js_number(parsed_code_page)
            if numeric == numeric and numeric != 0:
                code_page = numeric

    dictionary_entry = next((e for e in entries if e["id"] == 0x00), None)
    dictionary = (parse_property_dictionary(buffer, dictionary_entry["offset"], code_page)
                  if dictionary_entry is not None else {})
    fields: dict[str, str] = {}
    end_offset = offset + size if size else len(buffer)

    for entry in entries:
        if entry["id"] == 0x00 or entry["offset"] >= end_offset or entry["offset"] >= len(buffer):
            continue
        property_info = property_map.get(entry["id"])
        parsed = parse_typed_property_value(buffer, entry["offset"], code_page)
        if (property_info and property_info.get("kind") == "duration_filetime"
                and parsed["type"] == 0x40):
            value: Any = parse_file_time_duration(buffer, entry["offset"] + 4)
        else:
            value = parsed["value"]
        if property_info and property_info.get("kind") == "version":
            value = format_version_number(value)
        dict_name = dictionary.get(entry["id"])
        name = (property_info["key"] if property_info and "key" in property_info
                else (f"custom:{dict_name}" if dict_name else f"ole_prop_{entry['id']}"))
        add_field(fields, name, value)
    return fields


def parse_property_set_stream(content: bytes,
                              property_map: Mapping[int, Mapping[str, Any]]) -> dict[str, str]:
    """`parsePropertySetStream`（svc.cjs :596-608）。

    :604 `index === 0 ? propertyMap : {}`——同一流里的**第二个属性集**没有
    propertyMap，全部走 dictionary / `ole_prop_{id}` 命名（oracle 实证）。
    """
    buffer = bytes(content or b"")
    fields: dict[str, str] = {}
    if len(buffer) < 48 or read_uint16_le(buffer, 0) != 0xFFFE:
        return fields
    set_count = read_uint32_le(buffer, 24)
    index = 0
    while index < set_count and 28 + index * 20 + 20 <= len(buffer):
        set_offset = read_uint32_le(buffer, 28 + index * 20 + 16)
        if not set_offset or set_offset >= len(buffer):
            index += 1
            continue
        parsed = parse_property_set(buffer, set_offset, property_map if index == 0 else {})
        for key, value in parsed.items():
            add_field(fields, key, value)
        index += 1
    return fields


# ---------------------------------------------------------------- 结构与信号（:657-691）

_MACRO_PATH_RE_TEXT = r"(^|[\\/])(?:vba|macros?|vbaProject\.bin|dir)([\\/]|$)"


def _is_ole_property_set_stream_name(value: Any) -> bool:
    text = str(value or "")
    text = text.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if text.startswith("\x05") or text.startswith("!"):
        text = text[1:]
    return re.search(r"(?:summaryinformation|documentsummaryinformation)$", text,
                     re.IGNORECASE) is not None


def add_ole_structure_fields(fields: dict[str, str],
                             entries: Sequence[CfbEntry]) -> None:
    """`addOleStructureFields`（svc.cjs :669-687）；path 形状契约见模块 docstring。"""
    shaped = [(entry, normalize_value(entry.path) or f"stream_{index}")
              for index, entry in enumerate(entries)]
    shaped = [(entry, path) for entry, path in shaped if path]
    stream_entries = [(e, p) for e, p in shaped
                      if e.entry_type == 2 or e.content is not None]
    storage_entries = [(e, p) for e, p in shaped if e.entry_type == 1]
    stream_paths = [re.sub(r"^/Root Entry/?", "", p, flags=re.IGNORECASE)
                    for _, p in stream_entries]
    # :677 `content?.length || size || 0`——空 Buffer 长度 0 落到 size，再兜 0
    stream_sizes = [f"{p}:{len(e.content) if e.content else (e.size or 0)}"
                    for e, p in stream_entries]
    macro_paths = [p for p in stream_paths
                   if re.search(_MACRO_PATH_RE_TEXT, p, re.IGNORECASE)]

    add_field(fields, "ole_storage_count", len(storage_entries))
    add_field(fields, "ole_stream_count", len(stream_entries))
    add_field(fields, "ole_stream_paths", summarize_values(stream_paths, 120))
    if stream_paths:
        add_field(fields, "ole_stream_paths_fingerprint",
                  hash_text("\n".join(unique_sorted_values(stream_paths))))
    if stream_sizes:
        add_field(fields, "ole_stream_sizes_fingerprint",
                  hash_text("\n".join(unique_sorted_values(stream_sizes))))
    add_field(fields, "ole_has_macro_storage", yes_no(len(macro_paths) > 0))
    add_field(fields, "ole_macro_paths", summarize_values(macro_paths, 40))


def add_ole_signal_fields(fields: dict[str, str],
                          entries: Sequence[CfbEntry]) -> None:
    """`addOleSignalFields`（svc.cjs :657-667）。"""
    for index, entry in enumerate(entries):
        full_path = entry.path or _entry_name(entry.path) or f"stream_{index}"
        signal_key = f"ole_signal:{safe_metadata_key(full_path)}"
        for snippet in collect_signal_snippets(full_path, 2):
            add_list_field(fields, signal_key, snippet)
        # :663 `entry?.content?.length`——长度判定，bytes 的 truthy 恰好等价
        if entry.content and not _is_ole_property_set_stream_name(full_path):
            for snippet in collect_binary_signal_snippets(entry.content):
                add_list_field(fields, signal_key, snippet)


# ---------------------------------------------------------------- 主提取（:743-760）

_SUMMARY_STREAM = "\x05SummaryInformation"
_DOC_SUMMARY_STREAM = "\x05DocumentSummaryInformation"


def extract_ole_fields(entries: Sequence[CfbEntry]) -> dict[str, str]:
    """`extractOleMetadata` 的纯函数核心（:743-760；文件读取在适配层）。"""
    fields: dict[str, str] = {}
    summary = find_cfb_stream(entries, _SUMMARY_STREAM)
    document_summary = find_cfb_stream(entries, _DOC_SUMMARY_STREAM)

    if summary is not None:
        for key, value in parse_property_set_stream(
                summary, SUMMARY_PROPERTY_MAP).items():
            add_field(fields, key, value)
    if document_summary is not None:
        for key, value in parse_property_set_stream(
                document_summary, DOC_SUMMARY_PROPERTY_MAP).items():
            add_field(fields, key, value)
    add_ole_structure_fields(fields, entries)
    add_ole_signal_fields(fields, entries)
    add_wps_signal_fields(fields)
    return fields
