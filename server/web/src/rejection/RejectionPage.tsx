// 废标项检查页（P4d-2）：三步流编排层。
// - 步骤 1 文档导入（招标/投标分组上传，覆盖式 + 删除）；
// - 步骤 2 提取（任务轮询）→ 步骤 3/4 主体识别与确认 → 步骤 5 检查（四项开关 + 任务）；
// - 结果区：四项结果概览 + findings 明细 + Excel 导出。
// 轮询沿用查重页模式（1s getTask 直至终态；进度以 state 刷新承载）。
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  confirmRejectionSubject,
  deleteRejectionDocument,
  downloadBlob,
  exportRejectionExcel,
  getRejectionState,
  getTask,
  saveRejectionUiState,
  startRejectionCheck,
  startRejectionExtraction,
  startSubjectIdentification,
  uploadRejectionDocuments,
} from '../api/client';
import type {
  RejectionDocumentView,
  RejectionState,
  RejectionTaskView,
} from '../api/types';
import { useToast } from '../ui';

type Step = 'documents' | 'extract' | 'subject' | 'check';
const STEPS: Array<{ key: Step; label: string }> = [
  { key: 'documents', label: '1 文档导入' },
  { key: 'extract', label: '2 无效与废标项解析' },
  { key: 'subject', label: '3 投标主体' },
  { key: 'check', label: '4 检查与结果' },
];

const CHECK_LABELS: Record<string, string> = {
  rejection: '废标项检查',
  typo: '错别字检查',
  logic: '逻辑谬误检查',
  subject: '主体一致性检查',
};

const STATUS_LABELS: Record<string, string> = {
  idle: '未执行', running: '检查中', success: '已完成', error: '失败',
};

function DocumentList({ documents, onDelete }: {
  documents: RejectionDocumentView[];
  onDelete: (role: 'tender' | 'bid', documentId?: string) => void;
}) {
  if (documents.length === 0) return <p className="muted">尚未导入</p>;
  return (
    <ul className="rejection-doc-list">
      {documents.map((doc) => (
        <li key={doc.id}>
          <span>{doc.fileName}</span>
          <span className="muted">{doc.contentChars} 字</span>
          <button type="button" className="text-button"
            onClick={() => onDelete(doc.role as 'tender' | 'bid', doc.id)}>
            删除
          </button>
        </li>
      ))}
    </ul>
  );
}

function TaskBadge({ task }: { task?: RejectionTaskView }) {
  if (!task) return null;
  if (task.status === 'running') {
    return <span className="muted">进行中 {task.progress}%</span>;
  }
  if (task.status === 'error') {
    return <span className="error-text">失败：{task.error || '未知错误'}</span>;
  }
  return <span className="muted">已完成</span>;
}

