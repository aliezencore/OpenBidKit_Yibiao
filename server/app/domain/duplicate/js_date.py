"""JS `new Date(text)` 的最小可移植子集（P3d T1，R-P3d-7）。

参考实现 `normalizeComparable` / `normalizeDateDay`（svc.cjs :145-160）用
`new Date(...)` 把日期样文本折算成 UTC ISO 串。V8 的解析规则与
`datetime.fromisoformat` 差异很大，且**时区缺省语义按形态分流**：

- `YYYY` / `YYYY-MM` / `YYYY-MM-DD`（纯日期）→ **UTC** 零点；
- `YYYY-MM-DDTHH:MM[:SS[.f]]` 带时区（Z / ±HH:MM）→ 该时区；
- 同形**不带时区**、空格分隔、斜杠 / 非补零日期（V8 legacy 分支）→ **本地时区**。

本模块只覆盖参考值域会出现的形态（docx XSD 日期、FileTime 转出的 ISO 串、
PDF `D:` 日期剥离后的形态、`YYYY/M/D` 等），并保证「JS 认为无效的 → 这里也返回
None」（用 oracle 样本钉住）。

**T5 补样本（oracle-extract 撞出的 V8 legacy 数字串解析）**：真实字段值
（revision=`"3"`、pages=`"42"`、code_page=`"936"`、app_version=`"11.1.0"`）
经 `new Date` 均解析成功且这些键 comparable → normalized 落库值必须对齐。
实测钉定的规则（全部**本地时区**、缺省年 = V8 常量 **2001**）：

- 纯数字 1~3 位 `n`：`1..12` → `{2001, n, 1}`（month 优先）；`0` 或 `13..99` →
  yy 规则（≤49→20nn、50-99→19nn）的 `{yy, 1, 1}`；`100..999` → 年直取。
- 纯数字 5 位 → 年直取（toISOString 年份 >9999 输出 `+YYYYYY` 6 位）。
- `a.b`（两段点分）→ `{2001, a, b}`（M.D）；a=0 → `{2000, b, 1}`（a 回落 yy）；
  a≥13 → 无效（V8 实测 NaN，'13.1'）。
- `a.b.c`（三段点分）→ a≥100 → `{a, b, c}`（Y.M.D，'2019.1.2'）；否则
  `{yy(c), a, b}`（M.D.Y；yy 规则同上，'1.2.3'→2003-01-02、'11.1.0'→2000-11-01、
  '1.2.99'→1999-01-02）。

**已登记偏离（spec §15）**：①含字母/符号的 V8 病态串（如 `"wps-uid-77"` 被
V8 解出 1977-01-01）不复刻；②4 段以上点分（'1.2.3.4'）两侧均 NaN，无差；
③远古年份（如 936 年）的本地时区偏移，V8/Windows 走 LMT（+08:05:43）而
Python astimezone 给现代偏移 → 秒级分歧，仅影响 3 位年直取形态；④T1 登记
的「1~3 位纯数字视为非日期」**已被 T5 推翻**（这些值出现在 comparable 键上），
按本节规则实现。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# ISO 8285 风格：日期必须补零（YYYY 或 YYYY-MM 或 YYYY-MM-DD），
# 时间部分 [T] 分隔、可带小数秒与时区。JS \d 是纯 ASCII → re.ASCII。
# re.IGNORECASE：normalizeComparable 先 toLowerCase() 再喂 new Date()，
# oracle 对 "…T08:00:00.000Z" 仍解出 ISO → V8 接受小写 t/z，这里必须对齐。
_ISO_RE = re.compile(
    r"^(?P<y>\d{4})"
    r"(?:-(?P<mo>\d{2})"
    r"(?:-(?P<d>\d{2})"
    r"(?:T(?P<H>\d{2}):(?P<M>\d{2})"
    r"(?::(?P<S>\d{2})(?:\.(?P<frac>\d+))?)?"
    r"(?P<tz>Z|[+-]\d{2}:\d{2})?"
    r")?"
    r")?"
    r")?$",
    re.ASCII | re.IGNORECASE,
)

# V8 legacy：空格分隔的日期时间（本地时区）。
_SPACE_RE = re.compile(
    r"^(?P<y>\d{4})-(?P<mo>\d{1,2})-(?P<d>\d{1,2})"
    r"[ ](?P<H>\d{1,2}):(?P<M>\d{2})(?::(?P<S>\d{2})(?:\.(?P<frac>\d+))?)?$",
    re.ASCII,
)

# V8 legacy：斜杠分隔（本地时区），日期可非补零，时间可选。
_SLASH_RE = re.compile(
    r"^(?P<y>\d{4})/(?P<mo>\d{1,2})/(?P<d>\d{1,2})"
    r"(?:[ ](?P<H>\d{1,2}):(?P<M>\d{2})(?::(?P<S>\d{2})(?:\.(?P<frac>\d+))?)?)?$",
    re.ASCII,
)

# V8 legacy：非补零短横线日期（本地时区），时间可选。
_DASH_LOOSE_RE = re.compile(
    r"^(?P<y>\d{4})-(?P<mo>\d{1,2})-(?P<d>\d{1,2})"
    r"(?:[ ](?P<H>\d{1,2}):(?P<M>\d{2})(?::(?P<S>\d{2})(?:\.(?P<frac>\d+))?)?)?$",
    re.ASCII,
)

# V8 legacy：纯数字串（1~3 位 / 5 位；4 位被 _ISO_RE 先行捕获走 UTC）。
_NUMERIC_RE = re.compile(r"^\d{1,3}$|^\d{5}$", re.ASCII)

# V8 legacy：点分段（两段 M.D / M.Y（'11.0001'）/ 三段 M.D.Y 或 Y.M.D）。
_DOT_TWO_RE = re.compile(r"^(\d{1,2})\.(\d{1,4})$", re.ASCII)
_DOT_THREE_RE = re.compile(r"^(\d{1,4})\.(\d{1,2})\.(\d{1,2})$", re.ASCII)

# V8 legacy 缺省年（oracle 实测常量：'3'→2001-03-01、'5.6'→2001-05-06）。
_DEFAULT_YEAR = 2001


def _yy_year(raw: int) -> int | None:
    """ES yy 世纪规则：≤49 → 20nn、50-99 → 19nn；≥100 直取。"""
    if raw <= 49:
        return 2000 + raw
    if raw <= 99:
        return 1900 + raw
    return raw


def _days_from_civil(y: int, m: int, d: int) -> int:
    """proleptic Gregorian → 自 1970-01-01 的天数（Howard Hinnant 算法）。

    `datetime` 年份上限 9999，而 V8 Date 支持 ±27 万年——`'12345'`（words 字段
    真实值域）超出 datetime 表达范围，只能算术直算。
    """
    y2 = y - (1 if m <= 2 else 0)
    era = (y2 if y2 >= 0 else y2 - 399) // 400
    yoe = y2 - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


# 本机固定时区偏移（现代日期基准；1970/远古边界在 Windows CRT 会 OSError，
# spec §15 登记：超界年份的本地化按现代固定偏移近似）。
try:
    _LOCAL_FIXED_OFFSET = datetime(2000, 1, 15).astimezone().utcoffset()
except (OSError, ValueError):
    _LOCAL_FIXED_OFFSET = timedelta(0)


def _local_date(y: int, mo: int, d: int) -> datetime | None:
    """本地时区零点（V8 legacy 形态共用出口）；越界 → None。

    年份在 datetime 范围外（>9999）→ Hinnant 算术 + 固定偏移直接产出 UTC 时刻
    （对齐 V8 '12345' → '+012344-12-31T16:00:00.000Z'）；构造失败 → None。
    """
    if 1 <= y <= 9999:
        return _build(y, mo, d, 0, 0, 0, None, None, default_utc=False)
    try:
        epoch_seconds = _days_from_civil(y, mo, d) * 86400 - _LOCAL_FIXED_OFFSET.total_seconds()
        return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=epoch_seconds)
    except (OSError, ValueError, OverflowError):
        return None


def to_iso_z(moment: datetime) -> str:
    """`Date.prototype.toISOString()` 对应物：恒 UTC、毫秒精度、`Z` 结尾。

    年份 [0,9999] 补零 4 位（Windows 的 `%Y` 对 <1000 年不补零，会产出 `599-…`）；
    超出范围按 ES 规范输出 ±YYYYYY（6 位带符号，'12345' → '+012345-…'）。
    """
    delta_ms = moment.microsecond // 1000
    if 0 <= moment.year <= 9999:
        year_text = f"{moment.year:04d}"
    else:
        sign = "+" if moment.year > 0 else "-"
        year_text = f"{sign}{abs(moment.year):06d}"
    return (f"{year_text}-{moment.month:02d}-{moment.day:02d}"
            f"T{moment.hour:02d}:{moment.minute:02d}:{moment.second:02d}"
            f".{delta_ms:03d}Z")


def _build(y: int, mo: int, d: int, h: int, mi: int, s: int,
           frac: str | None, tz: str | None,
           *, default_utc: bool) -> datetime | None:
    """按部件构造时刻；越界（月/日/时/分/秒非法）→ None（对齐 ES 规范白名单）。

    `default_utc`：纯日期形态按 UTC 零点（ES 规范）；带时间无时区 → 本地时区
    （V8 语义）。时区存在时恒按该时区。
    """
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    if h > 23 or mi > 59 or s > 59:
        return None
    micro = 0
    if frac:
        micro = int((frac + "000000")[:6])
    try:
        naive = datetime(y, mo, d, h, mi, s, micro)
    except ValueError:  # 如 2 月 30 日
        return None
    if tz is not None and tz.upper() == "Z":
        return naive.replace(tzinfo=timezone.utc)
    if tz is not None:
        sign = 1 if tz[0] == "+" else -1
        offset = timedelta(hours=int(tz[1:3]), minutes=int(tz[4:6]))
        return naive.replace(tzinfo=timezone(sign * offset))
    if default_utc:
        return naive.replace(tzinfo=timezone.utc)
    # 本地时区：naive → 本地感知 → UTC。Windows CRT 对远古年份（如 0936）
    # 的 astimezone 会抛 OSError[22]——V8 那边给 LMT 偏移（+08:05:43），
    # Python 无法对齐（spec §15 登记的秒级分歧），回落 UTC 保证不崩。
    try:
        return naive.astimezone()
    except (OSError, ValueError, OverflowError):
        return naive.replace(tzinfo=timezone.utc)


def js_date_iso(text: str) -> str | None:
    """`new Date(text).toISOString()` 的直接对应物；Invalid → `None`。

    与 `js_date_parse` 的差别：年份超出 datetime 范围（5 位数字形态，如
    words 字段的 `"12345"`）时用 proleptic Gregorian 算术直出 ISO 串
    （V8 实测 '12345' → '+012344-12-31T16:00:00.000Z'，本地零点按现代
    固定偏移近似）。
    """
    moment = js_date_parse(text)
    if moment is not None:
        return to_iso_z(moment.astimezone(timezone.utc))
    if _NUMERIC_RE.match(text):
        n = int(text)
        if 13 <= n <= 31 or len(text) not in (3, 5):
            return None
        try:
            epoch_ms = _days_from_civil(n, 1, 1) * 86400000 - round(
                _LOCAL_FIXED_OFFSET.total_seconds() * 1000)
            return _iso_from_epoch_ms(epoch_ms)
        except (OSError, ValueError, OverflowError):
            return None
    return None


def _civil_from_days(days: int) -> tuple[int, int, int]:
    """自 1970-01-01 的天数 → proleptic Gregorian (y, m, d)（Hinnant 逆变换）。"""
    days += 719468
    era = (days if days >= 0 else days - 146096) // 146097
    doe = days - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    return (y + (1 if m <= 2 else 0), m, d)


def _iso_from_epoch_ms(ms: int) -> str:
    """epoch 毫秒 → toISOString 形态（支持 datetime 范围外的年份）。"""
    days, rem = divmod(ms, 86400000)
    y, m, d = _civil_from_days(days)
    h, rem = divmod(rem, 3600000)
    mi, s_ms = divmod(rem, 60000)
    s, ms_part = divmod(s_ms, 1000)
    if 0 <= y <= 9999:
        year_text = f"{y:04d}"
    else:
        year_text = (f"+{y:06d}" if y > 0 else f"-{abs(y):06d}")
    return (f"{year_text}-{m:02d}-{d:02d}"
            f"T{h:02d}:{mi:02d}:{s:02d}.{ms_part:03d}Z")


def js_date_parse(text: str) -> datetime | None:
    """`new Date(text)` 的子集等价物；不可解析 → `None`（对应 Invalid Date）。

    入参约定：调用方已做完参考实现里 `new Date(...)` 之前的两步 replace
    （剥 `D:` 前缀、`±HH'MM'` → `±HH:MM`），本函数不重复处理。
    """
    if not text:
        return None
    match = _ISO_RE.match(text)
    if match:
        parts = match.groupdict()
        if parts["mo"] is None:  # 纯年份
            return _build(int(parts["y"]), 1, 1, 0, 0, 0, None, None,
                          default_utc=True)
        has_time = parts["H"] is not None
        return _build(
            int(parts["y"]), int(parts["mo"]), int(parts["d"] or 1),
            int(parts["H"] or 0), int(parts["M"] or 0), int(parts["S"] or 0),
            parts["frac"], parts["tz"],
            # 日期零点（无时间部分）→ UTC；有时间无时区 → 本地。
            default_utc=not has_time)

    for pattern in (_SPACE_RE, _SLASH_RE, _DASH_LOOSE_RE):
        match = pattern.match(text)
        if match:
            parts = match.groupdict()
            return _build(
                int(parts["y"]), int(parts["mo"]), int(parts["d"]),
                int(parts["H"] or 0), int(parts["M"] or 0), int(parts["S"] or 0),
                parts["frac"], None, default_utc=False)

    # ---- T5 补样本：V8 legacy 数字串 / 点分段（oracle-extract.out.json 钉定）
    if _NUMERIC_RE.match(text):
        n = int(text)
        if 1 <= n <= 12:
            return _local_date(_DEFAULT_YEAR, n, 1)  # month 优先（'3'→2001-03-01）
        if n == 0 or 32 <= n <= 99:  # '0'→2000、'42'→2042、'99'→1999（yy 世纪规则）
            return _local_date(_yy_year(n), 1, 1)
        if 13 <= n <= 31:
            return None  # 仅剩 day 语义、无 month → NaN（'13'/'26'/'31'，oracle 实证）
        return _local_date(n, 1, 1)  # 3 位 / 5 位 → 年直取（'936'、'12345'）

    match = _DOT_TWO_RE.match(text)
    if match:
        a, b = int(match.group(1)), int(match.group(2))
        if len(match.group(2)) >= 3 and a <= 12:
            # '11.0001' → M.Y 形态（V8 实测 2001-11-01：M=11、Y=0001→2001）
            return _local_date(_yy_year(b), a, 1)
        if a == 0:  # '0.5' → a 回落 yy（2000-05-01）
            return _local_date(_yy_year(a), b, 1)
        if a <= 12 and b >= 1:  # '2.3' → 2001-02-03；b=0 → NaN（'1.0'）
            return _local_date(_DEFAULT_YEAR, a, b)
        return None  # a≥13 → NaN（'13.1'、'25.12'）

    match = _DOT_THREE_RE.match(text)
    if match:
        a, b, c = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if a >= 100:  # '2019.1.2' → Y.M.D
            return _local_date(a, b, c)
        year = _yy_year(c)  # '1.2.3' → 2003-01-02、'11.1.0' → 2000-11-01
        if a <= 12 and b >= 1:
            return _local_date(year, a, b)
        return None
    return None
