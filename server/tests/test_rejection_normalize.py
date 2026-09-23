"""P4a T2a：废标项检查 domain 纯函数（逐字对齐 rejectionCheckStore.cjs:82-160）。"""

from __future__ import annotations

import hashlib

from app.domain.rejection.normalize import (
    BID_DOCUMENT_SEPARATOR,
    CHECK_INPUT_SEPARATOR,
    DEFAULT_CHECK_OPTIONS,
    DOCUMENT_SIGNATURE_SEPARATOR,
    combine_tender_markdown,
    create_bid_document_id,
    create_document_signature,
    create_rejection_check_input_signature,
    create_tender_source_document_id,
    normalize_check_options,
    normalize_check_result_tab,
    normalize_document_role,
    normalize_document_tab,
    normalize_result_tab,
    normalize_step,
    stable_hash,
    strip_triple_quote_wrapper,
)


def test_stable_hash_is_full_sha256_hex():
    assert stable_hash("正文") == hashlib.sha256("正文".encode("utf-8")).hexdigest()
    assert stable_hash(None) == hashlib.sha256(b"").hexdigest()


def test_strip_triple_quote_wrapper_semantics():
    assert strip_triple_quote_wrapper("'''内容'''") == "内容"
    assert strip_triple_quote_wrapper("  '''内容'''  ") == "内容"
    # 未命中分支返回**原值**（不 trim）——与参考 :127 逐字一致
    assert strip_triple_quote_wrapper("  内容  ") == "  内容  "
    assert strip_triple_quote_wrapper("'''只有开头") == "'''只有开头"


def test_normalizers_match_reference_defaults():
    assert normalize_step("items") == "items"
    assert normalize_step("results") == "results"
    assert normalize_step("whatever") == "documents"
    assert normalize_document_role("bid") == "bid"
    assert normalize_document_role("tender") == "tender"
    assert normalize_document_role(None) == "tender"
    assert normalize_document_tab("  ") == "tender"
    assert normalize_result_tab("custom") == "custom"
    assert normalize_result_tab("analysis") == "analysis"
    assert normalize_result_tab("nope") == "analysis"
    assert normalize_check_result_tab("typo") == "typo"
    assert normalize_check_result_tab("nope") == "rejection"


def test_normalize_check_options_subject_seal_depends_on_subject():
    assert normalize_check_options(None) == DEFAULT_CHECK_OPTIONS
    # 显式关掉主体检查 → 印章检查必须跟着关（参考 :115-119）
    options = normalize_check_options({"subjectCheck": False, "subjectSealCheck": True})
    assert options["subjectCheck"] is False
    assert options["subjectSealCheck"] is False
    # 废标项恒 true，不受传参影响
    assert normalize_check_options({"rejectionCheck": False})["rejectionCheck"] is True
    # 只关错别字
    assert normalize_check_options({"typoCheck": False}) == {
        **DEFAULT_CHECK_OPTIONS, "typoCheck": False}


def test_create_bid_document_id_shape():
    document_id = create_bid_document_id("投标文件.docx", "正文")
    assert document_id.startswith("bid-")
    assert len(document_id) == len("bid-") + 16


def test_create_tender_source_document_id_shape():
    assert create_tender_source_document_id("招标.pdf", "正文", 0).startswith("tender-01-")
    assert create_tender_source_document_id("招标.pdf", "正文", 9).startswith("tender-10-")


def test_combine_tender_markdown_prefers_file_content_and_skips_blanks():
    combined = combine_tender_markdown([
        {"file_content": " A "},
        {"content": "B"},
        {"content": "   "},
        {"file_content": "C"},
    ])
    assert combined == "A\n\nB\n\nC"
    assert combine_tender_markdown(None) == ""


def test_document_signature_is_verbatim_and_handles_bid_1_special_case():
    signature = create_document_signature({
        "id": "bid-1", "role": "bid", "source": "upload",
        "fileName": "投标.docx", "content": " 正文 ",
    })
    # 参考 :132 的特例：bid + id='bid-1' → 签名里用 'bid'
    assert signature.split(DOCUMENT_SIGNATURE_SEPARATOR)[0] == "bid"
    fields = signature.split(DOCUMENT_SIGNATURE_SEPARATOR)
    assert fields[1:4] == ["upload", "投标.docx", "2"]  # content 已 trim → 长度 2
    assert fields[4] == "正文" and fields[5] == "正文"


def test_document_signature_accepts_snake_case_rows():
    """服务端拿到的是 DB 行（snake_case），签名必须与 camelCase 输入一致。"""
    camel = create_document_signature({
        "id": "bid-2", "role": "bid", "source": "upload",
        "fileName": "投标.docx", "content": "正文"})
    snake = create_document_signature({
        "document_id": "bid-2", "role": "bid", "source": "upload",
        "file_name": "投标.docx", "content": "正文"})
    assert camel == snake


def test_document_signature_empty_document():
    assert create_document_signature(None) == ""
    assert create_document_signature({}) == ""


def test_check_input_signature_requires_both_parts():
    bid = [{"document_id": "bid-2", "role": "bid", "source": "upload",
            "file_name": "投标.docx", "content": "正文"}]
    # 缺解析内容 → 空串（参考 :150-151）
    assert create_rejection_check_input_signature(bid, "", "") == ""
    # 缺投标文件 → 空串
    assert create_rejection_check_input_signature([], "解析内容", "") == ""
    signature = create_rejection_check_input_signature(bid, " 解析内容 ", "自定义项")
    assert signature
    assert CHECK_INPUT_SEPARATOR in signature
    # 单文档时 `join(分隔符)` 不产生投标文件间分隔符——只有多文档才出现
    assert BID_DOCUMENT_SEPARATOR not in signature
    assert signature.startswith(create_document_signature(bid[0]))
    second = {"document_id": "bid-3", "role": "bid", "source": "upload",
              "file_name": "投标2.docx", "content": "正文二"}
    multi = create_rejection_check_input_signature([bid[0], second], "解析内容", "")
    assert BID_DOCUMENT_SEPARATOR in multi
    assert multi.startswith(
        create_document_signature(bid[0]) + BID_DOCUMENT_SEPARATOR
        + create_document_signature(second))
    # 单文档（非列表）也接受（参考 :145 的等价处理）
    assert create_rejection_check_input_signature(bid[0], "解析内容", "") == \
        create_rejection_check_input_signature(bid, "解析内容", "")
