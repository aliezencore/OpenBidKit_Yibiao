"""P3d-3 T6：静态前端挂载（方案 A「路由 + 静态前端」，spec §4.3 D3）。

边界：
- dist 不存在 → create_app 不炸，纯 API 服务照常；
- dist 存在 → `/` 服务 index.html、`/assets/*` 服务构建产物；
- **API 路由优先**（R-P3d3-9）：include_router 先于 mount("/")——API 命中
  不被静态遮蔽，API 的 404 也是 JSON 而非静态 index 兜底。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import _resolve_web_dist, create_app


def _repo_root() -> Path:
    """`server/tests/<file>` 的上两级即仓库根。"""
    return Path(__file__).resolve().parents[2]


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


def test_no_web_source_file_is_gitignored():
    """前端源码不得被 `.gitignore` 吞掉（P3d-3 实测教训，护栏用例）。

    根 `.gitignore` 的 Python 打包段含 `lib/` 模式，会匹配**任意层级**的
    `lib/` 目录 —— `server/web/src/lib/` 曾整目录未进版本库：本地 build
    照常通过（文件在盘上），但干净克隆/CI 直接构建失败。源码目录已改名
    `src/shared/`；本用例把「server/web 下（除 node_modules/dist 外）
    没有任何被忽略的文件」钉成不变量。

    git 不可用或非工作区时跳过（不把环境缺失当失败）。
    """
    root = _repo_root()
    web = root / "server" / "web"
    if not (root / ".git").exists() or not web.is_dir():
        pytest.skip("非 git 工作区或 web 目录缺失")
    files = sorted(
        path.relative_to(root).as_posix()
        for path in web.rglob("*")
        if path.is_file()
        and "node_modules" not in path.parts
        and "dist" not in path.parts
    )
    assert files, "server/web 下应当有源码文件"
    try:
        result = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            cwd=root, input="\n".join(files),
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        pytest.skip("git 不可用")
    if result.returncode not in (0, 1):
        pytest.skip(f"git check-ignore 不可用：{result.returncode}")
    ignored = [line for line in result.stdout.splitlines() if line.strip()]
    assert not ignored, f"以下前端文件被 .gitignore 忽略，会漏出提交：{ignored}"
