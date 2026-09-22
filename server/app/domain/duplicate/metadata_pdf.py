r"""查重元数据的 PDF 提取纯函数（P3d T4，R-P3d-11）。

逐字对齐参考实现 `client/electron/services/duplicateCheckService.cjs`
:814-1058（PDF_INFO_KEY_MAP 到 extractPdfMetadata 的 PDF 全家）。

**JS → Python 的移植要点**：

- 参考的 `fields` Map → `dict`（插入序等价，同 T1 约定）；
- 含 `\\b`/`\\d` 的正则一律 `re.ASCII`（MEMORY 铁律；`\\s` 保持 Python 语义
  ——本段正则的输入是 latin1 解码文本，两语义在 ASCII 空白上一致）；
- `Buffer.toString('latin1')` → `bytes.decode('latin-1')`；
- `Buffer.from(text, 'latin1')`（decodePdfLiteralString 收尾）→ 逐字符
  `& 0xFF`（JS latin1 编码对 >255 的码点截断，如八进制转义 ``\\777``）；
- Node `toString('utf16le')` 奇数长度静默丢尾字节 → `_node_utf16le_decode`
  先截偶（同 metadata_ole 的双胞胎实现）；
- JS `String.prototype.trim` 的字符集与 Python `str.strip()` 不同
  （JS 含 ``\\ufeff``/``\\u00a0``/``\\u2028`` 等）→ `_js_trim` 显式字符集；
- `countMatches` 用 `count_matches`（finditer 非重叠计数）。

**分隔符语义（oracle 实测钉定，易错）**：

- `add_list_field` 的追加连接符是全角 ``；``，且拼接发生在 normalize 之外
  ——`pdf_raw:*`、`fingerprints`（数组 join 同理）保持全角；
- `summarize_values` 的输出再经 `add_field`/`add_list_field` 的
  `normalize_value`（NFKC）→ 全角 ``；`` 被折成半角 ``;``
  ——`pdf_trailer_id`、`pdf_embedded_file_names`、以及 `add_wps_signal_fields`
  拷贝出的 `wps:*` 值里的分隔符都是半角（NFKC 兼容分解 FF1B→003B）。

**已登记偏离（spec §15 回填）**：

1. `decode_pdf_hex_string` 对非法十六进制输入返回 ``''``——Node
   `Buffer.from(str,'hex')` 是「解析到首个非法字符为止」的截断语义，
   仅病态文件的 `pdf_raw:*`/附件名分支可触达，oracle 样本内无此形态；
2. `unique_sorted_values` 的排序键是 ICU `localeCompare` 的近似
   （见 metadata_base 模块注释第 2 条）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.domain.duplicate.metadata_base import (
    add_field,
    add_field_if_absent,
    add_list_field,
    count_matches,
    normalize_value,
    safe_metadata_key,
    summarize_values,
    unique_sorted_values,
    yes_no,
)
from app.domain.duplicate.metadata_signals import (
    add_wps_signal_fields,
    collect_binary_signal_snippets,
    collect_signal_snippets,
)

# ---------------------------------------------------------------- Info 键映射（:814-824）

PDF_INFO_KEY_MAP: dict[str, str] = {
    "Title": "title",
    "Author": "author",
    "Subject": "subject",
    "Keywords": "keywords",
    "Creator": "creator",
    "Producer": "producer",
    "CreationDate": "created",
    "ModDate": "modified",
    "PDFFormatVersion": "pdf_version",
}

# ---------------------------------------------------------------- metadata 形状契约（R-P3d-10）


@dataclass
class PdfParseResult:
    """pdf-parse@2 `getInfo()` 结果的形状契约（适配层的产出边界）。

    实测钉定（oracle shape）：`result.version` 在 2.4.5 **不存在**（undefined
    → JSON 丢键），版本回落 `info.PDFFormatVersion`；`info` 是普通对象、
    值可为 `null`/`false`；`metadata` 无 XMP 时为 null；`permission` 未加密
    为 null。`buffer` 一并随行（raw/结构统计吃原始字节）。
    """

    info: dict[str, Any]
    metadata: "PdfMetadata | None"
    buffer: bytes
    total: int | None
    fingerprints: list[str] | None
    permission: list[int] | None
    version: str | None = None  # pdf-parse@2 恒 None（字段不存在）


class PdfMetadata:
    """pdf-parse@2 `getInfo().metadata`（pdf.js Metadata 类）的形状契约。

    - 迭代产出 ``(key, value)`` 且**键已小写**（pdf.js MetadataParser 落库
      时统一小写：``<pdf:Keywords>`` → ``pdf:keywords``）；
    - `get(name)` **大小写敏感**精确查找——`get('pdf:Keywords')` 查不到
      小写化的 ``pdf:keywords``（oracle metadata_get 实测 null）；
    - `get_raw()` → 原始 XMP 包串（参考 `metadata.getRaw()`）。
    """

    def __init__(self, entries: dict[str, Any], raw: str = "") -> None:
        self._entries = dict(entries)
        self._raw = raw

    def get(self, name: str) -> Any:
        return self._entries.get(name)

    def get_raw(self) -> str:
        return self._raw

    def __iter__(self) -> Any:
        return iter(list(self._entries.items()))


def get_pdf_metadata_value(metadata: Any, *names: str) -> Any:
    """对齐 `getPdfMetadataValue`（:826-836）：get 优先、hasOwnProperty 兜底。

    命中判据是 `normalizeValue(value)` 非空，返回**原始值**（数组原样返回，
    归一化交给后续 addField）。
    """
    if metadata is None:
        return ""
    for name in names:
        getter = getattr(metadata, "get", None)
        if callable(getter):
            value = getter(name)
            if normalize_value(value):
                return value
        if isinstance(metadata, dict) and name in metadata \
                and normalize_value(metadata[name]):
            return metadata[name]
    return ""


def get_pdf_metadata_entries(metadata: Any) -> list[tuple[str, Any]]:
    """对齐 `getPdfMetadataEntries`（:838-842）。

    Metadata 类（可迭代）→ 全量条目**不过滤**；普通 dict → entries 后按
    归一化非空过滤（JS 侧普通对象无 Symbol.iterator 走的分支）。
    """
    if metadata is None:
        return []
    if isinstance(metadata, PdfMetadata):
        return list(metadata)
    if isinstance(metadata, dict):
        return [(key, value) for key, value in metadata.items()
                if normalize_value(value)]
    return []


# ---------------------------------------------------------------- XMP 规范键（:844-856）

_CANON_TITLE_RE = re.compile(r"(^|:)title$")
_CANON_CREATOR_RE = re.compile(r"(^|:)creator$")
_CANON_CREATORTOOL_RE = re.compile(r"creatortool$")
_CANON_PRODUCER_RE = re.compile(r"producer$")
_CANON_SUBJECT_RE = re.compile(r"(^|:)subject$")
_CANON_KEYWORDS_RE = re.compile(r"keywords$")
_CANON_DESCRIPTION_RE = re.compile(r"description$")
_CANON_CREATEDATE_RE = re.compile(r"createdate$")
_CANON_MODDATE_RE = re.compile(r"(modifydate|metadatadate)$")


def canonical_pdf_xmp_key(raw_key: Any) -> str:
    """对齐 `canonicalPdfXmpKey`（:844-856）：小写后按序匹配，未命中 ``''``。

    顺序敏感：`xmp:creatortool` 不能被 `(^|:)creator$` 抢走（后半才匹配）。
    """
    key = str(raw_key or "").lower()
    if _CANON_TITLE_RE.search(key):
        return "title"
    if _CANON_CREATOR_RE.search(key):
        return "author"
    if _CANON_CREATORTOOL_RE.search(key):
        return "creator"
    if _CANON_PRODUCER_RE.search(key):
        return "producer"
    if _CANON_SUBJECT_RE.search(key):
        return "subject"
    if _CANON_KEYWORDS_RE.search(key):
        return "keywords"
    if _CANON_DESCRIPTION_RE.search(key):
        return "description"
    if _CANON_CREATEDATE_RE.search(key):
        return "created"
    if _CANON_MODDATE_RE.search(key):
        return "modified"
    return ""


# ---------------------------------------------------------------- Info 字段（:858-865）


def add_pdf_info_fields(fields: dict[str, str], info: Any) -> None:
    """对齐 `addPdfInfoFields`：键序 = info 插入序；映射键先写规范字段再写
    `pdf_info:<safe>`（PDFFormatVersion → pdf_version 是映射键，位于首位）；
    Language/EncryptFilterName 为 None → 归一化空 → 跳过；Is* 布尔 →
    "false"/"true" 字符串落字段。
    """
    for raw_key, value in (info or {}).items():
        text = normalize_value(value)
        if not text:
            continue
        mapped = PDF_INFO_KEY_MAP.get(raw_key)
        if mapped:
            add_field(fields, mapped, text)
        add_field(fields, f"pdf_info:{safe_metadata_key(raw_key)}", text)


def add_pdf_xmp_fields(fields: dict[str, str], metadata: Any) -> None:
    """对齐 `addPdfXmpFields`（:867-878）：规范键 addFieldIfAbsent 回填 +
    `pdf_xmp:<safe>` 全写 + getRaw() 的信号片段（limit 5）。"""
    for raw_key, value in get_pdf_metadata_entries(metadata):
        text = normalize_value(value)
        if not text:
            continue
        canonical = canonical_pdf_xmp_key(raw_key)
        if canonical:
            add_field_if_absent(fields, canonical, text)
        add_field(fields, f"pdf_xmp:{safe_metadata_key(raw_key)}", text)

    raw = metadata.get_raw() if hasattr(metadata, "get_raw") else ""
    for snippet in collect_signal_snippets(raw, 5):
        add_list_field(fields, "pdf_xmp:raw_signals", snippet)


# ---------------------------------------------------------------- PDF 字符串解码（:880-931）

# JS String.prototype.trim 的字符集（MDN White_Space + 行终止符）
_JS_TRIM_CHARS = ("\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004"
                  "\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f"
                  "\u205f\u3000\ufeff")
# JS \s 的字符集（decodePdfHexString 的 replace(/\s+/g,'')）
_JS_WS_RE = re.compile("[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029"
                       "\u202f\u205f\u3000\ufeff]+")


def _js_trim(text: str) -> str:
    return text.strip(_JS_TRIM_CHARS)


def _node_utf16le_decode(raw: bytes) -> str:
    """Node `toString('utf16le')`：奇数长度静默丢尾字节（T3 双胞胎实现）。"""
    if len(raw) % 2:
        raw = raw[:-1]
    return raw.decode("utf-16-le", errors="replace")


def decode_utf16_be(buffer: bytes) -> str:
    """对齐 `decodeUtf16Be`（:880-887）：BE 码元逐个读，0 号码点跳过
    （`if (code)` 过滤 NUL，而非终止）；奇数尾字节丢弃。"""
    chars: list[str] = []
    for offset in range(0, len(buffer) - 1, 2):
        code = (buffer[offset] << 8) | buffer[offset + 1]
        if code:
            chars.append(chr(code))
    return "".join(chars)


def decode_pdf_string_buffer(buffer: bytes) -> str:
    """对齐 `decodePdfStringBuffer`（:889-894）：FEFF→UTF-16BE（跳过 BOM）、
    FFFE→UTF-16LE、否则 utf-8（replace）非空否则 latin1；两分支都 trim。"""
    if len(buffer) >= 2 and buffer[0] == 0xFE and buffer[1] == 0xFF:
        return decode_utf16_be(buffer[2:])
    if len(buffer) >= 2 and buffer[0] == 0xFF and buffer[1] == 0xFE:
        return _node_utf16le_decode(buffer[2:])
    utf8 = _js_trim(buffer.decode("utf-8", errors="replace"))
    return utf8 or _js_trim(buffer.decode("latin-1"))


def decode_pdf_literal_string(value: str) -> str:
    r"""对齐 `decodePdfLiteralString`（:896-921）。

    逐字符扫描：`\n/\r/\t/\b/\f`、八进制（1~3 位，首 digit 必须是 0-7、
    后续至多再吃 2 位）、其余转义取字面；尾部孤立 `\` 丢弃（JS for-continue
    仍前进）。收尾 `Buffer.from(text,'latin1')` → 逐字符 & 0xFF。
    """
    text: list[str] = []
    index = 0
    length = len(value)
    while index < length:
        char = value[index]
        if char != "\\":
            text.append(char)
            index += 1
            continue
        if index + 1 >= length:  # 尾部反斜杠：JS continue 等价（不产出、前进）
            index += 1
            continue
        nxt = value[index + 1]
        index += 2
        if nxt == "n":
            text.append("\n")
        elif nxt == "r":
            text.append("\r")
        elif nxt == "t":
            text.append("\t")
        elif nxt == "b":
            text.append("\b")
        elif nxt == "f":
            text.append("\f")
        elif nxt in "01234567":
            octal = nxt
            taken = 0
            while taken < 2 and index < length and value[index] in "01234567":
                octal += value[index]
                index += 1
                taken += 1
            text.append(chr(int(octal, 8) & 0xFF))
        else:
            text.append(nxt)
    raw = bytes(ord(char) & 0xFF for char in "".join(text))
    return decode_pdf_string_buffer(raw)


def decode_pdf_hex_string(value: str) -> str:
    """对齐 `decodePdfHexString`（:923-931）：剥空白后奇数长度/空串 → ``''``。

    偏离见模块注释第 1 条（非法 hex 字符：Node 截断 vs 这里整串失败）。
    """
    hex_text = _JS_WS_RE.sub("", str(value or ""))
    if not hex_text or len(hex_text) % 2 != 0:
        return ""
    try:
        return decode_pdf_string_buffer(bytes.fromhex(hex_text))
    except ValueError:
        return ""


# ---------------------------------------------------------------- raw 正则字段（:933-943）

# 字面 `(…)` 或十六进制 `<…>`（group3 = 十六进制内文）
_PDF_RAW_VALUE_RE = re.compile(
    r"/(Title|Author|Subject|Keywords|Creator|Producer|CreationDate|ModDate)"
    r"\s*(\((?:\\.|[^\\)]){0,1000}\)|<([0-9a-fA-F\s]{2,2000})>)")


def add_pdf_raw_fields(fields: dict[str, str], buffer: bytes) -> None:
    """对齐 `addPdfRawFields`（:933-943）：latin1 全文按 8 个 Info 键名抓
    字面/十六进制值（`pdf_raw:<safe>` 追加）+ collectBinarySignalSnippets。"""
    text = buffer.decode("latin-1")
    for match in _PDF_RAW_VALUE_RE.finditer(text):
        raw_key = match.group(1)
        if match.group(3):
            raw_value = decode_pdf_hex_string(match.group(3))
        else:
            raw_value = decode_pdf_literal_string(match.group(2)[1:-1])
        add_list_field(fields, f"pdf_raw:{safe_metadata_key(raw_key)}", raw_value)
    for snippet in collect_binary_signal_snippets(buffer):
        add_list_field(fields, "pdf_raw:signals", snippet)


# ---------------------------------------------------------------- 名字/记号/Trailer ID（:945-990）

_PDF_NAME_HEX_RE = re.compile(r"#([0-9a-fA-F]{2})")


def decode_pdf_name(value: Any) -> str:
    """对齐 `decodePdfName`（:945-947）：`#HH` → 对应码点字符。"""
    return _PDF_NAME_HEX_RE.sub(
        lambda match: chr(int(match.group(1), 16)), str(value or ""))


