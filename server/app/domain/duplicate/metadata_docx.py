"""查重元数据的 DOCX 提取纯函数（P3d T2，R-P3d-11）。

逐字对齐参考实现 `client/electron/services/duplicateCheckService.cjs`
:703-741（extractDocxMetadata）与 :324-347（addDocxRsidFields）。

**分层**（R-P3d-11）：本模块只吃 XML 文本，zip I/O 在
`app/infra/parsing/metadata_extract.py`。`fields` 用 dict（插入序 = items 顺序，
与参考 Map 一致）。

**键序契约**（extractMetadata 的 items 顺序由此决定）：core 10 键 → app 11 键 →
custom:*（custom.xml property 顺序）→ word_rsid_root/count/values[/fingerprint]
→ wps:*（addWpsSignalFields 追加在尾部）。

RSID 正则注意：参考**无** `i` 旗标——属性名必须小写 `rsid` 前缀（`rsidR`/
`rsidRoot`…），捕获后再 toLowerCase 判定是否 rsidroot；值 1~16 位十六进制
（`[0-9A-Fa-f]{1,16}`），转大写后去重排序。RSID 全 ASCII → 汇总排序不受
「汉字排序偏离」影响。
"""

from __future__ import annotations

import re
from typing import Iterable

from app.domain.duplicate.metadata_base import (
    add_field,
    decode_xml,
    format_docx_total_time,
    hash_text,
    summarize_values,
    unique_sorted_values,
    xml_text,
)
from app.domain.duplicate.metadata_signals import add_wps_signal_fields

# ---------------------------------------------------------------- RSID（:324-347）

# 参考：/\b(?:[A-Za-z0-9_]+:)?(rsid[A-Za-z0-9]*)=["']([0-9A-Fa-f]{1,16})["']/g
# （无 i 旗标；\b → re.ASCII）
_RSID_RE = re.compile(
    r"\b(?:[A-Za-z0-9_]+:)?(rsid[A-Za-z0-9]*)=[\"']([0-9A-Fa-f]{1,16})[\"']",
    re.ASCII,
)


def add_docx_rsid_fields(fields: dict[str, str],
                         word_xml_texts: Iterable[str]) -> None:
    """对齐 `addDocxRsidFields`（svc.cjs :324-347）。"""
    values: set[str] = set()
    root = ""
    for xml in word_xml_texts:
        for match in _RSID_RE.finditer(xml):
            attr = str(match[1] or "").lower()
            value = str(match[2] or "").upper()
            if not value:
                continue
            values.add(value)
            if attr == "rsidroot" and not root:
                root = value
    sorted_values = unique_sorted_values(list(values))
    add_field(fields, "word_rsid_root", root)
    add_field(fields, "word_rsid_count", len(sorted_values))
    add_field(fields, "word_rsid_values", summarize_values(sorted_values))
    if sorted_values:
        add_field(fields, "word_rsid_fingerprint",
                  hash_text("\n".join(sorted_values)))


# ---------------------------------------------------------------- custom 属性（:732-736）

# 参考：/<property\b[^>]*\bname="([^"]+)"[^>]*>([\s\S]*?)<\/property>/gi
_CUSTOM_PROP_RE = re.compile(
    r"<property\b[^>]*\bname=\"([^\"]+)\"[^>]*>([\s\S]*?)</property>",
    re.IGNORECASE | re.ASCII,
)
# 参考：match[2].match(/<[^>]+>([\s\S]*?)<\/[^>]+>/)（首个内层元素的文本）
_CUSTOM_VALUE_RE = re.compile(r"<[^>]+>([\s\S]*?)</[^>]+>")


def add_docx_custom_fields(fields: dict[str, str], custom_xml: str) -> None:
    """对齐 extractDocxMetadata 的 custom.xml 循环（svc.cjs :732-736）。"""
    for match in _CUSTOM_PROP_RE.finditer(custom_xml):
        key = f"custom:{decode_xml(match[1])}"
        value_match = _CUSTOM_VALUE_RE.search(match[2])
        add_field(fields, key,
                  decode_xml(value_match[1]) if value_match
                  else decode_xml(match[2]))


# ---------------------------------------------------------------- 主提取（:703-741）


def extract_docx_fields(core_xml: str, app_xml: str, custom_xml: str,
                        word_xml_texts: list[str]) -> dict[str, str]:
    """对齐 `extractDocxMetadata`（svc.cjs :703-741）；键序即参考 Map 插入序。"""
    fields: dict[str, str] = {}
    add_field(fields, "title", xml_text(core_xml, "title"))
    add_field(fields, "subject", xml_text(core_xml, "subject"))
    add_field(fields, "author", xml_text(core_xml, "creator"))
    add_field(fields, "last_modified_by", xml_text(core_xml, "lastModifiedBy"))
    add_field(fields, "revision", xml_text(core_xml, "revision"))
    add_field(fields, "created", xml_text(core_xml, "created"))
    add_field(fields, "modified", xml_text(core_xml, "modified"))
    add_field(fields, "keywords", xml_text(core_xml, "keywords"))
    add_field(fields, "category", xml_text(core_xml, "category"))
    add_field(fields, "description", xml_text(core_xml, "description"))
    add_field(fields, "application", xml_text(app_xml, "Application"))
    add_field(fields, "app_version", xml_text(app_xml, "AppVersion"))
    add_field(fields, "company", xml_text(app_xml, "Company"))
    add_field(fields, "manager", xml_text(app_xml, "Manager"))
    add_field(fields, "template", xml_text(app_xml, "Template"))
    add_field(fields, "pages", xml_text(app_xml, "Pages"))
    add_field(fields, "words", xml_text(app_xml, "Words"))
    add_field(fields, "characters", xml_text(app_xml, "Characters"))
    add_field(fields, "lines", xml_text(app_xml, "Lines"))
    add_field(fields, "paragraphs", xml_text(app_xml, "Paragraphs"))
    add_field(fields, "total_time",
              format_docx_total_time(xml_text(app_xml, "TotalTime")))

    add_docx_custom_fields(fields, custom_xml)
    add_docx_rsid_fields(fields, word_xml_texts)
    add_wps_signal_fields(fields)
    return fields
