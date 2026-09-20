"""P3b Task 3 —— 招标文本五级匹配器与重复句汇总。

逐字移植参考 `client/electron/services/duplicateCheckService.cjs`
:1756-2010（``normalizeTenderComparableText`` → ``buildDuplicateSentences``）。

五级短路匹配顺序（:1980-1996，**逐字**）：

    exact → strict（strictKey 长度 ≥3）→ field（allow + 命中 fieldSet +
    isSafeTenderFieldTail）→ skeleton（allowed + 命中 skeletonSet）→ near。

数据形状：``match`` 返回 plain dict（``{"reason": "exact"}`` 或
``{"reason": "near", "dice", "containment", "tender"}``），键名与参考 JS
逐字一致；``build_duplicate_sentences`` 的条目在输入 item 基础上加 ``id``。

偏离登记（与 Task 1/2 的 D 编号口径一致）：

- **D1 码点语义（R-P3b-5）**：``char_bigrams_from_loose_text`` 用 ``list(str)``
  按码点切（参考 :1896-1903 用 ``String.slice`` 按 UTF-16 code unit）；两端仅对
  astral 字符（emoji 等）有差，标书正文极罕见，登记不做 UTF-16 仿真。
- **D2 ``\\d``/``\\b``/``\\w`` ASCII 语义（对齐 JS，非偏离）**：JS 的 ``\\d``
  只匹配 ASCII ``[0-9]``、``\\w`` 只 ``[A-Za-z0-9_]``、``\\b`` 按 ASCII 单词
  边界；Python ``re`` 默认 Unicode 语义（``\\d`` 匹配 Unicode 十进制数字、``\\w``
  含汉字、``\\b`` 按 Unicode 边界）。**本模块所有含 ``\\d``/``\\b``/``\\w``
  的正则一律加 ``re.ASCII``** 以对齐 JS——否则 ``\\b\\d+\\b`` 对「2026年」会因
  「年」在 Python 里是 ``\\w`` 而匹配失败（比对结果将偏离参考）。
- **D3 参考硬编码专名**：:func:`is_safe_tender_field_tail` 的
  ``天津港保税区消防救援支队 | 消防装备管理系统项目 | 天津众信招标咨询有限公司``
  是参考 :1893 写死的项目专名，**逐字保留、刻意不"修复"**（已知空白 3）。
- **D4 参考缺陷：skeleton 级事实不可达（逐字复刻，不"修复"）**：参考
  :1839 的标点剥除正则把 ``{}`` 也纳入字符集，导致 :1828-1836 生成的
  ``{date}/{money}/{num}/…`` 占位符花括号在最后一步被剥掉（``{money}``→``money``）；
  而 :1847 的守卫 ``!/[{}]/`` 因此恒成立，:func:`is_tender_skeleton_allowed`
  **恒返回 False**，五级匹配的 skeleton 级实际不可达。此外 :1828 的
  ``\\b…年…月…日\\b`` 日期占位在中文语境因 ``\\b`` 不认中文边界也基本不命中
  （日期数字被裸数字正则捕成 ``{num}``）。本移植按「行为对齐」原则逐字复刻
  这些缺陷——比对结果必须与参考一致，不"修复"。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from app.domain.duplicate.content import normalize_content_sentence

# ---------------------------------------------------------------------------
# 模块级正则（:1763-1773 等的编译形式；flag 按逐字符裁定，见各条注释）
# ---------------------------------------------------------------------------

# 转义符剥除 :1759 —— 无 i 需求、无 . 跨行，逐字
_ESCAPED_META_PATTERN = re.compile(r"\\([\[\]().{}<>#+=\-])")
# 连字符类归一 :1760 —— 逐字字符集
_HYPHEN_CLASS_PATTERN = re.compile(r"[‐‑‒–—―]")
# 弯引号→直引号 :1761-1762
_CURLY_DQUOTE_PATTERN = re.compile(r"[“”]")
_CURLY_SQUOTE_PATTERN = re.compile(r"[‘’]")
# 全角标点→半角 :1763-1768（逐字符）
_FULL_PAREN_L_PATTERN = re.compile(r"（")
_FULL_PAREN_R_PATTERN = re.compile(r"）")
_FULL_COMMA_PATTERN = re.compile(r"，")
_FULL_PERIOD_PATTERN = re.compile(r"。")
_FULL_SEMI_PATTERN = re.compile(r"；")
_FULL_COLON_PATTERN = re.compile(r"：")
# 比较词归一 :1769-1770（逐字；含中文词，无大小写 flag 需求）
_GE_PATTERN = re.compile(r"≥|大于等于|不低于|不少于")
_LE_PATTERN = re.compile(r"≤|小于等于|不高于|不超过")
# N×N 归一 :1771
_TIMES_PATTERN = re.compile(r"(\d)\s*[xX×]\s*(\d)", re.ASCII)
# 日期两形态 :1772-1773（逐字；re.ASCII 对齐 JS \d 的 ASCII 语义与 \b 的 ASCII 边界）
_DATE_CN_PATTERN = re.compile(r"(\d{4})\s*年\s*0?(\d{1,2})\s*月\s*0?(\d{1,2})\s*日", re.ASCII)
_DATE_SEP_PATTERN = re.compile(r"\b(\d{4})[-/.](0?\d{1,2})[-/.](0?\d{1,2})\b", re.ASCII)
# 空白压缩 :1774（含全角空格）
_WS_FULL_PATTERN = re.compile(r"[\s　]+")

# 表前缀剥 :1784 —— 动态前缀 + 可选序号；`i` flag → re.I（无 . 跨行）
_TABLE_PREFIXES = ["技术要求", "招标要求", "评分标准", "评标标准", "投标应答", "偏离说明"]

# 目录页尾 :1803 关键词、:1805-1808 四条剥尾
_DIRECTORY_HINT_PATTERN = re.compile(r"(目录|页码|检索|评分因素|评标标准|评分标准)")
_PAGE_CN_TAIL_PATTERN = re.compile(r"\s*(?:第\s*)?\d{1,4}\s*页\s*$", re.I | re.ASCII)
_PAGE_P_TAIL_PATTERN = re.compile(r"\s*P\s*\d{1,4}(?:\s*[-~至]\s*P?\s*\d{1,4})?\s*$", re.I | re.ASCII)
_PAGE_DOTS_TAIL_PATTERN = re.compile(r"(?:\.{2,}|…{2,}|·{2,}|\s{2,})\s*\d{1,4}\s*$", re.ASCII)
_PAGE_NUM_TAIL_PATTERN = re.compile(r"\s+\d{1,4}\s*$", re.ASCII)

# strict/loose key 的空白 + 标点剥除 :1814-1816 / :1821-1823（逐字字符集）
_STRICT_WS_PATTERN = re.compile(r"[\s　]+")
_STRICT_PUNCT_PATTERN = re.compile(r"[.,，。;；:：、!！?？\"'“”‘’《》<>〈〉()[\]【】{}]")

# skeleton 占位符 :1828-1836（**替换顺序逐字**：date → date-sep → code → 万元 → 元 → % → 分 → P页码 → 裸数字）
_SKELETON_DATE_PATTERN = re.compile(r"\b\d{4}年\d{1,2}月\d{1,2}日\b", re.ASCII)
_SKELETON_DATE_SEP_PATTERN = re.compile(r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b", re.ASCII)
_SKELETON_CODE_PATTERN = re.compile(r"\b[A-Z]{2,}[-A-Z0-9]{4,}\b", re.I | re.ASCII)
_SKELETON_WAN_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*万元", re.ASCII)
_SKELETON_YUAN_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*元", re.ASCII)
_SKELETON_PERCENT_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*%", re.ASCII)
_SKELETON_SCORE_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*分", re.ASCII)
_SKELETON_PAGE_PATTERN = re.compile(r"P\s*\d+(?:\s*[-~至]\s*P?\s*\d+)?", re.I | re.ASCII)
_SKELETON_NUM_PATTERN = re.compile(r"\b\d+(?:\.\d+)?\b", re.ASCII)

# skeleton allowed 评分类词 :1849（逐字）
_SKELETON_SCORE_HINT_PATTERN = re.compile(r"(评分|评标|分值|计分|内容无瑕疵|内容存在|页码检索|合同复印件|技术要求|招标要求)")

# field 黑/白名单 :1852-1853（逐字）
_TENDER_FIELD_DENY_PATTERN = re.compile(
    r"(供应商名称|供应商地址|法定代表人|供应商代表|授权代表|被授权人|委托代理人|联系人|联系电话|电话|手机|邮政编码|邮箱|电子邮箱|开户|账号|银行|报价|投标报价|投标总价|合同金额|金额|总价)"
)
_TENDER_FIELD_ALLOW_PATTERN = re.compile(
    r"^(投标日期|日期|项目名称|项目编号|采购人|采购代理机构|评分因素及评标标准页码检索|投标文件总目录|目录|附件\d*|投标书|开标一览表|报价分项一览表|投标产品配置清单|商务要求点对点应答表|技术要求点对点应答表|主要相关业绩一览表|政府采购政策情况表|中小微企业声明函|非残疾人福利性单位声明函)$"
)

# field 名归一 :1856-1860
_FIELD_WS_PATTERN = re.compile(r"[\s　]+")
_FIELD_COLON_TAIL_PATTERN = re.compile(r"[：:]+$")
_FIELD_BRACKET_PATTERN = re.compile(r"[()（）【】\[\]《》]")

# 评分因素特例 :1866
_SCORE_RETRIEVAL_PATTERN = re.compile(
    r"^评分因素及评标标准页码检索(?:\s+\d{1,4}|\s+P?\d{1,4}(?:[-~至]P?\d{1,4})?)?$", re.I
)

# safe tail :1889-1892
_SAFE_TAIL_DATE_PATTERN = re.compile(r"^(?:\d{4}年\d{1,2}月\d{1,2}日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})$", re.ASCII)
_SAFE_TAIL_PAGE_PATTERN = re.compile(r"^(?:第\s*)?\d{1,4}\s*页?$", re.ASCII)
_SAFE_TAIL_P_PATTERN = re.compile(r"^P\s*\d{1,4}(?:\s*[-~至]\s*P?\s*\d{1,4})?$", re.I | re.ASCII)
_SAFE_TAIL_CODE_PATTERN = re.compile(r"^[A-Z0-9-]{4,}$", re.I | re.ASCII)
# :1893 参考硬编码专名（D3，逐字保留）
_SAFE_TAIL_PROJECT_PATTERN = re.compile(r"(天津港保税区消防救援支队|消防装备管理系统项目|天津众信招标咨询有限公司)")

# near 门 :1911
_NEAR_HINT_PATTERN = re.compile(r"(评分|评标|页码|投标日期|日期|技术要求|招标要求)")


# ---------------------------------------------------------------------------
# :1756-1780 normalizeTenderComparableText
# ---------------------------------------------------------------------------


def normalize_tender_comparable_text(value: str) -> str:
    """招标可比文本归一化（:1756-1780）：normalizeContentSentence → NFKC →
    转义符剥 → 连字符归一 → 弯引号 → 全角标点 → 比较词 → N×N → 日期两形态 →
    空白压缩 → stripTenderTablePrefix → stripTenderDirectoryPageTail。"""
    text = normalize_content_sentence(value)
    text = _nfkc(text)
    text = _ESCAPED_META_PATTERN.sub(r"\1", text)
    text = _HYPHEN_CLASS_PATTERN.sub("-", text)
    text = _CURLY_DQUOTE_PATTERN.sub('"', text)
    text = _CURLY_SQUOTE_PATTERN.sub("'", text)
    text = _FULL_PAREN_L_PATTERN.sub("(", text)
    text = _FULL_PAREN_R_PATTERN.sub(")", text)
    text = _FULL_COMMA_PATTERN.sub(",", text)
    text = _FULL_PERIOD_PATTERN.sub(".", text)
    text = _FULL_SEMI_PATTERN.sub(";", text)
    text = _FULL_COLON_PATTERN.sub(":", text)
    text = _GE_PATTERN.sub(">=", text)
    text = _LE_PATTERN.sub("<=", text)
    text = _TIMES_PATTERN.sub(r"\1×\2", text)
    text = _DATE_CN_PATTERN.sub(_date_cn_repl, text)
    text = _DATE_SEP_PATTERN.sub(_date_cn_repl, text)
    text = _WS_FULL_PATTERN.sub(" ", text)
    text = text.strip()

    text = strip_tender_table_prefix(text)
    text = strip_tender_directory_page_tail(text)
    return text.strip()


def _nfkc(value: str) -> str:
    """JS ``String.prototype.normalize('NFKC')`` 的 Python 对应物。"""
    import unicodedata

    return unicodedata.normalize("NFKC", value)


def _date_cn_repl(match: re.Match[str]) -> str:
    year = match.group(1)
    month = int(match.group(2))
    day = int(match.group(3))
    return f"{year}年{month}月{day}日"


# ---------------------------------------------------------------------------
# :1782-1799 stripTenderTablePrefix
# ---------------------------------------------------------------------------


def strip_tender_table_prefix(value: str) -> str:
    """表前缀循环剥（:1782-1799）：六前缀 + 可选序号，循环剥直到不动。"""
    text = str(value or "").strip()
    changed = True
    while changed:
        changed = False
        for prefix in _TABLE_PREFIXES:
            pattern = re.compile(
                rf"^{re.escape(prefix)}\s*[:：]?\s*(?:\d+(?:\.\d+)*\s*[.)、．]?\s*)?",
                re.I,
            )
            nxt = pattern.sub("", text).strip()
            if nxt != text and nxt:
                text = nxt
                changed = True
                break
    return text


# ---------------------------------------------------------------------------
# :1801-1810 stripTenderDirectoryPageTail
# ---------------------------------------------------------------------------


def strip_tender_directory_page_tail(value: str) -> str:
    """目录页尾剥（:1801-1810）：含目录类词才动；四条剥尾逐字。"""
    text = str(value or "").strip()
    if not _DIRECTORY_HINT_PATTERN.search(text):
        return text
    text = _PAGE_CN_TAIL_PATTERN.sub("", text)
    text = _PAGE_P_TAIL_PATTERN.sub("", text)
    text = _PAGE_DOTS_TAIL_PATTERN.sub("", text)
    text = _PAGE_NUM_TAIL_PATTERN.sub("", text)
    return text.strip()


# ---------------------------------------------------------------------------
# :1812-1824 strict/loose key
# ---------------------------------------------------------------------------


def build_tender_strict_key(value: str) -> str:
    """strict key（:1812-1817）：normalize → 去空白 → 去标点集 → lower。"""
    return _strict_or_loose(value)


def build_tender_loose_text(value: str) -> str:
    """loose text（:1819-1824）：与 strict key **当前实现相同**（参考就是同款两份，
    刻意保留两个名字，不合并成别名）。"""
    return _strict_or_loose(value)


def _strict_or_loose(value: str) -> str:
    text = normalize_tender_comparable_text(value)
    text = _STRICT_WS_PATTERN.sub("", text)
    text = _STRICT_PUNCT_PATTERN.sub("", text)
    return text.lower()


# ---------------------------------------------------------------------------
# :1826-1842 buildTenderSkeletonKey
# ---------------------------------------------------------------------------


def build_tender_skeleton_key(value: str) -> str:
    """skeleton key（:1826-1842）：占位符替换顺序**逐字**（date → date-sep →
    code → 万元 → 元 → % → 分 → P页码 → 裸数字），再剥空白/标点 + lower。"""
    text = normalize_tender_comparable_text(value)
    text = _SKELETON_DATE_PATTERN.sub("{date}", text)
    text = _SKELETON_DATE_SEP_PATTERN.sub("{date}", text)
    text = _SKELETON_CODE_PATTERN.sub("{code}", text)
    text = _SKELETON_WAN_PATTERN.sub("{money}", text)
    text = _SKELETON_YUAN_PATTERN.sub("{money}", text)
    text = _SKELETON_PERCENT_PATTERN.sub("{percent}", text)
    text = _SKELETON_SCORE_PATTERN.sub("{score}", text)
    text = _SKELETON_PAGE_PATTERN.sub("{page}", text)
    text = _SKELETON_NUM_PATTERN.sub("{num}", text)
    text = _STRICT_WS_PATTERN.sub("", text)
    text = _STRICT_PUNCT_PATTERN.sub("", text)
    return text.lower()


# ---------------------------------------------------------------------------
# :1844-1850 isTenderSkeletonAllowed
# ---------------------------------------------------------------------------


def is_tender_skeleton_allowed(value: str, skeleton_key: str) -> bool:
    """skeleton 门（:1844-1850）：skeletonKey 非空且 ≥8 且含 ``{}``；
    compact ≥18 收；否则须含评分类词。

    **D4（参考缺陷，逐字复刻）**：``build_tender_skeleton_key`` 的最后一步
    （:1839）把 ``{}`` 剥掉，故 ``skeleton_key`` 恒不含 ``{}``，本函数的
    ``!/[{}]/`` 守卫恒成立，**恒返回 False**。逐字保留不"修复"。
    """
    text = normalize_tender_comparable_text(value)
    compact_length = len(build_tender_loose_text(text))
    if not skeleton_key or len(skeleton_key) < 8 or "{" not in skeleton_key or "}" not in skeleton_key:
        return False
    if compact_length >= 18:
        return True
    return bool(_SKELETON_SCORE_HINT_PATTERN.search(text))


# ---------------------------------------------------------------------------
# :1855-1861 normalizeTenderFieldName
# ---------------------------------------------------------------------------


def normalize_tender_field_name(value: str) -> str:
    """字段名归一（:1855-1861）：去空白 → 去尾部冒号 → 去括号类 → trim。"""
    text = str(value or "")
    text = _FIELD_WS_PATTERN.sub("", text)
    text = _FIELD_COLON_TAIL_PATTERN.sub("", text)
    text = _FIELD_BRACKET_PATTERN.sub("", text)
    return text.strip()


# ---------------------------------------------------------------------------
# :1863-1879 parseTenderFormatField
# ---------------------------------------------------------------------------


def parse_tender_format_field(value: str) -> dict[str, str] | None:
    """格式化字段解析（:1863-1879）：评分因素特例 → 冒号 1-24 位 → allow 白名单整行。"""
    text = normalize_tender_comparable_text(value)
    if not text:
        return None
    if _SCORE_RETRIEVAL_PATTERN.search(text):
        tail = _SCORE_RETRIEVAL_PATTERN.sub("", text).strip()
        # 参考用 text.replace(/^评分因素及评标标准页码检索/i, '') 剥前缀
        tail = re.sub(r"^评分因素及评标标准页码检索", "", text, flags=re.I).strip()
        return {"field": "评分因素及评标标准页码检索", "tail": tail}

    colon_index = text.find(":")
    if 0 < colon_index <= 24:
        field = normalize_tender_field_name(text[:colon_index])
        tail = text[colon_index + 1 :].strip()
        return {"field": field, "tail": tail} if field else None

    title = normalize_tender_field_name(strip_tender_directory_page_tail(text))
    if _TENDER_FIELD_ALLOW_PATTERN.match(title):
        tail = text[len(title) :].strip()
        return {"field": title, "tail": tail}
    return None


# ---------------------------------------------------------------------------
# :1881-1884 isTenderFieldAllowed
# ---------------------------------------------------------------------------


def is_tender_field_allowed(field: str) -> bool:
    """字段白名单门（:1881-1884）：deny 先拒 → allow 白名单。"""
    if not field or _TENDER_FIELD_DENY_PATTERN.search(field):
        return False
    return bool(_TENDER_FIELD_ALLOW_PATTERN.match(field))


# ---------------------------------------------------------------------------
# :1886-1895 isSafeTenderFieldTail
# ---------------------------------------------------------------------------


def is_safe_tender_field_tail(value: str) -> bool:
    """字段尾安全判定（:1886-1895）：空尾真；日期/页码/P码/大写编码串真；
    ≤36 且含项目专名真（**D3：专名是参考硬编码，逐字保留**）。"""
    tail = normalize_tender_comparable_text(value).strip()
    if not tail:
        return True
    if _SAFE_TAIL_DATE_PATTERN.match(tail):
        return True
    if _SAFE_TAIL_PAGE_PATTERN.match(tail):
        return True
    if _SAFE_TAIL_P_PATTERN.match(tail):
        return True
    if _SAFE_TAIL_CODE_PATTERN.match(tail):
        return True
    return len(tail) <= 36 and bool(_SAFE_TAIL_PROJECT_PATTERN.search(tail))


# ---------------------------------------------------------------------------
# :1896-1912 bigram / dice / near 门
# ---------------------------------------------------------------------------


def char_bigrams_from_loose_text(value: str) -> set[str]:
    """相邻字符对集合（:1896-1903）：长度 1 → {自身}；否则相邻对（码点，D1）。"""
    text = str(value or "")
    if not text:
        return set()
    if len(text) == 1:
        return {text}
    chars = list(text)
    return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}


def dice_similarity_from_shared(shared: int, left_size: int, right_size: int) -> float:
    """Dice 相似度（:1905-1907）：``2*shared / max(left+right, 1)``。"""
    return (2 * shared) / max(left_size + right_size, 1)


def should_apply_near_tender_match(value: str, loose_text: str) -> bool:
    """near 门（:1909-1912）：loose ≥12 或含关键词。"""
    if len(loose_text) >= 12:
        return True
    return bool(_NEAR_HINT_PATTERN.search(normalize_tender_comparable_text(value)))


# ---------------------------------------------------------------------------
# :1914-1997 buildTenderSourceMatcher
# ---------------------------------------------------------------------------


@dataclass
class _TenderSourceMatcher:
    """参考 :1980-1996 返回的 JS 对象 ``{ tenderSentenceCount, match }`` 的对应物。

    参考是对象（``matcher.match(...)`` / ``matcher.tenderSentenceCount``），故用
    dataclass 承载属性访问语义；``match`` 是普通函数（非方法，避免 self 绑定
    干扰），键名逐字 snake_case 化。
    """

    tender_sentence_count: int
    match: Callable[[Mapping[str, str]], dict | None]


def build_tender_source_matcher(tender_sentences: Sequence[Mapping[str, str]]) -> _TenderSourceMatcher:
    """招标源匹配器（:1914-1997）：建四集合 + gram 倒排 + candidateCounts，
    返回 ``_TenderSourceMatcher(tender_sentence_count, match)``。五级短路逐字。"""
    exact_set: set[str] = set()
    strict_set: set[str] = set()
    skeleton_set: set[str] = set()
    field_set: set[str] = set()
    entries: list[dict] = []
    gram_index: dict[str, list[int]] = {}

    for sentence in tender_sentences:
        source = sentence.get("sentence") or sentence.get("normalized") or ""
        normalized = sentence.get("normalized") or normalize_content_sentence(source)
        strict_key = build_tender_strict_key(normalized)
        skeleton_key = build_tender_skeleton_key(normalized)
        loose_text = build_tender_loose_text(normalized)
        grams = char_bigrams_from_loose_text(loose_text)
        if normalized:
            exact_set.add(normalized)
        if strict_key and len(strict_key) >= 3:
            strict_set.add(strict_key)
        if is_tender_skeleton_allowed(normalized, skeleton_key):
            skeleton_set.add(skeleton_key)
        parsed_field = parse_tender_format_field(normalized)
        if parsed_field and is_tender_field_allowed(parsed_field["field"]):
            field_set.add(parsed_field["field"])
        entry = {"normalized": normalized, "strict_key": strict_key, "skeleton_key": skeleton_key, "loose_text": loose_text, "grams": grams}
        entry_index = len(entries)
        entries.append(entry)
        for gram in grams:
            lst = gram_index.get(gram, [])
            lst.append(entry_index)
            gram_index[gram] = lst

    candidate_counts = [0] * len(entries)

    def match_near(sentence: Mapping[str, str]) -> dict | None:
        loose_text = build_tender_loose_text(sentence["normalized"])
        if not should_apply_near_tender_match(sentence["normalized"], loose_text):
            return None
        grams = char_bigrams_from_loose_text(loose_text)
        if len(grams) < 4:
            return None
        candidates: list[int] = []
        for gram in grams:
            for index in gram_index.get(gram, []):
                if candidate_counts[index] == 0:
                    candidates.append(index)
                candidate_counts[index] += 1

        best: dict | None = None
        compact_length = len(loose_text)
        for index in candidates:
            shared = candidate_counts[index]
            candidate_counts[index] = 0
            entry = entries[index]
            if not entry["grams"]:
                continue
            shorter = min(len(grams), len(entry["grams"]))
            longer = max(len(grams), len(entry["grams"]))
            containment = shared / max(shorter, 1)
            dice = dice_similarity_from_shared(shared, len(grams), len(entry["grams"]))
            length_ratio = shorter / max(longer, 1)
            if compact_length >= 30:
                allowed = containment >= 0.9 and dice >= 0.82 and length_ratio >= 0.5
            else:
                allowed = containment >= 0.95 and dice >= 0.88 and length_ratio >= 0.55
            if not allowed:
                continue
            if best is None or dice > best["dice"]:
                best = {"reason": "near", "dice": dice, "containment": containment, "tender": entry["normalized"]}
        return best

    def match(sentence: Mapping[str, str]) -> dict | None:
        normalized = sentence.get("normalized") or ""
        if not normalized:
            return None
        if normalized in exact_set:
            return {"reason": "exact"}
        strict_key = build_tender_strict_key(normalized)
        if strict_key and strict_key in strict_set:
            return {"reason": "strict"}
        parsed_field = parse_tender_format_field(normalized)
        if (
            parsed_field
            and is_tender_field_allowed(parsed_field["field"])
            and parsed_field["field"] in field_set
            and is_safe_tender_field_tail(parsed_field["tail"])
        ):
            return {"reason": "field"}
        skeleton_key = build_tender_skeleton_key(normalized)
        if is_tender_skeleton_allowed(normalized, skeleton_key) and skeleton_key in skeleton_set:
            return {"reason": "skeleton"}
        return match_near(sentence)

    return _TenderSourceMatcher(
        tender_sentence_count=len(exact_set),
        match=match,
    )


# ---------------------------------------------------------------------------
# :1999-2004 buildDuplicateSentences
# ---------------------------------------------------------------------------


def build_duplicate_sentences(global_sentences: Mapping[str, dict]) -> list[dict]:
    """重复句汇总（:1999-2004）：file_ids > 1 过滤 → 三级排序
    （-file_ids 数, -句长, first_order 升序）→ ``S{序号:06d}`` id。"""
    items = list(global_sentences.values())
    filtered = [item for item in items if len(item["file_ids"]) > 1]
    filtered.sort(
        key=lambda item: (-len(item["file_ids"]), -len(item["sentence"]), item["first_order"])
    )
    result = []
    for index, item in enumerate(filtered):
        result.append({**item, "id": f"S{index + 1:06d}"})
    return result