def decode_pdf_token_string(token: Any) -> str:
    """对齐 `decodePdfTokenString`（:949-956）：`(字面)`/`<十六进制>`/`/名字`/
    裸串四分派（trim 后前后缀判定）。"""
    value = _js_trim(str(token or ""))
    if not value:
        return ""
    if value.startswith("(") and value.endswith(")"):
        return decode_pdf_literal_string(value[1:-1])
    if value.startswith("<") and value.endswith(">"):
        return decode_pdf_hex_string(value[1:-1])
    if value.startswith("/"):
        return decode_pdf_name(value[1:])
    return value


def normalize_pdf_id_token(token: Any) -> str:
    """对齐 `normalizePdfIdToken`（:958-962）：尖括号 → 剥空白小写保留尖括号；
    其余（圆括号字面/裸串）走 decodePdfTokenString。"""
    value = _js_trim(str(token or ""))
    if value.startswith("<") and value.endswith(">"):
        inner = _JS_WS_RE.sub("", value[1:-1])
        return f"<{inner.lower()}>"
    return decode_pdf_token_string(value)


_TRAILER_ID_RE = re.compile(
    r"/ID\s*\[\s*(<[^>\r\n]{1,512}>|\((?:\\.|[^\\)]){0,512}\))"
    r"\s*(<[^>\r\n]{1,512}>|\((?:\\.|[^\\)]){0,512}\))")


