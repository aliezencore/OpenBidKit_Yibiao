from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Yibiao Check Service")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
