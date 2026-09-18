"""目录比对纯函数：三来源抽取 / 分组 / pairwise / riskFromScore（零 I/O）。

逐字移植参考实现 `client/electron/services/duplicateCheckService.cjs`
（下称 svc.cjs）:1134-1451 的 18 个函数，camelCase → snake_case：

============================  ==========================  ===============
参考函数                       svc.cjs 行号                 本模块
============================  ==========================  ===============
``stripMarkdownForOutline``   :1134                       ``strip_markdown_for_outline``
``normalizeOutlineTitle``     :1146                       ``normalize_outline_title``
``cleanOutlineTitle``         :1156                       ``clean_outline_title``
``splitTenderSentences``      :1165                       ``split_tender_sentences``
``matchTenderSentence``       :1185                       ``match_tender_sentence``
``parseOutlineMarker``        :1196                       ``parse_outline_marker``
``inferOutlineLevel``         :1216                       ``infer_outline_level``
``isCatalogTitleLine``        :1224                       ``is_catalog_title_line``
``parseCatalogLine``          :1228                       ``parse_catalog_line``
``extractCatalogOutline``     :1239                       ``extract_catalog_outline``
``extractHeadingOutline``     :1258                       ``extract_heading_outline``
``extractSemanticOutline``    :1272                       ``extract_semantic_outline``
``buildOutlineItems``         :1286                       ``build_outline_items``
``intersectSize``             :1328                       ``intersect_size``
``bigramSimilarity``          :1334                       ``bigram_similarity``
``lcsSimilarity``             :1350                       ``lcs_similarity``
``riskFromScore``             :1361                       ``risk_from_score``
``buildOutlineComparison``    :1368                       ``build_outline_comparison``
============================  ==========================  ===============

上游依赖 ``normalizeValue``（svc.cjs:133-143）以 :func:`_normalize_value` 一并移植。

**偏离登记（测试 `server/tests/test_domain_outline.py` 的预期值全部经 Node
直接运行参考实现取地面真值核对）：**

- **D1 码点语义（R-P3b-5，登记偏离）**：JS 的 ``str.length`` / ``slice`` /
  索引按 UTF-16 码元；Python 的 ``len`` / 切片 / 迭代按码点。影响点：
  ``split_tender_sentences`` 的 ``[6, 160]`` 长度窗（:1178）、
  ``extract_semantic_outline`` 的 ≤90 行长（:1277）与 260 截断（:1283）、
  ``build_outline_items`` 的 normalized 长度判据（:1297）。BMP 内（含全部
  CJK）两端一致，仅 astral 字符（emoji 等）有差。裁定：Python 按码点实现，
  不做 UTF-16 仿真（标书正文 emoji 极罕见，整套 UTF-16 仿真代价不成比例）。
  **例外**：``bigram_similarity`` 参考用 ``Array.from``（:1340，码点迭代），
  Python ``list(str)`` 同为码点——**两端恰好一致，无偏离**。
- **D2 ``\\d`` 语义**：JS ``\\d`` 只匹配 ASCII ``[0-9]``；Python ``re`` 的
  ``\\d`` 匹配 Unicode 十进制数字。为保持正则文本与参考逐字可对照，本模块
  保留 ``\\d`` 写法。实际影响面：本模块所有进入正则的文本都先经
  :func:`_normalize_value` 的 **NFKC**（:139），全角数字（１２３）已被归一成
  ASCII，唯一能触达 ``\\d`` 差异的只剩 ``infer_outline_level`` 直调
  全角数字串（内部编号串来自 post-NFKC 的匹配组，不可达），接受并登记。
- **D3 ``normalizeValue`` 分支裁剪**：参考 :135-137 的 Date / Array / object
  分支在本模块不可达（所有调用点的输入都是字符串），只移植字符串路径。
- **D4 ``to_fixed_2`` 用精确十进制 half-up**（R-P3b-7）：**没有**采用
  ``math.floor(x * 100 + 0.5) / 100`` 的直觉写法——它在刀刃输入上偏离
  ECMA ``toFixed(2)`` 语义，恰好落在参考真实输出可观察的位置（反例与裁定
  见 :func:`to_fixed_2` docstring）。
- **D5 ``$`` 锚语义**：Python ``re`` 的 ``$`` 在尾随换行前也匹配；JS 的
  ``$``（无 ``m`` 标志）只在串尾。本模块所有进入带 ``$`` 正则的文本均先经
  :func:`_normalize_value` 的 ``strip()``（无尾随换行），两端口径实际一致；
  保留 ``$`` 写法以便与参考逐字对照。

数据形状：item / group / pairwise 均为 plain dict，键名与参考 JS 对象逐字
一致（``from_tender`` / ``duplicate_group_ids`` / ``file_a_id`` 等）；参考的
``undefined`` 缺省键以 ``None`` 表达（``parent_id`` / ``matched_tender_sentence``
/ ``number``）。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

# ---------------------------------------------------------------------------
# 模块级正则（逐字移植；注释 = svc.cjs 参考行号）
# ---------------------------------------------------------------------------

# normalizeValue :140 的控制字符类
_CONTROL_CHARS_PATTERN = re.compile(r"[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\ufeff]")
# normalizeValue :141
_MULTI_WHITESPACE_PATTERN = re.compile(r"\s+")

# stripMarkdownForOutline :1136-1143
_MD_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_MD_EMPHASIS_PATTERN = re.compile(r"[`*_~]")
_ENTITY_NBSP_PATTERN = re.compile(r"&nbsp;", re.IGNORECASE)
_ENTITY_LT_PATTERN = re.compile(r"&lt;", re.IGNORECASE)
_ENTITY_GT_PATTERN = re.compile(r"&gt;", re.IGNORECASE)
_ENTITY_AMP_PATTERN = re.compile(r"&amp;", re.IGNORECASE)

# normalizeOutlineTitle :1148 / cleanOutlineTitle :1158
_HEADING_PREFIX_PATTERN = re.compile(r"^(?:#{1,6}\s*)")
# normalizeOutlineTitle :1149 / cleanOutlineTitle :1159
_LIST_PREFIX_PATTERN = re.compile(r"^[-*+>]\s*")
# normalizeOutlineTitle :1150 的编号前缀（含全角括号与圈号分支——NFKC 之后不可达，见 D2）
_NUMBERING_PREFIX_PATTERN = re.compile(
    r"^(?:第[一二三四五六七八九十百千万\d]+[章节篇部分]"
    r"|\d+(?:\.\d+)*[.)、．]?"
    r"|[一二三四五六七八九十]+[、.．]"
    r"|（[一二三四五六七八九十\d]+）"
    r"|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])[\s、.．-]*"
)
# normalizeOutlineTitle :1151（``\s`` 已含全角空格，``　`` 按参考原文保留）
_ALL_WHITESPACE_PATTERN = re.compile(r"[\s　]+")
# normalizeOutlineTitle :1152 的标点集（全角/半角并存，逐字）
_OUTLINE_PUNCTUATION_PATTERN = re.compile(
    r"""[，。！？；：、“”‘’'"《》〈〉（）()\[\]【】{}.,!?;:|/\\_-]+"""
)

