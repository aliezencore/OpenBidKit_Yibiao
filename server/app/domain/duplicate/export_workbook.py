"""查重结果 Excel 导出的工作簿 plan 构造（P3d-2 T1，R-P3d2-2）。

逐字对齐参考实现 `client/electron/services/checkResultExportService.cjs` 的
``buildDuplicateWorkbook``（:341-469）及其传递依赖（``normalizeCellText`` 一族、
``selectCurrentDuplicateResults``、``getResultStatus``/``getResultMessage``、
``joinFileNames``）。oracle = 参考实现本身
（``.superpowers/sdd/2026-09-22-p3d2-export/oracle-duplicate.cjs``，stub XLSX
记录六表 AOA/列宽/autofilter/cell.z 真值；断言值必须来自它）。

分层（R-P3d2-2）：本模块只产出结构化 plan（表名/净化后行/列宽/autofilter
ref/百分比格式坐标 + 导出文件名），**零 I/O 零 openpyxl**；xlsx 序列化在
``app/infra/exporting/duplicate_xlsx.py``（T2）。输入 =
``DuplicateRepository.load_analysis`` 产物（与参考前端 ``loadDuplicateCheck()``
state 同构）+ signature + exported_at。

**JS → Python 的移植约定**：

- ``normalizeRows`` 在参考里位于 xlsx 层（``createWorksheet`` 内），此处
  **前置**到 plan 构造（净化后 AOA 直接进 plan，组装器免再净化）——
  净化语义不变（R-P3d2-4：number 直写、其余经 ``normalize_cell_text``）；
- ``Number(x || 0)`` 收在 ``_js_number_or_zero``（实际值域 int/float；JS 对
  非空数字串 ``Number('3')``→3、对不可转串 →NaN 的行为在当前仓储形状下
  不可达，统一按 0 处理）；``Number.isFinite(Number(x))`` 收在
  ``_js_number_finite``（图片位置序号）；
- JS 空对象为真值、Python 空 dict 为假值的分歧收在
  ``descriptor.get("source") is None`` 判断（对齐 ``source || descriptor``
  的回落语义）；
- 时间解析：``new Date(value)`` 的 ISO 串形态 → ``datetime.fromisoformat``
  （Python 3.13 支持 Z 后缀/空格分隔/毫秒/偏移）；epoch 毫秒数字 →
  ``fromtimestamp``；解析失败 → 原样过 ``normalize_cell_text``（参考的
  NaN 分支）。带时区 → 转**本地时区**（与 V8 一致；oracle 在本机 GMT+8
  对拍验证 iso_utc/iso_offset/epoch_ms 三形态）；无时区串按本地墙钟
  （V8 同语义，naive datetime 即本地墙钟）；
- 文本限值（``length > 32767``、``slice(0, 32750)``）按**码点**实现（同
  outline.py/content.py 的登记：JS 是 UTF-16 码元，BMP 内两端一致，仅
  astral 字符（emoji）有差——标书文本极罕见，不做 UTF-16 仿真）；
- ``_js_string`` 与 metadata_base 同实现（bool → "true"/"false"、整数值
  浮点 → 整数串）；本包惯例不跨模块 import 私有名，故本地重写。

**有意偏离（R-P3d2-1）**：signature 由服务端自持
（``duplicate_check_meta.current_signature``），不经请求传入；纯函数仍以
参数接收，判定形状与参考一致。

**已登记偏离（spec §15 回填）**：

1. risk 映射用 ``dict.get(risk, "无")``——JS 的
   ``({high:...})[item.risk]`` 对 ``"toString"`` 等原型键会取到函数再被
   ``|| '无'`` 吞掉成函数串；不可达（risk 恒四枚举值），Python 语义更稳。
2. metadata 表头/路径明细/位置串等 f-string 中若值为 None 输出 ``"None"``
   （JS 模板串输出 ``"undefined"``）——``load_analysis`` 产出保证
   file_name/路径为串，不可达。
3. ``format_file_timestamp`` 对 Invalid Date 参考产出 ``"NaNNaNNaN_..."``
   拼接（参考缺陷逐字复刻）；exported_at 恒为调用方传入的有效 ``now()``，
   不可达。
4. ``count_duplicate_rows`` 用固定四键映射表：参考 if/elif 链对未知 field
   落到 duplicateImages 分支；调用点恒四值，未知 field 此处返回 0。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------- 常量（:5-10）

DUPLICATE_SHEET_NAMES = ["查重概览", "元数据", "目录重复", "文件相似度", "重复句子", "重复图片"]

EXCEL_TEXT_LIMIT = 32767
EXCEL_TEXT_PREFIX_LIMIT = 32750
EXCEL_TEXT_TRUNCATION_SUFFIX = "……（内容过长，已截断）"

_JOIN_SEP = "；"  # joinFileNames/概览文件名列共用
_RISK_LABELS = {"high": "高", "medium": "中", "low": "低", "none": "无"}
_EMPTY_MESSAGE = "分析已完成，未发现重复项"

# normalizeCellText :33 —— 剥 \u0000-\u0008 \u000B \u000C \u000E-\u001F \uFFFE \uFFFF
_CELL_TEXT_STRIP_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")
# sanitizeFileName :43
_FILE_NAME_ILLEGAL_RE = re.compile(r"[<>:\"/\\|?*\x00-\x1f]")
_XLSX_SUFFIX_RE = re.compile(r"\.xlsx$", re.IGNORECASE)

_COUNT_KEY_BY_FIELD = {
    "metadataAnalysis": "rows",
    "outlineAnalysis": "duplicateGroups",
    "contentAnalysis": "duplicateSentences",
    "imageAnalysis": "duplicateImages",
}

# ---------------------------------------------------------------- 文本/数字/时间


def _js_string(value: Any) -> str:
    """``String(value)`` 的对应物（与 metadata_base 同实现，见模块注释）。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    return str(value)


