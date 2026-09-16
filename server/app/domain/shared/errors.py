from __future__ import annotations


class ParsingError(Exception):
    """解析失败。`code` 是稳定的机器可读码，供 API 与前端分支。"""

    def __init__(self, message: str, *, code: str, path: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.path = path
