"""Task 5：查重文件簿记 API 的 HTTP 契约。

契约来源是 brief `task-5-brief.md` 的「测试要求」12 条 + 参考实现
`client/electron/services/duplicateCheckStore.cjs`。夹具沿用 P1
`tests/test_api_tasks.py:14-19` 的写法（不要另起一套）。
"""

from __future__ import annotations

import json
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


@pytest.fixture
def project(client):
    return client.post("/projects", json={"name": "查重项目"}).json()["id"]


@pytest.fixture
def docx_bytes(tmp_path):
    """真实可解析的 docx（复用 P2a 夹具，不要手搓字节）。"""
    from tests.parsing_fixtures import build_docx

    path = build_docx(tmp_path / "doc.docx", blocks=[("p", "投标文件正文内容")])
    return path.read_bytes()


def _post_files(client, project, *, tender=None, bid=None):
    """两个分组字段全量上传；`None` 表示该分组字段不出现；`[]` 发显式空 part。

    注意：multipart 里「字段不出现」「字段出现但为空」是两件事——前者两组都
    发生时是歧义请求（→400），后者是显式清空该组（fix-round 2 / N-4）。

    「出现但为空」在 multipart 里唯一协议可达的形状是 `filename=""` 的空
    file part（浏览器空 `<input type="file">` 的原生提交形状）。httpx 便捷
    API 发不出它——`files=[("tender", ("", b""))]` 会把空 filename 整个
    省略，part 退化成字符串字段（`Expected UploadFile` → 422）；而
    `data={"tender": ""}` 同样是字符串字段，也是 422。故整个请求体手工编码
    （实测 `httpx.Request(...).read()` 的 body：`name="tender"` 无 filename
    属性）。
    """
    boundary = "YibiaoTestBoundary3141"
    parts: list[bytes] = []
    for group, items in (("tender", tender), ("bid", bid)):
        if items is None:
            continue  # 字段不出现
        if items == []:
            # 显式空组：filename="" 的空 part（0 字节）。
            parts.append(
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="{group}"; filename=""\r\n'
                f'Content-Type: application/octet-stream\r\n\r\n\r\n'.encode())
            continue
        for name, payload in items:
            parts.append(
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="{group}"; '
                f'filename="{name}"\r\n'
                f'Content-Type: {DOCX_MIME}\r\n\r\n'.encode()
                + payload + b'\r\n')
    if not parts:
        # 两个分组字段都不出现（用例 2 的歧义形状）。
        return client.post(f"/projects/{project}/duplicate/files",
                           data={}, files=[])
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    return client.post(
        f"/projects/{project}/duplicate/files", content=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})


def _listing(client, project):
    resp = client.get(f"/projects/{project}/duplicate/files")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _state(client, project):
    resp = client.get(f"/projects/{project}/duplicate/state")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _db(client):
    return client.app.state.yibiao.db


# --------------------------------------------------------------- 必有用例 1

def test_upload_records_role_and_metadata(client, project, docx_bytes):
    resp = _post_files(client, project, tender=[("招标文件.docx", docx_bytes)])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tender"][0]["role"] == "tender"
    assert body["tender"][0]["file_name"] == "招标文件.docx"
    assert body["tender"][0]["size"] == len(docx_bytes)


# --------------------------------------------------------------- 必有用例 2

def test_upload_rejects_ambiguous_request_without_any_group(client, project):
    """两个分组字段都不出现 → 400（F-1 后取代了原「非法 role」用例）。

    新形状下请求体里没有 `role` 字段了，所以「非法 role → 400」这个用例在
    字面上已经不可能存在；它守护的不变量（「形状说不清意图时必须响亮失败，
    不能静默当成清空」）改由「两个分组字段都不出现」来承载。
    """
    resp = client.post(f"/projects/{project}/duplicate/files", data={}, files=[])
    assert resp.status_code == 400, resp.text


# --------------------------------------------------------------- 必有用例 3

def test_upload_replaces_all_files_not_just_same_role(client, project, docx_bytes):
    """F-1 判别：全量替换两组，不是按 role 替换。

    先传 tender+bid，再**只传 bid**；按 role 替换的实现会保留 tender，本用例会红。
    """
    _post_files(client, project,
                tender=[("招标.docx", docx_bytes)],
                bid=[("投标a.docx", docx_bytes)])
    before = _listing(client, project)
    assert [f["file_name"] for f in before["tender"]] == ["招标.docx"]

    _post_files(client, project, bid=[("投标b.docx", docx_bytes)])

    after = _listing(client, project)
    assert after["tender"] == [], "tender 未被清空 → 不是全量替换语义"
    assert [f["file_name"] for f in after["bid"]] == ["投标b.docx"]


