'use strict';

const test = require('node:test');
const assert = require('node:assert');
const {
  detectImageFormat,
  resolveImagePlan,
  computeTargetSize,
  NATIVE_DECODABLE_FORMATS,
  PASSTHROUGH_FORMATS,
  TRANSCODE_FORMATS,
} = require('./imageFormat.cjs');

function pngBytes(size = 32) {
  const buffer = Buffer.alloc(size);
  Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]).copy(buffer, 0);
  return buffer;
}

function jpegBytes(size = 32) {
  const buffer = Buffer.alloc(size);
  Buffer.from([0xff, 0xd8, 0xff, 0xe0]).copy(buffer, 0);
  return buffer;
}

function gifBytes(size = 64) {
  const buffer = Buffer.alloc(size);
  buffer.write('GIF89a', 0, 'latin1');
  return buffer;
}

function webpBytes(size = 64) {
  const buffer = Buffer.alloc(size);
  buffer.write('RIFF', 0, 'latin1');
  buffer.write('WEBP', 8, 'latin1');
  return buffer;
}

function bmpBytes(size = 64) {
  const buffer = Buffer.alloc(size);
  buffer.write('BM', 0, 'latin1');
  return buffer;
}

function tiffLeBytes() {
  return Buffer.from([0x49, 0x49, 0x2a, 0x00, 0x08, 0x00, 0x00, 0x00]);
}

function tiffBeBytes() {
  return Buffer.from([0x4d, 0x4d, 0x00, 0x2a, 0x00, 0x00, 0x00, 0x08]);
}

function emfBytes() {
  const buffer = Buffer.alloc(64);
  buffer.writeUInt32LE(1, 0);
  buffer.write(' EMF', 40, 'latin1');
  return buffer;
}

function wmfBytes() {
  return Buffer.from([0xd7, 0xcd, 0xc6, 0x9a, 0x00, 0x00, 0x00, 0x00]);
}

test('detectImageFormat 按魔数识别全部提取器会产出的格式', () => {
  assert.strictEqual(detectImageFormat(pngBytes()).format, 'png');
  assert.strictEqual(detectImageFormat(jpegBytes()).format, 'jpeg');
  assert.strictEqual(detectImageFormat(gifBytes()).format, 'gif');
  assert.strictEqual(detectImageFormat(webpBytes()).format, 'webp');
  assert.strictEqual(detectImageFormat(bmpBytes()).format, 'bmp');
  assert.strictEqual(detectImageFormat(tiffLeBytes()).format, 'tiff');
  assert.strictEqual(detectImageFormat(tiffBeBytes()).format, 'tiff');
  assert.strictEqual(detectImageFormat(emfBytes()).format, 'emf');
  assert.strictEqual(detectImageFormat(wmfBytes()).format, 'wmf');
  assert.strictEqual(detectImageFormat(pngBytes()).label, 'PNG');
  assert.strictEqual(detectImageFormat(gifBytes()).mime, 'image/gif');
});

test('detectImageFormat 对未知内容与空缓冲区返回 unknown 且不抛错', () => {
  assert.strictEqual(detectImageFormat(Buffer.from('not an image at all')).format, 'unknown');
  assert.strictEqual(detectImageFormat(Buffer.from([0x89, 0x50])).format, 'unknown', '截断的 PNG 头不算 PNG');
  assert.strictEqual(detectImageFormat(Buffer.alloc(0)).format, 'unknown');
  assert.strictEqual(detectImageFormat(null).format, 'unknown');
});

test('格式分类集合互不重叠且覆盖第九类', () => {
  assert.deepStrictEqual([...NATIVE_DECODABLE_FORMATS].sort(), ['jpeg', 'png']);
  assert.deepStrictEqual([...PASSTHROUGH_FORMATS], ['gif']);
  assert.deepStrictEqual([...TRANSCODE_FORMATS].sort(), ['bmp', 'webp']);
});

test('resolveImagePlan：PNG/JPEG 走压缩', () => {
  assert.strictEqual(resolveImagePlan(pngBytes(), { maxPassthroughBytes: 1024 }).action, 'compress');
  assert.strictEqual(resolveImagePlan(jpegBytes(), { maxPassthroughBytes: 1024 }).action, 'compress');
});

test('resolveImagePlan：GIF 在上限内透传，含等于上限的边界', () => {
  const limit = 64;
  const plan = resolveImagePlan(gifBytes(limit), { maxPassthroughBytes: limit });
  assert.strictEqual(plan.action, 'passthrough');
  assert.strictEqual(plan.mime, 'image/gif');
  assert.strictEqual(resolveImagePlan(gifBytes(limit - 1), { maxPassthroughBytes: limit }).action, 'passthrough');
});

test('resolveImagePlan：GIF 超上限跳过并给出透传上限原因', () => {
  const plan = resolveImagePlan(gifBytes(128), { maxPassthroughBytes: 64 });
  assert.strictEqual(plan.action, 'skip');
  assert.strictEqual(plan.code, 'PASSTHROUGH_TOO_LARGE');
  assert.ok(plan.reason.includes('透传上限'), `原因应说明透传上限，实际：${plan.reason}`);
});

test('resolveImagePlan：BMP/WebP 走转码', () => {
  assert.strictEqual(resolveImagePlan(bmpBytes(), { maxPassthroughBytes: 1024 }).action, 'transcode');
  assert.strictEqual(resolveImagePlan(webpBytes(), { maxPassthroughBytes: 1024 }).action, 'transcode');
});

test('resolveImagePlan：TIFF/EMF/WMF/未知 跳过且原因含具体格式名', () => {
  const cases = [
    [tiffLeBytes(), 'TIFF'],
    [emfBytes(), 'EMF'],
    [wmfBytes(), 'WMF'],
    [Buffer.from('whatever'), '未知格式'],
  ];
  for (const [buffer, label] of cases) {
    const plan = resolveImagePlan(buffer, { maxPassthroughBytes: 1024 });
    assert.strictEqual(plan.action, 'skip', `${label} 应跳过`);
    assert.strictEqual(plan.code, 'UNSUPPORTED_IMAGE_FORMAT');
    assert.ok(plan.reason.includes(label), `原因应含 ${label}，实际：${plan.reason}`);
  }
});

test('computeTargetSize 不放大且按长边等比缩放', () => {
  assert.deepStrictEqual(computeTargetSize(100, 50, 2048), { width: 100, height: 50, scale: 1 });
  const landscape = computeTargetSize(4000, 2000, 2048);
  assert.strictEqual(landscape.width, 2048);
  assert.strictEqual(landscape.height, 1024);
  const portrait = computeTargetSize(2000, 4000, 2048);
  assert.strictEqual(portrait.width, 1024);
  assert.strictEqual(portrait.height, 2048);
  const square = computeTargetSize(4096, 4096, 2048);
  assert.strictEqual(square.width, 2048);
  assert.strictEqual(square.height, 2048);
});
