"""T1：MinerU-Agent 客户端单测（httpx.MockTransport + 快进时钟，不联网不睡眠）。

真值来源：`client/electron/services/fileService.cjs:140-201`
（`parseWithMineruAgent` / `pollMineruAgent` / `uploadFile` / `downloadText` /
`stripMarkdownImages`）——断言里的 URL、body、错误文案逐字对齐。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.infra.parsing.mineru_agent import MineruAgentParser
from app.infra.parsing.mineru_common import (
    MineruError,
    js_json,
    make_data_id,
    strip_markdown_images,
)

BASE = "https://mineru.test"


class FakeClock:
    """虚拟时钟 + 快进 sleeper：每次 sleep 前进固定秒数。"""

    def __init__(self, step: float = 1.0) -> None:
        self.now = 0.0
        self.step = step
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += self.step


def _client(handler, requests: list[httpx.Request]) -> httpx.Client:
    def wrapped(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(wrapped))


def _build(tmp_path: Path, handler, *, clock: FakeClock | None = None):
    source = tmp_path / "投标文件.docx"
    source.write_bytes(b"docx-bytes")
    requests: list[httpx.Request] = []
    clock = clock or FakeClock()
    parser = MineruAgentParser(
        base_url=BASE,
        client=_client(handler, requests),
        sleeper=clock.sleep,
        now=clock.time,
    )
    return parser, source, requests, clock


def _ok_response(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


def test_success_flow_request_shape_and_result(tmp_path):
    """成功路径：申请链接 → 上传 → 轮询（含一帧 running）→ 下载 markdown。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _ok_response({"code": 0, "data": {
                "task_id": "tk-1", "file_url": f"{BASE}/upload/tk-1"}})
        if request.method == "PUT":
            return httpx.Response(200)
        if request.url.path == "/api/v1/agent/parse/tk-1":
            polls = [r for r in requests if r.method == "GET"
                     and r.url.path == "/api/v1/agent/parse/tk-1"]
            if len(polls) == 1:
                return _ok_response({"code": 0, "data": {"state": "running"}})
            return _ok_response({"code": 0, "data": {
                "state": "done", "markdown_url": f"{BASE}/result/tk-1.md"}})
        if request.url.path == "/result/tk-1.md":
            return httpx.Response(200, text="# 标题\n\n![图](http://x/y.png)\n\n\n\n正文")
        raise AssertionError(f"未预期的请求：{request.method} {request.url}")

    parser, source, requests, clock = _build(tmp_path, handler)

    markdown = parser.parse_file(source)

    # 1) 创建请求：URL + body 逐字（:142-149），且无 Authorization
    create = requests[0]
    assert create.method == "POST"
    assert str(create.url) == f"{BASE}/api/v1/agent/parse/file"
    assert json.loads(create.content) == {
        "file_name": "投标文件.docx", "language": "ch",
        "enable_table": True, "is_ocr": True, "enable_formula": True}
    assert "Authorization" not in create.headers
    # 2) 上传是 PUT 且带原始字节（:283-289）
    upload = requests[1]
    assert upload.method == "PUT"
    assert str(upload.url) == f"{BASE}/upload/tk-1"
    assert upload.content == b"docx-bytes"
    # 3) 轮询 3s 间隔一次（running → done），URL 形态对齐 :183
    polls = [r for r in requests if r.method == "GET" and r.url.path.endswith("/tk-1")]
    assert [str(r.url) for r in polls] == [f"{BASE}/api/v1/agent/parse/tk-1"] * 2
    assert clock.sleeps == [3.0]
    # 4) 下载 markdown 且**剥除图片**（:170-174 + stripMarkdownImages :333-338）
    assert markdown == "# 标题\n\n正文"
    assert requests[-1].url.path == "/result/tk-1.md"