# --------------------------------------------------------------- 必有用例 4

def test_state_returns_four_sections_and_overall(client, project):
    body = _state(client, project)
    assert body["overallProgress"] == 0
    assert set(body["sections"]) == {"metadata", "outline", "content", "image"}
    assert body["message"] == "标书查重分析运行中。"
    # F-3：load_analysis 的 camelCase 键原样透传（前端按它消费）
    for key in ("tenderFiles", "bidFiles", "step", "activeAnalysisTab",
                "metadataAnalysis", "outlineAnalysis", "contentAnalysis",
                "imageAnalysis", "contentFiles"):
        assert key in body, f"GET /state 缺少 load_analysis 的键 {key}"


# --------------------------------------------------------------- 必有用例 5

def test_analysis_requires_files(client, project):
    resp = client.post(f"/projects/{project}/duplicate/analysis",
                       json={"force": False})
    assert resp.status_code == 400, resp.text


# --------------------------------------------------------------- 必有用例 6

def test_export_is_not_implemented_in_p3a(client, project):
    assert client.get(f"/projects/{project}/duplicate/export").status_code == 501


# --------------------------------------------------------------- 必有用例 7

def test_upload_clears_sections_and_rewrites_signature(client, project, docx_bytes):
    """F-2 判别：上传 = 破坏性操作，作废四段 + 重写签名。

    预置四段 success + 旧签名 → 上传 → 四段清空（overallProgress 归零）
    且 `current_signature` 等于用当前文件算出的签名。
    """
    state = client.app.state.yibiao
    _post_files(client, project, tender=[("招标.docx", docx_bytes)])
    rows = state.duplicate.list_files(project)
    expected_signature = _signature_of(rows)

    for section in ("metadata", "outline", "content", "image"):
        state.duplicate.save_section(project, section, status="success", progress=100,
                                     message="旧结果", signature="stale-signature",
                                     updated_at="2026-01-01T00:00:00Z")
    state.duplicate.save_meta(project, current_signature="stale-signature")

    resp = _post_files(client, project, tender=[("招标2.docx", docx_bytes)])
    assert resp.status_code == 200, resp.text

    body = _state(client, project)
    assert body["overallProgress"] == 0, "四段未作废 → 上传没有清分析状态"
    meta = state.duplicate.get_meta(project)
    current = _signature_of(state.duplicate.list_files(project))
    assert meta["current_signature"] == current
    assert meta["current_signature"] != "stale-signature"
    assert current != expected_signature  # 换了文件名 → 签名确实变了


# --------------------------------------------------------------- 必有用例 8

