import { describe, expect, it } from 'vitest';
import { shouldAutoStart } from './autoStart';

const base = {
  step: 'analysis' as const,
  bidCount: 2,
  hydrated: true,
  starting: false,
  hasActiveTask: false,
  sectionRunning: false,
  allSuccess: false,
  attempted: false,
};

describe('shouldAutoStart（R-P3d3-2 守卫）', () => {
  it('全部条件满足 → 启动', () => {
    expect(shouldAutoStart(base)).toBe(true);
  });

  it('非分析步不启动', () => {
    expect(shouldAutoStart({ ...base, step: 'upload' })).toBe(false);
  });

  it('状态未加载不启动', () => {
    expect(shouldAutoStart({ ...base, hydrated: false })).toBe(false);
  });

  it('没有投标文件不启动', () => {
    expect(shouldAutoStart({ ...base, bidCount: 0 })).toBe(false);
  });

  it('启动在途 / 已有任务 / 段运行中 任一即不启动', () => {
    expect(shouldAutoStart({ ...base, starting: true })).toBe(false);
    expect(shouldAutoStart({ ...base, hasActiveTask: true })).toBe(false);
    expect(shouldAutoStart({ ...base, sectionRunning: true })).toBe(false);
  });

  it('四段全 success 不启动', () => {
    expect(shouldAutoStart({ ...base, allSuccess: true })).toBe(false);
  });

  it('本轮已尝试过不启动（防失败循环）', () => {
    expect(shouldAutoStart({ ...base, attempted: true })).toBe(false);
  });
});
