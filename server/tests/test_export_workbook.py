"""P3d-2 T1 —— 查重导出纯函数层（export_workbook）的 oracle 对拍测试。

断言值唯一来源：`server/tests/fixtures/oracle-duplicate-export.json`
（参考实现 `client/electron/services/checkResultExportService.cjs` 的
``buildDuplicateWorkbook``（:341-469）+ 辅助函数，本机 Node 直跑真值；
生成脚本 `.superpowers/sdd/2026-09-22-p3d2-export/oracle-duplicate.cjs`
——stub XLSX 记录六表 AOA/列宽/autofilter/cell.z 坐标，用后即弃不入库，
真值快照随 fixtures 入库保证干净环境可跑）。

八个 workbook case：full（全维度有数据）、signature_mismatch（签名失效）、
empty（空 state）、error_section（error 段）、success_no_rows（全空 success）、
pairwise_only（仅 pairwise 可导出）、pending_state（pending 不可导出）、
tender_fallback（tenderFiles 空列表回落 tenderFile + bidFiles 空）。

时间语义样本（formatDisplayTime/formatFileTimestamp 的本地时区分支）的期望
值 = 本机（GMT+8）oracle 实测；跨时区机器需重跑 oracle-duplicate.cjs。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from app.domain.duplicate.export_workbook import (
    DUPLICATE_SHEET_NAMES,
    EXCEL_TEXT_LIMIT,
    EXCEL_TEXT_PREFIX_LIMIT,
    EXCEL_TEXT_TRUNCATION_SUFFIX,
    build_duplicate_workbook_plan,
    count_duplicate_rows,
    ensure_xlsx_path,
    format_display_time,
    format_file_timestamp,
    get_duplicate_analysis_descriptor,
    get_result_message,
    get_result_status,
    has_duplicate_rows,
    join_file_names,
    normalize_cell_text,
    normalize_rows,
    sanitize_file_name,
    select_current_duplicate_results,
)

WORKTREE_ROOT = Path(__file__).resolve().parents[2]
ORACLE_PATH = WORKTREE_ROOT / "server" / "tests" / "fixtures" / "oracle-duplicate-export.json"

CASE_TAGS = (
    "full",
    "signature_mismatch",
    "empty",
    "error_section",
    "success_no_rows",
    "pairwise_only",
    "pending_state",
    "tender_fallback",
)

_EMPTY_MESSAGE = "分析已完成，未发现重复项"


@pytest.fixture(scope="module")
def oracle():
    return json.loads(ORACLE_PATH.read_text(encoding="utf-8"))


def _norm_number(value):
    """JS number 无 int/float 之分：整数值浮点归一成 int 后比较（1.0 == 1）。"""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [_norm_number(v) for v in value]
    if isinstance(value, dict):
        return {k: _norm_number(v) for k, v in value.items()}
    return value


# ---------------------------------------------------------------------------
# 六表 plan × 8 case：全量对拍（rows/列宽/autofilter/百分比坐标/exportable）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_tag", CASE_TAGS)
def test_workbook_plan_matches_oracle(oracle, case_tag):
    case = oracle["cases"][case_tag]
    selected = select_current_duplicate_results(case["state"], case["signature"])
    assert selected["exportable"] is case["exportable"], case_tag

    exported_at = datetime.fromisoformat(case["exported_at"])
    plan = build_duplicate_workbook_plan(case["state"], case["signature"], exported_at)

    assert plan.title == case["props_title"]
    assert [s.name for s in plan.sheets] == [s["name"] for s in case["sheets"]]
    assert [s.name for s in plan.sheets] == list(DUPLICATE_SHEET_NAMES)

    for sheet, expected in zip(plan.sheets, case["sheets"]):
        assert _norm_number(sheet.rows) == expected["rows"], sheet.name
        assert sheet.widths == expected["cols_wch"], sheet.name
        assert sheet.autofilter_ref == expected["autofilter_ref"], sheet.name
        assert sheet.percent_cells == expected["percent_cells"], sheet.name
        # 有 autofilter ⇔ filterHeaderRow=0；概览恒 None
        assert (sheet.filter_header_row == 0) == (expected["autofilter_ref"] is not None), sheet.name


def test_plan_file_name_matches_oracle(oracle):
    case = oracle["cases"]["full"]
    plan = build_duplicate_workbook_plan(
        case["state"], case["signature"], datetime.fromisoformat(case["exported_at"]))
    # oracle format_file_timestamp.iso_utc = "20260922_141509"（GMT+8 实测）
    assert plan.file_name == f"标书查重结果_{oracle['helpers']['format_file_timestamp']['iso_utc']}.xlsx"
    assert plan.file_name == "标书查重结果_20260922_141509.xlsx"


# ---------------------------------------------------------------------------
# 辅助函数逐项对拍（oracle helpers 节）
# ---------------------------------------------------------------------------


def test_normalize_cell_text_matches_oracle(oracle):
    for tag, item in oracle["helpers"]["normalize_cell_text"].items():
        value = None if tag == "undefined" else item["input"]
        assert normalize_cell_text(value) == item["output"], tag


def test_format_display_time_matches_oracle(oracle):
    for tag, item in oracle["helpers"]["format_display_time"].items():
        assert format_display_time(item["input"]) == item["output"], tag


def test_format_file_timestamp_matches_oracle(oracle):
    assert format_file_timestamp(datetime.fromisoformat("2026-09-22T06:15:09.000Z")) == "20260922_141509"
    # naive datetime = 本地墙钟（V8 无时区串同语义）
    assert format_file_timestamp(datetime(2026, 9, 22, 14, 15, 9)) == "20260922_141509"


def test_sanitize_file_name_matches_oracle(oracle):
    for tag, item in oracle["helpers"]["sanitize_file_name"].items():
        assert sanitize_file_name(item["input"]) == item["output"], tag


def test_ensure_xlsx_path_matches_oracle(oracle):
    for tag, item in oracle["helpers"]["ensure_xlsx_path"].items():
        assert ensure_xlsx_path(item["input"]) == item["output"], tag


def test_join_file_names_matches_oracle():
    # 与 oracle 脚本同构造：Map {b1:'投标文件A.docx', b2:'', b3:'投标C'}
    name_by_id = {"b1": "投标文件A.docx", "b2": "", "b3": "投标C"}
    assert join_file_names(["b1", "ghost", "b3"], name_by_id) == "投标文件A.docx；ghost；投标C"
    assert join_file_names(["b1", "b2", "b3"], name_by_id) == "投标文件A.docx；b2；投标C"
    assert join_file_names(None, name_by_id) == ""
    assert join_file_names(["b1", ""], name_by_id) == "投标文件A.docx"
    assert join_file_names([], name_by_id) == ""


# status_matrix 输入（与 oracle 脚本同构造，经 get_duplicate_analysis_descriptor）
_MATRIX_INPUTS = {
    "no_source": None,
    "empty_obj": {},
    "stale_sig": {"signature": "old"},
    "pending": {"status": "pending", "signature": "s"},
    "running_msg": {"status": "running", "signature": "s", "message": "正在分析目录"},
    "error_err": {"status": "error", "signature": "s", "error": "解析失败"},
    "error_msg_fallback": {"status": "error", "signature": "s", "message": "消息兜底"},
    "success_zero": {"status": "success", "signature": "s"},
    "success_progress_msg": {"status": "success", "signature": "s", "progressMessage": "3/5"},
    "idle": {"status": "idle", "signature": "s"},
}


def test_result_status_message_matrix_matches_oracle(oracle):
    expected_by_tag = {entry["tag"]: entry for entry in oracle["helpers"]["status_matrix"]}
    for tag, analysis in _MATRIX_INPUTS.items():
        descriptor = get_duplicate_analysis_descriptor(analysis, "s")
        expected = expected_by_tag[tag]
        assert get_result_status(descriptor, "pending") == expected["status"], tag
        assert get_result_message(descriptor, 0, _EMPTY_MESSAGE) == expected["message_zero"], tag
        assert get_result_message(descriptor, 5, _EMPTY_MESSAGE) == expected["message_five"], tag


# ---------------------------------------------------------------------------
# 判定口径钉子（oracle case 间接覆盖，此处显式断言）
# ---------------------------------------------------------------------------


def test_count_and_has_duplicate_rows_oracle_full_case(oracle):
    state = oracle["cases"]["full"]["state"]
    assert count_duplicate_rows("metadataAnalysis", state["metadataAnalysis"]) == 4
    assert count_duplicate_rows("outlineAnalysis", state["outlineAnalysis"]) == 3
    assert count_duplicate_rows("contentAnalysis", state["contentAnalysis"]) == 3
    assert count_duplicate_rows("imageAnalysis", state["imageAnalysis"]) == 2
    assert has_duplicate_rows("outlineAnalysis", state["outlineAnalysis"]) is True
    # content 有 pairwiseSimilarities 不算（仅 outline 特例）
    assert has_duplicate_rows("contentAnalysis", {"duplicateSentences": [], "pairwiseSimilarities": [1]}) is False
    # outline 仅 pairwiseSimilarities → true（pairwise_only case 可导出的依据）
    assert has_duplicate_rows("outlineAnalysis", {"duplicateGroups": [], "pairwiseSimilarities": [1]}) is True
    assert count_duplicate_rows("metadataAnalysis", None) == 0
    assert count_duplicate_rows("metadataAnalysis", {}) == 0


def test_select_tender_files_fallback_shape(oracle):
    # tenderFiles 空列表回落 [tenderFile]；tenderFile 也 None → []
    state = oracle["cases"]["tender_fallback"]["state"]
    selected = select_current_duplicate_results(state, "sig-current")
    assert selected["tenderFiles"] == [state["tenderFile"]]
    assert selected["bidFiles"] == []
    assert selected["exportable"] is True
    assert select_current_duplicate_results({"tenderFile": None, "tenderFiles": []}, None)["tenderFiles"] == []
    # bidFiles 非列表 → []
    assert select_current_duplicate_results({"bidFiles": None}, None)["bidFiles"] == []


# ---------------------------------------------------------------------------
# 本地钉子（净化/number 分支的边界形状）
# ---------------------------------------------------------------------------


def test_normalize_rows_number_branch_excludes_bool():
    # JS typeof number 直写；bool 是 boolean → 走净化成 "true"/"false"
    assert normalize_rows([[1, 2.5, "a", True, False, None]]) == [[1, 2.5, "a", "true", "false", ""]]
    assert normalize_rows([]) == []
    assert normalize_rows([[]]) == [[]]


def test_normalize_cell_text_truncation_boundary():
    assert normalize_cell_text("A" * EXCEL_TEXT_LIMIT) == "A" * EXCEL_TEXT_LIMIT
    truncated = normalize_cell_text("A" * (EXCEL_TEXT_LIMIT + 1))
    assert truncated == "A" * 32750 + EXCEL_TEXT_TRUNCATION_SUFFIX
    assert len(truncated) == EXCEL_TEXT_PREFIX_LIMIT + len(EXCEL_TEXT_TRUNCATION_SUFFIX)
    assert normalize_cell_text("x\x00\x08\x0b\x0c\x0e\x1f\ufffe\uffffy") == "xy"


def test_format_display_time_invalid_passthrough():
    assert format_display_time("not-a-date") == "not-a-date"
    assert format_display_time("2026-13-45") == "2026-13-45"
    assert format_display_time(None) == ""
    assert format_display_time("") == ""
    assert format_display_time(0) == ""
