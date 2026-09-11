# 印章检查进度上报、格式容错与失败诊断 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让印章检查不再因解码器限制丢失可用图片、能按去重数量上报两阶段进度，并把图片准备失败与 VLM 失败的真实原因与错误码落进后台日志与 Excel。

**Architecture:** 新增两个纯/半纯模块——`imageFormat.cjs`（格式识别与「压缩/透传/转码/跳过」决策、缩放计算）与 `offscreenImageTranscoder.cjs`（复用离屏 Chromium 解码 BMP/WebP 并直出 JPEG data URL）。`aiService` 按决策三分派；`subjectVisionProvider` 增加预检跳过、批次降级隔离、进度回调与失败上报；`subjectCheckTask` 把事件翻译成中文文案与「未能识别清单」，`rejectionCheckTask` 落进 `stats.visionStats`。另修 `docxVisualAssetExtractor` 的章节/附近文本提取，供 Excel 明细使用。

**Tech Stack:** Electron 41（Main 为 CommonJS `.cjs`）、Node 内置 `node:test`、React + TypeScript（Renderer，**本次不改**）、`xlsx`（Excel 导出）、离屏 `BrowserWindow` + canvas 转码。

**Spec:** `docs/superpowers/specs/2026-09-11-seal-check-progress-and-diagnostics-design.md`

## Global Constraints

- **前端零改动**：不改 `client/src/` 下任何文件。前端只读 `visionStats` 的数字字段，新增的 `unrecognized` 是附加键。
- **不引入图像解码依赖**：不安装 `sharp`、`jimp` 等；离屏 Chromium 已提供原生解码器。
- **不重构** `client/electron/services/localImageRenderService.cjs`（只复用其窗口配置这一事实，新模块内联同一份配置并在注释中指向它）。
- **零数据库迁移**：不新增列、不改 `sql/workspace_schema.sql`。`stats_json` 是 JSON blob，可扩键。
- **不接 Pi Agent 执行监视器**（废标/主体检查不经过 Pi，见规格 §8）。
- 用户可见文案一律中文。
- `MULTIMODAL_PASSTHROUGH_MAX_BYTES = 2 * 1024 * 1024`；`maxEdge` 复用既有 `MULTIMODAL_IMAGE_MAX_EDGE`（2048）；JPEG 质量复用 `MULTIMODAL_IMAGE_JPEG_QUALITY`（85）。
- **进度口径**：阶段一按**去重后可读图片数**（被跳过的不进分母）；阶段二按**盖章位置组数**（不去重）。
- 改完 `.cjs` 必须 `node --check <file>`。
- 纯逻辑测试用普通 `node --test <file>`；**任何触达 `better-sqlite3` 的测试**必须用
  `cd client && ELECTRON_RUN_AS_NODE=1 ./node_modules/electron/dist/electron.exe --test <file>`。
- 需要真实 Electron 主进程的验证必须清掉常驻环境变量：
  `cd client && env -u ELECTRON_RUN_AS_NODE ./node_modules/electron/dist/electron.exe <script.cjs>`
- `npm run build` 若被本机 safe-delete 守卫拦截，对命令临时加 `CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=100000`，**不要改仓库配置**；既有 chunk 体积警告不算失败。
- **既有失败测试** `electron/services/knowledgeBaseStore.search.test.cjs` 与本改动无关，**不要修**。
- 直接在 `main` 上按任务提交，**不建分支、不推送**。提交身份固定：
  `git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "..."`。

## File Structure

| 文件 | 职责 |
|---|---|
| `client/electron/utils/imageFormat.cjs`（新增） | 魔数识别、`resolveImagePlan` 决策、`computeTargetSize` 缩放。纯函数，无 Electron 依赖 |
| `client/electron/utils/aiLog.cjs`（修改） | 新增 `logAiFailure`：始终打印的单行结构化失败日志 |
| `client/electron/services/offscreenImageTranscoder.cjs`（新增） | 离屏窗口解码 BMP/WebP → canvas 缩放/编码 → JPEG data URL；懒创建 + 复用 + 空闲销毁 |
| `client/electron/services/aiService.cjs`（修改） | `compressLocalImageToDataUrl` 三分派、`inspectLocalImage`、准备阶段失败日志、保留真实错误 |
| `client/electron/services/subjectVisionProvider.cjs`（修改） | 预检跳过、批次降级隔离、进度/日志回调、失败上报 |
| `client/electron/services/subjectCheckTask.cjs`（修改） | `runSubjectSealCheck` 接收 logger、翻译进度文案、状态判定、汇总未能识别清单 |
| `client/electron/services/rejectionCheckTask.cjs`（修改） | 把 `developerLogger` 与 `visualAssets` 信息接进印章检查，落 `stats.visionStats.unrecognized` |
| `client/electron/services/docxVisualAssetExtractor.cjs`（修改） | 段落索引；`extractSectionTitle` 取章节、`extractNearbyText` 取可读段落文本 |
| `client/electron/services/checkResultExportService.cjs`（修改） | 「投标主体」表追加「未能识别的图片」区块 |
| 对应 5 个 `.test.cjs` | 见各任务 |

---

### Task 1: 图片格式识别与决策纯函数

**Files:**
- Create: `client/electron/utils/imageFormat.cjs`
- Test: `client/electron/utils/imageFormat.test.cjs`

**Interfaces:**
- Consumes: 无（本任务是第一个任务）
- Produces:
  - `detectImageFormat(buffer) → { format, label, mime }`
  - `resolveImagePlan(buffer, { maxPassthroughBytes }) → { action, ... }`
    - `{ action: 'compress', format, label, mime }`
    - `{ action: 'passthrough', format, label, mime }`
    - `{ action: 'transcode', format, label, mime }`
    - `{ action: 'skip', code, reason, format, label, mime }`
  - `computeTargetSize(width, height, maxEdge) → { width, height, scale }`
  - 常量 `NATIVE_DECODABLE_FORMATS`、`PASSTHROUGH_FORMATS`、`TRANSCODE_FORMATS`

- [ ] **Step 1: 写失败的测试**

创建 `client/electron/utils/imageFormat.test.cjs`：

```js
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd client && node --test electron/utils/imageFormat.test.cjs`
Expected: FAIL —— `Cannot find module './imageFormat.cjs'`

- [ ] **Step 3: 写最小实现**

创建 `client/electron/utils/imageFormat.cjs`：

```js
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd client && node --test electron/utils/imageFormat.test.cjs`
Expected: PASS（9 个用例全绿，无告警）

- [ ] **Step 5: 提交**

```bash
cd C:/workspace/OpenBidKit_Yibiao
git add client/electron/utils/imageFormat.cjs client/electron/utils/imageFormat.test.cjs
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat: 新增图片格式识别与压缩/透传/转码决策纯函数"
```

---

### Task 2: 离屏转码模块

**Files:**
- Create: `client/electron/services/offscreenImageTranscoder.cjs`

**Interfaces:**
- Consumes: `computeTargetSize`（Task 1）
- Produces:
  - `createOffscreenImageTranscoder({ idleDisposeMs }) → { convertToJpegDataUrl(filePath, { maxEdge, quality }), dispose() }`
  - `getSharedTranscoder() → 上者单例`
  - 错误带 `error.code`：`IMAGE_TRANSCODE_FAILED` / `IMAGE_TOO_LARGE_TO_TRANSCODE`

**说明（为什么这么设计）：**
- 离屏窗口创建实测 110~340ms，转码本身仅 ~2.9ms/张（差 37.7 倍）→ **必须懒创建 + 复用**。
- 缩放规则必须与主进程一致，因此把 `computeTargetSize` 的**函数源码注入渲染层脚本**，避免两处实现漂移。

- [ ] **Step 1: 写模块**

创建 `client/electron/services/offscreenImageTranscoder.cjs`：

```js
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { BrowserWindow, app: electronApp } = require('electron');
const { computeTargetSize } = require('../utils/imageFormat.cjs');

const DEFAULT_IDLE_DISPOSE_MS = 30000;
// 转码要把原始字节以 base64 送进渲染层，用上限兜底避免超大文件撑爆内存。
// 超过上限按「转码失败」处理（该张单独失败，不影响同批其它图片）。
const MAX_TRANSCODE_INPUT_BYTES = 16 * 1024 * 1024;

function createOffscreenImageTranscoder({ idleDisposeMs = DEFAULT_IDLE_DISPOSE_MS } = {}) {
  let window = null;
  let idleTimer = null;
  let queue = Promise.resolve();
  // 把缩放规则注入渲染层，保证与主进程用的是同一份实现。
  const targetSizeSource = computeTargetSize.toString();

  function clearIdleTimer() {
    if (idleTimer) {
      clearTimeout(idleTimer);
      idleTimer = null;
    }
  }

  function scheduleIdleDispose() {
    clearIdleTimer();
    if (!Number.isFinite(idleDisposeMs) || idleDisposeMs <= 0) return;
    idleTimer = setTimeout(() => { void dispose(); }, idleDisposeMs);
    if (typeof idleTimer.unref === 'function') idleTimer.unref();
  }

  // 窗口配置与 localImageRenderService.cjs 的 createRenderWindow 保持一致。
  async function ensureWindow() {
    if (window && !window.isDestroyed() && window.webContents && !window.webContents.isDestroyed()) {
      return window;
    }
    window = new BrowserWindow({
      width: 32,
      height: 32,
      show: false,
      frame: false,
      transparent: false,
      backgroundColor: '#ffffff',
      webPreferences: {
        offscreen: true,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        webSecurity: false,
        backgroundThrottling: false,
      },
    });
    window.setMenuBarVisibility(false);
    await window.loadURL('data:text/html,<html><body style="margin:0"></body></html>');
    return window;
  }

  async function dispose() {
    clearIdleTimer();
    const target = window;
    window = null;
    if (target && !target.isDestroyed()) {
      try {
        target.destroy();
      } catch {
        // 销毁失败不影响主流程。
      }
    }
  }

  async function convert(filePath, { maxEdge, quality, mime }) {
    const buffer = await fs.promises.readFile(filePath);
    if (buffer.length > MAX_TRANSCODE_INPUT_BYTES) {
      const error = new Error(`图片读取或压缩失败（${path.basename(filePath)}）：图片体积过大，无法转码`);
      error.code = 'IMAGE_TOO_LARGE_TO_TRANSCODE';
      error.filePath = filePath;
      throw error;
    }

    const win = await ensureWindow();
    const base64 = buffer.toString('base64');
    const script = `(async () => {
      const computeTargetSize = ${targetSizeSource};
      const binary = atob(${JSON.stringify(base64)});
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
      const blobUrl = URL.createObjectURL(new Blob([bytes], { type: ${JSON.stringify(mime || 'application/octet-stream')} }));
      try {
        const image = new Image();
        image.src = blobUrl;
        await image.decode();
        const target = computeTargetSize(image.naturalWidth, image.naturalHeight, ${JSON.stringify(Number(maxEdge) || 0)});
        const canvas = document.createElement('canvas');
        canvas.width = target.width;
        canvas.height = target.height;
        canvas.getContext('2d').drawImage(image, 0, 0, target.width, target.height);
        return canvas.toDataURL('image/jpeg', ${JSON.stringify(Number(quality) || 0.85)});
      } finally {
        URL.revokeObjectURL(blobUrl);
      }
    })()`;

    let dataUrl = '';
    try {
      dataUrl = await win.webContents.executeJavaScript(script, true);
    } catch (error) {
      if (win.isDestroyed()) window = null;
      const wrapped = new Error(`图片读取或压缩失败（${path.basename(filePath)}）：图片转码失败`);
      wrapped.code = 'IMAGE_TRANSCODE_FAILED';
      wrapped.filePath = filePath;
      wrapped.cause = error;
      throw wrapped;
    }
    if (typeof dataUrl !== 'string' || !dataUrl.startsWith('data:image/jpeg')) {
      const wrapped = new Error(`图片读取或压缩失败（${path.basename(filePath)}）：图片转码未返回有效图像`);
      wrapped.code = 'IMAGE_TRANSCODE_FAILED';
      wrapped.filePath = filePath;
      throw wrapped;
    }
    return dataUrl;
  }

  function convertToJpegDataUrl(filePath, options = {}) {
    // 离屏窗口是单线程的，串行化避免并发争用同一个窗口。
    const task = queue.catch(() => undefined).then(async () => {
      try {
        return await convert(filePath, options);
      } finally {
        scheduleIdleDispose();
      }
    });
    queue = task;
    return task;
  }

  return { convertToJpegDataUrl, dispose };
}

let sharedTranscoder = null;

function getSharedTranscoder() {
  if (!sharedTranscoder) {
    sharedTranscoder = createOffscreenImageTranscoder();
    if (electronApp && typeof electronApp.once === 'function') {
      electronApp.once('will-quit', () => {
        const target = sharedTranscoder;
        sharedTranscoder = null;
        void target?.dispose();
      });
    }
  }
  return sharedTranscoder;
}

module.exports = {
  createOffscreenImageTranscoder,
  getSharedTranscoder,
  MAX_TRANSCODE_INPUT_BYTES,
};
```