def test_upload_and_analysis_signatures_agree(client, project):
    """F-2 硬约束：上传路径与 POST /analysis 路径算出的签名必须一致。

    这是防「两处签名算法不一致」的唯一哨兵。

    **判别点必须落在签名本身，不能只看四段是否 success**：两处算法不一致时，
    编排器 `__call__:335` 的 `self._signature_reader(project_id) != signature`
    为真 → 走 `_discard_previous` 把旧结果作废后**照常重跑**，四段最终**仍是**
    `success`（只是多跑了一次）。所以「四段 success」对签名不一致**不敏感**
    （变异探针 2 实测：12 passed）。真正敏感的断言是：

    1. 上传后库里存的 `current_signature` 恰好等于**编排器据 payload 算出的**
       签名 —— 用 `repo.list_files()` 组装 payload 后按 T4 的算法复算，与
       `current_signature` 逐字节比对；
    2. 跑完后的四段签名也都等于这一个值（而不是「等于另一个值」）。

    这样即便换了实现路径，只要两处不一致，第 1 条立刻红。

    **夹具偏离登记（T4）**：用 `.txt` 而非 docx——P2a 的 docx 解析器在
    `include_images=True` 时显式抛 not-implemented（P2a 裁定 R2，docx 图片
    属 P2b），而 T4 的 content_extraction 按裁定 R-P3b-1 必须
    `include_images=True` 重解析。本测试验证的是签名一致性（与文件格式
    无关），txt 让分析链路真实跑通；docx 图片链路待 P2b 落地后回归。
    """
    state = client.app.state.yibiao
    _post_files(client, project,
                tender=[("招标.txt", "招标要求正文".encode("utf-8"))],
                bid=[("投标.txt", "投标应答正文".encode("utf-8"))])

    # 上传写入的签名
    uploaded = state.duplicate.get_meta(project)["current_signature"]

    first = client.post(f"/projects/{project}/duplicate/analysis", json={"force": False})
    assert first.status_code == 200, first.text
    first_task = first.json()["taskId"]

    # 编排器据「按 list_files 组装的 payload」算出的签名（与 runner 同一算法）
    rows = state.duplicate.list_files(project)
    expected = _signature_from_rows(rows)
    assert uploaded == expected, (
        "上传路径与编排器路径的签名不一致："
        f"上传={uploaded} 编排器={expected}")

    _wait_for_task(state, first_task)
    task = state.tasks.get(first_task)
    assert task["status"] == "success", task

    sections = state.duplicate.get_sections(project)
    assert all(sections[s]["status"] == "success" for s in
               ("metadata", "outline", "content", "image")), sections
    # 跑完后的段签名必须也是同一个值（不是「另一个算法算出的另一个值」）
    assert all(sections[s]["signature"] == expected for s in
               ("metadata", "outline", "content", "image")), {
        s: sections[s]["signature"] for s in ("metadata", "outline", "content", "image")}

    # 第二次：四段已 success 且签名一致 → 命中幂等（runner 直接报 100 返回）
    second = client.post(f"/projects/{project}/duplicate/analysis", json={"force": False})
    assert second.status_code == 200, second.text
    second_task = _wait_for_task(state, second.json()["taskId"])
    sections_after = state.duplicate.get_sections(project)
    assert all(sections_after[s]["status"] == "success" for s in
               ("metadata", "outline", "content", "image")), sections_after
    assert second_task["status"] == "success", second_task
    assert sections_after["metadata"]["signature"] == expected


# --------------------------------------------------------------- 必有用例 9

def test_delete_files_cascades_through_api(client, project, docx_bytes):
    """F-4：不加 CASCADE，改为经 HTTP 面钉住文件级级联的实际行为。"""
    state = client.app.state.yibiao
    _post_files(client, project,
                tender=[("招标.docx", docx_bytes)],
                bid=[("投标.docx", docx_bytes)])
    files = state.duplicate.list_files(project)
    fid = files[0]["file_id"]

    # 手工插满 5 张子表（T2 已实现的 FK CASCADE 才是被钉的对象）。
    _seed_child_rows(state, project, fid)
    # 预置四段为 success —— 否则 (b) 的 overallProgress == 0 是空断言
    # （四段本来就缺段、本来就是 0，无论 DELETE 有没有清段状态都会绿）。
    for section in ("metadata", "outline", "content", "image"):
        state.duplicate.save_section(project, section, status="success", progress=100,
                                     message="旧结果", signature="stale-signature",
                                     updated_at="2026-01-01T00:00:00Z")
    assert _state(client, project)["overallProgress"] == 100, "预置四段失败"

    resp = client.delete(f"/projects/{project}/duplicate/files")
    assert resp.status_code == 200, resp.text

    # (a) 两组皆空
    after = _listing(client, project)
    assert after["tender"] == [] and after["bid"] == []

    # (b) 四段归零（DELETE 必须同时清段状态，不能只删文件）
    assert state.duplicate.get_sections(project) == {}, "DELETE 未清四段状态"
    body = _state(client, project)
    assert body["overallProgress"] == 0

    # (c) 直查库：5 张子表本项目无行
    db = _db(client)
    for table in ("duplicate_check_content_files",
                  "duplicate_check_metadata_items",
                  "duplicate_check_outline_items",
                  "duplicate_check_image_files",
                  "duplicate_check_content_occurrences"):
        rows = db.query(f"SELECT * FROM {table} WHERE project_id = ?", (project,))
        assert rows == [], f"{table} 未级联清空：{rows}"


# -------------------------------------------------------------- 必有用例 10

