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
)
