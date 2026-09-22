r"""P3c 图片比对的纯函数（domain 层：零 I/O、零框架）。

逐字对齐参考实现 `client/electron/services/duplicateCheckService.cjs`：
- `extractImageOccurrences`（:2048-2072）——逐行扫 markdown，维护标题栈与前文窗口
- `extractLineImageTargets`（:2006-2017）——行内图片 target 抽取（位置升序）
- `parseImageContextHeading`（:2019-2037）/ `updateImageContextHeadings`（:2034-2037）
- `getPreviousImageSentence`（:2039-2046）——取图片前最后一个句子，截断 500

**target 形态的偏离（计划裁定 R-P3c-7）**：参考接受**任意** target（它的 target 是
`yibiao-asset://` URL，所以只能读盘算哈希），本实现只认自己产出的
`assets/<64 位小写十六进制>`——产出形态由我们定义（docx 接线产出 markdown 图片形态，
表内图片也是），消费侧与产出一致即可；顺带绕开参考 `!\[(?<alt>[^\]]*)\]\(…` 在
alt 含 `]` 时命中 0 的局限（spec §15 的 G4）。

**UTF-16 vs 码点**：参考的 `text.length > 90`、`.slice(0, 500)`、`.slice(-4000)` 都按
**UTF-16 码元**计数；本实现按 **Python 码点**。BMP 字符（含全部常用 CJK）两者相同，
差异只在 astral 字符（emoji），与 P3b 的 R-P3b-5 同一登记。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.domain.duplicate.content import (
    clean_content_sentence,
    clean_markdown_inline_text,
    clean_markdown_line,
    normalize_content_line_breaks,
)
from app.domain.duplicate.outline import (
    clean_outline_title,
    is_catalog_title_line,
    parse_outline_marker,
)

_MAX_HEADING_TITLE_LENGTH = 90          # 参考 :2030 `text.length > 90`
_PREVIOUS_SENTENCE_LIMIT = 500          # 参考 :2045 `.slice(0, 500)`
_PREVIOUS_TEXT_LIMIT = 4000             # 参考 :2068 `.slice(-4000)`

_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```")           # 参考 :2049
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")        # 参考 :2044
_HORIZONTAL_SPACE_RE = re.compile(r"[\t ]+")              # 参考 :2043
_ATX_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+)$")  # 参考 :2020
_BOLD_LINE_RE = re.compile(r"^\s*\*\*.+\*\*\s*$")          # 参考 :2033
_SENTENCE_END_RE = re.compile(r"[。！？；;]$")               # 参考 :2030

# 图片 target：只认 `assets/<64 位小写十六进制>`（R-P3c-7）。
# markdown 形态用**惰性**前缀以便锚在最近的 `](assets/<hash>)`；`[^\n]*?` 允许 alt 里
# 出现 `]` 与括号（参考的 `[^\]]*` 在 alt 含 `]` 时会命中 0，见 G4）。
_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[[^\n]*?\]\(\s*<?(?P<target>assets/[0-9a-f]{64})>?\s*\)")
# HTML 形态：`<img … src="assets/<hash>" …>`（参考 :2012 用 htmlImageSrcPattern，
# `match.index` 记的是 `<img` 的起点）。
_HTML_IMG_SRC_RE = re.compile(
    r"<img\b[^>]*?\bsrc=[\"'](?P<target>assets/[0-9a-f]{64})[\"']", re.IGNORECASE)


def asset_hash_from_target(target: str) -> str | None:
    """`assets/<64hex>` → 哈希段；其它形态返回 `None`（计划裁定 R-P3c-1）。

    图片**身份就是文件名哈希**：docx 接线产出的 target 与 `save_docx_assets` 落盘的
    文件名是同一个 sha256（R-DW-2），所以不需要读盘上字节再哈希——那是**容器**哈希
    （compress/transcode 后会变），不是图片身份。
    """
    text = str(target or "")
    if not text.startswith("assets/"):
        return None
    digest = text[len("assets/"):]
    return digest if _HEX64_RE.fullmatch(digest) else None


def line_image_targets(line: str) -> list[dict[str, Any]]:
    """抽出一行里的图片 target，**按出现位置升序**（参考 :2006-2017）。

    `index` = 图片语法的起点偏移（markdown 形态记 `![`、HTML 形态记 `<img`）——
    参考用它切出「同一行里图片之前的文本」，故量纲必须一致。
    """
    text = str(line or "")
    found: list[dict[str, Any]] = []
    for match in _MARKDOWN_IMAGE_RE.finditer(text):
        found.append({"target": match.group("target"), "index": match.start()})
    for match in _HTML_IMG_SRC_RE.finditer(text):
        found.append({"target": match.group("target"), "index": match.start()})
    found.sort(key=lambda item: item["index"])
    return found


def previous_image_sentence(value: str) -> str:
    """取 `value` 里**最后一个**句子（清洗后），截断 500（参考 :2039-2046）。"""
    text = clean_markdown_inline_text(value).replace("|", " ")
    text = _HORIZONTAL_SPACE_RE.sub(" ", text).strip()
    parts = [item for item in
             (clean_content_sentence(part) for part in _SENTENCE_SPLIT_RE.split(text))
             if item]
    return (parts[-1] if parts else "")[:_PREVIOUS_SENTENCE_LIMIT]


def parse_image_context_heading(line: str) -> dict[str, Any] | None:
    """把一行识别成「图片上下文标题」（参考 :2019-2037）。

    `#{1,6}` 标题直接成立；否则要求「清洗后非空、≤90 字、不以句末标点结尾、
    不以 `|` 开头、不是目录标题」，**且**整行有编号标记或加粗。
    """
    text = str(line or "")
    hash_match = _ATX_HEADING_RE.match(text)
    if hash_match:
        title = clean_outline_title(hash_match.group(2))
        if not title:
            return None
        return {"level": min(len(hash_match.group(1)), 6), "title": title}

    cleaned = clean_outline_title(text)
    if (not cleaned
            or len(cleaned) > _MAX_HEADING_TITLE_LENGTH
            or _SENTENCE_END_RE.search(cleaned)
            or cleaned.startswith("|")
            or is_catalog_title_line(cleaned)):
        return None
    marker = parse_outline_marker(cleaned)
    bold = bool(_BOLD_LINE_RE.match(text))
    if not marker and not bold:
        return None
    return {"level": (marker or {}).get("level") or 2,
            "title": (marker or {}).get("title") or cleaned}


def _update_image_context_headings(headings: list[dict[str, Any]],
                                   heading: dict[str, Any]) -> None:
    """弹出所有 `level >= 新 heading.level` 的项再入栈（参考 :2034-2037 / 目录栈语义）。"""
    while headings and headings[-1]["level"] >= heading["level"]:
        headings.pop()
    headings.append(heading)


def extract_image_occurrences(markdown: str) -> list[dict[str, Any]]:
    """逐行抽取图片出现位置（参考 :2048-2072）。

    每条 = `{target, index, directory, previous_sentence}`：`index` 是**全局 1-based**
    序号（不是行内偏移），`directory` 是标题栈以 `' > '` 连接，`previous_sentence`
    取「此前窗口（最近 4000 字符）+ 同行图片之前的文本」的最后一个句子。

    围栏代码块先整体替换成换行（参考 :2049）——里面的图片不算出现位置。
    """
    without_fences = _FENCED_CODE_RE.sub("\n", str(markdown or ""))
    lines = normalize_content_line_breaks(without_fences).split("\n")

    occurrences: list[dict[str, Any]] = []
    headings: list[dict[str, Any]] = []
    previous_text = ""
    image_index = 0
    for line in lines:
        heading = parse_image_context_heading(line)
        if heading:
            _update_image_context_headings(headings, heading)
        for item in line_image_targets(line):
            image_index += 1
            occurrences.append({
                "target": item["target"],
                "index": image_index,
                "directory": " > ".join(entry["title"] for entry in headings),
                "previous_sentence": previous_image_sentence(
                    f"{previous_text}\n{line[: item['index']]}"),
            })
        cleaned_line = clean_markdown_line(line)
        if cleaned_line:
            previous_text = f"{previous_text}\n{cleaned_line}"[-_PREVIOUS_TEXT_LIMIT:]
    return occurrences


def build_duplicate_images(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """跨文件去重汇总：只留**出现在 >1 个文件**的图片，按双键降序并编号（参考 :2115-2121）。

    排序：先按 `file_ids` 数量**降序**，再按出现总次数降序；`id = f"I{序号:06d}"`
    （序号从 1 开始）。`occurrences`/`locations` 的展开由调用方按 `hash` 关联。
    """
    def total_occurrences(row: Mapping[str, Any]) -> int:
        return sum((row.get("occurrences") or {}).values())

    duplicated = [row for row in rows if len(row.get("file_ids") or []) > 1]
    duplicated.sort(key=lambda row: (len(row["file_ids"]), total_occurrences(row)),
                    reverse=True)
    return [dict(row, image_id=f"I{index:06d}")
            for index, row in enumerate(duplicated, start=1)]
