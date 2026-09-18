"""查重四段状态与统计补丁（纯数据，零 I/O）。

逐字对齐参考实现 `client/electron/services/duplicateCheckStore.cjs`：

- `createSectionStatsPatch`（:179-222）
- `hasOwn`（:33-35，即 `Object.prototype.hasOwnProperty.call`）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

ANALYSIS_SECTIONS: tuple[str, ...] = ("metadata", "outline", "content", "image")
SECTION_STATUSES: frozenset[str] = frozenset({"pending", "running", "success", "error"})

# `stats.files` 的字段裁剪白名单，对齐 :187 与 :196-205。
_METADATA_FILE_FIELDS: tuple[str, ...] = (
    "file_id",
    "file_name",
    "status",
    "error",
)
_OUTLINE_FILE_FIELDS: tuple[str, ...] = (
    "file_id",
    "file_name",
    "status",
    "source",
    "confidence",
    "item_count",
    "tender_matched_count",
    "error",
)


def normalize_section_status(value: object, *, fallback: str = "pending") -> str:
    """把任意值归一化为白名单内的状态字符串，否则返回 `fallback`。

    白名单外的值（`"weird"` / `None` / `123` / 不可哈希对象）一律回落，**不抛异常** ——
    SQLite 与 JSON 里读回来的历史数据可能是任何东西。`value` 可能是 `str` 之外的类型，
    故先用 `isinstance` 判类型再查集合，避免对不可哈希对象求 `in` 时抛 `TypeError`。
    """
    if isinstance(value, str) and value in SECTION_STATUSES:
        return value
    return fallback


@dataclass(frozen=True)
class SectionState:
    """单段分析的持久化状态。

    **`frozen=True` 的确切保证（fix-round 1 / F4 订正）**：只禁止**属性名重新绑定**
    （`s.status = "x"` 抛 `FrozenInstanceError`），此外因字段不可变、`dataclass`
    会一并生成 `__hash__`（该哈希只有在 `stats` 为可哈希映射时才可用）。

    **它不保证的内容**：`stats` 字段是 `Mapping`，`frozen` **拦不住就地改写** ——
    `s.stats["k"] = 2` 仍会改到同一个 dict（`default_factory=dict` 产出的是可变
    `dict`），且该实例因此**不可哈希**（`hash(s)` 抛 `TypeError: unhashable type`）。
    这是刻意的取舍：`stats` 是部分更新补丁（见 `build_section_stats_patch`），
    上游需要往里塞嵌套的原始 JSON，不冻结它更实用。

    结论：本对象是「**浅不可变**」的传输载体，**不要**当作深不可变的共享值使用。
    """

    status: str = "pending"
    progress: int = 0
    message: str = ""
    signature: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    stats: Mapping[str, Any] = field(default_factory=dict)


def _pick(analysis: Mapping[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """按 `hasOwn` 语义挑字段：**键存在**即产出，值可以是 `None`。

    对齐 `hasOwn`（:33-35）—— 不能用 `analysis.get(key) is not None`，
    否则 `{"logs": None}` 会被误判为「没有 logs」。
    """
    return {key: analysis[key] for key in keys if key in analysis}


def _files_of(analysis: Mapping[str, Any], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """`stats.files` 分支：非列表静默变空列表，每个元素按 `fields` 裁剪。

    对齐 :186-187 与 :196-205 的 `Array.isArray(analysis.files) ? analysis.files : []`
    与 `.map((file) => ({...}))`：`isinstance(value, list)` 即 `Array.isArray` 的等价判定；
    裁剪时缺字段产出 `None`（JS 对象字面量里 `file.error` 不存在就是 `undefined`）。
    """
    raw = analysis["files"]
    if not isinstance(raw, list):
        raw = []
    return [{name: file.get(name) if isinstance(file, Mapping) else None for name in fields}
            for file in raw]


def build_section_stats_patch(section: str, analysis: Mapping[str, Any]) -> dict[str, Any]:
    """按 section 的字段白名单，从 analysis 里裁出一份 stats 补丁。

    对齐 `createSectionStatsPatch`（:179-222）：四个显式分支，未知 section 返回 `{}`。
    白名单外的键一律丢弃（补丁是部分更新，不是整体替换）。
    """
    if section == "metadata":
        patch = _pick(analysis, ("contentExtraction", "metadataExtraction", "logs"))
        if "files" in analysis:
            patch["files"] = _files_of(analysis, _METADATA_FILE_FIELDS)
        return patch
    if section == "outline":
        patch = _pick(
            analysis, ("tenderSentenceCount", "tenderMatchedItemCount", "extraction")
        )
        if "files" in analysis:
            patch["files"] = _files_of(analysis, _OUTLINE_FILE_FIELDS)
        return patch
    if section == "content":
        return _pick(
            analysis,
            (
                "tenderSentenceCount",
                "tenderMatchedSentenceCount",
                "totalSentenceCount",
                "extraction",
            ),
        )
    if section == "image":
        return _pick(analysis, ("extraction", "totalImageCount"))
    return {}
