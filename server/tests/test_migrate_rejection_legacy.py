"""P4e：legacy `yibiao.sqlite` 废标项数据迁移导入。

R-P4-10：脚本只读旧库（URI mode=ro）、只写新库与数据目录；不修改旧库。
真值来源 `client/electron/services/sqliteDatabase.cjs` 的 rejection 表结构（:589-772）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.config import Settings
from app.infra.db.engine import Database
from app.infra.db.migrate import apply_schema
from app.infra.repositories.rejection import RejectionRepository
from scripts.migrate_rejection_legacy import migrate

LEGACY_SCHEMA = """
CREATE TABLE rejection_check_meta (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  step TEXT NOT NULL DEFAULT 'documents',
  active_document_tab TEXT NOT NULL DEFAULT 'tender',
  active_result_tab TEXT NOT NULL DEFAULT 'analysis',
  active_check_result_tab TEXT NOT NULL DEFAULT 'rejection',
  custom_check_items TEXT NOT NULL DEFAULT '',
  check_options_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE rejection_check_documents (
  document_id TEXT PRIMARY KEY, role TEXT NOT NULL, source TEXT NOT NULL,
  file_name TEXT NOT NULL, markdown_path TEXT NOT NULL, content_hash TEXT NOT NULL,
  content_chars INTEGER NOT NULL DEFAULT 0, parser_label TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0, imported_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE rejection_check_extraction (
  id INTEGER PRIMARY KEY CHECK (id = 1), status TEXT NOT NULL DEFAULT 'idle',
  content TEXT NOT NULL DEFAULT '', source TEXT, tender_signature TEXT,
  error TEXT, updated_at TEXT
);
CREATE TABLE rejection_check_results (
  result_type TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'idle',
  input_signature TEXT, active_finding_id TEXT, progress_message TEXT,
  error TEXT, updated_at TEXT
);
CREATE TABLE rejection_check_risk_findings (
  finding_id TEXT PRIMARY KEY, bid_document_id TEXT, type TEXT NOT NULL,
  severity TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
  requirement TEXT NOT NULL, bid_evidence TEXT NOT NULL, risk_reason TEXT NOT NULL,
  suggestion TEXT NOT NULL, sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE rejection_check_typo_findings (
  finding_id TEXT PRIMARY KEY, bid_document_id TEXT, wrong_text TEXT NOT NULL,
  correct_text TEXT NOT NULL, original_excerpt TEXT NOT NULL, reason TEXT NOT NULL,
  location_hint TEXT, sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE rejection_check_logic_findings (
  finding_id TEXT PRIMARY KEY, bid_document_id TEXT, title TEXT NOT NULL,
  original_text TEXT NOT NULL, location_hint TEXT NOT NULL,
  fallacy_reason TEXT NOT NULL, suggestion TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE rejection_check_subject_profile (
  id INTEGER PRIMARY KEY CHECK (id = 1), status TEXT NOT NULL DEFAULT 'idle',
  official_name TEXT NOT NULL DEFAULT '', aliases_json TEXT,
  candidates_json TEXT, bid_signature TEXT, confirmed_at TEXT, error TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE rejection_check_visual_assets (
  asset_id TEXT PRIMARY KEY, bid_document_id TEXT NOT NULL,
  media_name TEXT NOT NULL, content_hash TEXT NOT NULL,
  relative_path TEXT NOT NULL, mime_type TEXT NOT NULL,
  width INTEGER NOT NULL DEFAULT 0, height INTEGER NOT NULL DEFAULT 0,
  placement TEXT NOT NULL DEFAULT 'unknown', asset_kind TEXT NOT NULL DEFAULT 'unknown',
  location_hint TEXT NOT NULL DEFAULT '', nearby_text TEXT NOT NULL DEFAULT '',
  section_title TEXT, sort_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE rejection_check_subject_findings (
  finding_id TEXT PRIMARY KEY, bid_document_id TEXT, level TEXT NOT NULL,
  source TEXT NOT NULL, expected_subject TEXT NOT NULL, detected_subject TEXT NOT NULL,
  location_kind TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
  location_hint TEXT NOT NULL, original_text TEXT NOT NULL,
  role_reason TEXT NOT NULL, mismatch_reason TEXT NOT NULL,
  suggestion TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0,
  visual_asset_id TEXT, asset_relative_path TEXT, seal_crop_relative_path TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


@pytest.fixture
def legacy(tmp_path: Path):
    """旧库 + 旧 workspace 文件（markdown ×2 + 印章图片 ×1），内容哈希用于只读断言。"""
    legacy_db = tmp_path / "yibiao.sqlite"
    connection = sqlite3.connect(legacy_db)
    connection.executescript(LEGACY_SCHEMA)
    connection.execute(
        "INSERT INTO rejection_check_documents VALUES"
        " ('bid-a', 'bid', 'upload', '甲.docx',"
        "  'rejection-check/bids/bid-a.md', 'hash-a', 100, 'local', 0, 't1', 't1')")
    connection.execute(
        "INSERT INTO rejection_check_documents VALUES"
        " ('tender', 'tender', 'upload', '招标.docx',"
        "  'rejection-check/tender.md', 'hash-t', 80, 'local', 0, 't1', 't1')")
    connection.execute(
        "INSERT INTO rejection_check_extraction VALUES"
        " (1, 'success', '解析出的检查项', 'ai', 'tender-sig', NULL, 't2')")
    connection.execute(
        "INSERT INTO rejection_check_results VALUES"
        " ('typo', 'success', 'bid-sig', NULL, '错别字检查发现 1 项', NULL, 't3')")
    connection.execute(
        "INSERT INTO rejection_check_risk_findings VALUES"
        " ('risk-1', 'bid-a', 'invalidBid', 'high', '资质不符', '摘要', '要求',"
        "  '证据', '原因', '建议', 0, 't3', 't3')")
    connection.execute(
        "INSERT INTO rejection_check_typo_findings VALUES"
        " ('typo-1', 'bid-a', '保证金', '保证金', '投标保证金条款', '疑似错别字',"
        "  '原文第 3 行', 0, 't3', 't3')")
    connection.execute(
        "INSERT INTO rejection_check_logic_findings VALUES"
        " ('logic-1', 'bid-a', '工期不一致', '60 天', '第 1 段', '与 90 天矛盾',"
        "  '统一', 0, 't3', 't3')")
    connection.execute(
        "INSERT INTO rejection_check_subject_findings VALUES"
        " ('subject-seal-asset-1', 'bid-a', 'confirmed-mismatch', 'seal',"
        "  '甲公司', '乙方建设公司', 'standalone-image', '主体不一致', '摘要',"
        "  '第 2 页', '投标正文', '角色依据', '不一致原因', '建议', 0.9,"
        "  'asset-1', 'hash1/hash1.png', NULL, 0, 't3', 't3')")
    connection.execute(
        "INSERT INTO rejection_check_subject_profile VALUES"
        " (1, 'confirmed', '甲公司', '[\"甲\"]', '[]', 'bid-sig', 't4', NULL, 't4')")
    connection.execute(
        "INSERT INTO rejection_check_visual_assets VALUES"
        " ('asset-1', 'bid-a', 'media/image1.png', 'hash-1', 'hash1/hash1.png',"
        "  'image/png', 300, 200, 'inline', 'standalone', '第 2 页', '投标函附近',"
        "  '投标函', 0, 't5')")
    connection.execute(
        "INSERT INTO rejection_check_meta VALUES"
        " (1, 'check', 'bid-a', 'analysis', 'rejection', '自定义检查项',"
        "  '{\"typoCheck\": true}', 't6', 't6')")
    connection.commit()
    connection.close()

    workspace = tmp_path / "legacy-workspace"
    (workspace / "rejection-check" / "bids").mkdir(parents=True)
    (workspace / "rejection-check" / "subject-media" / "bid-a" / "hash1").mkdir(parents=True)
    (workspace / "rejection-check" / "bids" / "bid-a.md").write_text("甲正文", encoding="utf-8")
    (workspace / "rejection-check" / "tender.md").write_text("招标正文", encoding="utf-8")
    (workspace / "rejection-check" / "subject-media" / "bid-a" / "hash1"
     / "hash1.png").write_bytes(b"fake-png")
    return {"db": legacy_db, "workspace": workspace,
            "db_bytes": legacy_db.read_bytes()}


def test_migrate_imports_all_domain_data(tmp_path: Path, legacy):
    data_root = tmp_path / "data"
    summary = migrate(legacy["db"], legacy["workspace"], data_root,
                      project_name="迁移的废标项检查")

    settings = Settings(_env_file=None, data_root=data_root)
    database = Database(settings.db_path)
    apply_schema(database)
    repo = RejectionRepository(database)
    project_id = summary["projectId"]

    # 文档 + markdown 复制到新布局
    assert summary["documents"] == 2
    assert len(repo.list_documents(project_id, role="bid")) == 1
    assert (data_root / "projects" / project_id / "rejection-check" / "bids"
            / "bid-a.md").read_text(encoding="utf-8") == "甲正文"

    # 提取与四项结果
    assert summary["extracted"] is True
    assert repo.load_extraction(project_id)["content"] == "解析出的检查项"
    assert repo.load_result(project_id, "typo")["status"] == "success"
    assert repo.load_result(project_id, "typo")["inputSignature"] == "bid-sig"

    # findings 四表（snake → camel，finding_id 保留）
    assert len(repo.load_findings(project_id, "rejection")) == 1
    typo_findings = repo.load_findings(project_id, "typo")
    assert typo_findings[0]["wrongText"] == "保证金"
    assert len(repo.load_findings(project_id, "logic")) == 1
    subject_findings = repo.load_findings(project_id, "subject")
    assert subject_findings[0]["level"] == "confirmed-mismatch"
    assert subject_findings[0]["source"] == "seal"
    assert summary["findings"] == {"rejection": 1, "typo": 1, "logic": 1,
                                   "subject": 1}

    # 主体档案 + 视觉资产（含图片文件复制）
    profile = repo.load_subject_profile(project_id)
    assert profile["status"] == "confirmed"
    assert profile["officialName"] == "甲公司"
    assert profile["aliases"] == ["甲"]
    assets = repo.list_visual_assets(project_id, "bid-a")
    assert len(assets) == 1
    assert assets[0]["relativePath"] == "hash1/hash1.png"
    media_file = (data_root / "projects" / project_id / "rejection-check"
                  / "subject-media" / "bid-a" / "hash1" / "hash1.png")
    assert media_file.read_bytes() == b"fake-png"

    # meta（步进/页签/自定义检查项）
    state_meta = repo.ensure_meta(project_id)
    assert state_meta["step"] == "check"
    assert state_meta["custom_check_items"] == "自定义检查项"

    # R-P4-10：旧库未被修改
    assert legacy["db"].read_bytes() == legacy["db_bytes"]
    database.close()


def test_migrate_rejects_missing_target_project(tmp_path: Path, legacy):
    with pytest.raises(SystemExit, match="目标项目不存在"):
        migrate(legacy["db"], legacy["workspace"], tmp_path / "data",
                project_id="nonexistent")
