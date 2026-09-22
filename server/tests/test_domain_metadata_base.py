"""P3d T1 —— 元数据基座纯函数（metadata_base + js_date）的 oracle 对拍测试。

断言值唯一来源：`.superpowers/sdd/2026-09-22-p3d-metadata/oracle-base.out.json`
（参考实现 `client/electron/services/duplicateCheckService.cjs` 基座函数的本机
Node 直跑真值；对拍脚本同目录 `oracle-base.cjs`）。

两条**已登记偏离**（见 metadata_base.py / js_date.py 模块 docstring）以专用
用例显式文档化，用例内并列注明 oracle 真值：

- 偏离①：1~3 位纯数字（如 "600"）不按日期解析（V8 legacy 会）；
- 偏离②：`unique_sorted_values` 排序键近似（Python 无 ICU；汉字按码点，
  oracle zh-CN 按拼音且整体排在拉丁之前）。

本地时区语义样本（V8 legacy 分支：斜杠/非补零/空格分隔 → 本地时区）的期望值
= 本机（GMT+8）oracle 实测；跨时区机器需重跑 oracle-base.cjs。
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone

from app.domain.duplicate.js_date import js_date_parse, to_iso_z
from app.domain.duplicate.metadata_base import (
    add_decoded_base64_fields,
    add_field,
    add_field_if_absent,
    add_list_field,
    count_matches,
    decode_xml,
    format_docx_total_time,
    format_metadata_key,
    get_metadata_label,
    hash_text,
    is_comparable_key,
    is_date_comparable_key,
    metadata_items,
    normalize_comparable,
    normalize_date_day,
    normalize_value,
    safe_metadata_key,
    should_skip_base64_decode,
    summarize_values,
    try_decode_base64_text,
    unique_sorted_values,
    xml_text,
    yes_no,
)


def _b64(text: str) -> str:
    """oracle 脚本里的 `b64`：Buffer.from(s,'utf8').toString('base64')。"""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


# ---------------------------------------------------------------------------
# normalizeValue（svc.cjs :133-143）
# ---------------------------------------------------------------------------


def test_normalize_value_js_coercion_and_normalization():
    # None / undefined → ""
    assert normalize_value(None) == ""
    # String(600) → "600"；String(0) → "0"；String(1.5) → "1.5"
    assert normalize_value(600) == "600"
    assert normalize_value(0) == "0"
    assert normalize_value(1.5) == "1.5"
    # String(true/false) → "true"/"false"
    assert normalize_value(True) == "true"
    assert normalize_value(False) == "false"
    # 空白折叠 + strip；\t 属控制符区间被剥（不是折成空格）
    assert normalize_value("  你好  世界\t\t第二行  ") == "你好 世界第二行"
    # 控制符批量剥除；\ufeeff = U+FEEF + 'f'，NFKC(U+FEEF) = ى
    assert normalize_value("a\u0000b\u0007c\u007fd\u009fe\u200bf\u202eg\ufeeff") == "abcdefgىf"
    # NFKC：全角字母数字 + 全角括号
    assert normalize_value("ＡＢＣ１２３（一）") == "ABC123(一)"
    # 圈号 → 数字
    assert normalize_value("⑨") == "9"
    # dict → JSON.stringify（插入序、紧凑、不转义非 ASCII）
    assert normalize_value({"b": 1, "a": "x", "中文": "值"}) == '{"b":1,"a":"x","中文":"值"}'
    assert normalize_value({"k": [1, "二", None]}) == '{"k":[1,"二",null]}'
    # list → 递归归一化后 `；` 连接（滤空）
    assert normalize_value(["甲", "", "乙", "甲"]) == "甲；乙；甲"
    assert normalize_value(["", None]) == ""


def test_normalize_value_datetime_extension():
    """Python 侧值域扩展（参考的 `value instanceof Date` 分支）：datetime →
    toISOString。参考值域（Node）不会出现 naive，这里约定按 UTC。"""
    moment = datetime(2024, 1, 15, 8, 0, 0, tzinfo=timezone.utc)
    assert normalize_value(moment) == "2024-01-15T08:00:00.000Z"
    assert normalize_value(datetime(2024, 1, 15, 8, 0, 0)) == "2024-01-15T08:00:00.000Z"


# ---------------------------------------------------------------------------
# normalizeComparable / normalizeDateDay（svc.cjs :145-160）
# ---------------------------------------------------------------------------


def test_normalize_comparable_dates_utc_and_explicit_tz():
    # 时区无关样本（纯日期 → UTC；显式时区 → 换算）——oracle 全量真值
    assert normalize_comparable("2024-01-15T08:00:00.000Z") == "2024-01-15T08:00:00.000Z"
    assert normalize_comparable("2024-01-15T08:00:00Z") == "2024-01-15T08:00:00.000Z"
    assert normalize_comparable("2024-01-15") == "2024-01-15T00:00:00.000Z"
    assert normalize_comparable("2024") == "2024-01-01T00:00:00.000Z"
    assert normalize_comparable("2024-01") == "2024-01-01T00:00:00.000Z"
    assert normalize_comparable("2024-01-15T08:00:00.000+08:00") == "2024-01-15T00:00:00.000Z"
    # 非日期 → lower + 紧凑剥空白
    assert normalize_comparable("20240115") == "20240115"
    assert normalize_comparable("D:20240115080000") == "d:20240115080000"
    assert normalize_comparable("D:20240115080000+08'00'") == "d:20240115080000+08'00'"
    assert normalize_comparable("2024年1月5日") == "2024年1月5日"
    assert normalize_comparable("2024年01月05日 08:00") == "2024年01月05日08:00"
    assert normalize_comparable("2024-13-45") == "2024-13-45"
    assert normalize_comparable("not a date") == "notadate"
    # 空值清单 → ""
    for text in ("没有提及", "原文未提及", "-", "无", "null", "undefined", ""):
        assert normalize_comparable(text) == ""


def test_normalize_comparable_local_tz_samples():
    """V8 legacy 分支（带时间无时区 / 空格 / 斜杠 / 非补零 → **本地时区**）。

    期望值 = 本机（GMT+8）oracle 实测：本地 08:00 → UTC 前一日 16:00。
    """
    assert normalize_comparable("2024-01-15T08:00:00") == "2024-01-15T00:00:00.000Z"
    assert normalize_comparable("2024/01/15") == "2024-01-14T16:00:00.000Z"
    assert normalize_comparable("2024/1/5") == "2024-01-04T16:00:00.000Z"
    assert normalize_comparable("2024-1-5") == "2024-01-04T16:00:00.000Z"
    assert normalize_comparable("2024-01-15 08:00:00") == "2024-01-15T00:00:00.000Z"


def test_deviation_small_numbers_not_parsed_as_dates():
    """3 位纯数字按 V8 legacy 解析（T5 推翻了 T1 的「视为非日期」偏离）。

    oracle（T5 重取）：normalizeComparable("600") = "0599-12-31T15:54:17.000Z"
    （V8 legacy 年直取、本地时区 LMT +08:05:43）。本实现走 datetime 现路径，
    Windows CRT 对远古年份 astimezone 抛 OSError → 兜底 UTC，秒差 5:43——
    spec §15 登记项（真实值域 code_page='936' 等同款）。date_day 与
    normalized 同源。
    """
    assert normalize_comparable("600") == "0600-01-01T00:00:00.000Z"
    assert normalize_date_day("600") == "0600-01-01"


def test_normalize_date_day_samples():
    # 日期解析成功路径 → toISOString[:10]
    assert normalize_date_day("2024-01-15T08:00:00.000Z") == "2024-01-15"
    assert normalize_date_day("2024-01-15T08:00:00Z") == "2024-01-15"
    assert normalize_date_day("2024-01-15") == "2024-01-15"
    assert normalize_date_day("2024") == "2024-01-01"
    assert normalize_date_day("2024-01") == "2024-01-01"
    assert normalize_date_day("2024-01-15T08:00:00.000+08:00") == "2024-01-15"
    # 正则分支（非日期解析路径）——保留原始位数，分隔符统一为 -
    assert normalize_date_day("2024年1月5日") == "2024-1-5"
    assert normalize_date_day("2024年01月05日 08:00") == "2024-01-05"
    assert normalize_date_day("签字日期2024.2.6前后") == "2024-2-6"
    assert normalize_date_day("见 2024.2.6 页") == "2024-2-6"
    assert normalize_date_day("2024-13-45") == "2024-13-45"
    # 非日期 → ""
    assert normalize_date_day("20240115") == ""
    assert normalize_date_day("D:20240115080000") == ""
    assert normalize_date_day("D:20240115080000+08'00'") == ""
    assert normalize_date_day("not a date") == ""
    for text in ("没有提及", "原文未提及", "-", "无", "null", "undefined", ""):
        assert normalize_date_day(text) == ""


def test_normalize_date_day_local_tz_samples():
    """本地时区样本（GMT+8 oracle 实测，见 test_normalize_comparable_local_tz_samples）。"""
    assert normalize_date_day("2024-01-15T08:00:00") == "2024-01-15"
    assert normalize_date_day("2024/01/15") == "2024-01-14"
    assert normalize_date_day("2024/1/5") == "2024-01-04"
    assert normalize_date_day("2024-1-5") == "2024-01-04"
    assert normalize_date_day("2024-01-15 08:00:00") == "2024-01-15"
    assert normalize_date_day("2024/12/31 23:59") == "2024-12-31"


# ---------------------------------------------------------------------------
# safeMetadataKey / formatMetadataKey / getMetadataLabel（svc.cjs :185-209）
# ---------------------------------------------------------------------------


def test_safe_metadata_key_samples():
    assert safe_metadata_key("Hello World!") == "hello_world"
    assert safe_metadata_key("pdf_info:Creator") == "pdf_info_creator"
    # 全角括号经 NFKC → 半角 → 剥成 _，再去尾 _
    assert safe_metadata_key("custom:My Key（定制）") == "custom_my_key_定制"
    assert safe_metadata_key("converted_docx:title") == "converted_docx_title"
    assert safe_metadata_key("custom:标记:base64_decoded") == "custom_标记_base64_decoded"
    assert safe_metadata_key("x:base64_decoded") == "x_base64_decoded"
    assert safe_metadata_key("custom:") == "custom"
    # \x05 控制符被 normalizeValue 剥除
    assert safe_metadata_key("\x05SummaryInformation") == "summaryinformation"
    assert safe_metadata_key("ole_signal:path/to/stream") == "ole_signal_path_to_stream"
    assert safe_metadata_key("wps:作者") == "wps_作者"
    assert safe_metadata_key("　ｆｕｌｌ－ｗｉｄｔｈ　") == "full_width"
    assert safe_metadata_key("") == "field"
    assert safe_metadata_key("___") == "field"
    assert safe_metadata_key("标题123") == "标题123"


def test_safe_metadata_key_truncates_to_80():
    """80 字符截断（svc.cjs :190 `key[:80] || 'field'`）——T1 探针靶点。"""
    assert safe_metadata_key("A" * 100) == "a" * 80


def test_format_metadata_key_samples():
    assert format_metadata_key("pdf_info_creator") == "pdf info creator"
    assert format_metadata_key("custom_my_key") == "custom my key"
    assert format_metadata_key("a_b__c") == "a b c"
    assert format_metadata_key("plain") == "plain"
    assert format_metadata_key("") == ""


def test_metadata_label_samples():
    assert get_metadata_label("title") == "标题"
    assert get_metadata_label("file_sha256") == "原始文件 SHA256"
    assert get_metadata_label("word_rsid_root") == "Word 编辑会话根 ID"
    assert get_metadata_label("pdf_trailer_id") == "PDF Trailer ID"
    # 前缀分支的判定顺序即语义（先 custom:+base64_decoded，再普通 base64 尾缀递归）
    assert get_metadata_label("converted_docx:title") == "转换 DOCX：标题"
    assert get_metadata_label("custom:标记:base64_decoded") == "自定义：标记（Base64 解码）"
    assert get_metadata_label("custom:标记") == "自定义：标记"
    assert get_metadata_label("created:base64_decoded") == "创建时间（Base64 解码）"
    assert get_metadata_label("pdf_info:Creator") == "PDF Info：Creator"
    assert get_metadata_label("pdf_xmp:dc_title") == "PDF XMP：dc title"
    assert get_metadata_label("pdf_raw:Title") == "PDF 原始记录：Title"
    assert get_metadata_label("ole_signal:Some_Stream") == "OLE 疑似痕迹：Some Stream"
    assert get_metadata_label("wps:author") == "疑似 WPS 用户/账号：author"
    assert get_metadata_label("some_unknown_key") == "some unknown key"
    assert get_metadata_label("ole_prop_77") == "ole prop 77"


# ---------------------------------------------------------------------------
# isComparableKey / isDateComparableKey（svc.cjs :211-228）
# ---------------------------------------------------------------------------

_IS_COMPARABLE = {
    "title": True, "last_modified_by": True, "lastmodifiedby": False,
    "created": True, "modified": True, "last_printed": True, "size": False,
    "file_name": False, "extension": False, "total_time": True,
    "pdf_info:CreationDate": True, "pdf_xmp:createdate": True,
    "pdf_raw:created": True, "custom:foo": True, "converted_docx:created": True,
    "ole_signal:x": True, "ole_prop_12": False, "wps:created": True,
    "word_rsid_root": True, "pdf_trailer_id": True, "fingerprints": True,
    "pdf_permissions": True, "metadata_error": False, "created_by": True,
    "modifiedby": False, "x_created_x": True,
}
_IS_DATE_COMPARABLE = {
    "title": False, "last_modified_by": False, "lastmodifiedby": False,
    "created": True, "modified": True, "last_printed": True, "size": False,
    "file_name": False, "extension": False, "total_time": False,
    "pdf_info:CreationDate": True, "pdf_xmp:createdate": True,
    "pdf_raw:created": True, "custom:foo": False, "converted_docx:created": True,
    "ole_signal:x": False, "ole_prop_12": False, "wps:created": True,
    "word_rsid_root": False, "pdf_trailer_id": False, "fingerprints": False,
    "pdf_permissions": False, "metadata_error": False, "created_by": True,
    "modifiedby": False, "x_created_x": True,
}


def test_is_comparable_and_is_date_comparable_tables():
    for key, expected in _IS_COMPARABLE.items():
        assert is_comparable_key(key) is expected, f"comparable[{key}]"
    for key, expected in _IS_DATE_COMPARABLE.items():
        assert is_date_comparable_key(key) is expected, f"date_comparable[{key}]"


# ---------------------------------------------------------------------------
# base64 解码（svc.cjs :230-259）
# ---------------------------------------------------------------------------


def test_try_decode_base64_text():
    assert try_decode_base64_text(_b64("hello world")) == "hello world"
    # JSON 载荷 → JSON.stringify 复原（紧凑、不转义非 ASCII）
    assert try_decode_base64_text(_b64('{"k":1,"中文":"值"}')) == '{"k":1,"中文":"值"}'
    # 长度门槛（<12）→ ""
    assert try_decode_base64_text(_b64("hi")) == ""
    # 二次编码：解码结果本身长得像 base64，也原样返回（oracle "identity"）
    assert try_decode_base64_text(_b64("aGVsbG8=")) == "aGVsbG8="
    # 非 base64 字符集 → ""
    assert try_decode_base64_text("这不是 base64!!!") == ""
    # 解码含控制符 → ""
    assert try_decode_base64_text(_b64("a\u0001b")) == ""
    assert try_decode_base64_text("") == ""


def test_should_skip_base64_decode():
    assert should_skip_base64_decode("pdf_raw:sha256_hash", "x") is True
    assert should_skip_base64_decode("title", "x") is False
    assert should_skip_base64_decode("title", "d41d8cd98f00b204e9800998ecf8427e") is True
    assert should_skip_base64_decode("title", "a" * 63) is False


def test_add_decoded_base64_fields_snapshot_and_tail_append():
    fields: dict[str, str] = {}
    add_field(fields, "title", _b64('{"a":1}'))
    add_field(fields, "file_sha256", _b64('{"b":2}'))
    add_field(fields, "custom:blob", _b64("plain text here"))
    add_field(fields, "x:base64_decoded", _b64('{"c":3}'))
    add_decoded_base64_fields(fields)
    # sha 键跳过、已解码键跳过；新键按快照序追加在尾部
    assert list(fields.items()) == [
        ("title", "eyJhIjoxfQ=="),
        ("file_sha256", "eyJiIjoyfQ=="),
        ("custom:blob", "cGxhaW4gdGV4dCBoZXJl"),
        ("x:base64_decoded", "eyJjIjozfQ=="),
        ("title:base64_decoded", '{"a":1}'),
        ("custom:blob:base64_decoded", "plain text here"),
    ]


# ---------------------------------------------------------------------------
# 通用小工具（svc.cjs :266-322）
# ---------------------------------------------------------------------------


def test_hash_text_public_vectors():
    # sha256 空串与 "abc" 的公开测试向量（与 Node crypto.createHash('sha256') 同值）
    assert hash_text(None) == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
    assert hash_text("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    # JS `value || ''`：0 视为空
    assert hash_text(0) == hash_text(None)


def test_yes_no_and_count_matches():
    assert yes_no(1) == "是"
    assert yes_no(0) == "否"
    assert yes_no("") == "否"
    obj_re = re.compile(r"(?:^|[\r\n])\s*\d+\s+\d+\s+obj\b")
    assert count_matches("1 0 obj\n2 0 obj\nendobj", obj_re) == 2
    # 全局非重叠计数
    assert count_matches("aaaa", re.compile("aa")) == 2


def test_unique_sorted_values_and_summarize():
    # 非数组 → []
    assert unique_sorted_values("not-array") == []
    # 偏离②用例（见模块 docstring）：oracle（zh-CN ICU）= ["甲","乙","alpha",
    # "Alpha","SummaryInformation"]——汉字按拼音且整体排在拉丁之前；Python 无
    # ICU，码点序恰好相反，这里按本实现序断言并登记。
    mixed = ["乙", "alpha", "甲", "Alpha", "\x05SummaryInformation", "alpha"]
    assert unique_sorted_values(mixed) == [
        "alpha", "Alpha", "SummaryInformation", "乙", "甲"]
    # summarizeValues 形状（与 oracle 一致的样本）
    assert summarize_values(["v1", "v2", "v3"], 2) == "v1；v2；...共3项"
    assert summarize_values(["值1", "值2", "值3"], 2) == "值1；值2；...共3项"
    assert summarize_values(["a", "b"], 2) == "a；b"
    # 偏离②：oracle "甲；乙；alpha"，本实现序
    assert summarize_values(["乙", "alpha", "甲"]) == "alpha；乙；甲"


def test_xml_text_and_decode_xml():
    assert xml_text("<dc:title>Hello</dc:title>", "title") == "Hello"
    assert xml_text("<dc:creator>张三</dc:creator>", "creator") == "张三"
    assert xml_text("<title><![CDATA[<b>加粗</b>]]></title>", "title") == "<b>加粗</b>"
    assert xml_text("<title>a&lt;b&gt;c&amp;d&quot;e&apos;f</title>", "title") == "a<b>c&d\"e'f"
    assert xml_text("<title/>", "title") == ""
    assert xml_text("<other>x</other>", "title") == ""
    # &amp; 必须最后替换：decodeXml("&amp;lt;") = "&lt;"（不是 "<"）
    assert decode_xml("&amp;lt;") == "&lt;"
    # 标签名带换行属性、大小写不敏感
    assert xml_text("<cp:lastModifiedBy\n  x=\"1\">李四</cp:lastModifiedBy>",
                    "lastModifiedBy") == "李四"


def test_format_docx_total_time():
    assert format_docx_total_time("120") == "120 分钟"
    assert format_docx_total_time("abc") == "abc"
    assert format_docx_total_time("") == ""
    assert format_docx_total_time("0") == "0 分钟"


# ---------------------------------------------------------------------------
# js_date 直测（oracle "600" 真值的年份补零形态）
# ---------------------------------------------------------------------------


def test_to_iso_z_pads_year_below_1000():
    # oracle：normalizeComparable("600") = "0599-12-31T15:54:17.000Z"——
    # V8 把 "600" 解出的时刻就是它；钉住 to_iso_z 的 4 位年份补零
    moment = datetime(599, 12, 31, 15, 54, 17, tzinfo=timezone.utc)
    assert to_iso_z(moment) == "0599-12-31T15:54:17.000Z"


def test_js_date_parse_rejects_out_of_range():
    assert js_date_parse("") is None
    assert js_date_parse("2024-13-01") is None
    assert js_date_parse("2024-13-45") is None


# ---------------------------------------------------------------------------
# items 映射（svc.cjs :1085-1093）与 addField 三兄弟的 Map 语义
# ---------------------------------------------------------------------------


def test_metadata_items_mapping():
    fields: dict[str, str] = {}
    add_field(fields, "file_name", "标书A.docx")
    add_field(fields, "title", "  投  标  文件  ")
    add_field(fields, "created", "D:20240115080000+08'00'")
    add_field(fields, "created_at", "2024-01-15T08:00:00.000Z")
    add_field(fields, "size", 600)
    add_field(fields, "unknown_key", "x")
    add_field(fields, "author", "没有提及")
    add_field(fields, "metadata_error", "OLE 元数据读取失败：boom")

    items = metadata_items(fields)
    assert [item["key"] for item in items] == [
        "file_name", "title", "created", "created_at", "size",
        "unknown_key", "author", "metadata_error"]
    by_key = {item["key"]: item for item in items}
    # label / value（value 是 normalizeValue 后的文本；全角冒号 NFKC → 半角）
    assert by_key["file_name"]["label"] == "文件名"
    assert by_key["file_name"]["value"] == "标书A.docx"
    assert by_key["title"]["value"] == "投 标 文件"
    assert by_key["title"]["normalized"] == "投标文件"
    assert by_key["created"]["normalized"] == "d:20240115080000+08'00'"
    assert by_key["created"]["date_day"] == ""
    assert by_key["created_at"]["normalized"] == "2024-01-15T08:00:00.000Z"
    assert by_key["created_at"]["date_day"] == "2024-01-15"
    assert by_key["unknown_key"]["label"] == "unknown key"
    assert by_key["author"]["normalized"] == ""
    assert by_key["metadata_error"]["value"] == "OLE 元数据读取失败:boom"
    assert by_key["metadata_error"]["normalized"] == "ole元数据读取失败:boom"
    # comparable / date_comparable 旗标
    assert by_key["file_name"]["comparable"] is False
    assert by_key["file_name"]["date_comparable"] is False
    assert by_key["title"]["comparable"] is True
    assert by_key["title"]["date_comparable"] is False
    assert by_key["created"]["comparable"] is True
    assert by_key["created"]["date_comparable"] is True
    assert by_key["created_at"]["comparable"] is True
    assert by_key["created_at"]["date_comparable"] is True
    assert by_key["size"]["comparable"] is False
    assert by_key["unknown_key"]["comparable"] is False
    assert by_key["author"]["comparable"] is True
    assert by_key["author"]["date_comparable"] is False
    assert by_key["metadata_error"]["comparable"] is False
    # 3 位年直取落在 items 上：size 的 normalized/date_day（oracle：
    # "0599-12-31T15:54:17.000Z" / "0599-12-31"；本实现 UTC 兜底秒差 §15 登记）
    assert by_key["size"]["value"] == "600"
    assert by_key["size"]["normalized"] == "0600-01-01T00:00:00.000Z"
    assert by_key["size"]["date_day"] == "0600-01-01"


def test_add_field_map_semantics():
    fields: dict[str, str] = {}
    add_field(fields, "k", "v1")
    add_field(fields, "k", "v2")            # 覆盖（Map.set）
    add_field(fields, "empty", "  ")        # 空白 → 不写
    add_field_if_absent(fields, "k", "v3")  # 已有 → 跳过
    add_field_if_absent(fields, "absent", "")  # 空 → 不写
    add_list_field(fields, "list", "甲")
    add_list_field(fields, "list", "甲")     # 重复 → 不加
    add_list_field(fields, "list", "乙")
    add_list_field(fields, "list", "  乙  ")  # 归一后重复 → 不加
    assert list(fields.items()) == [("k", "v2"), ("list", "甲；乙")]
