'use strict';

const {
  SEMANTIC_PRONOUNS,
  normalizeSubjectName,
  applySubjectAutoSelectRules,
} = require('./subjectNameUtils.cjs');

const IDENTIFICATION_SEGMENT_CHARS = 12000;
const BID_DOCUMENT_MARKER = '\n---yibiao-subject-bid-document---\n';

function now() {
  return new Date().toISOString();
}

function formatBidDocumentIdList(bidDocuments) {
  return (Array.isArray(bidDocuments) ? bidDocuments : [])
    .map((document) => `- ${document.id}（${document.fileName}）`)
    .join('\n');
}

function chunkContent(content, limit) {
  const text = String(content || '');
  const segments = [];
  for (let index = 0; index < text.length; index += limit) {
    segments.push({ start: index, text: text.slice(index, index + limit) });
  }
  return segments.length ? segments : [{ start: 0, text: '' }];
}

function buildSubjectIdentificationMessages({ bidDocuments }) {
  const documents = Array.isArray(bidDocuments) ? bidDocuments : [];
  const idList = formatBidDocumentIdList(documents);
  const documentBlocks = documents.map((document) => {
    const segments = chunkContent(document.content, IDENTIFICATION_SEGMENT_CHARS);
    const segmentText = segments.map((segment, index) => `【${document.fileName} 第 ${index + 1}/${segments.length} 段】\n${segment.text}`).join('\n');
    return `【投标文件】bidDocumentId：${document.id}\n文件名：${document.fileName}\n${segmentText}`;
  }).join(BID_DOCUMENT_MARKER);

  return [
    {
      role: 'user',
      content: `【投标主体候选识别任务】
本投标包有效 bidDocumentId：
${idList}

语义代称说明：文档中出现的“${SEMANTIC_PRONOUNS.join('、')}”等代称，语义上代表投标方发言、承诺、声明、签署或盖章。请据此定位真正的投标主体，不要把这些代称本身当作公司名称。

请扫描全部投标文件，重点但不仅限于以下区域：封面与文档标题、投标函/响应函、投标人/应答人/供应商字段、报价表、授权委托书、法定代表人声明、投标方承诺函、签字盖章附近文字，以及高频“我公司/本公司”对应的具名主体。

输出要求：
1. 每个候选必须给出 bidDocumentId（必须来自上方有效列表）、候选公司全称、confidence（0~1）、location_hint（位置线索）、original_text（可定位的原文摘录，必须逐字来自文件）、role_evidence（为什么判断它是投标方）。
2. aliases 只填写文中确实出现、可安全视为该主体简称的短名称，不要臆造。
3. 不得把招标人、代理机构、客户、制造商、认证机构、案例单位列为候选。

JSON 格式：{"candidates":[{"bid_document_id":"有效 bidDocumentId","name":"候选公司全称","confidence":0.93,"location_hint":"投标函落款","original_text":"原文摘录","role_evidence":"判断依据","aliases":["简称"]}]}

待分析投标文件：
${documentBlocks}`,
    },
  ];
}

function locateOriginalText(content, excerpt) {
  const source = String(content || '');
  const target = String(excerpt || '');
  if (!source || !target) return false;
  if (source.includes(target)) return true;
  // 忽略空白差异的定位（spec §8.4）。
  const compactSource = source.replace(/\s+/g, '');
  const compactTarget = target.replace(/\s+/g, '');
  return Boolean(compactTarget) && compactSource.includes(compactTarget);
}

function normalizeSubjectCandidates(payload, bidDocuments) {
  const documents = Array.isArray(bidDocuments) ? bidDocuments : [];
  const byId = new Map(documents.map((document) => [document.id, document]));
  const rawCandidates = Array.isArray(payload?.candidates) ? payload.candidates : [];
  const merged = new Map();
  for (const item of rawCandidates) {
    const bidDocumentId = String(item?.bid_document_id || '');
    const document = byId.get(bidDocumentId);
    if (!document) continue;
    const name = String(item?.name || '').trim();
    if (!name) continue;
    const originalText = String(item?.original_text || '').trim();
    if (!originalText || !locateOriginalText(document.content, originalText)) continue;
    const key = `${bidDocumentId}\u0000${normalizeSubjectName(name)}`;
    const candidate = {
      bidDocumentId,
      name,
      confidence: Math.max(0, Math.min(1, Number(item?.confidence || 0))),
      locationHint: String(item?.location_hint || ''),
      originalText,
      roleEvidence: String(item?.role_evidence || ''),
      aliases: (Array.isArray(item?.aliases) ? item.aliases : [])
        .map((alias) => String(alias || '').trim())
        .filter((alias) => alias && normalizeSubjectName(alias) !== normalizeSubjectName(name)),
    };
    const existing = merged.get(key);
    if (!existing || candidate.confidence > existing.confidence) merged.set(key, candidate);
  }
  return [...merged.values()].sort((a, b) => b.confidence - a.confidence);
}

