from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import AppState, get_state

router = APIRouter()


class CreateProjectRequest(BaseModel):
    name: str


@router.post("/projects", status_code=201)
def create_project(body: CreateProjectRequest, state: AppState = Depends(get_state)) -> dict:
    project_id = state.projects.create(body.name)
    return state.projects.get(project_id)


@router.get("/projects")
def list_projects(state: AppState = Depends(get_state)) -> list[dict]:
    return state.projects.list_all()


@router.get("/projects/{project_id}")
def get_project(project_id: str, state: AppState = Depends(get_state)) -> dict:
    project = state.projects.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project