# cleanOutlineTitle :1160 / parseCatalogLine :1231、:1235 的页尾点线
_PAGE_TRAIL_PATTERN = re.compile(r"(?:\.{2,}|…{2,}|·{2,}|\s{3,})\s*\d+\s*$")
# cleanOutlineTitle :1161 / parseCatalogLine :1235 的裸数字页尾
_PAGE_TAIL_NUMBER_PATTERN = re.compile(r"\s+\d{1,4}\s*$")

# splitTenderSentences :1167-1171
_LINE_BREAK_PATTERN = re.compile(r"\r?\n")
_TAB_SPACE_PATTERN = re.compile(r"[\t ]+")
_TENDER_SPLIT_PATTERN = re.compile(r"[。！？!?；;\n]+")

# parseOutlineMarker :1199-1203 的五种编号模式（``(?<name>)`` → ``(?P<name>)``，/u 语义 Python str 原生）
_OUTLINE_MARKER_PATTERNS = [
    re.compile(r"^(?P<number>\d+(?:\.\d+)*)(?:[.)、．])?\s*(?P<title>.+)$"),
    re.compile(r"^(?P<number>第[一二三四五六七八九十百千万\d]+[章节篇部分])\s*(?P<title>.*)$"),
    re.compile(r"^(?P<number>[一二三四五六七八九十]+[、.．])\s*(?P<title>.+)$"),
    re.compile(r"^(?P<number>（[一二三四五六七八九十\d]+）)\s*(?P<title>.+)$"),
    re.compile(r"^(?P<number>[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])\s*(?P<title>.+)$"),
]

# inferOutlineLevel :1218-1220
_DOTTED_NUMBER_PATTERN = re.compile(r"^\d+(?:\.\d+)+")
_LEADING_DIGITS_PATTERN = re.compile(r"^\d+")
_CHAPTER_PATTERN = re.compile(r"^第.+[章节篇部分]$")
_CJK_NUMBER_PATTERN = re.compile(r"^[一二三四五六七八九十]+[、.．]$")
_PAREN_NUMBER_PATTERN = re.compile(r"^（.+）$")
_CIRCLED_NUMBER_PATTERN = re.compile(r"^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]$")

# isCatalogTitleLine :1225（先剥全部空白再测，``\s*`` 实际匹配不到东西——逐字保留）
_CATALOG_TITLE_PATTERN = re.compile(r"^(?:#{1,6}\s*)?(目录|目次|contents)$", re.IGNORECASE)

# parseCatalogLine :1229-1231
_PIPE_EDGES_PATTERN = re.compile(r"^\|+|\|+$")
_PIPE_CHAR_PATTERN = re.compile(r"\|")
_SEPARATOR_LINE_PATTERN = re.compile(r"[-:|\s]+")
_SHORT_PAGE_TRAIL_PATTERN = re.compile(r"\s\d{1,4}$")