- [ ] **Step 2: 语法检查**

Run: `cd client && node --check electron/services/offscreenImageTranscoder.cjs`
Expected: 无输出（通过）

- [ ] **Step 3: 用一次性的 Electron 脚本验证转码真的可用**

创建 `client/.tmp-transcoder-check.cjs`：

```js
// 一次性验证脚本：跑完即删，不进入版本库。
const { app, BrowserWindow } = require('electron');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { getSharedTranscoder } = require('./electron/services/offscreenImageTranscoder.cjs');

function makeBmp(width, height) {
  const rowSize = Math.floor((24 * width + 31) / 32) * 4;
  const pixelBytes = rowSize * height;
  const buffer = Buffer.alloc(54 + pixelBytes);
  buffer.write('BM', 0, 'latin1');
  buffer.writeUInt32LE(54 + pixelBytes, 2);
  buffer.writeUInt32LE(54, 10);
  buffer.writeUInt32LE(40, 14);
  buffer.writeInt32LE(width, 18);
  buffer.writeInt32LE(height, 22);
  buffer.writeUInt16LE(1, 26);
  buffer.writeUInt16LE(24, 28);
  buffer.writeUInt32LE(pixelBytes, 34);
  for (let i = 0; i < pixelBytes; i += 1) buffer[54 + i] = (i * 7) % 251;
  return buffer;
}

async function mintWebp() {
  const win = new BrowserWindow({
    width: 32, height: 32, show: false, frame: false,
    webPreferences: { offscreen: true, contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  await win.loadURL('data:text/html,<html><body style="margin:0"></body></html>');
  const dataUrl = await win.webContents.executeJavaScript(`(() => {
    const c = document.createElement('canvas'); c.width = 320; c.height = 200;
    const g = c.getContext('2d');
    g.fillStyle = '#c00'; g.fillRect(0, 0, 320, 200);
    g.fillStyle = '#00c'; g.fillRect(40, 40, 120, 80);
    return c.toDataURL('image/webp');
  })()`, true);
  win.destroy();
  return Buffer.from(dataUrl.split(',')[1], 'base64');
}

app.whenReady().then(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'yibiao-transcode-'));
  const bmpPath = path.join(dir, 'seal.bmp');
  const webpPath = path.join(dir, 'logo.webp');
  fs.writeFileSync(bmpPath, makeBmp(300, 180));
  fs.writeFileSync(webpPath, await mintWebp());

  const transcoder = getSharedTranscoder();
  let failed = 0;
  for (const filePath of [bmpPath, webpPath]) {
    const started = Date.now();
    const dataUrl = await transcoder.convertToJpegDataUrl(filePath, { maxEdge: 2048, quality: 85 });
    const bytes = Buffer.from(dataUrl.split(',')[1], 'base64');
    const isJpeg = bytes[0] === 0xff && bytes[1] === 0xd8;
    const ok = dataUrl.startsWith('data:image/jpeg;base64,') && isJpeg;
    if (!ok) failed += 1;
    console.log(`${path.basename(filePath)} -> ${ok ? 'OK' : 'FAIL'} bytes=${bytes.length} ms=${Date.now() - started}`);
  }

  // 复用窗口时第二张应明显快于第一张。
  const repeatStarted = Date.now();
  await transcoder.convertToJpegDataUrl(bmpPath, { maxEdge: 2048, quality: 85 });
  console.log(`reuse ms=${Date.now() - repeatStarted}`);
  await transcoder.dispose();
  fs.rmSync(dir, { recursive: true, force: true });
  app.exit(failed ? 1 : 0);
}).catch((error) => {
  console.log('ERR ' + error.message);
  app.exit(1);
});
```

Run: `cd client && env -u ELECTRON_RUN_AS_NODE ./node_modules/electron/dist/electron.exe .tmp-transcoder-check.cjs`
Expected: 两行 `OK`，退出码 0，且 `reuse ms=` 明显小于首次

- [ ] **Step 4: 删除一次性脚本**

```bash
cd C:/workspace/OpenBidKit_Yibiao/client && rm -f .tmp-transcoder-check.cjs
```

- [ ] **Step 5: 提交**

```bash
cd C:/workspace/OpenBidKit_Yibiao
git add client/electron/services/offscreenImageTranscoder.cjs
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat: 新增离屏 Chromium 图片转码模块（懒创建+复用+空闲销毁）"
```

---

### Task 3: 后台失败日志（始终打印 + 补上准备阶段的洞）

**Files:**
- Modify: `client/electron/utils/aiLog.cjs`
- Modify: `client/electron/services/aiService.cjs:19-25`（引入）、`:785-810`（真实错误保留）、`:812-870`（准备阶段日志 + 真实错误保留）、`:1362-1366`（`chatWithConfig` 准备阶段日志）
- Test: `client/electron/utils/aiLog.test.cjs`

**Interfaces:**
- Consumes: 无
- Produces: `logAiFailure(scope, payload) → void`（不抛错，单行 stdout，**不受 `developer_mode` 控制**）

**说明：** 现存两个洞——① `prepareMultimodalMessages` 在 `writeAiLog` **之前**调用，图片准备失败完全不产生日志；
② JSON 解析/修复全失败时真实错误被通用 `failureMessage` 替换。本任务同时堵住。`aiLog.cjs` 无 Electron 依赖，
因此 `logAiFailure` 可用普通 `node --test` 覆盖。

- [ ] **Step 1: 写失败的测试**

创建 `client/electron/utils/aiLog.test.cjs`：

```js
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd client && node --test electron/utils/aiLog.test.cjs`
Expected: FAIL —— `logAiFailure is not a function`

- [ ] **Step 3: 在 `aiLog.cjs` 实现 `logAiFailure`**

在 `client/electron/utils/aiLog.cjs` 顶部引入后新增（放在 `writeAiLog` 之后）：

```js
const { logError } = require('./logger.cjs');
```

```js
const MAX_AI_FAILURE_VALUE_LENGTH = 500;

// 值可能是对象、含空格、超长甚至循环引用——统一归一化成单行片段。
function formatAiFailureValue(value) {
  let text;
  if (typeof value === 'string') {
    text = value;
  } else {
    try {
      text = JSON.stringify(value);
    } catch {
      text = String(value);
    }
  }
  const normalized = String(text === undefined || text === null ? '' : text).replace(/\s+/g, ' ').trim();
  if (!normalized) return '';
  return normalized.length > MAX_AI_FAILURE_VALUE_LENGTH
    ? `${normalized.slice(0, MAX_AI_FAILURE_VALUE_LENGTH)}…`
    : normalized;
}

// 始终打印的失败诊断：不受 developer_mode 控制，单行 key=value，绝不抛错。
function logAiFailure(scope, payload = {}) {
  try {
    const parts = [];
    const scopeValue = formatAiFailureValue(scope);
    if (scopeValue) parts.push(`scope=${scopeValue}`);
    for (const [key, value] of Object.entries(payload || {})) {
      const formatted = formatAiFailureValue(value);
      if (!formatted) continue;
      parts.push(`${key}=${/\s/.test(formatted) ? `"${formatted}"` : formatted}`);
    }
    logError(`ai-failure ${parts.join(' ')}`);
  } catch {
    // 诊断日志不能影响主流程。
  }
}
```

并把 `logAiFailure` 加进 `module.exports`。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd client && node --test electron/utils/aiLog.test.cjs`
Expected: PASS（4 个用例）

- [ ] **Step 5: 在 `aiService.cjs` 保留真实错误**

在引入块（`:19-25`）把 `logAiFailure` 加进去：

```js
const {
  createAiRequestId: createRequestId,
  getAiErrorLogError,
  getAiErrorLogResponse,
  logAiFailure,
  resolveAiLogTitle,
  writeAiLog,
} = require('../utils/aiLog.cjs');
```

新增辅助函数（放在 `formatJsonIssues` 之后）：

```js
// 用户可见的 message 保持不变，但把底层原因挂上去，供日志与诊断使用。
function attachFailureDetail(failureMessage, cause) {
  const wrapped = new Error(failureMessage);
  wrapped.code = 'AI_JSON_INVALID';
  wrapped.detail = {
    cause: cause?.message || String(cause || ''),
    issues: formatJsonIssues(cause),
  };
  wrapped.cause = cause;
  return wrapped;
}
```

把 `parseOrRepairJsonResponseWithConfig` 里的 `throw new Error(failureMessage);`（`:806`）改为：

```js
    } catch (repairError) {
      throw attachFailureDetail(failureMessage, repairError);
    }
```

（注意：原代码该处 `catch {}` 未接收错误对象，需要改成 `catch (repairError)`。）

把 `collectJsonResponseWithConfig` 里重试耗尽处的 `throw new Error(failureMessage);`（`:859`）改为：

```js
      if (attempt === maxRetries) {
        await emitProgress(request.progressCallback, `${progressLabel}连续 ${totalAttempts} 次校验失败。`);
        throw attachFailureDetail(failureMessage, lastError);
      }
```

把文件末尾的 `throw new Error(lastError?.message || failureMessage);`（`:867`）改为：

```js
  throw attachFailureDetail(failureMessage, lastError);
```

- [ ] **Step 6: 在 `aiService.cjs` 补上「准备阶段失败」的日志**

新增辅助函数（放在 `prepareMultimodalMessages` 之后）：

```js
// 图片准备失败发生在 writeAiLog 之前，若不单独记录就完全静默。
function logPrepareFailure(app, config, { request, logTitle, error }) {
  const errorDetail = {
    code: error?.code || '',
    message: error?.message || String(error || ''),
    file_path: error?.filePath || '',
    image_format: error?.imageFormat || '',
  };
  try {
    writeAiLog(app, config, {
      request_id: createRequestId(),
      log_title: logTitle,
      type: 'request-prepare-error',
      url: `${trimBaseUrl(config.base_url)}/chat/completions`,
      status: 'error',
      error: errorDetail.message,
      error_code: errorDetail.code,
      error_detail: errorDetail,
      created_at: new Date().toISOString(),
    });
  } catch {
    // 诊断日志不能影响主流程。
  }
  logAiFailure('ai.prepare', {
    log_title: logTitle,
    code: errorDetail.code,
    file: errorDetail.file_path ? path.basename(errorDetail.file_path) : '',
    image_format: errorDetail.image_format,
    message: errorDetail.message,
  });
}
```

把 `collectJsonResponseWithConfig` 开头改为（注意 `progressLabel`/`logTitle` 需要上移，因为下面要用）：

```js
async function collectJsonResponseWithConfig(app, config, request) {
  const maxRetries = request.max_retries ?? 2;
  const totalAttempts = maxRetries + 1;
  const responseFormat = request.response_format || { type: 'json_object' };
  const progressLabel = request.progressLabel || 'JSON结果';
  const failureMessage = request.failureMessage || '模型返回的 JSON 数据格式无效';
  const logTitle = resolveAiLogTitle(request, progressLabel);
  let lastError = null;

  let preparedMessages;
  try {
    preparedMessages = await prepareMultimodalMessages(config, request.messages);
  } catch (error) {
    logPrepareFailure(app, config, { request, logTitle, error });
    throw error;
  }

  for (let attempt = 0; attempt < totalAttempts; attempt += 1) {
```

（即：把原来 `const preparedMessages = await ...` 那一行替换成上面的 try/catch 结构，并把它下面原有的
`const maxRetries` / `totalAttempts` / `responseFormat` / `progressLabel` / `failureMessage` / `logTitle` / `let lastError = null;`
这几行**删除**，因为已经上移。）

同样在 `chatWithConfig`（`:1362`）把：

```js
  const preparedRequest = {
    ...request,
    messages: await prepareMultimodalMessages(config, request.messages),
  };
```

改为：

```js
  let preparedRequest = null;
  try {
    preparedRequest = {
      ...request,
      messages: await prepareMultimodalMessages(config, request.messages),
    };
  } catch (error) {
    logPrepareFailure(app, config, { request, logTitle: resolveAiLogTitle(request, '文本请求'), error });
    throw error;
  }
```

- [ ] **Step 7: 语法检查与手工验证日志真的落盘**

Run: `cd client && node --check electron/utils/aiLog.cjs && node --check electron/services/aiService.cjs`
Expected: 无输出（通过）

创建 `client/.tmp-prepare-log-check.cjs`：

```js
// 一次性验证：准备阶段失败必须同时产出 AI 日志与 ai-failure 单行。跑完即删。
const { app } = require('electron');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'yibiao-prepare-'));
app.setPath('userData', userDataDir);

