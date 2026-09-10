'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { createOnlineVlmProvider } = require('./subjectVisionProvider.cjs');

function createFakeAiService({ multimodalEnabled = true, responses = [] } = {}) {
  const calls = [];
  let index = 0;
  return {
    calls,
    getConfig: () => ({ multimodal_enabled: multimodalEnabled }),
    collectJsonResponse: async (request) => {
      calls.push(request);
      const response = responses[Math.min(index, responses.length - 1)];
      index += 1;
      if (response instanceof Error) throw response;
      return typeof response === 'function' ? response(request) : response;
    },
  };
}

function makeAsset(overrides = {}) {
  return {
    assetId: 'asset-1',
    bidDocumentId: 'bid-1',
    absolutePath: 'C:/fake/path/seal.png',
    contentHash: 'hash-A',
    assetKind: 'standalone',
    mimeType: 'image/png',
    sectionTitle: '投标函',
    nearbyText: '投标人：（盖章）',
    ...overrides,
  };
}

const REQUEST_BASE = {
  protocolVersion: 1,
  officialName: '北方长风智能科技有限公司',
  aliases: ['北方长风'],
};

test('getCapabilities 报告当前在线能力', () => {
  const provider = createOnlineVlmProvider({ aiService: createFakeAiService() });
  assert.deepStrictEqual(provider.getCapabilities(), {
    sealDetection: true,
    sealOcr: true,
    roleClassification: true,
    boundingBoxes: false,
  });
});

test('未开启多模态时不发起任何图片请求并返回 unavailable', async () => {
  const aiService = createFakeAiService({ multimodalEnabled: false });
  const provider = createOnlineVlmProvider({ aiService });
  const result = await provider.inspect({ ...REQUEST_BASE, assets: [makeAsset()] });
  assert.strictEqual(result.providerStatus, 'unavailable');
  assert.ok(result.message.includes('多模态'));
  assert.strictEqual(aiService.calls.length, 0);
});

test('同一 contentHash 只上传识别一次，不同出现位置分别判断角色', async () => {
  const aiService = createFakeAiService({
    responses: [
      { images: [{ hasSeal: true, sealText: '南屿云图数据服务有限责任公司', imageType: 'document' }] },
      { positions: [
        { pageRole: 'bidder', sealRole: 'bidder', nameConfidence: 0.9, roleConfidence: 0.9, reason: '投标函盖章处' },
        { pageRole: 'third-party', sealRole: 'third-party', nameConfidence: 0.9, roleConfidence: 0.9, reason: '制造商授权函' },
      ] },
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [
      makeAsset({ assetId: 'asset-1', nearbyText: '投标函：投标人（盖章）' }),
      makeAsset({ assetId: 'asset-2', nearbyText: '制造商授权函：由制造商盖章' }),
    ],
  });
  assert.strictEqual(result.providerStatus, 'success');
  assert.strictEqual(aiService.calls.length, 2);
  const imageCall = aiService.calls[0];
  assert.ok(imageCall.messages.some((message) => Array.isArray(message.content)
    && message.content.some((part) => part.type === 'local_image' && part.path === 'C:/fake/path/seal.png')));
  const imageParts = imageCall.messages
    .flatMap((message) => Array.isArray(message.content) ? message.content : [])
    .filter((part) => part.type === 'local_image');
  assert.strictEqual(imageParts.length, 1, '同一 contentHash 只应上传识别一次');
  const roleCall = aiService.calls[1];
  assert.ok(roleCall.messages.every((message) => typeof message.content === 'string'), '角色判断必须是纯文本请求');
  assert.strictEqual(result.assets[0].hasSeal, true);
  assert.strictEqual(result.assets[0].sealRole, 'bidder');
  assert.strictEqual(result.assets[1].sealRole, 'third-party');
});

test('page-scan 每次只发 1 张图', async () => {
  const aiService = createFakeAiService({
    responses: [
      { images: [{ hasSeal: false, imageType: 'document' }] },
      { positions: [{ pageRole: 'unclear', nameConfidence: 0.3, roleConfidence: 0.3 }] },
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  await provider.inspect({
    ...REQUEST_BASE,
    assets: [makeAsset({ assetId: 'scan-1', contentHash: 'hash-S', assetKind: 'page-scan' })],
  });
  const imageCall = aiService.calls[0];
  const imageParts = imageCall.messages.flatMap((message) => Array.isArray(message.content) ? message.content : [])
    .filter((part) => part.type === 'local_image');
  assert.strictEqual(imageParts.length, 1);
});

test('整页扫描图与独立小图分开分批，不混批', async () => {
  const aiService = createFakeAiService({
    responses: [
      { images: [{ hasSeal: false, imageType: 'document' }] },
      { images: [{ hasSeal: false, imageType: 'document' }, { hasSeal: false, imageType: 'document' }] },
      { positions: [
        { pageRole: 'unclear', nameConfidence: 0.3, roleConfidence: 0.3 },
        { pageRole: 'unclear', nameConfidence: 0.3, roleConfidence: 0.3 },
        { pageRole: 'unclear', nameConfidence: 0.3, roleConfidence: 0.3 },
      ] },
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [
      makeAsset({ assetId: 'scan-1', contentHash: 'hash-S', assetKind: 'page-scan' }),
      makeAsset({ assetId: 'small-1', contentHash: 'hash-1', assetKind: 'standalone' }),
      makeAsset({ assetId: 'small-2', contentHash: 'hash-2', assetKind: 'standalone' }),
    ],
  });
  const firstImageParts = aiService.calls[0].messages
    .flatMap((message) => Array.isArray(message.content) ? message.content : [])
    .filter((part) => part.type === 'local_image');
  assert.strictEqual(firstImageParts.length, 1, '整页扫描图必须单独成批');
  const secondImageParts = aiService.calls[1].messages
    .flatMap((message) => Array.isArray(message.content) ? message.content : [])
    .filter((part) => part.type === 'local_image');
  assert.strictEqual(secondImageParts.length, 2, '独立小图按批上传');
  assert.strictEqual(result.assets.length, 3);
});

test('单批失败不阻塞其他批次，providerStatus 为 partial 级错误信息在 assets 上', async () => {
  const aiService = createFakeAiService({
    responses: [
      new Error('第一批失败'),
      { images: [{ hasSeal: true, sealText: '北方长风智能科技有限公司', imageType: 'document' }] },
      { positions: [{ pageRole: 'bidder', sealRole: 'bidder', nameConfidence: 0.95, roleConfidence: 0.9 }] },
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [
      makeAsset({ assetId: 'a1', contentHash: 'hash-1', assetKind: 'page-scan' }),
      makeAsset({ assetId: 'a2', contentHash: 'hash-2' }),
    ],
  });
  assert.strictEqual(result.assets.find((item) => item.assetId === 'a1').status, 'failed');
  assert.strictEqual(result.assets.find((item) => item.assetId === 'a2').status, 'inspected');
  assert.strictEqual(result.providerStatus, 'success');
});