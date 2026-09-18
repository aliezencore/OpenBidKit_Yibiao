"""分析签名与稳定 file_id（纯函数，零 I/O）。

逐字对齐参考实现 `client/electron/services/duplicateCheckService.cjs`：

- `createSignature`（:126-131）
- `stableFileId`（:118-120）

**JS 模板字符串的 null 渲染**：`${null}` 在 JS 里得到字面量 `"null"`，而 Python 的
f-string 得到 `"None"`。本模块用 `_js_str` 助手统一按 JS 语义渲染，保证跨语言签名
一致（否则同一批文件在两端算出的 sha1 不同，历史签名的比较会失效）。
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol, Sequence


class FileLike(Protocol):
    """签名入参的鸭子类型契约。

    domain 层不依赖 infra 的类型，只要求调用方传进来的对象具备这三个属性。
    """

    file_path: Any
    size: Any
    modified_at: Any


def _js_str(value: Any) -> str:
    """按 JS 模板字符串的语义渲染单个值。

    `null` / `undefined` 在 JS 模板字符串里都渲染为字面量 `"null"`；Python 的
    对应物只有 `None`，故 `None -> "null"`，其余走 `str()`。
    """
    if value is None:
        return "null"
    return str(value)


def compute_analysis_signature(files: Sequence[Any]) -> str:
    """对文件清单算 sha1 签名。

    对齐 :126-131：

    ```js
    const files = [...tenderFiles, ...bidFiles]
      .filter(Boolean)
      .map((file) => `${file.file_path}|${file.size}|${file.modified_at}`);
    return crypto.createHash('sha1').update(files.join('\\n')).digest('hex');
    ```

    - `.filter(Boolean)` 跳过 `None` 与任何 falsy 项；
    - 逐项渲染为 `path|size|modified`，**保持输入顺序**（顺序敏感）；
    - 以 `\\n` 连接后整体做 sha1。
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
    """
    if explicit_id:
        return explicit_id
    return hashlib.sha1((file_path or file_name or "").encode("utf-8")).hexdigest()
