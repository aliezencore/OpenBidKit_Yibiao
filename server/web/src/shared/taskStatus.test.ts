import { describe, expect, it } from 'vitest';
import { TERMINAL_TASK_STATUSES, terminalStatusOf } from './taskStatus';

describe('任务终态判定', () => {
  it('覆盖服务端 engine 的四个终态取值', () => {
    expect([...TERMINAL_TASK_STATUSES].sort())
      .toEqual(['cancelled', 'error', 'interrupted', 'success']);
  });

  it('失败态是 error（不是 failed）', () => {
    expect(terminalStatusOf('error')).toBe('error');
    expect(terminalStatusOf('failed')).toBeNull();
  });

  it('非终态/未知值返回 null', () => {
    expect(terminalStatusOf('queued')).toBeNull();
    expect(terminalStatusOf('running')).toBeNull();
    expect(terminalStatusOf('')).toBeNull();
    expect(terminalStatusOf(null)).toBeNull();
    expect(terminalStatusOf(undefined)).toBeNull();
  });
});
