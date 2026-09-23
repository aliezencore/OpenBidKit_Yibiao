// 服务端契约类型（P3a–P3d-2 已就绪端点的实际产出形状）。
// 键形依据：app/infra/repositories/duplicate.py 的 load_analysis /
// _file_from_row（键名 `id` 而非 `file_id`）/ _build_rows，以及
// app/api/duplicate.py 的 _section_response / _files_for_role。

export type AnalysisStatus = 'pending' | 'running' | 'success' | 'error';

export type DuplicateSectionKey = 'metadata' | 'outline' | 'content' | 'image';

/** GET /state 的 tenderFiles/bidFiles 行（`_file_from_row`：键名 `id`）。 */
export interface WorkspaceFile {
  id: string;
  file_name: string;
  file_path: string;
  extension: string | null;
  size: number;
  modified_at: string;
}

/** GET/POST/DELETE /duplicate/files 的响应（`_files_for_role` 行形状）。 */
export interface DuplicateFilesRow {
  file_id: string | null;
  file_name: string | null;
  file_path: string | null;
  extension: string | null;
  size: number | null;
  modified_at: string | null;
  role: 'tender' | 'bid' | null;
  error: string | null;
}

export interface DuplicateFilesResponse {
  tender: DuplicateFilesRow[];
  bid: DuplicateFilesRow[];
}

/** 段公共形状（`_section_common`）。 */
export interface SectionBase {
  status: AnalysisStatus;
  progress: number;
  message: string;
  signature: string | null;
  started_at: string | null;
  updated_at: string | null;
}

/** GET /state 的 sections.{metadata|outline|content|image}（缺段也有 pending 形状）。 */
export interface SectionResponse extends SectionBase {
  stats: Record<string, unknown>;
}

export interface ProgressCounter {
  status: AnalysisStatus;
  completed: number;
  total: number;
}

export interface ContentFileEntry {
  file_id: string;
  file_name: string;
  status: AnalysisStatus;
  error?: string | null;
  content_path?: string | null;
}

// ------------------------------------------------------------------ metadata

export interface MetadataItem {
  key: string;
  label: string;
  value: string;
  normalized: string | null;
  date_day: string | null;
  comparable: boolean;
  date_comparable: boolean;
}

export interface MetadataFileEntry {
  file_id: string;
  file_name: string;
  status: AnalysisStatus;
  metadata: MetadataItem[];
  error?: string | null;
}

export interface MetadataRow {
  key: string;
  label: string;
  values: Record<string, string>;
  duplicate_file_ids: string[];
  same_day_file_ids?: string[];
}

export interface MetadataAnalysis extends SectionBase {
  contentExtraction?: ProgressCounter;
  metadataExtraction?: ProgressCounter;
  files?: MetadataFileEntry[];
  rows?: MetadataRow[];
  contentFiles?: ContentFileEntry[];
  logs?: unknown[];
}

// ------------------------------------------------------------------- outline

export interface OutlineGroup {
  id: string;
  type: string;
  title: string | null;
  score: number;
  file_ids: string[];
  item_ids?: Record<string, string[]>;
  paths?: Record<string, string[]>;
}

export interface OutlineAnalysis extends SectionBase {
  duplicateGroups?: OutlineGroup[];
  files?: Array<{
    file_id: string;
    file_name: string;
    status: AnalysisStatus;
    source?: string | null;
    confidence?: number;
  }>;
  tenderMatchedItemCount?: number;
  pairwiseSimilarities?: Record<string, unknown>;
}

// ------------------------------------------------------------------- content

export interface DuplicateSentence {
  id: string;
  normalized: string;
  sentence: string;
  file_ids: string[];
  occurrences: Record<string, number>;
}

export interface ContentAnalysis extends SectionBase {
  duplicateSentences?: DuplicateSentence[];
  tenderSentenceCount?: number;
  tenderMatchedSentenceCount?: number;
  totalSentenceCount?: number;
}

// --------------------------------------------------------------------- image

export interface ImageLocation {
  image_index?: number;
  directory?: string | null;
  previous_sentence?: string | null;
}

export interface DuplicateImage {
  id: string;
  hash: string;
  preview_url: string;
  file_ids: string[];
  occurrences: Record<string, number>;
  locations?: Record<string, ImageLocation[]>;
}

export interface ImageAnalysis extends SectionBase {
  duplicateImages?: DuplicateImage[];
  totalImageCount?: number;
}

// -------------------------------------------------------------- state 聚合

export interface DuplicateState {
  tenderFile: WorkspaceFile | null;
  tenderFiles: WorkspaceFile[];
  bidFiles: WorkspaceFile[];
  step: 'upload' | 'analysis';
  activeAnalysisTab: DuplicateSectionKey;
  metadataAnalysis: MetadataAnalysis | null;
  outlineAnalysis: OutlineAnalysis | null;
  contentAnalysis: ContentAnalysis | null;
  imageAnalysis: ImageAnalysis | null;
  contentFiles?: ContentFileEntry[];
  sections: Record<DuplicateSectionKey, SectionResponse>;
  overallProgress: number;
  message: string;
}

// -------------------------------------------------------------------- tasks

/** 任务状态取值 = 服务端 engine 实际发布的全集（含重启恢复的 interrupted）。 */
export type TaskStatus =
  | 'queued' | 'running' | 'success' | 'error' | 'cancelled' | 'interrupted';

/** GET /projects/{pid}/tasks 的行（tasks 表全列投影）。 */
export interface TaskRow {
  task_id: string;
  project_id: string;
  domain: string;
  kind: string;
  status: TaskStatus;
  progress: number;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
}

/** SSE `data:` 事件（engine._publish 的形状）。 */
export interface TaskStatusEvent {
  type: string;
  taskId: string;
  status: string;
  error?: string | null;
}

// ----------------------------------------------------------------- projects

export interface ProjectRow {
  id: string;
  name: string;
  created_at: string;
}
