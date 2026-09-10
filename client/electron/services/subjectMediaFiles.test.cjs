'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {
  getSubjectMediaDir,
  writeVisualMediaFiles,
  removeVisualMediaDir,
  resolveVisualAssetAbsolutePath,
  buildAssetPreviewUrl,
  ASSET_PROTOCOL_HOSTNAME,
} = require('./subjectMediaFiles.cjs');

function makeTempRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'subject-media-test-'));
}

test('writeVisualMediaFiles 相同内容哈希只写一份，mediaName 映射各自路径', () => {
  const root = makeTempRoot();
  const mediaDir = getSubjectMediaDir(root, 'bid-abc');
  const buffer = Buffer.from('fake-image-bytes');
  const paths = writeVisualMediaFiles({
    mediaDir,
    mediaFiles: [
      { mediaName: 'media/a.png', buffer, contentHash: 'hash-1', mimeType: 'image/png' },
      { mediaName: 'media/b.png', buffer, contentHash: 'hash-1', mimeType: 'image/png' },
      { mediaName: 'media/c.jpg', buffer: Buffer.from('other'), contentHash: 'hash-2', mimeType: 'image/jpeg' },
    ],
  });
  assert.strictEqual(paths.get('media/a.png'), path.posix.join('hash-1', 'hash-1.png'));
  assert.strictEqual(paths.get('media/b.png'), path.posix.join('hash-1', 'hash-1.png'));
  assert.strictEqual(paths.get('media/c.jpg'), path.posix.join('hash-2', 'hash-2.jpg'));
  assert.strictEqual(fs.readdirSync(path.join(mediaDir, 'hash-1')).length, 1);
  assert.ok(fs.existsSync(path.join(mediaDir, 'hash-2', 'hash-2.jpg')));
});

test('resolveVisualAssetAbsolutePath 阻止越界路径', () => {
  const root = makeTempRoot();
  const resolved = resolveVisualAssetAbsolutePath({
    mediaRootDir: path.join(root, 'subject-media'),
    bidDocumentId: 'bid-abc',
    relativePath: 'hash-1/hash-1.png',
  });
  assert.strictEqual(resolved, path.join(root, 'subject-media', 'bid-abc', 'hash-1', 'hash-1.png'));
  assert.throws(() => resolveVisualAssetAbsolutePath({
    mediaRootDir: path.join(root, 'subject-media'),
    bidDocumentId: 'bid-abc',
    relativePath: '../../user_config.json',
  }));
});

test('removeVisualMediaDir 幂等删除', async () => {
  const root = makeTempRoot();
  const mediaDir = getSubjectMediaDir(root, 'bid-abc');
  writeVisualMediaFiles({ mediaDir, mediaFiles: [{ mediaName: 'm.png', buffer: Buffer.from('x'), contentHash: 'h', mimeType: 'image/png' }] });
  await removeVisualMediaDir(mediaDir);
  assert.strictEqual(fs.existsSync(mediaDir), false);
  await removeVisualMediaDir(mediaDir);
});

test('buildAssetPreviewUrl 使用 rejection-check-media 主机名', () => {
  assert.strictEqual(ASSET_PROTOCOL_HOSTNAME, 'rejection-check-media');
  const url = buildAssetPreviewUrl({ bidDocumentId: 'bid-abc', relativePath: 'hash-1/hash-1.png' });
  assert.strictEqual(url, 'yibiao-asset://rejection-check-media/bid-abc/hash-1/hash-1.png');
  const encoded = buildAssetPreviewUrl({ bidDocumentId: 'bid 中文', relativePath: 'h/a.png' });
  assert.ok(encoded.startsWith(`yibiao-asset://${ASSET_PROTOCOL_HOSTNAME}/bid%20`));
});