"""废标检查结果 Excel 导出的工作簿 plan 构造（P4d T1）。

逐字对齐参考实现 `client/electron/services/checkResultExportService.cjs` 的
``buildRejectionWorkbook``（:217-334）及传递依赖（``getRejectionResultDescriptor``
:102-111、``selectCurrentRejectionResults`` :112-137、``getResultStatus``/``getResultMessage``
:182-204、``buildDocumentLookup``/``getDocumentColumns`` :205-215、
``buildUnrecognizedRows`` :84-100、``subjectVisionStatusLabels``/``subjectLocationKindLabels``
:12-29）。

**复用（R-P4d-1）**：``normalize_cell_text`` 一族、``_sheet_plan``/``SheetPlan``/
``DuplicateWorkbookPlan``/``normalize_rows`` 直接 import 自查重导出模块
（:30-84 与 :394-429 与废标项导出完全同构，不复制）；xlsx 组装器复用
``app/infra/exporting/duplicate_xlsx.write_duplicate_xlsx``（只依赖 plan 形状）。

**签名自持（同 R-P3d2-1）**：三份结果的 expected signature（rejectionInputSignature /
bidSignature / subjectInputSignature）由服务端按**当前输入**重算（与 run 端点同一
套计算），不经请求传入——「结果签名与当前检查输入不一致」即「结果已失效」。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from app.domain.duplicate.export_workbook import (
    DuplicateWorkbookPlan,
    SheetPlan,
    normalize_rows,
)

REJECTION_SHEET_NAMES = ["检查概览", "废标项", "错别字", "逻辑问题", "投标主体"]

SUBJECT_VISION_STATUS_LABELS = {
    "disabled": "未启用印章检查",
    "pending": "等待印章检查",
    "running": "正在检查印章",
    "success": "印章检查已完成",
    "partial": "印章检查部分完成",
    "unavailable": "当前文本模型未开启多模态支持，未执行印章检查",
    "error": "印章检查失败",
}

SUBJECT_LOCATION_KIND_LABELS = {
    "body": "正文",
    "table": "表格",
    "signature": "签署/落款",
    "standalone-image": "独立图片",
    "scanned-page": "整页扫描件",
}

CONFIRMED_CONFIDENCE_LABELS = {"confirmed-mismatch": "明确不一致",
                               "needs-review": "需人工复核"}


def get_rejection_result_descriptor(result: Any, expected_signature: Any) -> dict[str, Any]:
    """参考 :102-111：签名一致才 valid；invalid 时 findings 清空（不导出旧数据）。"""
    source = result if isinstance(result, Mapping) else {"status": "idle", "findings": []}
    valid = source.get("inputSignature") == expected_signature
    return {
        **source,
        "findings": (list(source.get("findings") or [])
                     if valid and isinstance(source.get("findings"), list) else []),
        "valid": valid,
    }


def select_current_rejection_results(state: Mapping[str, Any],
                                     signatures: Mapping[str, Any]) -> dict[str, Any]:
    """参考 :112-137：四结果签名判定 + exportable（任一 valid 且 success 或有行）。"""
    bid_documents = state.get("bidDocuments")
    selected: dict[str, Any] = {
        "bidDocuments": bid_documents if isinstance(bid_documents, list) else [],
        "rejectionCheckResult": get_rejection_result_descriptor(
            state.get("rejectionCheckResult"), signatures.get("rejectionInputSignature")),
        "typoCheckResult": get_rejection_result_descriptor(
            state.get("typoCheckResult"), signatures.get("bidSignature")),
        "logicCheckResult": get_rejection_result_descriptor(
            state.get("logicCheckResult"), signatures.get("bidSignature")),
        "subjectCheckResult": get_rejection_result_descriptor(
            state.get("subjectCheckResult"), signatures.get("subjectInputSignature")),
    }
    results = [selected["rejectionCheckResult"], selected["typoCheckResult"],
               selected["logicCheckResult"], selected["subjectCheckResult"]]
    selected["exportable"] = any(
        result["valid"] and (result.get("status") == "success"
                             or len(result["findings"]) > 0)
        for result in results)
    return selected


def get_result_status(descriptor: Mapping[str, Any], idle_status: str = "idle") -> str:
    """参考 :182-193：invalid（有签名）→ 结果已失效；success → 已完成；error → 失败。"""
    source = descriptor.get("source") or descriptor
    if not source:
        return "未执行"
    if not descriptor.get("valid"):
        return ("结果已失效" if source.get("inputSignature") or source.get("signature")
                else "未执行")
    if source.get("status") == "success":
        return "已完成"
    if source.get("status") == "error":
        return "失败"
    if source.get("status") in (idle_status, "pending"):
        return "未执行"
    return "未执行"


def get_result_message(descriptor: Mapping[str, Any], result_count: int,
                       empty_message: str) -> str:
    """参考 :194-204。"""
    source = descriptor.get("source") or descriptor
    if not source:
        return ""
    if not descriptor.get("valid"):
        return ("结果签名与当前检查输入不一致"
                if source.get("inputSignature") or source.get("signature") else "")
    if source.get("status") == "error":
        return (source.get("error") or source.get("message")
                or source.get("progressMessage") or "检查失败")
    if source.get("status") == "success" and result_count == 0:
        return empty_message
    return source.get("message") or source.get("progressMessage") or ""


def build_document_lookup(documents: Any, id_field: str = "id",
                          name_field: str = "fileName") -> dict[str, dict[str, Any]]:
    """参考 :205-210。"""
    return {document[id_field]: {"index": index + 1,
                                 "name": document.get(name_field) or ""}
            for index, document in enumerate(documents if isinstance(documents, list) else [])
            if document.get(id_field)}


def get_document_columns(lookup: Mapping[str, Mapping[str, Any]],
                         document_id: Any) -> list[Any]:
    """参考 :212-215：找不到文档 → 两个空串占位。"""
    document = lookup.get(document_id)
    return [document["index"], document["name"]] if document else ["", ""]


def build_unrecognized_rows(subject_check_result: Mapping[str, Any]) -> list[list[Any]]:
    """参考 :84-100：Excel「未能识别的图片」明细区块。"""
    if not subject_check_result.get("valid"):
        return []
    stats = subject_check_result.get("stats") or {}
    items = (stats.get("visionStats") or {}).get("unrecognized") \
        if isinstance(stats.get("visionStats"), Mapping) else None
    if not isinstance(items, list) or not items:
        return []
    rows: list[list[Any]] = [
        [],
        ["未能识别的图片"],
        ["章节/位置", "序号", "媒体文件", "格式", "原因"],
    ]
    for item in items:
        rows.append([
            item.get("sectionTitle") or item.get("locationHint") or "正文",
            f"第 {int(item.get('ordinal') or 0)} 张",
            item.get("mediaName") or "",
            item.get("label") or item.get("format") or "",
            item.get("reason") or "",
        ])
    return rows


def format_display_time(value: Any) -> str:
    """复用查重导出的显示时间格式（YYYY-MM-DD HH:MM:SS，本地墙钟）。"""
    from app.domain.duplicate.export_workbook import format_display_time as _impl

    return _impl(value)


def build_rejection_workbook_plan(state: Mapping[str, Any],
                                  signatures: Mapping[str, Any],
                                  exported_at: datetime) -> DuplicateWorkbookPlan:
    """参考 ``buildRejectionWorkbook`` :217-334（五工作表，列宽/autofilter 逐字）。"""
    selected = select_current_rejection_results(state, signatures)
    bid_names = "；".join(
        document.get("fileName") for document in selected["bidDocuments"]
        if document.get("fileName"))
    overview_definitions = [
        ["废标项检查", selected["rejectionCheckResult"]],
        ["错别字检查", selected["typoCheckResult"]],
        ["逻辑问题检查", selected["logicCheckResult"]],
        ["主体一致性检查", selected["subjectCheckResult"]],
    ]
    subject_result = selected["subjectCheckResult"]
    subject_stats = (subject_result.get("stats") or {}).get("visionStats") \
        if isinstance(subject_result.get("stats"), Mapping) else None
    overview_rows: list[list[Any]] = [
        ["导出时间", format_display_time(exported_at)],
        [],
        ["检查类型", "状态", "问题数量", "更新时间", "检查对象", "说明"],
        *[[label, get_result_status(result),
           len(result["findings"]) if result["valid"] else 0,
           result.get("updatedAt") or "",
           bid_names,
           get_result_message(result, len(result["findings"]),
                              "检查已完成，未发现问题")]
          for label, result in overview_definitions],
        [],
        ["主体检查统计项", "数值"],
        ["当前确认主体", str(state.get("subjectOfficialName") or "")],
        ["允许简称", "、".join(state.get("subjectAliases") or [])
         if isinstance(state.get("subjectAliases"), list) else ""],
        ["主体结果数量", len(subject_result["findings"]) if subject_result["valid"] else 0],
        ["印章检查状态", SUBJECT_VISION_STATUS_LABELS.get(
            (subject_result.get("stats") or {}).get("visionStatus")
            if isinstance(subject_result.get("stats"), Mapping) else None,
            "未执行")],
        ["视觉覆盖", (f"已检查 {subject_stats.get('inspectedAssets')}/"
                      f"{subject_stats.get('totalAssets')}，"
                      f"印章 {subject_stats.get('sealAssets')}，"
                      f"跳过 {subject_stats.get('skippedAssets')}，"
                      f"失败 {subject_stats.get('failedAssets')}")
         if isinstance(subject_stats, Mapping) else ""],
    ]

    document_lookup = build_document_lookup(selected["bidDocuments"])
    rejection_rows: list[list[Any]] = [
        ["序号", "投标文件序号", "投标文件名", "类型", "风险等级", "标题", "摘要",
         "招标要求", "投标证据", "风险原因", "修改建议"],
        *[[index + 1,
           *get_document_columns(document_lookup, finding.get("bidDocumentId")),
           "无效标" if finding.get("type") == "invalidBid" else "废标项",
           {"high": "高风险", "medium": "中风险", "low": "低风险"}.get(
               finding.get("severity"), ""),
           finding.get("title", ""), finding.get("summary", ""),
           finding.get("requirement", ""), finding.get("bidEvidence", ""),
           finding.get("riskReason", ""), finding.get("suggestion", "")]
          for index, finding in enumerate(selected["rejectionCheckResult"]["findings"])],
    ]

    typo_rows: list[list[Any]] = [
        ["序号", "投标文件序号", "投标文件名", "错误文本", "建议改正", "位置",
         "原文摘录", "判断原因"],
        *[[index + 1,
           *get_document_columns(document_lookup, finding.get("bidDocumentId")),
           finding.get("wrongText", ""), finding.get("correctText", ""),
           finding.get("locationHint", ""), finding.get("originalExcerpt", ""),
           finding.get("reason", "")]
          for index, finding in enumerate(selected["typoCheckResult"]["findings"])],
    ]

    logic_rows: list[list[Any]] = [
        ["序号", "投标文件序号", "投标文件名", "标题", "位置", "原文", "问题原因",
         "修改建议"],
        *[[index + 1,
           *get_document_columns(document_lookup, finding.get("bidDocumentId")),
           finding.get("title", ""), finding.get("locationHint", ""),
           finding.get("originalText", ""), finding.get("fallacyReason", ""),
           finding.get("suggestion", "")]
          for index, finding in enumerate(selected["logicCheckResult"]["findings"])],
    ]

    subject_rows: list[list[Any]] = [
        ["序号", "投标文件序号", "投标文件名", "来源", "正确主体", "检测主体", "级别",
         "位置类型", "位置", "原文或印章文字", "角色依据", "不一致原因", "置信度", "建议"],
        *[[index + 1,
           *get_document_columns(document_lookup, finding.get("bidDocumentId")),
           "印章" if finding.get("source") == "seal" else "正文",
           finding.get("expectedSubject", ""), finding.get("detectedSubject", ""),
           CONFIRMED_CONFIDENCE_LABELS.get(finding.get("level"),
                                           finding.get("level", "")),
           SUBJECT_LOCATION_KIND_LABELS.get(finding.get("locationKind"),
                                            finding.get("locationKind", "")),
           finding.get("locationHint", ""), finding.get("originalText", ""),
           finding.get("roleReason", ""), finding.get("mismatchReason", ""),
           _js_number(finding.get("confidence")), finding.get("suggestion", "")]
          for index, finding in enumerate(subject_result["findings"])],
        *build_unrecognized_rows(subject_result),
    ]

    def _plan(name: str, raw_rows: list[list[Any]], widths: list[int],
              filter_header_row: int | None) -> SheetPlan:
        rows = normalize_rows(raw_rows)
        ref = None
        if filter_header_row is not None and rows:
            last_column = max(0, (len(rows[filter_header_row]) if rows[filter_header_row]
                                  else 1) - 1)
            last_row = max(filter_header_row, len(rows) - 1)
            ref = f"A{filter_header_row + 1}:{_column_letter(last_column)}{last_row + 1}"
        return SheetPlan(name=name, rows=rows, widths=widths,
                         filter_header_row=filter_header_row, autofilter_ref=ref,
                         percent_cells={})

    sheets = [
        _plan(REJECTION_SHEET_NAMES[0], overview_rows, [16, 14, 12, 22, 32, 42], None),
        _plan(REJECTION_SHEET_NAMES[1], rejection_rows,
              [8, 14, 28, 12, 12, 24, 36, 42, 42, 42, 42], 0),
        _plan(REJECTION_SHEET_NAMES[2], typo_rows, [8, 14, 28, 18, 18, 26, 48, 38], 0),
        _plan(REJECTION_SHEET_NAMES[3], logic_rows, [8, 14, 28, 24, 28, 48, 42, 42], 0),
        _plan(REJECTION_SHEET_NAMES[4], subject_rows,
              [8, 14, 28, 10, 32, 32, 14, 14, 28, 48, 42, 42, 12, 42], 0),
    ]
    # 参考 :326-333：autofilter 默认会延伸到「未能识别的图片」区块，收缩到表头+主体结果行
    subject_sheet = sheets[4]
    if subject_sheet.filter_header_row is not None:
        last_column = max(0, len(subject_sheet.rows[0]) - 1) if subject_sheet.rows else 0
        last_row = max(subject_sheet.filter_header_row,
                       len(subject_result["findings"]))
        subject_sheet = SheetPlan(
            name=subject_sheet.name, rows=subject_sheet.rows,
            widths=subject_sheet.widths,
            filter_header_row=subject_sheet.filter_header_row,
            autofilter_ref=(f"A{subject_sheet.filter_header_row + 1}:"
                            f"{_column_letter(last_column)}{last_row + 1}"),
            percent_cells={})
    return DuplicateWorkbookPlan(
        title="废标检查结果", created_at=exported_at,
        file_name=sanitize_export_file_name(exported_at), sheets=[
            *(sheet if sheet.name != REJECTION_SHEET_NAMES[4] else subject_sheet
              for sheet in sheets)])


def _column_letter(index: int) -> str:
    from openpyxl.utils import get_column_letter

    return get_column_letter(index + 1)


def _js_number(value: Any) -> int | float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0
    return int(number) if number == int(number) else number


def sanitize_export_file_name(exported_at: datetime) -> str:
    """参考 defaultFileName 形态：``废标检查结果_<YYYYMMDD_HHMMSS>.xlsx``。"""
    return f"废标检查结果_{exported_at.strftime('%Y%m%d_%H%M%S')}.xlsx"