const { createAiService } = require('./electron/services/aiService.cjs');

// 44 字节的伪 EMF：既不是 PNG/JPEG，也不是 GIF/BMP/WebP，必然走 skip 分支。
function emfBytes() {
  const buffer = Buffer.alloc(64);
  buffer.writeUInt32LE(1, 0);
  buffer.write(' EMF', 40, 'latin1');
  return buffer;
}

app.whenReady().then(async () => {
  const imagePath = path.join(userDataDir, 'scan.emf');
  fs.writeFileSync(imagePath, emfBytes());

  const config = {
    api_key: 'sk-test',
    base_url: 'https://example.invalid/v1',
    model_name: 'test-model',
    multimodal_enabled: true,
    developer_mode: true,
    request_mode: 'normal',
  };
  const aiService = createAiService({ app, configStore: { load: () => config, save: () => {} } });

  let caught = null;
  try {
    await aiService.collectJsonResponse({
      messages: [{ role: 'user', content: [{ type: 'text', text: 'x' }, { type: 'local_image', path: imagePath }] }],
      progressLabel: '主体印章图片识别',
      failureMessage: '印章图片识别结果格式无效',
    });
  } catch (error) {
    caught = error;
  }

  const logDir = path.join(userDataDir, 'logs', 'ai');
  const logs = fs.existsSync(logDir) ? fs.readdirSync(logDir) : [];
  const prepareLogs = logs.filter((name) => name.includes('主体印章图片识别'));
  let prepareType = '';
  for (const name of prepareLogs) {
    const payload = JSON.parse(fs.readFileSync(path.join(logDir, name), 'utf8'));
    if (payload.type === 'request-prepare-error') prepareType = payload.type;
  }

  console.log('caught=' + (caught ? caught.message : '(未抛错)'));
  console.log('caught_code=' + (caught && caught.code));
  console.log('log_files=' + logs.length);
  console.log('prepare_error_type=' + (prepareType || '(缺失)'));

  fs.rmSync(userDataDir, { recursive: true, force: true });
  const ok = Boolean(caught) && caught.code === 'UNSUPPORTED_IMAGE_FORMAT' && prepareType === 'request-prepare-error';
  app.exit(ok ? 0 : 1);
}).catch((error) => {
  console.log('ERR ' + error.message);
  app.exit(1);
});
```

Run: `cd client && env -u ELECTRON_RUN_AS_NODE ./node_modules/electron/dist/electron.exe .tmp-prepare-log-check.cjs`
Expected: `caught_code=UNSUPPORTED_IMAGE_FORMAT`、`prepare_error_type=request-prepare-error`，退出码 0
（终端还会出现一行 `[yibiao-client] ai-failure scope=ai.prepare …`，这正是要验证的行为）

- [ ] **Step 8: 删除一次性脚本并提交**

```bash
cd C:/workspace/OpenBidKit_Yibiao/client && rm -f .tmp-prepare-log-check.cjs
cd C:/workspace/OpenBidKit_Yibiao
git add client/electron/utils/aiLog.cjs client/electron/utils/aiLog.test.cjs client/electron/services/aiService.cjs
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat: 补上图片准备阶段的失败日志并保留 JSON 解析的真实错误"
```

---

### Task 4: `aiService` 接入「压缩 / 透传 / 转码」三分派

**Files:**
- Modify: `client/electron/services/aiService.cjs:159-188`（`compressLocalImageToDataUrl`）、`:26-30`（新增常量）、引入块、`module.exports`
- Test: 通过 `__test__` 导出 + 一次性 Electron 脚本验证

**Interfaces:**
- Consumes: `resolveImagePlan`（Task 1）、`getSharedTranscoder`（Task 2）
- Produces:
  - `aiService.inspectLocalImage(filePath) → Promise<{ readable, format, label, reason? }>`
  - `aiService.__test__.compressLocalImageToDataUrl(filePath) → Promise<string>`
  - `aiService.__test__.inspectLocalImage(filePath) → Promise<{...}>`
  - 常量 `MULTIMODAL_PASSTHROUGH_MAX_BYTES = 2 * 1024 * 1024`
  - 错误码：`UNSUPPORTED_IMAGE_FORMAT`、`PASSTHROUGH_TOO_LARGE`、`IMAGE_DECODE_FAILED`、`IMAGE_READ_FAILED`

**说明：** `compressLocalImageToDataUrl` 依赖 `nativeImage` 与离屏窗口，无法用普通 `node --test` 覆盖，
因此按仓库既有惯例（`checkResultExportService.cjs` 的 `__test__`）导出供 Electron 脚本验证。

- [ ] **Step 1: 新增常量与引入**

在 `client/electron/services/aiService.cjs` 的 `MULTIMODAL_IMAGE_JPEG_QUALITY` 之后新增：

```js
// 透传不做缩放，用体积上限兜底，避免超大文件撑爆请求体。
const MULTIMODAL_PASSTHROUGH_MAX_BYTES = 2 * 1024 * 1024;
```

在引入区新增：

```js
const { resolveImagePlan } = require('../utils/imageFormat.cjs');
const { getSharedTranscoder } = require('./offscreenImageTranscoder.cjs');
```

- [ ] **Step 2: 重写 `compressLocalImageToDataUrl`**

把现有 `compressLocalImageToDataUrl`（`:159-188`）整体替换为：

```js
function wrapLocalImageError(filePath, reason, code, cause) {
  const error = new Error(`图片读取或压缩失败（${path.basename(filePath)}）：${reason}`);
  error.code = code;
  error.filePath = filePath;
  if (cause) error.cause = cause;
  return error;
}

async function compressLocalImageToDataUrl(filePath) {
  const normalizedPath = String(filePath || '').trim();
  if (!normalizedPath) {
    throw new Error('本地图片路径不能为空');
  }

  let sourceBuffer;
  try {
    sourceBuffer = await fs.promises.readFile(normalizedPath);
  } catch (error) {
    throw wrapLocalImageError(normalizedPath, '图片文件不可读', 'IMAGE_READ_FAILED', error);
  }

  const plan = resolveImagePlan(sourceBuffer, { maxPassthroughBytes: MULTIMODAL_PASSTHROUGH_MAX_BYTES });

  // GIF：解码器不支持，但模型能识别，原字节直接透传。
  if (plan.action === 'passthrough') {
    return `data:${plan.mime};base64,${sourceBuffer.toString('base64')}`;
  }

  // BMP/WebP：nativeImage 解不了、Chromium 能解，交给离屏窗口转成 JPEG。
  if (plan.action === 'transcode') {
    return getSharedTranscoder().convertToJpegDataUrl(normalizedPath, {
      maxEdge: MULTIMODAL_IMAGE_MAX_EDGE,
      quality: MULTIMODAL_IMAGE_JPEG_QUALITY / 100,
      mime: plan.mime,
    });
  }

  // TIFF/EMF/WMF/未知格式，以及超限的 GIF。
  if (plan.action === 'skip') {
    throw wrapLocalImageError(normalizedPath, plan.reason, plan.code);
  }

  // PNG/JPEG：维持既有行为（解码 → 必要时缩放 → JPEG）。
  try {
    const sourceImage = nativeImage.createFromBuffer(sourceBuffer);
    if (sourceImage.isEmpty()) {
      throw new Error('图片格式不受支持或文件已损坏');
    }

    const { width, height } = sourceImage.getSize();
    const maxEdge = Math.max(width, height);
    const image = maxEdge > MULTIMODAL_IMAGE_MAX_EDGE
      ? sourceImage.resize(width >= height
        ? { width: MULTIMODAL_IMAGE_MAX_EDGE, quality: 'best' }
        : { height: MULTIMODAL_IMAGE_MAX_EDGE, quality: 'best' })
      : sourceImage;
    const compressedBuffer = image.toJPEG(MULTIMODAL_IMAGE_JPEG_QUALITY);
    if (!compressedBuffer.length) {
      throw new Error('图片压缩结果为空');
    }

    return `data:image/jpeg;base64,${compressedBuffer.toString('base64')}`;
  } catch (error) {
    throw wrapLocalImageError(normalizedPath, error.message, 'IMAGE_DECODE_FAILED', error);
  }
}

// 供印章检查预检：判断这张图是否可能被送给模型（不做实际转码/上传）。
async function inspectLocalImageFile(filePath) {
  const normalizedPath = String(filePath || '').trim();
  if (!normalizedPath) {
    return { readable: false, format: 'unknown', label: '未知格式', reason: '图片文件不可读' };
  }

  let sourceBuffer;
  try {
    sourceBuffer = await fs.promises.readFile(normalizedPath);
  } catch {
    return { readable: false, format: 'unknown', label: '未知格式', reason: '图片文件不可读' };
  }

  const plan = resolveImagePlan(sourceBuffer, { maxPassthroughBytes: MULTIMODAL_PASSTHROUGH_MAX_BYTES });
  if (plan.action === 'skip') {
    return { readable: false, format: plan.format, label: plan.label, reason: plan.reason };
  }
  if (plan.action === 'passthrough' || plan.action === 'transcode') {
    // 不预演转码：失败已被逐张隔离兜住，预演会让成本翻倍。
    return { readable: true, format: plan.format, label: plan.label };
  }

  try {
    const image = nativeImage.createFromBuffer(sourceBuffer);
    if (image.isEmpty()) {
      return { readable: false, format: plan.format, label: plan.label, reason: '图片无法解析' };
    }
  } catch {
    return { readable: false, format: plan.format, label: plan.label, reason: '图片无法解析' };
  }
  return { readable: true, format: plan.format, label: plan.label };
}
```

- [ ] **Step 3: 把 `inspectLocalImage` 挂到服务对象并导出 `__test__`**

在 `const service = { getConfig() { ... }, ...` 里，`getConfig()` 之后新增：

```js
    inspectLocalImage(filePath) {
      return inspectLocalImageFile(filePath);
    },
```

（模块级函数命名为 `inspectLocalImageFile`，服务方法命名为 `inspectLocalImage`，避免同名遮蔽。）

把文件末尾的 `module.exports` 改为：

```js
module.exports = {
  createAiService,
  __test__: {
    compressLocalImageToDataUrl,
    inspectLocalImageFile,
  },
};
```

- [ ] **Step 4: 语法检查**

Run: `cd client && node --check electron/services/aiService.cjs`
Expected: 无输出（通过）

- [ ] **Step 5: 用一次性 Electron 脚本验证四条分支**

创建 `client/.tmp-dispatch-check.cjs`：

```js
// 一次性验证：PNG/JPEG→压缩、GIF→透传、BMP/WebP→转码、TIFF→跳过。跑完即删。
const { app, BrowserWindow } = require('electron');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'yibiao-dispatch-'));
app.setPath('userData', userDataDir);

const { createAiService } = require('./electron/services/aiService.cjs');
const { nativeImage } = require('electron');

function makeBmp(width, height) {
  const rowSize = Math.floor((24 * width + 31) / 32) * 4;
  const pixelBytes = rowSize * height;
  const buffer = Buffer.alloc(54 + pixelBytes);
  buffer.write('BM', 0, 'latin1');
  buffer.writeUInt32LE(54 + pixelBytes, 2);
  buffer.writeUInt32LE(54, 10);
  buffer.writeUInt32LE(40, 14);
  buffer.writeInt32LE(width, 18);
  buffer.writeInt32LE(height, 22);
  buffer.writeUInt16LE(1, 26);
  buffer.writeUInt16LE(24, 28);
  buffer.writeUInt32LE(pixelBytes, 34);
  for (let i = 0; i < pixelBytes; i += 1) buffer[54 + i] = (i * 11) % 251;
  return buffer;
}

