'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { logAiFailure } = require('./aiLog.cjs');

function captureError(run) {
  const original = console.error;
  const lines = [];
  console.error = (...args) => { lines.push(args.join(' ')); };
  try {
    run();
  } finally {
    console.error = original;
  }
  return lines;
}

test('logAiFailure 输出单行 key=value 并带 ai-failure 前缀', () => {
  const lines = captureError(() => logAiFailure('subject.seal.content', {
    phase: 'content',
    batch: '1/1',
    assets: 3,
    code: 'UNSUPPORTED_IMAGE_FORMAT',
    message: '图片格式不支持（TIFF）',
  }));
  assert.strictEqual(lines.length, 1);
  assert.ok(lines[0].includes('ai-failure'), lines[0]);
  assert.ok(lines[0].includes('scope=subject.seal.content'));
  assert.ok(lines[0].includes('code=UNSUPPORTED_IMAGE_FORMAT'));
  assert.ok(lines[0].includes('assets=3'));
  assert.strictEqual(lines[0].includes('\n'), false);
});

test('logAiFailure 跳过空值并对含空格的值加引号', () => {
  const lines = captureError(() => logAiFailure('scope.a', {
    empty: '',
    missing: undefined,
    nil: null,
    providerMessage: 'model does not support image',
  }));
  assert.strictEqual(lines[0].includes('empty='), false);
  assert.ok(lines[0].includes('providerMessage="model does not support image"'), lines[0]);
});

test('logAiFailure 截断超长值', () => {
  const lines = captureError(() => logAiFailure('scope.b', { body: 'x'.repeat(2000) }));
  assert.ok(lines[0].length < 1000, `单行应被截断，实际长度 ${lines[0].length}`);
});

test('logAiFailure 遇到循环引用也不抛错', () => {
  const circular = {};
  circular.self = circular;
  const lines = captureError(() => logAiFailure('scope.c', { circular }));
  assert.strictEqual(lines.length, 1);
});
