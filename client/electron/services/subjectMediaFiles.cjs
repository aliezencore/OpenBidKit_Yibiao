'use strict';

const fs = require('node:fs');
const path = require('node:path');

const ASSET_PROTOCOL_HOSTNAME = 'rejection-check-media';

function sanitizeDocumentId(documentId) {
  return String(documentId || '').replace(/[^a-zA-Z0-9_-]+/g, '_') || 'unknown';
}

function extensionForMimeType(mimeType) {
  const map = {
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/gif': 'gif',
    'image/bmp': 'bmp',
    'image/tiff': 'tif',
    'image/webp': 'webp',
    'image/emf': 'emf',
    'image/wmf': 'wmf',
  };
  return map[mimeType] || 'bin';
}

function getSubjectMediaDir(rejectionCheckDir, bidDocumentId) {
  return path.join(String(rejectionCheckDir || ''), 'subject-media', sanitizeDocumentId(bidDocumentId));
}

function writeVisualMediaFiles({ mediaDir, mediaFiles = [] }) {
  const written = new Map();
  for (const media of mediaFiles) {
    if (!media?.mediaName || !Buffer.isBuffer(media.buffer)) continue;
    const hash = String(media.contentHash || '').trim() || 'unknown';
    const extension = extensionForMimeType(media.mimeType);
    const relativePath = path.posix.join(hash, `${hash}.${extension}`);
    const targetPath = path.join(mediaDir, relativePath);
    if (!written.has(media.mediaName)) {
      fs.mkdirSync(path.dirname(targetPath), { recursive: true });
      if (!fs.existsSync(targetPath)) fs.writeFileSync(targetPath, media.buffer);
    }
    written.set(media.mediaName, relativePath);
  }
  return written;
}

async function removeVisualMediaDir(mediaDir) {
  await fs.promises.rm(String(mediaDir || ''), { recursive: true, force: true });
}

function resolveVisualAssetAbsolutePath({ mediaRootDir, bidDocumentId, relativePath }) {
  const baseDir = path.resolve(String(mediaRootDir || ''), sanitizeDocumentId(bidDocumentId));
  const filePath = path.resolve(baseDir, String(relativePath || ''));
  if (filePath !== baseDir && !filePath.startsWith(`${baseDir}${path.sep}`)) {
    throw new Error('图片路径超出工作区范围');
  }
  return filePath;
}

function buildAssetPreviewUrl({ bidDocumentId, relativePath }) {
  const documentPart = encodeURIComponent(String(bidDocumentId || ''));
  const pathPart = String(relativePath || '').split('/').map(encodeURIComponent).join('/');
  return `yibiao-asset://${ASSET_PROTOCOL_HOSTNAME}/${documentPart}/${pathPart}`;
}

module.exports = {
  ASSET_PROTOCOL_HOSTNAME,
  getSubjectMediaDir,
  writeVisualMediaFiles,
  removeVisualMediaDir,
  resolveVisualAssetAbsolutePath,
  buildAssetPreviewUrl,
};