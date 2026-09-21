"""Task 4：content_extraction 角色接线测试。

覆盖计划 :305-310 的 5 条用例。docx 夹具用 `tests.parsing_fixtures.build_docx`
的**等价真实文档**——本任务实测发现 P2a 的本地 `docx_to_markdown` 在
`include_images=True` 时抛 `not-implemented`（见 `docx.py:12-21`），故 happy-path
改用 `.txt` 真文件（`.txt` + `include_images=True` 在解析器里是 no-op，能跑通
真实重解析链路；runner 逻辑与文件格式无关）。该环境偏离在报告中登记，待 P2b
图片解析落地后改回 build_docx。

参考实现：svc.cjs:2302-2354 `runContentExtraction`。
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.infra.db.engine import Database
from app.infra.db.migrate import apply_schema
from app.infra.repositories.duplicate import DuplicateFileInput, DuplicateRepository
from app.infra.tasks.duplicate_runner import (
    ANALYSIS_RUNNER_ROLES,
    CONTENT_EXTRACTION,
    DuplicateAnalysisRunner,
    SectionOutput,
    SectionRunContext,
    _stub_sections,
)
from app.infra.tasks.duplicate_sections import (
    build_content_extraction,
    content_dir_for,
)


class FakeCtx:
    """TaskContext 最小替身（同 test_duplicate_runner.py 的 FakeCtx）。"""

    project_id = "p1"

    def __init__(self):
        self.progress_calls: list[tuple[int, str]] = []
        self.logs: list[str] = []

    def progress(self, value, message=""):
        self.progress_calls.append((value, message))

    def log(self, message):
        self.logs.append(message)

    def raise_if_cancelled(self):
        return None


@pytest.fixture()
def repo(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    apply_schema(db)
    db.execute("INSERT INTO projects(id, name, created_at) VALUES ('p1','a','now')")
    return DuplicateRepository(db)


@pytest.fixture()
def client(tmp_path):
    """HTTP 层夹具（同 test_duplicate_e2e.py 的形状）。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings = Settings(_env_file=None, data_root=tmp_path)
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def project(client):
    """建一个项目，返回 project_id。"""
    return client.post("/projects", json={"name": "查重项目"}).json()["id"]


def _mk_file(tmp_path, name: str, text: str) -> dict:
    """造一个真实可读的源文件 + 入参 dict（file_id/file_name/file_path/size/...）。

    用 `write_bytes` 落盘（LF 换行）：text 模式在 Windows 会把 ``\\n`` 写成
    ``\\r\\n``，解析器读回后 markdown 里保留 ``\\r``，content_length 会比
    预期多 1（实测踩过）。
    """
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return {
        "file_id": path.stem,  # 文件名即稳定 id（测试内自洽）
        "file_name": name,
        "file_path": str(path),
        "extension": path.suffix.lstrip(".").lower(),
        "size": path.stat().st_size,
        "modified_at": "2026-01-01T00:00:00.000Z",
    }


def _runner_output(tmp_path, files) -> SectionOutput:
    """直接驱动 content_extraction runner，返回 SectionOutput。"""
    runner = build_content_extraction(tmp_path)
    section_ctx = SectionRunContext(
        project_id="p1", section=CONTENT_EXTRACTION, files=list(files))
    return asyncio.run(runner(section_ctx))


# ----------------------------------------------------------- 用例 1：逐文件写盘 + rows

