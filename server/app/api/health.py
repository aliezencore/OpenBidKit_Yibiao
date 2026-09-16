from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import AppState, get_state

router = APIRouter()


@router.get("/health")
def health(state: AppState = Depends(get_state)) -> dict[str, str]:
    state.db.query_one("SELECT 1")
    return {"status": "ok", "db": "ready"}
