from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, projects, tasks
from app.api.deps import build_state
from app.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.yibiao = build_state(resolved)
        try:
            yield
        finally:
            await app.state.yibiao.aclose()

    app = FastAPI(title="Yibiao Check Service", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(tasks.router)
    return app


app = create_app()
