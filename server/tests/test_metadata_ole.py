r"""P3d T3 —— OLE/CFB 元数据提取（domain/metadata_ole + infra CFB 适配）。

对拍来源：`.superpowers/sdd/2026-09-22-p3d-metadata/oracle-ole.out.json`
（参考实现 `extractOleMetadata` / `parsePropertySetStream` 全家 / 信号函数
在本机 Node 直跑、读**同一字节**夹具后的真值）。夹具
`tests/fixtures/ole-metadata-fixture.doc` 由 oracle-ole.cjs 用 cfb 库生成、
已入库跟踪——**改动夹具必须重跑 oracle**。

覆盖：cfb 形状契约（12 条 sid 序 + FullPaths 形状 + content 语义：仅 root
为 undefined）、full 27 字段序（含双属性集第二 set 的 `ole_prop_{id}` 命名、
`code_page` 覆盖链、半角分号折叠、`﹀`U+FE40 经 NFKC 变 `〉`）、
find_cfb_stream 的四候选收敛与**空 Buffer truthy**（:615 `entry?.content`
——Buffer 是对象）、属性集流名的信号排除、cp936/gb18030/utf16le 解码、
FILETIME/时长/版本号（`>>>` 的 ToUint32）。
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from app.domain.duplicate.metadata_ole import (
    DOC_SUMMARY_PROPERTY_MAP,
    CfbEntry,
    _node_utf16le_decode,
    decode_code_page_buffer,
    find_cfb_stream,
    format_version_number,
    parse_file_time_duration,
    parse_file_time_value,
    parse_property_set_stream,
)
from app.domain.duplicate.metadata_signals import collect_binary_signal_snippets
from app.infra.parsing.metadata_extract import (
    extract_ole_metadata_file,
    read_cfb_entries,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ole-metadata-fixture.doc"

# ---------------------------------------------------------------- oracle 常量

# (fullPath, type, size, content)——content 为 None 表示 oracle 的
# contentIsUndefined=true（仅 root）；否则是 contentLength。
_ORACLE_CFB_SHAPE: list[tuple[str, int, int, int | None]] = [
    ("Root Entry/", 5, 768, None),
    ("Root Entry/Macros/", 1, 0, 0),
    ("Root Entry/Macros/VBA/", 1, 0, 0),
    ("Root Entry/Macros/VBA/dir", 2, 13, 13),
    ("Root Entry/\u0001Sh33tJ5", 2, 4, 4),
    ("Root Entry/Embeddings", 2, 0, 0),
    ("Root Entry/WordDocument", 2, 30, 30),
    ("Root Entry/\u0005SummaryInformation", 2, 400, 400),
    ("Root Entry/\u0005DocumentSummaryInformation", 2, 128, 128),
    ("/", 0, 0, 0),
    ("/", 0, 0, 0),
    ("/", 0, 0, 0),
]

# oracle `full` 段：extractOleMetadata(夹具) 的 27 个 (key, value)（键序敏感）。
# ole_stream_paths / wps 镜像里的 `;` 是半角——addField 的 NFKC 把全角 `；`
# 折成半角（T2 实证）；ole_signal 字段保留全角 `；`（addListField 不整体归一）。
_ORACLE_FULL_FIELDS: list[tuple[str, str]] = [
    ("code_page", "936"),
    ("title", "桥梁投标文件"),
    ("author", "张三 admin@corp-mail.com"),
    ("revision", "7"),
    ("total_time", "2 小时 15 分钟"),
    ("created", "2024-01-15T08:00:00.000Z"),
    ("ole_prop_99", "无字典属性"),
    ("custom:秘密标记", "走字典的值"),
    ("ole_prop_1", "936"),
    ("ole_prop_15", "中铁建"),
    ("ole_prop_16", "true"),
    ("ole_prop_23", "720897"),
    ("company", "中铁建"),
    ("links_dirty", "true"),
    ("app_version", "11.0001"),
    ("ole_storage_count", "2"),
    ("ole_stream_count", "11"),
    ("ole_stream_paths",
     "/;Root Entry/DocumentSummaryInformation;Root Entry/Embeddings"
     ";Root Entry/Macros/;Root Entry/Macros/VBA/;Root Entry/Macros/VBA/dir"
     ";Root Entry/Sh33tJ5;Root Entry/SummaryInformation;Root Entry/WordDocument"),
    ("ole_stream_paths_fingerprint",
     "e0674736ceade93e162e3e968f8e50e952ed207c737142ab428cb8bedca078e6"),
    ("ole_stream_sizes_fingerprint",
     "39517c94687838c2130eb594ca0d65dbe8ff62f1a6d8077a4cd826599f34dc5e"),
    ("ole_has_macro_storage", "是"),
    ("ole_macro_paths",
     "Root Entry/Macros/;Root Entry/Macros/VBA/;Root Entry/Macros/VBA/dir"),
    ("ole_signal:root_entry_macros_vba_dir", "VBA mail code"),
    ("ole_signal:root_entry_worddocument",
     "mail admin 璐〉彿 wps-uid-77；mail admin 账号 wps-uid-77"),
    ("wps:author", "张三 admin@corp-mail.com"),
    ("wps:ole_signal_root_entry_macros_vba_dir", "VBA mail code"),
    ("wps:ole_signal_root_entry_worddocument",
     "mail admin 璐〉彿 wps-uid-77;mail admin 账号 wps-uid-77"),
]

# oracle `unit` 段（原始解码真值，未过 normalize——`﹀` 是 U+FE40）。
_ORACLE_BINARY_SNIPPETS_WORD = ["mail admin 璐﹀彿 wps-uid-77",
                                "mail admin 账号 wps-uid-77"]
_ORACLE_BINARY_SNIPPETS_VBA = ["VBA mail code"]
# utf16le 重解释的 15 个码元（含不可见字符，全转义防转录丢字）：
# "mail admin " + 账号(wps-uid-77) 的 UTF-8 字节按 LE u16 逐对重解释
# （0xE820 是私用区字符——终端不显示，曾致转录漏字）。
_ORACLE_DECODE_WORD_UTF16LE = (
    "\u616d\u6c69\u6120\u6d64\u6e69\ue820\ua6b4\u8fe5\u20b7\u7077"
    "\u2d73\u6975\u2d64\u3737\x00")
_ORACLE_DECODE_WORD_CP936 = "mail admin 璐﹀彿 wps-uid-77\u0000\u0000"
_ORACLE_DECODE_WORD_UTF8 = "mail admin 账号 wps-uid-77\u0000\u0000"


def _entry(entries: list[CfbEntry], path: str) -> CfbEntry:
    return next(e for e in entries if e.path == path)


# ---------------------------------------------------------------- 形状与全字段


def test_cfb_shape_matches_oracle():
    entries = read_cfb_entries(FIXTURE)
    assert len(entries) == len(_ORACLE_CFB_SHAPE)
    for got, (path, entry_type, size, content_len) in zip(entries, _ORACLE_CFB_SHAPE):
        assert (got.path, got.entry_type, got.size) == (path, entry_type, size)
        if content_len is None:
            assert got.content is None
        else:
            assert got.content is not None and len(got.content) == content_len


def test_full_fields_match_oracle():
    fields = extract_ole_metadata_file(FIXTURE)
    assert list(fields.items()) == _ORACLE_FULL_FIELDS


# ---------------------------------------------------------------- find 收敛


def test_find_cfb_stream_four_candidates_match_oracle_probe():
    """oracle find_probe：四个候选形式全命中 400 字节流；裸名不命中。"""
    entries = read_cfb_entries(FIXTURE)
    for candidate in ("\u0005SummaryInformation", "/\u0005SummaryInformation",
                      "!SummaryInformation", "/!SummaryInformation"):
        found = find_cfb_stream(entries, candidate)
        assert found is not None and len(found) == 400, candidate
    assert find_cfb_stream(entries, "SummaryInformation") is None
    assert find_cfb_stream(entries, "/SummaryInformation") is None
    # 第二条属性流（find_via_ref=400 只钉了 Summary；DocSummary 128 字节）
    doc_summary = find_cfb_stream(entries, "!DocumentSummaryInformation")
    assert doc_summary is not None and len(doc_summary) == 128


def test_find_cfb_stream_empty_buffer_is_truthy():
    """:615 `entry?.content`——空 Buffer 是对象（truthy），空流也命中。

    oracle 无法直接表达（夹具的属性流非空）；此用例钉的是 JS 对象 truthy
    语义的对齐（区别于 :663 信号门的 `content?.length` 长度判定）。
    """
    entries = [CfbEntry("Root Entry/\u0005Empty", 2, 0, b"")]
    assert find_cfb_stream(entries, "\u0005Empty") == b""


# ---------------------------------------------------------------- unit 真值


def test_unit_binary_snippets_match_oracle():
    entries = read_cfb_entries(FIXTURE)
    word = _entry(entries, "Root Entry/WordDocument").content
    vba_dir = _entry(entries, "Root Entry/Macros/VBA/dir").content
    assert collect_binary_signal_snippets(word) == _ORACLE_BINARY_SNIPPETS_WORD
    assert collect_binary_signal_snippets(vba_dir) == _ORACLE_BINARY_SNIPPETS_VBA


def test_unit_decodes_match_oracle():
    entries = read_cfb_entries(FIXTURE)
    word = _entry(entries, "Root Entry/WordDocument").content
    assert _node_utf16le_decode(word) == _ORACLE_DECODE_WORD_UTF16LE
    assert decode_code_page_buffer(word, 936) == _ORACLE_DECODE_WORD_CP936
    assert word.decode("utf-8", errors="replace") == _ORACLE_DECODE_WORD_UTF8
    assert decode_code_page_buffer(b"\xd6\xd0", 936) == "中"
    assert decode_code_page_buffer(b"\xd6", 1252) == "Ö"


def test_unit_filetime_duration_version_match_oracle():
    # filetime：2024-01-15T08:00:00Z → ticks = (unix_ms + 11644473600000) * 10^4
    ticks = (1705305600000 + 11644473600000) * 10000
    assert parse_file_time_value(struct.pack("<Q", ticks), 0) \
        == "2024-01-15T08:00:00.000Z"
    assert parse_file_time_value(struct.pack("<Q", 0), 0) == ""
    assert parse_file_time_duration(struct.pack("<Q", 8100 * 10_000_000), 0) \
        == "2 小时 15 分钟"
    assert parse_file_time_duration(struct.pack("<Q", 45 * 10_000_000), 0) \
        == "45 秒"
    assert parse_file_time_duration(struct.pack("<Q", 90 * 60 * 10_000_000), 0) \
        == "1 小时 30 分钟"
    assert parse_file_time_duration(struct.pack("<Q", 0), 0) == ""
    # 0x000B0001 = 720897
    assert format_version_number(720897) == "11.0001"
    # `>>>` 的 ToUint32（负值回绕）——JS 语言语义，参考对 readInt32LE 负值的行为
    assert format_version_number(-1) == "65535.65535"
    assert format_version_number("abc") == "abc"


# ---------------------------------------------------------------- 结构语义


def test_two_streams_and_second_set_naming_match_oracle():
    """流间分派 + :604 `index === 0 ? propertyMap : {}` + 跨流 code_page 覆盖链。

    SUMMARY 流**第一 set** 的 code_page 是 1200、**第二 set**（无 map）产出
    ole_prop_1=936 等；DOCSUM 流的 0x01=936 经 map 同名 `code_page` 把 SUMMARY
    的 1200 覆盖（addField 是 Map.set 保位覆盖 → full 第一项终值 936）。
    """
    entries = read_cfb_entries(FIXTURE)
    summary = _entry(entries, "Root Entry/\u0005SummaryInformation").content
    doc_summary = _entry(
        entries, "Root Entry/\u0005DocumentSummaryInformation").content

    from app.domain.duplicate.metadata_ole import SUMMARY_PROPERTY_MAP
    summary_fields = parse_property_set_stream(summary, SUMMARY_PROPERTY_MAP)
    assert list(summary_fields.keys()) == [
        "code_page", "title", "author", "revision", "total_time", "created",
        "ole_prop_99", "custom:秘密标记",                       # 第一 set
        "ole_prop_1", "ole_prop_15", "ole_prop_16", "ole_prop_23",  # 第二 set
    ]
    assert summary_fields["code_page"] == "1200"           # 第一 set 的原始值
    assert summary_fields["ole_prop_1"] == "936"           # 第二 set 的 0x01
    assert summary_fields["custom:秘密标记"] == "走字典的值"  # 第一 set 的字典

    docsum_fields = parse_property_set_stream(doc_summary, DOC_SUMMARY_PROPERTY_MAP)
    assert list(docsum_fields.items()) == [
        ("code_page", "936"),   # 与 SUMMARY 的 1200 同名 → extract 时覆盖
        ("company", "中铁建"), ("links_dirty", "true"), ("app_version", "11.0001"),
    ]


def test_property_set_streams_excluded_from_signals():
    """SummaryInformation 的字节里含 admin@corp-mail.com（mail 信号词），
    若 :690 的流名排除失效，会多出 ole_signal 字段——oracle full 没有它们。"""
    fields = extract_ole_metadata_file(FIXTURE)
    assert "ole_signal:root_entry_summaryinformation" not in fields
    assert "ole_signal:root_entry_documentsummaryinformation" not in fields
    # 空槽（path="/"）与 storage 无 content/无信号词 → 无独立信号键
    assert "ole_signal:" not in fields
    assert "ole_signal:root_entry_macros" not in fields


def test_invalid_ole_file_raises(tmp_path):
    """参考 `CFB.read` 对非 OLE 抛异常（legacy 包装层转错误信息）——
    olefile 的异常原样传播。"""
    bogus = tmp_path / "bogus.doc"
    bogus.write_bytes(b"definitely not a CFB file" * 10)
    with pytest.raises(Exception):
        extract_ole_metadata_file(bogus)
