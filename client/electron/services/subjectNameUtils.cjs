'use strict';

// 固定语义代称（spec §7.3）：只帮助模型理解发言角色，不作为公司名称参与字符串比较。
const SEMANTIC_PRONOUNS = ['我公司', '本公司', '我方', '本投标人', '投标人', '应答人', '供应商', '响应人'];

const SUBJECT_CHECK_PROTOCOL_VERSION = 1;

const PRONOUN_SET = new Set(SEMANTIC_PRONOUNS);

// 允许的规范化（spec §7.4）：空白折叠、全半角括号/常见中文标点统一、英文小写。
function normalizeSubjectName(value) {
  return String(value || '')
    .replace(/\s+/g, '')
    .replace(/（/g, '(')
    .replace(/）/g, ')')
    .replace(/【/g, '[')
    .replace(/】/g, ']')
    .replace(/，/g, ',')
    .replace(/：/g, ':')
    .trim()
    .toLowerCase();
}

function matchesAllowedSubject(value, officialName, aliases = []) {
  const normalized = normalizeSubjectName(value);
  if (!normalized) return false;
  const allowed = [officialName, ...(Array.isArray(aliases) ? aliases : [])]
    .map(normalizeSubjectName)
    .filter(Boolean);
  return allowed.includes(normalized);
}

// 主体检查输入签名（spec §11.3）：全部投标文件签名 + 正式主体 + 排序后允许简称 + 印章开关 + 协议版本。
function createSubjectCheckInputSignature({
  bidSignature,
  officialName,
  aliases,
  subjectSealCheck,
  protocolVersion = SUBJECT_CHECK_PROTOCOL_VERSION,
}) {
  const sortedAliases = (Array.isArray(aliases) ? aliases : [])
    .map((item) => String(item || '').trim())
    .filter(Boolean)
    .sort()
    .map(normalizeSubjectName)
    .join('|');
  return [
    `yibiao-subject-check-v${protocolVersion}`,
    String(bidSignature || ''),
    normalizeSubjectName(officialName),
    sortedAliases,
    subjectSealCheck ? 'seal-on' : 'seal-off',
  ].join('\n---yibiao-subject-signature---\n');
}

// 自动预选门槛（spec §7.2）：置信度≥0.85、与次名差值≥0.10、有明确投标身份原文。
function applySubjectAutoSelectRules(candidates = []) {
  const list = (Array.isArray(candidates) ? candidates : []).filter((item) => item && String(item.name || '').trim());
  if (!list.length) return -1;
  const sorted = [...list].sort((a, b) => Number(b.confidence || 0) - Number(a.confidence || 0));
  const first = sorted[0];
  const second = sorted[1];
  if (Number(first.confidence || 0) < 0.85) return -1;
  if (second && Number(first.confidence || 0) - Number(second.confidence || 0) < 0.10) return -1;
  const hasBidderEvidence = Boolean(String(first.roleEvidence || '').trim() || String(first.originalText || '').trim());
  if (!hasBidderEvidence) return -1;
  return candidates.indexOf(first);
}

// 粗略判断是否是具名机构（用于 unclear 角色下过滤纯代称）：包含常见机构后缀即视为具名。
function looksLikeCompanyName(value) {
  const text = String(value || '').trim();
  if (!text || PRONOUN_SET.has(text)) return false;
  return /公司|集团|研究院|研究所|事务所|中心|院|所|局|处|队$/.test(text);
}

module.exports = {
  SEMANTIC_PRONOUNS,
  SUBJECT_CHECK_PROTOCOL_VERSION,
  normalizeSubjectName,
  matchesAllowedSubject,
  createSubjectCheckInputSignature,
  applySubjectAutoSelectRules,
  looksLikeCompanyName,
};