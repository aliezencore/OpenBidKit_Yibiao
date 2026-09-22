"""P3c Task 5 —— 取图端点 `GET /projects/{pid}/duplicate/assets/{hash}` 的测试。

这是 `preview_url`（`/projects/{pid}/duplicate/assets/<hash>`，相对端点）的落地面。
纪律：`validate_project_id` 在前、`resolve_asset_path` 只认 `<64hex>` 且唯一命中、
**响应里绝不出现服务器路径**（spec §15 硬约束①）。
"""

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.infra.parsing.asset_store import assets_dir_for
from app.main import create_app
from tests.parsing_fixtures import make_image

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-payload"


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, data_root=tmp_path)
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def project(client):
    return client.post("/projects", json={"name": "查重项目"}).json()["id"]


def _write_asset(tmp_path, project_id, digest, *, ext=".png", data=PNG_BYTES):
    assets = assets_dir_for(tmp_path, project_id)
    assets.mkdir(parents=True, exist_ok=True)
    (assets / f"{digest}{ext}").write_bytes(data)
    return assets / f"{digest}{ext}"


def test_serves_png_with_mime_and_exact_bytes(client, project, tmp_path):
    digest = hashlib.sha256(b"one").hexdigest()
    path = _write_asset(tmp_path, project, digest, ext=".png")

    resp = client.get(f"/projects/{project}/duplicate/assets/{digest}")

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.content == path.read_bytes()


def test_serves_jpg_after_transcode(client, project, tmp_path):
    """compress/transcode 后落盘扩展名会变（`.jpg`）——mime 跟着实际文件走。"""
    digest = hashlib.sha256(b"two").hexdigest()
    path = _write_asset(tmp_path, project, digest, ext=".jpg", data=b"\xff\xd8jpg")

    resp = client.get(f"/projects/{project}/duplicate/assets/{digest}")

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("image/jpeg")


def test_unknown_hash_is_404_without_leaking_paths(client, project, tmp_path):
    digest = hashlib.sha256(b"missing").hexdigest()

    resp = client.get(f"/projects/{project}/duplicate/assets/{digest}")

    assert resp.status_code == 404
    # **两种形态都要查**：JSON 会把反斜杠转义（`\\`），只查 `str(tmp_path)` 会漏掉
    # 转义后的泄漏（探针实测：带 data_root 的 detail 在原断言下假绿）。
    body = resp.text
    assert str(tmp_path) not in body, body
    assert str(tmp_path).replace("\\", "\\\\") not in body, body


def test_non_hex_digest_is_404(client, project):
    resp = client.get(f"/projects/{project}/duplicate/assets/not-a-hash")
    assert resp.status_code == 404


def test_traversal_project_id_cannot_read_files(client, tmp_path):
    """`%2e%2e` 形态的 project_id 不得触达盘上读取（同评审 B 的 B1 纪律）。"""
    from app.domain.shared.errors import ParsingError

    victim = tmp_path / "secret.bin"
    victim.write_bytes(b"leak")

    try:
        resp = client.get(
            f"/projects/%2e%2e/duplicate/assets/{'a' * 64}")
        assert resp.status_code != 200
    except ParsingError:
        pass  # 净化失败以异常上抛——同样是「请求没有成功」

    assert victim.read_bytes() == b"leak"
