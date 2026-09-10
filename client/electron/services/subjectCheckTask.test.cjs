'use strict';

const test = require('node:test');
const assert = require('node:assert');
const {
  buildSubjectIdentificationMessages,
  normalizeSubjectCandidates,
  parseAliasesFromCandidates,
  runBidSubjectIdentificationTask,
} = require('./subjectCheckTask.cjs');

const SUBJECT_A = '北方长风智能科技有限公司';
const SUBJECT_B = '南屿云图数据服务有限责任公司';

const BID_DOCUMENTS = [
  { id: 'bid-1', fileName: '投标文件一.docx', content: `投标函\n致：某某招标代理公司\n投标人：${SUBJECT_A}（盖章）\n我公司郑重承诺遵守招标文件全部条款。` },
  { id: 'bid-2', fileName: '报价表.docx', content: `报价一览表\n投标人名称：${SUBJECT_A}\n含税总价：1234567.89 元` },
];

const IDENTIFICATION_CANDIDATE = {
  bid_document_id: 'bid-1',
  name: SUBJECT_A,
  confidence: 0.93,
  location_hint: '投标函落款',
  original_text: `投标人：${SUBJECT_A}（盖章）`,
  role_evidence: '投标函落款处标注为投标人',
  aliases: [],
};

function createIdentificationAiStub(captured, candidate = IDENTIFICATION_CANDIDATE) {
  return {
    collectJsonResponse: async ({ messages }) => {
      captured.messages = messages;
      return { candidates: [candidate] };
    },
  };
}

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

const {
  normalizeSubjectBodyFindings,
  createEmptySubjectBodyState,
  applySubjectBodyPatch,
  createSubjectBodyFinalCandidates,
} = require('./subjectCheckTask.cjs');

const BODY_CONTEXT = {
  bidDocuments: BID_DOCUMENTS,
  officialName: SUBJECT_A,
  aliases: ['北方长风'],
};

test('normalizeSubjectBodyFindings 保留 bidder 明确不一致并丢弃第三方', () => {
  // 自洽夹具：该投标正文包含 SUBJECT_B，使两条 finding 的 original_text 均可在文件中定位（spec §8.4-2）。
  const bodyContextWithB = {
    bidDocuments: [{ id: 'bid-1', fileName: '投标文件一.docx', content: `投标函\n致：${SUBJECT_B}（招标代理）\n投标人：${SUBJECT_B}（盖章）\n我公司郑重承诺遵守招标文件全部条款。` }],
    officialName: SUBJECT_A,
    aliases: ['北方长风'],
  };
  const payload = {
    findings: [
      {
        bid_document_id: 'bid-1', role: 'bidder', detected_subject: SUBJECT_B, location_kind: 'signature',
        title: '落款主体错误', summary: '投标函落款为其他公司', location_hint: '投标函落款',
        original_text: `投标人：${SUBJECT_B}（盖章）`, role_reason: '投标函落款处应由投标方签署',
        mismatch_reason: '落款主体与正式投标主体不一致', suggestion: '改为投标方名称并加盖投标方公章',
        name_confidence: 0.93, role_confidence: 0.92,
      },
      {
        bid_document_id: 'bid-1', role: 'third-party', detected_subject: SUBJECT_B, location_kind: 'body',
        title: '第三方出现', summary: '第三方', location_hint: '正文',
        original_text: `致：${SUBJECT_B}（招标代理）`, role_reason: '招标人',
        mismatch_reason: '不适用', suggestion: '无需修改', name_confidence: 0.95, role_confidence: 0.95,
      },
    ],
  };
  const findings = normalizeSubjectBodyFindings(payload, bodyContextWithB);
  assert.strictEqual(findings.length, 1);
  assert.strictEqual(findings[0].level, 'confirmed-mismatch');
  assert.strictEqual(findings[0].source, 'text');
  assert.strictEqual(findings[0].detectedSubject, SUBJECT_B);
});

test('normalizeSubjectBodyFindings 丢弃无法定位的原文（幻觉）与允许主体', () => {
  const payload = {
    findings: [
      {
        bid_document_id: 'bid-1', role: 'bidder', detected_subject: SUBJECT_B, location_kind: 'body',
        title: 'x', summary: 'x', location_hint: 'x', original_text: '这段文字不存在于文件中',
        role_reason: 'x', mismatch_reason: 'x', suggestion: 'x', name_confidence: 0.9, role_confidence: 0.9,
      },
      {
        bid_document_id: 'bid-1', role: 'bidder', detected_subject: SUBJECT_A, location_kind: 'body',
        title: 'y', summary: 'y', location_hint: 'y', original_text: `投标人：${SUBJECT_A}（盖章）`,
        role_reason: 'y', mismatch_reason: 'y', suggestion: 'y', name_confidence: 0.9, role_confidence: 0.9,
      },
      {
        bid_document_id: 'bid-1', role: 'bidder', detected_subject: '北方长风', location_kind: 'body',
        title: 'z', summary: 'z', location_hint: 'z', original_text: '北方长风出现在承诺函中',
        role_reason: 'z', mismatch_reason: 'z', suggestion: 'z', name_confidence: 0.9, role_confidence: 0.9,
      },
    ],
  };
  assert.deepStrictEqual(normalizeSubjectBodyFindings(payload, BODY_CONTEXT), []);
});