def test_content_extraction_writes_per_file_content_and_rows(tmp_path):
    """tender + bid 各一 → content 目录两份 {file_id}.md、rows 两行 success、
    content_length / content_hash 正确（sha256 手算对照）。

    偏离登记：源文件用 `.txt`（P2a 的 docx 解析器在 include_images=True 时抛
    not-implemented，见 docx.py:12-21）；runner 逻辑与格式无关，.txt 等价覆盖
    「重解析 → 写 content 目录 → 收行 → 算 hash」全链路。
    """
    tender = _mk_file(tmp_path, "tender.txt", "招标正文第一段。\n第二段。")
    bid = _mk_file(tmp_path, "bid.txt", "投标正文内容。")
    output = _runner_output(tmp_path, [tender, bid])

    rows = output.rows["content_files"]
    assert len(rows) == 2
    assert {r["file_id"] for r in rows} == {"tender", "bid"}
    assert all(r["status"] == "success" for r in rows)

    # content 目录两份 .md
    content_dir = content_dir_for(tmp_path, "p1")
    assert (content_dir / "tender.md").exists()
    assert (content_dir / "bid.md").exists()

    # 内容逐字：重解析后 .strip()（参考 :2321 .trim()）
    assert (content_dir / "tender.md").read_text(encoding="utf-8") == \
        "招标正文第一段。\n第二段。".strip()

    # content_length / content_hash 与手算一致
    for file, expected_text in (("tender", "招标正文第一段。\n第二段。"),
                                ("bid", "投标正文内容。")):
        row = next(r for r in rows if r["file_id"] == file)
        stripped = expected_text.strip()
        assert row["content_length"] == len(stripped)
        assert row["content_hash"] == hashlib.sha256(
            stripped.encode("utf-8")).hexdigest()
        # content_path 指向该 md
        assert row["content_path"] == str(content_dir / f"{file}.md")


# ----------------------------------------------------------- 用例 2：重解析带图片（含图片语法）

def test_content_extraction_markdown_includes_images(tmp_path, monkeypatch):
    """content_extraction 调 parse_document 时 **带 include_images=True**（与上传
    解析 documents/ 的 include_images=False 差异，R-P3b-1 的判别证据）。

    环境偏离登记：P2a 本地 docx 解析器在 include_images=True 时抛 not-implemented，
    无法产出真实图片 markdown。故此处用 monkeypatch 的解析桩验证「当请求
    include_images=True 时，正文里会出现图片语法」这条接线语义——桩在
    include_images=True 时返回含 `![](...)` 的 markdown，False 时不含。这把
    R-P3b-1 的判别点（content_extraction 走图片路径、上传解析不走）钉成可运行契约，
    真实图片语法待 P2b 解析器落地后由同一断言覆盖。
    """
    import app.infra.tasks.duplicate_sections as mod

    captured = {}

    def fake_parse(path, *, provider="local", include_images=False):
        from app.infra.parsing.router import ParsedDocument
        captured["include_images"] = include_images
        if include_images:
            return ParsedDocument(
                markdown="![图1](asset://img1.png)\n正文带图。",
                format_kind="docx", extension=".docx", parser_label="local")
        return ParsedDocument(
            markdown="正文不带图。", format_kind="docx", extension=".docx",
            parser_label="local")

    monkeypatch.setattr(mod, "parse_document", fake_parse)

    file = _mk_file(tmp_path, "doc.txt", "任意内容")
    runner = build_content_extraction(tmp_path)
    section_ctx = SectionRunContext(
        project_id="p1", section=CONTENT_EXTRACTION, files=[file])
    output = asyncio.run(runner(section_ctx))

    # 关键接线：content_extraction 必须请求 include_images=True
    assert captured["include_images"] is True
    row = output.rows["content_files"][0]
    assert row["status"] == "success"
    # 带图片的产物 markdown 含图片语法（上传解析 documents/ 无此内容）。
    # 图片语法与桩产出逐字一致（解析器保留 alt 文本是 P2a 既定行为）。
    content = Path(row["content_path"]).read_text(encoding="utf-8")
    assert "![图1](asset://img1.png)" in content
    assert "asset://" in content


# ----------------------------------------------------------- 用例 3：坏文件不连坐整批