def extract_pdf_trailer_ids(text: str) -> list[str]:
    """对齐 `extractPdfTrailerIds`（:964-974）：抓**全部** /ID 数组
    （增量保存多代 trailer），每代 `tok0 / tok1`，最后 uniqueSorted。"""
    ids: list[str] = []
    for match in _TRAILER_ID_RE.finditer(text):
        first = normalize_pdf_id_token(match.group(1))
        second = normalize_pdf_id_token(match.group(2))
        if first or second:
            ids.append(" / ".join(item for item in (first, second) if item))
    return unique_sorted_values(ids)


_FILESPEC_RE = re.compile(r"/Type\s*/Filespec\b", re.ASCII)
# JS 侧以字符串拼接构造；三选一：圆括号字面 / 尖括号十六进制 / 斜杠名字记号
_STRING_TOKEN = r"(\((?:\\.|[^\\)]){0,500}\)|<[0-9a-fA-F\s]{2,1000}>|/[^\s<>\[\]()/]{1,300})"
_UF_PATTERN = re.compile(r"/UF\s*" + _STRING_TOKEN)
_F_PATTERN = re.compile(r"/F\s*" + _STRING_TOKEN)


def extract_pdf_attachment_names(text: str) -> list[str]:
    """对齐 `extractPdfAttachmentNames`（:976-990）：每个 /Filespec 起
    2200 字符窗口内先找 /UF 再回落 /F（/Filespec 的 "/F" 前缀因名字记号
    分支要求字面 `/` 而不会误命中），最后 uniqueSorted。"""
    names: list[str] = []
    for match in _FILESPEC_RE.finditer(text):
        chunk = text[match.start(): min(len(text), match.start() + 2200)]
        name_match = _UF_PATTERN.search(chunk) or _F_PATTERN.search(chunk)
        name = decode_pdf_token_string(name_match.group(1) if name_match else "")
        if name:
            names.append(name)
    return unique_sorted_values(names)