test('normalizeSubjectBodyFindings 置信度不足降级为 needs-review', () => {
  const payload = {
    findings: [{
      bid_document_id: 'bid-1', role: 'bidder', detected_subject: SUBJECT_B, location_kind: 'body',
      title: 't', summary: 's', location_hint: 'l', original_text: `投标人名称：${SUBJECT_B}`,
      role_reason: 'r', mismatch_reason: 'm', suggestion: 'g', name_confidence: 0.6, role_confidence: 0.9,
    }],
  };
  const findings = normalizeSubjectBodyFindings(payload, {
    ...BODY_CONTEXT,
    bidDocuments: [{ id: 'bid-1', fileName: '投标文件一.docx', content: `投标人名称：${SUBJECT_B}` }],
  });
  assert.strictEqual(findings.length, 1);
  assert.strictEqual(findings[0].level, 'needs-review');
});

test('normalizeSubjectBodyFindings 角色 unclear 且出现其他具名公司时进入 needs-review', () => {
  const findings = normalizeSubjectBodyFindings({
    findings: [{
      bid_document_id: 'bid-1', role: 'unclear', detected_subject: SUBJECT_B, location_kind: 'body',
      title: 't', summary: 's', location_hint: 'l', original_text: `合作单位：${SUBJECT_B}`,
      role_reason: 'r', mismatch_reason: 'm', suggestion: 'g', name_confidence: 0.9, role_confidence: 0.9,
    }],
  }, {
    ...BODY_CONTEXT,
    bidDocuments: [{ id: 'bid-1', fileName: '投标文件一.docx', content: `合作单位：${SUBJECT_B}` }],
  });
  assert.strictEqual(findings.length, 1);
  assert.strictEqual(findings[0].level, 'needs-review');
});

test('滚动状态机累积候选并在定稿时按位置去重', () => {
  let state = createEmptySubjectBodyState();
  state = applySubjectBodyPatch(state, {
    subject_summary: [`投标方：${SUBJECT_A}`],
    role_summary: ['bidder=投标函'],
    candidates: [{
      bid_document_id: 'bid-1', role: 'bidder', detected_subject: SUBJECT_B, location_kind: 'signature',
      title: 't', summary: 's', location_hint: 'l', original_text: `投标人：${SUBJECT_B}`,
      role_reason: 'r', mismatch_reason: 'm', suggestion: 'g', name_confidence: 0.9, role_confidence: 0.9,
    }],
  });
  state = applySubjectBodyPatch(state, {
    subject_summary: [],
    role_summary: [],
    candidates: [{
      bid_document_id: 'bid-1', role: 'bidder', detected_subject: SUBJECT_B, location_kind: 'signature',
      title: 't2', summary: 's2', location_hint: 'l', original_text: `投标人：${SUBJECT_B}`,
      role_reason: 'r', mismatch_reason: 'm', suggestion: 'g', name_confidence: 0.9, role_confidence: 0.9,
    }],
  });
  const candidates = createSubjectBodyFinalCandidates(state);
  assert.strictEqual(candidates.length, 1);
  assert.strictEqual(candidates[0].detectedSubject, SUBJECT_B);
});

const {
  classifySealFinding,
  createEmptyVisionStats,
} = require('./subjectCheckTask.cjs');

const ASSET_BASE = {
  id: 'asset-1',
  bidDocumentId: 'bid-1',
  relativePath: 'hash-1/hash-1.png',
  assetKind: 'standalone',
  locationHint: '正文',
  nearbyText: '投标人：（盖章）',
  sectionTitle: '投标函',
};

const SEAL_CONTEXT = { officialName: SUBJECT_A, aliases: ['北方长风'] };

test('createEmptyVisionStats 返回规格定义的统计字段', () => {
  assert.deepStrictEqual(createEmptyVisionStats(), {
    totalAssets: 0,
    candidateAssets: 0,
    inspectedAssets: 0,
    sealAssets: 0,
    skippedAssets: 0,
    reviewAssets: 0,
    failedAssets: 0,
  });
});

