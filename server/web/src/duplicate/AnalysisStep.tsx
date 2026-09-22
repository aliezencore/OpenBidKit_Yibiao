// 分析步：四页签（元数据/目录/正文/图片）+ 结果区 + 分页/复制。
// 交互语义对齐参考页 DuplicateCheckPage.tsx :181-594（R-P3d3-6）。
import { useEffect, useMemo, useState } from 'react';
import type {
  ContentAnalysis,
  DuplicateSectionKey,
  ImageAnalysis,
  MetadataAnalysis,
  OutlineAnalysis,
  OutlineGroup,
  WorkspaceFile,
} from '../api/types';
import {
  buildFileLabelMap,
  fileIndexLabel,
  formatDuplicateSentenceText,
  formatImageLocationSentence,
  progressText,
  statusLabel,
} from '../lib/format';
import { ProgressBar, useToast } from '../ui';

export const analysisTabs: Array<{ id: DuplicateSectionKey; label: string }> = [
  { id: 'metadata', label: '元数据' },
  { id: 'outline', label: '目录' },
  { id: 'content', label: '正文' },
  { id: 'image', label: '图片' },
];

function DuplicateFileCodeBar({ files }: { files: WorkspaceFile[] }) {
  return (
    <div className="file-codebar" aria-label="投标文件编号">
      {files.map((file, index) => (
        <span key={file.id} title={file.file_name}>
          <strong>{fileIndexLabel(index)}</strong>
          {file.file_name}
        </span>
      ))}
    </div>
  );
}

function PaginationControls({
  page, pageSize, total, onPageChange,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="pagination">
      <span>
        第 {Math.min(page, totalPages)} / {totalPages} 页，共 {total} 条
      </span>
      <div>
        <button
          type="button"
          onClick={() => onPageChange(Math.max(1, page - 1))}
          disabled={page <= 1}
        >
          上一页
        </button>
        <button
          type="button"
          onClick={() => onPageChange(Math.min(totalPages, page + 1))}
          disabled={page >= totalPages}
        >
          下一页
        </button>
      </div>
    </div>
  );
}

function usePaged<T>(items: T[], pageSize: number) {
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [items.length]);
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const pageItems = items.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  return { currentPage, pageItems, setPage, totalPages };
}

async function copyText(
  value: string | null | undefined, showToast: (message: string, kind?: 'info' | 'success' | 'error') => void,
  doneMessage: string, failedMessage: string,
): Promise<void> {
  try {
    await navigator.clipboard.writeText(value || '');
    showToast(doneMessage, 'success');
  } catch {
    showToast(failedMessage, 'error');
  }
}

// ---------------------------------------------------------------- 元数据页签

