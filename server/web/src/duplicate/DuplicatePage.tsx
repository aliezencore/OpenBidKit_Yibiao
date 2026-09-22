// 查重页主容器（编排层）：
// - 会话文件集模型（R-P3d3-1）：本会话选中的 File 是唯一可提交面；
//   页面加载发现服务端已有文件时集合锁定（只读），重置后解锁。
// - 上传 = 两组全量替换（POST /files 恒带两组字段），成功后刷新服务端状态。
// - 步进：upload ↔ analysis；恢复服务端 step / activeAnalysisTab。
// - 自动启动（R-P3d3-2，守卫见 lib/autoStart）+ SSE 终态信号 + 1s 轮询
//   进度（R-P3d3-3，runner 不向 bus 发进度事件，轮询是唯一进度源）。
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  clearDuplicateFiles,
  downloadBlob,
  exportDuplicateWorkbook,
  getDuplicateState,
  getTask,
  listProjectTasks,
  startAnalysis as startAnalysisApi,
  subscribeTaskEvents,
  uploadDuplicateFiles,
} from '../api/client';
import type {
  DuplicateSectionKey,
  DuplicateState,
  TaskStatus,
  WorkspaceFile,
} from '../api/types';
import { shouldAutoStart } from '../lib/autoStart';
import { useToast } from '../ui';
import AnalysisStep from './AnalysisStep';
import UploadStep, { fileKey } from './UploadStep';

type Step = 'upload' | 'analysis';
const STEPS: Step[] = ['upload', 'analysis'];

const TERMINAL_TASK_STATUSES: ReadonlyArray<TaskStatus> = ['success', 'failed', 'cancelled'];

