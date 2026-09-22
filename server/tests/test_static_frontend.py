"""P3d-3 T6：静态前端挂载（方案 A「路由 + 静态前端」，spec §4.3 D3）。

边界：
- dist 不存在 → create_app 不炸，纯 API 服务照常；
- dist 存在 → `/` 服务 index.html、`/assets/*` 服务构建产物；
- **API 路由优先**（R-P3d3-9）：include_router 先于 mount("/")——API 命中
  不被静态遮蔽，API 的 404 也是 JSON 而非静态 index 兜底。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import _resolve_web_dist, create_app


def _build_client(tmp_path: Path, web_dist: Path | None) -> TestClient:
    settings = Settings(_env_file=None, data_root=tmp_path / "data",
                        web_dist=web_dist)
    return TestClient(create_app(settings))


def test_app_without_dist_serves_api(tmp_path):
    with _build_client(tmp_path, tmp_path / "missing-dist") as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_index_and_assets_served_when_dist_exists(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><body>yibiao-web</body></html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text(
        "console.log('yibiao');", encoding="utf-8")
    with _build_client(tmp_path, dist) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert "yibiao-web" in index.text
        asset = client.get("/assets/app.js")
        assert asset.status_code == 200
        assert "yibiao" in asset.text


def test_api_routes_take_priority_over_static(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>static-fallback</html>",
                                     encoding="utf-8")
    with _build_client(tmp_path, dist) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        # API 404 保持 JSON 形状（未落进静态兜底）。
        # 注意：duplicate 系端点对 project_id 是自由命名空间（P3a 契约，
        # 不存在也 200 空态），真正 404 的是项目详情端点。
        missing = client.get("/projects/nope")
        assert missing.status_code == 404
        assert missing.headers["content-type"].startswith("application/json")
        assert missing.json()["detail"] == "项目不存在"


def test_default_web_dist_resolves_to_server_web_dist():
    """缺省定位 = `server/web/dist`（与 app/ 平级的 web 目录）。

    只验证解析函数本身的路径形态，不依赖 dist 是否已构建。
    """
    resolved = _resolve_web_dist(Settings(_env_file=None))
    assert resolved is not None
    assert resolved.name == "dist"
    assert resolved.parent.name == "web"