# extractHeadingOutline :1262
_HEADING_LINE_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$")

# extractSemanticOutline :1277-1279
_SENTENCE_END_PATTERN = re.compile(r"[。！？；;]$")
_TABLE_LINE_PATTERN = re.compile(r"^\|")
_BOLD_LINE_PATTERN = re.compile(r"^\s*\*\*.+\*\*\s*$")


# ---------------------------------------------------------------------------
# 上游 helper
# ---------------------------------------------------------------------------


def _normalize_value(value: Any) -> str:
    """对齐 svc.cjs:133-143 ``normalizeValue`` 的字符串路径。

    NFKC（:139）→ 控制字符剥除（:140）→ 空白压缩（:141）→ trim（:142）。
    **偏离 D3**：参考的 Date / Array / object 分支（:135-137）在本模块的
    调用点不可达（输入恒为 ``strip_markdown_for_outline`` 的字符串产物），
    不移植；``null``/``undefined`` → ``''`` 对应 ``None → ""``（:134）。

    注意 NFKC 先于所有编号/页尾正则执行——全角括号 ``（）``→``()``、圈号
    ``①..⑳``→``1..20``、省略号 ``…``→``...`` 等都会被归一，这是参考自身的
    行为（测试里 ``（一）概况``/``⑨ 注意事项`` 的地面真值即由此而来）。
    """
    if value is None:
        return ""
    text = str(value)
    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL_CHARS_PATTERN.sub("", text)
    text = _MULTI_WHITESPACE_PATTERN.sub(" ", text)
    return text.strip()