function makeGif() {
  const buffer = Buffer.alloc(64);
  buffer.write('GIF89a', 0, 'latin1');
  return buffer;
}

function makeTiff() {
  return Buffer.from([0x49, 0x49, 0x2a, 0x00, 0x08, 0x00, 0x00, 0x00]);
}

async function mintWebp() {
  const win = new BrowserWindow({
    width: 32, height: 32, show: false, frame: false,
    webPreferences: { offscreen: true, contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  await win.loadURL('data:text/html,<html><body style="margin:0"></body></html>');
  const dataUrl = await win.webContents.executeJavaScript(`(() => {
    const c = document.createElement('canvas'); c.width = 240; c.height = 160;
    const g = c.getContext('2d');
    g.fillStyle = '#0a0'; g.fillRect(0, 0, 240, 160);
    g.fillStyle = '#fff'; g.fillRect(30, 30, 90, 60);
    return c.toDataURL('image/webp');
  })()`, true);
  win.destroy();
  return Buffer.from(dataUrl.split(',')[1], 'base64');
}

app.whenReady().then(async () => {
  const pngPath = path.join(userDataDir, 'seal.png');
  const jpegPath = path.join(userDataDir, 'seal.jpg');
  const gifPath = path.join(userDataDir, 'seal.gif');
  const bmpPath = path.join(userDataDir, 'seal.bmp');
  const webpPath = path.join(userDataDir, 'seal.webp');
  const tiffPath = path.join(userDataDir, 'seal.tiff');

  const base = nativeImage.createFromBitmap(Buffer.alloc(120 * 90 * 4, 210), { width: 120, height: 90 });
  fs.writeFileSync(pngPath, base.toPNG());
  fs.writeFileSync(jpegPath, base.toJPEG(90));
  fs.writeFileSync(gifPath, makeGif());
  fs.writeFileSync(bmpPath, makeBmp(120, 90));
  fs.writeFileSync(webpPath, await mintWebp());
  fs.writeFileSync(tiffPath, makeTiff());

  const config = { api_key: 'sk-test', base_url: 'https://example.invalid/v1', model_name: 'm', multimodal_enabled: true, developer_mode: true };
  const aiService = createAiService({ app, configStore: { load: () => config, save: () => {} } });
  const { compressLocalImageToDataUrl, inspectLocalImage } = aiService.__test__;

  const expectations = [
    [pngPath, 'data:image/jpeg;base64,'],
    [jpegPath, 'data:image/jpeg;base64,'],
    [gifPath, 'data:image/gif;base64,'],
    [bmpPath, 'data:image/jpeg;base64,'],
    [webpPath, 'data:image/jpeg;base64,'],
  ];

  let failed = 0;
  for (const [filePath, prefix] of expectations) {
    let value = '';
    try {
      value = await compressLocalImageToDataUrl(filePath);
    } catch (error) {
      value = `THREW ${error.code} ${error.message}`;
    }
    const ok = value.startsWith(prefix);
    if (!ok) failed += 1;
    console.log(`${path.basename(filePath)} ${ok ? 'OK' : 'FAIL'} -> ${String(value).slice(0, 48)}`);
  }

  let tiffError = null;
  try {
    await compressLocalImageToDataUrl(tiffPath);
  } catch (error) {
    tiffError = error;
  }
  const tiffOk = tiffError?.code === 'UNSUPPORTED_IMAGE_FORMAT';
  if (!tiffOk) failed += 1;
  console.log(`seal.tiff ${tiffOk ? 'OK' : 'FAIL'} -> code=${tiffError?.code}`);

  const inspections = await Promise.all([pngPath, gifPath, bmpPath, webpPath, tiffPath]
    .map(async (filePath) => [path.basename(filePath), await inspectLocalImage(filePath)]));
  for (const [name, result] of inspections) {
    console.log(`inspect ${name} readable=${result.readable} format=${result.format} ${result.reason || ''}`);
  }
  const inspectOk = inspections.find(([name]) => name === 'seal.tiff')[1].readable === false
    && inspections.filter(([name]) => name !== 'seal.tiff').every(([, r]) => r.readable === true);
  if (!inspectOk) failed += 1;

  fs.rmSync(userDataDir, { recursive: true, force: true });
  app.exit(failed ? 1 : 0);
}).catch((error) => {
  console.log('ERR ' + error.message);
  app.exit(1);
});
```

Run: `cd client && env -u ELECTRON_RUN_AS_NODE ./node_modules/electron/dist/electron.exe .tmp-dispatch-check.cjs`
Expected: 5 行 `OK`、`seal.tiff OK -> code=UNSUPPORTED_IMAGE_FORMAT`、5 行 `inspect …`（仅 tiff 为 `readable=false`），退出码 0

- [ ] **Step 6: 删除一次性脚本并提交**

```bash
cd C:/workspace/OpenBidKit_Yibiao/client && rm -f .tmp-dispatch-check.cjs
cd C:/workspace/OpenBidKit_Yibiao
git add client/electron/services/aiService.cjs
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat: 图片按格式三分派（PNG/JPEG 压缩、GIF 透传、BMP/WebP 离屏转码）"
```

---

### Task 5: `subjectVisionProvider` 预检跳过、批次降级、进度与失败上报

**Files:**
- Modify: `client/electron/services/subjectVisionProvider.cjs`
- Test: `client/electron/services/subjectVisionProvider.test.cjs`

**Interfaces:**
- Consumes: `aiService.inspectLocalImage`（Task 4）、`logAiFailure`（Task 3）
- Produces: `createOnlineVlmProvider({ aiService }).inspect(request, hooks)`，其中
  ```ts
  hooks = {
    onProgress?: (event:
      | { phase: 'content', processed: number, total: number, sealCount: number, skipped: number }
      | { phase: 'role', processed: number, total: number }
    ) => void,
    onLog?: (event: { type: string, [key: string]: unknown }) => void,
  }
  ```
  结果资产仍是 `{ assetId, status: 'inspected' | 'skipped' | 'failed', skipReason?, hasSeal, ... }`

**说明：** 阶段一 `total` 是**去重后可读图片数**（被跳过的不进分母）；阶段二 `total` 是**盖章位置数**（不去重）。
批次失败时降级为逐张重试，只有真正失败的那张记 `failed`。

- [ ] **Step 1: 在测试文件里补桩与用例**

在 `client/electron/services/subjectVisionProvider.test.cjs` 末尾追加：

```js
function createFakeAiServiceWithPrecheck({ multimodalEnabled = true, responses = [], unreadable = [] } = {}) {
  const base = createFakeAiService({ multimodalEnabled, responses });
  const skipped = new Set(unreadable);
  return {
    ...base,
    inspectLocalImage: async (filePath) => (skipped.has(path.basename(filePath))
      ? { readable: false, format: 'tiff', label: 'TIFF', reason: '图片格式不支持（TIFF）' }
      : { readable: true, format: 'png', label: 'PNG' }),
  };
}
```

（需在文件顶部补 `const path = require('node:path');` 与
`const { logAiFailure } = require('../utils/aiLog.cjs');` 之外的引入——`logAiFailure` 不需要引入，
provider 内部直接调用）

```js
test('不可读的格式被跳过且不进批次', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [{ images: [{ has_seal: false, image_type: 'unknown' }] }],
    unreadable: ['logo.tiff'],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const logs = [];
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [
      makeAsset({ assetId: 'asset-1', absolutePath: 'C:/fake/logo.tiff', contentHash: 'hash-A' }),
      makeAsset({ assetId: 'asset-2', absolutePath: 'C:/fake/seal.png', contentHash: 'hash-B' }),
    ],
  }, { onLog: (event) => logs.push(event) });

  const skippedAsset = result.assets.find((item) => item.assetId === 'asset-1');
  assert.strictEqual(skippedAsset.status, 'skipped');
  assert.ok(skippedAsset.skipReason.includes('TIFF'), skippedAsset.skipReason);
  assert.ok(logs.some((event) => event.type === 'asset.skipped' && event.assetId === 'asset-1'));

  const imageParts = aiService.calls[0].messages
    .filter((message) => Array.isArray(message.content))
    .flatMap((message) => message.content)
    .filter((part) => part.type === 'local_image');
  assert.strictEqual(imageParts.length, 1, '被跳过的图片不应进入批次');
  assert.ok(imageParts[0].path.endsWith('seal.png'));
});

test('可转码格式照常入批，不被跳过', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [{ images: [{ has_seal: true, seal_text: '甲公司', image_type: 'document' }] }],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [makeAsset({ assetId: 'asset-1', absolutePath: 'C:/fake/seal.bmp', contentHash: 'hash-A' })],
  });
  assert.strictEqual(result.assets[0].status, 'inspected');
  assert.strictEqual(result.assets[0].hasSeal, true);
});

test('批次失败降级为逐张，只有坏图失败', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [
      new Error('整批失败'),                                          // 第 1 次：整批
      { images: [{ has_seal: false, image_type: 'unknown' }] },         // 第 2 次：asset-1 单独成功
      new Error('该图失败'),                                          // 第 3 次：asset-2 单独失败
      { positions: [{ page_role: 'unclear', seal_role: 'unclear' }] },   // 第 4 次：角色判断
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [
      makeAsset({ assetId: 'asset-1', contentHash: 'hash-A', assetKind: 'standalone' }),
      makeAsset({ assetId: 'asset-2', contentHash: 'hash-B', assetKind: 'standalone' }),
    ],
  });
  assert.strictEqual(aiService.calls.length, 4, '应为 1 次整批 + 2 次逐张 + 1 次角色判断');
  assert.strictEqual(result.assets.find((item) => item.assetId === 'asset-1').status, 'inspected');
  assert.strictEqual(result.assets.find((item) => item.assetId === 'asset-2').status, 'failed');
});

test('进度按去重后张数计，重复内容只算一次', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [
      { images: [{ has_seal: true, seal_text: '甲公司', image_type: 'document' }] },
      { images: [{ has_seal: false, image_type: 'unknown' }] },
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const events = [];
  await provider.inspect({
    ...REQUEST_BASE,
    assets: [
      makeAsset({ assetId: 'asset-1', contentHash: 'hash-A' }),
      makeAsset({ assetId: 'asset-2', contentHash: 'hash-A' }),
      makeAsset({ assetId: 'asset-3', contentHash: 'hash-A' }),
      makeAsset({ assetId: 'asset-4', contentHash: 'hash-A' }),
      makeAsset({ assetId: 'asset-5', contentHash: 'hash-B' }),
    ],
  }, { onProgress: (event) => events.push(event) });

  const contentEvents = events.filter((event) => event.phase === 'content');
  assert.strictEqual(contentEvents[0].total, 2, '分母应为去重后的 2，而不是 5');
  assert.strictEqual(contentEvents[contentEvents.length - 1].processed, 2);
  assert.strictEqual(contentEvents[contentEvents.length - 1].sealCount, 4, '印章数按出现次数累计');
});

test('被跳过的图片不进分母', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [{ images: [{ has_seal: false, image_type: 'unknown' }] }],
    unreadable: ['logo.tiff'],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const events = [];
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [
      makeAsset({ assetId: 'asset-1', absolutePath: 'C:/fake/logo.tiff', contentHash: 'hash-A' }),
      makeAsset({ assetId: 'asset-2', absolutePath: 'C:/fake/seal.png', contentHash: 'hash-B' }),
    ],
  }, { onProgress: (event) => events.push(event) });

  const contentEvents = events.filter((event) => event.phase === 'content');
  assert.strictEqual(contentEvents[0].total, 1);
  assert.strictEqual(result.assets.filter((item) => item.status === 'skipped').length, 1);
});

test('全部图片被跳过时 total 为 0', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [],
    unreadable: ['logo.tiff'],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const events = [];
  await provider.inspect({
    ...REQUEST_BASE,
    assets: [makeAsset({ assetId: 'asset-1', absolutePath: 'C:/fake/logo.tiff', contentHash: 'hash-A' })],
  }, { onProgress: (event) => events.push(event) });

  const contentEvents = events.filter((event) => event.phase === 'content');
  assert.strictEqual(contentEvents[0].total, 0);
  assert.strictEqual(contentEvents[0].skipped, 1, '事件应携带被跳过的去重张数');
  assert.strictEqual(aiService.calls.length, 0, '不应发起任何图片请求');
});

