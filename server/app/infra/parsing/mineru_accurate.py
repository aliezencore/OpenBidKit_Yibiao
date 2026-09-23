"""MinerU **精准解析** API 客户端（`fileService.cjs:203-273` / `:274-289` / `:291-311`）。

流程：Bearer token 校验 → 申请批量上传链接 → PUT 上传 → 轮询批次结果
→ 下载结果 zip → 抽 `full.md`。轮询 5s / 600s 超时。
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from app.infra.parsing.mineru_common import (
    BaseMineruParser,
    MineruError,
    as_json,
    is_full_markdown,
    js_json,
    make_data_id,
    strip_markdown_images,
)

CREATE_PATH = "/api/v4/file-urls/batch"
POLL_PATH_TEMPLATE = "/api/v4/extract-results/batch/{batch_id}"


def extract_markdown_from_zip(zip_bytes: bytes) -> str:
    """对齐 `extractMarkdownFromZip`（:291-311）：`full.md`（任意层级）优先，否则任一 `.md`。

    偏离（登记）：zip 损坏时参考抛 AdmZip 的英文库错误，本实现抛中文 `MineruError`；
    错误文案不可能逐字对齐库内部，语义等价（两者最终都被包成「文件解析失败：…」）。
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as error:
        raise MineruError(f"解析结果 zip 无效：{error}") from error
    with archive:
        names = archive.namelist()
        full = next((name for name in names if is_full_markdown(name)), None)
        any_md = next((name for name in names if name.lower().endswith(".md")), None)
        target = full or any_md
        if target is None:
            raise MineruError("MinerU 精准解析结果 zip 中未找到 Markdown 文件")
        return archive.read(target).decode("utf-8")


class MineruAccurateParser(BaseMineruParser):
    """对齐 `parseWithMineruAccurate`（:203-234）+ `pollMineruAccurate`（:236-257）。"""

    def __init__(self, *, token: str = "", **kwargs: object) -> None:
        kwargs.setdefault("poll_interval", 5.0)
        kwargs.setdefault("poll_timeout", 600.0)
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._token = token or ""

    @property
    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def parse_file(self, path: Path) -> str:
        # 参考 :204-206：**发请求之前**就校验 token
        if not self._token:
            raise MineruError("请先在设置中填写 MinerU Token")
        path = Path(path)
        file_name = path.name
        created = self._post_json(
            f"{self._base_url}{CREATE_PATH}",
            # body 逐字对齐 :210-219
            {"files": [{"name": file_name, "data_id": make_data_id(file_name),
                        "is_ocr": True}],
             "model_version": "vlm", "language": "ch",
             "enable_table": True, "enable_formula": True},
            headers=self._auth_headers,
            error_prefix="申请 MinerU 精准解析上传链接失败",
        )
        data = created.get("data") or {}
        batch_id = data.get("batch_id")
        file_urls = data.get("file_urls") or []
        file_url = file_urls[0] if file_urls else None
        if not batch_id or not file_url:
            raise MineruError(
                f"MinerU 精准解析响应缺少 batch_id/file_url：{js_json(created)}")

        self._upload(file_url, path)
        item = self._poll(batch_id, file_name)
        full_zip_url = item.get("full_zip_url")
        if not full_zip_url:
            raise MineruError("MinerU 精准解析完成但未返回 full_zip_url")
        zip_bytes = self._download_bytes(full_zip_url)
        # P2c 不做远端图片导入：按 !preserveImages 分支剥除图片（:291-300 的 else 路径）。
        return strip_markdown_images(extract_markdown_from_zip(zip_bytes))

    def _poll(self, batch_id: str, file_name: str) -> dict[str, object]:
        deadline = self._deadline()
        headers = {**self._auth_headers, "Accept": "*/*"}
        while self._now() < deadline:
            response = self._client.get(
                f"{self._base_url}{POLL_PATH_TEMPLATE.format(batch_id=batch_id)}",
                headers=headers)
            payload = as_json(response)
            if not response.is_success or payload.get("code") != 0:
                raise MineruError(
                    f"查询 MinerU 精准解析任务失败：HTTP {response.status_code}，"
                    f"{js_json(payload)}")
            items = (payload.get("data") or {}).get("extract_result") or []
            # 对齐 :247：按 file_name 命中，缺则取首个
            item = next(
                (candidate for candidate in items
                 if candidate.get("file_name") == file_name),
                None) or (items[0] if items else None)
            state = (item or {}).get("state")
            if state == "done":
                return item
            if state == "failed":
                raise MineruError(
                    f"MinerU 精准解析失败：{(item or {}).get('err_msg') or '未知错误'}")
            self._sleeper(self._poll_interval)
        raise MineruError(
            f"MinerU 精准解析轮询超时，请稍后重试，batch_id: {batch_id}")
