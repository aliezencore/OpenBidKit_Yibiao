import asyncio

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, data_root=tmp_path, sse_heartbeat_seconds=0.01)
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def test_create_and_get_project(client):
    created = client.post("/projects", json={"name": "示例项目"})

    assert created.status_code == 201
    project_id = created.json()["id"]

    fetched = client.get(f"/projects/{project_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "示例项目"

    listed = client.get("/projects").json()
    assert [item["id"] for item in listed] == [project_id]


def test_get_missing_project_returns_404(client):
    assert client.get("/projects/nope").status_code == 404


def test_health_reports_db_ready(client):
    payload = client.get("/health").json()

    assert payload["status"] == "ok"
    assert payload["db"] == "ready"


def test_task_endpoints_report_queued_placeholders(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]

    assert client.get(f"/projects/{project_id}/tasks").json() == []
    assert client.get("/tasks/missing").status_code == 404
    assert client.post("/tasks/missing/cancel").status_code == 404


def test_sse_endpoint_streams_heartbeat(tmp_path):
    # 事件投递的确定性验证在 tests/test_events.py（直接驱动生成器）；
    # 这里用 0.01s 心跳把 HTTP 契约也钉死，不依赖竞态。
    # R22：starlette 1.6.0 + httpx 0.28.1 的 TestClient 传输层会把整段响应体
    # 缓冲进 BytesIO、仅在 more_body=False 后才构造 Response，导致无限 SSE 流在
    # client.stream(...).__enter__() 处死锁；本环境实测 httpx.ASGITransport 的
    # stream.__aenter__() 同样会挂死（同样的根因）。故退回到裁定允许的“真实回环
    # 服务器”方案：在测试内的后台 asyncio 任务启动 uvicorn.Server（真正流式、会跑
    # lifespan 装配 state），用 httpx.AsyncClient 在回环口上流式读取首行，再发信号
    # 关闭并 await 任务，确保不留进程/端口。两次 asyncio.wait_for 包住“获取流”与
    # “读取首行”——若将来客户端库再次引入缓冲，测试会“快速失败”而非挂死整个套件。
    settings = Settings(_env_file=None, data_root=tmp_path, sse_heartbeat_seconds=0.01)
    asyncio.run(_sse_endpoint_streams_heartbeat(settings))


async def _sse_endpoint_streams_heartbeat(settings: Settings) -> None:
    app = create_app(settings)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_level="warning", timeout_graceful_shutdown=2
    )
    server = uvicorn.Server(config)

    server_task = asyncio.create_task(server._serve())
    loop = asyncio.get_running_loop()
    # 等启动完成：lifespan 跑完（build_state）、端口已绑定（port=0 由 OS 分配）。
    deadline = loop.time() + 10
    while not getattr(server, "servers", None) and not server_task.done():
        await asyncio.sleep(0.02)
        if loop.time() > deadline:
            break
    if server_task.done():
        # 启动即失败，抛出真实异常以便排查。
        await server_task
    port = server.servers[0].sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/tasks/t-sse/events"

    try:
        async with httpx.AsyncClient() as client:
            stream_cm = client.stream("GET", url)
            # 硬超时包住“获取流”——这是原 TestClient 死锁的位置。
            response = await asyncio.wait_for(stream_cm.__aenter__(), timeout=10)
            try:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                assert response.headers["cache-control"] == "no-cache"
                # 硬超时包住“读取首行”。
                first_line = await asyncio.wait_for(
                    response.aiter_lines().__anext__(), timeout=10
                )
                assert first_line == ": ping"
            finally:
                await stream_cm.__aexit__(None, None, None)
    finally:
        server.should_exit = True
        await server_task


def test_task_endpoint_returns_seeded_task(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    state = client.app.state.yibiao
    state.tasks.create(task_id="t1", project_id=project_id, domain="duplicate",
                       kind="duplicate-analysis", payload={"force": True},
                       created_at="2026-09-15T00:00:00+00:00")
    state.tasks.set_progress("t1", 50, "半程")

    body = client.get("/tasks/t1").json()

    assert body["status"] == "queued"
    assert body["progress"] == 50
    assert body["stage_label"] == "半程"
    assert body["payload_json"] == '{"force": true}'


def test_startup_recovers_interrupted_tasks(tmp_path):
    settings = Settings(_env_file=None, data_root=tmp_path)

    with TestClient(create_app(settings)) as first:
        project_id = first.post("/projects", json={"name": "P"}).json()["id"]
        state = first.app.state.yibiao
        state.tasks.create(task_id="t-interrupted", project_id=project_id,
                           domain="duplicate", kind="duplicate-analysis", payload={},
                           created_at="2026-09-15T00:00:00+00:00")
        state.tasks.mark_running("t-interrupted", "2026-09-15T00:00:00+00:00")

    # 模拟进程重启：同一个 data_root 重新装配应用
    with TestClient(create_app(settings)) as second:
        task = second.get("/tasks/t-interrupted").json()

    assert task["status"] == "interrupted"