test('role 阶段按位置计（同一内容的不同位置不去重）', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [
      { images: [{ has_seal: true, seal_text: '甲公司', image_type: 'document' }] },
      { positions: [{ page_role: 'bidder', seal_role: 'bidder', detected_subject: '甲公司', name_confidence: 0.9, role_confidence: 0.9 }] },
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const events = [];
  await provider.inspect({
    ...REQUEST_BASE,
    assets: [
      makeAsset({ assetId: 'asset-1', contentHash: 'hash-A', nearbyText: '投标函落款' }),
      makeAsset({ assetId: 'asset-2', contentHash: 'hash-A', nearbyText: '页脚' }),
    ],
  }, { onProgress: (event) => events.push(event) });

  const roleEvents = events.filter((event) => event.phase === 'role');
  assert.ok(roleEvents.length > 0, '应上报 role 进度');
  assert.strictEqual(roleEvents[roleEvents.length - 1].total, 2, '两个位置不能去重成 1');
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd client && node --test electron/services/subjectVisionProvider.test.cjs`
Expected: 新增用例 FAIL（跳过/降级/进度事件均未实现），既有用例仍 PASS

- [ ] **Step 3: 改 `subjectVisionProvider.cjs`**

把 `inspect(request)` 整个函数体替换为下面的实现（保留文件顶部的 `chunkItems` / `hashText` /
`buildContentPrompt` / `buildRolePrompt` 与 `runJson` 不变，并新增引入）：

```js
const { logAiFailure } = require('../utils/aiLog.cjs');
```

```js
// 把模型返回的单条角色判断归一化（原实现内联在循环里，抽出来便于逐张重试复用）。
function normalizeRoleItem(item) {
  const source = item || {};
  const pageRole = source.page_role !== undefined ? source.page_role : source.pageRole;
  const sealRole = source.seal_role !== undefined ? source.seal_role : source.sealRole;
  const detectedSubject = source.detected_subject !== undefined ? source.detected_subject : source.detectedSubject;
  const nameConfidence = source.name_confidence !== undefined ? source.name_confidence : source.nameConfidence;
  const roleConfidence = source.role_confidence !== undefined ? source.role_confidence : source.roleConfidence;
  return {
    pageRole: ['bidder', 'third-party', 'unclear'].includes(pageRole) ? pageRole : 'unclear',
    sealRole: ['bidder', 'third-party', 'unclear'].includes(sealRole) ? sealRole : 'unclear',
    detectedSubject: String(detectedSubject || ''),
    nameConfidence: Math.max(0, Math.min(1, Number(nameConfidence || 0))),
    roleConfidence: Math.max(0, Math.min(1, Number(roleConfidence || 0))),
    reason: String(source.reason || ''),
  };
}
```

```js
  async function inspect(request, hooks = {}) {
    const emitProgress = typeof hooks.onProgress === 'function' ? hooks.onProgress : () => {};
    const emitLog = typeof hooks.onLog === 'function' ? hooks.onLog : () => {};

    const config = typeof aiService.getConfig === 'function' ? aiService.getConfig() : {};
    if (!config?.multimodal_enabled) {
      return {
        providerStatus: 'unavailable',
        message: '当前文本模型未开启多模态支持，请到设置页的文本模型中开启',
        assets: [],
      };
    }

    const assets = Array.isArray(request?.assets) ? request.assets : [];
    const byAssetId = new Map(assets.map((asset) => [asset.assetId, { asset, content: null, error: undefined, skipReason: undefined }]));

    // 同一内容在文档里的出现次数：进度按去重张数，印章数按出现次数累计。
    const occurrenceCount = new Map();
    for (const entry of byAssetId.values()) {
      const hash = entry.asset.contentHash;
      occurrenceCount.set(hash, (occurrenceCount.get(hash) || 0) + 1);
    }

    // 预检：两种解码器都解不了的格式直接跳过，且不连坐同批其它图片。
    const precheck = typeof aiService.inspectLocalImage === 'function'
      ? aiService.inspectLocalImage.bind(aiService)
      : null;
    const pendingEntries = [];
    const seenHashes = new Set();
    let skippedCount = 0;
    for (const entry of byAssetId.values()) {
      const hash = entry.asset.contentHash;
      if (seenHashes.has(hash)) continue;
      seenHashes.add(hash);

      if (precheck) {
        let verdict = null;
        try {
          verdict = await precheck(entry.asset.absolutePath);
        } catch (error) {
          verdict = { readable: false, format: 'unknown', label: '未知格式', reason: '图片不可读' };
          logAiFailure('subject.seal.precheck', { asset: entry.asset.assetId, message: error?.message });
        }
        if (verdict && verdict.readable === false) {
          entry.skipReason = `${verdict.reason || '图片不可读'}，已跳过`;
          entry.format = verdict.format || 'unknown';
          entry.label = verdict.label || '未知格式';
          skippedCount += 1;
          emitLog({ type: 'asset.skipped', assetId: entry.asset.assetId, format: verdict.format, reason: entry.skipReason });
          continue;
        }
        entry.format = verdict?.format || 'unknown';
        entry.label = verdict?.label || '未知格式';
      }
      pendingEntries.push(entry);
    }

    const contentTotal = pendingEntries.length;
    let contentProcessed = 0;
    let sealCount = 0;
    const emitContentProgress = () => emitProgress({
      phase: 'content', processed: contentProcessed, total: contentTotal, sealCount, skipped: skippedCount,
    });
    // 先发一次，令「全部被跳过」时上层也能看见 total === 0。
    emitContentProgress();

    // 分批策略（spec §9.3）：整页扫描图每批 1 张；独立小图每批最多 4 张。两类不可混批。
    const contentBatches = [
      ...pendingEntries.filter((entry) => entry.asset.assetKind === 'page-scan').map((entry) => [entry]),
      ...chunkItems(pendingEntries.filter((entry) => entry.asset.assetKind !== 'page-scan'), STANDALONE_BATCH_SIZE),
    ];

    const contentCache = new Map();

    async function submitContentBatch(entries) {
      const payload = await runJson({
        messages: [{
          role: 'user',
          content: [
            { type: 'text', text: buildContentPrompt(entries) },
            ...entries.map((entry) => ({ type: 'local_image', path: entry.asset.absolutePath })),
          ],
        }],
      }, '主体印章图片识别', '印章图片识别结果格式无效');

      const images = Array.isArray(payload?.images) ? payload.images : [];
      entries.forEach((entry, index) => {
        const image = images[index] || {};
        const hasSeal = image.has_seal !== undefined ? image.has_seal : image.hasSeal;
        const sealText = image.seal_text !== undefined ? image.seal_text : image.sealText;
        const imageType = image.image_type !== undefined ? image.image_type : image.imageType;
        entry.content = {
          hasSeal: Boolean(hasSeal),
          sealText: String(sealText || ''),
          imageType: String(imageType || 'unknown'),
        };
        contentCache.set(entry.asset.contentHash, entry.content);
      });
    }

    function describeFailure(error) {
      return {
        status: error?.status || error?.statusCode || '',
        code: error?.code || '',
        providerMessage: error?.message || String(error || ''),
        errorName: error?.name || 'Error',
      };
    }

    for (let batchIndex = 0; batchIndex < contentBatches.length; batchIndex += 1) {
      const entries = contentBatches[batchIndex];
      const batchLabel = `${batchIndex + 1}/${contentBatches.length}`;
      const startedAt = Date.now();
      try {
        await submitContentBatch(entries);
      } catch (error) {
        const failure = describeFailure(error);
        logAiFailure('subject.seal.content', { phase: 'content', batch: batchLabel, assets: entries.length, ...failure, durationMs: Date.now() - startedAt });
        emitLog({
          type: 'batch.failed', phase: 'content', batch: batchLabel,
          assetIds: entries.map((entry) => entry.asset.assetId), ...failure,
        });

        if (entries.length > 1) {
          // 降级：逐张重试，只有仍然失败的那张记为失败。
          const recovered = [];
          const failedAssetIds = [];
          for (const entry of entries) {
            try {
              await submitContentBatch([entry]);
              recovered.push(entry.asset.assetId);
            } catch (singleError) {
              entry.error = singleError?.message || '图片识别失败';
              failedAssetIds.push(entry.asset.assetId);
              logAiFailure('subject.seal.content.isolated', { asset: entry.asset.assetId, ...describeFailure(singleError) });
            }
          }
          emitLog({ type: 'batch.isolated', phase: 'content', batch: batchLabel, batchSize: entries.length, recovered, failedAssetIds });
        } else {
          entries[0].error = error?.message || '图片识别失败';
        }
      }

      for (const entry of entries) {
        contentProcessed += 1;
        if (entry.content?.hasSeal) sealCount += occurrenceCount.get(entry.asset.contentHash) || 1;
      }
      emitContentProgress();
    }

    // 把缓存中的内容识别结果回填到被去重跳过的同 hash 出现位置，保证每个出现位置都参与角色判断。
    for (const entry of byAssetId.values()) {
      if (!entry.content && !entry.error && !entry.skipReason && contentCache.has(entry.asset.contentHash)) {
        entry.content = contentCache.get(entry.asset.contentHash);
      }
    }

    // 阶段二：角色判断（纯文本请求，按 contentHash + nearbyContextHash 缓存）。
    const roleCache = new Map();
    const pendingRoles = [...byAssetId.values()].filter((entry) => entry.content && !entry.error);
    const roleTotal = pendingRoles.length;
    let roleProcessed = 0;
    const emitRoleProgress = () => emitProgress({ phase: 'role', processed: roleProcessed, total: roleTotal });
    const roleBatches = chunkItems(pendingRoles, ROLE_BATCH_SIZE);

    async function submitRoleBatch(entries) {
      const positions = entries.map((entry) => ({ asset: entry.asset, content: entry.content }));
      const cacheKey = positions.map((entry) => `${entry.asset.contentHash}:${hashText(entry.asset.nearbyText)}`).join('|');
      if (roleCache.has(cacheKey)) {
        const cached = roleCache.get(cacheKey);
        entries.forEach((entry, index) => { entry.role = cached[index]; });
        return;
      }
      const payload = await runJson({
        messages: [{ role: 'user', content: buildRolePrompt({ officialName: request.officialName, aliases: request.aliases || [], positions }) }],
      }, '主体盖章角色判断', '盖章角色判断结果格式无效');
      const results = Array.isArray(payload?.positions) ? payload.positions : [];
      const normalized = entries.map((_entry, index) => normalizeRoleItem(results[index]));
      roleCache.set(cacheKey, normalized);
      entries.forEach((entry, index) => { entry.role = normalized[index]; });
    }

    for (let batchIndex = 0; batchIndex < roleBatches.length; batchIndex += 1) {
      const entries = roleBatches[batchIndex];
      const batchLabel = `${batchIndex + 1}/${roleBatches.length}`;
      const startedAt = Date.now();
      try {
        await submitRoleBatch(entries);
      } catch (error) {
        const failure = describeFailure(error);
        logAiFailure('subject.seal.role', { phase: 'role', batch: batchLabel, assets: entries.length, ...failure, durationMs: Date.now() - startedAt });
        emitLog({
          type: 'batch.failed', phase: 'role', batch: batchLabel,
          assetIds: entries.map((entry) => entry.asset.assetId), ...failure,
        });
        if (entries.length > 1) {
          for (const entry of entries) {
            try {
              await submitRoleBatch([entry]);
            } catch (singleError) {
              entry.error = singleError?.message || '角色判断失败';
            }
          }
        } else {
          entries[0].error = error?.message || '角色判断失败';
        }
      }
      roleProcessed += entries.length;
      emitRoleProgress();
    }

    // 组装结果：明确普通产品截图/拓扑图/照片跳过并记录原因（spec §9.3）。
    const resultAssets = [...byAssetId.values()].map((entry) => {
      if (entry.skipReason) {
        return {
          assetId: entry.asset.assetId,
          status: 'skipped',
          skipReason: entry.skipReason,
          format: entry.format || '',
          label: entry.label || '',
          hasSeal: false,
        };
      }
      if (entry.error) {
        return {
          assetId: entry.asset.assetId,
          status: 'failed',
          skipReason: entry.error,
          format: entry.format || '',
          label: entry.label || '',
          hasSeal: false,
        };
      }
      const content = contentCache.get(entry.asset.contentHash) || { hasSeal: false, sealText: '', imageType: 'unknown' };
      if (['product-screenshot', 'diagram', 'photo'].includes(content.imageType)) {
        return { assetId: entry.asset.assetId, status: 'skipped', skipReason: `识别为${content.imageType}，非盖章区域`, hasSeal: false };
      }
      if (!content.hasSeal) {
        return { assetId: entry.asset.assetId, status: 'inspected', hasSeal: false, imageType: content.imageType };
      }
      return {
        assetId: entry.asset.assetId,
        status: 'inspected',
        hasSeal: true,
        sealText: content.sealText || undefined,
        detectedSubject: entry.role?.detectedSubject || content.sealText || '',
        imageType: content.imageType,
        pageRole: entry.role?.pageRole || 'unclear',
        sealRole: entry.role?.sealRole || 'unclear',
        nameConfidence: entry.role?.nameConfidence ?? 0,
        roleConfidence: entry.role?.roleConfidence ?? 0,
        reason: entry.role?.reason || '',
      };
    });

    const failedCount = resultAssets.filter((item) => item.status === 'failed').length;
    return {
      providerStatus: failedCount === resultAssets.length && resultAssets.length > 0 ? 'error' : 'success',
      message: failedCount ? `${failedCount} 张图片识别失败` : undefined,
      assets: resultAssets,
    };
  }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd client && node --test electron/services/subjectVisionProvider.test.cjs`
Expected: PASS（既有 6 个用例 + 新增 7 个用例全绿）

- [ ] **Step 5: 语法检查与提交**

```bash
cd client && node --check electron/services/subjectVisionProvider.cjs
cd C:/workspace/OpenBidKit_Yibiao
git add client/electron/services/subjectVisionProvider.cjs client/electron/services/subjectVisionProvider.test.cjs
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat: 印章视觉检查支持预检跳过、批次降级隔离与去重进度上报"
```

---

### Task 6: `runSubjectSealCheck` 文案、状态与未识别清单

**Files:**
- Modify: `client/electron/services/subjectCheckTask.cjs:459-545`（`runSubjectSealCheck`）
- Modify: `client/electron/services/rejectionCheckTask.cjs:1753-1760`（传入 `logger`）
- Test: `client/electron/services/subjectCheckTask.test.cjs`

**Interfaces:**
- Consumes: `createOnlineVlmProvider(...).inspect(request, hooks)`（Task 5）
- Produces: `runSubjectSealCheck(aiService, options, onProgress)` 返回
  `{ findings, visionStatus, visionStats, unrecognized?, error? }`，其中
  `visionStats.unrecognized` 恒为数组（可直接被 Excel 导出消费）
- 未识别项形状：
  ```ts
  { assetId, bidDocumentId, ordinal, sectionTitle, locationHint, mediaName, mimeType, format, label, kind: 'skipped' | 'failed', reason }
  ```

- [ ] **Step 1: 写失败的测试**

在 `client/electron/services/subjectCheckTask.test.cjs` 里补引入与桩，再追加用例。
把文件顶部的引入块改为：

```js
const path = require('node:path');
const {
  buildSubjectIdentificationMessages,
  normalizeSubjectCandidates,
  parseAliasesFromCandidates,
  runBidSubjectIdentificationTask,
  runSubjectSealCheck,
} = require('./subjectCheckTask.cjs');
```

在 `createIdentificationAiStub` 之后追加：

```js
function createSealAiStub({ responses = [], unreadable = [] } = {}) {
  const calls = [];
  const skipped = new Set(unreadable);
  let index = 0;
  return {
    calls,
    getConfig: () => ({ multimodal_enabled: true }),
    inspectLocalImage: async (filePath) => (skipped.has(path.basename(filePath))
      ? { readable: false, format: 'tiff', label: 'TIFF', reason: '图片格式不支持（TIFF）' }
      : { readable: true, format: 'png', label: 'PNG' }),
    collectJsonResponse: async (request) => {
      calls.push(request);
      const response = responses[Math.min(index, responses.length - 1)];
      index += 1;
      if (response instanceof Error) throw response;
      return response;
    },
  };
}

function makeSealAsset(overrides = {}) {
  return {
    id: 'asset-1',
    bidDocumentId: 'bid-1',
    mediaName: 'media/image1.png',
    absolutePath: 'C:/fake/seal.png',
    contentHash: 'hash-A',
    mimeType: 'image/png',
    assetKind: 'standalone',
    sectionTitle: '第三章 投标函',
    locationHint: '正文',
    nearbyText: '投标人：（盖章）',
    ...overrides,
  };
}
```

追加用例：

```js
test('印章检查按去重张数上报两阶段进度文案', async () => {
  const aiService = createSealAiStub({
    responses: [
      { images: [{ has_seal: true, seal_text: SUBJECT_B, image_type: 'document' }, { has_seal: false, image_type: 'unknown' }] },
      { positions: [
        { page_role: 'bidder', seal_role: 'bidder', detected_subject: SUBJECT_B, name_confidence: 0.95, role_confidence: 0.95 },
        { page_role: 'unclear', seal_role: 'unclear', detected_subject: '', name_confidence: 0, role_confidence: 0 },
      ] },
    ],
  });
  const progress = [];
  await runSubjectSealCheck(aiService, {
    bidDocuments: BID_DOCUMENTS,
    visualAssets: [
      makeSealAsset({ id: 'asset-1', contentHash: 'hash-A' }),
      makeSealAsset({ id: 'asset-2', contentHash: 'hash-A' }),
      makeSealAsset({ id: 'asset-3', contentHash: 'hash-B', absolutePath: 'C:/fake/other.png' }),
    ],
    officialName: SUBJECT_A,
    aliases: [],
    mediaRootDir: 'C:/fake/media',
  }, (message) => progress.push(message));

  const joined = progress.join(' | ');
  assert.ok(progress.some((message) => message.includes('正在识别图片内容 2/2 张')), `分母应为去重后的 2：${joined}`);
  assert.ok(progress.some((message) => message.includes('已发现印章')), joined);
  assert.ok(progress.some((message) => message.includes('正在判断盖章身份')), joined);
});

test('全部图片被跳过时给出专门文案且状态为 partial', async () => {
  const aiService = createSealAiStub({ unreadable: ['seal.tiff'], responses: [] });
  const progress = [];
  const logs = [];
  const result = await runSubjectSealCheck(aiService, {
    visualAssets: [makeSealAsset({ absolutePath: 'C:/fake/seal.tiff' })],
    officialName: SUBJECT_A,
    aliases: [],
    mediaRootDir: 'C:/fake/media',
    logger: { write: (event, payload) => logs.push({ event, payload }) },
  }, (message) => progress.push(message));

  assert.ok(progress.some((message) => message.includes('没有可被识别的图片（1 张已跳过）')), progress.join(' | '));
  assert.strictEqual(result.visionStatus, 'partial');
  assert.strictEqual(result.visionStats.skippedAssets, 1);
  assert.strictEqual(result.visionStats.inspectedAssets, 0);
  assert.strictEqual(result.visionStats.failedAssets, 0);
  assert.strictEqual(aiService.calls.length, 0, '全部跳过时不应发起请求');
  assert.ok(logs.some((entry) => entry.event === 'rejection.subject.seal.asset.skipped'));
  assert.ok(logs.some((entry) => entry.event === 'rejection.subject.seal.completed'));
});

test('未能识别清单记录文档内序号、格式与原因', async () => {
  const aiService = createSealAiStub({
    unreadable: ['skip.tiff'],
    responses: [new Error('该图失败')],
  });
  const result = await runSubjectSealCheck(aiService, {
    visualAssets: [
      makeSealAsset({ id: 'asset-1', contentHash: 'hash-A', absolutePath: 'C:/fake/skip.tiff' }),
      makeSealAsset({ id: 'asset-2', contentHash: 'hash-B', absolutePath: 'C:/fake/broken.png' }),
    ],
    officialName: SUBJECT_A,
    aliases: [],
    mediaRootDir: 'C:/fake/media',
  }, () => {});

  assert.strictEqual(result.unrecognized.length, 2);
  const skipped = result.unrecognized.find((item) => item.kind === 'skipped');
  assert.strictEqual(skipped.ordinal, 1);
  assert.strictEqual(skipped.label, 'TIFF');
  assert.strictEqual(skipped.mediaName, 'media/image1.png');
  assert.ok(skipped.reason.includes('TIFF'), skipped.reason);
  const failed = result.unrecognized.find((item) => item.kind === 'failed');
  assert.strictEqual(failed.ordinal, 2);
  assert.ok(failed.reason.includes('该图失败'), failed.reason);
  assert.strictEqual(result.visionStats.unrecognized.length, 2, 'visionStats 上也要带上清单');
});

test('未传 logger 时印章检查照常完成', async () => {
  const aiService = createSealAiStub({ responses: [{ images: [{ has_seal: false, image_type: 'unknown' }] }] });
  const result = await runSubjectSealCheck(aiService, {
    visualAssets: [makeSealAsset()],
    officialName: SUBJECT_A,
    aliases: [],
    mediaRootDir: 'C:/fake/media',
  }, () => {});
  assert.strictEqual(result.visionStatus, 'success');
  assert.strictEqual(result.visionStats.inspectedAssets, 1);
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd client && node --test electron/services/subjectCheckTask.test.cjs`
Expected: 新增 4 个用例 FAIL（`runSubjectSealCheck` 还不接受 hooks/logger，也不返回 `unrecognized`），既有用例 PASS

- [ ] **Step 3: 改 `subjectCheckTask.cjs`**

在 `createEmptyVisionStats` 之后新增辅助函数：

```js
// 「未能识别的图片」清单项：供 Excel 明细逐张展示。
function createUnrecognizedEntry(asset, ordinal, { kind, reason, format, label }) {
  return {
    assetId: asset.id,
    bidDocumentId: asset.bidDocumentId,
    ordinal,
    sectionTitle: asset.sectionTitle || '',
    locationHint: asset.locationHint || '',
    mediaName: asset.mediaName || '',
    mimeType: asset.mimeType || '',
    format: format || '',
    label: label || '',
    kind,
    reason: String(reason || ''),
  };
}
```

把 `runSubjectSealCheck` 整个函数替换为：

```js
async function runSubjectSealCheck(aiService, {
  bidDocuments = [],
  visualAssets = [],
  officialName,
  aliases = [],
  subjectSealCheck = true,
  mediaRootDir,
  logger,
}, onProgress) {
  const stats = createEmptyVisionStats();
  stats.totalAssets = visualAssets.length;
  stats.unrecognized = [];

  if (!subjectSealCheck) {
    return { findings: [], visionStatus: 'disabled', visionStats: stats };
  }

  const provider = createOnlineVlmProvider({ aiService });
  const config = typeof aiService.getConfig === 'function' ? aiService.getConfig() : {};
  if (!config?.multimodal_enabled) {
    onProgress('当前文本模型未开启多模态支持，已跳过印章检查。');
    return { findings: [], visionStatus: 'unavailable', visionStats: stats };
  }
  if (!visualAssets.length) {
    return { findings: [], visionStatus: 'success', visionStats: stats };
  }

  const writeLog = typeof logger?.write === 'function' ? logger.write.bind(logger) : () => {};
  const providerAssets = visualAssets.map((asset) => ({
    assetId: asset.id,
    bidDocumentId: asset.bidDocumentId,
    absolutePath: resolveVisualAssetAbsolutePath({
      mediaRootDir,
      bidDocumentId: asset.bidDocumentId,
      relativePath: asset.relativePath,
    }),
    contentHash: asset.contentHash,
    assetKind: asset.assetKind,
    mimeType: asset.mimeType,
    sectionTitle: asset.sectionTitle,
    nearbyText: asset.nearbyText,
  }));
  stats.candidateAssets = providerAssets.length;

  writeLog('rejection.subject.seal.content.started', {
    total_assets: visualAssets.length,
    unique_assets: new Set(visualAssets.map((asset) => asset.contentHash)).size,
  });

  let response;
  try {
    response = await provider.inspect({
      protocolVersion: 1,
      officialName,
      aliases,
      assets: providerAssets,
    }, {
      onProgress: (event) => {
        if (event.phase === 'content') {
          if (event.total === 0) {
            onProgress(`没有可被识别的图片（${event.skipped || 0} 张已跳过）。`);
            return;
          }
          onProgress(`正在识别图片内容 ${event.processed}/${event.total} 张（已发现印章 ${event.sealCount} 张）…`);
          return;
        }
        if (event.total === 0) return;
        onProgress(`正在判断盖章身份 ${event.processed}/${event.total} 组…`);
      },
      onLog: (event) => {
        const { type, ...payload } = event;
        writeLog(`rejection.subject.seal.${type}`, payload);
      },
    });
  } catch (error) {
    writeLog('rejection.subject.seal.error', { name: error?.name || 'Error', message: error?.message || '' });
    return { findings: [], visionStatus: 'error', visionStats: stats, error: error.message };
  }

  if (response.providerStatus === 'unavailable') {
    return { findings: [], visionStatus: 'unavailable', visionStats: stats, error: response.message };
  }

  const findings = [];
  const unrecognized = [];
  const ordinalByDocument = new Map();
  for (const asset of visualAssets) {
    const ordinal = (ordinalByDocument.get(asset.bidDocumentId) || 0) + 1;
    ordinalByDocument.set(asset.bidDocumentId, ordinal);

    const providerAsset = response.assets.find((item) => item.assetId === asset.id);
    if (!providerAsset) {
      stats.failedAssets += 1;
      unrecognized.push(createUnrecognizedEntry(asset, ordinal, { kind: 'failed', reason: '未返回识别结果' }));
      continue;
    }
    if (providerAsset.status === 'skipped') {
      stats.skippedAssets += 1;
      unrecognized.push(createUnrecognizedEntry(asset, ordinal, {
        kind: 'skipped',
        reason: providerAsset.skipReason,
        format: providerAsset.format,
        label: providerAsset.label,
      }));
      continue;
    }
    if (providerAsset.status === 'failed') {
      stats.failedAssets += 1;
      unrecognized.push(createUnrecognizedEntry(asset, ordinal, {
        kind: 'failed',
        reason: providerAsset.skipReason,
        format: providerAsset.format,
        label: providerAsset.label,
      }));
      continue;
    }
    stats.inspectedAssets += 1;
    if (providerAsset.hasSeal) stats.sealAssets += 1;
    const finding = classifySealFinding(asset, providerAsset, { officialName, aliases });
    if (finding) {
      if (finding.level === 'needs-review') stats.reviewAssets += 1;
      findings.push(finding);
    }
  }

  // 全部失败（含 Provider 整体 error）时为 error；部分失败或「全跳过」为 partial（spec §13）。
  const allFailed = stats.failedAssets > 0 && stats.inspectedAssets + stats.skippedAssets === 0;
  const noneInspected = stats.inspectedAssets === 0 && stats.skippedAssets > 0;
  const visionStatus = response.providerStatus === 'error' || allFailed
    ? 'error'
    : (stats.failedAssets > 0 || noneInspected) ? 'partial' : 'success';

  stats.unrecognized = unrecognized;
  writeLog('rejection.subject.seal.completed', {
    vision_status: visionStatus,
    vision_stats: stats,
    unrecognized_count: unrecognized.length,
  });

  return { findings, visionStatus, visionStats: stats, unrecognized };
}
```

- [ ] **Step 4: 在 `rejectionCheckTask.cjs` 传入 logger**

把主体分支里的印章调用改为（只加一行 `logger`）：

```js
          sealResult = await runSubjectSealCheck(aiService, {
            bidDocuments: currentBidDocuments,
            visualAssets: subjectVisualAssets,
            officialName: subjectProfile.officialName,
            aliases: subjectProfile.aliases,
            subjectSealCheck: true,
            mediaRootDir: workspaceStore.getSubjectMediaRootDir(),
            logger: developerLogger,
          }, onProgress);
```

（`developerLogger` 已在 `runRejectionCheckTask` 开头创建，无需新增。）

- [ ] **Step 5: 运行测试确认通过**

Run: `cd client && node --test electron/services/subjectCheckTask.test.cjs`
Expected: PASS（既有 19 个用例 + 新增 4 个用例全绿）

- [ ] **Step 6: 语法检查与提交**

```bash
cd client && node --check electron/services/subjectCheckTask.cjs && node --check electron/services/rejectionCheckTask.cjs
cd C:/workspace/OpenBidKit_Yibiao
git add client/electron/services/subjectCheckTask.cjs client/electron/services/rejectionCheckTask.cjs client/electron/services/subjectCheckTask.test.cjs
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat: 印章检查上报去重进度、状态判定与未能识别清单"
```

---

### Task 7: 修复视觉资产的章节与附近文本提取

**Files:**
- Modify: `client/electron/services/docxVisualAssetExtractor.cjs:40-61`（新增段落索引与两个新实现）、`:68-128`（两个 collect 函数签名）、`:153-157`（调用处）
- Test: `client/electron/services/docxVisualAssetExtractor.test.cjs`

**Interfaces:**
- Consumes: 无
- Produces: `occurrences[]` 中 `sectionTitle`（章节文本或 `undefined`）与 `nearbyText`（可读文本，≤200 字符）语义变好；
  导出函数签名 `extractDocxVisualAssets(docxFilePath)` **不变**

**说明：** 现状 `nearbyText` 是 XML 碎片（按字符偏移切原始 XML），且它是喂给 VLM 的「位置上下文」；
`sectionTitle` 因只回溯 4000 字符而全为 `null`。本任务一并修好。

- [ ] **Step 1: 写失败的测试**

在 `client/electron/services/docxVisualAssetExtractor.test.cjs` 末尾追加：

```js
function withRewrittenDocumentXml(zip, rewrite) {
  const xml = zip.getEntry('word/document.xml').getData().toString('utf8');
  zip.updateFile('word/document.xml', Buffer.from(rewrite(xml), 'utf8'));
  return zip;
}

test('nearbyText 是可读文本，不含 XML 片段', async () => {
  const filePath = writeFixture(buildMinimalDocx(), 'nearby.docx');
  const { occurrences } = await extractDocxVisualAssets(filePath);
  const inline = occurrences.find((item) => item.placement === 'inline');
  assert.ok(inline.nearbyText.includes('承诺') || inline.nearbyText.includes('投标函'), inline.nearbyText);
  assert.strictEqual(
    /w:rsid|<wp:|w:val|w:drawing|effectExtent/.test(inline.nearbyText),
    false,
    `nearbyText 不应含 XML 片段：${inline.nearbyText}`,
  );
  assert.ok(inline.nearbyText.length <= 200, `nearbyText 应被截断，实际长度 ${inline.nearbyText.length}`);
});

test('章节标题取命中编号模式的最近段落', async () => {
  const filePath = writeFixture(buildMinimalDocx(), 'section-pattern.docx');
  const { occurrences } = await extractDocxVisualAssets(filePath);
  const inline = occurrences.find((item) => item.placement === 'inline');
  assert.strictEqual(inline.sectionTitle, '第一章 投标函');
});

test('章节标题优先取标题样式段落（即使文本没有编号）', async () => {
  const zip = withRewrittenDocumentXml(buildMinimalDocx(), (xml) => xml.replace(
    '<w:p><w:r><w:t>第一章 投标函</w:t></w:r></w:p>',
    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>投标函</w:t></w:r></w:p>',
  ));
  const { occurrences } = await extractDocxVisualAssets(writeFixture(zip, 'section-style.docx'));
  const inline = occurrences.find((item) => item.placement === 'inline');
  assert.strictEqual(inline.sectionTitle, '投标函');
});

test('无标题样式也无编号段落时不返回正文句子', async () => {
  const zip = withRewrittenDocumentXml(buildMinimalDocx(), (xml) => xml.replace(
    '<w:p><w:r><w:t>第一章 投标函</w:t></w:r></w:p>',
    '<w:p><w:r><w:t>我方已认真阅读本项目招标文件的全部内容并愿意按照其要求参加本次投标活动</w:t></w:r></w:p>',
  ));
  const { occurrences } = await extractDocxVisualAssets(writeFixture(zip, 'section-none.docx'));
  const inline = occurrences.find((item) => item.placement === 'inline');
  assert.strictEqual(inline.sectionTitle, undefined, `不应把正文句子当章节：${inline.sectionTitle}`);
});

test('章节回溯不受 4000 字符距离限制', async () => {
  const filler = Array.from(
    { length: 60 },
    (_item, index) => `<w:p><w:r><w:t>填充段落${index}：本段用于拉长文档，说明我方完全响应招标文件的各项要求与条件。</w:t></w:r></w:p>`,
  ).join('');
  const zip = withRewrittenDocumentXml(buildMinimalDocx(), (xml) => xml
    .replace('<w:p><w:r><w:t>第一章 投标函</w:t></w:r></w:p>', '<w:p><w:r><w:t>第七章 售后服务</w:t></w:r></w:p>')
    .replace('<w:p><w:r><w:t>致：某某招标代理公司。我公司郑重承诺：</w:t></w:r></w:p>', `<w:p><w:r><w:t>致：某某招标代理公司。</w:t></w:r></w:p>${filler}`));
  const { occurrences } = await extractDocxVisualAssets(writeFixture(zip, 'section-far.docx'));
  const inline = occurrences.find((item) => item.placement === 'inline');
  assert.strictEqual(inline.sectionTitle, '第七章 售后服务');
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd client && node --test electron/services/docxVisualAssetExtractor.test.cjs`
Expected: 新增 5 个用例中至少 `nearbyText 是可读文本`、`无标题样式也无编号段落时不返回正文句子`、
`章节回溯不受 4000 字符距离限制` FAIL；既有 4 个用例 PASS

- [ ] **Step 3: 实现段落索引与新的两个提取函数**

在 `client/electron/services/docxVisualAssetExtractor.cjs` 里，把 `extractNearbyText` 与
`extractSectionTitle`（`:48-61`）整体替换为：

```js
const MAX_SECTION_TITLE_LENGTH = 40;
const MAX_NEARBY_TEXT_LENGTH = 200;
const NEARBY_PARAGRAPH_RADIUS = 2;

// 中文标书大量使用纯文本编号当章节，并不使用 Word 标题样式。
const CHAPTER_TEXT_PATTERNS = [
  /^第[一二三四五六七八九十百千0-9]+[章节篇]/,
  /^[一二三四五六七八九十]+、/,
  /^（[一二三四五六七八九十]+）/,
  /^\d+(\.\d+)*[\s、.]/,
];
const HEADING_STYLE_PATTERN = /^(heading\s*[1-9]|标题\s*[1-9一二三四五六七八九十])$/i;

// 一次性把 XML 切成段落数组并预计算特征，避免每张图片都重扫全文。
function buildParagraphIndex(xml) {
  const source = String(xml || '');
  const paragraphs = [];
  const positions = [];
  const paragraphRe = /<w:p\b[^>]*>[\s\S]*?<\/w:p>/g;
  let match = paragraphRe.exec(source);
  while (match) {
    const block = match[0];
    const styleMatch = /<w:pStyle\b[^>]*w:val="([^"]+)"/.exec(block);
    paragraphs.push({
      text: stripXmlToText(block),
      styleVal: styleMatch ? styleMatch[1] : '',
      bold: /<w:b\b[^>]*\/?>/.test(block),
    });
    positions.push({ start: match.index, end: match.index + block.length });
    match = paragraphRe.exec(source);
  }
  return { paragraphs, positions };
}

function findParagraphPosition(positions, charIndex) {
  for (let index = positions.length - 1; index >= 0; index -= 1) {
    if (charIndex >= positions[index].start && charIndex < positions[index].end) return index;
  }
  return -1;
}

function isHeadingParagraph(paragraph) {
  if (!paragraph || !paragraph.text) return false;
  if (paragraph.styleVal && HEADING_STYLE_PATTERN.test(String(paragraph.styleVal).trim())) return true;
  if (paragraph.text.length > MAX_SECTION_TITLE_LENGTH) return false;
  return CHAPTER_TEXT_PATTERNS.some((pattern) => pattern.test(paragraph.text));
}

// 向前全篇回溯：先找标题样式/编号段落，再退一步找加粗短段落，都没有就返回 undefined。
function extractSectionTitle(paragraphs, paragraphPosition) {
  for (let index = paragraphPosition - 1; index >= 0; index -= 1) {
    if (!paragraphs[index]?.text) continue;
    if (isHeadingParagraph(paragraphs[index])) return paragraphs[index].text;
  }
  for (let index = paragraphPosition - 1; index >= 0; index -= 1) {
    const paragraph = paragraphs[index];
    if (!paragraph?.text || paragraph.text.length > MAX_SECTION_TITLE_LENGTH) continue;
    if (paragraph.bold) return paragraph.text;
  }
  return undefined;
}

// 取图片所在段落文本；图片独占段落时取前后各最多 2 个有文本的段落。
function extractNearbyText(paragraphs, paragraphPosition) {
  if (paragraphPosition < 0) return '';
  const own = paragraphs[paragraphPosition]?.text || '';
  if (own) return own.slice(0, MAX_NEARBY_TEXT_LENGTH);

  const parts = [];
  for (let offset = 1; offset <= NEARBY_PARAGRAPH_RADIUS; offset += 1) {
    const text = paragraphs[paragraphPosition - offset]?.text;
    if (text) parts.push(text);
  }
  for (let offset = 1; offset <= NEARBY_PARAGRAPH_RADIUS; offset += 1) {
    const text = paragraphs[paragraphPosition + offset]?.text;
    if (text) parts.push(text);
  }
  return parts.join(' ').slice(0, MAX_NEARBY_TEXT_LENGTH);
}
```

然后对两个 collect 函数做**三处外科式替换**（其余字段逻辑一律不动）：

**(1) `collectDrawingOccurrences`（`:68`）**

- 签名加参数：

```js
function collectDrawingOccurrences(xml, relationships, partLabel, paragraphIndex) {
  const { paragraphs, positions } = paragraphIndex;
  const occurrences = [];
```

- 把这两行（`:95-96`）：

```js
      nearbyText: extractNearbyText(xml, drawing.index),
      sectionTitle: extractSectionTitle(xml, drawing.index),
```

替换为：

```js
      nearbyText: extractNearbyText(paragraphs, findParagraphPosition(positions, drawing.index)),
      sectionTitle: extractSectionTitle(paragraphs, findParagraphPosition(positions, drawing.index)),
```

**(2) `collectVmlOccurrences`（`:102`）**

- 签名加参数：

```js
function collectVmlOccurrences(xml, relationships, partLabel, paragraphIndex) {
  const { paragraphs, positions } = paragraphIndex;
  const occurrences = [];
```

- 把这两行（`:123-124`）：

```js
      nearbyText: extractNearbyText(xml, match.index),
      sectionTitle: extractSectionTitle(xml, match.index),
```

替换为：

```js
      nearbyText: extractNearbyText(paragraphs, findParagraphPosition(positions, match.index)),
      sectionTitle: extractSectionTitle(paragraphs, findParagraphPosition(positions, match.index)),
```

**(3) 调用处（`:153-157`）** 把：

```js
  const occurrences = [];
  for (const part of parts) {
    occurrences.push(...collectDrawingOccurrences(part.xml, part.rels, part.label));
    occurrences.push(...collectVmlOccurrences(part.xml, part.rels, part.label));
  }
```

替换为：

```js
  const occurrences = [];
  for (const part of parts) {
    const paragraphIndex = buildParagraphIndex(part.xml);
    occurrences.push(...collectDrawingOccurrences(part.xml, part.rels, part.label, paragraphIndex));
    occurrences.push(...collectVmlOccurrences(part.xml, part.rels, part.label, paragraphIndex));
  }
```

> 注意：`locationHint: partLabel` 保持不变；`mediaName`、`relationshipId`、`placement`、
> `width`/`height`（`EMU_PER_PX` / `PT_TO_EMU` 换算）与 `assetKind` 全部照旧，**不在本次改动范围**。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd client && node --test electron/services/docxVisualAssetExtractor.test.cjs`
Expected: PASS（既有 4 个 + 新增 5 个用例全绿）

- [ ] **Step 5: 语法检查与提交**

```bash
cd client && node --check electron/services/docxVisualAssetExtractor.cjs
cd C:/workspace/OpenBidKit_Yibiao
git add client/electron/services/docxVisualAssetExtractor.cjs client/electron/services/docxVisualAssetExtractor.test.cjs
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "fix: 视觉资产改为按段落提取章节与附近文本"
```

---

### Task 8: Excel 列出未能识别的图片

**Files:**
- Modify: `client/electron/services/checkResultExportService.cjs:282-300`
- Test: `client/electron/services/checkResultExportService.subject.test.cjs`

**Interfaces:**
- Consumes: `subjectCheckResult.stats.visionStats.unrecognized`（Task 6）
- Produces: 「投标主体」sheet 末尾的「未能识别的图片」区块；**sheet 名称与数量不变**

- [ ] **Step 1: 写失败的测试**

在 `client/electron/services/checkResultExportService.subject.test.cjs` 末尾追加：

```js
test('投标主体表列出未能识别的图片', () => {
  const state = {
    ...STATE,
    subjectCheckResult: {
      ...STATE.subjectCheckResult,
      stats: {
        ...STATE.subjectCheckResult.stats,
        visionStats: {
          ...STATE.subjectCheckResult.stats.visionStats,
          unrecognized: [
            {
              assetId: 'asset-1', bidDocumentId: 'bid-1', ordinal: 3, sectionTitle: '第三章 投标函',
              locationHint: '正文', mediaName: 'media/image3.tif', mimeType: 'image/tiff',
              format: 'tiff', label: 'TIFF', kind: 'skipped', reason: '图片格式不支持（TIFF），已跳过',
            },
            {
              assetId: 'asset-2', bidDocumentId: 'bid-1', ordinal: 5, sectionTitle: '',
              locationHint: '正文', mediaName: 'media/image5.webp', mimeType: 'image/webp',
              format: 'webp', label: 'WebP', kind: 'failed', reason: '图片转码失败',
            },
          ],
        },
      },
    },
  };

  const workbook = __test__.buildRejectionWorkbook(state, { subjectInputSignature: 'sig-subject-1' }, { exportedAt: new Date('2026-09-10T02:00:00.000Z') });
  assert.deepStrictEqual(workbook.SheetNames, ['检查概览', '废标项', '错别字', '逻辑问题', '投标主体']);

  const rows = XLSX.utils.sheet_to_json(workbook.Sheets['投标主体'], { header: 1 });
  const flat = rows.map((row) => row.join('|'));
  const titleIndex = flat.findIndex((line) => line.includes('未能识别的图片'));
  assert.ok(titleIndex >= 0, '应出现「未能识别的图片」区块');
  assert.deepStrictEqual(rows[titleIndex + 1], ['章节/位置', '序号', '媒体文件', '格式', '原因']);
  assert.ok(flat.some((line) => line.includes('第三章 投标函') && line.includes('第 3 张')
    && line.includes('media/image3.tif') && line.includes('TIFF') && line.includes('格式不支持')), flat.join('\n'));
  assert.ok(flat.some((line) => line.includes('第 5 张') && line.includes('WebP')
    && line.includes('图片转码失败')), flat.join('\n'));
});

test('无未能识别图片时不追加区块', () => {
  const workbook = __test__.buildRejectionWorkbook(STATE, { subjectInputSignature: 'sig-subject-1' }, { exportedAt: new Date('2026-09-10T02:00:00.000Z') });
  const rows = XLSX.utils.sheet_to_json(workbook.Sheets['投标主体'], { header: 1 });
  assert.strictEqual(rows.flat().join('|').includes('未能识别的图片'), false);
  assert.strictEqual(workbook.SheetNames.length, 5);
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd client && node --test electron/services/checkResultExportService.subject.test.cjs`
Expected: 第一个新用例 FAIL（找不到区块），第二个新用例 PASS

- [ ] **Step 3: 实现区块拼接**

在 `client/electron/services/checkResultExportService.cjs` 的 `appendWorksheet` 之后新增：

```js
// 「未能识别的图片」明细：解释「跳过 N 张 / 失败 N 张」到底卡在哪几张图上。
function buildUnrecognizedRows(subjectCheckResult) {
  const items = subjectCheckResult?.stats?.visionStats?.unrecognized;
  if (!Array.isArray(items) || !items.length) return [];
  return [
    [],
    ['未能识别的图片'],
    ['章节/位置', '序号', '媒体文件', '格式', '原因'],
    ...items.map((item) => [
      item.sectionTitle || item.locationHint || '正文',
      `第 ${Number(item.ordinal) || 0} 张`,
      item.mediaName || '',
      item.label || item.format || '',
      item.reason || '',
    ]),
  ];
}
```

把主体表的写入改为（`:300`）：

```js
  const subjectRows = [
    ['序号', '投标文件序号', '投标文件名', '来源', '正确主体', '检测主体', '级别', '位置类型', '位置', '原文或印章文字', '角色依据', '不一致原因', '置信度', '建议'],
    ...selected.subjectCheckResult.findings.map((finding, index) => [
      index + 1,
      ...getDocumentColumns(documentLookup, finding.bidDocumentId),
      finding.source === 'seal' ? '印章' : '正文',
      finding.expectedSubject,
      finding.detectedSubject,
      finding.level === 'confirmed-mismatch' ? '明确不一致' : '需人工复核',
      subjectLocationKindLabels[finding.locationKind] || finding.locationKind,
      finding.locationHint,
      finding.originalText,
      finding.roleReason,
      finding.mismatchReason,
      Number(finding.confidence || 0),
      finding.suggestion,
    ]),
    ...buildUnrecognizedRows(selected.subjectCheckResult),
  ];
```

（其余不变；`appendWorksheet(workbook, rejectionSheetNames[4], subjectRows, [...], 0)` 保持原样。）

- [ ] **Step 4: 运行测试确认通过**

Run: `cd client && node --test electron/services/checkResultExportService.subject.test.cjs`
Expected: PASS（既有 4 个 + 新增 2 个用例全绿）

- [ ] **Step 5: 语法检查与提交**

```bash
cd client && node --check electron/services/checkResultExportService.cjs
cd C:/workspace/OpenBidKit_Yibiao
git add client/electron/services/checkResultExportService.cjs client/electron/services/checkResultExportService.subject.test.cjs
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat: 导出 Excel 时列出未能识别的图片及其章节位置"
```

---

### Task 9: 全量验证

**Files:** 无源码改动（只跑验证并记录结论）

- [ ] **Step 1: 全部改动文件的语法检查**

```bash
cd C:/workspace/OpenBidKit_Yibiao/client
for file in \
  electron/utils/imageFormat.cjs \
  electron/utils/aiLog.cjs \
  electron/services/offscreenImageTranscoder.cjs \
  electron/services/aiService.cjs \
  electron/services/subjectVisionProvider.cjs \
  electron/services/subjectCheckTask.cjs \
  electron/services/rejectionCheckTask.cjs \
  electron/services/docxVisualAssetExtractor.cjs \
  electron/services/checkResultExportService.cjs ; do
  node --check "$file" || echo "FAIL: $file"
done
echo SYNTAX_DONE
```

Expected: 只输出 `SYNTAX_DONE`

- [ ] **Step 2: 定向单测**

```bash
cd C:/workspace/OpenBidKit_Yibiao/client
node --test \
  electron/utils/imageFormat.test.cjs \
  electron/utils/aiLog.test.cjs \
  electron/services/subjectVisionProvider.test.cjs \
  electron/services/subjectCheckTask.test.cjs \
  electron/services/docxVisualAssetExtractor.test.cjs \
  electron/services/checkResultExportService.subject.test.cjs
```

Expected: 全部 PASS

- [ ] **Step 3: 既有服务测试回归**

```bash
cd C:/workspace/OpenBidKit_Yibiao/client
node --test electron/services/*.test.cjs 2>&1 | grep "^not ok" || echo "无失败"
```

Expected: 只可能出现 `knowledgeBaseStore.search.test.cjs` 的既有失败（**与本改动无关，不要修**）。
若有**其它**失败，必须停下来排查。

- [ ] **Step 4: Electron native 冒烟与构建**

```bash
cd C:/workspace/OpenBidKit_Yibiao/client
npm run smoke:electron-native
CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=100000 npm run build 2>&1 | tail -6
```

Expected: 冒烟通过；构建通过（仅既有 chunk 体积警告）

- [ ] **Step 5: 记录结论**

把 Step 1–4 的**实际输出**记入完成报告；手动验收清单见规格 §7（需 GUI，由用户执行）。

- [ ] **Step 6: 提交（若有文档改动）**

```bash
cd C:/workspace/OpenBidKit_Yibiao
git status --short
```

Expected: 无源码改动；若有文档补充，按同样身份提交。

---

## 完成后的收尾

- 全量验证通过后，按 `subagent-driven-development` 的流程做一次**全分支审查**（base = 本计划首个提交之前）。
- 手动验收清单（规格 §7 共 12 条）需要 GUI，由用户执行；其中第 1、6、7、9、10、11、12 条是本计划的核心验收点。
- **计划外需注意**：`config.agent_mode_scenarios` 开关全仓无消费点（空转），属独立缺陷，不在本计划范围。