def test_content_extraction_failed_file_does_not_poison_batch(
        tmp_path, monkeypatch):
    """一个坏文件（monkeypatch parse_document 抛）→ 其余文件仍 success、failed=True。

    文件顺序把坏文件放中间：正常实现下 good1 success、bad error 行、good2 success；
    变异探针（删掉 catch → 抛出中断整批）下 good2 不会被处理 → 本用例必须红。
    """
    import app.infra.tasks.duplicate_sections as mod

    def boom_on(path, *, provider="local", include_images=False):
        if "bad" in str(path):
            raise RuntimeError("模拟解析失败")
        from app.infra.parsing.router import ParsedDocument
        return ParsedDocument(
            markdown="ok", format_kind="txt", extension=".txt", parser_label="local")

    monkeypatch.setattr(mod, "parse_document", boom_on)

    good1 = _mk_file(tmp_path, "good1.txt", "好文件一")
    bad = _mk_file(tmp_path, "bad.txt", "坏文件")
    good2 = _mk_file(tmp_path, "good2.txt", "好文件二")
    output = _runner_output(tmp_path, [good1, bad, good2])

    rows = {r["file_id"]: r for r in output.rows["content_files"]}
    assert rows["good1"]["status"] == "success"
    assert rows["good2"]["status"] == "success"   # 坏文件之后仍被处理（继续语义）
    assert rows["bad"]["status"] == "error"
    assert "模拟解析失败" in rows["bad"]["error"]
    assert output.failed is True                  # 任一 error → 段 failed


# ----------------------------------------------------------- 用例 4：上传清除 content 目录

def test_upload_clears_duplicate_content_directory(client, project, tmp_path, monkeypatch):
    """先跑一次分析产 content 目录 → 重新上传文件 → 目录被清空/不存在（R-P3b-6）。

    用 `.txt` 真文件：txt + include_images=True 在解析器里是 no-op，分析能真实跑完
    并写出 content 目录（docx 会因 P2a 限制走 error，这里用 txt 保证 happy path）。
    """
    import app.infra.tasks.duplicate_sections as mod

    # 让 content_extraction 用真实解析（txt 路径 include_images=True 可跑通）
    def real_parse(path, *, provider="local", include_images=False):
        from app.infra.parsing.router import parse_document as real
        return real(Path(path), provider=provider, include_images=include_images)

    monkeypatch.setattr(mod, "parse_document", real_parse)

    state = client.app.state.yibiao
    _post_text_files(client, project,
                     tender=[("招标.txt", "招标正文。")],
                     bid=[("投标.txt", "投标正文。")])

    # 跑一次分析，产生 content 目录
    first = client.post(f"/projects/{project}/duplicate/analysis", json={"force": False})
    assert first.status_code == 200, first.text
    _wait_for_task(state, first.json()["taskId"])

    content_dir = content_dir_for(state.settings.data_root, project)
    assert content_dir.exists(), "分析后 content 目录应当存在"
    assert list(content_dir.glob("*.md"))

    # 重新上传文件 → content 目录应被清空
    _post_text_files(client, project,
                     tender=[("招标2.txt", "新的招标正文。")],
                     bid=[("投标2.txt", "新的投标正文。")])
    assert not content_dir.exists() or not list(content_dir.glob("*.md")), \
        "重新上传后 content 目录应被清空"


# ----------------------------------------------------------- 用例 5：stats.completed 断言