function parseAliasesFromCandidates(candidates = []) {
  const seen = new Set();
  const result = [];
  for (const candidate of Array.isArray(candidates) ? candidates : []) {
    const nameKey = normalizeSubjectName(candidate?.name);
    for (const alias of Array.isArray(candidate?.aliases) ? candidate.aliases : []) {
      const trimmed = String(alias || '').trim();
      const key = normalizeSubjectName(trimmed);
      if (!key || key === nameKey || seen.has(key)) continue;
      seen.add(key);
      result.push(trimmed);
    }
  }
  return result;
}

// 合并同名候选的简称（同一主体可能在多份文件里各给了一次简称）。
function mergeCandidatesByName(candidates = []) {
  const byName = new Map();
  for (const candidate of candidates) {
    const key = normalizeSubjectName(candidate.name);
    const existing = byName.get(key);
    if (!existing) {
      byName.set(key, { ...candidate, aliases: [...(candidate.aliases || [])] });
      continue;
    }
    existing.aliases = [...new Set([...existing.aliases, ...(candidate.aliases || [])])];
    if (candidate.confidence > existing.confidence) {
      Object.assign(existing, { ...candidate, aliases: existing.aliases });
    }
  }
  return [...byName.values()].sort((a, b) => b.confidence - a.confidence);
}

async function runBidSubjectIdentificationTask({ aiService, checkpointTask, payload, previousState }) {
  const state = { ...(previousState || {}), ...(payload?.workspaceState || {}) };
  const bidDocuments = (Array.isArray(state.bidDocuments) ? state.bidDocuments : [])
    .map((document) => ({ ...document, content: String(document.content || '') }))
    .filter((document) => document.id && document.content.trim());
  if (!bidDocuments.length) throw new Error('缺少投标文件内容，无法识别投标主体');

  const bidSignature = String(payload?.workspaceState?.bidSignature || '');

  checkpointTask({ status: 'running', progress: 10, logs: ['正在识别投标主体候选。'] }, {
    subjectProfile: {
      status: 'identifying',
      officialName: '',
      aliases: [],
      candidates: [],
      bidSignature,
      updatedAt: now(),
    },
  });

  const response = await aiService.collectJsonResponse({
    messages: buildSubjectIdentificationMessages({ bidDocuments }),
    progressLabel: '投标主体候选识别',
    failureMessage: '投标主体候选识别结果格式无效，请重新识别',
    progressCallback: (message) => {
      checkpointTask({ status: 'running', progress: 45, logs: [`投标主体候选识别：${message}`] }, {});
    },
  });

  const candidates = mergeCandidatesByName(normalizeSubjectCandidates(response, bidDocuments));
  if (!candidates.length) throw new Error('未能从投标文件中识别出投标主体，请手工填写');

  const preselectIndex = applySubjectAutoSelectRules(candidates);
  const aliases = parseAliasesFromCandidates(candidates);

  checkpointTask({ status: 'success', progress: 100, logs: [`投标主体候选识别完成，共 ${candidates.length} 个候选。`] }, {
    subjectProfile: {
      status: 'pending-confirmation',
      officialName: preselectIndex >= 0 ? candidates[preselectIndex].name : '',
      aliases: preselectIndex >= 0 ? aliases : [],
      candidates,
      preselectIndex,
      bidSignature,
      updatedAt: now(),
    },
  });

  return { candidates, preselectIndex, aliases };
}

module.exports = {
  runBidSubjectIdentificationTask,
  buildSubjectIdentificationMessages,
  normalizeSubjectCandidates,
  parseAliasesFromCandidates,
  mergeCandidatesByName,
};