def test_upload_while_analysis_running_is_signature_guarded(
        client, project, docx_bytes):
    """F-5：不做取消，改为显式表达「running 的旧任务会被签名守卫拦下」。

    预置四段 success + 旧签名 + 一个 `domain="duplicate"` `status="running"` 的
    孤儿任务 → 上传 → 断言四段被清空、旧签名被重写。孤儿任务即使跑完拿旧签名
    写段状态，也会被 T4 的签名守卫拦住，不污染本次上传的状态。
    """
    state = client.app.state.yibiao
    _post_files(client, project, tender=[("招标.docx", docx_bytes)])

    for section in ("metadata", "outline", "content", "image"):
        state.duplicate.save_section(project, section, status="success", progress=100,
                                     message="旧结果", signature="stale-signature",
                                     updated_at="2026-01-01T00:00:00Z")
    state.duplicate.save_meta(project, current_signature="stale-signature")
    state.tasks.create(task_id="duplicate-analysis_orphan", project_id=project,
                       domain="duplicate", kind="duplicate-analysis",
                       payload={"tenderFiles": [], "bidFiles": []},
                       created_at="2026-01-01T00:00:00Z")
    state.tasks.mark_running("duplicate-analysis_orphan", "2026-01-01T00:00:01Z")

    resp = _post_files(client, project, tender=[("新招标.docx", docx_bytes)])
    assert resp.status_code == 200, resp.text

    # 直接断言「四段行不存在」= (b) 方案要守护的不变量本身。
    #
    # fix-round 1（评审 A Important-1）：这里原先是
    # `assert sections == {} or all(s["status"] != "success" or
    # s["signature"] != "stale-signature" ...)`。那个析取式的第二支在「实现没清
    # 四段、但签名已重写」时为真 → **在该断言要守护的语义上退化为恒真**。
    # 现在与用例 9（`:277`）的写法一致：不用 `or` 连接两个都能独立为真的分支。
    assert state.duplicate.get_sections(project) == {}, "上传未清四段状态"
    body = _state(client, project)
    assert body["overallProgress"] == 0


# -------------------------------------------------------------- 必有用例 11

def test_analysis_payload_carries_stored_files(client, project, docx_bytes):
    """payload 的项必须带 `file_path`/`size`/`modified_at`（T4 靠它们算签名）。"""
    state = client.app.state.yibiao
    _post_files(client, project,
                tender=[("招标.docx", docx_bytes)],
                bid=[("投标.docx", docx_bytes)])

    client.post(f"/projects/{project}/duplicate/analysis", json={"force": False})

    tasks = state.tasks.list_for_project(project)
    assert len(tasks) == 1, tasks
    payload = json.loads(tasks[0]["payload_json"])
    assert len(payload["tenderFiles"]) == 1
    assert len(payload["bidFiles"]) == 1
    for item in [*payload["tenderFiles"], *payload["bidFiles"]]:
        assert item["file_path"]
        assert item["size"] == len(docx_bytes)
        # `modified_at` 是文件 mtime（JS `toISOString()` 形状），不是 content hash。
        # 它是签名的第三个分量（`path|size|modified`），形状错了签名就不对齐。
        assert item["modified_at"].endswith("Z"), item["modified_at"]
        assert "T" in item["modified_at"], item["modified_at"]
        assert len(item["modified_at"]) == len("2026-01-01T00:00:00.000Z")


# -------------------------------------------------------------- 必有用例 12

def test_parse_failure_marks_row_error_and_continues(client, project, docx_bytes):
    """一次传两个文件，其中一个坏字节 → 请求仍 200，好文件正常入库。"""
    resp = _post_files(client, project, tender=[
        ("好文件.docx", docx_bytes),
        ("坏文件.docx", b"this is definitely not a docx"),
    ])
    assert resp.status_code == 200, resp.text
    body = resp.json()

    tender = {item["file_name"]: item for item in body["tender"]}
    assert set(tender) == {"好文件.docx", "坏文件.docx"}
    good, bad = tender["好文件.docx"], tender["坏文件.docx"]

    # 好文件：有真路径、真 mtime、无 error
    assert good["file_id"]
    assert good["error"] is None
    assert good["modified_at"], "好文件应带文件 mtime"
    assert not good["file_path"].startswith("upload-failed:")

    # 坏文件：error 痕迹 + 占位路径 + 无 mtime
    #
    # fix-round 2 / N-1：这里原先是
    #     assert bad["file_path"] == f"upload-failed:{bad['error']}"
    # —— **恒真式**：等式两边都由实现产出（`file_path` 是 `_parse_failed_file` 写的，
    # `error` 是 `_error_of` 从同一个 `file_path` 解出来的），只要解码口径自洽就恒成立。
    # 它恰好掩盖了 N-1（重复定义的 `_error_of` → 解码根本没生效、`error` 里带着
    # `\x1f<文件名>`，等式**仍然**成立）。评审 B 早先点名它「接近同义反复」，
    # 修复环 1 漏改，缺陷从它下面溜过。
    #
    # 现在改成**与实现无关的精确断言**：`error` 必须是纯 code，`file_path` 必须是
    # 显式写出的编码形状。任何一侧口径漂移都会红。
    assert bad["error"] == "format-mismatch", bad["error"]
    assert bad["file_path"] == "upload-failed:format-mismatch\x1f坏文件.docx", \
        repr(bad["file_path"])
    assert bad["modified_at"] is None
    assert bad["size"] == len(b"this is definitely not a docx")

    # 坏文件不阻断整批：好文件仍在列表里
    listing = _listing(client, project)
    assert [f["file_name"] for f in listing["tender"]] == ["好文件.docx", "坏文件.docx"]

    # 坏文件的 error 在 GET /files 上也能读到（不是只在 POST 响应里），
    # 且**同样必须是干净 code**（N-1 实测两个端点都泄漏 `\x1f<文件名>`）。
    by_name = {f["file_name"]: f for f in listing["tender"]}
    assert by_name["坏文件.docx"]["error"] == "format-mismatch", \
        repr(by_name["坏文件.docx"]["error"])