test('classifySealFinding 投标方位置出现其他公司清晰印章判为明确不一致', () => {
  const finding = classifySealFinding(ASSET_BASE, {
    status: 'inspected',
    hasSeal: true,
    sealText: SUBJECT_B,
    detectedSubject: SUBJECT_B,
    pageRole: 'bidder',
    sealRole: 'bidder',
    nameConfidence: 0.93,
    roleConfidence: 0.91,
    reason: '投标函盖章处',
  }, SEAL_CONTEXT);
  assert.strictEqual(finding.level, 'confirmed-mismatch');
  assert.strictEqual(finding.source, 'seal');
  assert.strictEqual(finding.locationKind, 'standalone-image');
  assert.strictEqual(finding.visualAssetId, 'asset-1');
  assert.strictEqual(finding.assetRelativePath, 'hash-1/hash-1.png');
});

test('classifySealFinding 合法第三方印章不报警', () => {
  const finding = classifySealFinding(ASSET_BASE, {
    status: 'inspected',
    hasSeal: true,
    sealText: '某某认证中心',
    detectedSubject: '某某认证中心',
    pageRole: 'third-party',
    sealRole: 'third-party',
    nameConfidence: 0.95,
    roleConfidence: 0.95,
  }, SEAL_CONTEXT);
  assert.strictEqual(finding, null);
});

test('classifySealFinding 投标方自身印章不报警，模糊印章降级为需人工复核', () => {
  assert.strictEqual(classifySealFinding(ASSET_BASE, {
    status: 'inspected', hasSeal: true, sealText: SUBJECT_A, detectedSubject: SUBJECT_A,
    pageRole: 'bidder', sealRole: 'bidder', nameConfidence: 0.95, roleConfidence: 0.95,
  }, SEAL_CONTEXT), null);

  const fuzzy = classifySealFinding(ASSET_BASE, {
    status: 'inspected', hasSeal: true, sealText: '南屿云图数据服务有限', detectedSubject: '南屿云图数据服务有限',
    pageRole: 'bidder', sealRole: 'bidder', nameConfidence: 0.6, roleConfidence: 0.6,
  }, SEAL_CONTEXT);
  assert.strictEqual(fuzzy.level, 'needs-review');
});

test('classifySealFinding 跳过与失败资产不生成结果', () => {
  assert.strictEqual(classifySealFinding(ASSET_BASE, { status: 'skipped', skipReason: '产品截图', hasSeal: false }, SEAL_CONTEXT), null);
  assert.strictEqual(classifySealFinding(ASSET_BASE, { status: 'failed', skipReason: '识别失败', hasSeal: false }, SEAL_CONTEXT), null);
});

test('识别范围收窄时只把指定文档发给模型', async () => {
  const captured = {};
  await runBidSubjectIdentificationTask({
    aiService: createIdentificationAiStub(captured),
    checkpointTask: () => {},
    payload: { bidDocumentIds: ['bid-1'], workspaceState: { bidDocuments: BID_DOCUMENTS, bidSignature: 'sig-1' } },
    previousState: {},
  });
  const text = captured.messages.map((message) => message.content).join('\n');
  assert.ok(text.includes('投标文件一.docx'), '应包含选中的文档');
  assert.ok(!text.includes('报价表.docx'), '不应包含范围外的文档');
});

test('未指定识别范围时仍扫描全部投标文件', async () => {
  const captured = {};
  await runBidSubjectIdentificationTask({
    aiService: createIdentificationAiStub(captured),
    checkpointTask: () => {},
    payload: { workspaceState: { bidDocuments: BID_DOCUMENTS, bidSignature: 'sig-1' } },
    previousState: {},
  });
  const text = captured.messages.map((message) => message.content).join('\n');
  assert.ok(text.includes('投标文件一.docx'));
  assert.ok(text.includes('报价表.docx'));
});

test('识别提示词不再声明扫描全部投标文件', () => {
  const messages = buildSubjectIdentificationMessages({ bidDocuments: BID_DOCUMENTS });
  const text = messages.map((message) => message.content).join('\n');
  assert.ok(text.includes('请扫描以下投标文件'));
  assert.ok(!text.includes('请扫描全部投标文件'));
});

test('识别范围外的候选会被丢弃', async () => {
  const captured = {};
  await assert.rejects(
    () => runBidSubjectIdentificationTask({
      aiService: createIdentificationAiStub(captured, {
        ...IDENTIFICATION_CANDIDATE,
        bid_document_id: 'bid-2',
        // 原文必须能在 bid-2 中定位，否则候选会先因"原文无法定位"被丢弃，测不出范围过滤。
        original_text: `投标人名称：${SUBJECT_A}`,
      }),
      checkpointTask: () => {},
      payload: { bidDocumentIds: ['bid-1'], workspaceState: { bidDocuments: BID_DOCUMENTS, bidSignature: 'sig-1' } },
      previousState: {},
    }),
    /未能从投标文件中识别出投标主体/,
  );
});
