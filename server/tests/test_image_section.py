"""P3c Task 3 —— `image` 段 runner（`build_image_runner`）的行为测试。

参考实现：svc.cjs:2632-2721 `runImageDuplicateAnalysis`。文案/进度公式/失败语义逐字
对齐参考；图片身份取 target 哈希（计划裁定 R-P3c-1），缺资产跳过（R-P3c-2）。

接线级覆盖（经编排器 + `/state`）在 Task 4；本文件直驱 runner，把每条分支的形状钉细。
"""

from __future__ import annotations

import asyncio

import pytest

from app.infra.db.engine import Database
from app.infra.db.migrate import apply_schema
from app.infra.parsing.asset_store import assets_dir_for
from app.infra.repositories.duplicate import DuplicateRepository
from app.infra.tasks.duplicate_runner import SectionOutput, SectionRunContext
from app.infra.tasks.duplicate_sections import build_image_runner, content_dir_for

H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64


@pytest.fixture()
def tmp_root(tmp_path):
    return tmp_path


@pytest.fixture()
def repo(tmp_root):
    db = Database(tmp_root / "t.sqlite")
    apply_schema(db)
    db.execute("INSERT INTO projects(id, name, created_at) VALUES ('p1','a','now')")
    return DuplicateRepository(db)


def _file(file_id: str) -> dict:
    return {"file_id": file_id, "file_name": f"{file_id}.docx",
            "file_path": f"/{file_id}.docx"}


def _write_content(tmp_root, spec) -> SectionOutput:
    """写 content markdown 落盘并返回 content_extraction 风格的 `SectionOutput`。"""
    content_dir = content_dir_for(tmp_root, "p1")
    content_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for file_id, file_name, markdown in spec:
        path = content_dir / f"{file_id}.md"
        path.write_bytes(markdown.encode("utf-8"))
        results.append({"file_id": file_id, "file_name": file_name,
                        "status": "success", "content_path": str(path)})
    return SectionOutput(rows={"content_files": results})


def _write_asset(tmp_root, digest: str, *, ext=".png", data=b"png-bytes") -> Path:
    assets = assets_dir_for(tmp_root, "p1")
    assets.mkdir(parents=True, exist_ok=True)
    (assets / f"{digest}{ext}").write_bytes(data)
    return assets / f"{digest}{ext}"


def _drive(tmp_root, *, content: SectionOutput, bids, progress=None):
    runner = build_image_runner(tmp_root)
    section_ctx = SectionRunContext(
        project_id="p1", section="image", files=list(bids), bid_files=list(bids),
        content_files=content,
        section_progress=progress or (lambda percent, message: None))
    return asyncio.run(runner(section_ctx))


def test_cross_file_duplicate_image_is_persisted_with_relative_preview(tmp_root):
    """跨文件同图 → 进 `duplicate_images`；`preview_url` 是**相对端点**（R-P3c-3）。"""
    _write_asset(tmp_root, H1)
    _write_asset(tmp_root, H2)
    content = _write_content(tmp_root, [
        ("b1", "b1.docx", f"# 甲\n\n第一句。\n\n![](assets/{H1})\n"),
        ("b2", "b2.docx",
         f"# 乙\n\n第二句。\n\n![](assets/{H1})\n![](assets/{H2})\n"),
    ])
    calls: list[tuple[int, str]] = []
    output = _drive(tmp_root, content=content,
                    bids=[_file("b1"), _file("b2")],
                    progress=lambda percent, message: calls.append((percent, message)))

    assert output.failed is False
    assert output.message == "图片比对完成"
    assert output.extraction == {"status": "success", "completed": 2, "total": 2}
    assert output.stats == {"totalImageCount": 3}

    image_files = output.rows["image_files"]
    assert [(r["file_id"], r["status"], r["image_count"], r["unique_image_count"])
            for r in image_files] == [("b1", "success", 1, 1),
                                      ("b2", "success", 2, 2)]

    duplicates = output.rows["duplicate_images"]
    assert [d["hash"] for d in duplicates] == [H1]          # H2 只在一份文件里 → 不算重复
    assert duplicates[0]["image_id"] == "I000001"
    assert duplicates[0]["file_ids"] == ["b1", "b2"]
    assert duplicates[0]["preview_url"] == f"/projects/p1/duplicate/assets/{H1}"

    occurrences = output.rows["image_occurrences"]
    assert [(o["image_id"], o["file_id"], o["occurrence_count"])
            for o in occurrences] == [("I000001", "b1", 1), ("I000001", "b2", 1)]
    assert occurrences[0]["locations"] == [
        {"image_index": 1, "directory": "甲", "previous_sentence": "第一句"}]

    # 进度：初始广播 5 → 每文件尾 half_up(len(results)/n*85) → 1/2*85=42.5 → 43
    assert calls == [(5, "正在准备图片比对"), (43, "图片比对 1/2"),
                     (85, "图片比对 2/2")]