def to_fixed_2(value: float) -> float:
    """JS ``(x).toFixed(2)`` → ``Number`` 的对应物（R-P3b-7）。

    ECMA-262 的 ``toFixed(2)`` 对浮点数的**精确二进制十进制展开**做 half-up
    取两位（同为 n 时取较大者）。直觉写法 ``math.floor(x * 100 + 0.5) / 100``
    在刀刃输入上会偏离：

    - ``x = 0.615``（double 实为 ``0.61499999999999999112…``）：
      ``x * 100`` 先被舍入成**精确的** 61.5，再 half-up 得 **0.62**；
      ``toFixed`` 按精确展开 ``0.61499…`` 舍入得 **0.61**。
    - 参考真实可观察路径 ``(0.68 + 0.55 + 0.68 + 0.55) / 4`` 恰好落在该刀刃上，
      Node 实测参考实现（``buildOutlineItems`` :1325 的 confidence 平均）输出
      **0.61**。

    故本实现用 ``Decimal(value)``（float 的精确十进制值）+ ``ROUND_HALF_UP``
    复刻 ECMA 语义：``0.615 → 0.61``、``0.125 → 0.13``（真 half-up，Python
    内建 ``round()`` 是 half-even 会给 0.12）、``1/3 → 0.33``，与 Node 全部
    一致。这偏离了 P3b 计划 R-P3b-7 建议的 ``math.floor(x*100 + 0.5)`` 写法，
    但符合该裁定的本意（half-up 对齐 JS ``toFixed``）与计划总纲的「行为对齐」
    验收标准；已在任务报告显著登记。输入恒 ≥0（score / confidence 均非负），
    不处理负数方向。
    """
    return float(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------------------
# :1134-1163 抽取前置清洗
# ---------------------------------------------------------------------------


def strip_markdown_for_outline(markdown: str) -> str:
    """剥除 markdown 噪声（:1134-1144）：图片→空格、链接保文字、HTML 标签→
    空格、`` `*_~ `` 剥除、HTML 实体 nbsp/lt/gt/amp（大小写不敏感）。"""
    value = markdown or ""
    value = _MD_IMAGE_PATTERN.sub(" ", value)        # :1136 图片 → 空格
    value = _MD_LINK_PATTERN.sub(r"\1", value)       # :1137 链接保文字
    value = _HTML_TAG_PATTERN.sub(" ", value)        # :1138 HTML 标签 → 空格
    value = _MD_EMPHASIS_PATTERN.sub("", value)      # :1139 强调记号剥除
    value = _ENTITY_NBSP_PATTERN.sub(" ", value)     # :1140
    value = _ENTITY_LT_PATTERN.sub("<", value)       # :1141
    value = _ENTITY_GT_PATTERN.sub(">", value)       # :1142
    value = _ENTITY_AMP_PATTERN.sub("&", value)      # :1143（amp 在最后）
    return value


def normalize_outline_title(value: str) -> str:
    """标题归一化（:1146-1154）：stripMarkdown → NFKC/空白清洗 → 依次剥
    ``#{1,6}``、列表前缀、编号前缀（:1150）→ 去全部空白（:1151）→ 去标点集
    （:1152）→ lower。用于 normalized_title / 匹配键。"""
    text = _normalize_value(strip_markdown_for_outline(value))   # :1147
    text = _HEADING_PREFIX_PATTERN.sub("", text, count=1)        # :1148
    text = _LIST_PREFIX_PATTERN.sub("", text, count=1)           # :1149
    text = _NUMBERING_PREFIX_PATTERN.sub("", text, count=1)      # :1150
    text = _ALL_WHITESPACE_PATTERN.sub("", text)                 # :1151
    text = _OUTLINE_PUNCTUATION_PATTERN.sub("", text)            # :1152
    return text.lower()                                          # :1153


def clean_outline_title(value: str) -> str:
    """标题清洗（:1156-1163）：stripMarkdown → NFKC/空白清洗 → 剥 markdown /
    编号前缀（:1158-1159）→ 剥页尾点线与裸数字页尾（:1160-1161）→ trim。
    **保留原始大小写与词内空白**（与 normalize_outline_title 的差异点）。"""
    text = _normalize_value(strip_markdown_for_outline(value))   # :1157
    text = _HEADING_PREFIX_PATTERN.sub("", text, count=1)        # :1158
    text = _LIST_PREFIX_PATTERN.sub("", text, count=1)           # :1159
    text = _PAGE_TRAIL_PATTERN.sub("", text)                     # :1160
    text = _PAGE_TAIL_NUMBER_PATTERN.sub("", text)               # :1161
    return text.strip()                                          # :1162


# ---------------------------------------------------------------------------
# :1165-1222 招标句白名单与编号解析
# ---------------------------------------------------------------------------


def split_tender_sentences(markdown: str) -> list[dict[str, str]]:
    """招标句白名单（:1165-1183）：stripMarkdown → ``|`` 换行 → 按
    ``[。！？!?；;\\n]+`` 切 → clean_outline_title 洗 → normalized 长度窗
    ``[6, 160]`` + 去重 → ``[{text, normalized}]``。"""
    text = strip_markdown_for_outline(markdown)                  # :1166
    text = _PIPE_CHAR_PATTERN.sub("\n", text)                    # :1167
    text = _LINE_BREAK_PATTERN.sub("\n", text)                   # :1168
    text = _TAB_SPACE_PATTERN.sub(" ", text)                     # :1169
    parts = [clean_outline_title(part) for part in _TENDER_SPLIT_PATTERN.split(text)]  # :1170-1172
    seen: set[str] = set()                                       # :1174
    sentences: list[dict[str, str]] = []                         # :1175
    for part in parts:                                           # :1176（filter(Boolean) 内联在判据里）
        if not part:
            continue
        normalized = normalize_outline_title(part)               # :1177
        # :1178 长度窗 [6, 160]（码点语义，D1）+ 去重
        if len(normalized) < 6 or len(normalized) > 160 or normalized in seen:
            continue
        seen.add(normalized)                                     # :1179
        sentences.append({"text": part, "normalized": normalized})  # :1180
    return sentences                                             # :1182


def match_tender_sentence(
    title: str, tender_sentences: Sequence[Mapping[str, str]]
) -> Mapping[str, str] | None:
    """招标句匹配（:1185-1194）：相等命中；本方 normalized ≥10 且被招标句
    包含命中；招标句 ≥10 且包含本方且长度比 ≥0.8 命中；``<6`` 直接拒。
    命中返回招标句条目本身（参考返回对象引用）。"""
    normalized = normalize_outline_title(title)                  # :1186
    if len(normalized) < 6:                                      # :1187
        return None
    for sentence in tender_sentences:                            # :1188
        if sentence["normalized"] == normalized:                 # :1189
            return sentence
        # :1190 本方 ≥10 且被包含
        if len(normalized) >= 10 and normalized in sentence["normalized"]:
            return sentence
        # :1191 对方 ≥10 且包含本方且长度比 ≥0.8
        if (
            len(sentence["normalized"]) >= 10
            and sentence["normalized"] in normalized
            and len(sentence["normalized"]) / len(normalized) >= 0.8
        ):
            return sentence
    return None                                                  # :1193


def parse_outline_marker(line: str) -> dict[str, Any] | None:
    """五种编号模式解析（:1196-1214）；title 清洗后 normalized <2 拒。
    返回 ``{number, title, level}`` 或 None。

    注意：输入先经 ``clean_outline_title``（内含 NFKC），全角括号/圈号已被
    归一，:1202/:1203 两个分支在真实管线里不可达（圈号由 :1199 的数字分支
    以 ``9`` 之类命中）——Node 地面真值证实，测试钉住该行为。"""
    text = clean_outline_title(line)                             # :1197
    for pattern in _OUTLINE_MARKER_PATTERNS:                     # :1205
        match = pattern.match(text)                              # :1206
        if match is None:                                        # :1207
            continue
        number = match.group("number").strip()                   # :1208
        title = clean_outline_title(match.group("title") or number)  # :1209
        if not title or len(normalize_outline_title(title)) < 2:  # :1210
            continue
        return {"number": number, "title": title, "level": infer_outline_level(number)}  # :1211
    return None                                                  # :1213


def infer_outline_level(number: str) -> int:
    """编号推断层级（:1216-1222）：``1.2.3`` → 点分段数；纯数字 / ``第X章`` /
    ``一、`` → 1；``（一）`` / 圈号 → 2；兜底 1。（直调不做 NFKC——与参考一致。）"""
    marker = str(number or "").strip()                           # :1217
    if _DOTTED_NUMBER_PATTERN.match(marker):                     # :1218
        return len([part for part in marker.split(".") if part])
    if (
        _LEADING_DIGITS_PATTERN.match(marker)
        or _CHAPTER_PATTERN.match(marker)
        or _CJK_NUMBER_PATTERN.match(marker)
    ):                                                           # :1219
        return 1
    if _PAREN_NUMBER_PATTERN.match(marker) or _CIRCLED_NUMBER_PATTERN.match(marker):  # :1220
        return 2
    return 1                                                     # :1221


# ---------------------------------------------------------------------------
# :1224-1284 目录行解析与三来源抽取
# ---------------------------------------------------------------------------


def is_catalog_title_line(line: str) -> bool:
    """是否目录标题行（:1224-1226）：去全部空白后
    ``^(?:#{1,6}\\s*)?(目录|目次|contents)$``（忽略大小写）。"""
    stripped = re.sub(r"\s+", "", line or "")                    # :1225
    return _CATALOG_TITLE_PATTERN.match(stripped) is not None


def parse_catalog_line(line: str) -> dict[str, Any] | None:
    """目录行解析（:1228-1237）：剥首尾 ``|``、``|``→空格 → clean；分隔行 /
    目录行拒；有编号走 marker；有页尾（hasPageTrail）才收无编号行
    ``{title, level: 1}``。"""
    raw = clean_outline_title(
        _PIPE_CHAR_PATTERN.sub(" ", _PIPE_EDGES_PATTERN.sub("", line or ""))  # :1229
    )
    if not raw or _SEPARATOR_LINE_PATTERN.fullmatch(raw) or is_catalog_title_line(raw):  # :1230
        return None
    has_page_trail = bool(_PAGE_TRAIL_PATTERN.search(raw)) or bool(
        _SHORT_PAGE_TRAIL_PATTERN.search(raw)
    )                                                                           # :1231
    marker = parse_outline_marker(raw)                           # :1232
    if marker:                                                   # :1233
        return marker
    if not has_page_trail:                                       # :1234
        return None
    title = clean_outline_title(
        _PAGE_TAIL_NUMBER_PATTERN.sub("", _PAGE_TRAIL_PATTERN.sub("", raw))   # :1235
    )
    if title and len(normalize_outline_title(title)) >= 2:       # :1236
        return {"title": title, "level": 1}
    return None


def extract_catalog_outline(markdown: str) -> list[dict[str, Any]]:
    """目录段抽取（:1239-1256）：找 ``目录`` 行起；窗口 180 行；有 items 后
    连续 10 个未命中断开；confidence **0.92**；``source: 'catalog'``。"""
    lines = _LINE_BREAK_PATTERN.split(markdown or "")            # :1240
    start = next(
        (index for index, line in enumerate(lines) if is_catalog_title_line(line)), -1
    )                                                            # :1241
    if start < 0:                                                # :1242
        return []
    items: list[dict[str, Any]] = []                             # :1243
    misses = 0                                                   # :1244
    for index in range(start + 1, min(len(lines), start + 180)):  # :1245
        parsed = parse_catalog_line(lines[index])                # :1246
        if not parsed:                                           # :1247
            if items:                                            # :1248
                misses += 1
            if misses >= 10:                                     # :1249
                break
            continue
        misses = 0                                               # :1252
        items.append({**parsed, "source": "catalog", "confidence": 0.92})  # :1253
    return items                                                 # :1255


def extract_heading_outline(markdown: str) -> list[dict[str, Any]]:
    """标题抽取（:1258-1270）：``^(#{1,6})\\s+(.+)$``；catalog 标题行拒；
    ``level = min(井号数, 6)``；confidence **0.82**。"""
    items: list[dict[str, Any]] = []                             # :1259
    lines = _LINE_BREAK_PATTERN.split(markdown or "")            # :1260
    for line in lines:                                           # :1261
        match = _HEADING_LINE_PATTERN.match(line)                # :1262
        if match is None:
            continue
        title = clean_outline_title(match.group(2))              # :1264
        if not title or is_catalog_title_line(title):            # :1265
            continue
        marker = parse_outline_marker(title)                     # :1266
        items.append(
            {
                "number": marker["number"] if marker else None,
                "title": (marker["title"] or "") and marker["title"] or title if marker else title,
                "level": min(len(match.group(1)), 6),
                "source": "heading",
                "confidence": 0.82,
            }
        )                                                        # :1267
    return items                                                 # :1269


def extract_semantic_outline(markdown: str) -> list[dict[str, Any]]:
    """语义行抽取（:1272-1284）：每行 ≤90 字（码点，D1）、非句尾标点、非表行；
    编号行 confidence **0.68**、粗体行 **0.55**、``level = marker.level or 2``；
    截断 **260**。粗体判据测**原始行**（:1279）。"""
    items: list[dict[str, Any]] = []                             # :1273
    lines = _LINE_BREAK_PATTERN.split(markdown or "")            # :1274
    for line in lines:                                           # :1275
        text = clean_outline_title(line)                         # :1276
        if (
            not text
            or len(text) > 90                                    # :1277（码点语义，D1）
            or _SENTENCE_END_PATTERN.search(text)
            or _TABLE_LINE_PATTERN.match(text)
            or is_catalog_title_line(text)
        ):
            continue
        marker = parse_outline_marker(text)                      # :1278
        bold = _BOLD_LINE_PATTERN.match(line) is not None        # :1279
        if not marker and not bold:                              # :1280
            continue
        items.append(
            {
                "number": marker["number"] if marker else None,
                "title": marker["title"] if marker and marker["title"] else text,
                "level": marker["level"] if marker and marker["level"] else 2,
                "source": "semantic",
                "confidence": 0.68 if marker else 0.55,
            }
        )                                                        # :1281（marker?.title || title、marker?.level || 2）
    return items[:260]                                           # :1283


# ---------------------------------------------------------------------------
# :1286-1366 汇总与相似度基元
# ---------------------------------------------------------------------------


def build_outline_items(
    markdown: str, tender_sentences: Sequence[Mapping[str, str]] = ()
) -> dict[str, Any]:
    """三来源汇总（:1286-1326）：catalog / heading / semantic 依次取
    ``len≥3`` 的首个来源，否则取首个非空来源；level 夹取 ``[1,6]`` 且
    ``level > stack+1 → stack+1``；去重键 ``level:normalized``；
    ``id = O{序号:05d}``；``from_tender`` / ``matched_tender_sentence`` 来自
    match_tender_sentence；confidence 为来源内平均（half-up 2 位）。"""
    candidates = [
        extract_catalog_outline(markdown),                       # :1287
        extract_heading_outline(markdown),
        extract_semantic_outline(markdown),
    ]
    selected = next((c for c in candidates if len(c) >= 3), None) or next(
        (c for c in candidates if c), []
    ) or []                                                      # :1288
    stack: list[dict[str, Any]] = []                             # :1289
    items: list[dict[str, Any]] = []                             # :1290
    seen: set[str] = set()                                       # :1291
    for candidate in selected:                                   # :1292
        raw_level = candidate.get("level")
        level = max(1, min(int(raw_level) if raw_level else 1, 6))        # :1293
        if level > len(stack) + 1:                               # :1294
            level = len(stack) + 1
        title = clean_outline_title(candidate.get("title") or "")          # :1295
        normalized = normalize_outline_title(title)              # :1296
        if not title or len(normalized) < 2:                     # :1297
            continue
        key = f"{level}:{normalized}"                            # :1298
        if key in seen:                                          # :1299
            continue
        seen.add(key)                                            # :1300
        # :1301 splice(level-1)：截断后恒有 len(stack) == level-1（:1294 保证
        # level ≤ 原长+1），因此 :1323 的赋值恒为「在 level-1 处追加」。
        del stack[level - 1:]
        # :1302 JS stack[-1] 是 undefined → None；Python 需显式防负索引。
        parent = stack[level - 2] if level >= 2 and level - 2 < len(stack) else None
        path_titles = [*(parent["path_titles"] if parent else []), title]  # :1303
        matched = match_tender_sentence(title, tender_sentences)           # :1304
        item = {
            "id": f"O{len(items) + 1:05d}",                      # :1306
            "level": level,
            "number": candidate.get("number"),
            "title": title,
            "normalized_title": normalized,
            "path_titles": path_titles,
            "normalized_path": ">".join(
                t for t in (normalize_outline_title(p) for p in path_titles) if t
            ),                                                   # :1312
            "source": candidate.get("source"),
            "confidence": candidate.get("confidence"),
            "order": len(items),                                 # :1315
            "parent_id": parent["id"] if parent else None,       # :1316
            "from_tender": bool(matched),                        # :1317
            "matched_tender_sentence": matched["text"] if matched else None,  # :1318
            "duplicate_group_ids": [],                           # :1319
            "similar_group_ids": [],                             # :1320
        }
        items.append(item)                                       # :1322
        stack.append(item)                                       # :1323（见 :1301 注释）
    return {
        "items": items,
        "source": selected[0].get("source") if selected else None,          # :1325
        "confidence": (
            to_fixed_2(sum(c["confidence"] for c in selected) / len(selected))
            if selected
            else 0
        ),
    }


def intersect_size(items: Iterable[Any], pool: Iterable[Any]) -> int:
    """交集计数（:1328-1332）：遍历 ``items``，命中 ``pool`` 即计数
    （pairwise 处两个实参都是集合）。"""
    count = 0                                                    # :1329
    for item in items:                                           # :1330
        if item in pool:
            count += 1
    return count                                                 # :1331


def bigram_similarity(left_value: str, right_value: str) -> float:
    """相邻字符对集合的 Dice 相似度（:1334-1348）。

    **码点语义两端一致（R-P3b-5 例外）**：参考 ``Array.from``（:1340）按码点
    迭代，Python ``list(str)`` 天然同语义——astral 字符亦无偏离。
    相等串短路 1（:1338）；空串 0（:1337）；分母 ``+right.size || 1``（:1347）。"""
    left = str(left_value or "")                                 # :1335
    right = str(right_value or "")                               # :1336
    if not left or not right:                                    # :1337
        return 0
    if left == right:                                            # :1338
        return 1

    def to_bigrams(value: str) -> set[str]:                      # :1339
        chars = list(value)                                      # :1340 Array.from —— 码点
        if len(chars) <= 1:                                      # :1341
            return set(chars)
        return {f"{chars[i]}{chars[i + 1]}" for i in range(len(chars) - 1)}  # :1342

    left_set = to_bigrams(left)                                  # :1344
    right_set = to_bigrams(right)                                # :1345
    shared = intersect_size(left_set, right_set)                 # :1346
    denominator = len(left_set) + len(right_set)
    return 2 * shared / (denominator if denominator else 1)      # :1347


def lcs_similarity(left: Sequence[Any], right: Sequence[Any]) -> float:
    """最长公共子序列相似度（:1350-1359）：``lcs / max(len)``；空串 0。
    pairwise 实参是 normalized_title **列表**（:1435），对列表与字符串皆可用。"""
    if not len(left) or not len(right):                          # :1351
        return 0
    dp = [[0] * (len(right) + 1) for _ in range(len(left) + 1)]  # :1352
    for i in range(1, len(left) + 1):                            # :1353
        for j in range(1, len(right) + 1):                       # :1354
            if left[i - 1] == right[j - 1]:                      # :1355
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[len(left)][len(right)] / max(len(left), len(right))  # :1358


def risk_from_score(score: float) -> str:
    """分数 → 风险档（:1361-1366，逐字阈值）：≥0.75 high / ≥0.55 medium /
    ≥0.35 low / else none。"""
    if score >= 0.75:                                            # :1362
        return "high"
    if score >= 0.55:                                            # :1363
        return "medium"
    if score >= 0.35:                                            # :1364
        return "low"
    return "none"                                                # :1365


# ---------------------------------------------------------------------------
# :1368-1451 跨文件比对
# ---------------------------------------------------------------------------


def build_outline_comparison(files: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """跨文件目录比对（:1368-1451）：

    1. 只吃 ``status == 'success'`` 的文件结果；跳过 ``from_tender`` 项；
    2. ``by_path``（normalized_path）精确组 → ``type='duplicate', score=1``；
       ``by_title``（normalized_title）组里**尚无 duplicate 组的**
       （``alreadyGrouped`` = 每个条目都已有 duplicate 组）→ ``score=0.95``；
       组 ``file_ids < 2`` 跳过；``id = G{序号:04d}``；回写
       ``item.duplicate_group_ids``；排序 ``(-score, -len(file_ids))``；
    3. similar：两两文件 × 未分组项 × level 差 ≤1 × bigram ≥ **0.86**（逐字）；
       key 去重；``score = toFixed(2)``；
    4. pairwise：``titleOverlap = titleShared / min(两侧数, 1)``、pathOverlap
       同式、``orderSimilarity = lcs(normalized_title 列表)``；
       ``score = toFixed(0.45*path + 0.35*title + 0.2*order)``（逐字权重）；
       ``shared_count = max(titleShared, pathShared)``；``risk = riskFromScore``。
    """
    groups: list[dict[str, Any]] = []                            # :1369
    by_title: dict[str, list[dict[str, Any]]] = {}               # :1370
    by_path: dict[str, list[dict[str, Any]]] = {}                # :1371
    successful = [f for f in files if f.get("status") == "success"]  # :1372
    for file in successful:                                      # :1373
        for item in file.get("items") or []:                     # :1374
            if item.get("from_tender"):                          # :1375
                continue
            # :1376-1378 / :1379-1381（dict 保插入序 = JS Map 序）
            by_title.setdefault(item["normalized_title"], []).append({"file": file, "item": item})
            by_path.setdefault(item["normalized_path"], []).append({"file": file, "item": item})

    def add_group(
        group_type: str, entries: list[dict[str, Any]], title: str, score: float
    ) -> dict[str, Any] | None:                                  # :1385
        # :1386 Array.from(new Set(...))：保首次出现序去重
        file_ids = list(dict.fromkeys(entry["file"]["file_id"] for entry in entries))
        if len(file_ids) < 2:                                    # :1387
            return None
        group_id = f"G{len(groups) + 1:04d}"                     # :1388
        group: dict[str, Any] = {
            "id": group_id,
            "type": group_type,
            "title": title,
            "score": score,
            "file_ids": file_ids,
            "item_ids": {},
            "paths": {},
        }                                                        # :1389
        for entry in entries:                                    # :1390
            file_id = entry["file"]["file_id"]
            group["item_ids"][file_id] = [
                *(group["item_ids"].get(file_id) or []),
                entry["item"]["id"],
            ]                                                    # :1391
            group["paths"][file_id] = [
                *(group["paths"].get(file_id) or []),
                " > ".join(entry["item"]["path_titles"]),
            ]                                                    # :1392
            if group_type == "duplicate":                        # :1393
                entry["item"]["duplicate_group_ids"].append(group_id)
            else:
                entry["item"]["similar_group_ids"].append(group_id)
        groups.append(group)                                     # :1396
        return group

    for entries in by_path.values():                             # :1400
        first_item = entries[0]["item"]
        add_group(
            "duplicate",
            entries,
            " > ".join(first_item["path_titles"]) or first_item["title"] or "",
            1,
        )
    for entries in by_title.values():                            # :1401
        # :1402 alreadyGrouped = 每个条目都已入 duplicate 组
        already_grouped = all(entry["item"]["duplicate_group_ids"] for entry in entries)
        if not already_grouped:                                  # :1403
            add_group("duplicate", entries, entries[0]["item"]["title"] or "", 0.95)

    seen_similar: set[str] = set()                               # :1406
    for i in range(len(successful)):                             # :1407
        for j in range(i + 1, len(successful)):                  # :1408
            left_candidates = [
                item
                for item in (successful[i].get("items") or [])
                if not item.get("from_tender") and not item["duplicate_group_ids"]
            ]                                                    # :1409
            for left in left_candidates:
                right_candidates = [
                    item
                    for item in (successful[j].get("items") or [])
                    if (
                        not item.get("from_tender")
                        and not item["duplicate_group_ids"]
                        and abs(item["level"] - left["level"]) <= 1
                    )
                ]                                                # :1410
                for right in right_candidates:
                    score = bigram_similarity(
                        left["normalized_title"], right["normalized_title"]
                    )                                            # :1411
                    if score < 0.86:                             # :1412（逐字阈值）
                        continue
                    key = ":".join(
                        [successful[i]["file_id"], left["id"], successful[j]["file_id"], right["id"]]
                    )                                            # :1413
                    if key in seen_similar:                      # :1414
                        continue
                    seen_similar.add(key)
                    add_group(
                        "similar",
                        [
                            {"file": successful[i], "item": left},
                            {"file": successful[j], "item": right},
                        ],
                        left["title"],
                        to_fixed_2(score),
                    )                                            # :1416

    pairwise_similarities: list[dict[str, Any]] = []             # :1422
    for i in range(len(successful)):                             # :1423
        for j in range(i + 1, len(successful)):                  # :1424
            left_items = [
                item
                for item in (successful[i].get("items") or [])
                if not item.get("from_tender")
            ]                                                    # :1425
            right_items = [
                item
                for item in (successful[j].get("items") or [])
                if not item.get("from_tender")
            ]                                                    # :1426
            left_titles = {item["normalized_title"] for item in left_items}    # :1427
            right_titles = {item["normalized_title"] for item in right_items}  # :1428
            left_paths = {item["normalized_path"] for item in left_items}      # :1429
            right_paths = {item["normalized_path"] for item in right_items}    # :1430
            title_shared = intersect_size(left_titles, right_titles)           # :1431
            path_shared = intersect_size(left_paths, right_paths)              # :1432
            title_overlap = title_shared / max(min(len(left_titles), len(right_titles)), 1)  # :1433
            path_overlap = path_shared / max(min(len(left_paths), len(right_paths)), 1)      # :1434
            order_similarity = lcs_similarity(
                [item["normalized_title"] for item in left_items],
                [item["normalized_title"] for item in right_items],
            )                                                                    # :1435
            score = to_fixed_2(
                path_overlap * 0.45 + title_overlap * 0.35 + order_similarity * 0.2
            )                                                                    # :1436（逐字权重）
            pairwise_similarities.append(
                {
                    "file_a_id": successful[i]["file_id"],       # :1438
                    "file_b_id": successful[j]["file_id"],       # :1439
                    "score": score,                              # :1440
                    "title_overlap": to_fixed_2(title_overlap),  # :1441
                    "path_overlap": to_fixed_2(path_overlap),    # :1442
                    "order_similarity": to_fixed_2(order_similarity),  # :1443
                    "shared_count": max(title_shared, path_shared),    # :1444
                    "risk": risk_from_score(score),              # :1445
                }
            )

    return {
        # :1450 b.score - a.score || b.file_ids.length - a.file_ids.length
        "duplicateGroups": sorted(
            groups, key=lambda group: (-group["score"], -len(group["file_ids"]))
        ),
        "pairwiseSimilarities": pairwise_similarities,           # :1450
    }
