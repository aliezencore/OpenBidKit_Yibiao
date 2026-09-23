"""Task 4：content_extraction 角色接线测试。

覆盖计划 :305-310 的 5 条用例；图片相关的 3 条由 P2a↔P2b 接线（docx 带图解析）
补齐——原先 P2a 在 `include_images=True` 时抛 `not-implemented`（裁定 R2，
`docx.py:12-21`），故当时只能改用 `.txt` 真文件 + 解析桩；R2 已随接线撤销，
现在图片用例走**真 docx + 真图片**，桩已移除。

参考实现：svc.cjs:2302-2354 `runContentExtraction`。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
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


def _mk_docx_file(tmp_path, name: str, *, image_name="pic.png") -> dict:
    """造一个**带真图片**的 docx + 同一形状的入参 dict。

    图片由 `tests.parsing_fixtures.make_image`（Pillow）产，docx 由
    `build_docx_with_images` 产——P2b 的既有夹具，无需手搓。
    """
    from tests.parsing_fixtures import build_docx_with_images, make_image

    image = make_image(tmp_path / image_name, size=(40, 30), color=(200, 30, 30))
    path = build_docx_with_images(
        tmp_path / name, blocks=[("p", "正文段落。")], images=[image])
    return {
        "file_id": path.stem,
        "file_name": name,
        "file_path": str(path),
        "extension": "docx",
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

    夹具用 `.txt`：runner 逻辑与格式无关，.txt 等价覆盖「重解析 → 写 content 目录 →
    收行 → 算 hash」全链路。（图片链路另有真 docx 的用例覆盖。）**裁定史**：接线前
    P2a 的 docx 解析器在 `include_images=True` 时抛 `not-implemented`，故当年只能
    用 txt；该限制已随 P2a↔P2b 接线撤销。
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


# ------------------------------------------------- 用例 2：重解析带图片 + 资产落盘

def test_content_extraction_requests_images_and_stores_assets(tmp_path):
    """content_extraction 请求 `include_images=True`，**且**把图片资产落盘。

    与上传解析（`include_images=False`）的差异是本任务的判别点。真 docx 让这条
    断言变成**直接证据**而不是间接代理：若 runner 传了 `False`，markdown 里就
    不会有任何占位（`docx_to_markdown` 的 False 分支剥掉所有 `<img>`）。

    「落盘」的判别点必须落在**文件真的存在**上：占位只是字符串，`assets/<hash>`
    指向的文件不见得写出来了——两者是两个独立的失败面（P3b 的 P1 就是这类）。
    """
    from app.infra.parsing.asset_store import resolve_asset_path

    docx_file = _mk_docx_file(tmp_path, "投标.docx")
    runner = build_content_extraction(tmp_path)
    section_ctx = SectionRunContext(
        project_id="p1", section=CONTENT_EXTRACTION, files=[docx_file])
    output = asyncio.run(runner(section_ctx))

    row = output.rows["content_files"][0]
    assert row["status"] == "success", row
    content = Path(row["content_path"]).read_text(encoding="utf-8")
    hashes = re.findall(r"!\[[^\]]*\]\(assets/([0-9a-f]{64})\)", content)
    assert hashes, content

    # 每个占位都必须能解析到**存在的文件**（接线真的接上了）
    for digest in hashes:
        resolved = resolve_asset_path(
            data_root=tmp_path, project_id="p1", target=f"assets/{digest}")
        assert resolved is not None and resolved.exists(), digest

    # 资产目录里恰好是「不同哈希数」份（同图重复出现只落一份）
    assets_dir = tmp_path / "projects" / "p1" / "assets"
    assert len(list(assets_dir.iterdir())) == len(set(hashes))


def test_content_extraction_skips_asset_storage_for_non_docx(tmp_path):
    """非 docx 不落资产（R-DW-5）：txt 没有图片占位，落盘是纯浪费。"""
    file = _mk_file(tmp_path, "doc.txt", "纯文本正文。")
    runner = build_content_extraction(tmp_path)

    output = asyncio.run(runner(SectionRunContext(
        project_id="p1", section=CONTENT_EXTRACTION, files=[file])))

    assert output.rows["content_files"][0]["status"] == "success"
    assert not (tmp_path / "projects" / "p1" / "assets").exists()


def test_content_extraction_asset_failure_does_not_lose_the_text(
        tmp_path, monkeypatch, caplog):
    """落资产失败**不拖累正文**（T4-T7 评审 A 的 F4 裁定）。

    初版把落资产放在写 markdown **之前**且不兜异常，于是「一个坏 CRC 的、正文
    未引用的 `word/media/*` 孤儿条目」就能让整篇正文从查重里消失
    （`save_docx_assets` 会读全部媒体条目，而 mammoth 只读被引用的）。

    裁定：**正文与图片是两个独立交付物**。资产失败 → 正文照写、该行仍 `success`、
    记一条 warning；缺失的资产由消费侧（P3c）的 `resolve_asset_path → None` 逐图跳过。
    """
    import logging

    import app.infra.tasks.duplicate_sections as mod

    def boom(**_kwargs):
        raise OSError("坏 CRC 的孤儿媒体条目")

    monkeypatch.setattr(mod, "save_docx_assets", boom)

    docx_file = _mk_docx_file(tmp_path, "投标.docx")
    txt_file = _mk_file(tmp_path, "招标.txt", "纯文本正文。")
    runner = build_content_extraction(tmp_path)

    with caplog.at_level(logging.WARNING, logger="app.infra.tasks.duplicate_sections"):
        output = asyncio.run(runner(SectionRunContext(
            project_id="p1", section=CONTENT_EXTRACTION, files=[docx_file, txt_file])))

    rows = {r["file_id"]: r for r in output.rows["content_files"]}
    # 正文照常产出：行 success、`.md` 真的写出
    assert rows["投标"]["status"] == "success", rows
    assert Path(rows["投标"]["content_path"]).exists(), rows
    assert rows["招标"]["status"] == "success", rows
    assert output.failed is False
    # 失败必须**可观测**（不是静默吞掉）
    assert any("图片资产落盘失败" in record.getMessage() for record in caplog.records), \
        [record.getMessage() for record in caplog.records]


# ----------------------------------------------------------- 用例 3：坏文件不连坐整批

def test_content_extraction_failed_file_does_not_poison_batch(
        tmp_path, monkeypatch):
    """一个坏文件（monkeypatch parse_document 抛）→ 其余文件仍 success、failed=True。

    文件顺序把坏文件放中间：正常实现下 good1 success、bad error 行、good2 success；
    变异探针（删掉 catch → 抛出中断整批）下 good2 不会被处理 → 本用例必须红。
    """
    import app.infra.tasks.duplicate_sections as mod

    def boom_on(path, *, provider="local", mineru_token="", include_images=False):
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

    用 `.txt` 真文件：txt + include_images=True 在解析器里是 no-op，能跑通真实
    重解析链路（本用例验的是「上传清目录」这条编排行为，与格式无关）。
    """
    import app.infra.tasks.duplicate_sections as mod

    # 让 content_extraction 用真实解析（txt 路径 include_images=True 可跑通）
    def real_parse(path, *, provider="local", mineru_token="", include_images=False):
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


