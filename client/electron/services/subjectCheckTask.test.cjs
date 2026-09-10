'use strict';

const test = require('node:test');
const assert = require('node:assert');
const {
  buildSubjectIdentificationMessages,
  normalizeSubjectCandidates,
  parseAliasesFromCandidates,
} = require('./subjectCheckTask.cjs');

const SUBJECT_A = '北方长风智能科技有限公司';
const SUBJECT_B = '南屿云图数据服务有限责任公司';

const BID_DOCUMENTS = [
  { id: 'bid-1', fileName: '投标文件一.docx', content: `投标函\n致：某某招标代理公司\n投标人：${SUBJECT_A}（盖章）\n我公司郑重承诺遵守招标文件全部条款。` },
  { id: 'bid-2', fileName: '报价表.docx', content: `报价一览表\n投标人名称：${SUBJECT_A}\n含税总价：1234567.89 元` },
];

test('识别提示词包含语义代称说明与有效 bidDocumentId 列表', () => {
  const messages = buildSubjectIdentificationMessages({ bidDocuments: BID_DOCUMENTS });
  const text = messages.map((message) => typeof message.content === 'string' ? message.content : '').join('\n');
  assert.ok(text.includes('bid-1'));
  assert.ok(text.includes('bid-2'));
  assert.ok(text.includes('我公司'), '应包含语义代称说明');
  assert.ok(text.includes('投标函') || text.includes('投标人'), '应提示重点区域');
  assert.ok(/JSON/.test(text), '应给出 JSON 输出格式');
});

test('normalizeSubjectCandidates 丢弃不属于当前输入的 bidDocumentId', () => {
  const payload = {
    candidates: [
      { bid_document_id: 'bid-1', name: SUBJECT_A, confidence: 0.93, location_hint: '投标函落款', original_text: `投标人：${SUBJECT_A}`, role_evidence: '投标函明确投标人' },
      { bid_document_id: 'bid-404', name: SUBJECT_B, confidence: 0.99, location_hint: '不存在', original_text: 'x', role_evidence: 'x' },
    ],
  };
  const candidates = normalizeSubjectCandidates(payload, BID_DOCUMENTS);
  assert.strictEqual(candidates.length, 1);
  assert.strictEqual(candidates[0].name, SUBJECT_A);
});

test('normalizeSubjectCandidates 丢弃原文摘录无法定位的候选', () => {
  const payload = {
    candidates: [
      { bid_document_id: 'bid-1', name: SUBJECT_A, confidence: 0.9, location_hint: '', original_text: '这段原文在文件里根本不存在', role_evidence: 'x' },
    ],
  };
  assert.deepStrictEqual(normalizeSubjectCandidates(payload, BID_DOCUMENTS), []);
});

test('normalizeSubjectCandidates 忽略空白差异定位原文并合并重复候选', () => {
  const payload = {
    candidates: [
      { bid_document_id: 'bid-1', name: SUBJECT_A, confidence: 0.93, location_hint: '投标函', original_text: '投标人：北方长风智能科技有限公司（盖章）', role_evidence: '投标函' },
      { bid_document_id: 'bid-1', name: SUBJECT_A, confidence: 0.8, location_hint: '投标函', original_text: '投标人：北方长风智能科技有限公司（盖章）', role_evidence: '重复' },
    ],
  };
  const candidates = normalizeSubjectCandidates(payload, BID_DOCUMENTS);
  assert.strictEqual(candidates.length, 1);
  assert.strictEqual(candidates[0].confidence, 0.93);
});

test('parseAliasesFromCandidates 汇总去重简称并剔除与正式名相同的项', () => {
  const aliases = parseAliasesFromCandidates([
    { name: SUBJECT_A, aliases: ['北方长风', '长风智能'] },
    { name: SUBJECT_A, aliases: ['北方长风', SUBJECT_A] },
    { name: SUBJECT_B, aliases: ['南屿云图'] },
  ]);
  assert.deepStrictEqual(aliases.sort(), ['北方长风', '南屿云图', '长风智能'].sort());
});