# -------------------------------------------- 新增 E（N-1：error 必须是干净 code）

def test_failed_row_error_is_a_clean_code(client, project):
    """N-1 判别：失败行的 `error` 必须是**纯机器码**，不得带编码分隔符或文件名。

    fix-round 1 引入了**重复定义的 `_error_of`**（新定义含 `\\x1f` 解码、
    旧定义不含），Python 取模块级**最后一个**定义 → 新解码是死代码。
    实测两个端点都返回 `'format-mismatch\\x1f坏文件.docx'`。

    这条用例用**精确等值断言**钉住口径（不是 `in` / `not in` 的弱断言），
    并且**两个端点都测**（POST /files 与 GET /files —— 前者走本层的
    `_files_for_role`，后者同样，但两条路径的响应体是分别组装的）。
    """
    resp = _post_files(client, project, tender=[("坏文件.docx", b"not a docx")])
    assert resp.status_code == 200, resp.text

    posted = resp.json()["tender"][0]
    assert posted["error"] == "format-mismatch", repr(posted["error"])
    assert "\x1f" not in posted["error"], repr(posted["error"])
    assert "坏文件.docx" not in posted["error"], repr(posted["error"])

    fetched = _listing(client, project)["tender"][0]
    assert fetched["error"] == "format-mismatch", repr(fetched["error"])
    assert "\x1f" not in fetched["error"], repr(fetched["error"])
    assert "坏文件.docx" not in fetched["error"], repr(fetched["error"])

    # 后端仍保留完整的编码形状（含文件名）——解码是**读出时**做的，
    # 存储形状不能为了「error 干净」而被一起削掉：`file_id` 的单射性
    # （C-1）与签名的敏感性（I-2）都依赖它。
    assert fetched["file_path"] == "upload-failed:format-mismatch\x1f坏文件.docx", \
        repr(fetched["file_path"])


# ------------------------------------- 新增 F（N-4：空组字段 = 显式清空）

