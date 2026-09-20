"""正文比对纯函数：文本块提取(GFM 表格) / 切句 / 信息量判据（零 I/O）。

逐字移植参考实现 `client/electron/services/duplicateCheckService.cjs`
（下称 svc.cjs）:108-112（模块级正则）+ :1453-1754（16 个函数），
camelCase → snake_case：

============================  ==========================  ===============
参考函数                       svc.cjs 行号                 本模块
============================  ==========================  ===============
（模块级正则 ×4）             :108-111                    ``_MARKDOWN_IMAGE_PATTERN`` 等
``stripImagesFromMarkdown``   :1453                       ``strip_images_from_markdown``
``codePointToString``          :1460                       ``_code_point_to_string``
``hexCodePointToString``       :1469                       ``_hex_code_point_to_string``
``decodeBasicHtmlEntities``    :1478                       ``decode_basic_html_entities``
``normalizeContentLineBreaks`` :1490                       ``_normalize_content_line_breaks``
``addContentTextBlock``        :1494                       ``add_content_text_block``
``splitMarkdownTableRow``      :1534                       ``split_markdown_table_row``
``isMarkdownTableSeparator``   :1560                       ``is_markdown_table_separator``
``isMarkdownTableRow``         :1565                       ``is_markdown_table_row``
``cleanMarkdownInlineText``    :1569                       ``clean_markdown_inline_text``
``cleanMarkdownLine``          :1584                       ``clean_markdown_line``
``extractMarkdownTextBlocks``  :1592                       ``extract_markdown_text_blocks``
``extractContentTextBlocks``   :1644                       ``extract_content_text_blocks``
``normalizeContentSentence``   :1665                       ``normalize_content_sentence``
``stripLeadingContentSequence``:1673                       ``strip_leading_content_sequence``
``cleanContentSentence``       :1699                       ``clean_content_sentence``
``splitContentBlockSentences`` :1708                       ``split_content_block_sentences``
``isInformativeContentSentence``:1730                      ``is_informative_content_sentence``
``splitContentSentences``      :1742                       ``split_content_sentences``
============================  ==========================  ===============

**偏离登记（测试 `server/tests/test_domain_content.py` 的预期值全部经 Node
直接运行参考实现取地面真值核对）：**

- **D1 码点语义（R-P3b-5，登记偏离）**：JS 的 `str.length` / 索引 /
  `String.fromCodePoint` 之外的字符串遍历按 UTF-16 码元；Python 的
  ``len`` / 切片 / ``for char in str`` 按码点。影响点：
  ``split_content_block_sentences`` 的非空白累计 ``current_length`` 与
  边界判定（:1715-1719，JS 用 ``text[index]`` over ``text.length``）；
  600 截断（:1750，``sentence.length`` / ``sentence.slice(0,600)``）；
  ``is_informative_content_sentence`` 的 ``compact.length`` 阈值（:1735-1739，
  但 ``compact.match(...)`` 的匹配计数是码点，BMP 内与 JS 一致）。
  BMP 内（含全部 CJK）两端一致，仅 astral 字符（emoji 等）有差。裁定：Python
  按码点实现，不做 UTF-16 仿真（标书正文 emoji 极罕见）。
  例外：「循环剥」``strip_leading_content_sequence`` 参考用 ``for...of``（码点
  迭代）且整体字符串 ``replace``，本身即码点语义——**两端一致，无偏离**。
- **D2 ``\\d`` 语义**：JS ``\\d`` 仅匹配 ASCII ``[0-9]``；Python ``re`` 的
  ``\\d`` 匹配 Unicode 十进制数字。本模块保留 ``\\d`` 写法（与参考逐字对照）。
  实际影响面：进入 ``strip_leading_content_sequence`` / ``is_informative*
  _content_sentence`` 的文本是纯数字/汉字，全角数字 ``１２３`` 仅在极端输入下
  触达 ``\\d`` 差异（如 ``^\\d+$`` 纯数字判据会把全角数字也判为「纯数字」），
  接受并登记。模块级四正则的 ``gi`` 已在 D5 逐字符分析。
- **D3 ``u`` flag**：JS 正则 ``u``（Unicode 模式）在 Python ``re`` 处理 ``str``
  时天然成立（``re`` 默认 Unicode），无需对应 flag。``\\u{...}`` 形式未使用。
- **D4 HTML 表格路径不做（R-P3b-2）**：参考 ``extractContentTextBlocks`` 的
  cheerio HTML 表格分流（:1644-1663，依赖 ``extractHtmlTableTextBlocks`` /
  ``extractHtmlCellTextBlocks`` / ``<table>`` 标记）**不移植**；``html_table_
  pattern`` 仅按参考逐字定义（:111），**不用于分流**。``extract_content_
  text_blocks`` 直通 ``extract_markdown_text_blocks``（GFM 表格已由表格三元组
  检测覆盖）。依据 P3：两端 markdown 管线产出的都是 GFM 管道表格，HTML
  ``<table>`` 残留基本不可达。
- **D5 ``gi`` → Python flag 逐字符裁定**：计划总纲写「``gi`` → ``re.I | re.S``」，
  但按 Task 1 评审纪律「逐个正则按参考逐字符分析每个 flag 的实际影响」**不盲目
  加 ``re.S``**。结论：
  * ``markdown_image_pattern``（:108）：无字母、无 ``.`` 元字符 → ``re.I`` 是
    no-op（忠实镜像 JS 的 ``i``，但等价）；**不加 ``re.S``**（模式无 ``.``）。
  * ``html_image_src_pattern``（:109）：含 ``img`` / ``src`` 字母 → ``re.I``
    **有真实效果**（``<IMG SRC=…>`` 也匹配）；**不加 ``re.S``**（用 ``[^>]``
    而非 ``.``）。
  * ``html_image_pattern``（:110）：含 ``img`` → ``re.I`` 真实效果；不加 ``re.S``。
  * ``html_table_pattern``（:111）：含 ``table`` → ``re.I`` 真实效果；用
    ``[\\s\\S]``（非 ``.``）匹配任意字符含换行，**不加 ``re.S``**（加了也是
    no-op，且违背逐字符分析纪律）。
  四条统一用 ``re.I``（镜像 ``gi`` 的 ``i``），**全部不加 ``re.S``**。
  另：数值实体 ``&#x…;`` 的 ``gi`` 使 ``[0-9a-f]`` 在 ``i`` 下也吃大写十六进制
  （``&#x4E2D;`` 可解），``_HEX_ENTITY`` 加 ``re.I`` 复刻该效果。

数据形状：句条目为 plain dict，键名与参考 JS 对象逐字一致（``sentence`` /
``normalized``）；参考的 ``undefined`` 缺省以 ``None`` 表达。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 模块级正则（逐字移植；注释 = svc.cjs 参考行号）
# ---------------------------------------------------------------------------

# :108  /!\[(?<alt>[^\]]*)\]\((?<target><[^>]+>|[^)\s]+)(?<title>\s+"[^"]*")?\)/gi
# 命名组 (?<name>) → (?P<name>)；无字母无 "." → re.I 为 no-op；不加 re.S。
_MARKDOWN_IMAGE_PATTERN = re.compile(
    r"""!\[(?P<alt>[^\]]*)\]\((?P<target><[^>]+>|[^)\s]+)(?P<title>\s+"[^"]*")?\)""",
    re.IGNORECASE,
)

