'use strict';

const test = require('node:test');
const assert = require('node:assert');
const XLSX = require('xlsx');
const { __test__ } = require('./checkResultExportService.cjs');

const SUBJECT_A = '北方长风智能科技有限公司';
const SUBJECT_B = '南屿云图数据服务有限责任公司';

const STATE = {
  bidDocuments: [{ id: 'bid-1', fileName: '投标文件一.docx' }],
  rejectionCheckResult: { status: 'idle', findings: [] },
  typoCheckResult: { status: 'idle', findings: [] },
  logicCheckResult: { status: 'idle', findings: [] },
  subjectCheckResult: {
    status: 'success',
    inputSignature: 'sig-subject-1',
    updatedAt: '2026-09-10T01:00:00.000Z',
    findings: [
      {
        id: 'subject-text-1', bidDocumentId: 'bid-1', level: 'confirmed-mismatch', source: 'text',
        expectedSubject: SUBJECT_A, detectedSubject: SUBJECT_B, locationKind: 'signature',
        title: '落款主体错误', summary: '投标函落款为其他公司', locationHint: '投标函落款',
        originalText: `投标人：${SUBJECT_B}（盖章）`, roleReason: '投标函落款应由投标方签署',
        mismatchReason: '与正式投标主体不一致', suggestion: '改为投标方名称并加盖公章', confidence: 0.92,
      },
      {
        id: 'subject-seal-2', bidDocumentId: 'bid-1', level: 'needs-review', source: 'seal',
        expectedSubject: SUBJECT_A, detectedSubject: '南屿云图数据服务有限', locationKind: 'standalone-image',
        title: '图片印章主体异常', summary: '印章识别结果不清晰', locationHint: '授权委托书',
        originalText: '南屿云图数据服务有限', roleReason: '投标方盖章位置', mismatchReason: '印章文字不完整',
        suggestion: '人工核对印章', confidence: 0.6, visualAssetId: 'asset-9', assetRelativePath: 'h/h.png',
      },
    ],
    stats: {
      visionStatus: 'partial',
      visionStats: { totalAssets: 6, candidateAssets: 4, inspectedAssets: 3, sealAssets: 2, skippedAssets: 1, reviewAssets: 1, failedAssets: 1 },
    },
  },
};

test('subject 结果纳入可导出判定', () => {
  const selected = __test__.selectCurrentRejectionResults(STATE, { subjectInputSignature: 'sig-subject-1' });
  assert.strictEqual(selected.subjectCheckResult.valid, true);
  assert.strictEqual(selected.exportable, true);
  const stale = __test__.selectCurrentRejectionResults(STATE, { subjectInputSignature: 'sig-other' });
  assert.strictEqual(stale.subjectCheckResult.valid, false);
});

test('工作簿包含五张表且投标主体表内容正确', () => {
  const workbook = __test__.buildRejectionWorkbook(STATE, { subjectInputSignature: 'sig-subject-1' }, { exportedAt: new Date('2026-09-10T02:00:00.000Z') });
  assert.deepStrictEqual(workbook.SheetNames, ['检查概览', '废标项', '错别字', '逻辑问题', '投标主体']);

  const overview = XLSX.utils.sheet_to_json(workbook.Sheets['检查概览'], { header: 1 });
  const flatOverview = overview.flat().join('|');
  assert.ok(flatOverview.includes('主体一致性检查'), '概览应包含主体检查行');

  const rows = XLSX.utils.sheet_to_json(workbook.Sheets['投标主体'], { header: 1 });
  assert.strictEqual(rows[0][0], '序号');
  assert.ok(rows[0].includes('正确主体'));
  assert.ok(rows[0].includes('检测主体'));
  assert.strictEqual(rows.length, 3);
  assert.strictEqual(rows[1][4], SUBJECT_A);
  assert.strictEqual(rows[1][5], SUBJECT_B);
  assert.strictEqual(rows[2][3], '印章');
});

test('签名不匹配时投标主体表只保留表头', () => {
  const workbook = __test__.buildRejectionWorkbook(STATE, { subjectInputSignature: 'sig-stale' }, { exportedAt: new Date() });
  const rows = XLSX.utils.sheet_to_json(workbook.Sheets['投标主体'], { header: 1 });
  assert.strictEqual(rows.length, 1);
});

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
