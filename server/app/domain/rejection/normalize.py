"""废标项检查的纯函数层（无 I/O、不 import 框架——spec §4.1 的 domain 纪律）。

逐字移植 `client/electron/services/rejectionCheckStore.cjs:82-160` 与
`initialState`（:16-33）里的取值语义：状态归一、三引号包裹剥除、稳定哈希、
文档签名与「废标项检查输入签名」。**签名分隔符逐字照抄**——它们是哈希输入的一部分，
换一个字符就会让幂等判据与参考不一致。
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

# 参考 :16-33 `initialState`
DEFAULT_CHECK_OPTIONS: dict[str, bool] = {
    "rejectionCheck": True,
    "typoCheck": True,
    "logicCheck": True,
    "subjectCheck": True,
    "subjectSealCheck": True,
}
TENDER_DOCUMENT_ID = "tender"  # 参考 :47
STEP_VALUES = ("documents", "items", "results")
SECTION_STATUSES = ("idle", "running", "success", "error")

# 参考 :129/:143/:158 的分隔符（逐字）
DOCUMENT_SIGNATURE_SEPARATOR = "\n---yibiao-rejection-signature---\n"
BID_DOCUMENT_SEPARATOR = "\n---yibiao-rejection-bid-document---\n"
CHECK_INPUT_SEPARATOR = "\n---yibiao-rejection-check-input---\n"
BID_SIGNATURE_SEPARATOR = "\n---yibiao-rejection-bid-signature---\n"  # 参考 :1311


def stable_hash(content: Any) -> str:
    """参考 :82-84：sha256(utf8) 十六进制（**不截断**；截断由调用方做）。"""
    return hashlib.sha256(str(content or "").encode("utf-8")).hexdigest()


def strip_triple_quote_wrapper(content: Any) -> str:
    """参考 :122-128：整串被 ```'''``` 包裹时剥掉，否则**原样返回**（不 trim）。

    注意与 `str.strip()` 的差别：只有「首尾都是三引号」才剥，且剥后 trim；
    未命中的分支返回的是**未 trim 的原值**——逐字对齐，别顺手改成统一 trim。
    """
    trimmed = str(content or "").strip()
    if trimmed.startswith("'''") and trimmed.endswith("'''"):
        return trimmed[3:-3].strip()
    return str(content or "")


def normalize_status(value: Any, allowed: Sequence[str], fallback: str) -> str:
    return value if value in allowed else fallback


def normalize_step(value: Any) -> str:
    return value if value in ("items", "results") else "documents"


def normalize_document_role(value: Any) -> str:
    return "bid" if value == "bid" else "tender"


def normalize_document_tab(value: Any) -> str:
    return str(value or "").strip() or "tender"


def normalize_result_tab(value: Any) -> str:
    return "custom" if value == "custom" else "analysis"


def normalize_check_result_tab(value: Any) -> str:
    return value if value in ("rejection", "typo", "logic", "subject") else "rejection"


def normalize_check_options(options: Mapping[str, Any] | None) -> dict[str, bool]:
    """参考 :111-120：废标项恒 true；subjectSealCheck 依赖 subjectCheck。"""
    source = options or {}
    subject_check = source.get("subjectCheck") is not False
    return {
        "rejectionCheck": True,
        "typoCheck": source.get("typoCheck") is not False,
        "logicCheck": source.get("logicCheck") is not False,
        "subjectCheck": subject_check,
        "subjectSealCheck": subject_check and source.get("subjectSealCheck") is not False,
    }


def _get(document: Mapping[str, Any], camel: str, snake: str) -> Any:
    """参考的文档对象是 camelCase（IPC 形状）；服务端来自 DB 行时是 snake_case。

    两种都认（camelCase 优先），免得签名随调用来源漂移。
    """
    if camel in document:
        return document[camel]
    return document.get(snake)


def create_document_signature(document: Mapping[str, Any] | None) -> str:
    """参考 :130-142（含 `bid-1` → `bid` 的特例，逐字保留）。"""
    if not document:
        return ""
    content = str(_get(document, "content", "content") or "").strip()
    raw_id = _get(document, "id", "document_id")
    signature_id = ("bid" if _get(document, "role", "role") == "bid" and raw_id == "bid-1"
                    else raw_id or _get(document, "role", "role"))
    return DOCUMENT_SIGNATURE_SEPARATOR.join([
        str(signature_id),
        str(_get(document, "source", "source")),
        str(_get(document, "fileName", "file_name")),
        str(len(content)),
        content[:800],
        content[-800:],
    ])


def create_rejection_check_input_signature(
    bid_documents: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None,
    invalid_bid_and_rejection_items: Any,
    custom_check_items: Any,
) -> str:
    """参考 :144-159：任一段缺失（无投标文件 / 无解析内容）→ **空串**。"""
    if isinstance(bid_documents, Mapping) or bid_documents is None:
        documents = [bid_documents] if bid_documents else []
    else:
        documents = list(bid_documents)
    bid_signature = BID_DOCUMENT_SEPARATOR.join(
        signature for signature in (create_document_signature(item) for item in documents)
        if signature
    )
    analysis = str(invalid_bid_and_rejection_items or "").strip()
    if not bid_signature or not analysis:
        return ""
    custom = str(custom_check_items or "").strip()
    return CHECK_INPUT_SEPARATOR.join([
        bid_signature,
        str(len(analysis)),
        analysis[:800],
        analysis[-800:],
        str(len(custom)),
        custom[:800],
        custom[-800:],
    ])


def create_bid_document_id(file_name: Any, markdown: Any) -> str:
    """参考 :216-219：`bid-<sha256 前 16>`。"""
    digest = stable_hash(f"{file_name or ''}\n{markdown or ''}")[:16]
    return f"bid-{digest}"


def create_bid_signature(documents: Sequence[Mapping[str, Any]] | None) -> str:
    """投标文件集合签名（参考 `getCurrentBidSignature` :1308-1312）：

    逐份 `create_document_signature` → 去空 → 用 `BID_SIGNATURE_SEPARATOR` 连接。
    主体确认时写进档案（`bid_signature`），主体检查的幂等判据依赖它。
    """
    signatures = [signature for signature in
                  (create_document_signature(document) for document in (documents or []))
                  if signature]
    return BID_SIGNATURE_SEPARATOR.join(signatures)


def create_tender_source_document_id(file_name: Any, markdown: Any, index: int) -> str:
    """参考 :221-224：`tender-<两位序号>-<sha256 前 12>`。"""
    digest = stable_hash(f"{file_name or ''}\n{markdown or ''}")[:12]
    return f"tender-{index + 1:02d}-{digest}"


def combine_tender_markdown(documents: Sequence[Mapping[str, Any]] | None) -> str:
    """参考 :226-231：逐份取 content（兼容 `file_content`）→ trim → 非空 → `\\n\\n` 连接。"""
    parts = []
    for document in documents or []:
        value = _get(document, "file_content", "file_content")
        if value in (None, ""):
            value = _get(document, "content", "content")
        text = str(value or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)
