"""MinerU 两条 API 的共用件（R-P2c-1）。

同步实现（`parse_document` 是同步签名），`transport` 与 `sleeper`/`now` 可注入——
测试用 `httpx.MockTransport` + 快进 sleeper + 虚拟时钟，**不联网、不睡眠**（R-P2c-8）。

移植对齐 `client/electron/services/fileService.cjs`：
- `upload_file` / `download_text` / `download_bytes` ↔ :283-315
- `make_data_id` ↔ :316-318（`[^A-Za-z0-9_.-]+ → _`，截 96，空则 `document`）
- `strip_markdown_images` ↔ :333-338（正则见 R-P2c-6）
- 轮询参数：agent 3s/300s（:172-173）、accurate 5s/600s（:255-256）
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

import httpx

MINERU_BASE_URL = "https://mineru.net"

# 正则纪律：JS 的 \d/\b/\w 是纯 ASCII，Python `re` 默认 Unicode —— 含这些转义的
# 模式一律 re.ASCII（P3b 裁定③）。`\s` 在 JS 与 Python 都含 Unicode 空白，
# **不加** re.ASCII（加了反而窄化，属反向偏离）。
_MARKDOWN_IMAGE_RE = re.compile(
    r'!\[[^\]]*\]\((<[^>]+>|[^)\s]+)(\s+"[^"]*")?\)', re.IGNORECASE)
_HTML_IMAGE_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE | re.ASCII)
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_DATA_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_FULL_MD_RE = re.compile(r"(^|[/\\])full\.md$", re.IGNORECASE | re.ASCII)


class MineruError(RuntimeError):
    """MinerU 运行期错误。文案逐字对齐参考；路由层会再包一层「文件解析失败：」。"""


def js_json(payload: Any) -> str:
    """等价 `JSON.stringify`：无空格分隔、不转义非 ASCII。用于错误文案拼接。"""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def make_data_id(file_name: str) -> str:
    """对齐 `makeDataId`（:316-318）。"""
    cleaned = _DATA_ID_RE.sub("_", file_name or "")
    return cleaned[:96] or "document"


def strip_markdown_images(text: str) -> str:
    """剥除远端图片引用（R-P2c-6）：md 图片语法 → `<img>` 标签 → 折叠空行。"""
    cleaned = _MARKDOWN_IMAGE_RE.sub("", text or "")
    cleaned = _HTML_IMAGE_RE.sub("", cleaned)
    return _BLANK_RUN_RE.sub("\n\n", cleaned)


def is_full_markdown(entry_name: str) -> bool:
    """zip 内是否 `full.md`（对齐 `/(^|[/\\\\])full\\.md$/i`）。"""
    return bool(_FULL_MD_RE.search(entry_name))


class BaseMineruParser:
    """两条 API 的共同骨架：client 持有、注入点、终止条件检查。"""

    def __init__(
        self,
        *,
        base_url: str = MINERU_BASE_URL,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
        poll_interval: float = 3.0,
        poll_timeout: float = 300.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        # connect 30s / read 120s：上传与结果下载都可能较大，参考（fetch 默认）无显式超时。
        self._client = client or httpx.Client(timeout=httpx.Timeout(30.0, read=120.0))
        self._sleeper = sleeper
        self._now = now
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout

    # -- 生命周期 --------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "BaseMineruParser":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- 共用步骤 --------------------------------------------------------

    def _post_json(self, url: str, body: dict[str, Any], *,
                   headers: dict[str, str] | None = None,
                   error_prefix: str) -> dict[str, Any]:
        """POST JSON 并做 `!ok || code != 0` 同判（对齐参考的判定顺序：先取 JSON）。"""
        response = self._client.post(url, json=body, headers=headers)
        payload = as_json(response)
        if not response.is_success or payload.get("code") != 0:
            raise MineruError(
                f"{error_prefix}：HTTP {response.status_code}，{js_json(payload)}")
        return payload

    def _upload(self, file_url: str, path: Path) -> None:
        """PUT 原始字节到 MinerU 预签名地址（:283-289）。"""
        response = self._client.put(file_url, content=path.read_bytes())
        if not response.is_success:
            raise MineruError(
                f"文件上传失败：HTTP {response.status_code}，{response.text}")

    def _download_text(self, url: str, error_prefix: str) -> str:
        response = self._client.get(url)
        if not response.is_success:
            raise MineruError(f"{error_prefix}：HTTP {response.status_code}")
        return response.text

    def _download_bytes(self, url: str) -> bytes:
        response = self._client.get(url)
        if not response.is_success:
            raise MineruError(f"下载 MinerU 精准解析结果失败：HTTP {response.status_code}")
        return response.content

    def _deadline(self) -> float:
        return self._now() + self._poll_timeout


def as_json(response: httpx.Response) -> dict[str, Any]:
    """取 JSON；非 JSON 体按参考的 `await response.json()` 语义直接抛错（不吞）。"""
    try:
        payload = response.json()
    except ValueError as error:  # JSONDecodeError ⊂ ValueError
        raise MineruError(
            f"响应不是合法 JSON：HTTP {response.status_code}，{error}") from error
    if not isinstance(payload, dict):
        raise MineruError(
            f"响应不是 JSON 对象：HTTP {response.status_code}，{js_json(payload)}")
    return payload
