// REST + SSE 数据获取层（spec §10：不引状态管理库，fetch 直用）。
import type {
  DuplicateFilesResponse,
  DuplicateState,
  ProjectRow,
  RejectionRole,
  RejectionState,
  TaskRow,
  TaskStatusEvent,
} from './types';

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
    if (Array.isArray(body.detail)) return '请求参数不合法';
  } catch {
    // 非 JSON 响应体，走状态行兜底
  }
  return `请求失败（HTTP ${response.status}）`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) throw new Error(await errorMessage(response));
  return (await response.json()) as T;
}

// ----------------------------------------------------------------- projects

export function listProjects(): Promise<ProjectRow[]> {
  return request<ProjectRow[]>('/projects');
}

export function createProject(name: string): Promise<ProjectRow> {
  return request<ProjectRow>('/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
}

// ---------------------------------------------------------------- duplicate

export function getDuplicateFiles(projectId: string): Promise<DuplicateFilesResponse> {
  return request<DuplicateFilesResponse>(
    `/projects/${encodeURIComponent(projectId)}/duplicate/files`);
}

/**
 * R-P3d3-1：两组字段恒出现——组内每文件一个真实 part，空组补
 * `filename=""` 占位 part（服务端 N-4 形状，即浏览器空 input 的原生形状）。
 * POST /files 是两组全量替换；漏掉某组字段会把该组清空。
 */
export function buildDuplicateFilesForm(tender: File[], bid: File[]): FormData {
  const form = new FormData();
  for (const file of tender) form.append('tender', file, file.name);
  if (tender.length === 0) form.append('tender', new Blob([]), '');
  for (const file of bid) form.append('bid', file, file.name);
  if (bid.length === 0) form.append('bid', new Blob([]), '');
  return form;
}

export function uploadDuplicateFiles(
  projectId: string, tender: File[], bid: File[],
): Promise<DuplicateFilesResponse> {
  return request<DuplicateFilesResponse>(
    `/projects/${encodeURIComponent(projectId)}/duplicate/files`,
    { method: 'POST', body: buildDuplicateFilesForm(tender, bid) });
}

export function clearDuplicateFiles(projectId: string): Promise<DuplicateFilesResponse> {
  return request<DuplicateFilesResponse>(
    `/projects/${encodeURIComponent(projectId)}/duplicate/files`,
    { method: 'DELETE' });
}

export function getDuplicateState(projectId: string): Promise<DuplicateState> {
  return request<DuplicateState>(
    `/projects/${encodeURIComponent(projectId)}/duplicate/state`);
}

export function startAnalysis(projectId: string, force = false): Promise<{ taskId: string }> {
  return request<{ taskId: string }>(
    `/projects/${encodeURIComponent(projectId)}/duplicate/analysis`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force }),
    });
}

// -------------------------------------------------------------------- tasks

export function listProjectTasks(projectId: string): Promise<TaskRow[]> {
  return request<TaskRow[]>(`/projects/${encodeURIComponent(projectId)}/tasks`);
}

export function getTask(taskId: string): Promise<TaskRow> {
  return request<TaskRow>(`/tasks/${encodeURIComponent(taskId)}`);
}

/** 返回退订函数；EventSource 断线自动重连由浏览器自持。 */
export function subscribeTaskEvents(
  taskId: string, onEvent: (event: TaskStatusEvent) => void,
): () => void {
  const source = new EventSource(`/tasks/${encodeURIComponent(taskId)}/events`);
  source.onmessage = (message: MessageEvent<string>) => {
    try {
      onEvent(JSON.parse(message.data) as TaskStatusEvent);
    } catch {
      // 心跳之外不可解析的事件忽略（心跳本身是注释行，不触发 message）
    }
  };
  return () => source.close();
}

// ------------------------------------------------------------------- export