def test_empty_group_field_is_explicit_clear(client, project, docx_bytes):
    """N-4 判别：「字段出现但为空」= 显式清空该组，不是 422、不是失败行。

    brief F-1 说「空列表是合法的显式意图」，参考实现里「删到 0 个文件」走的
    就是空列表（`DuplicateCheckPage.tsx:1101` 的
    `persistSelectedFiles(tenderFiles, [])`）。修复前任何「出现但空」的形状
    都 422（`Expected UploadFile`）—— P3d 前端照参考实现改写会直接撞死。

    修复后契约：`filename=""` 的空 part（浏览器空 `<input type="file">` 的
    原生提交形状，`_post_files` 的 `tender=[]` 就是发它）= 显式清空该组；
    与「字段完全不出现」（两组都缺席 → 400，用例 2）的区分保持不变。
    """
    # 铺底：两组各 1 个文件
    _post_files(client, project,
                tender=[("招标.docx", docx_bytes)],
                bid=[("投标.docx", docx_bytes)])
    assert len(_listing(client, project)["tender"]) == 1

    # 场景 1（对照 persistSelectedFiles(tenderFiles, [])）：
    # tender 传真文件、bid 显式空 → 200，tender 全量替换、bid 清空。
    resp = _post_files(client, project,
                       tender=[("招标2.docx", docx_bytes)], bid=[])
    assert resp.status_code == 200, resp.text
    listing = _listing(client, project)
    assert [f["file_name"] for f in listing["tender"]] == ["招标2.docx"]
    assert listing["bid"] == [], \
        f"bid 显式空 part 应清空该组，实际：{listing['bid']}"

    # 场景 2：两组都显式空 = 全清空，不撞 400（两字段都**缺席**才是 400）。
    resp = _post_files(client, project, tender=[], bid=[])
    assert resp.status_code == 200, resp.text
    listing = _listing(client, project)
    assert listing["tender"] == [], \
        f"tender 显式空 part 应清空该组，实际：{listing['tender']}"
    assert listing["bid"] == []

    # 空组不是失败行：库里不能留下 filename="" 的解析失败痕迹。
    rows = _db(client).execute(
        "SELECT file_name FROM duplicate_check_files WHERE project_id = ?",
        (project,)).fetchall()
    assert rows == [], f"空组请求不应留下任何行，实际：{rows}"


# ------------------------------------------------------ 新增 A（C-1 判别）

def test_two_distinct_failed_files_do_not_collide(client, project):
    """C-1 判别：两个不同名、同 error code、同字节数的坏文件不得撞 id。

    fix-round 1 之前，失败行的 `file_id` 只由 `upload-failed:<code>` 决定
    （`stable_file_id(file_path, file_name)` 里 `file_path or file_name` 短路使
    文件名根本不参与），于是两个坏文件算出**同一个 id** → 撞
    `PRIMARY KEY (project_id, file_id)` → 整个 `save_files` 事务回滚 → 500。
    """
    resp = _post_files(client, project, tender=[
        ("坏A.docx", b"not a docx"),
        ("坏B.docx", b"also not a docx"),   # 同 code、字节数相同
    ])
    assert resp.status_code == 200, resp.text
    tender = resp.json()["tender"]
    assert len(tender) == 2, tender
    ids = [f["file_id"] for f in tender]
    assert len(set(ids)) == 2, f"失败行 id 未单射：{ids}"
    # 两个坏文件都必须留在库里（不是「丢一行」）
    assert len(_listing(client, project)["tender"]) == 2


# ------------------------------------------------------ 新增 B（C-1 泄漏面）

def test_no_orphan_copies_when_upload_rolls_back(client, project, docx_bytes, tmp_path):
    """好文件 + 撞 id 的坏文件同批出现时，回滚路径必须清掉已落盘的副本。

    C-1 的第二个后果：`_ingest_upload` 对每个文件**先落盘**，而事务只回滚 DB
    → 磁盘上留下不可回收的孤儿副本（`DELETE /files` 也不删盘）。
    """
    resp = _post_files(client, project, tender=[
        ("好文件.docx", docx_bytes),
        ("坏A.docx", b"not a docx"),
        ("坏B.docx", b"also not a docx"),
    ])
    # 修 C-1 后这条请求本身是成功的（200）；这里断言的是**无论成败都不留孤儿**。
    assert resp.status_code == 200, resp.text

    project_dir = tmp_path / "projects" / project
    on_disk = {p.name for p in project_dir.rglob("*") if p.is_file()} \
        if project_dir.exists() else set()
    rows = client.app.state.yibiao.duplicate.list_files(project)

    # 期待的 `uploads/` 副本：正好是入库行的 `file_path` 文件名（好文件一个）。
    # 坏文件**没有**落盘副本（`ParsingError` 路径一个字节都没写）。
    expected_uploads = {Path(r["file_path"]).name for r in rows
                        if r["file_path"] and not r["file_path"].startswith(
                            "upload-failed:")}
    actual_uploads = {p.name for p in (project_dir / "uploads").glob("*")} \
        if (project_dir / "uploads").exists() else set()
    assert actual_uploads == expected_uploads, (
        f"uploads/ 存在无主副本：{sorted(actual_uploads - expected_uploads)}")

    # `documents/*.md` 的份数必须与「成功落盘的文件数」一致 —— 孤儿副本会让它多出来。
    # 这个断言才是 C-1 泄漏面的**直接**判据：事务回滚后 DB 里只剩 1 行，
    # 而磁盘上残留的孤儿 `.md` 会让实际份数 > 1。
    markdowns = {p.name for p in (project_dir / "documents").glob("*.md")} \
        if (project_dir / "documents").exists() else set()
    assert len(markdowns) == len(expected_uploads), (
        f"documents/ 份数不符：磁盘 {sorted(markdowns)} / 期待 {len(expected_uploads)} 份")
    assert on_disk, "项目目录不该是空的"


