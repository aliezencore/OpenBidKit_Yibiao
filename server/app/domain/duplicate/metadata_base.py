"""查重元数据的基座纯函数（P3d T1，R-P3d-11）。

逐字对齐参考实现 `client/electron/services/duplicateCheckService.cjs` 的
:133-331 与 :1085-1093（normalizeValue 一族、键表、items 映射）。

**JS → Python 的移植约定**：

- 参考的 `fields` 是 `Map`（插入序 + `set` 覆盖语义），这里用 **`dict`**
  （Python 3.7+ 的插入序语义等价）；
- `String(value)` 的强转语义收在 `_js_string`（bool → "true"/"false"、
  整数值浮点 → 整数串、dict → 紧凑 JSON）；
- 所有含 `\\d` 的正则一律 `re.ASCII`（JS `\\d` 是纯 ASCII，
  全角数字不能被当成数字——MEMORY 铁律）。

**已登记偏离（spec §15 回填）**：

1. 1~3 位纯数字（如 "600"）V8 legacy 会当日期解析（"600" →
   `0599-12-31T15:54:17.000Z`），本实现视为非日期（js_date.py 模块注释）；
2. `unique_sorted_values` 的排序键用「casefold + 小写优先 + 码点」近似 ICU
   `localeCompare`——拉丁文与参考一致，**汉字按码点序**（ICU 按拼音，
   「甲」先于「乙」；Python 无 ICU，码点序恰好相反）。等值分组行为不受影响
   （排序只影响汇总展示与路径指纹；参考语料的 OLE 流名/RSID 全 ASCII）。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

from app.domain.duplicate.js_date import js_date_iso, to_iso_z

# ---------------------------------------------------------------- 键表（svc.cjs :15-107）

METADATA_LABELS: dict[str, str] = {
    "file_name": "文件名",
    "extension": "扩展名",
    "size": "文件大小",
    "file_sha256": "原始文件 SHA256",
    "created_at": "文件创建时间",
    "modified_at": "文件修改时间",
    "accessed_at": "文件访问时间",
    "title": "标题",
    "subject": "主题",
    "author": "作者",
    "last_modified_by": "最后修改人",
    "revision": "修订号",
    "created": "创建时间",
    "modified": "修改时间",
    "last_printed": "最后打印时间",
    "keywords": "关键词",
    "category": "类别",
    "description": "描述",
    "content_status": "内容状态",
    "content_type": "内容类型",
    "identifier": "标识符",
    "language": "语言",
    "application": "应用程序",
    "app_version": "应用程序版本",
    "company": "公司",
    "manager": "管理者",
    "template": "模板",
    "presentation_format": "演示格式",
    "pages": "页数",
    "words": "字数",
    "characters": "字符数",
    "characters_with_spaces": "含空格字符数",
    "bytes": "字节数",
    "lines": "行数",
    "paragraphs": "段落数",
    "slides": "幻灯片数",
    "notes": "备注数",
    "hidden_slides": "隐藏幻灯片数",
    "multimedia_clips": "多媒体剪辑数",
    "total_time": "编辑时长",
    "code_page": "代码页",
    "document_version": "文档版本",
    "doc_security": "文档安全状态",
    "shared_doc": "共享文档",
    "links_dirty": "链接已变更",
    "hlinks_changed": "超链接已变更",
    "creator": "创建工具",
    "producer": "生成工具",
    "pdf_version": "PDF 版本",
    "pdf_permissions": "PDF 权限",
    "fingerprints": "PDF 指纹",
    "word_rsid_root": "Word 编辑会话根 ID",
    "word_rsid_count": "Word 编辑会话 ID 数量",
    "word_rsid_values": "Word 编辑会话 ID 列表",
    "word_rsid_fingerprint": "Word 编辑会话指纹",
    "ole_storage_count": "OLE 存储数量",
    "ole_stream_count": "OLE Stream 数量",
    "ole_stream_paths": "OLE Stream 路径摘要",
    "ole_stream_paths_fingerprint": "OLE Stream 路径指纹",
    "ole_stream_sizes_fingerprint": "OLE Stream 大小指纹",
    "ole_has_macro_storage": "OLE 宏存储",
    "ole_macro_paths": "OLE 宏存储路径",
    "pdf_header_version": "PDF 头版本",
    "pdf_object_count": "PDF 对象数量",
    "pdf_startxref_count": "PDF startxref 数量",
    "pdf_incremental_update_count": "PDF 增量保存次数",
    "pdf_linearized": "PDF 线性化",
    "pdf_xref_type": "PDF XRef 类型",
    "pdf_trailer_id": "PDF Trailer ID",
    "pdf_has_acroform": "PDF 表单",
    "pdf_has_xfa": "PDF XFA 表单",
    "pdf_signature_count": "PDF 签名字段数量",
    "pdf_byterange_signature_count": "PDF ByteRange 签名数量",
    "pdf_embedded_file_count": "PDF 附件数量",
    "pdf_embedded_file_names": "PDF 附件文件名",
}

COMPARABLE_KEYS: frozenset[str] = frozenset({
    "title", "subject", "author", "last_modified_by", "revision", "created",
    "modified", "last_printed", "keywords",
    "category", "description", "content_status", "content_type", "identifier",
    "language", "application", "app_version",
    "company", "manager", "template", "presentation_format", "pages", "words",
    "characters", "characters_with_spaces",
    "bytes", "lines", "paragraphs", "slides", "notes", "hidden_slides",
    "multimedia_clips", "total_time", "creator",
    "producer", "pdf_version", "pdf_permissions", "fingerprints",
    "document_version", "doc_security", "shared_doc",
    "links_dirty", "hlinks_changed",
    "file_sha256", "word_rsid_root", "word_rsid_count", "word_rsid_values",
    "word_rsid_fingerprint",
    "ole_storage_count", "ole_stream_count", "ole_stream_paths",
    "ole_stream_paths_fingerprint", "ole_stream_sizes_fingerprint",
    "ole_has_macro_storage", "ole_macro_paths", "pdf_header_version",
    "pdf_object_count", "pdf_startxref_count",
    "pdf_incremental_update_count", "pdf_linearized", "pdf_xref_type",
    "pdf_trailer_id", "pdf_has_acroform",
    "pdf_has_xfa", "pdf_signature_count", "pdf_byterange_signature_count",
    "pdf_embedded_file_count", "pdf_embedded_file_names",
})

DATE_COMPARABLE_KEYS: frozenset[str] = frozenset({
    "created_at", "modified_at", "accessed_at", "created", "modified",
    "last_printed",
})

# ---------------------------------------------------------------- JS 强转与归一化


def _js_string(value: Any) -> str:
    """`String(value)` 的对应物（只处理参考值域里出现的类型）。

    - `bool` → "true"/"false"（JS `String(true)`；Python `str(True)` 是 "True"）；
    - 整数值浮点 → 整数串（JS `String(1.0)` 是 "1"）；
    - 其余标量 → `str()`。
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    return str(value)


