"""P4e：`yibiao.sqlite` 废标项检查数据迁移导入（一次性脚本）。

只读旧库（R-P4-10：URI `mode=ro` 打开，绝不写旧库——它是用户唯一的历史数据源），
把 `rejection_check_*` 各表落成服务端默认 project，并把 markdown 与图片资产复制进
新数据目录：

- 文档：legacy `markdown_path`（相对旧 workspace 或绝对路径）→ 新
  `data_root/projects/<pid>/<relative>`（保留原相对路径；拒绝逃逸项目目录的路径）；
- 视觉资产：legacy `subject-media/<bidDocumentId>/<relativePath>` → 新
  `subject-media/<bidDocumentId>/<relativePath>`；
- findings 四表：legacy snake_case 列 → camelCase findings 输入（复用
  ``findings_values`` 的归一化与 id 保留语义）；
- `rejection_check_tasks` **不迁移**（新库任务统一走 `tasks` 表，R-P4a-T1）。

用法：
    python scripts/migrate_rejection_legacy.py \\
        --legacy-db <workspace>/yibiao.sqlite \\
        --legacy-workspace <workspace> \\
        --data-root <server data root> \\
        [--project-id <id>] [--project-name 名称]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

# 让脚本可从仓库任意位置运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.infra.db.engine import Database  # noqa: E402
from app.infra.db.migrate import apply_schema  # noqa: E402
from app.infra.repositories.projects import ProjectRepository  # noqa: E402
from app.infra.repositories.rejection import RejectionRepository  # noqa: E402


def _open_legacy_readonly(legacy_db: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{legacy_db.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _resolve_legacy_file(legacy_workspace: Path, relative: str) -> Path | None:
    """legacy 相对路径解析：先按 workspace 根、再按绝对路径原样；拒绝逃逸。"""
    candidate = (legacy_workspace / relative).resolve()
    if candidate.is_file():
        return candidate
    absolute = Path(relative)
    if absolute.is_file():
        return absolute
    return None


def _copy_into(project_dir: Path, relative: str, source: Path) -> Path | None:
    """把 source 复制为 project_dir/relative（拒绝 `..` 逃逸；父目录自建）。"""
    normalized = Path(*[part for part in Path(relative).parts if part not in ("..",)])
    if not normalized.parts:
        return None
    target = project_dir / normalized
    try:
        target.resolve().relative_to(project_dir.resolve())
    except ValueError:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(source, target)
    return target


def _findings_from_legacy(rows: Sequence[Mapping[str, Any]],
                          key_map: Mapping[str, str]) -> list[dict[str, Any]]:
    """legacy snake 行 → camelCase findings 输入（保留 finding_id 为 id）。"""
    converted: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {"id": row["finding_id"]}
        for legacy_key, camel_key in key_map.items():
            if legacy_key in row.keys() and row[legacy_key] is not None:
                item[camel_key] = row[legacy_key]
        converted.append(item)
    return converted


def migrate(legacy_db: Path, legacy_workspace: Path, data_root: Path,
            project_name: str = "迁移的废标项检查",
            project_id: str | None = None) -> dict[str, Any]:
    """执行迁移，返回摘要（供脚本打印与测试断言）。"""
    legacy = _open_legacy_readonly(legacy_db)

    settings = Settings(_env_file=None, data_root=data_root)
    new_db_path = settings.db_path
    new_db_path.parent.mkdir(parents=True, exist_ok=True)
    database = Database(new_db_path)
    apply_schema(database)
    projects = ProjectRepository(database)
    if project_id:
        project_row = projects.get(project_id)
        if project_row is None:
            raise SystemExit(f"目标项目不存在：{project_id}")
        project_id = project_row["id"]
    else:
        project_id = projects.create(project_name)
    repo = RejectionRepository(database)
    project_dir = Path(data_root) / "projects" / project_id

    summary: dict[str, Any] = {"projectId": project_id, "documents": 0,
                               "mediaFiles": 0, "assetRows": 0,
                               "findings": {}, "results": 0, "extracted": False,
                               "meta": False, "profile": False}

    def _rows(table: str) -> list[sqlite3.Row]:
        """读 legacy 表；表不存在（旧版本库）时容错跳过并告警（评审 B P2）。"""
        try:
            return legacy.execute(f"SELECT * FROM {table}").fetchall()
        except sqlite3.OperationalError as error:
            print(f"警告：legacy 表 {table} 不可读，已跳过（{error}）", file=sys.stderr)
            return []

    # ① 文档（含 markdown 复制；非 docx/markdown 缺文件时保留行、跳过文件）
    document_rows = _rows("rejection_check_documents")
    for row in document_rows:
        values = {key: row[key] for key in row.keys()}
        repo.save_document(project_id, values)
        summary["documents"] += 1
        source = _resolve_legacy_file(legacy_workspace, row["markdown_path"])
        if source:
            _copy_into(project_dir, row["markdown_path"], source)

    # ② 提取结果
    for row in _rows("rejection_check_extraction"):
        repo.save_extraction(project_id, {
            "status": row["status"], "content": row["content"],
            "source": row["source"], "tender_signature": row["tender_signature"],
            "error": row["error"], "updated_at": row["updated_at"],
        })
        summary["extracted"] = True

    # ③ 四项结果（patch 语义，camelCase）
    result_rows = _rows("rejection_check_results")
    key_map = {"input_signature": "inputSignature",
               "active_finding_id": "activeFindingId",
               "progress_message": "progressMessage",
               "error": "error", "updated_at": "updatedAt"}
    for row in result_rows:
        result_type = row["result_type"]
        patch: dict[str, Any] = {"status": row["status"]}
        for legacy_key, camel_key in key_map.items():
            if legacy_key in row.keys() and row[legacy_key] is not None:
                patch[camel_key] = row[legacy_key]
        repo.save_result(project_id, result_type, patch)
        summary["results"] += 1

    # ④ findings 四表（snake → camel，finding_id 保留）
    findings_maps: dict[str, tuple[str, Mapping[str, str]]] = {
        "rejection": ("rejection_check_risk_findings", {
            "type": "type", "severity": "severity", "title": "title",
            "summary": "summary", "requirement": "requirement",
            "bid_evidence": "bidEvidence", "risk_reason": "riskReason",
            "suggestion": "suggestion"}),
        "typo": ("rejection_check_typo_findings", {
            "wrong_text": "wrongText", "correct_text": "correctText",
            "original_excerpt": "originalExcerpt", "reason": "reason",
            "location_hint": "locationHint"}),
        "logic": ("rejection_check_logic_findings", {
            "title": "title", "original_text": "originalText",
            "location_hint": "locationHint", "fallacy_reason": "fallacyReason",
            "suggestion": "suggestion"}),
        "subject": ("rejection_check_subject_findings", {
            "level": "level", "source": "source",
            "expected_subject": "expectedSubject",
            "detected_subject": "detectedSubject",
            "location_kind": "locationKind", "title": "title",
            "summary": "summary", "location_hint": "locationHint",
            "original_text": "originalText", "role_reason": "roleReason",
            "mismatch_reason": "mismatchReason", "suggestion": "suggestion",
            "confidence": "confidence", "visual_asset_id": "visualAssetId",
            "asset_relative_path": "assetRelativePath"}),
    }
    for result_type, (table, key_map) in findings_maps.items():
        legacy_rows = _rows(table)
        if not legacy_rows:
            summary["findings"][result_type] = 0
            continue
        converted = _findings_from_legacy(legacy_rows, key_map)
        repo.clear_findings(project_id, result_type)
        saved = repo.save_findings(project_id, result_type, converted)
        summary["findings"][result_type] = saved

    # ⑤ 主体档案（snake → camel；JSON 列解析）
    for row in _rows("rejection_check_subject_profile"):
        def _json(value: Any) -> Any:
            try:
                return json.loads(value) if value else []
            except (TypeError, ValueError):
                return []
        repo.save_subject_profile(project_id, {
            "status": row["status"],
            "officialName": row["official_name"],
            "aliases": _json(row["aliases_json"]),
            "candidates": _json(row["candidates_json"]),
            "bidSignature": row["bid_signature"],
            "confirmedAt": row["confirmed_at"],
            "error": row["error"],
            "updatedAt": row["updated_at"],
        })
        summary["profile"] = True

    # ⑥ 视觉资产行 + 图片文件（subject-media/<bidDocumentId>/<relativePath>）
    media_dir = project_dir / "rejection-check" / "subject-media"
    legacy_media_root = legacy_workspace / "rejection-check" / "subject-media"
    # save_visual_assets 是整批替换语义：按文档聚合后一次写入
    assets_by_document: dict[str, list[dict[str, Any]]] = {}
    for row in _rows("rejection_check_visual_assets"):
        document_id = row["bid_document_id"]
        asset = {
            "id": row["asset_id"], "mediaName": row["media_name"],
            "contentHash": row["content_hash"],
            "relativePath": row["relative_path"], "mimeType": row["mime_type"],
            "width": row["width"], "height": row["height"],
            "placement": row["placement"], "assetKind": row["asset_kind"],
            "locationHint": row["location_hint"],
            "nearbyText": row["nearby_text"], "sortOrder": row["sort_order"],
        }
        if row["section_title"]:
            asset["sectionTitle"] = row["section_title"]
        assets_by_document.setdefault(document_id, []).append(asset)
        source = _resolve_legacy_file(
            legacy_media_root / document_id, row["relative_path"])
        if source:
            target = _copy_into(media_dir / document_id, row["relative_path"], source)
            if target:
                summary["mediaFiles"] += 1
    for document_id, assets in assets_by_document.items():
        repo.save_visual_assets(project_id, document_id, assets)
        summary["assetRows"] += len(assets)

    # ⑦ meta（步进/页签/自定义检查项/检查开关）
    for row in _rows("rejection_check_meta"):
        repo.update_meta(project_id, {
            "step": row["step"],
            "active_document_tab": row["active_document_tab"],
            "active_result_tab": row["active_result_tab"],
            "active_check_result_tab": row["active_check_result_tab"],
            "custom_check_items": row["custom_check_items"],
            "check_options_json": row["check_options_json"] or "{}",
        })
        summary["meta"] = True

    legacy.close()
    database.close()
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-db", required=True, type=Path,
                        help="旧 yibiao.sqlite 路径（只读）")
    parser.add_argument("--legacy-workspace", required=True, type=Path,
                        help="旧 workspace 目录（rejection-check/ 所在处）")
    parser.add_argument("--data-root", required=True, type=Path,
                        help="服务端数据根目录")
    parser.add_argument("--project-name", default="迁移的废标项检查")
    parser.add_argument("--project-id", default=None,
                        help="导入到已有项目（默认新建）")
    args = parser.parse_args(argv)

    summary = migrate(args.legacy_db, args.legacy_workspace, args.data_root,
                      project_name=args.project_name, project_id=args.project_id)
    print("迁移完成：", json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