def test_missing_asset_is_skipped_but_still_counted_as_occurrence(tmp_root):
    """缺资产 → **跳过该图**（R-P3c-2），但 `image_count` 仍含它（参考同口径）。

    这两条口径刻意分开：`image_count` 是 markdown 里的**出现次数**，
    `unique_image_count` 是**能解析出资产的**不同图片数。
    """
    _write_asset(tmp_root, H1)
    content = _write_content(tmp_root, [
        ("b1", "b1.docx", f"![](assets/{H1})\n![](assets/{H3})\n")])
    output = _drive(tmp_root, content=content, bids=[_file("b1")])

    row = output.rows["image_files"][0]
    assert (row["status"], row["image_count"], row["unique_image_count"]) == (
        "success", 2, 1)
    assert output.rows["duplicate_images"] == []            # 只在一份文件里 → 不算重复


def test_same_image_twice_in_one_file_counts_occurrences_not_duplicates(tmp_root):
    _write_asset(tmp_root, H1)
    content = _write_content(tmp_root, [
        ("b1", "b1.docx", f"![](assets/{H1})\n\n![](assets/{H1})\n")])
    output = _drive(tmp_root, content=content, bids=[_file("b1")])

    row = output.rows["image_files"][0]
    assert (row["image_count"], row["unique_image_count"]) == (2, 1)
    assert output.rows["duplicate_images"] == []


def test_unreadable_content_markdown_produces_error_row(tmp_root):
    """读不到正文 → error 行（**逐字**文案，R-P3c-8）；其余文件不受牵连（R-P3c-5）。"""
    content = _write_content(tmp_root, [("b2", "b2.docx", f"![](assets/{H1})\n")])
    _write_asset(tmp_root, H1)

    output = _drive(tmp_root, content=content, bids=[_file("b1"), _file("b2")])

    rows = {r["file_id"]: r for r in output.rows["image_files"]}
    assert rows["b1"]["status"] == "error"
    assert rows["b1"]["error"] == "正文内容尚未成功提取，无法进行目录分析"
    assert (rows["b1"]["image_count"], rows["b1"]["unique_image_count"]) == (0, 0)
    assert rows["b2"]["status"] == "success"

    assert output.failed is True                            # 与 content 段恒 False 不同
    assert output.message == "部分文件图片比对失败"
    assert output.extraction == {"status": "error", "completed": 2, "total": 2}
    assert output.stats == {"totalImageCount": 1}


def test_no_bid_files_yields_empty_output_and_only_the_initial_broadcast(tmp_root):
    calls: list[tuple[int, str]] = []
    output = _drive(tmp_root, content=_write_content(tmp_root, []), bids=[],
                    progress=lambda percent, message: calls.append((percent, message)))

    assert output.rows == {"image_files": [], "duplicate_images": [],
                           "image_occurrences": []}
    assert output.extraction == {"status": "success", "completed": 0, "total": 0}
    assert output.stats == {"totalImageCount": 0}
    assert output.failed is False
    assert calls == [(5, "正在准备图片比对")]