# :109  /<img\b[^>]*?\bsrc=["'](?<src>[^"']+)["'][^>]*>/gi
# 含 img/src 字母 → re.I 真实效果；用 [^>] 非 "." → 不加 re.S。
_HTML_IMAGE_SRC_PATTERN = re.compile(
    r"""<img\b[^>]*?\bsrc=["'](?P<src>[^"']+)["'][^>]*>""",
    re.IGNORECASE,
)

# :110  /<img\b[^>]*>/gi  —— 含 img 字母 → re.I 真实效果；不加 re.S。
_HTML_IMAGE_PATTERN = re.compile(r"<img\b[^>]*>", re.IGNORECASE)

# :111  /<table\b[\s\S]*?<\/table>/gi  —— R-P3b-2：只定义，不用于分流。
# 含 table 字母 → re.I 真实效果；用 [\s\S] 跨换行（非 "."）→ 不加 re.S。
_HTML_TABLE_PATTERN = re.compile(r"<table\b[\s\S]*?<\/table>", re.IGNORECASE)

# decodeBasicHtmlEntities :1480-1487（命名实体；amp 在最后，:1485）
_ENTITY_NBSP = re.compile(r"&nbsp;", re.IGNORECASE)
_ENTITY_LT = re.compile(r"&lt;", re.IGNORECASE)
_ENTITY_GT = re.compile(r"&gt;", re.IGNORECASE)
_ENTITY_QUOT = re.compile(r"&quot;", re.IGNORECASE)
_ENTITY_APOS = re.compile(r"&apos;", re.IGNORECASE)
_ENTITY_AMP = re.compile(r"&amp;", re.IGNORECASE)
# :1486  &#x…; 十六进制（gi → [0-9a-f] 在 i 下也吃大写十六进制）
_HEX_ENTITY_PATTERN = re.compile(r"&#x([0-9a-f]+);", re.IGNORECASE)
# :1487  &#…; 十进制（无字母 → 不加 re.I）
_DEC_ENTITY_PATTERN = re.compile(r"&#(\d+);")

