// 自动启动查重分析的纯守卫（R-P3d3-2，对齐参考 :787-796 的触发条件）。
// 抽成纯函数以便单测 + 判别力探针。

export interface AutoStartInput {
  step: 'upload' | 'analysis';
  bidCount: number;
  /** 服务端状态是否已加载完成 */
  hydrated: boolean;
  /** 启动请求在途 */
  starting: boolean;
  /** 已有活跃分析任务（含刷新后找回的） */
  hasActiveTask: boolean;
  /** 任一段 running */
  sectionRunning: boolean;
  /** 四段全部 success */
  allSuccess: boolean;
  /** 本轮（自上次上传/重置/强制重跑以来）已尝试过 */
  attempted: boolean;
}

export function shouldAutoStart(input: AutoStartInput): boolean {
  if (input.step !== 'analysis') return false;
  if (!input.hydrated) return false;
  if (input.bidCount === 0) return false;
  if (input.starting || input.hasActiveTask || input.sectionRunning) return false;
  if (input.allSuccess) return false;
  return !input.attempted;
}
