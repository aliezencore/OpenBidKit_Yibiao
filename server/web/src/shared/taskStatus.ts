// 任务终态判定（P3d-3 评审 B 的 P1 教训，抽纯函数 + 单测防回归）。
//
// 服务端 `engine.py` 实际发布的 status 取值只有六种：
//   queued(:58) / running(:87) / cancelled(:93) / error(:98) / success(:103)
//   （另有进程重启恢复时的 interrupted，engine.py:144）
// **失败态是 `error`，不是 `failed`**——按 `failed` 判终态会让失败任务
// 永不被识别，UI 停在 running、按钮永久禁用。
import type { TaskStatus } from '../api/types';

export const TERMINAL_TASK_STATUSES: readonly TaskStatus[] = [
  'success', 'error', 'cancelled', 'interrupted',
];

/** 命中终态返回该终态，否则 null（running/queued/未知值）。 */
export function terminalStatusOf(status: string | null | undefined): TaskStatus | null {
  if (!status) return null;
  return TERMINAL_TASK_STATUSES.find((item) => item === status) ?? null;
}