# cleanMarkdownInlineText :1571-1581（图片三连 + 行内代码/链接/标签/强调）
_INLINE_CODE_PATTERN = re.compile(r"`([^`]+)`")
_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_BR_PATTERN = re.compile(r"<br\s*/?\s*>", re.IGNORECASE)
_BLOCK_CLOSE_PATTERN = re.compile(
    r"</(?:p|div|li|h[1-6]|blockquote|section|article)>", re.IGNORECASE
)
_OTHER_TAG_PATTERN = re.compile(r"<[^>]+>")
_BOLD_PATTERN = re.compile(r"\*\*([^*\n]+)\*\*")
_UNDERSCORE_PATTERN = re.compile(r"__([^_\n]+)__")
_STRIKE_PATTERN = re.compile(r"~~([^~\n]+)~~")

# cleanMarkdownLine :1586-1588
_MD_HEADING_PREFIX_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+")
_MD_LIST_PREFIX_PATTERN = re.compile(r"^\s*(?:[-*+]|>)\s+")
_INLINE_WS_PATTERN = re.compile(r"[\t ]+")

# extractMarkdownTextBlocks :1593 代码块剥除（[\s\S] 跨换行，不加 re.S）
_CODE_FENCE_PATTERN = re.compile(r"```[\s\S]*?```")
# :1562 表格分隔单元格判据
_TABLE_SEPARATOR_CELL_PATTERN = re.compile(r"^:?-{2,}:?$")
# :1630 standalone 七类前缀（逐字；CJK 数字固定集 [一二三四五六七八九十]）
_STANDALONE_PATTERN = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*[.)、．]"
    r"|[一二三四五六七八九十]+[、.．]"
    r"|（[一二三四五六七八九十\d]+）"
    r"|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])\s+"
)
# :1631 字段行判据
_FIELD_LINE_PATTERN = re.compile(r"^[^：:\s]{1,18}[：:]")
# :1632 句尾行判据
_SENTENCE_END_LINE_PATTERN = re.compile(r"[。！？!?；;]$")

# cleanContentSentence / normalizeContentSentence :1668 / :1702 控制字符类
_CONTROL_CHARS_PATTERN = re.compile(r"[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\ufeff]")
_BOM_PATTERN = re.compile(r"^\uFEFF")
# :1669 / :1704 空白类
_WS_FULL_PATTERN = re.compile(r"[\s　]+")
_TAB_SPACE_PATTERN = re.compile(r"[\t ]+")
_FULL_SPACE_PATTERN = re.compile(r"[　]+")