export default function RejectionPage({ projectId }: { projectId: string }) {
  const { showToast } = useToast();
  const [state, setState] = useState<RejectionState | null>(null);
  const [step, setStep] = useState<Step>('documents');
  const [busy, setBusy] = useState(false);
  const [polling, setPolling] = useState<string | null>(null);
  const [officialName, setOfficialName] = useState('');
  const [aliasesText, setAliasesText] = useState('');
  const pollRef = useRef<number | null>(null);

  const refresh = useCallback(async (): Promise<RejectionState> => {
    const next = await getRejectionState(projectId);
    setState(next);
    return next;
  }, [projectId]);

  useEffect(() => {
    let cancelled = false;
    refresh().then((next) => {
      if (cancelled) return;
      if (next.subjectProfile.officialName) {
        setOfficialName(next.subjectProfile.officialName);
        setAliasesText(next.subjectProfile.aliases.join('、'));
      }
    }).catch((error) => showToast(
      error instanceof Error ? error.message : '读取状态失败', 'error'));
    return () => { cancelled = true; };
  }, [refresh, showToast]);

  // 单一活动任务轮询：终态后刷新整个状态
  const pollTask = useCallback((taskId: string) => {
    setPolling(taskId);
    if (pollRef.current !== null) window.clearInterval(pollRef.current);
    pollRef.current = window.setInterval(() => {
      void (async () => {
        const task = await getTask(taskId);
        if (task.status === 'running' || task.status === 'queued') return;
        if (pollRef.current !== null) window.clearInterval(pollRef.current);
        pollRef.current = null;
        setPolling(null);
        await refresh();
        if (task.status === 'error') {
          showToast(task.error || '任务失败', 'error');
        }
      })();
    }, 1000);
  }, [refresh, showToast]);

  useEffect(() => () => {
    if (pollRef.current !== null) window.clearInterval(pollRef.current);
  }, []);

  const runAction = useCallback(async (action: () => Promise<{ taskId: string }>) => {
    setBusy(true);
    try {
      const { taskId } = await action();
      pollTask(taskId);
    } catch (error) {
      showToast(error instanceof Error ? error.message : '操作失败', 'error');
    } finally {
      setBusy(false);
    }
  }, [pollTask, showToast]);

  const handleUpload = useCallback(async (
    role: 'tender' | 'bid', files: FileList | null,
  ) => {
    if (!files || files.length === 0) return;
    setBusy(true);
    try {
      await uploadRejectionDocuments(projectId, role, Array.from(files));
      await refresh();
      showToast('导入成功', 'success');
    } catch (error) {
      showToast(error instanceof Error ? error.message : '导入失败', 'error');
    } finally {
      setBusy(false);
    }
  }, [projectId, refresh, showToast]);

  const handleDelete = useCallback(async (role: 'tender' | 'bid', documentId?: string) => {
    setBusy(true);
    try {
      await deleteRejectionDocument(projectId, role, documentId);
      await refresh();
    } catch (error) {
      showToast(error instanceof Error ? error.message : '删除失败', 'error');
    } finally {
      setBusy(false);
    }
  }, [projectId, refresh, showToast]);

  const handleConfirmSubject = useCallback(async () => {
    if (!officialName.trim()) {
      showToast('请填写正式投标主体名称', 'error');
      return;
    }
    setBusy(true);
    try {
      const aliases = aliasesText.split(/[、,，\s]+/).filter(Boolean);
      await confirmRejectionSubject(projectId, officialName.trim(), aliases);
      await refresh();
      showToast('投标主体已确认', 'success');
    } catch (error) {
      showToast(error instanceof Error ? error.message : '确认失败', 'error');
    } finally {
      setBusy(false);
    }
  }, [aliasesText, officialName, projectId, refresh, showToast]);

  const handleExport = useCallback(async () => {
    setBusy(true);
    try {
      const blob = await exportRejectionExcel(projectId);
      downloadBlob(blob, `废标检查结果_${new Date().toISOString().slice(0, 10)}.xlsx`);
    } catch (error) {
      showToast(error instanceof Error ? error.message : '导出失败', 'error');
    } finally {
      setBusy(false);
    }
  }, [projectId, showToast]);

  if (!state) return <div className="muted">加载中…</div>;

  const extraction = state.invalidBidAndRejectionItems;
  const profile = state.subjectProfile;
  const checks: Array<[string, typeof state.rejectionCheckResult]> = [
    ['rejection', state.rejectionCheckResult],
    ['typo', state.typoCheckResult],
    ['logic', state.logicCheckResult],
    ['subject', state.subjectCheckResult],
  ];
  const taskFor = (key: string): RejectionTaskView | undefined =>
    key === 'extraction' ? state.extractionTask
      : key === 'check' ? state.checkTask
        : state.subjectIdentificationTask;

  return (
    <div className="rejection-page">
      <nav className="rejection-steps" aria-label="检查步骤">
        {STEPS.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`rejection-step${step === item.key ? ' is-active' : ''}`}
            onClick={() => setStep(item.key)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      {step === 'documents' && (
        <section className="rejection-section">
          <h2>文档导入</h2>
          <div className="rejection-upload-row">
            <div>
              <h3>招标文件</h3>
              <input type="file" multiple accept=".docx,.md,.txt"
                onChange={(event) => void handleUpload('tender', event.target.files)} />
              <DocumentList documents={state.tenderDocuments}
                onDelete={(role, id) => void handleDelete(role, id)} />
              {state.tenderDocument && (
                <p className="muted">解析主文档：{state.tenderDocument.fileName}</p>
              )}
            </div>
            <div>
              <h3>投标文件（可多份）</h3>
              <input type="file" multiple accept=".docx,.md,.txt"
                onChange={(event) => void handleUpload('bid', event.target.files)} />
              <DocumentList documents={state.bidDocuments}
                onDelete={(role, id) => void handleDelete(role, id)} />
            </div>
          </div>
          <button type="button" className="primary-action"
            disabled={busy || !state.tenderDocument || state.bidDocuments.length === 0}
            onClick={() => setStep('extract')}>
            下一步：解析检查范围
          </button>
        </section>
      )}

      {step === 'extract' && (
        <section className="rejection-section">
          <h2>无效与废标项解析</h2>
          <TaskBadge task={state.extractionTask} />
          <p>
            状态：{extraction.status === 'success' ? '已完成'
              : extraction.status === 'error' ? `失败：${extraction.error || '未知'}`
                : '未执行'}
          </p>
          {extraction.status === 'success' && (
            <pre className="rejection-extract-preview">{extraction.content}</pre>
          )}
          <div className="rejection-actions">
            <button type="button" className="primary-action" disabled={busy || polling !== null}
              onClick={() => void runAction(() => startRejectionExtraction(projectId))}>
              {extraction.status === 'success' ? '重新解析' : '开始解析'}
            </button>
            <button type="button" className="primary-action"
              disabled={extraction.status !== 'success'}
              onClick={() => setStep('subject')}>
              下一步：确认投标主体
            </button>
          </div>
        </section>
      )}

      {step === 'subject' && (
        <section className="rejection-section">
          <h2>投标主体</h2>
          <TaskBadge task={state.subjectIdentificationTask} />
          <p>识别状态：{profile.status === 'confirmed' ? '已确认'
            : profile.status === 'pending-confirmation' ? '待确认' : '未识别'}</p>
          <label className="rejection-field">
            正式投标主体
            <input value={officialName}
              onChange={(event) => setOfficialName(event.target.value)} />
          </label>
          <label className="rejection-field">
            允许简称（用顿号/逗号分隔）
            <input value={aliasesText}
              onChange={(event) => setAliasesText(event.target.value)} />
          </label>
          <div className="rejection-actions">
            <button type="button" disabled={busy || polling !== null}
              onClick={() => void runAction(() => startSubjectIdentification(projectId))}>
              自动识别
            </button>
            <button type="button" className="primary-action" disabled={busy}
              onClick={() => void handleConfirmSubject()}>
              确认主体
            </button>
            <button type="button" className="primary-action"
              disabled={profile.status !== 'confirmed'}
              onClick={() => {
                void saveRejectionUiState(projectId, { step: 'check' }).then(() => setStep('check'));
              }}>
              下一步：开始检查
            </button>
          </div>
        </section>
      )}

      {step === 'check' && (
        <section className="rejection-section">
          <h2>检查与结果</h2>
          <TaskBadge task={state.checkTask} />
          <div className="rejection-actions">
            <button type="button" className="primary-action" disabled={busy || polling !== null}
              onClick={() => void runAction(() => startRejectionCheck(projectId))}>
              开始检查
            </button>
            <button type="button" disabled={busy}
              onClick={() => void handleExport()}>
              导出 Excel
            </button>
          </div>
          <ul className="rejection-results">
            {checks.map(([key, result]) => {
              const task = taskFor(key === 'rejection' ? 'check' : key === 'subject' ? 'check' : key);
              return (
                <li key={key} className="rejection-result-row">
                  <strong>{CHECK_LABELS[key]}</strong>
                  <span>{STATUS_LABELS[result.status] || result.status}</span>
                  <span className="muted">{result.progressMessage || ''}</span>
                  {key === 'subject' && result.stats && (
                    <span className="muted">
                      印章状态：{String((result.stats as Record<string, unknown>).visionStatus || '未执行')}
                    </span>
                  )}
                  {result.error && <span className="error-text">{result.error}</span>}
                  {task?.status === 'error' && <span className="error-text">{task.error}</span>}
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}
