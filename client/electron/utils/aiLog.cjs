const path = require('node:path');
const crypto = require('node:crypto');
const { getAiLogsDir } = require('./paths.cjs');
const { enqueueJsonReplace } = require('./silentFileLog.cjs');
const { logError } = require('./logger.cjs');

const MAX_AI_LOG_TITLE_LENGTH = 64;

function createAiRequestId() {
  return `${new Date().toISOString().replace(/[:.]/g, '-')}-${crypto.randomUUID()}`;
}

function sanitizeAiLogTitle(value) {
  return String(value || '')
    .replace(/[<>:"/\\|?*\x00-\x1F]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, MAX_AI_LOG_TITLE_LENGTH)
    .replace(/[. ]+$/g, '');
}

// 递归移除请求中的 Base64 图片正文，保留图片类型和消息结构用于诊断。
function sanitizeAiLogRequest(value) {
  if (typeof value === 'string') {
    const match = /^data:image\/[^;]+;base64,/i.exec(value);
    return match ? `${match[0]}[图片数据已省略]` : value;
  }
  if (Array.isArray(value)) {
    return value.map(sanitizeAiLogRequest);
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, sanitizeAiLogRequest(item)]));
  }
  return value;
}

function resolveAiLogTitle(request, fallback = '') {
  return sanitizeAiLogTitle(request?.logTitle || request?.log_title || request?.progressLabel || request?.schemaName || fallback);
}

function buildAiLogFileName(payload) {
  const requestId = String(payload.request_id || createAiRequestId()).trim();
  const logTitle = sanitizeAiLogTitle(payload.log_title);
  if (!logTitle) {
    return `${requestId}.json`;
  }

  const match = /^(.+)-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i.exec(requestId);
  if (match) {
    return `${match[1]}-${logTitle}-${match[2]}.json`;
  }
  return `${requestId}-${logTitle}.json`;
}

function writeAiLog(app, config, payload) {
  try {
    if (!config?.developer_mode) {
      return;
    }

    const logsDir = getAiLogsDir(app);
    const logTitle = sanitizeAiLogTitle(payload.log_title);
    const logPayload = {
      ...payload,
      ...(payload.request ? { request: sanitizeAiLogRequest(payload.request) } : {}),
      ...(logTitle ? { log_title: logTitle } : {}),
    };
    const fileName = buildAiLogFileName(logPayload);
    enqueueJsonReplace(path.join(logsDir, fileName), logPayload, {
      space: 2,
      removeOnFailure: logPayload.status !== 'pending',
    });
  } catch {
    // AI 请求日志不能影响模型调用。
  }
}

function getRawAiErrorResponse(error) {
  for (const key of ['raw_response_body', 'raw_response_payload', 'raw_response_data']) {
    if (Object.prototype.hasOwnProperty.call(error || {}, key)) {
      return error[key];
    }
  }
  return undefined;
}

function getAiErrorLogResponse(error, fallbackResponse) {
  const rawResponse = getRawAiErrorResponse(error);
  return rawResponse === undefined ? fallbackResponse : rawResponse;
}

function getAiErrorLogError(error, fallbackMessage) {
  const rawResponse = getRawAiErrorResponse(error);
  return rawResponse === undefined || rawResponse === '' ? fallbackMessage : rawResponse;
}

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

module.exports = {
  buildAiLogFileName,
  createAiRequestId,
  getAiErrorLogError,
  getAiErrorLogResponse,
  logAiFailure,
  resolveAiLogTitle,
  sanitizeAiLogTitle,
  writeAiLog,
};
