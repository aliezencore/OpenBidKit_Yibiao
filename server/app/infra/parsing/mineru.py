"""MinerU provider 的注册表与工厂（R-P2c-1 的第三块）。

放在独立模块而不是 `mineru_common`：客户端模块 import `mineru_common`，
工厂反过来要 import 两个客户端——同模块会成环。

扩展名表与标签逐字对齐 `client/electron/services/fileService.cjs:11-22`。
"""

from __future__ import annotations

from app.infra.parsing.mineru_accurate import MineruAccurateParser
from app.infra.parsing.mineru_agent import MineruAgentParser
from app.infra.parsing.mineru_common import BaseMineruParser

# 对齐 `mineruAgentSupportedExtensions`（:17-19）
AGENT_EXTENSIONS = frozenset({
    ".pdf", ".doc", ".docx", ".ppt", ".pptx",
    ".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp",
})
# 对齐 `mineruAccurateSupportedExtensions`（:20-22）：比 agent 多 `.html`
ACCURATE_EXTENSIONS = AGENT_EXTENSIONS | {".html"}

SUPPORTED_EXTENSIONS: dict[str, frozenset[str]] = {
    "mineru-agent-api": AGENT_EXTENSIONS,
    "mineru-accurate-api": ACCURATE_EXTENSIONS,
}

# 对齐 `parserLabels`（:11-14）：供前端展示（P4 的设置/文件列表消费）。
PARSER_LABELS: dict[str, str] = {
    "local": "本地解析",
    "mineru-accurate-api": "MinerU 精准解析 API",
    "mineru-agent-api": "MinerU-Agent 轻量解析 API",
}

MINERU_PROVIDERS = tuple(SUPPORTED_EXTENSIONS)


def build_mineru_parser(provider: str, *, token: str = "") -> BaseMineruParser:
    """按 provider 造客户端；未知 provider 直接 ValueError（配置层已用 Literal 拦一道）。"""
    if provider == "mineru-agent-api":
        return MineruAgentParser()
    if provider == "mineru-accurate-api":
        return MineruAccurateParser(token=token)
    raise ValueError(f"未知的 MinerU provider：{provider}")