# ---------------------------------------------------------------- 结构统计（:992-1026）

_PDF_HEADER_RE = re.compile(r"%PDF-(\d\.\d)", re.ASCII)
_PDF_OBJ_RE = re.compile(r"(?:^|[\r\n])\s*\d+\s+\d+\s+obj\b", re.ASCII)
_PDF_STARTXREF_RE = re.compile(r"(?:^|[\r\n])startxref\b", re.ASCII)
_PDF_CLASSIC_XREF_RE = re.compile(r"(?:^|[\r\n])xref(?:\s|[\r\n])", re.ASCII)
_PDF_XREF_STREAM_RE = re.compile(r"/Type\s*/XRef\b", re.ASCII)
# 公用：适配层的 info.IsLinearized 用同一启发式（pdf.js 线性化字典解析的近似）
PDF_LINEARIZED_RE = re.compile(r"/Linearized\s+\d", re.ASCII)


def pdf_header_version(head_text: str) -> str:
    """`addPdfStructureFields` 的头版本切片（供适配层复用）：前 1024 字节内
    `%PDF-\\d.\\d`。"""
    match = _PDF_HEADER_RE.search(head_text[:1024])
    return match.group(1) if match else ""


def add_pdf_structure_fields(fields: dict[str, str], buffer: bytes) -> None:
    """对齐 `addPdfStructureFields`（:992-1010）：头版本/对象计数/增量保存/
    线性化/xref 类型/trailer ID——对象与 startxref 计数锚定行首（``^`` 无
    m 旗标 = 仅串首或换行后）。"""
    text = buffer.decode("latin-1")
    object_count = count_matches(text, _PDF_OBJ_RE)
    startxref_count = count_matches(text, _PDF_STARTXREF_RE)
    has_classic_xref = _PDF_CLASSIC_XREF_RE.search(text) is not None
    xref_stream_count = count_matches(text, _PDF_XREF_STREAM_RE)
    xref_types: list[str] = []
    if has_classic_xref:
        xref_types.append("传统 xref 表")
    if xref_stream_count:
        xref_types.append("XRef 对象流")

    add_field(fields, "pdf_header_version", pdf_header_version(text))
    add_field(fields, "pdf_object_count", object_count)
    add_field(fields, "pdf_startxref_count", startxref_count)
    add_field(fields, "pdf_incremental_update_count", max(0, startxref_count - 1))
    add_field(fields, "pdf_linearized",
              yes_no(PDF_LINEARIZED_RE.search(text[:4096]) is not None))
    add_field(fields, "pdf_xref_type", "；".join(xref_types) if xref_types else "未识别")
    add_field(fields, "pdf_trailer_id", summarize_values(extract_pdf_trailer_ids(text), 8))


