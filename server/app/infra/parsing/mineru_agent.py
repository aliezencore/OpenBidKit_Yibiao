"""MinerU-**Agent** 轻量解析 API 客户端（`fileService.cjs:140-176` / `:177-201`）。

流程：申请上传链接 → PUT 上传 → 轮询任务 → 下载 markdown。
无 Authorization（Agent 免 token）；轮询 3s / 300s 超时。
"""

from __future__ import annotations

from pathlib import Path

from app.infra.parsing.mineru_common import (
    BaseMineruParser,
    MineruError,
    as_json,
    js_json,
    strip_markdown_images,
)

CREATE_PATH = "/api/v1/agent/parse/file"
POLL_PATH_TEMPLATE = "/api/v1/agent/parse/{task_id}"


class MineruAgentParser(BaseMineruParser):
    """对齐 `parseWithMineruAgent`（:140-176）+ `pollMineruAgent`（:177-201）。"""

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("poll_interval", 3.0)
        kwargs.setdefault("poll_timeout", 300.0)
        super().__init__(**kwargs)  # type: ignore[arg-type]

    def parse_file(self, path: Path) -> str:
        path = Path(path)
        file_name = path.name
        created = self._post_json(
            f"{self._base_url}{CREATE_PATH}",
            # body 逐字对齐 :143-149
            {"file_name": file_name, "language": "ch", "enable_table": True,
             "is_ocr": True, "enable_formula": True},
            error_prefix="申请 MinerU-Agent 上传链接失败",
        )
        data = created.get("data") or {}
        task_id = data.get("task_id")
        file_url = data.get("file_url")
        if not task_id or not file_url:
            raise MineruError(
                f"MinerU-Agent 响应缺少 task_id/file_url：{js_json(created)}")

        self._upload(file_url, path)
        final = self._poll(task_id)
        markdown_url = final.get("markdown_url")
        if not markdown_url:
            raise MineruError("MinerU-Agent 解析完成但未返回 markdown_url")
        markdown = self._download_text(markdown_url, "下载 MinerU-Agent Markdown 失败")
        # P2c 不做远端图片导入（计划「已知空白」）：一律按 stripMarkdownImages 语义剥除。
        return strip_markdown_images(markdown)

    def _poll(self, task_id: str) -> dict[str, object]:
        deadline = self._deadline()
        while self._now() < deadline:
            response = self._client.get(
                f"{self._base_url}{POLL_PATH_TEMPLATE.format(task_id=task_id)}")
            payload = as_json(response)
            if not response.is_success or payload.get("code") != 0:
                raise MineruError(
                    f"查询 MinerU-Agent 任务失败：HTTP {response.status_code}，"
                    f"{js_json(payload)}")
            data = payload.get("data") or {}
            state = data.get("state")
            if state == "done":
                return data
            if state == "failed":
                err_code = data.get("err_code")
                suffix = f" ({err_code})" if err_code else ""
                raise MineruError(
                    f"MinerU-Agent 解析失败：{data.get('err_msg') or '未知错误'}{suffix}")
            self._sleeper(self._poll_interval)
        raise MineruError(
            f"MinerU-Agent 轮询超时，请稍后重试，task_id: {task_id}")
