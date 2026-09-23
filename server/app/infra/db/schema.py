from __future__ import annotations

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS projects (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
      task_id TEXT PRIMARY KEY,
      project_id TEXT NOT NULL REFERENCES projects(id),
      domain TEXT NOT NULL,
      kind TEXT NOT NULL,
      status TEXT NOT NULL,
      progress INTEGER NOT NULL DEFAULT 0,
      stage_label TEXT,
      payload_json TEXT,
      checkpoint_json TEXT,
      stats_json TEXT,
      error TEXT,
      claimed_by TEXT,
      heartbeat_at TEXT,
      created_at TEXT NOT NULL,
      started_at TEXT,
      finished_at TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tasks_project_status
    ON tasks(project_id, status)
    """,
    """
    CREATE TABLE IF NOT EXISTS task_logs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      project_id TEXT NOT NULL,
      task_domain TEXT NOT NULL,
      task_type TEXT NOT NULL,
      task_id TEXT NOT NULL,
      message TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_task_logs_task
    ON task_logs(project_id, task_domain, task_type, task_id, id DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_meta (
      project_id TEXT PRIMARY KEY REFERENCES projects(id),
      step TEXT NOT NULL DEFAULT 'upload',
      active_analysis_tab TEXT NOT NULL DEFAULT 'metadata',
      current_signature TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_files (
      project_id TEXT NOT NULL REFERENCES projects(id),
      file_id TEXT NOT NULL,
      role TEXT NOT NULL,
      file_name TEXT NOT NULL,
      file_path TEXT NOT NULL,
      extension TEXT NOT NULL,
      size INTEGER NOT NULL DEFAULT 0,
      modified_at TEXT,
      sort_order INTEGER NOT NULL DEFAULT 0,
      content_hash TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (project_id, file_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_files_role_order
    ON duplicate_check_files(project_id, role, sort_order)
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_analysis_sections (
      project_id TEXT NOT NULL REFERENCES projects(id),
      section TEXT NOT NULL,
      status TEXT NOT NULL,
      progress INTEGER NOT NULL DEFAULT 0,
      message TEXT NOT NULL DEFAULT '',
      signature TEXT,
      stats_json TEXT,
      started_at TEXT,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (project_id, section)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_content_files (
      project_id TEXT NOT NULL REFERENCES projects(id),
      file_id TEXT NOT NULL,
      status TEXT NOT NULL,
      content_path TEXT,
      content_length INTEGER NOT NULL DEFAULT 0,
      parser_label TEXT,
      error TEXT,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (project_id, file_id),
      FOREIGN KEY (project_id, file_id)
        REFERENCES duplicate_check_files(project_id, file_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_content_files_status
    ON duplicate_check_content_files(project_id, status)
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_metadata_items (
      project_id TEXT NOT NULL REFERENCES projects(id),
      id INTEGER NOT NULL,
      file_id TEXT NOT NULL,
      key TEXT NOT NULL,
      label TEXT NOT NULL,
      value TEXT NOT NULL DEFAULT '',
      normalized TEXT,
      date_day TEXT,
      comparable INTEGER NOT NULL DEFAULT 0,
      date_comparable INTEGER NOT NULL DEFAULT 0,
      sort_order INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (project_id, id),
      UNIQUE (project_id, file_id, key),
      FOREIGN KEY (project_id, file_id)
        REFERENCES duplicate_check_files(project_id, file_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_metadata_file_order
    ON duplicate_check_metadata_items(project_id, file_id, sort_order)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_metadata_key
    ON duplicate_check_metadata_items(project_id, key)
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_outline_items (
      project_id TEXT NOT NULL REFERENCES projects(id),
      item_id TEXT NOT NULL,
      file_id TEXT NOT NULL,
      parent_item_id TEXT,
      level INTEGER NOT NULL,
      number TEXT,
      title TEXT NOT NULL,
      normalized_title TEXT NOT NULL,
      path_titles_json TEXT NOT NULL,
      normalized_path TEXT NOT NULL,
      source TEXT NOT NULL,
      confidence REAL NOT NULL DEFAULT 0,
      sort_order INTEGER NOT NULL DEFAULT 0,
      from_tender INTEGER NOT NULL DEFAULT 0,
      matched_tender_sentence TEXT,
      PRIMARY KEY (project_id, item_id),
      FOREIGN KEY (project_id, file_id)
        REFERENCES duplicate_check_files(project_id, file_id) ON DELETE CASCADE,
      FOREIGN KEY (project_id, parent_item_id)
        REFERENCES duplicate_check_outline_items(project_id, item_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_outline_file_order
    ON duplicate_check_outline_items(project_id, file_id, sort_order)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_outline_normalized
    ON duplicate_check_outline_items(project_id, normalized_title, normalized_path)
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_outline_groups (
      project_id TEXT NOT NULL REFERENCES projects(id),
      group_id TEXT NOT NULL,
      type TEXT NOT NULL,
      title TEXT NOT NULL,
      score REAL NOT NULL DEFAULT 0,
      file_ids_json TEXT NOT NULL,
      item_ids_json TEXT NOT NULL,
      paths_json TEXT NOT NULL,
      sort_order INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (project_id, group_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_outline_groups_order
    ON duplicate_check_outline_groups(project_id, sort_order)
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_outline_pairwise (
      project_id TEXT NOT NULL REFERENCES projects(id),
      id INTEGER NOT NULL,
      file_a_id TEXT NOT NULL,
      file_b_id TEXT NOT NULL,
      score REAL NOT NULL DEFAULT 0,
      title_overlap REAL NOT NULL DEFAULT 0,
      path_overlap REAL NOT NULL DEFAULT 0,
      order_similarity REAL NOT NULL DEFAULT 0,
      shared_count INTEGER NOT NULL DEFAULT 0,
      risk TEXT NOT NULL DEFAULT 'none',
      PRIMARY KEY (project_id, id),
      UNIQUE (project_id, file_a_id, file_b_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_outline_pairwise_score
    ON duplicate_check_outline_pairwise(project_id, score DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_content_duplicates (
      project_id TEXT NOT NULL REFERENCES projects(id),
      duplicate_id TEXT NOT NULL,
      sentence TEXT NOT NULL,
      normalized TEXT NOT NULL,
      file_ids_json TEXT NOT NULL,
      first_order INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (project_id, duplicate_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_content_duplicates_order
    ON duplicate_check_content_duplicates(project_id, first_order)
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_content_occurrences (
      project_id TEXT NOT NULL REFERENCES projects(id),
      id INTEGER NOT NULL,
      duplicate_id TEXT NOT NULL,
      file_id TEXT NOT NULL,
      occurrence_count INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (project_id, id),
      UNIQUE (project_id, duplicate_id, file_id),
      FOREIGN KEY (project_id, file_id)
        REFERENCES duplicate_check_files(project_id, file_id) ON DELETE CASCADE,
      FOREIGN KEY (project_id, duplicate_id)
        REFERENCES duplicate_check_content_duplicates(project_id, duplicate_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_content_occ_file
    ON duplicate_check_content_occurrences(project_id, file_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_image_files (
      project_id TEXT NOT NULL REFERENCES projects(id),
      file_id TEXT NOT NULL,
      status TEXT NOT NULL,
      image_count INTEGER NOT NULL DEFAULT 0,
      unique_image_count INTEGER NOT NULL DEFAULT 0,
      error TEXT,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (project_id, file_id),
      FOREIGN KEY (project_id, file_id)
        REFERENCES duplicate_check_files(project_id, file_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_duplicate_images (
      project_id TEXT NOT NULL REFERENCES projects(id),
      image_id TEXT NOT NULL,
      hash TEXT NOT NULL,
      preview_url TEXT NOT NULL,
      file_ids_json TEXT NOT NULL,
      sort_order INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (project_id, image_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_duplicate_images_hash
    ON duplicate_check_duplicate_images(project_id, hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_duplicate_images_order
    ON duplicate_check_duplicate_images(project_id, sort_order)
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_check_image_occurrences (
      project_id TEXT NOT NULL REFERENCES projects(id),
      id INTEGER NOT NULL,
      image_id TEXT NOT NULL,
      file_id TEXT NOT NULL,
      occurrence_count INTEGER NOT NULL DEFAULT 0,
      locations_json TEXT,
      PRIMARY KEY (project_id, id),
      UNIQUE (project_id, image_id, file_id),
      FOREIGN KEY (project_id, file_id)
        REFERENCES duplicate_check_files(project_id, file_id) ON DELETE CASCADE,
      FOREIGN KEY (project_id, image_id)
        REFERENCES duplicate_check_duplicate_images(project_id, image_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_duplicate_check_image_occ_file
    ON duplicate_check_image_occurrences(project_id, file_id)
    """,
    # ------------------------------------------------------------------ 废标项检查
    # 列形状逐字对齐参考 DDL（sqliteDatabase.cjs:589-772）+ spec §5.2：
    # **不改字段名、不改列序**（project_id 前置）。两处与参考的结构性差异是
    # spec §5.1 的裁定，不是自由发挥：
    #   ① 单例表的 `id INTEGER PRIMARY KEY CHECK (id = 1)` → `project_id PRIMARY KEY`；
    #   ② 各表主键由 `x_id TEXT PRIMARY KEY` → `(project_id, x_id)`。
    # `rejection_check_tasks` **不建**（任务统一收归 `tasks`，spec §5.1）——
    # 与 `duplicate_check_tasks` 同理，见 test_db_schema 的对应护栏。
    # `rejection_check_results.stats_json` 在参考里是 `addColumnIfMissing` 后补的
    # （:773），新库直接建列（迁移脚本负责把旧库该列带过来）。
    """
    CREATE TABLE IF NOT EXISTS rejection_check_meta (
      project_id TEXT PRIMARY KEY REFERENCES projects(id),
      step TEXT NOT NULL DEFAULT 'documents',
      active_document_tab TEXT NOT NULL DEFAULT 'tender',
      active_result_tab TEXT NOT NULL DEFAULT 'analysis',
      active_check_result_tab TEXT NOT NULL DEFAULT 'rejection',
      custom_check_items TEXT NOT NULL DEFAULT '',
      check_options_json TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rejection_check_documents (
      project_id TEXT NOT NULL REFERENCES projects(id),
      document_id TEXT NOT NULL,
      role TEXT NOT NULL,
      source TEXT NOT NULL,
      file_name TEXT NOT NULL,
      markdown_path TEXT NOT NULL,
      content_hash TEXT NOT NULL,
      content_chars INTEGER NOT NULL DEFAULT 0,
      parser_label TEXT,
      sort_order INTEGER NOT NULL DEFAULT 0,
      imported_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (project_id, document_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_rejection_check_documents_role_order
    ON rejection_check_documents(project_id, role, sort_order)
    """,
    """
    CREATE TABLE IF NOT EXISTS rejection_check_extraction (
      project_id TEXT PRIMARY KEY REFERENCES projects(id),
      status TEXT NOT NULL DEFAULT 'idle',
      content TEXT NOT NULL DEFAULT '',
      source TEXT,
      tender_signature TEXT,
      error TEXT,
      updated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rejection_check_results (
      project_id TEXT NOT NULL REFERENCES projects(id),
      result_type TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'idle',
      input_signature TEXT,
      active_finding_id TEXT,
      progress_message TEXT,
      error TEXT,
      stats_json TEXT,
      updated_at TEXT,
      PRIMARY KEY (project_id, result_type)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rejection_check_risk_findings (
      project_id TEXT NOT NULL REFERENCES projects(id),
      finding_id TEXT NOT NULL,
      bid_document_id TEXT,
      type TEXT NOT NULL,
      severity TEXT NOT NULL,
      title TEXT NOT NULL,
      summary TEXT NOT NULL,
      requirement TEXT NOT NULL,
      bid_evidence TEXT NOT NULL,
      risk_reason TEXT NOT NULL,
      suggestion TEXT NOT NULL,
      sort_order INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (project_id, finding_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_rejection_check_risk_order
    ON rejection_check_risk_findings(project_id, sort_order)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_rejection_check_risk_severity
    ON rejection_check_risk_findings(project_id, severity)
    """,
    """
    CREATE TABLE IF NOT EXISTS rejection_check_typo_findings (
      project_id TEXT NOT NULL REFERENCES projects(id),
      finding_id TEXT NOT NULL,
      bid_document_id TEXT,
      wrong_text TEXT NOT NULL,
      correct_text TEXT NOT NULL,
      original_excerpt TEXT NOT NULL,
      reason TEXT NOT NULL,
      location_hint TEXT,
      sort_order INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (project_id, finding_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_rejection_check_typo_order
    ON rejection_check_typo_findings(project_id, sort_order)
    """,
    """
    CREATE TABLE IF NOT EXISTS rejection_check_logic_findings (
      project_id TEXT NOT NULL REFERENCES projects(id),
      finding_id TEXT NOT NULL,
      bid_document_id TEXT,
      title TEXT NOT NULL,
      original_text TEXT NOT NULL,
      location_hint TEXT NOT NULL,
      fallacy_reason TEXT NOT NULL,
      suggestion TEXT NOT NULL,
      sort_order INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (project_id, finding_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_rejection_check_logic_order
    ON rejection_check_logic_findings(project_id, sort_order)
    """,
    """
    CREATE TABLE IF NOT EXISTS rejection_check_subject_profile (
      project_id TEXT PRIMARY KEY REFERENCES projects(id),
      status TEXT NOT NULL DEFAULT 'idle',
      official_name TEXT NOT NULL DEFAULT '',
      aliases_json TEXT,
      candidates_json TEXT,
      bid_signature TEXT,
      confirmed_at TEXT,
      error TEXT,
      updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rejection_check_visual_assets (
      project_id TEXT NOT NULL REFERENCES projects(id),
      asset_id TEXT NOT NULL,
      bid_document_id TEXT NOT NULL,
      media_name TEXT NOT NULL,
      content_hash TEXT NOT NULL,
      relative_path TEXT NOT NULL,
      mime_type TEXT NOT NULL,
      width INTEGER NOT NULL DEFAULT 0,
      height INTEGER NOT NULL DEFAULT 0,
      placement TEXT NOT NULL DEFAULT 'unknown',
      asset_kind TEXT NOT NULL DEFAULT 'unknown',
      location_hint TEXT NOT NULL DEFAULT '',
      nearby_text TEXT NOT NULL DEFAULT '',
      section_title TEXT,
      sort_order INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      PRIMARY KEY (project_id, asset_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_rejection_check_visual_assets_document
    ON rejection_check_visual_assets(project_id, bid_document_id, sort_order)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_rejection_check_visual_assets_hash
    ON rejection_check_visual_assets(project_id, content_hash)
    """,
    """
    CREATE TABLE IF NOT EXISTS rejection_check_subject_findings (
      project_id TEXT NOT NULL REFERENCES projects(id),
      finding_id TEXT NOT NULL,
      bid_document_id TEXT,
      level TEXT NOT NULL,
      source TEXT NOT NULL,
      expected_subject TEXT NOT NULL,
      detected_subject TEXT NOT NULL,
      location_kind TEXT NOT NULL,
      title TEXT NOT NULL,
      summary TEXT NOT NULL,
      location_hint TEXT NOT NULL,
      original_text TEXT NOT NULL,
      role_reason TEXT NOT NULL,
      mismatch_reason TEXT NOT NULL,
      suggestion TEXT NOT NULL,
      confidence REAL NOT NULL DEFAULT 0,
      visual_asset_id TEXT,
      asset_relative_path TEXT,
      seal_crop_relative_path TEXT,
      sort_order INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (project_id, finding_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_rejection_check_subject_findings_order
    ON rejection_check_subject_findings(project_id, sort_order)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_rejection_check_subject_findings_level
    ON rejection_check_subject_findings(project_id, level)
    """,
)
