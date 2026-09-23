"""T2：MinerU 精准解析客户端单测（MockTransport + 快进时钟 + 内存 zip）。

真值来源：`client/electron/services/fileService.cjs:203-311`
（`parseWithMineruAccurate` / `pollMineruAccurate` / `extractMarkdownFromZip`）。
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from app.infra.parsing.mineru_accurate import (
    MineruAccurateParser,
    extract_markdown_from_zip,
)
from app.infra.parsing.mineru_common import MineruError, js_json

BASE = "https://mineru.test"
TOKEN = "tok-123"


class FakeClock:
    def __init__(self, step: float = 1.0) -> None:
        self.now = 0.0
        self.step = step
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += self.step


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in entries.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def _build(tmp_path: Path, handler, *, token: str = TOKEN, clock: FakeClock | None = None):
    source = tmp_path / "投标文件.pdf"
    source.write_bytes(b"pdf-bytes")
    requests: list[httpx.Request] = []
    clock = clock or FakeClock()

    def wrapped(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    parser = MineruAccurateParser(
        token=token, base_url=BASE,
        client=httpx.Client(transport=httpx.MockTransport(wrapped)),
        sleeper=clock.sleep, now=clock.time,
    )
    return parser, source, requests, clock


def test_missing_token_fails_before_any_request(tmp_path):
    """参考 :204-206：token 校验先于发请求。"""
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("不应发出任何请求")

    parser, source, requests, _ = _build(tmp_path, handler, token="")
    with pytest.raises(MineruError) as error:
        parser.parse_file(source)
    assert str(error.value) == "请先在设置中填写 MinerU Token"
    assert requests == []


def test_success_flow_request_shape_and_zip_extraction(tmp_path):
    zip_payload = _zip_bytes({
        "images/x.png": "binary",
        "投标文件.md": "# 备选\n",
        "result/full.md": "# 正文\n\n![图](http://x/y.png)\n\n\n\n尾",
    })

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {
                "batch_id": "b-1", "file_urls": [f"{BASE}/upload/b-1"]}})
        if request.method == "PUT":
            return httpx.Response(200)
        if request.url.path == "/api/v4/extract-results/batch/b-1":
            polls = [r for r in requests
                     if r.url.path == "/api/v4/extract-results/batch/b-1"]
            if len(polls) == 1:
                return httpx.Response(200, json={"code": 0, "data": {
                    "extract_result": [{"file_name": "投标文件.pdf", "state": "running"}]}})
            return httpx.Response(200, json={"code": 0, "data": {
                "extract_result": [{"file_name": "投标文件.pdf", "state": "done",
                                    "full_zip_url": f"{BASE}/zip/b-1"}]}})
        if request.url.path == "/zip/b-1":
            return httpx.Response(200, content=zip_payload)
        raise AssertionError(f"未预期的请求：{request.method} {request.url}")

    parser, source, requests, clock = _build(tmp_path, handler)
    markdown = parser.parse_file(source)

    create = requests[0]
    assert str(create.url) == f"{BASE}/api/v4/file-urls/batch"
    assert create.headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(create.content) == {
        # data_id 真值来自 oracle（makeDataId('投标文件.pdf') === '_.pdf'：连续非法串折叠为单个 _）
        "files": [{"name": "投标文件.pdf", "data_id": "_.pdf", "is_ocr": True}],
        "model_version": "vlm", "language": "ch",
        "enable_table": True, "enable_formula": True}
    assert requests[1].method == "PUT"
    assert requests[1].content == b"pdf-bytes"
    # 轮询带 Bearer + Accept（:239-240）
    poll = requests[2]
    assert poll.headers["Authorization"] == f"Bearer {TOKEN}"
    assert poll.headers["Accept"] == "*/*"
    assert clock.sleeps == [5.0]
    # 抽 full.md（优先于任意 .md）并剥图
    assert markdown == "# 正文\n\n尾"


def test_create_failure_message_is_verbatim(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": 401, "msg": "token 无效"})

    parser, source, _, _ = _build(tmp_path, handler)
    with pytest.raises(MineruError) as error:
        parser.parse_file(source)
    assert str(error.value) == (
        f"申请 MinerU 精准解析上传链接失败：HTTP 401，"
        f"{js_json({'code': 401, 'msg': 'token 无效'})}")


def test_create_response_missing_fields(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "data": {"batch_id": "b-1"}})

    parser, source, _, _ = _build(tmp_path, handler)
    with pytest.raises(MineruError) as error:
        parser.parse_file(source)
    assert str(error.value) == (
        "MinerU 精准解析响应缺少 batch_id/file_url："
        f"{js_json({'code': 0, 'data': {'batch_id': 'b-1'}})}")


def test_poll_matches_by_file_name_with_first_item_fallback(tmp_path):
    """批次里有多个条目时按 file_name 命中；不匹配则回退首个（:247）。"""
    zip_payload = _zip_bytes({"full.md": "# 首个\n"})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {
                "batch_id": "b-1", "file_urls": [f"{BASE}/upload/b-1"]}})
        if request.method == "PUT":
            return httpx.Response(200)
        if request.url.path == "/zip/b-1":
            return httpx.Response(200, content=zip_payload)
        return httpx.Response(200, json={"code": 0, "data": {"extract_result": [
            {"file_name": "别的文件.pdf", "state": "done", "full_zip_url": f"{BASE}/zip/b-1"},
            {"file_name": "投标文件.pdf", "state": "failed", "err_msg": "本文件失败"},
        ]}})

    parser, source, _, _ = _build(tmp_path, handler)
    # 命中投标文件.pdf（failed）→ 报错，而不是取首个条目
    with pytest.raises(MineruError) as error:
        parser.parse_file(source)
    assert str(error.value) == "MinerU 精准解析失败：本文件失败"


def test_poll_failed_state(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {
                "batch_id": "b-1", "file_urls": [f"{BASE}/upload/b-1"]}})
        if request.method == "PUT":
            return httpx.Response(200)
        return httpx.Response(200, json={"code": 0, "data": {"extract_result": [
            {"file_name": "投标文件.pdf", "state": "failed"}]}})

    parser, source, _, _ = _build(tmp_path, handler)
    with pytest.raises(MineruError) as error:
        parser.parse_file(source)
    assert str(error.value) == "MinerU 精准解析失败：未知错误"


def test_poll_timeout_uses_600s_budget(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {
                "batch_id": "b-1", "file_urls": [f"{BASE}/upload/b-1"]}})
        if request.method == "PUT":
            return httpx.Response(200)
        return httpx.Response(200, json={"code": 0, "data": {"extract_result": [
            {"file_name": "投标文件.pdf", "state": "pending"}]}})

    clock = FakeClock(step=200.0)
    parser, source, requests, _ = _build(tmp_path, handler, clock=clock)
    with pytest.raises(MineruError) as error:
        parser.parse_file(source)
    assert str(error.value) == "MinerU 精准解析轮询超时，请稍后重试，batch_id: b-1"
    assert len([r for r in requests
                if r.url.path == "/api/v4/extract-results/batch/b-1"]) == 3


def test_poll_query_failure_message(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {
                "batch_id": "b-1", "file_urls": [f"{BASE}/upload/b-1"]}})
        if request.method == "PUT":
            return httpx.Response(200)
        return httpx.Response(503, json={"code": 3})

    parser, source, _, _ = _build(tmp_path, handler)
    with pytest.raises(MineruError) as error:
        parser.parse_file(source)
    assert str(error.value) == (
        f"查询 MinerU 精准解析任务失败：HTTP 503，{js_json({'code': 3})}")


def test_missing_full_zip_url(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {
                "batch_id": "b-1", "file_urls": [f"{BASE}/upload/b-1"]}})
        if request.method == "PUT":
            return httpx.Response(200)
        return httpx.Response(200, json={"code": 0, "data": {"extract_result": [
            {"file_name": "投标文件.pdf", "state": "done"}]}})

    parser, source, _, _ = _build(tmp_path, handler)
    with pytest.raises(MineruError) as error:
        parser.parse_file(source)
    assert str(error.value) == "MinerU 精准解析完成但未返回 full_zip_url"


# ------------------------------------------------------------ zip 抽取单测

def test_extract_markdown_prefers_full_md_at_any_depth():
    payload = _zip_bytes({"投标文件.md": "备选", "deep/nested/full.md": "正文"})
    assert extract_markdown_from_zip(payload) == "正文"


def test_extract_markdown_falls_back_to_any_md():
    payload = _zip_bytes({"readme.txt": "x", "content.md": "唯一候选"})
    assert extract_markdown_from_zip(payload) == "唯一候选"


def test_extract_markdown_missing_md_raises():
    payload = _zip_bytes({"a.txt": "x"})
    with pytest.raises(MineruError) as error:
        extract_markdown_from_zip(payload)
    assert str(error.value) == "MinerU 精准解析结果 zip 中未找到 Markdown 文件"


def test_extract_markdown_bad_zip_raises_chinese_error():
    """偏离登记：参考抛 AdmZip 英文库错误，本实现给中文文案（语义等价）。"""
    with pytest.raises(MineruError) as error:
        extract_markdown_from_zip(b"not-a-zip")
    assert str(error.value).startswith("解析结果 zip 无效：")
