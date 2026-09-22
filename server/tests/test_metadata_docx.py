"""P3d T2 —— DOCX 元数据提取（domain/metadata_docx + signals + infra zip）。

对拍来源：`.superpowers/sdd/2026-09-22-p3d-metadata/oracle-docx.out.json`
（参考实现 `extractDocxMetadata` / `addDocxRsidFields` / `addWpsSignalFields`
/ 信号函数经 adm-zip 读**同一构建器产出的夹具**后的本机 Node 直跑真值）。

夹具是合成 zip：内容覆盖 core/app/custom 全键、custom 实体解码、空 property、
RSID root/去重/排序、非 word/*.xml 条目过滤、WPS 信号（值/键两路触发）。
构建器 `build_docx_fixture` 是真值对齐的**唯一**夹具来源——改动即需重跑
oracle。
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from app.domain.duplicate.metadata_docx import (
    add_docx_custom_fields,
    add_docx_rsid_fields,
    extract_docx_fields,
)
from app.domain.duplicate.metadata_signals import (
    add_wps_signal_fields,
    collect_signal_snippets,
    is_readable_signal_snippet,
)
from app.infra.parsing.metadata_extract import (
    extract_docx_metadata_file,
    read_docx_zip_parts,
)

# ---------------------------------------------------------------- 夹具 XML 常量

CORE_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>城市轨道 &amp; 交通标书</dc:title>
  <dc:subject>投标文件</dc:subject>
  <dc:creator>张三 mail:zhangsan@example.com</dc:creator>
  <cp:lastModifiedBy>李四（WPS）</cp:lastModifiedBy>
  <cp:revision>3</cp:revision>
  <dcterms:created xsi:type="dcterms:W3CDTF">2024-01-15T08:00:00Z</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">2024-03-20T12:30:00Z</dcterms:modified>
  <dc:keywords>桥梁;隧道</dc:keywords>
  <dc:category>工程标书</dc:category>
  <dc:description>第一节&#10;第二节</dc:description>
</cp:coreProperties>
"""

APP_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>WPS Office</Application>
  <AppVersion>11.1.0</AppVersion>
  <Company>中铁十一局</Company>
  <Manager>王经理</Manager>
  <Template>normal.dotm</Template>
  <Pages>42</Pages>
  <Words>12345</Words>
  <Characters>67890</Characters>
  <Lines>900</Lines>
  <Paragraphs>150</Paragraphs>
  <TotalTime>120</TotalTime>
</Properties>
"""

CUSTOM_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="标记">
    <vt:lpwstr>绝密★使用前</vt:lpwstr>
  </property>
  <property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="3" name="KSO_Build">
    <vt:lpwstr>账号：wps-uid-42</vt:lpwstr>
  </property>
  <property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="4" name="空白">
  </property>
</Properties>
"""

DOC_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p w:rsidR="00A1B2C3" w:rsidRDefault="0DEADBEEF" w:rsidP="00A1B2C3">
      <w:r><w:t>投标函</w:t></w:r>
    </w:p>
    <w:p w:rsidR="00A1B2C3" w:rsidRPr="000FFF01"/>
    <w:sectPr w:rsidRoot="00495AEC" w:rsidDel="00ABCDEF"/>
  </w:body>
</w:document>
"""

SETTINGS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:zoom w:percent="100"/>
  <w:sect w:rsidSec="00B2C3D4"/>
</w:settings>
"""


def build_docx_fixture(path: Path) -> None:
    """确定性 .docx 夹具（oracle 用同一构建器产出对拍，改动须重跑 oracle）。"""
    entries = [
        ("[Content_Types].xml",
         '<?xml version="1.0"?><Types/>'),
        ("_rels/.rels",
         '<?xml version="1.0"?><Relationships/>'),
        ("word/document.xml", DOC_XML),
        ("word/settings.xml", SETTINGS_XML),
        ("word/_rels/document.xml.rels",
         '<?xml version="1.0"?><Relationships/>'),
        ("docProps/core.xml", CORE_XML),
        ("docProps/app.xml", APP_XML),
        ("docProps/custom.xml", CUSTOM_XML),
    ]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)