def _json_stringify(value: Any) -> str:
    """`JSON.stringify` 对应物：紧凑分隔、不转义非 ASCII、键按插入序。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


_CTRL_RE = re.compile(
    "[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\ufeff]")
_WS_RE = re.compile(r"\s+")


def normalize_value(value: Any) -> str:
    """对齐 `normalizeValue`（svc.cjs :133-143）。

    顺序敏感：**先 NFKC、再剥控制符、再折空白**——`\t`/`\n` 属控制符区间，
    在「折空白」之前就被剥掉（oracle：`"世界\\t\\t第二行"` → `"世界第二行"`，
    不是 `"世界 第二行"`）。

    非 `str` 标量先经 `_js_string`；`None` → ""；datetime → toISOString
    （对应参考的 `value instanceof Date` 分支；值域里不会出现 naive 时间，
    naive 一律按 UTC）；list/tuple → 递归归一化后 `；` 连接（滤空）；
    dict → `JSON.stringify`。
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return to_iso_z(value.astimezone(timezone.utc))
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        parts = [normalize_value(item) for item in value]
        return "；".join(part for part in parts if part)
    if isinstance(value, dict):
        return _json_stringify(value)

    if isinstance(value, float) and value.is_integer() and abs(value) < 1e21:
        text = str(int(value))
    else:
        text = value if isinstance(value, str) else str(value)
    text = unicodedata.normalize("NFKC", text)
    text = _CTRL_RE.sub("", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


_NON_DATE_TEXTS = frozenset({
    "没有提及", "原文未提及", "-", "无", "null", "undefined"})
_STRIP_D_PREFIX_RE = re.compile(r"^d:", re.IGNORECASE)
_TZ_QUOTE_RE = re.compile(r"([+-]\d{2})'(\d{2})'$")
_COMPACT_STRIP_RE = re.compile(
    r"[\s\u3000\u0000-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\ufeff]+")


def _parse_loose_date(text: str) -> str | None:
    """`new Date(text.replace(/^d:/i,'').replace(/([+-]\\d{2})'(\\d{2})'$/, '$1:$2')).toISOString()`。

    T5 起改产 **ISO 串**（js_date_iso）：年份超 datetime 范围的 5 位数字形态
    （words='12345'）需算术直出，datetime 契约表达不了。
    """
    candidate = _STRIP_D_PREFIX_RE.sub("", text)
    candidate = _TZ_QUOTE_RE.sub(r"\1:\2", candidate)
    return js_date_iso(candidate)


def normalize_comparable(value: Any) -> str:
    """对齐 `normalizeComparable`（svc.cjs :145-151）。"""
    text = normalize_value(value).lower()
    if not text or text in _NON_DATE_TEXTS:
        return ""
    moment = _parse_loose_date(text)
    if moment is not None:
        return moment
    return _COMPACT_STRIP_RE.sub("", text)


_CJK_DATE_RE = re.compile(r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}", re.ASCII)
_CJK_DATE_SEP_RE = re.compile(r"[年月/.]")


def normalize_date_day(value: Any) -> str:
    """对齐 `normalizeDateDay`（svc.cjs :153-160）。"""
    text = normalize_value(value)
    if not text:
        return ""
    moment = _parse_loose_date(text)
    if moment is not None:
        return moment[:10]
    match = _CJK_DATE_RE.search(text)
    if match:
        return _CJK_DATE_SEP_RE.sub("-", match[0]).replace("日", "")
    return ""


# ---------------------------------------------------------------- 字段收集三兄弟


def add_field(fields: dict[str, str], key: str, value: Any) -> None:
    """对齐 `addField`（:162-166）：归一化后非空才写；同键**覆盖**（Map.set）。"""
    text = normalize_value(value)
    if not text:
        return
    fields[key] = text


def add_field_if_absent(fields: dict[str, str], key: str, value: Any) -> None:
    """对齐 `addFieldIfAbsent`（:168-171）：已有键跳过。"""
    if key in fields:
        return
    add_field(fields, key, value)


def add_list_field(fields: dict[str, str], key: str, value: Any) -> None:
    """对齐 `addListField`（:173-183）：`；` 连接去重（按归一化后的整段精确匹配）。"""
    text = normalize_value(value)
    if not text:
        return
    current = fields.get(key)
    if not current:
        fields[key] = text
        return
    parts = [part.strip() for part in current.split("；")]
    parts = [part for part in parts if part]
    if text not in parts:
        fields[key] = f"{current}；{text}"


def merge_metadata_fields(target: dict[str, str], source: dict[str, str],
                          *, fill_only_if_absent: bool = False,
                          prefix: str | None = None) -> None:
    """对齐 `mergeMetadataFields`（svc.cjs :772-778）。"""
    for key, value in source.items():
        if fill_only_if_absent:
            add_field_if_absent(target, key, value)
        else:
            add_field(target, key, value)
        if prefix:
            add_field(target, f"{prefix}:{key}", value)


# ---------------------------------------------------------------- 键名与标签


def safe_metadata_key(value: Any) -> str:
    """对齐 `safeMetadataKey`（svc.cjs :185-191）。"""
    key = normalize_value(value).lower()
    key = re.sub(r"[^a-z0-9\u4e00-\u9fa5]+", "_", key)
    key = re.sub(r"^_+|_+$", "", key)
    return key[:80] or "field"


def format_metadata_key(key: Any) -> str:
    """对齐 `formatMetadataKey`（svc.cjs :193-195）。"""
    raw = str(key or "")
    formatted = re.sub(r"[_:]+", " ", raw).strip()
    return formatted or raw


def get_metadata_label(key: str) -> str:
    """对齐 `getMetadataLabel`（svc.cjs :197-209）：前缀分支的**判定顺序**即语义。"""
    if key in METADATA_LABELS:
        return METADATA_LABELS[key]
    if key.startswith("converted_docx:"):
        return f"转换 DOCX：{get_metadata_label(key[len('converted_docx:'):])}"
    if key.startswith("custom:") and key.endswith(":base64_decoded"):
        return (f"自定义：{key[len('custom:'):-len(':base64_decoded')]}"
                f"（Base64 解码）")
    if key.startswith("custom:"):
        return f"自定义：{key[len('custom:'):]}"
    if key.endswith(":base64_decoded"):
        tail = get_metadata_label(key[:-len(":base64_decoded")])
        return f"{tail}（Base64 解码）"
    if key.startswith("pdf_info:"):
        return f"PDF Info：{format_metadata_key(key[len('pdf_info:'):])}"
    if key.startswith("pdf_xmp:"):
        return f"PDF XMP：{format_metadata_key(key[len('pdf_xmp:'):])}"
    if key.startswith("pdf_raw:"):
        return f"PDF 原始记录：{format_metadata_key(key[len('pdf_raw:'):])}"
    if key.startswith("ole_signal:"):
        return f"OLE 疑似痕迹：{format_metadata_key(key[len('ole_signal:'):])}"
    if key.startswith("wps:"):
        return f"疑似 WPS 用户/账号：{format_metadata_key(key[len('wps:'):])}"
    return format_metadata_key(key)


_LAST_MODIFIED_BY_RE = re.compile(r"last[_-]?modified[_-]?by|lastmodifiedby")
_DATE_KEY_RE = re.compile(
    r"(^|[:_])(created|modified|last_printed|creationdate|moddate|createdate"
    r"|modifydate|metadatadate|lastsaved|lastprinted)([:_]|$)")


def is_date_comparable_key(key: Any) -> bool:
    """对齐 `isDateComparableKey`（svc.cjs :211-216）。"""
    if key in DATE_COMPARABLE_KEYS:
        return True
    normalized = str(key or "").lower()
    if _LAST_MODIFIED_BY_RE.search(normalized):
        return False
    return _DATE_KEY_RE.search(normalized) is not None


_COMPARABLE_PREFIXES = ("custom:", "converted_docx:", "pdf_info:", "pdf_xmp:",
                        "pdf_raw:", "ole_signal:", "wps:")


def is_comparable_key(key: str) -> bool:
    """对齐 `isComparableKey`（svc.cjs :218-228）：前缀判定用**原始 key**（大小写敏感）。"""
    return (key in COMPARABLE_KEYS
            or is_date_comparable_key(key)
            or key.startswith(_COMPARABLE_PREFIXES))


# ---------------------------------------------------------------- 通用小工具


def hash_text(value: Any) -> str:
    """对齐 `hashText`（svc.cjs :266-268）：sha256(utf8(String(value || '')))。"""
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def yes_no(value: Any) -> str:
    """对齐 `yesNo`（svc.cjs :270-272）。"""
    return "是" if value else "否"


def count_matches(value: Any, pattern: re.Pattern[str]) -> int:
    """对齐 `countMatches`（svc.cjs :274-280）：全局非重叠计数（finditer 天然全局）。"""
    return sum(1 for _ in pattern.finditer(str(value or "")))


def _collation_key(text: str) -> tuple[str, int, str]:
    """ICU `localeCompare` 的近似排序键（偏离见模块 docstring 第 2 条）。"""
    return (text.casefold(), 0 if text == text.lower() else 1, text)


def unique_sorted_values(values: Any) -> list[str]:
    """对齐 `uniqueSortedValues`（svc.cjs :282-287）：归一化 → 去重 → 排序。"""
    if not isinstance(values, (list, tuple)):
        return []
    items = {normalize_value(item) for item in values}
    items.discard("")
    return sorted(items, key=_collation_key)


def summarize_values(values: Any, limit: int = 80) -> str:
    """对齐 `summarizeValues`（svc.cjs :289-294）。"""
    sorted_items = unique_sorted_values(values)
    if not sorted_items:
        return ""
    visible = "；".join(sorted_items[:limit])
    if len(sorted_items) > limit:
        return f"{visible}；...共{len(sorted_items)}项"
    return visible


_XML_TEXT_CACHE: dict[str, re.Pattern[str]] = {}


def xml_text(xml: Any, tag_name: str) -> str:
    """对齐 `xmlText`（svc.cjs :296-300）：首个匹配的内文（decodeXml 之后）。"""
    pattern = _XML_TEXT_CACHE.get(tag_name)
    if pattern is None:
        pattern = re.compile(
            r"<[^:>]*:?" + tag_name + r"[^>]*>([\s\S]*?)</[^:>]*:?" + tag_name + r">",
            re.IGNORECASE)
        _XML_TEXT_CACHE[tag_name] = pattern
    match = pattern.search(str(xml or ""))
    return decode_xml(match[1]) if match else ""


_CDATA_RE = re.compile(r"<!\[CDATA\[([\s\S]*?)\]\]>")


def decode_xml(value: Any) -> str:
    """对齐 `decodeXml`（svc.cjs :302-311）：**&amp; 必须最后替换**（oracle `amp_order`）。"""
    text = str(value or "")
    text = _CDATA_RE.sub(r"\1", text)
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&apos;", "'")
    text = text.replace("&amp;", "&")
    return text.strip()


def format_docx_total_time(value: Any) -> str:
    """对齐 `formatDocxTotalTime`（svc.cjs :318-322）。"""
    text = normalize_value(value)
    if not text:
        return ""
    if re.fullmatch(r"\d+", text, re.ASCII):
        return f"{text} 分钟"
    return text


# ---------------------------------------------------------------- base64 解码


_B64_RE = re.compile(r"[A-Za-z0-9+/]+={0,2}")
_CONTROL_IN_DECODED_RE = re.compile("[\u0000-\u0008\u000b\u000c\u000e-\u001f]")
_SKIP_KEY_RE = re.compile(r"(^|[:_])(sha256|sha1|md5|hash|fingerprint)([:_]|$)")
_HEX_DIGEST_RE = re.compile(
    r"[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64}|[0-9a-f]{128}", re.IGNORECASE)


def try_decode_base64_text(value: Any) -> str:
    """对齐 `tryDecodeBase64Text`（svc.cjs :230-244）。"""
    text = normalize_value(value)
    if (not text or len(text) < 12 or len(text) % 4 != 0
            or _B64_RE.fullmatch(text) is None):
        return ""
    try:
        # Node `Buffer.from(text, 'base64')` 对**无 padding 的合法串**也能解
        # （16 字符无 `=` 的串 length%4==0，JS 直接收下）；Python 必须先补齐。
        body = text.rstrip("=")
        raw = base64.b64decode(body + "=" * (-len(body) % 4), validate=True)
        decoded = raw.decode("utf-8", errors="replace")
    except (binascii.Error, ValueError):
        return ""
    decoded = decoded.lstrip("\ufeff").strip()
    if (not decoded or decoded == text or "\ufffd" in decoded
            or _CONTROL_IN_DECODED_RE.search(decoded)):
        return ""
    try:
        return _json_stringify(json.loads(decoded))
    except (json.JSONDecodeError, ValueError):
        return decoded


def should_skip_base64_decode(key: Any, value: Any) -> bool:
    """对齐 `shouldSkipBase64Decode`（svc.cjs :246-250）。"""
    normalized_key = str(key or "").lower()
    if _SKIP_KEY_RE.search(normalized_key):
        return True
    return _HEX_DIGEST_RE.fullmatch(normalize_value(value)) is not None


def add_decoded_base64_fields(fields: dict[str, str]) -> None:
    """对齐 `addDecodedBase64Fields`（svc.cjs :252-259）：快照遍历、追加在尾部。"""
    for key, value in list(fields.items()):
        if key.endswith(":base64_decoded"):
            continue
        if should_skip_base64_decode(key, value):
            continue
        decoded = try_decode_base64_text(value)
        if decoded:
            add_field(fields, f"{key}:base64_decoded", decoded)


# ---------------------------------------------------------------- items 映射


def metadata_items(fields: dict[str, str]) -> list[dict[str, Any]]:
    """对齐 `extractMetadata` 尾部的 items 映射（svc.cjs :1085-1093）。

    键序 = `fields` 插入序；`value` 是**已归一化**的文本（fields 存的就是它）。
    """
    return [{
        "key": key,
        "label": get_metadata_label(key),
        "value": value,
        "normalized": normalize_comparable(value),
        "date_day": normalize_date_day(value),
        "comparable": is_comparable_key(key),
        "date_comparable": is_date_comparable_key(key),
    } for key, value in fields.items()]