# stripLeadingContentSequence :1676-1682（七模式，逐字；\\? = 字面反斜杠可选）
# 复用的字符集（防转录漏字，评审曾因漏 ⑰ 误报）
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
_CJK_DIGITS = "一二三四五六七八九十"
_CJK_DIGITS_LONG = "一二三四五六七八九十百千万"
_STRIP_LEADING_PATTERNS = [
    # :1676 数字编号（可带多级 . 与可选反斜杠转义点）
    re.compile(r"^\s*[\d０-９]+(?:\\?[.．][\d０-９]+)*\s*(?:\\?[.．]|[)）、])\s*"),
    # :1677 数字编号 + 空格 + 字母/汉字/括号（lookahead）
    re.compile(r"^\s*[\d０-９]+(?:\\?[.．][\d０-９]+)*\s+(?=[A-Za-z\u4e00-\u9fff（(])"),
    # :1678 半角括号 (数字|CJK数字)——CJK 集含「百千万」（参考 :1678 逐字）
    re.compile(r"^\s*\((?:[\d０-９]+(?:\\?[.．][\d０-９]+)*|[" + _CJK_DIGITS_LONG + r"]+)\)\s*(?:\\?[.．]|[、])?\s*"),
    # :1679 CJK 数字 + .．|、)）
    re.compile(r"^\s*[" + _CJK_DIGITS_LONG + r"]+\s*(?:\\?[.．]|[、)）])\s*"),
    # :1680 全角括号 （CJK数字|数字）
    re.compile(r"^\s*（(?:[" + _CJK_DIGITS_LONG + r"]+|[\d０-９]+(?:\\?[.．][\d０-９]+)*)）\s*(?:\\?[.．]|[、])?\s*"),
    # :1681 圈号
    re.compile(r"^\s*[" + _CIRCLED + r"]\s*(?:\\?[.．]|[、])?\s*"),
    # :1682 第…章/节/篇/部/分/卷
    re.compile(r"^\s*第(?:[\d０-９]+|[" + _CJK_DIGITS_LONG + r"]+)[章节篇部分卷]\s*"),
]

# splitContentBlockSentences :1718-1719 边界
_WHITESPACE_CHAR_PATTERN = re.compile(r"\s")
_STRONG_BOUNDARY_PATTERN = re.compile(r"[。！？!?]")
_CLAUSE_BOUNDARY_PATTERN = re.compile(r"[；;]")

