"""Task 6：P3a 端到端闭环验收（本计划唯一的 e2e）。

全链路：HTTP 上传两组 multipart → `POST /analysis` → 轮询终态 → 四段全
`success` → `GET /state` overallProgress == 100 → 第二次分析幂等（直读
task_logs 断言）→ 换文件作废重跑 → 无文件 400。

与既有测试的分工：`test_api_duplicate_files.py` 的任务等待用 `state.tasks.get`
直读任务行；本文件**全部走 HTTP**（`GET /tasks/{id}` 轮询终态），钉的是
「任务引擎经真实 HTTP 面跑通完整闭环」这件事本身。夹具沿用 P1
`tests/test_api_tasks.py:14-19` 的写法。

**计划过时点的修正**（以 brief 为准）：
1. 上传形状是**两组 multipart**（`files=[("tender", ...), ("bid", ...)]`），
   计划里的 `data={"role": ...}` 会 422；
2. 幂等日志断言直读 `client.app.state.yibiao.logs.list_for_task`（
   `/tasks/{id}/logs` 端点不存在）；
3. 变异探针落在 `DuplicateAnalysisRunner._is_up_to_date`（不再涉及
   `previous_state`）；
4. 基线 452（不是计划的 ≥300）。

**SSE 冒烟不做**：`GET /tasks/{id}/events` 在 TestClient 下会死锁（R22，
starlette 1.6.0 + httpx 0.28.1 的传输层把整段响应体缓冲进 BytesIO，无限
SSE 流在 stream 进入处挂死，见 `test_api_tasks.py:55-118` 的注释）；事件
契约本身已由 `tests/test_events.py`（直驱生成器）与 `test_api_tasks.py`
（真实回环服务器）覆盖，不属于本 e2e 的验收点。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

DOCX_MIME = ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document")


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, data_root=tmp_path)
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def _wait_for_task(client, task_id, *, timeout=30.0):
    """轮询任务终态（纯 HTTP：`GET /tasks/{id}`）。

    TestClient 是同步的，任务引擎与应用共用同一事件循环，轮询期间任务会
    真实推进（P1 既有行为，`test_api_duplicate_files.py` 用例 8 已证）。
    """
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        resp = client.get(f"/tasks/{task_id}")
        assert resp.status_code == 200, resp.text
        last = resp.json()
        if last["status"] in {"success", "error", "cancelled", "interrupted"}:
            return last
        time.sleep(0.02)
    raise AssertionError(f"任务未在 {timeout}s 内结束，最后状态：{last}")


def _setup_project(client, tmp_path):
    """建项目 + 上传 tender/bid 两组 multipart（Task 5 F-1 的形状）。

    返回 `(project_id, base)`，`base = /projects/{id}/duplicate`。

    **夹具偏离登记（T4）**：用 `.txt` 而非 docx——P2a 的 docx 解析器在
    `include_images=True` 时显式抛 not-implemented（P2a 裁定 R2，docx 图片
    属 P2b），而 T4 的 content_extraction 按裁定 R-P3b-1 必须
    `include_images=True` 重解析，docx 会让分析必 error。本测试验证的是
    「编排到四段 success」的链路（与格式无关），txt 让链路真实走通；docx
    图片链路待 P2b 落地后回归。
    """
    project = client.post("/projects", json={"name": "查重项目"}).json()["id"]
    base = f"/projects/{project}/duplicate"
    resp = client.post(
        f"{base}/files",
        files=[
            # 长句（≥12 内容字符）才能过 `isInformativeContentSentence`——T7 的
            # stats 断言要求真实切句产出 ≥1 句（7 字短句会被正确地拒掉）。
            ("tender", ("招标.txt",
                        "招标文件要求投标人提供完整的技术方案与报价明细。".encode("utf-8"),
                        "text/plain")),
            ("bid", ("投标A.txt",
                     "投标人已按招标文件要求提供完整技术方案与报价明细。".encode("utf-8"),
                     "text/plain")),
        ])
    assert resp.status_code == 200, resp.text
    return project, base


def _task_messages(client, task_id):
    """直读 task_logs 表，返回该任务的日志消息列表。

    幂等日志断言的最强形状（brief 修正点 2）：`/tasks/{id}/logs` 端点不存在，
    直读仓储证明幂等分支**真的走了**，而不是「碰巧又跑一遍也 success」。
    """
    return [entry.get("message", "")
            for entry in client.app.state.yibiao.logs.list_for_task(task_id)]


# ------------------------------------------------------------------- 用例 1

def test_upload_then_analyze_reaches_success_with_four_sections(client, tmp_path):
    """完整闭环：上传两组 → POST /analysis → 轮询到 success → 四段 success + 100。"""
    _, base = _setup_project(client, tmp_path)
    started = client.post(f"{base}/analysis", json={"force": False})
    assert started.status_code == 200, started.text
    task_id = started.json()["taskId"]

    task = _wait_for_task(client, task_id)
    assert task["status"] == "success", task

    state = client.get(f"{base}/state").json()
    assert state["overallProgress"] == 100, state
    assert set(state["sections"]) == {"metadata", "outline", "content", "image"}
    assert {s["status"] for s in state["sections"].values()} == {"success"}, \
        state["sections"]

    # T7 升级：真实算法产出的 stats 经 `load_analysis` 可读——**键名必须是
    # camelCase**（参考写入侧 :2487-2488/:2613-2615 与仓储读取侧
    # `stats.get("tenderSentenceCount")` 三方一致）。这条断言同时钉住
    # 「snake_case 会让 /state 恒 0」这个已被修复的缺陷。
    outline_analysis = state["outlineAnalysis"]
    content_analysis = state["contentAnalysis"]
    assert outline_analysis["tenderSentenceCount"] >= 1, outline_analysis
    assert content_analysis["totalSentenceCount"] >= 1, content_analysis
    # 元数据段的提取子统计（contentExtraction）走同一 camelCase 键，
    # 且**直接挂在 metadataAnalysis 上**（`_load_metadata_analysis` 的形状，
    # 对齐参考 `updateAnalysis({contentExtraction: ...})`）
    assert state["metadataAnalysis"]["contentExtraction"]["completed"] == 2, \
        state["metadataAnalysis"]


# ------------------------------------------------------------------- 用例 2

def test_second_analysis_is_idempotent_via_api(client, tmp_path):
    """同一批文件连跑两次：第二次命中幂等分支，日志含「无需重复分析」。"""
    _, base = _setup_project(client, tmp_path)
    first = client.post(f"{base}/analysis", json={"force": False})
    assert first.status_code == 200, first.text
    assert _wait_for_task(client, first.json()["taskId"])["status"] == "success"

    second = client.post(f"{base}/analysis", json={"force": False})
    assert second.status_code == 200, second.text
    second_id = second.json()["taskId"]
    assert _wait_for_task(client, second_id)["status"] == "success"

    messages = _task_messages(client, second_id)
    assert any("无需重复分析" in message for message in messages), messages


# ------------------------------------------------------------------- 用例 3

def test_changing_files_invalidates_and_reruns(client, tmp_path):
    """换 bid 文件 → 四段先被作废（overallProgress 归零）→ 重跑到 success。

    **中途的作废断言是探针 2 的敏感点**：只断言「重跑后 success + 100」对
    「save_files 不清四段」这个变异**不敏感**——签名变了本身就会触发重跑并把
    四段覆写成新签名（`test_api_duplicate_files.py` 用例 8 的 docstring 记了
    同款实测：对签名不一致「四段 success」不敏感）。上传是破坏性操作
    （`save_files` 事务内清四段）才是变异后立刻可见的行为。
    """
    _, base = _setup_project(client, tmp_path)
    first = client.post(f"{base}/analysis", json={"force": False})
    assert first.status_code == 200, first.text
    assert _wait_for_task(client, first.json()["taskId"])["status"] == "success"
    assert client.get(f"{base}/state").json()["overallProgress"] == 100

    # 夹具与 _setup_project 保持一致用 .txt（docx + include_images=True 会让
    # content_extraction 必 error——P2a 的 R2 拒绝路径，见 spec §15 跨阶段缺口）。
    # 评审 B B1：初版用 docx 替换时，重跑实际是 metadata 段 error 但
    # `analysis_progress` 对 error 也计 100 → 断言在失败下仍通过（假绿）。
    resp = client.post(
        f"{base}/files",
        files=[("bid", ("投标B.txt", "换了一份投标文件".encode("utf-8"), "text/plain"))])
    assert resp.status_code == 200, resp.text

    # 全量替换语义：只传 bid 组 → tender 一并被清（Task 5 F-1，e2e 面再钉一次）。
    listing = client.get(f"{base}/files").json()
    assert listing["tender"] == [], listing
    assert [f["file_name"] for f in listing["bid"]] == ["投标B.txt"]

    # 作废断言（探针 2 敏感点）：上传后四段必须已归零，不得保留旧 success。
    invalidated = client.get(f"{base}/state").json()
    assert invalidated["overallProgress"] == 0, invalidated

    second = client.post(f"{base}/analysis", json={"force": False})
    assert second.status_code == 200, second.text
    second_id = second.json()["taskId"]
    task = _wait_for_task(client, second_id)
    assert task["status"] == "success", task
    # 评审 B B1 加固：`overallProgress == 100` 对「段 error」不敏感（error 也计
    # 100，见 progress.py 的 analysis_progress），必须显式断言四段真的 success。
    reran = client.get(f"{base}/state").json()
    assert reran["overallProgress"] == 100
    assert {s["status"] for s in reran["sections"].values()} == {"success"}, reran["sections"]

    # 真的重跑了：第二次任务的日志里不得出现幂等文案。
    messages = _task_messages(client, second_id)
    assert not any("无需重复分析" in message for message in messages), messages


# ------------------------------------------------------------------- 用例 4

def test_api_rejects_analysis_without_files(client):
    """全新项目直接 POST /analysis → 400（API 层校验，brief 增补的用例 4）。"""
    project = client.post("/projects", json={"name": "空项目"}).json()["id"]
    resp = client.post(f"/projects/{project}/duplicate/analysis",
                       json={"force": False})
    assert resp.status_code == 400, resp.text
    assert client.app.state.yibiao.tasks.list_for_project(project) == [], \
        "不应提交任务"


# ------------------------------------------------- 用例 5（T7）：真实算法闭环

def test_e2e_duplicate_sentences_and_outline_groups_reachable(client, tmp_path):
    """真实算法闭环：两份内容相同的投标 + 一份招标 → 目录组与正文重复句都可读。

    这是 P3b 的端到端验收：`content_extraction` 重解析 → `outline`/`content`
    段跑真实算法 → `_persist_rows` 落库 → `load_analysis` 组装 → `/state` 可读。
    任何一环断裂（切句、matcher、落库、形状映射）都会让下面的断言之一变红。
    """
    project = client.post("/projects", json={"name": "算法闭环"}).json()["id"]
    base = f"/projects/{project}/duplicate"

    # 投标：相同标题（`#`/`##`）+ 相同长句（跨过 informative 门槛与 6 字下限）
    bid_text = (
        "# 项目概况\n"
        "本项目的服务范围包括设备采购与安装调试工作。\n"
        "## 技术要求\n"
        "投标人应当提供完整的施工组织设计方案与质量保证措施。\n"
    )
    tender_text = "投标人须提供完整的服务方案与质量保证措施。\n"
    resp = client.post(f"{base}/files", files=[
        ("tender", ("招标.txt", tender_text.encode("utf-8"), "text/plain")),
        ("bid", ("投标A.txt", bid_text.encode("utf-8"), "text/plain")),
        ("bid", ("投标B.txt", bid_text.encode("utf-8"), "text/plain")),
    ])
    assert resp.status_code == 200, resp.text

    started = client.post(f"{base}/analysis", json={"force": False})
    assert started.status_code == 200, started.text
    assert _wait_for_task(client, started.json()["taskId"])["status"] == "success"

    state = client.get(f"{base}/state").json()
    assert {s["status"] for s in state["sections"].values()} == {"success"}, \
        state["sections"]

    # 1) 目录：两份投标标题完全相同 → 至少一个 duplicate 组，且覆盖两个文件
    outline = state["outlineAnalysis"]
    groups = outline["duplicateGroups"]
    assert groups, outline
    assert any(len(group["file_ids"]) >= 2 for group in groups), groups
    # pairwise 也应有（两两一对）
    assert outline.get("pairwiseSimilarities"), outline

    # 2) 正文：两份投标的同一长句 → 至少一条重复句，且命中两个 file_id
    content = state["contentAnalysis"]
    duplicates = content["duplicateSentences"]
    assert duplicates, content
    assert any(len(item["file_ids"]) >= 2 for item in duplicates), duplicates
    # 3) stats 的 camelCase 键在同一响应里可读（T7 键名修复的回归钉子）
    assert content["totalSentenceCount"] >= 2, content
    assert outline["tenderSentenceCount"] >= 1, outline