def test_upload_clears_stale_image_assets(client, project, tmp_path):
    """上传全量替换 → 上一轮的图片资产一并作废（T4-T7 评审 A 的 F5）。

    参考的 `clearDuplicateContentArtifacts`（store.cjs:958-964）在删 content 的
    同一处还调 `deleteImportedImageBatches`，即「换文件 → 旧图片资产失效」。
    不清也不会**指错图**（资产按内容哈希寻址），但会无界累积。
    """
    from app.infra.parsing.asset_store import assets_dir_for

    state = client.app.state.yibiao
    assets_dir = assets_dir_for(state.settings.data_root, project)
    assets_dir.mkdir(parents=True)
    (assets_dir / "deadbeef.png").write_bytes(b"stale")

    _post_text_files(client, project,
                     tender=[("招标2.txt", "新的招标正文。")],
                     bid=[("投标2.txt", "新的投标正文。")])

    assert not assets_dir.exists(), "重新上传后旧图片资产应被清掉"


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


# ------------------------------------------- P2c：分析期解析器必须跟随配置 provider

def test_content_extraction_follows_configured_provider(tmp_path, monkeypatch):
    """**P2c 评审 B 的 P1 回归**：content 段的重解析跟随 provider，不是硬编码 local。

    上传期走 MinerU、分析期硬编码 local 的后果：`.pptx`/图片这类只有 MinerU 能读的
    文件会「上传成功、分析整段失败」；`.pdf`/`.docx` 则会因两次用了不同解析器而
    正文漂移。这里把远端工厂换成假件，断言 content 目录里的正文**来自远端**。
    """
    calls: list[str] = []

    class FakeParser:
        def parse_file(self, path):
            calls.append(Path(path).name)
            return "# 远端解析结果\n\n正文\n"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr("app.infra.parsing.router.build_mineru_parser",
                        lambda provider, *, token="": FakeParser())

    from tests.parsing_fixtures import build_docx

    docx = build_docx(tmp_path / "bid.docx", blocks=[("p", "本地会读到的正文")])
    file = {
        "file_id": docx.stem,
        "file_name": "bid.docx",
        "file_path": str(docx),
        "extension": "docx",
        "size": docx.stat().st_size,
        "modified_at": "2026-01-01T00:00:00.000Z",
    }
    runner = build_content_extraction(tmp_path, provider="mineru-agent-api",
                                      mineru_token="tok")
    output = asyncio.run(runner(SectionRunContext(
        project_id="p1", section=CONTENT_EXTRACTION, files=[file])))

    assert output.failed is False
    written = (content_dir_for(tmp_path, "p1") / f"{docx.stem}.md").read_text(
        encoding="utf-8")
    assert "远端解析结果" in written
    assert "本地会读到的正文" not in written
    assert calls == ["bid.docx"]


def test_content_extraction_local_provider_does_not_touch_remote(tmp_path, monkeypatch):
    """默认 provider=local：远端工厂**不被调用**（默认行为与 P2c 之前一致）。"""
    def explode(provider, *, token=""):  # pragma: no cover
        raise AssertionError("local provider 不应构造远端客户端")

    monkeypatch.setattr("app.infra.parsing.router.build_mineru_parser", explode)

    file = _mk_file(tmp_path, "bid.txt", "本地正文。")
    runner = build_content_extraction(tmp_path)
    output = asyncio.run(runner(SectionRunContext(
        project_id="p1", section=CONTENT_EXTRACTION, files=[file])))
    assert output.failed is False
    written = (content_dir_for(tmp_path, "p1") / f"{file['file_id']}.md").read_text(
        encoding="utf-8")
    assert "本地正文。" in written