function MetadataPane({
  analysis, bidFiles,
}: {
  analysis: MetadataAnalysis | null;
  bidFiles: WorkspaceFile[];
}) {
  const isDone = analysis?.status === 'success' || analysis?.status === 'error';
  const rows = analysis?.rows ?? [];
  const files = analysis?.files?.length
    ? analysis.files
    : bidFiles.map((file) => ({
      file_id: file.id,
      file_name: file.file_name,
      status: 'pending' as const,
      metadata: [],
    }));

  return (
    <div>
      <div className="duplicate-sub-status">
        <article>
          <span>正文内容提取</span>
          <strong>{progressText(analysis?.contentExtraction)}</strong>
          <small>{statusLabel(analysis?.contentExtraction?.status ?? 'pending')}</small>
        </article>
        <article>
          <span>元数据提取</span>
          <strong>{progressText(analysis?.metadataExtraction)}</strong>
          <small>{statusLabel(analysis?.metadataExtraction?.status ?? 'pending')}</small>
        </article>
      </div>

      {!analysis && (
        <div className="analysis-empty">
          <strong>等待启动元数据分析</strong>
          <p>进入查重结果后，会自动并发执行正文内容提取和投标文件元数据提取。</p>
        </div>
      )}

      {analysis && rows.length === 0 && (
        <div className="analysis-empty">
          <strong>{analysis.status === 'running' ? '正在提取元数据' : '暂无可对比元数据'}</strong>
          <p>{analysis.message || '请稍候，文件较多时需要一定时间。'}</p>
        </div>
      )}

      {rows.length > 0 && (
        <div className="metadata-table-wrap">
          <table className="metadata-table">
            <thead>
              <tr>
                <th>元数据项</th>
                {files.map((file) => (
                  <th key={file.file_id} title={file.file_name}>{file.file_name}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  <th>{row.label}</th>
                  {files.map((file) => {
                    const duplicated = row.duplicate_file_ids?.includes(file.file_id) ?? false;
                    const sameDay = row.same_day_file_ids?.includes(file.file_id) ?? false;
                    return (
                      <td
                        key={file.file_id}
                        className={duplicated ? 'is-duplicate' : sameDay ? 'is-same-day' : undefined}
                      >
                        {row.values[file.file_id] || '-'}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {isDone && analysis?.contentFiles?.some((file) => file.status === 'error') && (
        <p className="analysis-warning">部分文件正文提取失败，可重新选择文件后再分析。</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 目录页签

function getOutlineGroupText(group: OutlineGroup): string {
  const firstPath = group.file_ids
    .map((fileId) => group.paths?.[fileId]?.[0])
    .find(Boolean);
  return group.type === 'duplicate' && firstPath
    ? firstPath
    : group.title || firstPath || '未识别目录';
}

function OutlinePane({
  analysis, bidFiles,
}: {
  analysis: OutlineAnalysis | null;
  bidFiles: WorkspaceFile[];
}) {
  const { showToast } = useToast();
  const labelMap = useMemo(() => buildFileLabelMap(bidFiles), [bidFiles]);
  const duplicateGroups = analysis?.duplicateGroups ?? [];
  const { currentPage, pageItems, setPage } = usePaged(duplicateGroups, 50);
  const successfulCount = analysis?.files?.filter((file) => file.status === 'success').length ?? 0;

  return (
    <div>
      <DuplicateFileCodeBar files={bidFiles} />
      {duplicateGroups.length > 0 ? (
        <section>
          <div className="match-card-head">
            <strong>重复目录</strong>
            <span>
              {analysis?.message} · 已排除招标目录 {analysis?.tenderMatchedItemCount ?? 0} 项
            </span>
          </div>
          <div className="match-list">
            {pageItems.map((group) => {
              const text = getOutlineGroupText(group);
              return (
                <article key={group.id}>
                  <div className="sentence-content">
                    <p>
                      {text}
                      <button
                        type="button"
                        className="sentence-copy"
                        onClick={() => void copyText(text, showToast, '已复制重复目录', '复制重复目录失败')}
                        aria-label="复制重复目录"
                      >
                        复制
                      </button>
                    </p>
                  </div>
                  <div className="file-badges">
                    {group.file_ids.map((fileId) => {
                      const count = group.paths?.[fileId]?.length
                        || group.item_ids?.[fileId]?.length
                        || 1;
                      return (
                        <span
                          key={fileId}
                          title={bidFiles.find((file) => file.id === fileId)?.file_name || fileId}
                        >
                          {labelMap.get(fileId) || '?'}
                          {count > 1 ? ` x${count}` : ''}
                        </span>
                      );
                    })}
                  </div>
                </article>
              );
            })}
          </div>
          <PaginationControls
            page={currentPage}
            pageSize={50}
            total={duplicateGroups.length}
            onPageChange={setPage}
          />
        </section>
      ) : (
        <div className="analysis-empty">
          <strong>{analysis?.status === 'running' ? '正在分析目录' : '未发现重复目录'}</strong>
          <p>
            {analysis?.status === 'running'
              ? analysis.message
              : successfulCount > 0
                ? '未发现投标文件之间的目录重复；来自招标文件的目录项已自动排除。'
                : '暂无可用目录结果。'}
          </p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 正文页签

function ContentPane({
  analysis, bidFiles,
}: {
  analysis: ContentAnalysis | null;
  bidFiles: WorkspaceFile[];
}) {
  const { showToast } = useToast();
  const labelMap = useMemo(() => buildFileLabelMap(bidFiles), [bidFiles]);
  const duplicateSentences = analysis?.duplicateSentences ?? [];
  const { currentPage, pageItems, setPage } = usePaged(duplicateSentences, 50);

  return (
    <div>
      <DuplicateFileCodeBar files={bidFiles} />
      {duplicateSentences.length > 0 ? (
        <section>
          <div className="match-card-head">
            <strong>重复句子</strong>
            <span>
              {analysis?.message} · 已排除招标引用 {analysis?.tenderMatchedSentenceCount ?? 0} 句
            </span>
          </div>
          <div className="match-list">
            {pageItems.map((item) => (
              <article key={item.id}>
                <div className="sentence-content">
                  <p>
                    {formatDuplicateSentenceText(item.normalized, item.sentence)}
                    <button
                      type="button"
                      className="sentence-copy"
                      onClick={() => void copyText(
                        item.sentence || item.normalized,
                        showToast, '已复制重复句子', '复制重复句子失败',
                      )}
                      aria-label="复制重复句子"
                    >
                      复制
                    </button>
                  </p>
                </div>
                <div className="file-badges">
                  {item.file_ids.map((fileId) => (
                    <span
                      key={fileId}
                      title={bidFiles.find((file) => file.id === fileId)?.file_name || fileId}
                    >
                      {labelMap.get(fileId) || '?'}
                      {(item.occurrences?.[fileId] ?? 0) > 1 ? ` x${item.occurrences[fileId]}` : ''}
                    </span>
                  ))}
                </div>
              </article>
            ))}
          </div>
          <PaginationControls
            page={currentPage}
            pageSize={50}
            total={duplicateSentences.length}
            onPageChange={setPage}
          />
        </section>
      ) : (
        <div className="analysis-empty">
          <strong>{analysis?.status === 'running' ? '正在比对正文' : '未发现重复句子'}</strong>
          <p>
            {analysis?.status === 'running'
              ? analysis.message
              : '未发现投标文件之间的重复句子；引用招标文件的句子已自动排除。'}
          </p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 图片页签

function ImagePane({
  analysis, bidFiles,
}: {
  analysis: ImageAnalysis | null;
  bidFiles: WorkspaceFile[];
}) {
  const { showToast } = useToast();
  const labelMap = useMemo(() => buildFileLabelMap(bidFiles), [bidFiles]);
  const duplicateImages = analysis?.duplicateImages ?? [];
  const { currentPage, pageItems, setPage } = usePaged(duplicateImages, 24);

  return (
    <div>
      <DuplicateFileCodeBar files={bidFiles} />
      {duplicateImages.length > 0 ? (
        <section>
          <div className="match-card-head">
            <strong>重复图片</strong>
            <span>{analysis?.message} · 共识别 {analysis?.totalImageCount ?? 0} 张图片</span>
          </div>
          <div className="image-grid">
            {pageItems.map((item) => {
              const locationEntries = item.file_ids.flatMap((fileId) => {
                const location = item.locations?.[fileId]?.[0];
                return location ? [{ fileId, location }] : [];
              });
              return (
                <article key={item.id}>
                  <div className="image-preview">
                    <img
                      src={item.preview_url}
                      alt={`重复图片 ${item.hash.slice(0, 10)}`}
                      loading="lazy"
                    />
                  </div>
                  <strong>Hash {item.hash.slice(0, 12)}</strong>
                  <div className="file-badges">
                    {item.file_ids.map((fileId) => (
                      <span
                        key={fileId}
                        title={bidFiles.find((file) => file.id === fileId)?.file_name || fileId}
                      >
                        {labelMap.get(fileId) || '?'}
                        {(item.occurrences?.[fileId] ?? 0) > 1 ? ` x${item.occurrences[fileId]}` : ''}
                      </span>
                    ))}
                  </div>
                  {locationEntries.length > 0 && (
                    <div className="image-locations">
                      {locationEntries.map((entry) => (
                        <div key={entry.fileId} className="image-location">
                          <span>
                            {labelMap.get(entry.fileId) || '?'}：{entry.location.directory || '未识别目录'}
                          </span>
                          <p title={entry.location.previous_sentence || undefined}>
                            前文：
                            {entry.location.previous_sentence
                              ? formatImageLocationSentence(entry.location.previous_sentence)
                              : '未提取到图片前文'}
                          </p>
                          <button
                            type="button"
                            onClick={() => void copyText(
                              entry.location.previous_sentence,
                              showToast, '已复制定位线索', '复制定位线索失败',
                            )}
                            disabled={!entry.location.previous_sentence}
                          >
                            复制定位线索
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
          <PaginationControls
            page={currentPage}
            pageSize={24}
            total={duplicateImages.length}
            onPageChange={setPage}
          />
        </section>
      ) : (
        <div className="analysis-empty">
          <strong>{analysis?.status === 'running' ? '正在比对图片' : '未发现重复图片'}</strong>
          <p>{analysis?.status === 'running' ? analysis.message : '未发现投标文件之间完全相同的图片。'}</p>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ 分析步

export interface AnalysisStepProps {
  serverState: DuplicateStateView | null;
  activeTab: DuplicateSectionKey;
  onTabChange: (tab: DuplicateSectionKey) => void;
  running: boolean;
  onRerun: () => void;
}

/** AnalysisStep 实际消费的 serverState 视图（解耦整体类型）。 */
export type DuplicateStateView = {
  tenderFiles: WorkspaceFile[];
  bidFiles: WorkspaceFile[];
  metadataAnalysis: MetadataAnalysis | null;
  outlineAnalysis: OutlineAnalysis | null;
  contentAnalysis: ContentAnalysis | null;
  imageAnalysis: ImageAnalysis | null;
};

export default function AnalysisStep({
  serverState, activeTab, onTabChange, running, onRerun,
}: AnalysisStepProps) {
  const metadata = serverState?.metadataAnalysis ?? null;
  const metadataStatus = metadata?.status ?? 'pending';
  const metadataProgress = metadataStatus === 'success' || metadataStatus === 'error'
    ? 100
    : metadata?.metadataExtraction?.total
      ? Math.round(
        (metadata.metadataExtraction.completed / metadata.metadataExtraction.total) * 100,
      )
      : 0;

  return (
    <section className="duplicate-analysis-panel">
      <div className="duplicate-page-title">
        <div>
          <span className="section-kicker">STEP 02</span>
          <h2>查重结果</h2>
        </div>
        <button
          type="button"
          className="secondary-action"
          onClick={onRerun}
          disabled={serverState?.bidFiles.length === 0 || running}
        >
          {running ? '分析中...' : '重新查重'}
        </button>
      </div>

      <div className="duplicate-analysis-tabs" role="tablist" aria-label="标书查重维度">
        {analysisTabs.map((item) => {
          const isActive = item.id === activeTab;
          const analysis = item.id === 'metadata'
            ? metadata
            : item.id === 'outline'
              ? serverState?.outlineAnalysis
              : item.id === 'content'
                ? serverState?.contentAnalysis
                : serverState?.imageAnalysis;
          const status = analysis?.status ?? 'pending';
          const progress = item.id === 'metadata'
            ? metadataProgress
            : analysis?.progress ?? 0;
          return (
            <button
              type="button"
              key={item.id}
              className={`duplicate-analysis-tab${isActive ? ' is-active' : ''} is-${status}`}
              role="tab"
              aria-selected={isActive}
              onClick={() => onTabChange(item.id)}
            >
              <span className="duplicate-analysis-tab-main">
                <strong>{item.label}</strong>
                <em>{statusLabel(status)}</em>
              </span>
              {status !== 'pending' && (
                <ProgressBar value={progress} label={`${item.label}分析进度 ${progress}%`} />
              )}
            </button>
          );
        })}
      </div>

      <div
        className="duplicate-analysis-content"
        role="tabpanel"
        aria-label={`${analysisTabs.find((item) => item.id === activeTab)?.label ?? ''}结果`}
      >
        {activeTab === 'metadata' && (
          <MetadataPane analysis={metadata} bidFiles={serverState?.bidFiles ?? []} />
        )}
        {activeTab === 'outline' && (
          <OutlinePane
            analysis={serverState?.outlineAnalysis ?? null}
            bidFiles={serverState?.bidFiles ?? []}
          />
        )}
        {activeTab === 'content' && (
          <ContentPane
            analysis={serverState?.contentAnalysis ?? null}
            bidFiles={serverState?.bidFiles ?? []}
          />
        )}
        {activeTab === 'image' && (
          <ImagePane
            analysis={serverState?.imageAnalysis ?? null}
            bidFiles={serverState?.bidFiles ?? []}
          />
        )}
      </div>
    </section>
  );
}
