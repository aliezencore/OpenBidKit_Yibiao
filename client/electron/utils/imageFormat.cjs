'use strict';

// 按魔数识别图片格式。EMF/WMF 是矢量格式，Chromium 与 nativeImage 都无法解码，
// 但仍要识别出来，好让跳过原因里带上具体格式名而不是含糊的「未知格式」。
const FORMAT_SIGNATURES = [
  {
    format: 'png', label: 'PNG', mime: 'image/png',
    match: (b) => b.length >= 8 && b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47,
  },
  {
    format: 'jpeg', label: 'JPEG', mime: 'image/jpeg',
    match: (b) => b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff,
  },
  {
    format: 'gif', label: 'GIF', mime: 'image/gif',
    match: (b) => b.length >= 6 && b.toString('latin1', 0, 4) === 'GIF8',
  },
  {
    format: 'webp', label: 'WebP', mime: 'image/webp',
    match: (b) => b.length >= 12 && b.toString('latin1', 0, 4) === 'RIFF' && b.toString('latin1', 8, 12) === 'WEBP',
  },
  {
    format: 'bmp', label: 'BMP', mime: 'image/bmp',
    match: (b) => b.length >= 2 && b[0] === 0x42 && b[1] === 0x4d,
  },
  {
    format: 'tiff', label: 'TIFF', mime: 'image/tiff',
    match: (b) => b.length >= 4
      && ((b[0] === 0x49 && b[1] === 0x49 && b[2] === 0x2a) || (b[0] === 0x4d && b[1] === 0x4d && b[2] === 0x00 && b[3] === 0x2a)),
  },
  {
    format: 'emf', label: 'EMF', mime: 'image/emf',
    match: (b) => b.length >= 44 && b.toString('latin1', 40, 44) === ' EMF',
  },
  {
    format: 'wmf', label: 'WMF', mime: 'image/wmf',
    match: (b) => b.length >= 4 && b[0] === 0xd7 && b[1] === 0xcd && b[2] === 0xc6 && b[3] === 0x9a,
  },
];

const UNKNOWN_FORMAT = { format: 'unknown', label: '未知格式', mime: '' };

// Electron nativeImage.createFromBuffer 只稳定支持 PNG/JPEG（其余实测均解码失败）。
const NATIVE_DECODABLE_FORMATS = new Set(['png', 'jpeg']);
// 解码器不支持、但可原字节直接交给模型的格式（实测模型能识别静态 GIF）。
const PASSTHROUGH_FORMATS = new Set(['gif']);
// 解码器不支持、但 Chromium 能解，可经离屏窗口转码的格式。
const TRANSCODE_FORMATS = new Set(['bmp', 'webp']);

function toBuffer(value) {
  return Buffer.isBuffer(value) ? value : Buffer.alloc(0);
}

function detectImageFormat(buffer) {
  const bytes = toBuffer(buffer);
  for (const signature of FORMAT_SIGNATURES) {
    if (signature.match(bytes)) {
      return { format: signature.format, label: signature.label, mime: signature.mime };
    }
  }
  return { ...UNKNOWN_FORMAT };
}

// 单一决策入口：把「压缩 / 透传 / 转码 / 跳过」的策略集中在一处，便于单测与复核。
function resolveImagePlan(buffer, options = {}) {
  const bytes = toBuffer(buffer);
  const detected = detectImageFormat(bytes);
  const limit = Number(options.maxPassthroughBytes);
  const maxBytes = Number.isFinite(limit) && limit > 0 ? limit : Number.POSITIVE_INFINITY;

  if (NATIVE_DECODABLE_FORMATS.has(detected.format)) {
    return { action: 'compress', ...detected };
  }
  if (PASSTHROUGH_FORMATS.has(detected.format)) {
    if (bytes.length > maxBytes) {
      return {
        action: 'skip',
        code: 'PASSTHROUGH_TOO_LARGE',
        reason: 'GIF 体积超出透传上限',
        ...detected,
      };
    }
    return { action: 'passthrough', ...detected };
  }
  if (TRANSCODE_FORMATS.has(detected.format)) {
    return { action: 'transcode', ...detected };
  }
  return {
    action: 'skip',
    code: 'UNSUPPORTED_IMAGE_FORMAT',
    reason: `图片格式不支持（${detected.label}）`,
    ...detected,
  };
}

// 等比缩放到最长边不超过 maxEdge；不放大。
function computeTargetSize(width, height, maxEdge) {
  const safeWidth = Math.max(1, Math.floor(Number(width) || 0));
  const safeHeight = Math.max(1, Math.floor(Number(height) || 0));
  const edge = Math.floor(Number(maxEdge) || 0);
  const longest = Math.max(safeWidth, safeHeight);
  if (!edge || edge <= 0 || longest <= edge) {
    return { width: safeWidth, height: safeHeight, scale: 1 };
  }
  const scale = edge / longest;
  return {
    width: Math.max(1, Math.round(safeWidth * scale)),
    height: Math.max(1, Math.round(safeHeight * scale)),
    scale,
  };
}

module.exports = {
  detectImageFormat,
  resolveImagePlan,
  computeTargetSize,
  NATIVE_DECODABLE_FORMATS,
  PASSTHROUGH_FORMATS,
  TRANSCODE_FORMATS,
};
