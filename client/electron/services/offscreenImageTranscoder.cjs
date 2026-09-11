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
