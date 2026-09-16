from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api.deps import AppState, get_state
from app.infra.tasks.events import sse_event_stream

router = APIRouter()


@router.get("/projects/{project_id}/tasks")
def list_project_tasks(project_id: str, state: AppState = Depends(get_state)) -> list[dict]:
    return state.tasks.list_for_project(project_id)


@router.get("/tasks/{task_id}")
def get_task(task_id: str, state: AppState = Depends(get_state)) -> dict:
    task = state.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("/tasks/{task_id}/events")
def task_events(task_id: str, state: AppState = Depends(get_state)) -> StreamingResponse:
    return StreamingResponse(
        sse_event_stream(state.bus, task_id,
                         heartbeat_seconds=state.settings.sse_heartbeat_seconds),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, state: AppState = Depends(get_state)) -> dict:
    if state.tasks.get(task_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"cancelled": state.engine.cancel(task_id)}