function fileNameFromDisposition(header: string | null): string | null {
  if (!header) return null;
  const star = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (star) {
    try {
      return decodeURIComponent(star[1].trim());
    } catch {
      // 编码异常回退 filename= 形态
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1] : null;
}

export async function exportDuplicateWorkbook(
  projectId: string,
): Promise<{ blob: Blob; fileName: string }> {
  const response = await fetch(
    `/projects/${encodeURIComponent(projectId)}/duplicate/export`);
  if (!response.ok) throw new Error(await errorMessage(response));
  const blob = await response.blob();
  const fileName = fileNameFromDisposition(response.headers.get('Content-Disposition'))
    || '标书查重结果.xlsx';
  return { blob, fileName };
}

export function downloadBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------- rejection

// 追加到 api/client.ts 末尾（rejection 数据获取层）。

export function getRejectionState(projectId: string): Promise<RejectionState> {
  return request<RejectionState>(
    `/projects/${encodeURIComponent(projectId)}/rejection/state`);
}

export function uploadRejectionDocuments(
  projectId: string, role: RejectionRole, files: File[],
): Promise<{ tender: unknown[]; bid: unknown[] }> {
  const form = new FormData();
  form.append('role', role);
  for (const file of files) form.append('files', file);
  if (files.length === 0) {
    form.append('files', new File([], '', { type: 'text/plain' }));
  }
  return request(`/projects/${encodeURIComponent(projectId)}/rejection/documents`, {
    method: 'POST',
    body: form,
  });
}

export function deleteRejectionDocument(
  projectId: string, role: RejectionRole, documentId?: string,
): Promise<unknown> {
  const params = new URLSearchParams({ role });
  if (documentId) params.set('documentId', documentId);
  return request(
    `/projects/${encodeURIComponent(projectId)}/rejection/documents?${params.toString()}`,
    { method: 'DELETE' });
}

/** 返回 taskId；失败抛错（错误文案即端点 detail）。 */
export function startRejectionExtraction(projectId: string): Promise<{ taskId: string }> {
  return request(`/projects/${encodeURIComponent(projectId)}/rejection/extract`,
    { method: 'POST' });
}

export function startSubjectIdentification(
  projectId: string,
): Promise<{ taskId: string }> {
  return request(
    `/projects/${encodeURIComponent(projectId)}/rejection/subject/identify`,
    { method: 'POST' });
}

export function confirmRejectionSubject(
  projectId: string, officialName: string, aliases: string[],
): Promise<{ success: boolean; message: string; profile: unknown }> {
  return request(
    `/projects/${encodeURIComponent(projectId)}/rejection/subject/confirm`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ officialName, aliases }) });
}

export function startRejectionCheck(
  projectId: string, checkOptions?: Record<string, boolean>,
): Promise<{ taskId: string }> {
  return request(`/projects/${encodeURIComponent(projectId)}/rejection/run`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(checkOptions ? { checkOptions } : {}),
  });
}

export function saveRejectionUiState(
  projectId: string, patch: Record<string, unknown>,
): Promise<RejectionState> {
  return request(`/projects/${encodeURIComponent(projectId)}/rejection/ui`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
}

export interface ExportDownload {
  blob: Blob;
  /** 服务端建议文件名（Content-Disposition filename*，缺省 null）。 */
  fileName: string | null;
}

export async function exportRejectionExcel(projectId: string): Promise<ExportDownload> {
  const response = await fetch(
    `/projects/${encodeURIComponent(projectId)}/rejection/export`);
  if (!response.ok) throw new Error(await errorMessage(response));
  const disposition = response.headers.get('Content-Disposition') || '';
  const utf8Name = /filename\*=UTF-8''([^;]+)/.exec(disposition);
  const fileName = utf8Name ? decodeURIComponent(utf8Name[1]) : null;
  return { blob: await response.blob(), fileName };
}

/** 服务端命名形态：废标检查结果_<YYYYMMDD_HHMMSS>.xlsx（无响应头时的兜底）。 */
export function exportTimestamp(): string {
  const now = new Date();
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_` +
    `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}
