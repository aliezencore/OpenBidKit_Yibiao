"""分析签名与稳定 file_id（纯函数，零 I/O）。

逐字对齐参考实现 `client/electron/services/duplicateCheckService.cjs`：

- `createSignature`（:126-131）
- `stableFileId`（:118-120）

**JS 模板字符串的值渲染**：`` `${x}` `` 走的是 JS 的 `String(x)` 语义，与 Python 的
`str(x)` / f-string **不总是等价**（`true` vs `True`、`10.0` vs `10` 都会改变哈希）。
本模块用 `_js_str` 助手统一按 JS `String()` 语义渲染，保证跨语言签名一致
（否则同一批文件在两端算出的 sha1 不同，历史签名的比较会失效）。
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol, Sequence


class FileLike(Protocol):
    """签名入参的鸭子类型契约。

    domain 层不依赖 infra 的类型，只要求调用方传进来的对象具备这三个属性。
    仅用于 `compute_analysis_signature` 的注解，不做 `isinstance` 运行时判定
    （与 SQLite/JSON 读回来的动态对象配合时用的是 `getattr` 兜底），
    故**不加** `@runtime_checkable`。
    """

    file_path: Any
    size: Any
    modified_at: Any


def _js_str(value: Any) -> str:
    """按 JS `String(value)` 的语义渲染单个值。

    因为 `` `${x}` `` 等价于 `String(x)`，签名里的每一段都必须用 JS 的渲染规则，
    否则跨语言哈希不一致。三条分支的原因：

    - `None -> "null"`：JS 的 `null` 与 `undefined` 在模板字符串里都渲染成
      字面量 `"null"`；Python 的对应物只有 `None`，故都得 `"null"`。
    - `bool -> "true"` / `"false"`：JS 输出小写。**必须最先判**，因为
      `isinstance(True, int)` 为真，若先走数值分支会得到 `"True"` 之外的错误
      形态（布尔在 Python 里是 `int` 的子类）。
    - 整值 `float -> str(int(v))`：JS 的 `String(10.0)` 是 `"10"`（`Number` 没有
      `10.0` 这种表示），而 Python 的 `str(10.0)` 是 `"10.0"`。

    **有意保留的残留差异（见报告 fix-round 1 / F1）**：当整值浮点的
    `str(v)` 用指数记法（`abs(v) >= 1e16`）时，JS 会在 `abs(v) >= 1e21` 才切到
    指数记法。这不是单纯补个「整值就走 int 分支」能消掉的：`1e20` 需要展开成
    21 位数字，`1e21` 又需要退回 `1e+21`。用 `str(v).lstrip("-").isdigit()` 之类的
    守卫判定反而会**更差**（Node 实测：朴素分支 6/19 不符，加守卫后 12/19 不符），
    因为 `str(1e20)` 是 `"1e+20"`、`str(1e16)` 是 `"1e+16"`，守卫会把这些整值浮点
    误判成非整值。此外 JS 还会把 `1e-6` 展开成 `"0.000001"`、`1e-7` 又保留指数
    记法，要全部对齐需要自己写一套 ECMA-262 Number::toString 的
    「最短往返表示 + 小数点位置规则」，复杂度与文件大小/修改时间这类正常入参
    完全不相称。**结论：只覆盖位宽内的整值浮点（含 `123456789.0`、`1e15`），
    指数区间登记为已知未对齐项，不复刻。**
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        # 必须在数值分支之前：`bool` 是 `int` 的子类。
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def compute_analysis_signature(files: Sequence[FileLike | None]) -> str:
    """对文件清单算 sha1 签名。

    对齐 :126-131：

    ```js
    const files = [...tenderFiles, ...bidFiles]
      .filter(Boolean)
      .map((file) => `${file.file_path}|${file.size}|${file.modified_at}`);
    return crypto.createHash('sha1').update(files.join('\\n')).digest('hex');
    ```

    - `.filter(Boolean)` 跳过 `None` 与任何 falsy 项（`0` / `""` / 空值）；
    - 逐项渲染为 `path|size|modified`，**保持输入顺序**（顺序敏感）；
    - 以 `\\n` 连接后整体做 sha1。

    入参注解为 `Sequence[FileLike | None]`：`None` 元素表达「可能混入被
    `filter(Boolean)` 过滤掉的值」这一现实。运行时仍用 `getattr(..., None)` 兜底 ——
    实际调用方是 SQLite/JSON 读回来的动态对象，不保证真的有这三个属性名。
    """
    parts = [
        f"{_js_str(getattr(file, 'file_path', None))}"
        f"|{_js_str(getattr(file, 'size', None))}"
        f"|{_js_str(getattr(file, 'modified_at', None))}"
        for file in files
        if file
    ]
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()


def stable_file_id(file_path: str, file_name: str, *, explicit_id: str | None = None) -> str:
    """返回稳定的 file_id。

    对齐 :119：

    ```js
    return file?.id || crypto.createHash('sha1').update(String(file?.file_path || file?.file_name || '')).digest('hex');
    ```

    - 显式 `explicit_id` 非空则直接使用（JS `||`：空字符串 falsy，会继续回落）；
    - 否则对 `file_path || file_name || ''` 做 sha1 —— **空字符串同样走下一级**。

    **已知并接受的差异（不移除，见报告 fix-round 1 / F7）**：参考实现在 :119 用
    `String(...)` 包裹，因此传入非字符串（如数字）也能渲染；本实现直接
    `.encode("utf-8")`，传入非 `str` 会抛 `AttributeError`。签名已标注为 `str`，
    **非 `str` 入参属于调用方的契约违背**，这里选择快速失败而非静默 `str()` 转换
    （静默转换会掩盖上游的类型错误）。故此差异为「接受」，不修。
    """
    if explicit_id:
        return explicit_id
    return hashlib.sha1((file_path or file_name or "").encode("utf-8")).hexdigest()
