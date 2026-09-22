"""P3d T4：PDF 元数据提取对拍（metadata_pdf 纯核心 + pypdf 适配层）。

断言值全部来自参考实现直跑真值（oracle-pdf.out.json，由
`.superpowers/sdd/2026-09-22-p3d-metadata/oracle-pdf.cjs` 产出）：
full 三夹具全字段（含键序）、getInfo() 形状（info 派生键/键值、XMP
小写键迭代、fingerprints）、unit 解码与正则小件。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.duplicate.metadata_pdf import (
    PdfMetadata,
    canonical_pdf_xmp_key,
    decode_pdf_hex_string,
    decode_pdf_literal_string,
    decode_pdf_name,
    decode_pdf_token_string,
    extract_pdf_attachment_names,
    extract_pdf_trailer_ids,
    get_pdf_metadata_entries,
    get_pdf_metadata_value,
    normalize_pdf_id_token,
)
from app.infra.parsing.metadata_extract import (
    extract_pdf_metadata_file,
    parse_xmp_metadata,
    read_pdf_parse_result,
)

FIXTURES = Path(__file__).parent / "fixtures"

_ORACLE_FULL: dict[str, list[tuple[str, str]]] = {
    "pdf-info-fixture.pdf": [
        ("pdf_version", "1.7"),
        ("pdf_info:pdfformatversion", "1.7"),
        ("pdf_info:islinearized", "false"),
        ("pdf_info:isacroformpresent", "false"),
        ("pdf_info:isxfapresent", "false"),
        ("pdf_info:iscollectionpresent", "false"),
        ("pdf_info:issignaturespresent", "false"),
        ("title", "桥梁投标文件"),
        ("pdf_info:title", "桥梁投标文件"),
        ("author", "张三 zhangsan@corp-mail.com"),
        ("pdf_info:author", "张三 zhangsan@corp-mail.com"),
        ("subject", "投标文件"),
        ("pdf_info:subject", "投标文件"),
        ("keywords", "桥梁;隧道"),
        ("pdf_info:keywords", "桥梁;隧道"),
        ("creator", "WPS Office"),
        ("pdf_info:creator", "WPS Office"),
        ("producer", "PyMuPDF 造夹具"),
        ("pdf_info:producer", "PyMuPDF 造夹具"),
        ("created", "D:20240115080000"),
        ("pdf_info:creationdate", "D:20240115080000"),
        ("modified", "D:20240320123000"),
        ("pdf_info:moddate", "D:20240320123000"),
        ("pages", "1"),
        ("fingerprints", "32346e25c394c2a60dc282c3bec2a82c；47f52ca9566dcd517e713ee8e75daf72"),
        ("pdf_raw:producer", "MuPDF 1.28.2；PyMuPDF 造夹具"),
        ("pdf_raw:title", "桥梁投标文件"),
        ("pdf_raw:author", "张三 zhangsan@corp-mail.com"),
        ("pdf_raw:subject", "投标文件"),
        ("pdf_raw:keywords", "桥梁;隧道"),
        ("pdf_raw:creator", "WPS Office"),
        ("pdf_raw:creationdate", "D:20240115080000"),
        ("pdf_raw:moddate", "D:20240320123000"),
        ("pdf_raw:signals", "words<FEFF68656881003B96A79053>/Creator(WPS Office)/Producer<FEFF00500079004D00750050004400460020902059395177>/CreationDate(D:2024"),
        ("pdf_header_version", "1.7"),
        ("pdf_object_count", "5"),
        ("pdf_startxref_count", "1"),
        ("pdf_incremental_update_count", "0"),
        ("pdf_linearized", "否"),
        ("pdf_xref_type", "传统 xref 表"),
        ("pdf_trailer_id", "<32346e25c394c2a60dc282c3bec2a82c> / <47f52ca9566dcd517e713ee8e75daf72>"),
        ("pdf_has_acroform", "否"),
        ("pdf_has_xfa", "否"),
        ("pdf_signature_count", "0"),
        ("pdf_byterange_signature_count", "0"),
        ("pdf_embedded_file_count", "0"),
        ("wps:author", "张三 zhangsan@corp-mail.com"),
        ("wps:pdf_info_author", "张三 zhangsan@corp-mail.com"),
        ("wps:creator", "WPS Office"),
        ("wps:pdf_info_creator", "WPS Office"),
        ("wps:pdf_raw_author", "张三 zhangsan@corp-mail.com"),
        ("wps:pdf_raw_creator", "WPS Office"),
        ("wps:pdf_raw_signals", "words<FEFF68656881003B96A79053>/Creator(WPS Office)/Producer<FEFF00500079004D00750050004400460020902059395177>/CreationDate(D:2024"),
    ],
    "pdf-xmp-fixture.pdf": [
        ("pdf_version", "1.7"),
        ("pdf_info:pdfformatversion", "1.7"),
        ("pdf_info:islinearized", "false"),
        ("pdf_info:isacroformpresent", "false"),
        ("pdf_info:isxfapresent", "false"),
        ("pdf_info:iscollectionpresent", "false"),
        ("pdf_info:issignaturespresent", "false"),
        ("title", "桥梁投标文件"),
        ("pdf_info:title", "桥梁投标文件"),
        ("author", "张三 zhangsan@corp-mail.com"),
        ("pdf_info:author", "张三 zhangsan@corp-mail.com"),
        ("subject", "投标文件"),
        ("pdf_info:subject", "投标文件"),
        ("keywords", "桥梁;隧道"),
        ("pdf_info:keywords", "桥梁;隧道"),
        ("creator", "WPS Office"),
        ("pdf_info:creator", "WPS Office"),
        ("producer", "PyMuPDF 造夹具"),
        ("pdf_info:producer", "PyMuPDF 造夹具"),
        ("created", "D:20240115080000"),
        ("pdf_info:creationdate", "D:20240115080000"),
        ("modified", "D:20240320123000"),
        ("pdf_info:moddate", "D:20240320123000"),
        ("pdf_xmp:dc_title", "水利标书正文"),
        ("pdf_xmp:dc_creator", "李四 mail:lisi@corp-mail.com"),
        ("description", "第一节第二节"),
        ("pdf_xmp:dc_description", "第一节第二节"),
        ("pdf_xmp:pdf_keywords", "水利;大坝"),
        ("pdf_xmp:pdf_producer", "PyMuPDF XMP 造夹具"),
        ("pdf_xmp:xmp_creatortool", "WPS Office"),
        ("pdf_xmp:xmp_createdate", "2024-02-16T09:30:00+08:00"),
        ("pdf_xmp:xmp_modifydate", "2024-04-01T18:45:00+08:00"),
        ("pdf_xmp:xmp_metadatadate", "2024-04-02T10:00:00+08:00"),
        ("pdf_xmp:pdfw_customprop", "自定义值"),
        ("pdf_xmp:raw_signals", "tle> <dc:creator><rdf:Seq><rdf:li>李四 mail:lisi@corp-mail.com</rdf:li></rdf:Seq></dc:creator> <dc:description><rdf:Alt>；ator><rdf:Seq><rdf:li>李四 mail:lisi@corp-mail.com</rdf:li></rdf:Seq></dc:creator> <dc:description><rdf:Alt><rdf:li xml:lan；造夹具</pdf:Producer> <xmp:CreatorTool>WPS Office</xmp:CreatorTool> <xmp:CreateDate>2024-02-16T09:30:00+08:00</xmp:CreateDate>"),
        ("pages", "1"),
        ("fingerprints", "7fc294c2aac2b80bc3813058c38f3872；dfd792ede42e0a3648d0419f9c831e3e"),
        ("pdf_raw:producer", "MuPDF 1.28.2；PyMuPDF 造夹具"),
        ("pdf_raw:title", "桥梁投标文件"),
        ("pdf_raw:author", "张三 zhangsan@corp-mail.com"),
        ("pdf_raw:subject", "投标文件"),
        ("pdf_raw:keywords", "桥梁;隧道"),
        ("pdf_raw:creator", "WPS Office"),
        ("pdf_raw:creationdate", "D:20240115080000"),
        ("pdf_raw:moddate", "D:20240320123000"),
        ("pdf_raw:signals", "words<FEFF68656881003B96A79053>/Creator(WPS Office)/Producer<FEFF00500079004D00750050004400460020902059395177>/CreationDate(D:2024；le> <dc:creator><rdf:Seq><rdf:li>鏉庡洓 mail:lisi@corp-mail.com</rdf:li></rdf:Seq></dc:creator> <dc:description><rdf:Alt>；tor><rdf:Seq><rdf:li>鏉庡洓 mail:lisi@corp-mail.com</rdf:li></rdf:Seq></dc:creator> <dc:description><rdf:Alt><rdf:li xml:lan；tle> <dc:creator><rdf:Seq><rdf:li>李四 mail:lisi@corp-mail.com</rdf:li></rdf:Seq></dc:creator> <dc:description><rdf:Alt>；ator><rdf:Seq><rdf:li>李四 mail:lisi@corp-mail.com</rdf:li></rdf:Seq></dc:creator> <dc:description><rdf:Alt><rdf:li xml:lan"),
        ("pdf_header_version", "1.7"),
        ("pdf_object_count", "6"),
        ("pdf_startxref_count", "1"),
        ("pdf_incremental_update_count", "0"),
        ("pdf_linearized", "否"),
        ("pdf_xref_type", "传统 xref 表"),
        ("pdf_trailer_id", "<7fc294c2aac2b80bc3813058c38f3872> / <dfd792ede42e0a3648d0419f9c831e3e>"),
        ("pdf_has_acroform", "否"),
        ("pdf_has_xfa", "否"),
        ("pdf_signature_count", "0"),
        ("pdf_byterange_signature_count", "0"),
        ("pdf_embedded_file_count", "0"),
        ("wps:author", "张三 zhangsan@corp-mail.com"),
        ("wps:pdf_info_author", "张三 zhangsan@corp-mail.com"),
        ("wps:creator", "WPS Office"),
        ("wps:pdf_info_creator", "WPS Office"),
        ("wps:pdf_xmp_dc_creator", "李四 mail:lisi@corp-mail.com"),
        ("wps:pdf_xmp_xmp_creatortool", "WPS Office"),
        ("wps:pdf_xmp_raw_signals", "tle> <dc:creator><rdf:Seq><rdf:li>李四 mail:lisi@corp-mail.com</rdf:li></rdf:Seq></dc:creator> <dc:description><rdf:Alt>;ator><rdf:Seq><rdf:li>李四 mail:lisi@corp-mail.com</rdf:li></rdf:Seq></dc:creator> <dc:description><rdf:Alt><rdf:li xml:lan;造夹具</pdf:Producer> <xmp:CreatorTool>WPS Office</xmp:CreatorTool> <xmp:CreateDate>2024-02-16T09:30:00+08:00</xmp:CreateDate>"),
        ("wps:pdf_raw_author", "张三 zhangsan@corp-mail.com"),
        ("wps:pdf_raw_creator", "WPS Office"),
        ("wps:pdf_raw_signals", "words<FEFF68656881003B96A79053>/Creator(WPS Office)/Producer<FEFF00500079004D00750050004400460020902059395177>/CreationDate(D:2024;le> <dc:creator><rdf:Seq><rdf:li>鏉庡洓 mail:lisi@corp-mail.com</rdf:li></rdf:Seq></dc:creator> <dc:description><rdf:Alt>;tor><rdf:Seq><rdf:li>鏉庡洓 mail:lisi@corp-mail.com</rdf:li></rdf:Seq></dc:creator> <dc:description><rdf:Alt><rdf:li xml:lan;tle> <dc:creator><rdf:Seq><rdf:li>李四 mail:lisi@corp-mail.com</rdf:li></rdf:Seq></dc:creator> <dc:description><rdf:Alt>;ator><rdf:Seq><rdf:li>李四 mail:lisi@corp-mail.com</rdf:li></rdf:Seq></dc:creator> <dc:description><rdf:Alt><rdf:li xml:lan"),
    ],
    "pdf-structure-fixture.pdf": [
        ("pdf_version", "1.7"),
        ("pdf_info:pdfformatversion", "1.7"),
        ("pdf_info:islinearized", "false"),
        ("pdf_info:isacroformpresent", "false"),
        ("pdf_info:isxfapresent", "false"),
        ("pdf_info:iscollectionpresent", "false"),
        ("pdf_info:issignaturespresent", "false"),
        ("title", "桥梁投标文件"),
        ("pdf_info:title", "桥梁投标文件"),
        ("author", "张三 zhangsan@corp-mail.com"),
        ("pdf_info:author", "张三 zhangsan@corp-mail.com"),
        ("subject", "投标文件"),
        ("pdf_info:subject", "投标文件"),
        ("keywords", "桥梁;隧道"),
        ("pdf_info:keywords", "桥梁;隧道"),
        ("creator", "WPS Office"),
        ("pdf_info:creator", "WPS Office"),
        ("producer", "PyMuPDF 造夹具"),
        ("pdf_info:producer", "PyMuPDF 造夹具"),
        ("created", "D:20240115080000"),
        ("pdf_info:creationdate", "D:20240115080000"),
        ("modified", "D:20240320123000"),
        ("pdf_info:moddate", "D:20240320123000"),
        ("pages", "1"),
        ("fingerprints", "112233445566778899aabbccddeeff00；fedcba98765432100123456789abcdef"),
        ("pdf_raw:producer", "MuPDF 1.28.2；PyMuPDF 造夹具"),
        ("pdf_raw:title", "桥梁投标文件"),
        ("pdf_raw:author", "张三 zhangsan@corp-mail.com"),
        ("pdf_raw:subject", "投标文件"),
        ("pdf_raw:keywords", "桥梁;隧道"),
        ("pdf_raw:creator", "WPS Office"),
        ("pdf_raw:creationdate", "D:20240115080000；D:20260922124840+09'00'"),
        ("pdf_raw:moddate", "D:20240320123000；D:20260922124840+09'00'"),
        ("pdf_raw:signals", "words<FEFF68656881003B96A79053>/Creator(WPS Office)/Producer<FEFF00500079004D00750050004400460020902059395177>/CreationDate(D:2024"),
        ("pdf_header_version", "1.7"),
        ("pdf_object_count", "9"),
        ("pdf_startxref_count", "2"),
        ("pdf_incremental_update_count", "1"),
        ("pdf_linearized", "否"),
        ("pdf_xref_type", "传统 xref 表"),
        ("pdf_trailer_id", "<0ec2a22fc3bd6e62c38bc2abc39e2fc3> / <51fa6cc4d82888992969bd09c46b24bb>;<112233445566778899aabbccddeeff00> / <fedcba98765432100123456789abcdef>"),
        ("pdf_has_acroform", "是"),
        ("pdf_has_xfa", "是"),
        ("pdf_signature_count", "1"),
        ("pdf_byterange_signature_count", "1"),
        ("pdf_embedded_file_count", "2"),
        ("pdf_embedded_file_names", "attach-manual.txt;hello.txt"),
        ("wps:author", "张三 zhangsan@corp-mail.com"),
        ("wps:pdf_info_author", "张三 zhangsan@corp-mail.com"),
        ("wps:creator", "WPS Office"),
        ("wps:pdf_info_creator", "WPS Office"),
        ("wps:pdf_raw_author", "张三 zhangsan@corp-mail.com"),
        ("wps:pdf_raw_creator", "WPS Office"),
        ("wps:pdf_raw_signals", "words<FEFF68656881003B96A79053>/Creator(WPS Office)/Producer<FEFF00500079004D00750050004400460020902059395177>/CreationDate(D:2024"),
    ],
}

# pdf.js getInfo().info 的键序与键值（派生键在前、Info 字典在后；null/false 原样）
_ORACLE_INFO_ENTRIES: list[tuple[str, object]] = [
    ("PDFFormatVersion", "1.7"),
    ("Language", None),
    ("EncryptFilterName", None),
    ("IsLinearized", False),
    ("IsAcroFormPresent", False),
    ("IsXFAPresent", False),
    ("IsCollectionPresent", False),
    ("IsSignaturesPresent", False),
    ("Title", "桥梁投标文件"),
    ("Author", "张三 zhangsan@corp-mail.com"),
    ("Subject", "投标文件"),
    ("Keywords", "桥梁;隧道"),
    ("Creator", "WPS Office"),
    ("Producer", "PyMuPDF 造夹具"),
    ("CreationDate", "D:20240115080000"),
    ("ModDate", "D:20240320123000"),
]

# pdf.js Metadata 迭代键值：键已小写、dc:creator 是数组、dc:description 保留 \n
_ORACLE_METADATA_ENTRIES: list[tuple[str, object]] = [
    ("dc:title", "水利标书正文"),
    ("dc:creator", ["李四 mail:lisi@corp-mail.com"]),
    ("dc:description", "第一节\n第二节"),
    ("pdf:keywords", "水利;大坝"),
    ("pdf:producer", "PyMuPDF XMP 造夹具"),
    ("xmp:creatortool", "WPS Office"),
    ("xmp:createdate", "2024-02-16T09:30:00+08:00"),
    ("xmp:modifydate", "2024-04-01T18:45:00+08:00"),
    ("xmp:metadatadate", "2024-04-02T10:00:00+08:00"),
    ("pdfw:customprop", "自定义值"),
]

# fingerprints = trailer /ID 两项的小写十六进制（pdf.js 对拍）
_ORACLE_FINGERPRINTS: dict[str, list[str]] = {
    "pdf-info-fixture.pdf": ["32346e25c394c2a60dc282c3bec2a82c", "47f52ca9566dcd517e713ee8e75daf72"],
    "pdf-xmp-fixture.pdf": ["7fc294c2aac2b80bc3813058c38f3872", "dfd792ede42e0a3648d0419f9c831e3e"],
    "pdf-structure-fixture.pdf": ["112233445566778899aabbccddeeff00", "fedcba98765432100123456789abcdef"],
}


# ---------------------------------------------------------------- 全字段对拍（含键序）


@pytest.mark.parametrize("name", [
    "pdf-info-fixture.pdf",
    "pdf-xmp-fixture.pdf",
    "pdf-structure-fixture.pdf",
])
def test_full_fields_match_oracle(name: str) -> None:
    fields = extract_pdf_metadata_file(FIXTURES / name)
    assert list(fields.items()) == _ORACLE_FULL[name]


# ---------------------------------------------------------------- getInfo() 形状（适配层）


def test_info_entries_shape() -> None:
    result = read_pdf_parse_result(FIXTURES / "pdf-info-fixture.pdf")
    assert list(result.info.items()) == _ORACLE_INFO_ENTRIES
    assert result.version is None  # pdf-parse@2 的 result.version 不存在
    assert result.total == 1
    assert result.metadata is None
    assert result.fingerprints == _ORACLE_FINGERPRINTS["pdf-info-fixture.pdf"]
    assert result.permission is None  # 未加密 → null


def test_metadata_shape_xmp() -> None:
    result = read_pdf_parse_result(FIXTURES / "pdf-xmp-fixture.pdf")
    assert result.metadata is not None
    assert list(result.metadata) == _ORACLE_METADATA_ENTRIES
    # get 大小写敏感：小写键命中、原样大写键查不到（oracle metadata_get 钉定）
    assert result.metadata.get("dc:title") == "水利标书正文"
    assert result.metadata.get("dc:creator") == ["李四 mail:lisi@corp-mail.com"]
    assert result.metadata.get("pdf:Keywords") is None
    assert result.metadata.get("Title") is None
    assert result.metadata.get_raw().startswith('<?xpacket begin=""')


def test_fingerprints_match_trailer_id() -> None:
    for name, expected in _ORACLE_FINGERPRINTS.items():
        result = read_pdf_parse_result(FIXTURES / name)
        assert result.fingerprints == expected


# ---------------------------------------------------------------- 纯核心小件（oracle unit 块）


def test_canonical_pdf_xmp_key() -> None:
    cases = {
        "dc:title": "title",
        "title": "title",
        "xmp:CreatorTool": "creator",
        "creator": "author",
        "pdf:Producer": "producer",
        "CustomProp": "",
        "dc:description": "description",
        "xmp:MetadataDate": "modified",
        "pdf:Keywords": "keywords",
        "dc:creator": "author",
        "xmp:CreateDate": "created",
        "dc:subject": "subject",
        "dc:format": "",
    }
    for raw_key, expected in cases.items():
        assert canonical_pdf_xmp_key(raw_key) == expected, raw_key


def test_decode_pdf_literal_string() -> None:
    assert decode_pdf_literal_string("X\\(1\\)\\\\") == "X(1)\\"
    assert decode_pdf_literal_string("A\\101\\102") == "AAB"
    assert decode_pdf_literal_string("a\\nb\\rc\\td") == "a\nb\rc\td"


def test_decode_pdf_hex_string() -> None:
    assert decode_pdf_hex_string("FEFF0057") == "W"
    assert decode_pdf_hex_string("FEF") == ""  # 奇数长度
    assert decode_pdf_hex_string("FFFE51007D00") == "Q}"  # LE BOM


def test_decode_pdf_name_and_token() -> None:
    assert decode_pdf_name("text#2Fplain") == "text/plain"
    assert decode_pdf_token_string("/WPS") == "WPS"
    assert decode_pdf_token_string("<FEFF0057>") == "W"


def test_normalize_pdf_id_token() -> None:
    assert normalize_pdf_id_token("<ABCDEF>") == "<abcdef>"
    assert normalize_pdf_id_token("(paren\\123id)") == "parenSid"
    assert normalize_pdf_id_token("<4567AB>") == "<4567ab>"


def test_extract_pdf_trailer_ids() -> None:
    text = ("/ID [<ABCDEF0123456789> <abcdef0123456789>]\ntrailer\n"
            "/ID [ (paren\\123id) <4567AB> ]\n")
    assert extract_pdf_trailer_ids(text) == [
        "<abcdef0123456789> / <abcdef0123456789>",
        "parenSid / <4567ab>",
    ]


def test_extract_pdf_attachment_names() -> None:
    text = ("/Type /Filespec /UF (manual.txt) /F (fallback.txt)\n"
            "/Type/Filespec /UF <FEFF0041> /Other /x\n"
            "/Type /Filespec /F /NameOnly\n"
            "/Type /Filespec /NoNameHere\n")
    # UF 优先于 F；十六进制名解码；/Filespec 后的 "/F" 不会误命中 "/Filespec"；
    # localeCompare 近似排序（A < manual.txt < NameOnly）
    assert extract_pdf_attachment_names(text) == ["A", "manual.txt", "NameOnly"]


# ---------------------------------------------------------------- metadata 形状契约的两个分支


def test_get_pdf_metadata_value_prefers_get_then_dict() -> None:
    metadata = PdfMetadata({"dc:title": "甲", "Title": ""}, "")
    # get 命中（normalize 非空）→ 返回原始值
    assert get_pdf_metadata_value(metadata, "dc:title", "Title") == "甲"
    # get 返回空 → hasOwnProperty 兜底（dict 分支）
    assert get_pdf_metadata_value({"dc:title": "乙"}, "dc:title") == "乙"
    assert get_pdf_metadata_value(None, "dc:title") == ""


def test_get_pdf_metadata_entries_two_branches() -> None:
    # PdfMetadata（可迭代）→ 全量不过滤
    metadata = PdfMetadata({"a": "1", "b": ""}, "")
    assert get_pdf_metadata_entries(metadata) == [("a", "1"), ("b", "")]
    # 普通 dict（JS 普通对象）→ entries 后过滤空值
    assert get_pdf_metadata_entries({"a": "1", "b": ""}) == [("a", "1")]


# ---------------------------------------------------------------- XMP 解析（适配层）


def test_parse_xmp_metadata_keeps_document_order_and_types() -> None:
    raw = read_pdf_parse_result(FIXTURES / "pdf-xmp-fixture.pdf").metadata
    assert raw is not None
    assert parse_xmp_metadata(raw.get_raw()) == dict(_ORACLE_METADATA_ENTRIES)
    # 属性式（rdf:Description 的属性）也收
    attr_style = ('<x:xmpmeta xmlns:x="adobe:ns:meta/">'
                  '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                  '<rdf:Description rdf:about="" xmlns:pdf="http://ns.adobe.com/pdf/1.3/" '
                  'pdf:Keywords="属性式"/>'
                  "</rdf:RDF></x:xmpmeta>")
    assert parse_xmp_metadata(attr_style) == {"pdf:keywords": "属性式"}


def test_parse_xmp_metadata_invalid_xml_returns_empty() -> None:
    assert parse_xmp_metadata("not xml at all <<<") == {}