# ------------------------------------------------- 新增 C（双项目隔离，面向未来）

def test_save_files_does_not_touch_other_projects(client, docx_bytes):
    """跨项目隔离：对 P1 上传不得影响 P2 的文件行数。

    评审 B 的探针 1 评估指出：「跨项目数据破坏」这个危险面当前**零覆盖**
    （探针 1 变红是「副作用链」而非「跨项目破坏」）。这是面向未来的回归护栏。
    """
    p1 = client.post("/projects", json={"name": "P1"}).json()["id"]
    p2 = client.post("/projects", json={"name": "P2"}).json()["id"]
    _post_files(client, p1, tender=[("p1招标.docx", docx_bytes)])
    _post_files(client, p2, tender=[("p2招标.docx", docx_bytes)])

    state = client.app.state.yibiao
    before = len(state.duplicate.list_files(p2))
    assert before == 1

    _post_files(client, p1, tender=[("p1新招标.docx", docx_bytes)])

    after = state.duplicate.list_files(p2)
    assert len(after) == before, f"P2 的文件行被 P1 的上传影响：{after}"
    assert [f["file_name"] for f in after] == ["p2招标.docx"]


# -------------------------------------------------- 新增 D（I-1 判别）

def test_analysis_rejects_all_failed_rows(client, project, docx_bytes):
    """I-1 判别：全是失败行时 `POST /analysis` → 400，不得跑完并报四段 success。

    fix-round 1 之前唯一的前置校验是「文件表非空」，而失败行同样非空 →
    任务被提交、跑完、四段全 `success`、`task.error` 为 `None`：
    **一个字节可分析内容都没有，却报「查重已完成」**。
    """
    resp = _post_files(client, project, tender=[("坏文件.docx", b"not a docx")])
    assert resp.status_code == 200, resp.text
    assert resp.json()["tender"][0]["error"], "预置失败行不成立"

    analysis = client.post(f"/projects/{project}/duplicate/analysis",
                           json={"force": False})
    assert analysis.status_code == 400, analysis.text
    assert "解析失败" in analysis.json()["detail"], analysis.json()

    state = client.app.state.yibiao
    assert state.tasks.list_for_project(project) == [], "不应提交任务"
    assert state.duplicate.get_sections(project) == {}, "不应写四段状态"


# ------------------------------------------------------------------- 帮手

def _signature_of(rows):
    """用 domain 的签名函数 + runner 的包装手法算入库行的签名。"""
    from app.domain.duplicate.signature import compute_analysis_signature

    class _Ref:
        def __init__(self, item):
            self._item = item

        @property
        def file_path(self):
            return self._item.get("file_path")

        @property
        def size(self):
            return self._item.get("size")

        @property
        def modified_at(self):
            return self._item.get("modified_at")

    ordered = ([r for r in rows if r["role"] == "tender"]
               + [r for r in rows if r["role"] == "bid"])
    return compute_analysis_signature([_Ref(r) for r in ordered])


def _signature_from_rows(rows):
    """`_signature_of` 的别名，语义相同（用例 8 用它表达「编排器会算出这个值」）。"""
    return _signature_of(rows)