def test_content_extraction_stats_reports_completed(repo, tmp_path):
    """content_extraction 的产出 stats 携带 {status, completed, total}，且经主编排
    并入 `metadata` 段 stats 的 `content_extraction` 键（计划 Task 5 :357 修正后的
    用例 5：stats 断言而非 section_progress 桩——content_extraction 不产生进度行）。

    直接用 .txt 真文件驱动完整编排器（注入本 runner），核对 metadata 段 stats 的
    `content_extraction.completed == total`。
    """
    files = [
        _mk_file(tmp_path, "tender.txt", "招标正文。"),
        _mk_file(tmp_path, "bid.txt", "投标正文。"),
    ]
    # 把文件写进 repo（analysis 的 payload 来自 list_files）
    repo.replace_files("p1", "tender", [DuplicateFileInput(
        file_id="tender", file_name="tender.txt", file_path=str(tmp_path / "tender.txt"),
        extension="txt", size=(tmp_path / "tender.txt").stat().st_size,
        modified_at="2026-01-01T00:00:00.000Z")])
    repo.replace_files("p1", "bid", [DuplicateFileInput(
        file_id="bid", file_name="bid.txt", file_path=str(tmp_path / "bid.txt"),
        extension="txt", size=(tmp_path / "bid.txt").stat().st_size,
        modified_at="2026-01-01T00:00:00.000Z")])

    sections = dict(_stub_sections())
    sections[CONTENT_EXTRACTION] = build_content_extraction(tmp_path)
    payload = {
        "tenderFiles": [files[0]],
        "bidFiles": [files[1]],
        "force": False,
    }
    asyncio.run(DuplicateAnalysisRunner(repo=repo, sections=sections)(
        FakeCtx(), payload, None))

    sub = repo.get_sections("p1")["metadata"]["stats"]["contentExtraction"]
    assert sub["status"] == "success"
    assert sub["completed"] == 2
    assert sub["total"] == 2


# ----------------------------------------------------------- 用例 6：file_id 兜底对齐参考

def test_file_id_fallback_matches_stable_file_id():
    """`_file_id_of` 的兜底分支对齐参考 `stableFileId`（:118-120）。

    评审 A F2：实现初版回退 `file_id or file_path` 直接用路径串当 id——路径
    分隔符会进入 content 文件名（可能逃逸 content_dir）且与参考 id 不一致。
    参考语义是 `file.id || sha1(file.file_path || file.file_name || '')`。
    """
    from app.infra.tasks.duplicate_sections import _file_id_of

    # 主路径：file_id 直取
    assert _file_id_of({"file_id": "abc"}) == "abc"
    # 兜底：对路径求 sha1（参考 :119）
    assert _file_id_of({"file_path": "/tmp/x.txt"}) == hashlib.sha1(
        b"/tmp/x.txt").hexdigest()
    # 路径缺失时退到 file_name
    assert _file_id_of({"file_name": "x.txt"}) == hashlib.sha1(
        b"x.txt").hexdigest()
    # 全空 → sha1('')
    assert _file_id_of({}) == hashlib.sha1(b"").hexdigest()
    # 对象（非 Mapping）分支同样语义
    class _Obj:
        file_id = "obj-id"
        file_path = "/tmp/y.txt"

    assert _file_id_of(_Obj()) == "obj-id"

    class _ObjNoId:
        file_id = None
        file_path = "/tmp/y.txt"
        file_name = None

    assert _file_id_of(_ObjNoId()) == hashlib.sha1(b"/tmp/y.txt").hexdigest()


# ----------------------------------------------------------- HTTP 辅助（用例 4）

DOCX_MIME = ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document")


def _post_text_files(client, project, *, tender=None, bid=None):
    """手工编码 multipart 上传 .txt 文件（复用 test_api_duplicate_files 的写法）。"""
    boundary = "YibiaoTestBoundary3141"
    parts: list[bytes] = []
    for group, items in (("tender", tender), ("bid", bid)):
        if items is None:
            continue
        for name, text in items:
            payload = text.encode("utf-8")
            parts.append(
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="{group}"; '
                f'filename="{name}"\r\n'
                f'Content-Type: text/plain\r\n\r\n'.encode()
                + payload + b'\r\n')
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    return client.post(
        f"/projects/{project}/duplicate/files", content=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})


def _wait_for_task(state, task_id, timeout=10.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = state.tasks.get(task_id)
        if task and task["status"] in ("success", "error", "cancelled", "interrupted"):
            return task
        time.sleep(0.02)
    return state.tasks.get(task_id)
