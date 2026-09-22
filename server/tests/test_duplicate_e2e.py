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

import re
import time
from pathlib import Path

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


def _docx_bytes(tmp_path, name, text, *, color=(200, 30, 30)):
    """造一个**带图片**的 docx 并返回字节（e2e 全走 HTTP，故需要字节而非路径）。

    每份文件用不同颜色的图片，避免两份文件因图片字节相同而共享同一份资产
    （同一份资产当然是设计使然，但那样就测不到「多份资产各自落盘」这条路径）。
    """
    from tests.parsing_fixtures import build_docx_with_images, make_image

    image = make_image(tmp_path / f"{Path(name).stem}.png", size=(40, 30), color=color)
    path = build_docx_with_images(
        tmp_path / name, blocks=[("p", text)], images=[image])
    return path.read_bytes()


def _setup_project(client, tmp_path):
    """建项目 + 上传 tender/bid 两组 multipart（Task 5 F-1 的形状）。

    返回 `(project_id, base)`，`base = /projects/{id}/duplicate`。

    **夹具已回归 docx（P2a↔P2b 接线后）**：这里曾因「P2a 的 docx 解析器在
    `include_images=True` 时显式拒绝」而临时改用 `.txt`（见 spec §15 的跨阶段缺口
    登记）。接线完成后 docx 走真实图片链路——本夹具因此比 txt 覆盖更多：
    解析带占位、资产落盘、下游段消费同一条链路。
    """
    project = client.post("/projects", json={"name": "查重项目"}).json()["id"]
    base = f"/projects/{project}/duplicate"
    resp = client.post(
        f"{base}/files",
        files=[
            # 长句（≥12 内容字符）才能过 `isInformativeContentSentence`——T7 的
            # stats 断言要求真实切句产出 ≥1 句（7 字短句会被正确地拒掉）。
            ("tender", ("招标.docx", _docx_bytes(
                tmp_path, "招标.docx",
                "招标文件要求投标人提供完整的技术方案与报价明细。"), DOCX_MIME)),
            ("bid", ("投标A.docx", _docx_bytes(
                tmp_path, "投标A.docx",
                "投标人已按招标文件要求提供完整技术方案与报价明细。",
                color=(10, 120, 200)), DOCX_MIME)),
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
    # 精确句数（T7 评审 A §4.5）：夹具各 1 句，钉死「夹具 ↔ 切句器 ↔ 段统计」契约。
    assert outline_analysis["tenderSentenceCount"] == 1, outline_analysis
    assert content_analysis["totalSentenceCount"] == 1, content_analysis
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

    # 夹具与 `_setup_project` 一致用 docx（P2a↔P2b 接线后 docx 是生产路径）。
    # 评审 B B1：初版用 docx 替换时，重跑实际是 metadata 段 error 但
    # `analysis_progress` 对 error 也计 100 → 断言在失败下仍通过（假绿）。
    # 那正是当初把夹具退回 txt 的原因；接线完成后 docx 走通，本用例因此恢复了
    # 「真实格式 + 显式四段 success 断言」的判别力（下面 `assert ... section` 已加）。
    resp = client.post(
        f"{base}/files",
        files=[("bid", ("投标B.docx", _docx_bytes(
            tmp_path, "投标B.docx", "换了一份投标文件的内容与报价明细。",
            color=(30, 160, 30)), DOCX_MIME))])
    assert resp.status_code == 200, resp.text

    # 全量替换语义：只传 bid 组 → tender 一并被清（Task 5 F-1，e2e 面再钉一次）。
    listing = client.get(f"{base}/files").json()
    assert listing["tender"] == [], listing
    assert [f["file_name"] for f in listing["bid"]] == ["投标B.docx"]

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
    # 3) stats 的 camelCase 键在同一响应里可读（T7 键名修复的回归钉子）。
    # **断言精确句数而非 `>=`**（T7 评审 A §4.5）：夹具句的紧凑长度只比
    # `isInformativeContentSentence` 的 ≥12 门槛高 1.75~2.2 倍，用 `>= 1`
    # 会在「门槛上调 / 归一化口径变化」时静默改变判别力。精确值把
    # 「夹具文本 ↔ 切句器 ↔ 段统计」三者的契约钉死：任一环节漂移立刻变红。
    assert content["totalSentenceCount"] == 4, content   # 2 份投标 × 2 句
    assert outline["tenderSentenceCount"] == 1, outline   # 招标 1 句


# --------------------------- 用例 6（T7 评审 A §4.1 的 P1）：content_files 生产接线

def test_state_exposes_content_files_from_persisted_table(client, tmp_path):
    """生产接线钉子：`/state.contentFiles` 必须来自 `duplicate_check_content_files` 表。

    **这条测试钉的是 T7 评审 A §4.1 的 P1**：`content_extraction` 角色的产出
    `rows["content_files"]` 曾被 `_commit_terminal_states` 遗漏——该角色只经
    `_merge_metadata_stats` 把 `stats`/`extraction` 并入 `metadata` 段，`rows` 从未
    交给 `_persist_rows`（后者虽有 `content_files` 分派分支，但**分派表落地了、
    接线没落地**）。后果：该表恒 0 行、`/state.contentFiles` 与
    `/state.metadataAnalysis.contentFiles` 恒 `[]`、`duplicate_check_files.content_hash`
    永不回写，且前端「部分文件正文提取失败」警示永不出现。

    **为什么必须是 e2e**：既有 5 条 `content_files` 测试**全部注入自定义 runner
    直连 repo**（`test_content_extraction_section.py` 直接调 `build_content_extraction`
    后查表），对「编排器是否把该产出接到落库」零覆盖——本缺陷正是在 515 passed
    全绿下存在的。
    """
    _, base = _setup_project(client, tmp_path)
    started = client.post(f"{base}/analysis", json={"force": False})
    assert started.status_code == 200, started.text
    assert _wait_for_task(client, started.json()["taskId"])["status"] == "success"

    state = client.get(f"{base}/state").json()
    content_files = state["contentFiles"]
    # 两个文件都提取成功 → 两行（缺陷态下这里是 []）
    assert len(content_files) == 2, content_files
    assert {row["status"] for row in content_files} == {"success"}, content_files
    assert {row["file_name"] for row in content_files} == {"招标.docx", "投标A.docx"}, \
        content_files
    for row in content_files:
        assert row["content_length"] > 0, row
        # `content_path` 指向盘上真实产物（读取侧形状与参考 store.cjs:636-644 一致）
        assert row["content_path"] and Path(row["content_path"]).exists(), row
        assert row["error"] is None, row

    # 图片链路端到端闭合（P2a↔P2b 接线）：产物 markdown 里的每个
    # `assets/<hash>` 占位都必须能解析到**盘上真实存在的资产**。
    # 判别点刻意分开写：占位是字符串（解析侧产物），资产是文件（落盘侧产物），
    # 两者是两个独立的失败面——P3b 的 P1 正是「产物有了、落盘没接上」这类。
    from app.infra.parsing.asset_store import resolve_asset_path

    project_id = base.split("/")[2]
    for row in content_files:
        markdown = Path(row["content_path"]).read_text(encoding="utf-8")
        digests = re.findall(r"!\[[^\]]*\]\(assets/([0-9a-f]{64})\)", markdown)
        assert digests, markdown
        for digest in digests:
            resolved = resolve_asset_path(
                data_root=tmp_path, project_id=project_id, target=f"assets/{digest}")
            assert resolved is not None and resolved.exists(), (row["file_id"], digest)
    # `metadataAnalysis` 上的同一子对象走另一条读取路径，也必须非空
    assert state["metadataAnalysis"]["contentFiles"] == content_files, \
        state["metadataAnalysis"]

    # 次生交付物：`content_hash` 必须回写（`replace_content_files` 是唯一写点）。
    # 直读表证明「落库真的发生了」，而不是读取侧凭空造出形状。
    project_id = base.split("/")[2]
    db = client.app.state.yibiao.duplicate._db
    rows = db.query(
        "SELECT file_id, content_hash FROM duplicate_check_files WHERE project_id = ?",
        (project_id,))
    assert len(rows) == 2, rows
    assert all(row["content_hash"] for row in rows), rows


# --------------------------- 用例 8（P3c）：图片比对闭环 + preview_url 可取

def test_e2e_duplicate_images_reachable_and_previewable(client, tmp_path):
    """两份投标内嵌**同一张图** → image 段产出跨文件重复项，且 `preview_url` 能取到图。

    全链路：docx 带图上传 → content_extraction 抽占位并落资产 → image 段从 markdown
    抽出现位置、按 target 哈希跨文件合并 → 落库 → `/state.imageAnalysis` 可读 →
    相对端点按 hash 取图（spec §15 硬约束①：全程不出现服务器路径）。
    """
    project = client.post("/projects", json={"name": "图片比对"}).json()["id"]
    base = f"/projects/{project}/duplicate"

    # 两份投标用**同一份 docx 字节**（内嵌同一张图）→ 图片哈希相同
    same_docx = _docx_bytes(
        tmp_path, "投标A.docx",
        "投标人已按招标文件要求提供完整技术方案与报价明细。")
    resp = client.post(f"{base}/files", files=[
        ("tender", ("招标.docx", _docx_bytes(
            tmp_path, "招标.docx",
            "招标文件要求投标人提供完整的技术方案与报价明细。"), DOCX_MIME)),
        ("bid", ("投标A.docx", same_docx, DOCX_MIME)),
        ("bid", ("投标B.docx", same_docx, DOCX_MIME)),
    ])
    assert resp.status_code == 200, resp.text

    started = client.post(f"{base}/analysis", json={"force": False})
    assert started.status_code == 200, started.text
    assert _wait_for_task(client, started.json()["taskId"])["status"] == "success"

    state = client.get(f"{base}/state").json()
    assert {s["status"] for s in state["sections"].values()} == {"success"}, \
        state["sections"]

    images = state["imageAnalysis"]
    assert images["totalImageCount"] == 2, images            # 每份投标各 1 张
    assert [(f["file_id"], f["status"]) for f in images["files"]] == [
        (f["file_id"], "success") for f in images["files"]]

    bid_ids = [f["file_id"] for f in client.get(f"{base}/files").json()["bid"]]
    duplicates = images["duplicateImages"]
    assert len(duplicates) == 1, images
    duplicate = duplicates[0]
    assert duplicate["file_ids"] == bid_ids, duplicate
    assert duplicate["occurrences"] == {fid: 1 for fid in bid_ids}
    assert duplicate["preview_url"] == (
        f"/projects/{project}/duplicate/assets/{duplicate['hash']}")

    # `preview_url` 必须真的能取到图（相对端点 + 图片 mime）。
    # mime 断言只钉「是图片」：落盘扩展名由压缩/转码结果决定（夹具图会被压成 JPEG），
    # 钉死 `image/png` 会把「容器形态」误当成契约。
    preview = client.get(duplicate["preview_url"])
    assert preview.status_code == 200, preview.text
    assert preview.headers["content-type"].startswith("image/"), \
        preview.headers["content-type"]

