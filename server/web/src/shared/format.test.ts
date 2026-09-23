import { describe, expect, it } from 'vitest';
import {
  buildFileLabelMap,
  fileIndexLabel,
  formatDuplicateSentenceText,
  formatFileSize,
  formatImageLocationSentence,
  progressText,
  statusLabel,
} from './format';

describe('fileIndexLabel（bijective base-26，逐字移植）', () => {
  it('单字母段 A..Z', () => {
    expect(fileIndexLabel(0)).toBe('A');
    expect(fileIndexLabel(1)).toBe('B');
    expect(fileIndexLabel(25)).toBe('Z');
  });

  it('双字母段 AA..ZZ', () => {
    expect(fileIndexLabel(26)).toBe('AA');
    expect(fileIndexLabel(27)).toBe('AB');
    expect(fileIndexLabel(51)).toBe('AZ');
    expect(fileIndexLabel(52)).toBe('BA');
  });

  it('前缀回退不产生空串', () => {
    // 算法对 0 也产出 'A'（do-while 先执行）
    expect(fileIndexLabel(0).length).toBeGreaterThan(0);
  });
});

describe('formatFileSize', () => {
  it('B/KB/MB 三档', () => {
    expect(formatFileSize(512)).toBe('512 B');
    expect(formatFileSize(2048)).toBe('2.0 KB');
    expect(formatFileSize(3 * 1024 * 1024)).toBe('3.0 MB');
  });
});

describe('statusLabel', () => {
  it('四分支', () => {
    expect(statusLabel('running')).toBe('分析中');
    expect(statusLabel('success')).toBe('已完成');
    expect(statusLabel('error')).toBe('有错误');
    expect(statusLabel('pending')).toBe('待分析');
  });
});

describe('progressText', () => {
  it('缺省/零 total/正常', () => {
    expect(progressText(undefined)).toBe('0/0');
    expect(progressText({ completed: 3, total: 0 })).toBe('0/0');
    expect(progressText({ completed: 2, total: 5 })).toBe('2/5');
  });
});

describe('formatDuplicateSentenceText', () => {
  it('短文本透传', () => {
    expect(formatDuplicateSentenceText('短句', '原文')).toBe('短句');
    expect(formatDuplicateSentenceText('', '原文')).toBe('原文');
  });

  it('600 字截断边界', () => {
    const exact = 'a'.repeat(600);
    expect(formatDuplicateSentenceText(exact, 'x')).toBe(exact);
    const over = 'b'.repeat(601);
    expect(formatDuplicateSentenceText(over, 'x'))
      .toBe(`${'b'.repeat(600)}...`);
  });
});

describe('formatImageLocationSentence', () => {
  it('72 字截断边界', () => {
    expect(formatImageLocationSentence('c'.repeat(72))).toBe('c'.repeat(72));
    expect(formatImageLocationSentence('d'.repeat(73)))
      .toBe(`${'d'.repeat(72)}...`);
  });
});

describe('buildFileLabelMap', () => {
  it('按数组序编号', () => {
    const map = buildFileLabelMap([
      { id: 'f1' },
      { id: 'f2' },
      { id: 'f3' },
    ]);
    expect(map.get('f1')).toBe('A');
    expect(map.get('f2')).toBe('B');
    expect(map.get('f3')).toBe('C');
  });
});
