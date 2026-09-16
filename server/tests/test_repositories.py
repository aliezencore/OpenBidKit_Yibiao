import pytest

from app.infra.db.engine import Database
from app.infra.db.migrate import apply_schema
from app.infra.repositories.projects import ProjectRepository
from app.infra.repositories.task_logs import TaskLogRepository
from app.infra.repositories.tasks import TaskRepository

NOW = "2026-09-15T00:00:00+00:00"


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "repo.sqlite")
    apply_schema(database)
    yield database
    database.close()


@pytest.fixture
def repos(db):
    return ProjectRepository(db), TaskRepository(db), TaskLogRepository(db)


def test_project_create_and_get(repos):
    projects, _, _ = repos

    project_id = projects.create("示例项目")

    assert project_id
    assert projects.get(project_id)["name"] == "示例项目"
    assert projects.get("missing") is None


def test_task_lifecycle(repos):
    projects, tasks, _ = repos
    project_id = projects.create("P")

    tasks.create(
        task_id="t1", project_id=project_id, domain="duplicate",
        kind="duplicate-analysis", payload={"force": False}, created_at=NOW,
    )
    created = tasks.get("t1")
    assert created["status"] == "queued"
    assert created["progress"] == 0
    assert created["payload_json"] == '{"force": false}'
    assert created["claimed_by"] is None
    assert created["heartbeat_at"] is None

    tasks.mark_running("t1", NOW)
    assert tasks.get("t1")["status"] == "running"
    assert tasks.get("t1")["started_at"] == NOW

    tasks.set_progress("t1", 42, "正在提取元数据")
    assert tasks.get("t1")["progress"] == 42
    assert tasks.get("t1")["stage_label"] == "正在提取元数据"

    tasks.set_checkpoint("t1", {"stage": "outline"})
    assert tasks.get("t1")["checkpoint_json"] == '{"stage": "outline"}'

    tasks.mark_finished("t1", "success", NOW)
    assert tasks.get("t1")["status"] == "success"
    assert tasks.get("t1")["finished_at"] == NOW


def test_task_mark_finished_records_error(repos):
    projects, tasks, _ = repos
    project_id = projects.create("P")
    tasks.create(task_id="t1", project_id=project_id, domain="duplicate",
                 kind="duplicate-analysis", payload={}, created_at=NOW)

    tasks.mark_finished("t1", "error", NOW, error="模型超时")

    assert tasks.get("t1")["error"] == "模型超时"


def test_list_active_returns_only_unfinished(repos):
    projects, tasks, _ = repos
    project_id = projects.create("P")
    for task_id in ("t1", "t2", "t3"):
        tasks.create(task_id=task_id, project_id=project_id, domain="duplicate",
                     kind="duplicate-analysis", payload={}, created_at=NOW)
    tasks.mark_finished("t2", "success", NOW)

    active = tasks.list_active(project_id)

    assert {item["task_id"] for item in active} == {"t1", "t3"}


def test_list_for_project_filters_by_project(repos):
    projects, tasks, _ = repos
    p1 = projects.create("P1")
    p2 = projects.create("P2")
    tasks.create(task_id="t1", project_id=p1, domain="duplicate",
                 kind="duplicate-analysis", payload={}, created_at=NOW)
    tasks.create(task_id="t2", project_id=p2, domain="duplicate",
                 kind="duplicate-analysis", payload={}, created_at=NOW)

    assert [item["task_id"] for item in tasks.list_for_project(p1)] == ["t1"]


def test_task_logs_append_and_list_in_order(repos):
    projects, _, logs = repos
    project_id = projects.create("P")

    logs.append(project_id, "duplicate", "duplicate-analysis", "t1", "第一条", NOW)
    logs.append(project_id, "duplicate", "duplicate-analysis", "t1", "第二条", NOW)

    messages = [item["message"] for item in logs.list_for_task("t1")]

    assert messages == ["第一条", "第二条"]
