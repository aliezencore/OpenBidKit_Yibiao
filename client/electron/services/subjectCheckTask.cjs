'use strict';

const {
  SEMANTIC_PRONOUNS,
  normalizeSubjectName,
  applySubjectAutoSelectRules,
  matchesAllowedSubject,
  looksLikeCompanyName,
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

const ROLLING_SEGMENT_CHARS = 12000;
const FINAL_CANDIDATE_BATCH_SIZE = 12;

function chunkItems(items, batchSize) {
  const result = [];
  const source = Array.isArray(items) ? items : [];
  for (let index = 0; index < source.length; index += batchSize) result.push(source.slice(index, index + batchSize));
  return result;
}

function createEmptySubjectBodyState() {
  return {
    subjectSummary: [],
    roleSummary: [],
    candidates: [],
    candidateKeys: new Set(),
  };
}

function createSubjectCandidateKey(candidate) {
  return [
    String(candidate.bidDocumentId || ''),
    String(candidate.locationKind || ''),
    normalizeSubjectName(candidate.detectedSubject),
    String(candidate.originalText || '').replace(/\s+/g, ''),
  ].join('\u0000');
}

function applySubjectBodyPatch(state, patch) {
  const next = {
    subjectSummary: [...state.subjectSummary],
    roleSummary: [...state.roleSummary],
    candidates: [...state.candidates],
    candidateKeys: new Set(state.candidateKeys),
  };
  for (const item of Array.isArray(patch?.subject_summary) ? patch.subject_summary : []) {
    const text = String(item || '').trim();
    if (text && !next.subjectSummary.includes(text)) next.subjectSummary.push(text);
  }
  for (const item of Array.isArray(patch?.role_summary) ? patch.role_summary : []) {
    const text = String(item || '').trim();
    if (text && !next.roleSummary.includes(text)) next.roleSummary.push(text);
  }
  for (const raw of Array.isArray(patch?.candidates) ? patch.candidates : []) {
    const candidate = {
      bidDocumentId: String(raw?.bid_document_id || ''),
      role: ['bidder', 'third-party', 'unclear'].includes(raw?.role) ? raw.role : 'unclear',
      detectedSubject: String(raw?.detected_subject || ''),
      locationKind: ['body', 'table', 'signature'].includes(raw?.location_kind) ? raw.location_kind : 'body',
      title: String(raw?.title || ''),
      summary: String(raw?.summary || ''),
      locationHint: String(raw?.location_hint || ''),
      originalText: String(raw?.original_text || ''),
      roleReason: String(raw?.role_reason || ''),
      mismatchReason: String(raw?.mismatch_reason || ''),
      suggestion: String(raw?.suggestion || ''),
      nameConfidence: Math.max(0, Math.min(1, Number(raw?.name_confidence || 0))),
      roleConfidence: Math.max(0, Math.min(1, Number(raw?.role_confidence || 0))),
    };
    const key = createSubjectCandidateKey(candidate);
    if (next.candidateKeys.has(key)) continue;
    next.candidateKeys.add(key);
    next.candidates.push(candidate);
  }
  return next;
}

function createSubjectBodyFinalCandidates(state) {
  const seen = new Set();
  const result = [];
  for (const candidate of state.candidates) {
    const key = createSubjectCandidateKey(candidate);
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(candidate);
  }
  return result;
}

function createSubjectBodyStateSummary(state) {
  return {
    subject_summary: state.subjectSummary.slice(-40),
    role_summary: state.roleSummary.slice(-40),
    candidate_count: state.candidates.length,
  };
}

function buildSubjectBodySegmentMessages({ bidDocuments, segment, stateSummary, officialName, aliases }) {
  const document = bidDocuments.find((item) => item.id === segment.documentId);
  return [
    {
      role: 'user',
      content: `【投标主体一致性滚动审阅｜已发现状态】
正式投标主体：${officialName}
允许简称：${aliases.join('、') || '（无）'}
语义代称：${SEMANTIC_PRONOUNS.join('、')}

以下是 Main 侧维护的已发现主体与角色摘要，用于跨片段去重和避免早期信息丢失：
${JSON.stringify(stateSummary, null, 2)}`,
    },
    {
      role: 'user',
      content: `【投标主体一致性滚动审阅任务】
当前文件：${document?.fileName || segment.documentId}（bidDocumentId：${segment.documentId}）
当前片段：第 ${segment.segmentIndex}/${segment.totalSegments} 段

请通读本片段，找出所有可能出现的主体名称，并判断其在当前上下文中的角色：
- bidder：当前上下文代表投标方发言、承诺、声明、签署、落款或承担责任；
- third-party：招标人、代理机构、客户、制造商、认证机构、案例单位等第三方；
- unclear：无法确定。

规则：
1. 只有“我公司/本公司”等代称且没有出现其他具名公司时，不要生成候选。
2. 第三方角色不要生成候选，只在 role_summary 里记录，避免后续重复分析。
3. 不得因为片段里没有解释，就把第三方主体直接定为投标主体错误。
4. original_text 必须是本片段中逐字出现的原文摘录。

JSON 格式：{"subject_summary":["已确认的主体与证据"],"role_summary":["角色结论"],"candidates":[{"bid_document_id":"${segment.documentId}","role":"bidder","detected_subject":"检测到的公司名称","location_kind":"body","title":"不超过 24 字的标题","summary":"简要说明","location_hint":"位置线索","original_text":"原文摘录","role_reason":"角色判断依据","mismatch_reason":"不一致原因","suggestion":"修改建议","name_confidence":0.9,"role_confidence":0.9}]}

本片段正文：
${segment.text}`,
    },
  ];
}

function buildSubjectBodyFinalMessages({ officialName, aliases, candidates, stateSummary }) {
  return [
    {
      role: 'user',
      content: `【投标主体一致性定稿｜事实索引】
正式投标主体：${officialName}
允许简称：${aliases.join('、') || '（无）'}

Main 侧全量状态索引：
${JSON.stringify(stateSummary, null, 2)}`,
    },
    {
      role: 'user',
      content: `【投标主体一致性定稿任务】
以下 candidates 来自滚动审阅的初步结果。请只保留确实代表投标方身份、且与正式主体和允许简称均不匹配的问题，输出最终 JSON。

定稿规则：
1. 正式投标主体名称和允许简称出现在原文中属于正常，必须删除。
2. 第三方主体（招标人、代理机构、客户、制造商、认证机构、案例单位）不得输出。
3. 只有代称而没有错误具名主体的位置不得输出。
4. 相似但未经用户确认的公司名至少保留为需人工复核，不得直接丢弃。
5. bidDocumentId 必须来自上方有效列表，original_text 必须逐字来自对应原文。

待定稿 candidates：
${JSON.stringify(candidates, null, 2)}

JSON 格式：{"findings":[{"bid_document_id":"有效 bidDocumentId","role":"bidder","detected_subject":"检测到的公司名称","location_kind":"body","title":"不超过 24 字的标题","summary":"简要说明","location_hint":"位置线索","original_text":"原文摘录","role_reason":"角色判断依据","mismatch_reason":"不一致原因","suggestion":"修改建议","name_confidence":0.9,"role_confidence":0.9}]}`,
    },
  ];
}

function normalizeSubjectBodyFindings(payload, { bidDocuments, officialName, aliases }) {
  const documents = Array.isArray(bidDocuments) ? bidDocuments : [];
  const byId = new Map(documents.map((document) => [document.id, document]));
  const rawFindings = Array.isArray(payload?.findings) ? payload.findings : [];
  const seen = new Set();
  const findings = [];
  for (const raw of rawFindings) {
    const bidDocumentId = String(raw?.bid_document_id || '');
    const document = byId.get(bidDocumentId);
    if (!document) continue; // spec §8.4-1
    const originalText = String(raw?.original_text || '').trim();
    if (!originalText || !locateOriginalText(document.content, originalText)) continue; // spec §8.4-2
    const detectedSubject = String(raw?.detected_subject || '').trim();
    if (!detectedSubject || !originalText.includes(detectedSubject)) continue; // spec §8.4-3
    if (matchesAllowedSubject(detectedSubject, officialName, aliases)) continue; // spec §8.4-4
    const role = ['bidder', 'third-party', 'unclear'].includes(raw?.role) ? raw.role : 'unclear';
    if (role === 'third-party') continue; // spec §8.2
    if (role === 'unclear' && !looksLikeCompanyName(detectedSubject)) continue; // spec §8.2
    const nameConfidence = Math.max(0, Math.min(1, Number(raw?.name_confidence || 0)));
    const roleConfidence = Math.max(0, Math.min(1, Number(raw?.role_confidence || 0)));
    const locationKind = ['body', 'table', 'signature'].includes(raw?.location_kind) ? raw.location_kind : 'body';
    const key = [bidDocumentId, locationKind, normalizeSubjectName(detectedSubject), originalText.replace(/\s+/g, '')].join('\u0000');
    if (seen.has(key)) continue; // spec §8.4-5
    seen.add(key);
    // 明确不一致门槛（spec §10.1）：bidder + 双置信度 ≥0.85；否则降级为需人工复核。
    const level = role === 'bidder' && nameConfidence >= 0.85 && roleConfidence >= 0.85
      ? 'confirmed-mismatch'
      : 'needs-review';
    findings.push({
      id: `subject-text-${findings.length + 1}`,
      bidDocumentId,
      level,
      source: 'text',
      expectedSubject: officialName,
      detectedSubject,
      locationKind,
      title: String(raw?.title || '投标主体不一致'),
      summary: String(raw?.summary || ''),
      locationHint: String(raw?.location_hint || ''),
      originalText,
      roleReason: String(raw?.role_reason || ''),
      mismatchReason: String(raw?.mismatch_reason || ''),
      suggestion: String(raw?.suggestion || ''),
      confidence: Math.min(nameConfidence, roleConfidence),
    });
  }
  return findings;
}

async function runSubjectBodyCheck(aiService, { bidDocuments, officialName, aliases = [], semanticPronouns = SEMANTIC_PRONOUNS }, onProgress) {
  const documents = Array.isArray(bidDocuments) ? bidDocuments : [];
  const segments = [];
  for (const document of documents) {
    const text = String(document.content || '');
    const total = Math.max(1, Math.ceil(text.length / ROLLING_SEGMENT_CHARS));
    for (let index = 0; index < total; index += 1) {
      segments.push({
        documentId: document.id,
        documentLabel: document.fileName || document.id,
        segmentIndex: index + 1,
        totalSegments: total,
        text: text.slice(index * ROLLING_SEGMENT_CHARS, (index + 1) * ROLLING_SEGMENT_CHARS),
      });
    }
  }

  let state = createEmptySubjectBodyState();
  for (const segment of segments) {
    onProgress(`${segment.documentLabel}：正在审阅第 ${segment.segmentIndex}/${segment.totalSegments} 段。`);
    const payload = await aiService.collectJsonResponse({
      messages: buildSubjectBodySegmentMessages({
        bidDocuments: documents,
        segment,
        stateSummary: createSubjectBodyStateSummary(state),
        officialName,
        aliases,
      }),
      progressLabel: '投标主体一致性滚动审阅',
      failureMessage: '投标主体一致性审阅结果格式无效，请重新检查',
    });
    state = applySubjectBodyPatch(state, payload);
  }

  const candidates = createSubjectBodyFinalCandidates(state);
  if (!candidates.length) return [];

  onProgress('正在定稿投标主体一致性问题。');
  const findings = [];
  const batches = chunkItems(candidates, FINAL_CANDIDATE_BATCH_SIZE);
  for (const [batchIndex, batch] of batches.entries()) {
    onProgress(`正在定稿第 ${batchIndex + 1}/${batches.length} 批。`);
    const payload = await aiService.collectJsonResponse({
      messages: buildSubjectBodyFinalMessages({
        officialName,
        aliases,
        candidates: batch,
        stateSummary: createSubjectBodyStateSummary(state),
      }),
      progressLabel: '投标主体一致性定稿',
      failureMessage: '投标主体一致性定稿结果格式无效，请重新检查',
    });
    findings.push(...normalizeSubjectBodyFindings(payload, { bidDocuments: documents, officialName, aliases }));
  }

  const seen = new Set();
  return findings.filter((finding) => {
    const key = [finding.bidDocumentId, finding.locationKind, normalizeSubjectName(finding.detectedSubject), finding.originalText.replace(/\s+/g, '')].join('\u0000');
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
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
  runSubjectBodyCheck,
  normalizeSubjectBodyFindings,
  createEmptySubjectBodyState,
  applySubjectBodyPatch,
  createSubjectBodyFinalCandidates,
};