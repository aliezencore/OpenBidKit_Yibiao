// 应用壳：顶栏（品牌 / 导航占位 / 项目切换器）+ 主区域。
// 项目选择持久化 localStorage；失效 id 回落首项。
// 废标项检查导航项为 P4 占位（spec §15）。
import { useCallback, useEffect, useState } from 'react';
import { createProject, listProjects } from './api/client';
import type { ProjectRow } from './api/types';
import DuplicatePage from './duplicate/DuplicatePage';
import { ToastProvider, useToast } from './ui';

const PROJECT_STORAGE_KEY = 'yibiao.projectId';

function readStoredProjectId(): string {
  if (typeof window === 'undefined') return '';
  try {
    return window.localStorage.getItem(PROJECT_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

function AppShell() {
  const { showToast } = useToast();
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [projectId, setProjectId] = useState<string>(readStoredProjectId);
  const [loaded, setLoaded] = useState(false);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listProjects()
      .then((rows) => {
        if (cancelled) return;
        setProjects(rows);
        setLoaded(true);
      })
      .catch((error) => {
        if (cancelled) return;
        setLoaded(true);
        showToast(error instanceof Error ? error.message : '读取项目列表失败', 'error');
      });
    return () => {
      cancelled = true;
    };
  }, [showToast]);

  // 选中项目不在列表里（已删/存储失效）→ 回落首项
  useEffect(() => {
    if (!loaded) return;
    if (projectId && projects.some((project) => project.id === projectId)) return;
    const fallback = projects[0]?.id || '';
    if (fallback !== projectId) setProjectId(fallback);
  }, [loaded, projectId, projects]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      if (projectId) window.localStorage.setItem(PROJECT_STORAGE_KEY, projectId);
      else window.localStorage.removeItem(PROJECT_STORAGE_KEY);
    } catch {
      // 存储不可用（隐私模式等）不致命
    }
  }, [projectId]);

  const handleCreate = useCallback(async () => {
    const name = window.prompt('新项目名称', `项目 ${new Date().toLocaleDateString('zh-CN')}`);
    if (!name || !name.trim()) return;
    setCreating(true);
    try {
      const project = await createProject(name.trim());
      setProjects((prev) => [...prev, project]);
      setProjectId(project.id);
      showToast(`已创建项目「${project.name}」`, 'success');
    } catch (error) {
      showToast(error instanceof Error ? error.message : '创建项目失败', 'error');
    } finally {
      setCreating(false);
    }
  }, [showToast]);

  return (
    <div className="app-shell">
      <header className="app-topbar">
        <strong className="app-brand">易标投标工具箱</strong>
        <nav className="app-nav" aria-label="功能导航">
          <span className="app-nav-item is-active">标书查重</span>
          <span className="app-nav-item is-disabled" title="即将推出">废标项检查</span>
        </nav>
        <div className="app-project-picker">
          <label htmlFor="project-select">项目</label>
          <select
            id="project-select"
            value={projectId}
            onChange={(event) => setProjectId(event.target.value)}
          >
            {projects.length === 0 && <option value="">（暂无项目）</option>}
            {projects.map((project) => (
              <option key={project.id} value={project.id}>{project.name}</option>
            ))}
          </select>
          <button
            type="button"
            className="text-button"
            onClick={() => void handleCreate()}
            disabled={creating}
          >
            新建项目
          </button>
        </div>
      </header>
      <main className="app-main">
        {projectId ? (
          <DuplicatePage key={projectId} projectId={projectId} />
        ) : (
          <div className="app-empty">
            <strong>还没有项目</strong>
            <p>先创建一个项目，再进入「标书查重」上传标书。</p>
            <button
              type="button"
              className="primary-action"
              onClick={() => void handleCreate()}
              disabled={creating}
            >
              新建项目
            </button>
          </div>
        )}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <AppShell />
    </ToastProvider>
  );
}