_PDF_BYTERANGE_RE = re.compile(r"/ByteRange\s*\[", re.ASCII)
_PDF_FT_SIG_RE = re.compile(r"/FT\s*/Sig\b", re.ASCII)
_PDF_TYPE_SIG_RE = re.compile(r"/Type\s*/Sig\b", re.ASCII)
_PDF_EMBEDDED_FILE_RE = re.compile(r"/Type\s*/EmbeddedFile\b", re.ASCII)
_PDF_ACROFORM_RE = re.compile(r"/AcroForm\b", re.ASCII)
_PDF_XFA_RE = re.compile(r"/XFA\b", re.ASCII)


def add_pdf_form_signature_attachment_fields(fields: dict[str, str], buffer: bytes) -> None:
    """对齐 `addPdfFormSignatureAttachmentFields`（:1012-1026）：raw 正则口径
    的表单/签名/附件统计（与 info 的 Is* 布尔来源不同，两者可不一致）。"""
    text = buffer.decode("latin-1")
    byte_range_signature_count = count_matches(text, _PDF_BYTERANGE_RE)
    signature_field_count = count_matches(text, _PDF_FT_SIG_RE)
    signature_object_count = count_matches(text, _PDF_TYPE_SIG_RE)
    embedded_file_count = count_matches(text, _PDF_EMBEDDED_FILE_RE)
    attachment_names = extract_pdf_attachment_names(text)

    add_field(fields, "pdf_has_acroform", yes_no(_PDF_ACROFORM_RE.search(text) is not None))
    add_field(fields, "pdf_has_xfa", yes_no(_PDF_XFA_RE.search(text) is not None))
    add_field(fields, "pdf_signature_count",
              max(signature_field_count, signature_object_count, byte_range_signature_count))
    add_field(fields, "pdf_byterange_signature_count", byte_range_signature_count)
    add_field(fields, "pdf_embedded_file_count",
              max(embedded_file_count, len(attachment_names)))
    add_field(fields, "pdf_embedded_file_names", summarize_values(attachment_names, 40))


