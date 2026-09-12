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

// 近似命中判定（spec §7.4「相似但未经用户确认的名称不能自动通过」的对称应用）：
// 印章转写的高发错误是漏字、截断和夹带印鉴编号噪声（线上实测「江西博微新技术有限公司」
// 被读成「江西博微技术有限公司」，或转写尾部带着「3601081210231」这类防伪码）。
// 这里在归一化并去掉数字噪声后，按「互为子串或编辑距离 ≤2」判近似。
// 精确命中不算近似（那是 matchesAllowedSubject 的职责）；近似只应该把结论往
// 「需人工复核/再确认」方向降级，绝不用于自动放行。
const NEAR_MATCH_MAX_DISTANCE = 2;

function stripDigitNoise(value) {
  return normalizeSubjectName(value).replace(/\d+/g, '');
}

function levenshteinDistanceWithin(a, b, max) {
  if (a === b) return 0;
  if (Math.abs(a.length - b.length) > max) return max + 1;
  let previous = Array.from({ length: b.length + 1 }, (_value, index) => index);
  for (let i = 1; i <= a.length; i += 1) {
    const current = [i];
    for (let j = 1; j <= b.length; j += 1) {
      const substitutionCost = a[i - 1] === b[j - 1] ? 0 : 1;
      current[j] = Math.min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + substitutionCost);
    }
    previous = current;
  }
  return previous[b.length];
}

function isNearMatchSubjectName(value, officialName, aliases = []) {
  if (matchesAllowedSubject(value, officialName, aliases)) return false; // 精确命中由 matchesAllowedSubject 表达
  const detected = stripDigitNoise(value);
  if (!detected) return false;
  const allowed = [officialName, ...(Array.isArray(aliases) ? aliases : [])]
    .map(stripDigitNoise)
    .filter(Boolean);
  return allowed.some((name) => {
    if (name.includes(detected) || detected.includes(name)) return true;
    return levenshteinDistanceWithin(detected, name, NEAR_MATCH_MAX_DISTANCE) <= NEAR_MATCH_MAX_DISTANCE;
  });
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
  isNearMatchSubjectName,
  createSubjectCheckInputSignature,
  applySubjectAutoSelectRules,
  looksLikeCompanyName,
};