// 展示层纯函数——从参考页 DuplicateCheckPage.tsx :38-96 逐字移植（R-P3d3-5）。
// 页面级签名 SHA1（参考 :98-167）不移植：signature 已服务端自持（R-P3d2-1）。
import type { AnalysisStatus } from '../api/types';

export function formatFileSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleString('zh-CN', { hour12: false });
}

export function statusLabel(status: AnalysisStatus): string {
  if (status === 'running') return '分析中';
  if (status === 'success') return '已完成';
  if (status === 'error') return '有错误';
  return '待分析';
}

export function progressText(progress?: { completed: number; total: number }): string {
  if (!progress?.total) return '0/0';
  return `${progress.completed}/${progress.total}`;
}

/** bijective base-26 文件编号（A..Z, AA..AZ, BA..）——逐字移植，勿「修正」。 */
export function fileIndexLabel(index: number): string {
  let value = index;
  let label = '';
  do {
    label = String.fromCharCode(65 + (value % 26)) + label;
    value = Math.floor(value / 26) - 1;
  } while (value >= 0);
  return label;
}

export function buildFileLabelMap<T extends { id: string }>(
  files: T[],
): Map<string, string> {
  return new Map(files.map((file, index) => [file.id, fileIndexLabel(index)]));
}

export function formatDuplicateSentenceText(normalized: string, sentence: string): string {
  const text = normalized || sentence;
  return text.length > 600 ? `${text.slice(0, 600)}...` : text;
}

export function formatImageLocationSentence(value: string): string {
  return value.length > 72 ? `${value.slice(0, 72)}...` : value;
}
