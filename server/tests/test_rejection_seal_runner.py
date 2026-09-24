"""P4c-2：主体检查的印章分支（正文+印章合并、vision stats、disabled/success 路径）。

真值来源 `client/electron/services/subjectCheckTask.cjs` :491-642 与
`subjectVisionProvider.cjs`（预检/分批/降级重试/近名二次确认/角色缓存）。
"""

from __future__ import annotations

import asyncio

from app.infra.tasks.rejection_checks import RejectionCheckRunner


def _setup(tmp_path):
    from app.infra.db.engine import Database
    from app.infra.db.migrate import apply_schema
    from app.infra.repositories.projects import ProjectRepository
    from app.infra.repositories.rejection import RejectionRepository

    db = Database(tmp_path / "rejection.sqlite")
    apply_schema(db)
    project = ProjectRepository(db).create("seal")
    repo = RejectionRepository(db)
    repo.save_document(project, {
        "document_id": "bid-a", "role": "bid", "source": "upload",
        "file_name": "甲.docx", "markdown_path": "rejection-check/bids/bid-a.md",
        "content_hash": "h", "content_chars": 10, "parser_label": None,
        "sort_order": 0, "imported_at": "t", "updated_at": "t"})
    md = repo.get_document(project, "bid", "bid-a")["markdown_path"]
    target = tmp_path / "data" / "projects" / project / md
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("投标正文：乙方建设公司承担施工。", encoding="utf-8")
    return db, project, repo


def _write_png(tmp_path, project: str, asset_path: str) -> None:
    from PIL import Image

    target = tmp_path / "data" / "projects" / project / "rejection-check" \
        / "subject-media" / "bid-a" / asset_path
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), (200, 30, 30)).save(target, format="PNG")

def _make_png_bytes() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _ctx():
    class Ctx:
        def __init__(self) -> None:
            self.logs: list[str] = []

        def progress(self, percent, label=None):
            pass

        def log(self, message):
            self.logs.append(message)

    return Ctx()


def test_runner_subject_seal_merges_findings_and_stats(tmp_path):
    """印章开启：正文 finding + 印章 finding 合并；stats 带 visionStatus/visionStats。"""
    from app.infra.repositories.rejection import RejectionRepository  # noqa: F401
    from tests.ai_fakes import ScriptedAI

    db, project, repo = _setup(tmp_path)
    _write_png(tmp_path, project, "seal.png")
    repo.save_visual_assets(project, "bid-a", [{
        "id": "asset-1", "mediaName": "seal.png",
        "contentHash": "hash-1", "relativePath": "seal.png",
        "mimeType": "image/png", "width": 4, "height": 4,
        "placement": "inline", "assetKind": "standalone",
        "locationHint": "第 2 页", "nearbyText": "投标函附近",
        "sectionTitle": "投标函",
    }])

    ai = ScriptedAI([])
    # 调用顺序：①正文审阅 patch ②正文定稿 findings ③印章内容识别 ④角色判断
    ai._responses = [
        '{"subject_summary":[],"role_summary":[],"candidates":['
        '{"bid_document_id":"bid-a","role":"bidder","detected_subject":"乙方建设公司",'
        '"original_text":"投标正文：乙方建设公司承担施工。","name_confidence":0.9,'
        '"role_confidence":0.9}]}',
        '{"findings":[{"bid_document_id":"bid-a","role":"bidder",'
        '"detected_subject":"乙方建设公司","original_text":'
        '"投标正文：乙方建设公司承担施工。","name_confidence":0.9,'
        '"role_confidence":0.9}]}',
        '{"images":[{"has_seal":true,"seal_text":"乙方建设公司公章",'
        '"image_type":"document"}]}',
        '{"positions":[{"page_role":"bidder","seal_role":"bidder",'
        '"detected_subject":"乙方建设公司","name_confidence":0.9,'
        '"role_confidence":0.9,"reason":"落款位置"}]}',
    ]

    class Ctx:
        def __init__(self) -> None:
            self.logs: list[str] = []

        def progress(self, percent, label=None):
            pass

        def log(self, message):
            self.logs.append(message)

    asyncio.run(RejectionCheckRunner(
        repo, ai, tmp_path / "data", official_name="甲公司", aliases=["甲"])(
            Ctx(), {"projectId": project, "enabledChecks": ["subject"],
                    "signatures": {"subject": "s"}, "subjectSealCheck": True,
                    "bidDocuments": [{"id": "bid-a", "fileName": "甲.docx"}]}, None))

    findings = repo.load_findings(project, "subject")
    assert len(findings) == 2                              # 正文 1 + 印章 1
    sources = {finding["source"] for finding in findings}
    assert sources == {"text", "seal"}
    seal_finding = next(f for f in findings if f["source"] == "seal")
    assert seal_finding["level"] == "confirmed-mismatch"   # bidder + 0.9/0.9
    assert seal_finding["id"] == "subject-seal-asset-1"
    result = repo.load_result(project, "subject")
    assert result["status"] == "success"
    assert result["stats"]["visionStatus"] == "success"
    assert result["stats"]["visionStats"]["sealAssets"] == 1
    db.close()


def test_runner_subject_seal_disabled_when_flag_off(tmp_path):
    from tests.ai_fakes import ScriptedAI

    db, project, repo = _setup(tmp_path)
    ai = ScriptedAI([
        '{"subject_summary":[],"role_summary":[],"candidates":[]}'])
    runner = RejectionCheckRunner(repo, ai, tmp_path / "data",
                                  official_name="甲公司")

    class Ctx:
        def progress(self, percent, label=None):
            pass

        def log(self, message):
            pass

    asyncio.run(runner(Ctx(), {
        "projectId": project, "enabledChecks": ["subject"],
        "signatures": {"subject": "s"}, "bidDocuments": [{"id": "bid-a", "fileName": "甲.docx"}],
    }, None))
    result = repo.load_result(project, "subject")
    assert result["stats"]["visionStatus"] == "disabled"
    db.close()