def _wait_for_task(state, task_id, timeout=5.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = state.tasks.get(task_id)
        if task and task["status"] in ("success", "error", "cancelled", "interrupted"):
            return task
        time.sleep(0.02)
    return state.tasks.get(task_id)


def _seed_child_rows(state, project, file_id):
    """在 5 张子表里各插一行（content_occurrences 需先有 content_duplicates）。"""
    db = state.db
    db.execute(
        "INSERT INTO duplicate_check_content_files"
        "(project_id, file_id, status, content_length, updated_at)"
        " VALUES (?, ?, 'success', 10, '2026-01-01T00:00:00Z')", (project, file_id))
    db.execute(
        "INSERT INTO duplicate_check_metadata_items"
        "(project_id, id, file_id, key, label, value, sort_order)"
        " VALUES (?, 1, ?, 'k', '标签', 'v', 0)", (project, file_id))
    db.execute(
        "INSERT INTO duplicate_check_outline_items"
        "(project_id, item_id, file_id, level, title, normalized_title,"
        " path_titles_json, normalized_path, source, sort_order)"
        " VALUES (?, 'i1', ?, 1, '标题', '标题', '[]', '', 'tender', 0)",
        (project, file_id))
    db.execute(
        "INSERT INTO duplicate_check_image_files"
        "(project_id, file_id, status, image_count, unique_image_count, updated_at)"
        " VALUES (?, ?, 'success', 1, 1, '2026-01-01T00:00:00Z')", (project, file_id))
    db.execute(
        "INSERT INTO duplicate_check_content_duplicates"
        "(project_id, duplicate_id, sentence, normalized, file_ids_json)"
        " VALUES (?, 'd1', '句子', '句子', '[]')", (project,))
    db.execute(
        "INSERT INTO duplicate_check_content_occurrences"
        "(project_id, id, duplicate_id, file_id, occurrence_count)"
        " VALUES (?, 1, 'd1', ?, 1)", (project, file_id))


# =========================================== P3b Task 6：遗留清单（项 3/4/5）


def _messages(client, task_id):
    """直读 task_logs 表，返回该任务的日志消息列表（同 e2e 的 `_task_messages`）。"""
    return [entry.get("message", "")
            for entry in client.app.state.yibiao.logs.list_for_task(task_id)]


def test_force_true_reruns_and_skips_idempotent_log(client, project):
    """项 3（M-3）：force=True 经 HTTP 直达 → 真的重跑（日志不含幂等文案）。

    对照：同一签名下 force=False 的第二次分析**必须**命中幂等（含「无需重复分析」），
    否则本用例的判据（force 后不含该文案）就没有判别力。
    """
    state = client.app.state.yibiao
    _post_files(client, project,
                tender=[("招标.txt", "招标要求正文".encode("utf-8"))],
                bid=[("投标.txt", "投标应答正文".encode("utf-8"))])

    first = client.post(f"/projects/{project}/duplicate/analysis", json={})
    assert first.status_code == 200, first.text
    assert _wait_for_task(state, first.json()["taskId"])["status"] == "success"

    # 第二次 force=False：签名未变 → 幂等命中（判据的对照面）
    second = client.post(f"/projects/{project}/duplicate/analysis", json={})
    second_id = second.json()["taskId"]
    assert _wait_for_task(state, second_id)["status"] == "success"
    assert any("无需重复分析" in m for m in _messages(client, second_id)), \
        "对照组失效：force=False 的重复分析应命中幂等"

    # 第三次 force=True：必须绕过幂等真的重跑
    third = client.post(f"/projects/{project}/duplicate/analysis",
                        json={"force": True})
    assert third.status_code == 200, third.text
    third_id = third.json()["taskId"]
    assert _wait_for_task(state, third_id)["status"] == "success"
    assert not any("无需重复分析" in m for m in _messages(client, third_id)), \
        "force=True 不得走幂等短路"


def test_state_survives_non_dict_stats_json(client, project):
    """项 4：`stats_json` 是合法 JSON 但非 dict（标量）→ `GET /state` 回落 `{}` 不 500。"""
    state = client.app.state.yibiao
    state.db.execute(
        "INSERT INTO duplicate_check_analysis_sections"
        "(project_id, section, status, progress, message, stats_json, updated_at)"
        " VALUES (?, 'metadata', 'success', 100, '', '123', 'now')",
        (project,))

    resp = client.get(f"/projects/{project}/duplicate/state")
    assert resp.status_code == 200, resp.text
    assert resp.json()["sections"]["metadata"]["stats"] == {}


def test_error_of_legacy_failed_row_without_separator():
    """项 5：`_error_of` 对**旧形状**（只有 code、无 `\\x1f<name>`）仍解出 code。

    把「旧形状兼容」钉成显式契约：`upload-failed:<code>` 与
    `upload-failed:<code>\\x1f<name>` 两种行都返回**干净的 code**；
    非失败行返回 `None`。
    """
    from app.api.duplicate import _error_of

    assert _error_of({"file_path": "upload-failed:format-mismatch"}) == "format-mismatch"
    assert _error_of(
        {"file_path": "upload-failed:format-mismatch\x1f坏文件.docx"}
    ) == "format-mismatch"
    assert _error_of({"file_path": "C:/normal/path.docx"}) is None
    assert _error_of({}) is None