# isInformativeContentSentence :1732-1739
_ONLY_DIGITS_PATTERN = re.compile(r"^\d+$")
_CONTENT_CHAR_PATTERN = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")
_COLON_PATTERN = re.compile(r"[：:]")
_TWO_ALNUM_PATTERN = re.compile(r"[A-Za-z\u4e00-\u9fff]{2,}")
_CJK_CHAR_PATTERN = re.compile(r"[\u4e00-\u9fff]")
# :1739 单位词（含 GHz/MHz 字母 → re.I 真实效果）
_UNIT_WORD_PATTERN = re.compile(
    r"(?:日历天|个月|万元|GHz|MHz|GB|MB|kg|mm|cm|天|年|元|%|％)", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# :1453-1500 实体解码 / 行尾规范化 / 块收集
# ---------------------------------------------------------------------------


def _code_point_to_string(value: str, fallback: str) -> str:
    """对齐 svc.cjs:1460 ``codePointToString``：十进制码点 → 字符。

    越界 / 非数 → fallback（对应 JS ``Number.isFinite`` / ``fromCodePoint``
    抛错走 catch）。"""
    try:
        code_point = int(str(value), 10)
    except (ValueError, TypeError):
        return fallback
    try:
        return chr(code_point)
    except (ValueError, OverflowError):
        return fallback


def _hex_code_point_to_string(value: str, fallback: str) -> str:
    """对齐 svc.cjs:1469 ``hexCodePointToString``：十六进制码点 → 字符。"""
    try:
        code_point = int(str(value), 16)
    except (ValueError, TypeError):
        return fallback
    try:
        return chr(code_point)
    except (ValueError, OverflowError):
        return fallback


def decode_basic_html_entities(value: str) -> str:
    """HTML 实体解码（:1478-1487）：nbsp/lt/gt/quot/apos/amp **顺序逐字**
    （amp 在最后 → ``&amp;lt;`` 仅解成 ``&lt;``，不二次解码）；随后
    ``&#x…;`` 十六进制 + ``&#…;`` 十进制，解码失败保留原文。"""
    text = str(value or "")
    text = _ENTITY_NBSP.sub(" ", text)
    text = _ENTITY_LT.sub("<", text)
    text = _ENTITY_GT.sub(">", text)
    text = _ENTITY_QUOT.sub('"', text)
    text = _ENTITY_APOS.sub("'", text)
    text = _ENTITY_AMP.sub("&", text)
    text = _HEX_ENTITY_PATTERN.sub(
        lambda m: _hex_code_point_to_string(m.group(1), m.group(0)), text
    )
    text = _DEC_ENTITY_PATTERN.sub(
        lambda m: _code_point_to_string(m.group(1), m.group(0)), text
    )
    return text


def _normalize_content_line_breaks(value: str) -> str:
    """对齐 svc.cjs:1490：``\\r\\n``→``\\n``、``\\r``→``\\n``。"""
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def add_content_text_block(blocks: list[str], value: str) -> None:
    """对齐 svc.cjs:1494：解码实体 + cleanContentSentence → 按换行拆行逐块收集
    （空行跳过）。就地 append 到 ``blocks``（镜像 JS 对数组的 push）。"""
    text = clean_content_sentence(decode_basic_html_entities(value))
    for line in re.split(r"\n+", _normalize_content_line_breaks(text)):
        cleaned = clean_content_sentence(line)
        if cleaned:
            blocks.append(cleaned)


# ---------------------------------------------------------------------------
# :1534-1590 markdown 表格 / 行内清洗
# ---------------------------------------------------------------------------


def split_markdown_table_row(line: str) -> list[str]:
    """GFM 表格行分列（:1534-1557）：首尾 ``|`` 剥除（尾 ``|`` 须非转义）、
    ``\\|``→``|``、逐字符状态机（``\\`` 转义跳过管道）。码点迭代（JS
    ``for...of`` = 码点；BMP 一致，R-P3b-5）。"""
    text = str(line or "").strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    cells: list[str] = []
    current = ""
    escaped = False
    for char in text:
        if char == "\\" and not escaped:
            escaped = True
            current += char
            continue
        if char == "|" and not escaped:
            cells.append(current.replace("\\|", "|").strip())
            current = ""
            continue
        current += char
        escaped = False
    cells.append(current.replace("\\|", "|").strip())
    return cells


def is_markdown_table_separator(line: str) -> bool:
    """是否表格分隔行（:1560-1563）：多列且每格 ``^:?-{2,}:?$``（去空白后）。"""
    cells = split_markdown_table_row(line)
    if len(cells) <= 1:
        return False
    return all(
        _TABLE_SEPARATOR_CELL_PATTERN.fullmatch(re.sub(r"\s+", "", cell))
        for cell in cells
    )


def is_markdown_table_row(line: str) -> bool:
    """是否表格数据行（:1565-1567）：分列 > 1。"""
    return len(split_markdown_table_row(line)) > 1


def clean_markdown_inline_text(value: str) -> str:
    """行内 markdown 清洗（:1569-1582）：图片三连剥 → 行内代码保文字 → 链接
    保文字 → ``<br>``→``\\n`` → 块级闭合标签→``\\n`` → 其余标签→空格 → ``**``/
    ``__``/``~~`` 剥除；**最后**解码 HTML 实体。

    评审 A F1 修正：参考把 ``decodeBasicHtmlEntities`` 包在整个 replace 链
    **最外层**（:1569 ``return decodeBasicHtmlEntities(String(value||'')…)``
    到 :1583 收口），即先剥完全部 markdown/HTML 语法再解码——这样
    ``&lt;img …&gt;``、``&#x60;…&#x60;`` 这类「编码实体包装的语法」不会被
    解码后的裸语法二次命中（参考行为：残留为普通文本）。"""
    text = str(value or "")
    text = _MARKDOWN_IMAGE_PATTERN.sub(" ", text)
    text = _HTML_IMAGE_SRC_PATTERN.sub(" ", text)
    text = _HTML_IMAGE_PATTERN.sub(" ", text)
    text = _INLINE_CODE_PATTERN.sub(r"\1", text)
    text = _LINK_PATTERN.sub(r"\1", text)
    text = _BR_PATTERN.sub("\n", text)
    text = _BLOCK_CLOSE_PATTERN.sub("\n", text)
    text = _OTHER_TAG_PATTERN.sub(" ", text)
    text = _BOLD_PATTERN.sub(r"\1", text)
    text = _UNDERSCORE_PATTERN.sub(r"\1", text)
    text = _STRIKE_PATTERN.sub(r"\1", text)
    return decode_basic_html_entities(text)


def clean_markdown_line(value: str) -> str:
    """整行 markdown 清洗（:1584-1589）：cleanMarkdownInlineText + 去
    ``#{1,6}\\s`` / ``[-*+>]\\s`` 前缀 + 空白压缩 + trim。"""
    text = clean_markdown_inline_text(value)
    text = _MD_HEADING_PREFIX_PATTERN.sub("", text, count=1)
    text = _MD_LIST_PREFIX_PATTERN.sub("", text, count=1)
    text = _INLINE_WS_PATTERN.sub(" ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# :1592-1643 markdown 文本块抽取（含 GFM 表格三元组）
# ---------------------------------------------------------------------------


def extract_markdown_text_blocks(markdown: str) -> list[str]:
    """markdown 文本块抽取（:1592-1642）：

    ① 全文剥 ``` 代码块 → ② 逐行：**表格三元组检测**（本行是表行 && 下一行是
    分隔行 → 收集至非表行，分隔行跳过，**每格独立成块**）→ ③ ``standalone``
    （标题/列表/编号行） / ``fieldLine``（``^[^：:\\s]{1,18}[：:]``）/
    ``sentenceLine``（句尾标点）→ 立即成块；否则进段落缓冲，空行/上述条件
    flush（空格 join）。"""
    text = _CODE_FENCE_PATTERN.sub(
        "\n", _normalize_content_line_breaks(str(markdown or ""))
    )
    lines = text.split("\n")
    blocks: list[str] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph:
            return
        add_content_text_block(blocks, " ".join(paragraph))
        paragraph.clear()

    index = 0
    while index < len(lines):
        if (
            index + 1 < len(lines)
            and is_markdown_table_row(lines[index])
            and is_markdown_table_separator(lines[index + 1])
        ):
            flush_paragraph()
            table_rows = [split_markdown_table_row(lines[index])]
            index += 2
            while index < len(lines) and is_markdown_table_row(lines[index]):
                if not is_markdown_table_separator(lines[index]):
                    table_rows.append(split_markdown_table_row(lines[index]))
                index += 1
            index -= 1
            for row in table_rows:
                for cell in row:
                    add_content_text_block(blocks, clean_markdown_inline_text(cell))
            index += 1
            continue

        raw_line = lines[index]
        cleaned = clean_markdown_line(raw_line)
        if not cleaned:
            flush_paragraph()
            index += 1
            continue

        # :1630 standalone = 标题前缀 OR 列表前缀 OR 编号/圈号前缀（三者 ||）
        standalone = (
            bool(_MD_HEADING_PREFIX_PATTERN.match(raw_line))
            or bool(_MD_LIST_PREFIX_PATTERN.match(raw_line))
            or bool(_STANDALONE_PATTERN.match(raw_line))
        )
        field_line = bool(_FIELD_LINE_PATTERN.match(cleaned))
        sentence_line = bool(_SENTENCE_END_LINE_PATTERN.search(cleaned))
        if standalone or field_line or sentence_line:
            flush_paragraph()
            add_content_text_block(blocks, cleaned)
        else:
            paragraph.append(cleaned)
        index += 1

    flush_paragraph()
    return blocks


def strip_images_from_markdown(markdown: str) -> str:
    """剥除三种 markdown/HTML 图片形态（:1453-1458）：``![]()`` / ``<img
    src=…>`` / ``<img …>`` → 空格。"""
    value = str(markdown or "")
    value = _MARKDOWN_IMAGE_PATTERN.sub(" ", value)
    value = _HTML_IMAGE_SRC_PATTERN.sub(" ", value)
    value = _HTML_IMAGE_PATTERN.sub(" ", value)
    return value


def extract_content_text_blocks(markdown: str) -> list[str]:
    """正文文本块抽取（:1644-1663）。

    **R-P3b-2 / D4**：参考的 cheerio HTML 表格分流路径不移植；``html_table_
    pattern`` 仅定义不用于分流。本实现 = ``strip_images_from_markdown`` →
    直通 ``extract_markdown_text_blocks``（GFM 表格三元组已覆盖）。"""
    source = strip_images_from_markdown(markdown)
    return extract_markdown_text_blocks(source)


# ---------------------------------------------------------------------------
# :1665-1706 句归一化 / 先导序列剥除 / 句清洗
# ---------------------------------------------------------------------------


def normalize_content_sentence(value: str) -> str:
    """句归一化（:1665-1671）：stripLeadingContentSequence → 剥 BOM → 控制字符
    类剥 → 空白压缩 → trim。码点语义（D1 仅 astral 有差）。"""
    text = strip_leading_content_sequence(str(value or ""))
    text = _BOM_PATTERN.sub("", text, count=1)
    text = _CONTROL_CHARS_PATTERN.sub("", text)
    text = _WS_FULL_PATTERN.sub(" ", text)
    return text.strip()


def strip_leading_content_sequence(value: str) -> str:
    """循环剥七种编号前缀（:1673-1697）：每次命中即 restart（while changed），
    剥后 ``trimStart``。码点语义两边一致（D1 例外点）。"""
    text = str(value or "").strip()
    changed = True
    while changed:
        changed = False
        for pattern in _STRIP_LEADING_PATTERNS:
            nxt = pattern.sub("", text, count=1)
            if nxt != text:
                text = nxt.lstrip()
                changed = True
                break
    return text


def clean_content_sentence(value: str) -> str:
    """句清洗（:1699-1705）：剥 BOM → 控制字符类剥 → ``[\\t ]+``→空格 →
    全角空格→空格 → trim。"""
    text = str(value or "")
    text = _BOM_PATTERN.sub("", text, count=1)
    text = _CONTROL_CHARS_PATTERN.sub("", text)
    text = _TAB_SPACE_PATTERN.sub(" ", text)
    text = _FULL_SPACE_PATTERN.sub(" ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# :1708-1753 切句 / 信息量判据
# ---------------------------------------------------------------------------


def split_content_block_sentences(block: str) -> list[str]:
    """单块切句（:1708-1727）：逐码点（D1），强边界 ``[。！？!?]`` 即切；
    弱边界 ``[；;]`` 且非空白累计 ≥20 才切；尾部余量成段。"""
    text = clean_content_sentence(block)
    if not text:
        return []
    parts: list[str] = []
    chars = list(text)
    start = 0
    current_length = 0
    for index, char in enumerate(chars):
        if not _WHITESPACE_CHAR_PATTERN.search(char):
            current_length += 1
        strong_boundary = bool(_STRONG_BOUNDARY_PATTERN.search(char))
        clause_boundary = (
            bool(_CLAUSE_BOUNDARY_PATTERN.search(char)) and current_length >= 20
        )
        if strong_boundary or clause_boundary:
            parts.append("".join(chars[start : index + 1]))
            start = index + 1
            current_length = 0
    if start < len(chars):
        parts.append("".join(chars[start:]))
    return parts


def is_informative_content_sentence(sentence: str) -> bool:
    """信息量判据（:1730-1740）：空串/纯数字拒；内容字符
    ``[A-Za-z0-9\\u4e00-\\u9fff]`` <4 拒；紧凑长度 ≥12 收；6-11 且含 ``：:``
    且含 ≥2 连续字母/汉字 收；≥6 且含汉字且含单位词（:1739 正则逐字）收。
    紧凑长度阈值用码点（D1，仅 astral 有差）。"""
    compact = re.sub(r"\s+", "", str(sentence or ""))
    if not compact or _ONLY_DIGITS_PATTERN.match(compact):
        return False
    content_chars = _CONTENT_CHAR_PATTERN.findall(compact)
    if len(content_chars) < 4:
        return False
    if len(compact) >= 12:
        return True
    if len(compact) >= 6 and _COLON_PATTERN.search(compact) and _TWO_ALNUM_PATTERN.search(compact):
        return True
    return bool(
        len(compact) >= 6
        and _CJK_CHAR_PATTERN.search(compact)
        and _UNIT_WORD_PATTERN.search(compact)
    )


def split_content_sentences(markdown: str) -> list[dict[str, str]]:
    """正文切句总入口（:1742-1753）：extractContentTextBlocks × 切句 ×
    clean/normalize → 非空且 informative 才收；**600 截断**（码点语义，D1）：
    ``sentence[:600] + "..."``。返回 ``[{sentence, normalized}]``。"""
    sentences: list[dict[str, str]] = []
    for block in extract_content_text_blocks(markdown):
        for part in split_content_block_sentences(block):
            sentence = clean_content_sentence(part)
            normalized = normalize_content_sentence(sentence)
            if not normalized:
                continue
            if not is_informative_content_sentence(normalized):
                continue
            sentences.append(
                {
                    "sentence": sentence[:600] + "..." if len(sentence) > 600 else sentence,
                    "normalized": normalized,
                }
            )
    return sentences