def test_create_failure_message_is_verbatim(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": 1, "msg": "bad"})

    parser, source, _, _ = _build(tmp_path, handler)
    with pytest.raises(MineruError) as error:
        parser.parse_file(source)
    assert str(error.value) == (
        f"申请 MinerU-Agent 上传链接失败：HTTP 400，{js_json({'code': 1, 'msg': 'bad'})}")


def test_create_response_missing_fields(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok_response({"code": 0, "data": {"task_id": "tk"}})

    parser, source, _, _ = _build(tmp_path, handler)
    with pytest.raises(MineruError) as error:
        parser.parse_file(source)
    assert str(error.value) == (
        f"MinerU-Agent 响应缺少 task_id/file_url：{js_json({'code': 0, 'data': {'task_id': 'tk'}})}")


def test_upload_failure(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _ok_response({"code": 0, "data": {
                "task_id": "tk", "file_url": f"{BASE}/upload/tk"}})
        if request.method == "PUT":
            return httpx.Response(403, text="denied")
        raise AssertionError("不应到达轮询")

    parser, source, _, _ = _build(tmp_path, handler)
    with pytest.raises(MineruError) as error:
        parser.parse_file(source)
    assert str(error.value) == "文件上传失败：HTTP 403，denied"


def test_poll_failed_state_includes_err_code(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _ok_response({"code": 0, "data": {
                "task_id": "tk", "file_url": f"{BASE}/upload/tk"}})
        if request.method == "PUT":
            return httpx.Response(200)
        return _ok_response({"code": 0, "data": {
            "state": "failed", "err_msg": "页码超限", "err_code": "A0211"}})

    parser, source, _, _ = _build(tmp_path, handler)
    with pytest.raises(MineruError) as error:
        parser.parse_file(source)
    assert str(error.value) == "MinerU-Agent 解析失败：页码超限 (A0211)"


def test_poll_failed_without_err_msg_falls_back_to_unknown(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _ok_response({"code": 0, "data": {
                "task_id": "tk", "file_url": f"{BASE}/upload/tk"}})
        if request.method == "PUT":
            return httpx.Response(200)
        return _ok_response({"code": 0, "data": {"state": "failed"}})

    parser, source, _, _ = _build(tmp_path, handler)
    with pytest.raises(MineruError) as error:
        parser.parse_file(source)
    assert str(error.value) == "MinerU-Agent 解析失败：未知错误"


def test_poll_timeout(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _ok_response({"code": 0, "data": {
                "task_id": "tk", "file_url": f"{BASE}/upload/tk"}})
        if request.method == "PUT":
            return httpx.Response(200)
        return _ok_response({"code": 0, "data": {"state": "pending"}})

    clock = FakeClock(step=100.0)  # 每轮推进 100s → 300s 超时
    parser, source, requests, _ = _build(tmp_path, handler, clock=clock)
    with pytest.raises(MineruError) as error:
        parser.parse_file(source)
    assert str(error.value) == "MinerU-Agent 轮询超时，请稍后重试，task_id: tk"
    # 300/100 = 3 次轮询后截止（不含创建与上传）
    assert len([r for r in requests if r.method == "GET"]) == 3


def test_poll_query_failure_message(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _ok_response({"code": 0, "data": {
                "task_id": "tk", "file_url": f"{BASE}/upload/tk"}})
        if request.method == "PUT":
            return httpx.Response(200)
        return httpx.Response(500, json={"code": 2})

    parser, source, _, _ = _build(tmp_path, handler)
    with pytest.raises(MineruError) as error:
        parser.parse_file(source)
    assert str(error.value) == (
        f"查询 MinerU-Agent 任务失败：HTTP 500，{js_json({'code': 2})}")


def test_missing_markdown_url(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _ok_response({"code": 0, "data": {
                "task_id": "tk", "file_url": f"{BASE}/upload/tk"}})
        if request.method == "PUT":
            return httpx.Response(200)
        return _ok_response({"code": 0, "data": {"state": "done"}})

    parser, source, _, _ = _build(tmp_path, handler)
    with pytest.raises(MineruError) as error:
        parser.parse_file(source)
    assert str(error.value) == "MinerU-Agent 解析完成但未返回 markdown_url"


def test_markdown_download_failure(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _ok_response({"code": 0, "data": {
                "task_id": "tk", "file_url": f"{BASE}/upload/tk"}})
        if request.method == "PUT":
            return httpx.Response(200)
        if request.url.path.endswith(".md"):
            return httpx.Response(404, text="gone")
        return _ok_response({"code": 0, "data": {
            "state": "done", "markdown_url": f"{BASE}/result/tk.md"}})

    parser, source, _, _ = _build(tmp_path, handler)
    with pytest.raises(MineruError) as error:
        parser.parse_file(source)
    assert str(error.value) == "下载 MinerU-Agent Markdown 失败：HTTP 404"


# ---------------------------------------------------------------- 共用件单测

def test_make_data_id_sanitizes_and_truncates():
    """期望值来自 oracle（fileService.cjs 抠出 makeDataId 直跑）——注意 `+` 折叠连续串。"""
    assert make_data_id("投标文件 A.docx") == "_A.docx"
    assert make_data_id("") == "document"
    assert make_data_id("!!!") == "_"
    assert len(make_data_id("a" * 200)) == 96


def test_strip_markdown_images_semantics():
    """期望值来自 oracle（fileService.cjs 抠出 stripMarkdownImages 直跑）。"""
    assert strip_markdown_images(
        "# 标题\n\n![图](http://x/y.png)\n\n\n\n正文") == "# 标题\n\n正文"
    assert strip_markdown_images(
        "前\n\n![alt](http://a/b.png)\n![](<http://a/c.png> \"t\")\n"
        "<img src=\"x\">\n中\n\n\n\n后") == "前\n\n中\n\n后"
    assert strip_markdown_images("") == ""