# ---------------------------------------------------------------- 组装（:1028-1058）


def extract_pdf_fields(info: Any, metadata: Any, buffer: bytes, *,
                       total: int | None = None, version: str | None = None,
                       fingerprints: list[str] | None = None,
                       permission: list[int] | None = None) -> dict[str, str]:
    """对齐 `extractPdfMetadata`（:1028-1058）的**纯核心**（文件 IO 与
    pdf-parse 依赖由适配层承担，R-P3d-10 形状契约入参）。

    组装序即语义：info → xmp → 规范键回填（addFieldIfAbsent）→ pages /
    pdf_version / fingerprints / pdf_permissions → raw → 结构 → 表单签名
    附件 → wps 信号（`result.version` 在 pdf-parse@2 不存在，适配层恒传
    None → 落 info.PDFFormatVersion）。
    """
    fields: dict[str, str] = {}
    add_pdf_info_fields(fields, info)
    add_pdf_xmp_fields(fields, metadata)
    add_field_if_absent(fields, "title", get_pdf_metadata_value(metadata, "dc:title", "Title"))
    add_field_if_absent(fields, "author", get_pdf_metadata_value(metadata, "dc:creator", "Author"))
    add_field_if_absent(fields, "subject", get_pdf_metadata_value(metadata, "dc:subject", "Subject"))
    add_field_if_absent(fields, "keywords", get_pdf_metadata_value(metadata, "pdf:Keywords", "Keywords"))
    add_field_if_absent(fields, "creator", get_pdf_metadata_value(metadata, "xmp:CreatorTool", "Creator"))
    add_field_if_absent(fields, "producer", get_pdf_metadata_value(metadata, "pdf:Producer", "Producer"))
    add_field_if_absent(fields, "created", get_pdf_metadata_value(metadata, "xmp:CreateDate", "CreationDate"))
    add_field_if_absent(fields, "modified",
                        get_pdf_metadata_value(metadata, "xmp:ModifyDate", "xmp:MetadataDate", "ModDate"))
    add_field(fields, "pages", total if total else None)
    info_dict = info if isinstance(info, dict) else {}
    add_field(fields, "pdf_version", version or info_dict.get("PDFFormatVersion"))
    add_field(fields, "fingerprints", fingerprints)
    add_field(fields, "pdf_permissions", permission)
    add_pdf_raw_fields(fields, buffer)
    add_pdf_structure_fields(fields, buffer)
    add_pdf_form_signature_attachment_fields(fields, buffer)
    add_wps_signal_fields(fields)
    return fields
