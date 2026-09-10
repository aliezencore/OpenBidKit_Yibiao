'use strict';

const test = require('node:test');
const assert = require('node:assert');
const {
  SEMANTIC_PRONOUNS,
  SUBJECT_CHECK_PROTOCOL_VERSION,
  normalizeSubjectName,
  matchesAllowedSubject,
  createSubjectCheckInputSignature,
  applySubjectAutoSelectRules,
  looksLikeCompanyName,
} = require('./subjectNameUtils.cjs');

// 两组互不相关的虚构主体，证明不存在特定公司硬编码。
const SUBJECT_A = '北方长风智能科技有限公司';
const SUBJECT_A_ALIAS = '北方长风';
const SUBJECT_B = '南屿云图数据服务有限责任公司';

test('语义代称包含规格要求的固定列表', () => {
  for (const pronoun of ['我公司', '本公司', '我方', '本投标人', '投标人', '应答人', '供应商', '响应人']) {
    assert.ok(SEMANTIC_PRONOUNS.includes(pronoun), `缺少 ${pronoun}`);
  }
});

test('normalizeSubjectName 去空白、统一全半角括号、大小写不敏感', () => {
  assert.strictEqual(normalizeSubjectName('  北方长风\n智能科技 有限公司 '), '北方长风智能科技有限公司');
  assert.strictEqual(normalizeSubjectName('南屿云图（上海）数据服务'), normalizeSubjectName('南屿云图(上海)数据服务'));
  assert.strictEqual(normalizeSubjectName('ABC Tech Co.'), normalizeSubjectName('abc tech co.'));
});

test('normalizeSubjectName 不得去除公司后缀或合并相似法人', () => {
  assert.notStrictEqual(normalizeSubjectName(SUBJECT_A), normalizeSubjectName('北方长风智能科技'));
  assert.notStrictEqual(normalizeSubjectName(SUBJECT_A), normalizeSubjectName(SUBJECT_B));
});

test('matchesAllowedSubject 命中正式名称与允许简称，拒绝相似名', () => {
  assert.strictEqual(matchesAllowedSubject(SUBJECT_A, SUBJECT_A, [SUBJECT_A_ALIAS]), true);
  assert.strictEqual(matchesAllowedSubject('北方长风', SUBJECT_A, [SUBJECT_A_ALIAS]), true);
  assert.strictEqual(matchesAllowedSubject('北方长风智能科技', SUBJECT_A, [SUBJECT_A_ALIAS]), false);
  assert.strictEqual(matchesAllowedSubject(SUBJECT_B, SUBJECT_A, [SUBJECT_A_ALIAS]), false);
  assert.strictEqual(matchesAllowedSubject('', SUBJECT_A, [SUBJECT_A_ALIAS]), false);
});

test('createSubjectCheckInputSignature 简称顺序无关、开关与版本参与签名', () => {
  const base = {
    bidSignature: 'bid-sig-1',
    officialName: SUBJECT_A,
    aliases: ['北方长风', '长风智能'],
    subjectSealCheck: true,
  };
  assert.strictEqual(
    createSubjectCheckInputSignature(base),
    createSubjectCheckInputSignature({ ...base, aliases: ['长风智能', '北方长风'] }),
  );
  assert.notStrictEqual(
    createSubjectCheckInputSignature(base),
    createSubjectCheckInputSignature({ ...base, subjectSealCheck: false }),
  );
  assert.notStrictEqual(
    createSubjectCheckInputSignature(base),
    createSubjectCheckInputSignature({ ...base, protocolVersion: SUBJECT_CHECK_PROTOCOL_VERSION + 1 }),
  );
  assert.notStrictEqual(
    createSubjectCheckInputSignature(base),
    createSubjectCheckInputSignature({ ...base, officialName: SUBJECT_B }),
  );
});

test('applySubjectAutoSelectRules 高置信且差距足够且带投标身份证据才预选', () => {
  const strong = { name: SUBJECT_A, confidence: 0.93, roleEvidence: '投标函落款：北方长风智能科技有限公司（盖章）' };
  assert.strictEqual(applySubjectAutoSelectRules([strong]), 0);
  const weaker = { name: SUBJECT_B, confidence: 0.6, roleEvidence: '' };
  assert.strictEqual(applySubjectAutoSelectRules([strong, weaker]), 0);
});

test('applySubjectAutoSelectRules 置信度不足、候选接近、无身份证据均不预选', () => {
  assert.strictEqual(applySubjectAutoSelectRules([{ name: '甲公司', confidence: 0.7, roleEvidence: 'x' }]), -1);
  assert.strictEqual(applySubjectAutoSelectRules([
    { name: '甲公司', confidence: 0.9, roleEvidence: 'x' },
    { name: '乙公司', confidence: 0.85, roleEvidence: 'y' },
  ]), -1);
  assert.strictEqual(applySubjectAutoSelectRules([{ name: '甲公司', confidence: 0.95, roleEvidence: '' }]), -1);
  assert.strictEqual(applySubjectAutoSelectRules([]), -1);
});

test('looksLikeCompanyName 识别具名机构并排除语义代称', () => {
  assert.strictEqual(looksLikeCompanyName(SUBJECT_A), true);
  assert.strictEqual(looksLikeCompanyName('某某设计研究院'), true);
  assert.strictEqual(looksLikeCompanyName('我公司'), false);
  assert.strictEqual(looksLikeCompanyName('投标人'), false);
  assert.strictEqual(looksLikeCompanyName(''), false);
});