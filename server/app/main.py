from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import duplicate, health, projects, tasks
from app.api.deps import build_state
from app.config import Settings, get_settings


def _resolve_web_dist(settings: Settings) -> Path | None:
    """静态前端目录解析：显式配置优先；缺省取 `server/web/dist`。

    P3d-3（方案 A「路由 + 静态前端」）：产物由 `server/web` 的
    `npm run build` 产出；目录不存在（未构建前端）时**不挂载**——
    纯 API 服务照常可用。
    """
    if settings.web_dist is not None:
        return settings.web_dist
    return Path(__file__).resolve().parent.parent / "web" / "dist"


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.yibiao = build_state(resolved)
        try:
            yield
        finally:
            await app.state.yibiao.aclose()

    app = FastAPI(title="标书检查工具箱", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(tasks.router)
    app.include_router(duplicate.router)
    # 静态挂载**必须**在全部 API 路由之后：mount("/") 只承接未被 API 命中的
    # 路径，注册顺序即优先级（R-P3d3-9，tests/test_static_frontend.py 钉住）。
    web_dist = _resolve_web_dist(resolved)
    if web_dist.is_dir():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    return app


app = create_app()
