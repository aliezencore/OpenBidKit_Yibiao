"""T3：provider 从配置流到解析的 API 级验证（P2c 接线）。

三层各验一件事：
1. `file_parser_provider=mineru-agent-api` + `.txt` → 走**回退本地**且整批 200；
2. 同 provider + `.docx` → 走**远端分支**（把工厂换成假件，验证真被调用）；
3. 两边都不支持的扩展名 → 该行是失败行（文案含解析器与扩展名），整批仍 200。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.parsing_fixtures import build_docx

DOCX_MIME = ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document")


def _client(tmp_path: Path, *, provider: str, token: str = "") -> TestClient:
    settings = Settings(_env_file=None, data_root=tmp_path / "data",
                        file_parser_provider=provider, mineru_token=token)
    return TestClient(create_app(settings))


def _make_project(client: TestClient) -> str:
    return client.post("/projects", json={"name": "P2c"}).json()["id"]


def _markdown_files(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "data" / "projects").rglob("*.md"))


def test_mineru_provider_falls_back_to_local_for_txt(tmp_path):
    with _client(tmp_path, provider="mineru-agent-api") as client:
        project = _make_project(client)
        response = client.post(
            f"/projects/{project}/duplicate/files",
            files=[("bid", ("说明.txt", "本地正文".encode(), "text/plain"))])
        assert response.status_code == 200
        rows = response.json()["bid"]
        assert len(rows) == 1
        assert rows[0]["error"] is None
        assert "本地正文" in _markdown_files(tmp_path)[0].read_text(encoding="utf-8")


def test_mineru_provider_remote_branch_is_reachable(tmp_path, monkeypatch):
    """把工厂换成假件：证明 provider 命中支持表时**真的**走到远端分支。"""
    calls: list[str] = []

    class FakeParser:
        def parse_file(self, path):
            calls.append(Path(path).name)
            return "# 来自假 MinerU\n\n正文\n"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr("app.infra.parsing.router.build_mineru_parser",
                        lambda provider, *, token="": FakeParser())

    docx = build_docx(tmp_path / "bid.docx", blocks=[("p", "本地可见正文")])
    with _client(tmp_path, provider="mineru-agent-api", token="tok") as client:
        project = _make_project(client)
        response = client.post(
            f"/projects/{project}/duplicate/files",
            files=[("bid", ("bid.docx", docx.read_bytes(), DOCX_MIME))])
        assert response.status_code == 200
        assert response.json()["bid"][0]["error"] is None
        assert calls == ["bid.docx"]
        assert "来自假 MinerU" in _markdown_files(tmp_path)[0].read_text(encoding="utf-8")


def test_unsupported_by_provider_and_local_becomes_failed_row(tmp_path):
    with _client(tmp_path, provider="mineru-agent-api") as client:
        project = _make_project(client)
        response = client.post(
            f"/projects/{project}/duplicate/files",
            files=[("bid", ("页面.html", b"<html></html>", "text/html"))])
        assert response.status_code == 200
        rows = response.json()["bid"]
        assert len(rows) == 1
        # **失败行的 error 是错误码**（P3a 契约：文案编码在 file_path 的
        # `upload-failed:<code>` 里，HTTP 只解出 code）。「文案无法经 API 取到」
        # 是 P3a 的既有形状，登记为 P4 前端展示失败行的缺口，不在 P2c 改契约。
        assert rows[0]["error"] == "parser-unsupported"


def test_local_provider_still_rejects_unknown_extension(tmp_path):
    """P2a 行为不变：local provider + 不认识的扩展名仍是失败行（unsupported-extension）。"""
    with _client(tmp_path, provider="local") as client:
        project = _make_project(client)
        response = client.post(
            f"/projects/{project}/duplicate/files",
            files=[("bid", ("程序.exe", b"MZ", "application/octet-stream"))])
        rows = response.json()["bid"]
        assert rows[0]["error"] == "unsupported-extension"


def test_accurate_without_token_yields_failed_row(tmp_path):
    """accurate 缺 token → 失败行（parse-failed）。

    参考的 token 校验发生在任何请求之前（:204-206）；本实现同序，故错误码是
    parse-failed（MineruError 被 router 的归一化收口包成 `文件解析失败：…`）。
    """
    with _client(tmp_path, provider="mineru-accurate-api", token="") as client:
        project = _make_project(client)
        docx = build_docx(tmp_path / "bid.docx", blocks=[("p", "正文")])
        response = client.post(
            f"/projects/{project}/duplicate/files",
            files=[("bid", ("bid.docx", docx.read_bytes(), DOCX_MIME))])
        assert response.status_code == 200
        rows = response.json()["bid"]
        assert rows[0]["error"] == "parse-failed"