def normalize_cell_text(value: Any) -> str:
    """``normalizeCellText``（:30-36）：None/undefined → ""、剥控制字符、超限截断。

    长度按码点（JS 为 UTF-16 码元；BMP 内一致，见模块注释）。
    """
    if value is None:
        return ""
    normalized = _CELL_TEXT_STRIP_RE.sub("", value if isinstance(value, str) else _js_string(value))
    if len(normalized) <= EXCEL_TEXT_LIMIT:
        return normalized
    return normalized[:EXCEL_TEXT_PREFIX_LIMIT] + EXCEL_TEXT_TRUNCATION_SUFFIX


def normalize_rows(rows: list[list[Any]]) -> list[list[Any]]:
    """``normalizeRows``（:38-40）：number 直写（bool 不算 number，走净化成
    "true"/"false"——对齐 JS ``typeof``），其余经 ``normalize_cell_text``。"""
    return [
        [
            value
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else normalize_cell_text(value)
            for value in row
        ]
        for row in rows
    ]


def _js_number_or_zero(value: Any) -> int | float:
    """``Number(x || 0)``（概览统计块/相似度列）。实际值域 int/float；
    其余类型在当前仓储形状下不可达，统一 0（见模块注释）。"""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    return 0


def _js_number_finite(value: Any) -> float | None:
    """``Number.isFinite(Number(x))`` 的数（图片位置序号 :460）。

    覆盖实际触达形状：int/float/bool、数字串（含空串/空白串 —— JS
    ``Number('')`` 是 0）、None/缺键 → NaN（None）。
    """
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            parsed = float(text)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def _number_repr(value: int | float) -> str:
    """JS number → 模板串的数字形态（``String(3.0)`` 是 "3"）。"""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _parse_date_value(value: Any) -> datetime | None:
    """``new Date(value)`` 的实际触达形态（updated_at 为 ISO 串、epoch 毫秒数字）。

    解析失败返回 None（调用方走参考的 NaN 分支）。V8 的宽松 legacy parser
    （RFC 2822 等）不仿真——updated_at 恒 ISO 串。
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _as_local(value: datetime) -> datetime:
    """带时区 → 本地时区（V8 显示语义）；naive = 本地墙钟，原样。"""
    return value.astimezone() if value.tzinfo is not None else value


def format_display_time(value: Any) -> str:
    """``formatDisplayTime``（:57-63）：falsy → ""；无效串原样过净化；
    有效 → ``YYYY-MM-DD HH:MM:SS``（本地时区）。"""
    if not value:
        return ""
    parsed = _parse_date_value(value)
    if parsed is None:
        return normalize_cell_text(value)
    local = _as_local(parsed)
    return (
        f"{local.year:04d}-{local.month:02d}-{local.day:02d}"
        f" {local.hour:02d}:{local.minute:02d}:{local.second:02d}"
    )


def format_file_timestamp(value: Any) -> str:
    """``formatFileTimestamp``（:51-55）：``YYYYMMDD_HHMMSS``（本地时区）。

    Invalid Date 参考产出 "NaNNaNNaN_NaNNaNNaN"（逐字复刻；exported_at
    恒为有效 now()，不可达）。
    """
    parsed = _parse_date_value(value)
    if parsed is None:
        return "NaNNaNNaN_NaNNaNNaN"
    local = _as_local(parsed)
    return (
        f"{local.year}{local.month:02d}{local.day:02d}"
        f"_{local.hour:02d}{local.minute:02d}{local.second:02d}"
    )


def sanitize_file_name(file_name: Any) -> str:
    """``sanitizeFileName``（:42-44）：先净化文本，再替换文件系统非法字符。"""
    return _FILE_NAME_ILLEGAL_RE.sub("_", normalize_cell_text(file_name))


def ensure_xlsx_path(file_path: Any) -> str:
    """``ensureXlsxPath``（:46-49）：无 .xlsx 后缀（大小写不敏感）则补。"""
    value = str(file_path) if file_path else ""
    return value if _XLSX_SUFFIX_RE.search(value) else f"{value}.xlsx"


def join_file_names(file_ids: Any, name_by_id: dict[str, str]) -> str:
    """``joinFileNames``（:334-339）：``name || fileId`` 回落 → 滤空 → ``；`` join。"""
    ids = file_ids if isinstance(file_ids, list) else []
    return _JOIN_SEP.join(
        value
        for value in ((name_by_id.get(fid) or fid) for fid in ids)
        if value
    )


def _join_display(values: list[Any]) -> str:
    """概览表文件名列（:349-350）：``map(file_name).filter(Boolean).join('；')``。"""
    return _JOIN_SEP.join(value for value in values if value)


def _encode_cell(column: int, row: int) -> str:
    """SheetJS ``encode_cell``（bijective base-26，0→A；行 1 起）——
    产出坐标与 openpyxl 的 A1 记法一致，T2 直接用作 cell 坐标。"""
    letters = ""
    n = column + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row + 1}"


def _autofilter_ref(rows: list[list[Any]], filter_header_row: int) -> str:
    """``createWorksheet`` 的 !autofilter（:69-75）：
    ``encode_range({s:{r:header,c:0}, e:{r:max(header, len-1), c:max(0, header_len-1)}})``。
    header 行不存在/为空列表 → 长度按 1（JS ``?.length || 1``）。"""
    header = rows[filter_header_row]
    last_column = max(0, (len(header) if header else 1) - 1)
    last_row = max(filter_header_row, len(rows) - 1)
    return f"{_encode_cell(0, filter_header_row)}:{_encode_cell(last_column, last_row)}"

# ---------------------------------------------------------------- 判定（:138-203）


def get_duplicate_analysis_descriptor(analysis: Any, signature: Any) -> dict:
    """``getDuplicateAnalysisDescriptor``（:138-144）：``valid = source 存在
    且 source.signature === signature``（段 signature/current_signature 均
    str 或 None，``==`` 等价严格相等）。"""
    source = analysis if isinstance(analysis, dict) else None
    return {
        "source": source,
        "valid": bool(source) and source.get("signature") == signature,
    }


def count_duplicate_rows(field_name: str, analysis: Any) -> int:
    """``countDuplicateRows``（:146-152）四字段口径。"""
    if not analysis:
        return 0
    value = analysis.get(_COUNT_KEY_BY_FIELD.get(field_name))
    return len(value) if isinstance(value, list) else 0


def has_duplicate_rows(field_name: str, analysis: Any) -> bool:
    """``hasDuplicateRows``（:154-159）：仅 outlineAdditional 考虑
    ``pairwiseSimilarities``（导出判定特有口径）。"""
    if count_duplicate_rows(field_name, analysis) > 0:
        return True
    return (
        field_name == "outlineAnalysis"
        and isinstance(analysis, dict)
        and isinstance(analysis.get("pairwiseSimilarities"), list)
        and len(analysis["pairwiseSimilarities"]) > 0
    )


def select_current_duplicate_results(state: Any, signature: Any) -> dict:
    """``selectCurrentDuplicateResults``（:161-180）。

    ``tenderFiles`` 空列表回落 ``[tenderFile]``（滤 None）；exportable =
    四段任一 ``valid && (status == 'success' 或有重复行)``。
    """
    workspace = state if isinstance(state, dict) else {}
    tender_files = workspace.get("tenderFiles")
    if not isinstance(tender_files, list) or not tender_files:
        tender_file = workspace.get("tenderFile")
        tender_files = [tender_file] if tender_file else []
    bid_files = workspace.get("bidFiles")
    if not isinstance(bid_files, list):
        bid_files = []
    selected: dict[str, Any] = {"tenderFiles": tender_files, "bidFiles": bid_files}
    for field_name in ("metadataAnalysis", "outlineAnalysis", "contentAnalysis", "imageAnalysis"):
        selected[field_name] = get_duplicate_analysis_descriptor(workspace.get(field_name), signature)
    selected["exportable"] = any(
        selected[field_name]["valid"]
        and (
            (selected[field_name]["source"].get("status") == "success")
            or has_duplicate_rows(field_name, selected[field_name]["source"])
        )
        for field_name in ("metadataAnalysis", "outlineAnalysis", "contentAnalysis", "imageAnalysis")
    )
    return selected


def get_result_status(descriptor: dict, idle_status: str = "idle") -> str:
    """``getResultStatus``（:182-192）。

    末尾两分支（idle/pending → "未执行"、缺省 → "未执行"）同果，合并。
    ``descriptor.source || descriptor`` 的回落：source 为 None 才回落
    （空 dict 是 JS 真值，不回落——见模块注释）。
    """
    source = descriptor.get("source")
    if source is None:
        source = descriptor
    if not source:
        return "未执行"
    if not descriptor.get("valid", False):
        has_signature = bool(source.get("inputSignature") or source.get("signature"))
        return "结果已失效" if has_signature else "未执行"
    status = source.get("status")
    if status == "success":
        return "已完成"
    if status == "error":
        return "失败"
    return "未执行"


def get_result_message(descriptor: dict, result_count: int, empty_message: str) -> str:
    """``getResultMessage``（:194-203）：error 段吃
    ``error || message || progressMessage || '检查失败'``；success 空结果吃
    empty_message；其余吃 ``message || progressMessage``。"""
    source = descriptor.get("source")
    if source is None:
        source = descriptor
    if not source:
        return ""
    if not descriptor.get("valid", False):
        has_signature = bool(source.get("inputSignature") or source.get("signature"))
        return "结果签名与当前检查输入不一致" if has_signature else ""
    status = source.get("status")
    if status == "error":
        return (
            source.get("error")
            or source.get("message")
            or source.get("progressMessage")
            or "检查失败"
        )
    if status == "success" and result_count == 0:
        return empty_message
    return source.get("message") or source.get("progressMessage") or ""

# ---------------------------------------------------------------- plan 构造（:341-469）


@dataclass(frozen=True)
class SheetPlan:
    """单工作表 plan（T2 组装器的输入）。rows 是**净化后** AOA；
    percent_cells 键 = openpyxl A1 坐标（与 SheetJS encode_cell 同形），值 = 数字格式。"""

    name: str
    rows: list[list[Any]]
    widths: list[int]
    filter_header_row: int | None
    autofilter_ref: str | None
    percent_cells: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DuplicateWorkbookPlan:
    """六表工作簿 plan。``file_name`` 已过 ``sanitize_file_name``（T3 直接进
    Content-Disposition；参考 defaultFileName 形态 ``标书查重结果_<ts>.xlsx``）。"""

    title: str
    created_at: datetime
    file_name: str
    sheets: list[SheetPlan]


def _sheet_plan(name: str, raw_rows: list[list[Any]], widths: list[int],
                filter_header_row: int | None, percent_cells: dict[str, str] | None = None) -> SheetPlan:
    """normalize_rows 前置（见模块注释）+ autofilter ref 计算。"""
    rows = normalize_rows(raw_rows)
    return SheetPlan(
        name=name,
        rows=rows,
        widths=widths,
        filter_header_row=filter_header_row,
        autofilter_ref=_autofilter_ref(rows, filter_header_row) if filter_header_row is not None else None,
        percent_cells=percent_cells or {},
    )


def build_duplicate_workbook_plan(state: Any, signature: Any, exported_at: datetime) -> DuplicateWorkbookPlan:
    """``buildDuplicateWorkbook``（:341-469）的 plan 版：六表 + 导出文件名。

    ``exportable`` 判定不在此处（参考 buildDuplicateWorkbook 也不判；T3 先经
    ``select_current_duplicate_results`` 判，不可导出 → 400）。
    """
    selected = select_current_duplicate_results(state, signature)
    tender_files = selected["tenderFiles"]
    bid_files = selected["bidFiles"]

    all_files = [*tender_files, *bid_files]
    name_by_id = {f["id"]: (f.get("file_name") or "") for f in all_files if isinstance(f, dict) and "id" in f}
    tender_names = _join_display([f.get("file_name") for f in tender_files if isinstance(f, dict)])
    bid_names = _join_display([f.get("file_name") for f in bid_files if isinstance(f, dict)])

    # ---- 查重概览（:357-381）：导出时间/空行/表头/四维度行/空行/统计块；无 autofilter
    fields = (
        ("元数据", "metadataAnalysis"),
        ("目录", "outlineAnalysis"),
        ("正文", "contentAnalysis"),
        ("图片", "imageAnalysis"),
    )
    overview_rows: list[list[Any]] = [
        ["导出时间", format_display_time(exported_at)],
        [],
        ["分析维度", "状态", "结果数量", "更新时间", "招标文件", "投标文件", "说明"],
    ]
    for label, field_name in fields:
        descriptor = selected[field_name]
        source = descriptor["source"]
        count = count_duplicate_rows(field_name, source) if descriptor["valid"] else 0
        overview_rows.append([
            label,
            get_result_status(descriptor, "pending"),
            count,
            # 更新时间直取 ISO 原串（不 formatDisplayTime，oracle 实证）
            (source.get("updated_at") or "") if isinstance(source, dict) else "",
            tender_names,
            bid_names,
            get_result_message(descriptor, count, _EMPTY_MESSAGE),
        ])
    overview_rows += [
        [],
        ["统计项", "数值"],
        ["识别图片总数", _overview_stat(selected, "imageAnalysis", "totalImageCount")],
        ["招标目录排除数量", _overview_stat(selected, "outlineAnalysis", "tenderMatchedItemCount")],
        ["招标引用句子排除数量", _overview_stat(selected, "contentAnalysis", "tenderMatchedSentenceCount")],
        ["正文句子总量", _overview_stat(selected, "contentAnalysis", "totalSentenceCount")],
    ]
    sheets = [_sheet_plan(DUPLICATE_SHEET_NAMES[0], overview_rows, [16, 14, 12, 22, 30, 34, 42], None)]

    # ---- 元数据（:383-393）：表头动态投标文件列；values 缺键 → 空串
    metadata = selected["metadataAnalysis"]["source"] if selected["metadataAnalysis"]["valid"] else None
    metadata_rows: list[list[Any]] = [
        ["元数据项", "重复文件", "同日文件",
         *[f"投标文件{index + 1}：{f.get('file_name')}" for index, f in enumerate(bid_files)]],
    ]
    for row in (metadata.get("rows") or []) if metadata else []:
        values = row.get("values") or {}
        metadata_rows.append([
            row.get("label"),
            join_file_names(row.get("duplicate_file_ids"), name_by_id),
            join_file_names(row.get("same_day_file_ids"), name_by_id),
            *[values.get(f["id"]) for f in bid_files],
        ])
    sheets.append(_sheet_plan(
        DUPLICATE_SHEET_NAMES[1], metadata_rows,
        [24, 32, 32, *[32] * len(bid_files)], 0))

    # ---- 目录重复（:395-412）：D 列（c=3）百分比
    outline = selected["outlineAnalysis"]["source"] if selected["outlineAnalysis"]["valid"] else None
    outline_rows: list[list[Any]] = [["序号", "类型", "标题", "相似度", "涉及文件", "路径明细"]]
    for index, group in enumerate((outline.get("duplicateGroups") or []) if outline else []):
        file_ids = group.get("file_ids") or []
        paths = group.get("paths") or {}
        outline_rows.append([
            index + 1,
            "重复" if group.get("type") == "duplicate" else "相似",
            group.get("title"),
            _js_number_or_zero(group.get("score")),
            join_file_names(file_ids, name_by_id),
            _JOIN_SEP.join(
                f"{name_by_id.get(fid) or fid}：{path}"
                for fid in file_ids for path in (paths.get(fid) or [])
            ),
        ])
    outline_percent = {_encode_cell(3, row): "0.00%" for row in range(1, len(outline_rows))}
    sheets.append(_sheet_plan(
        DUPLICATE_SHEET_NAMES[2], outline_rows, [8, 10, 32, 12, 34, 64], 0, outline_percent))

    # ---- 文件相似度（:414-434）：c=2..5 百分比；risk 缺省 → 无
    pairwise_rows: list[list[Any]] = [
        ["文件 A", "文件 B", "综合相似度", "标题重合度", "路径重合度", "顺序相似度", "共享目录数量", "风险等级"],
    ]
    for item in (outline.get("pairwiseSimilarities") or []) if outline else []:
        pairwise_rows.append([
            name_by_id.get(item.get("file_a_id")) or item.get("file_a_id"),
            name_by_id.get(item.get("file_b_id")) or item.get("file_b_id"),
            _js_number_or_zero(item.get("score")),
            _js_number_or_zero(item.get("title_overlap")),
            _js_number_or_zero(item.get("path_overlap")),
            _js_number_or_zero(item.get("order_similarity")),
            _js_number_or_zero(item.get("shared_count")),
            _RISK_LABELS.get(item.get("risk"), "无"),
        ])
    pairwise_percent = {
        _encode_cell(column, row): "0.00%"
        for row in range(1, len(pairwise_rows))
        for column in (2, 3, 4, 5)
    }
    sheets.append(_sheet_plan(
        DUPLICATE_SHEET_NAMES[3], pairwise_rows, [28, 28, 14, 14, 14, 14, 14, 12], 0, pairwise_percent))

    # ---- 重复句子（:436-447）：总次数 = occurrences 全部值求和（不限于 file_ids）
    content = selected["contentAnalysis"]["source"] if selected["contentAnalysis"]["valid"] else None
    content_rows: list[list[Any]] = [["序号", "重复句子", "涉及文件", "各文件出现次数", "总出现次数"]]
    for index, item in enumerate((content.get("duplicateSentences") or []) if content else []):
        file_ids = item.get("file_ids") or []
        occurrences = item.get("occurrences") or {}
        content_rows.append([
            index + 1,
            item.get("sentence"),
            join_file_names(file_ids, name_by_id),
            _JOIN_SEP.join(
                f"{name_by_id.get(fid) or fid} × {_number_repr(_js_number_or_zero(occurrences.get(fid)))}"
                for fid in file_ids
            ),
            sum(_js_number_or_zero(count) for count in occurrences.values()),
        ])
    sheets.append(_sheet_plan(
        DUPLICATE_SHEET_NAMES[4], content_rows, [8, 72, 34, 48, 14], 0))

    # ---- 重复图片（:449-467）：位置/前文按 file_ids × locations 展开
    image = selected["imageAnalysis"]["source"] if selected["imageAnalysis"]["valid"] else None
    image_rows: list[list[Any]] = [["序号", "图片 Hash", "涉及文件", "各文件出现次数", "出现位置", "图片前文"]]
    for index, item in enumerate((image.get("duplicateImages") or []) if image else []):
        file_ids = item.get("file_ids") or []
        occurrences = item.get("occurrences") or {}
        locations = item.get("locations") or {}
        entries = [(fid, loc) for fid in file_ids for loc in (locations.get(fid) or [])]
        image_rows.append([
            index + 1,
            item.get("hash"),
            join_file_names(file_ids, name_by_id),
            _JOIN_SEP.join(
                f"{name_by_id.get(fid) or fid} × {_number_repr(_js_number_or_zero(occurrences.get(fid)))}"
                for fid in file_ids
            ),
            _JOIN_SEP.join(
                f"{name_by_id.get(fid) or fid}：{loc.get('directory') or '未识别目录'}{_image_index_text(loc)}"
                for fid, loc in entries
            ),
            _JOIN_SEP.join(
                f"{name_by_id.get(fid) or fid}：{loc.get('previous_sentence') or ''}"
                for fid, loc in entries
            ),
        ])
    sheets.append(_sheet_plan(
        DUPLICATE_SHEET_NAMES[5], image_rows, [8, 40, 34, 48, 64, 72], 0))

    file_stem = f"标书查重结果_{format_file_timestamp(exported_at)}"
    return DuplicateWorkbookPlan(
        title="标书查重结果",
        created_at=exported_at,
        file_name=sanitize_file_name(f"{file_stem}.xlsx"),
        sheets=sheets,
    )


def _overview_stat(selected: dict, field_name: str, key: str) -> int | float:
    """概览统计块（:376-379）：``valid ? Number(source[key] || 0) : 0``。"""
    descriptor = selected[field_name]
    if not descriptor["valid"]:
        return 0
    return _js_number_or_zero(descriptor["source"].get(key))


def _image_index_text(location: dict) -> str:
    """图片位置串的序号尾（:460-461）：``Number.isFinite(Number(image_index))``
    才拼 ``（图片 n）``；directory 缺失由调用方兜 "未识别目录"。"""
    number = _js_number_finite(location.get("image_index"))
    return f"（图片 {_number_repr(number)}）" if number is not None else ""