export default function DuplicatePage({ projectId }: { projectId: string }) {
  const { showToast } = useToast();
  const [serverState, setServerState] = useState<DuplicateState | null>(null);
  const [tenderSession, setTenderSession] = useState<File[]>([]);
  const [bidSession, setBidSession] = useState<File[]>([]);
  const [step, setStep] = useState<Step>('upload');
  const [activeTab, setActiveTab] = useState<DuplicateSectionKey>('metadata');
  const [busy, setBusy] = useState<'tender' | 'bid' | null>(null);
  const [collectionLocked, setCollectionLocked] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [exporting, setExporting] = useState(false);
  // 自动启动守卫（上传/重置/强制重跑时复位）
  const autoStartRef = useRef(false);

  const refreshState = useCallback(async (): Promise<DuplicateState> => {
    const next = await getDuplicateState(projectId);
    setServerState(next);
    return next;
  }, [projectId]);

  // 首次加载：恢复步进 / 锁定判定 / 找回运行中的分析任务
  useEffect(() => {
    let cancelled = false;
    refreshState()
      .then((state) => {
        if (cancelled) return;
        setHydrated(true);
        if (state.step === 'analysis') setStep('analysis');
        setActiveTab(state.activeAnalysisTab);
        if (state.tenderFiles.length > 0 || state.bidFiles.length > 0) {
          setCollectionLocked(true);
        }
        return listProjectTasks(projectId)
          .then((rows) => {
            if (cancelled) return;
            const active = rows.find((row) => row.domain === 'duplicate'
              && row.kind === 'duplicate-analysis'
              && (row.status === 'queued' || row.status === 'running'));
            if (active) setTaskId(active.task_id);
          })
          .catch(() => undefined);
      })
      .catch((error) => {
        if (!cancelled) {
          showToast(error instanceof Error ? error.message : '读取查重状态失败', 'error');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, refreshState, showToast]);

  const tenderHydrated: WorkspaceFile[] = collectionLocked
    ? serverState?.tenderFiles ?? []
    : [];
  const bidHydrated: WorkspaceFile[] = collectionLocked
    ? serverState?.bidFiles ?? []
    : [];
  const bidCount = collectionLocked ? bidHydrated.length : bidSession.length;

  const sectionRunning = Boolean(
    serverState && Object.values(serverState.sections).some((section) => section.status === 'running'),
  );
  const analysisRunning = starting || taskId !== null || sectionRunning;

  // -------------------------------------------------------- 任务启动与终态

  const startAnalysis = useCallback(async (force: boolean) => {
    if (bidCount === 0) {
      showToast('请先上传至少一份投标文件', 'info');
      return;
    }
    autoStartRef.current = true;
    setStarting(true);
    try {
      const { taskId: id } = await startAnalysisApi(projectId, force);
      setTaskId(id);
      showToast(
        force ? '标书查重重新分析任务已在后台启动' : '标书查重分析任务已在后台启动',
        'success',
      );
    } catch (error) {
      autoStartRef.current = false;
      setStarting(false);
      showToast(error instanceof Error ? error.message : '启动查重分析失败', 'error');
    }
  }, [bidCount, projectId, showToast]);

  const handleTaskTerminal = useCallback((status: TaskStatus, error?: string | null) => {
    setTaskId(null);
    setStarting(false);
    void refreshState().then(() => {
      if (status === 'failed') {
        showToast(error ? `查重分析任务失败：${error}` : '查重分析任务失败', 'error');
      } else if (status === 'cancelled') {
        showToast('查重分析任务已取消', 'info');
      }
    }).catch(() => undefined);
  }, [refreshState, showToast]);

  // SSE 终态信号（R-P3d3-3）：queued/running 不动，终态关流 + 刷状态
  useEffect(() => {
    if (!taskId) return;
    const unsubscribe = subscribeTaskEvents(taskId, (event) => {
      if (event.type !== 'status') return;
      const status = TERMINAL_TASK_STATUSES.find((item) => item === event.status);
      if (status) handleTaskTerminal(status, event.error);
    });
    return unsubscribe;
  }, [handleTaskTerminal, taskId]);

  // 进度轮询（运行期 1s）；taskId 兜底直查任务行，防 SSE 断流卡死按钮
  useEffect(() => {
    if (!analysisRunning) return;
    const timer = window.setInterval(() => {
      void refreshState().catch(() => undefined);
      if (taskId) {
        void getTask(taskId)
          .then((task) => {
            const status = TERMINAL_TASK_STATUSES.find((item) => item === task.status);
            if (status) handleTaskTerminal(status, task.error);
          })
          .catch(() => undefined);
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [analysisRunning, handleTaskTerminal, refreshState, taskId]);

  // 自动启动（R-P3d3-2）
  useEffect(() => {
    if (!serverState?.sections) return;
    const allSuccess = Object.values(serverState.sections)
      .every((section) => section.status === 'success');
    if (!shouldAutoStart({
      step,
      bidCount,
      hydrated,
      starting,
      hasActiveTask: taskId !== null,
      sectionRunning,
      allSuccess,
      attempted: autoStartRef.current,
    })) return;
    void startAnalysis(false);
  }, [bidCount, hydrated, sectionRunning, serverState, startAnalysis, starting, step, taskId]);

  // ------------------------------------------------------------ 上传（全量替换）

  const persist = useCallback(async (
    group: 'tender' | 'bid', nextTender: File[], nextBid: File[],
  ) => {
    setBusy(group);
    try {
      await uploadDuplicateFiles(projectId, nextTender, nextBid);
      setTenderSession(nextTender);
      setBidSession(nextBid);
      await refreshState();
      autoStartRef.current = false;
      showToast(
        group === 'tender'
          ? `已加入 ${nextTender.length} 份招标文件，暂不执行解析`
          : '投标文件已加入，暂不执行解析',
        'success',
      );
    } catch (error) {
      showToast(error instanceof Error ? error.message : '上传文件失败', 'error');
    } finally {
      setBusy(null);
    }
  }, [projectId, refreshState, showToast]);

  const handleSelect = useCallback((group: 'tender' | 'bid', incoming: File[]) => {
    if (analysisRunning) {
      showToast('标书查重分析正在运行，请完成后再调整文件', 'info');
      return;
    }
    const current = group === 'tender' ? tenderSession : bidSession;
    const seen = new Set(current.map(fileKey));
    const fresh: File[] = [];
    let skipped = 0;
    for (const file of incoming) {
      const key = fileKey(file);
      if (seen.has(key)) {
        skipped += 1;
        continue;
      }
      seen.add(key);
      fresh.push(file);
    }
    if (skipped > 0) showToast('已跳过重复选择的文件', 'info');
    if (fresh.length === 0) return;
    void persist(
      group,
      group === 'tender' ? [...tenderSession, ...fresh] : tenderSession,
      group === 'bid' ? [...bidSession, ...fresh] : bidSession,
    );
  }, [analysisRunning, bidSession, persist, showToast, tenderSession]);

  const handleRemove = useCallback((group: 'tender' | 'bid', file: File) => {
    if (analysisRunning) {
      showToast('标书查重分析正在运行，请完成后再调整文件', 'info');
      return;
    }
    void persist(
      group,
      group === 'tender' ? tenderSession.filter((item) => item !== file) : tenderSession,
      group === 'bid' ? bidSession.filter((item) => item !== file) : bidSession,
    );
  }, [analysisRunning, bidSession, persist, showToast, tenderSession]);

  // -------------------------------------------------------------------- 重置

  const handleReset = useCallback(async () => {
    if (analysisRunning) {
      showToast('标书查重分析正在运行，请完成后再重置文件', 'info');
      return;
    }
    try {
      await clearDuplicateFiles(projectId);
      setTenderSession([]);
      setBidSession([]);
      setCollectionLocked(false);
      setStep('upload');
      setActiveTab('metadata');
      await refreshState();
      autoStartRef.current = false;
      showToast('已重置上传列表', 'success');
    } catch (error) {
      showToast(error instanceof Error ? error.message : '清空查重文件失败', 'error');
    }
  }, [analysisRunning, projectId, refreshState, showToast]);

  // ------------------------------------------------------------------- 导出

  // R-P3d3-4：前端无法自算签名（R-P3d2-1 signature 自持），按钮禁用条件用
  // 「任一段 success」近似；误判（有 success 段但签名失配/无行）由服务端
  // 400 detail 逐字兜底展示。
  const canExport = Boolean(
    serverState && Object.values(serverState.sections).some((section) => section.status === 'success'),
  );

  const handleExport = useCallback(async () => {
    setExporting(true);
    try {
      const { blob, fileName } = await exportDuplicateWorkbook(projectId);
      downloadBlob(blob, fileName);
      showToast(`已下载 ${fileName}`, 'success');
    } catch (error) {
      showToast(error instanceof Error ? error.message : '标书查重结果导出失败', 'error');
    } finally {
      setExporting(false);
    }
  }, [projectId, showToast]);

  // ------------------------------------------------------------------- 步进

  const activeIndex = STEPS.indexOf(step);
  const isNextDisabled = activeIndex >= STEPS.length - 1 || bidCount === 0;
  const nextTooltip = activeIndex >= STEPS.length - 1
    ? '当前已经是最后一步'
    : bidCount > 0
      ? '进入查重结果'
      : '请先上传至少一份投标文件';

  return (
    <div className="duplicate-check-page">
      {step === 'upload' ? (
        <UploadStep
          tenderSession={tenderSession}
          bidSession={bidSession}
          tenderHydrated={tenderHydrated}
          bidHydrated={bidHydrated}
          collectionLocked={collectionLocked}
          busy={busy}
          running={analysisRunning}
          onSelectTender={(files) => handleSelect('tender', files)}
          onSelectBid={(files) => handleSelect('bid', files)}
          onRemoveTender={(file) => handleRemove('tender', file)}
          onRemoveBid={(file) => handleRemove('bid', file)}
        />
      ) : (
        <AnalysisStep
          serverState={serverState}
          activeTab={activeTab}
          onTabChange={setActiveTab}
          running={analysisRunning}
          onRerun={() => void startAnalysis(true)}
        />
      )}

      <div className="duplicate-toolbar" role="toolbar" aria-label="标书查重工具条">
        {step === 'analysis' && (
          <button
            type="button"
            className="success-action"
            onClick={() => void handleExport()}
            disabled={analysisRunning || exporting || !canExport}
            title={analysisRunning
              ? '请等待当前查重任务结束后再导出'
              : !canExport
                ? '没有当前文件集合对应的可导出结果'
                : '导出当前元数据、目录、正文和图片查重结果'}
          >
            {exporting ? '导出中...' : '导出 Excel'}
          </button>
        )}
        <button
          type="button"
          className="danger-action"
          onClick={() => void handleReset()}
          disabled={analysisRunning || busy !== null}
          title="清空当前标书查重流程"
        >
          重置
        </button>
        <button
          type="button"
          className="secondary-action"
          onClick={() => setStep('upload')}
          disabled={step === 'upload'}
          title={step === 'upload' ? '当前已经是第一步' : '回到选择标书'}
        >
          上一步
        </button>
        <button
          type="button"
          className="primary-action"
          onClick={() => setStep('analysis')}
          disabled={isNextDisabled}
          title={nextTooltip}
        >
          下一步
        </button>
      </div>
    </div>
  );
}