def build_rsid_order_fixture(path: Path) -> None:
    """两个 word/*.xml 各带 rsidRoot 且共享一个 RSID 值：钉「首个 root 胜出」
    与跨条目去重（oracle 场景 fixture-rsid-order.docx）。"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/a.xml",
                         'p rsidRoot="00112233" rsidR="00AABBCC" q')
        archive.writestr("word/b.xml",
                         'p rsidRoot="00445566" rsidR="00AABBCC" q')


def build_empty_fixture(path: Path) -> None:
    """无 docProps、无 word/*.xml 的退化 zip（oracle 场景 fixture-empty.docx）。"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')


# ---------------------------------------------------------------------------
# 纯函数结构断言（键序 = 参考 Map 插入序，svc.cjs :710-738）
# ---------------------------------------------------------------------------


def test_extract_docx_fields_key_order_contract():
    fields = extract_docx_fields(CORE_XML, APP_XML, CUSTOM_XML,
                                 [DOC_XML, SETTINGS_XML])
    keys = list(fields)
    # core 10 键 → app 11 键 → custom → rsid → wps（wps 属动态尾部，只看前缀）
    assert keys[:21] == [
        "title", "subject", "author", "last_modified_by", "revision",
        "created", "modified", "keywords", "category", "description",
        "application", "app_version", "company", "manager", "template",
        "pages", "words", "characters", "lines", "paragraphs", "total_time",
    ]
    custom_start = keys.index("custom:标记")
    assert keys[custom_start:custom_start + 2] == ["custom:标记",
                                                   "custom:KSO_Build"]
    # 空白 property（无内层元素）不产生键
    assert "custom:空白" not in keys
    rsid_start = keys.index("word_rsid_root")
    assert keys[rsid_start:rsid_start + 4] == [
        "word_rsid_root", "word_rsid_count", "word_rsid_values",
        "word_rsid_fingerprint"]
    # wps:* 恒在尾部
    wps_start = next(i for i, k in enumerate(keys) if k.startswith("wps:"))
    assert all(key.startswith("wps:") for key in keys[wps_start:])


def test_read_docx_zip_parts_filters_word_xml():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fixture.docx"
        build_docx_fixture(path)
        core, app, custom, word_texts = read_docx_zip_parts(path)
    assert core == CORE_XML and app == APP_XML and custom == CUSTOM_XML
    # word/*.xml 两个条目按 central directory 序；document.xml.rels 被滤除
    assert word_texts == [DOC_XML, SETTINGS_XML]


# ---------------------------------------------------------------------------
# oracle 对拍（oracle-docx.out.json，2026-09-22 Node 直跑参考真值）
# ---------------------------------------------------------------------------

# 注意 word_rsid_values 是**半角**分号：summarizeValues 产出全角 `；`，
# 但 addField → normalizeValue 的 NFKC 把 U+FF1B 折成 U+003B（跨层陷阱，
# oracle 实证）。description 的 &#10; 字面保留：decodeXml 不解析数值实体。
_ORACLE_FULL_FIELDS = [
    ("title", "城市轨道 & 交通标书"),
    ("subject", "投标文件"),
    ("author", "张三 mail:zhangsan@example.com"),
    ("last_modified_by", "李四(WPS)"),
    ("revision", "3"),
    ("created", "2024-01-15T08:00:00Z"),
    ("modified", "2024-03-20T12:30:00Z"),
    ("keywords", "桥梁;隧道"),
    ("category", "工程标书"),
    ("description", "第一节&#10;第二节"),
    ("application", "WPS Office"),
    ("app_version", "11.1.0"),
    ("company", "中铁十一局"),
    ("manager", "王经理"),
    ("template", "normal.dotm"),
    ("pages", "42"),
    ("words", "12345"),
    ("characters", "67890"),
    ("lines", "900"),
    ("paragraphs", "150"),
    ("total_time", "120 分钟"),
    ("custom:标记", "绝密★使用前"),
    ("custom:KSO_Build", "账号:wps-uid-42"),
    ("word_rsid_root", "00495AEC"),
    ("word_rsid_count", "6"),
    ("word_rsid_values",
     "000FFF01;00495AEC;00A1B2C3;00ABCDEF;00B2C3D4;0DEADBEEF"),
    ("word_rsid_fingerprint",
     "e8ea50ab6cfdbf3beaa22e513b4c9a26dd58c8d779c821d80a0a9b002188b5a0"),
    ("wps:author", "张三 mail:zhangsan@example.com"),
    ("wps:last_modified_by", "李四(WPS)"),
    ("wps:application", "WPS Office"),
    ("wps:custom_kso_build", "账号:wps-uid-42"),
]


def test_full_fixture_fields_match_oracle():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fixture-full.docx"
        build_docx_fixture(path)
        fields = extract_docx_metadata_file(path)
    assert list(fields.items()) == _ORACLE_FULL_FIELDS


def test_rsid_order_fixture_matches_oracle():
    """跨条目去重 + 首个 rsidroot 胜出（word/a.xml 先于 word/b.xml）。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fixture-rsid-order.docx"
        build_rsid_order_fixture(path)
        fields = extract_docx_metadata_file(path)
    assert list(fields.items()) == [
        ("word_rsid_root", "00112233"),
        ("word_rsid_count", "3"),
        ("word_rsid_values", "00112233;00445566;00AABBCC"),
        ("word_rsid_fingerprint",
         "418cac6f50490bde4b4a8693ed76c0d59b732f34dbf61f52d8a1200999d2e1e5"),
    ]


def test_empty_fixture_matches_oracle():
    """无 docProps/无 word 条目：仅剩 count=0（root/values/fingerprint 因空被跳过）。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fixture-empty.docx"
        build_empty_fixture(path)
        fields = extract_docx_metadata_file(path)
    assert list(fields.items()) == [("word_rsid_count", "0")]


def test_signals_match_oracle():
    assert collect_signal_snippets("wps office 账号admin", 5) == [
        "wps office 账号admin"]
    # \u0000 被整体剥除后 mail 落在 "abmailc"
    assert collect_signal_snippets("a\u0000bmailc", 5) == ["abmailc"]
    # 可读率 5/19 < 0.75 → 无片段
    assert collect_signal_snippets("mail ★★★★★★★★★★★★★★", 5) == []
    # 窗口重叠 → 去重成 1 条
    assert collect_signal_snippets("mail x mail x mail", 5) == [
        "mail x mail x mail"]
    # limit 只约束产出数
    assert collect_signal_snippets("mail a mail b mail c", 1) == [
        "mail a mail b mail c"]
    # 窗口 = 前 40 / 后 80
    assert collect_signal_snippets("x" * 50 + "email" + "y" * 100, 5) == [
        "x" * 40 + "email" + "y" * 80]
    assert is_readable_signal_snippet("标书mail123") is True
    assert is_readable_signal_snippet("mail★★★") is False
    assert is_readable_signal_snippet("") is False


def test_add_wps_signal_fields_matches_oracle():
    fields = {"application": "WPS Office", "title": "普通标题"}
    add_wps_signal_fields(fields)
    assert list(fields.items()) == [
        ("application", "WPS Office"),
        ("title", "普通标题"),
        ("wps:application", "WPS Office"),
    ]


def test_add_docx_rsid_fields_root_branch_oracle():
    """rsidroot 首个胜出（T2 探针靶点）+ 值排序跨条目合流。"""
    fields: dict[str, str] = {}
    add_docx_rsid_fields(fields, [
        'p rsidRoot="00112233" rsidR="00AABBCC" q',
        'p rsidRoot="00445566" rsidR="00AABBCC" q',
    ])
    assert fields["word_rsid_root"] == "00112233"
    assert fields["word_rsid_count"] == "3"
    assert fields["word_rsid_values"] == "00112233;00445566;00AABBCC"


def test_add_docx_custom_fields_entity_decoding():
    fields: dict[str, str] = {}
    add_docx_custom_fields(fields, (
        '<Properties><property name="标题&amp;二">'
        "<vt:lpwstr>&lt;b&gt;加粗&lt;/b&gt;</vt:lpwstr></property></Properties>"
    ))
    # 键与值各自过 decodeXml（命名实体）；值再过 normalizeValue
    assert fields == {"custom:标题&二": "<b>加粗</b>"}
