'use strict';

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
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

function createFakeAiServiceWithPrecheck({ multimodalEnabled = true, responses = [], unreadable = [] } = {}) {
  const base = createFakeAiService({ multimodalEnabled, responses });
  const skipped = new Set(unreadable);
  return {
    ...base,
    inspectLocalImage: async (filePath) => (skipped.has(path.basename(filePath))
      ? { readable: false, format: 'tiff', label: 'TIFF', reason: '图片格式不支持（TIFF）' }
      : { readable: true, format: 'png', label: 'PNG' }),
  };
}

// 角色阶段的桩响应：条目数必须与请求里的位置数一致，否则会被形状校验判为失败。
function positionsResponse(count) {
  return {
    positions: Array.from({ length: count }, () => ({
      page_role: 'unclear', seal_role: 'unclear', detected_subject: '', name_confidence: 0, role_confidence: 0,
    })),
  };
}

test('不可读的格式被跳过且不进批次', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [{ images: [{ has_seal: false, image_type: 'unknown' }] }],
    unreadable: ['logo.tiff'],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const logs = [];
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [
      makeAsset({ assetId: 'asset-1', absolutePath: 'C:/fake/logo.tiff', contentHash: 'hash-A' }),
      makeAsset({ assetId: 'asset-2', absolutePath: 'C:/fake/seal.png', contentHash: 'hash-B' }),
    ],
  }, { onLog: (event) => logs.push(event) });

  const skippedAsset = result.assets.find((item) => item.assetId === 'asset-1');
  assert.strictEqual(skippedAsset.status, 'skipped');
  assert.strictEqual(skippedAsset.skipKind, 'unsupported-format', '预检跳过应标记为 unsupported-format');
  assert.ok(skippedAsset.skipReason.includes('TIFF'), skippedAsset.skipReason);
  assert.ok(logs.some((event) => event.type === 'asset.skipped' && event.assetId === 'asset-1'));

  const imageParts = aiService.calls[0].messages
    .filter((message) => Array.isArray(message.content))
    .flatMap((message) => message.content)
    .filter((part) => part.type === 'local_image');
  assert.strictEqual(imageParts.length, 1, '被跳过的图片不应进入批次');
  assert.ok(imageParts[0].path.endsWith('seal.png'));
});

test('可转码格式照常入批，不被跳过', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [
      { images: [{ has_seal: true, seal_text: '甲公司', image_type: 'document' }] },
      positionsResponse(1),
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [makeAsset({ assetId: 'asset-1', absolutePath: 'C:/fake/seal.bmp', contentHash: 'hash-A' })],
  });
  assert.strictEqual(result.assets[0].status, 'inspected');
  assert.strictEqual(result.assets[0].hasSeal, true);
});

test('批次失败降级为逐张，只有坏图失败', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [
      new Error('整批失败'),                                          // 第 1 次：整批
      { images: [{ has_seal: false, image_type: 'unknown' }] },         // 第 2 次：asset-1 单独成功
      new Error('该图失败'),                                          // 第 3 次：asset-2 单独失败
      { positions: [{ page_role: 'unclear', seal_role: 'unclear' }] },   // 第 4 次：角色判断
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [
      makeAsset({ assetId: 'asset-1', contentHash: 'hash-A', assetKind: 'standalone' }),
      makeAsset({ assetId: 'asset-2', contentHash: 'hash-B', assetKind: 'standalone' }),
    ],
  });
  assert.strictEqual(aiService.calls.length, 4, '应为 1 次整批 + 2 次逐张 + 1 次角色判断');
  assert.strictEqual(result.assets.find((item) => item.assetId === 'asset-1').status, 'inspected');
  assert.strictEqual(result.assets.find((item) => item.assetId === 'asset-2').status, 'failed');
});

test('进度按去重后张数计，重复内容只算一次', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [
      // 2 条去重图片 → 批次响应必须给 2 条，否则会被形状校验判为失败。
      { images: [{ has_seal: true, seal_text: '甲公司', image_type: 'document' }, { has_seal: false, image_type: 'unknown' }] },
      positionsResponse(5),
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const events = [];
  await provider.inspect({
    ...REQUEST_BASE,
    assets: [
      makeAsset({ assetId: 'asset-1', contentHash: 'hash-A' }),
      makeAsset({ assetId: 'asset-2', contentHash: 'hash-A' }),
      makeAsset({ assetId: 'asset-3', contentHash: 'hash-A' }),
      makeAsset({ assetId: 'asset-4', contentHash: 'hash-A' }),
      makeAsset({ assetId: 'asset-5', contentHash: 'hash-B' }),
    ],
  }, { onProgress: (event) => events.push(event) });

  const contentEvents = events.filter((event) => event.phase === 'content');
  assert.strictEqual(contentEvents[0].total, 2, '分母应为去重后的 2，而不是 5');
  assert.strictEqual(contentEvents[contentEvents.length - 1].processed, 2);
  assert.strictEqual(contentEvents[contentEvents.length - 1].sealCount, 1, '按去重计：同一张图重复出现只算一次');
});

test('被跳过的图片不进分母', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [{ images: [{ has_seal: false, image_type: 'unknown' }] }],
    unreadable: ['logo.tiff'],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const events = [];
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [
      makeAsset({ assetId: 'asset-1', absolutePath: 'C:/fake/logo.tiff', contentHash: 'hash-A' }),
      makeAsset({ assetId: 'asset-2', absolutePath: 'C:/fake/seal.png', contentHash: 'hash-B' }),
    ],
  }, { onProgress: (event) => events.push(event) });

  const contentEvents = events.filter((event) => event.phase === 'content');
  assert.strictEqual(contentEvents[0].total, 1);
  assert.strictEqual(result.assets.filter((item) => item.status === 'skipped').length, 1);
});

test('全部图片被跳过时 total 为 0', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [],
    unreadable: ['logo.tiff'],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const events = [];
  await provider.inspect({
    ...REQUEST_BASE,
    assets: [makeAsset({ assetId: 'asset-1', absolutePath: 'C:/fake/logo.tiff', contentHash: 'hash-A' })],
  }, { onProgress: (event) => events.push(event) });

  const contentEvents = events.filter((event) => event.phase === 'content');
  assert.strictEqual(contentEvents[0].total, 0);
  assert.strictEqual(contentEvents[0].skipped, 1, '事件应携带被跳过的去重张数');
  assert.strictEqual(aiService.calls.length, 0, '不应发起任何图片请求');
});

test('role 阶段按位置计（同一内容的不同位置不去重）', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [
      { images: [{ has_seal: true, seal_text: '甲公司', image_type: 'document' }] },
      { positions: [{ page_role: 'bidder', seal_role: 'bidder', detected_subject: '甲公司', name_confidence: 0.9, role_confidence: 0.9 }] },
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const events = [];
  await provider.inspect({
    ...REQUEST_BASE,
    assets: [
      makeAsset({ assetId: 'asset-1', contentHash: 'hash-A', nearbyText: '投标函落款' }),
      makeAsset({ assetId: 'asset-2', contentHash: 'hash-A', nearbyText: '页脚' }),
    ],
  }, { onProgress: (event) => events.push(event) });

  const roleEvents = events.filter((event) => event.phase === 'role');
  assert.ok(roleEvents.length > 0, '应上报 role 进度');
  assert.strictEqual(roleEvents[roleEvents.length - 1].total, 2, '两个位置不能去重成 1');
});

test('同 contentHash 的多个出现位置共享失败结果', async () => {
  // 两个资产用同一个 contentHash；内容批次抛错（且逐张重试也抛错，因 responses 只有 1 项且为 Error）。
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [new Error('整批识别失败')],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [
      makeAsset({ assetId: 'asset-1', contentHash: 'hash-A' }),
      makeAsset({ assetId: 'asset-2', contentHash: 'hash-A' }),
    ],
  });
  const a1 = result.assets.find((item) => item.assetId === 'asset-1');
  const a2 = result.assets.find((item) => item.assetId === 'asset-2');
  assert.strictEqual(a1.status, 'failed', `asset-1 应为 failed，实际 ${a1.status}`);
  assert.strictEqual(a2.status, 'failed', `asset-2 应为 failed，实际 ${a2.status}`);
});

test('同 contentHash 的多个出现位置共享跳过结果', async () => {
  // 两个资产用同一个 contentHash，且该文件格式不可读；代表项被跳过，兄弟项也应共享 skipReason。
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [{ images: [{ has_seal: false, image_type: 'unknown' }] }],
    unreadable: ['logo.tiff'],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [
      makeAsset({ assetId: 'asset-1', absolutePath: 'C:/fake/logo.tiff', contentHash: 'hash-A' }),
      makeAsset({ assetId: 'asset-2', absolutePath: 'C:/fake/logo.tiff', contentHash: 'hash-A' }),
    ],
  });
  const a1 = result.assets.find((item) => item.assetId === 'asset-1');
  const a2 = result.assets.find((item) => item.assetId === 'asset-2');
  assert.strictEqual(a1.status, 'skipped', `asset-1 应为 skipped，实际 ${a1.status}`);
  assert.strictEqual(a2.status, 'skipped', `asset-2 应为 skipped，实际 ${a2.status}`);
  assert.ok(a1.skipReason.includes('TIFF'), a1.skipReason);
  assert.ok(a2.skipReason.includes('TIFF'), a2.skipReason);
});

test('内容类型判定为非盖章区域的图标记为 not-seal-area 且 status skipped', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [{ images: [{ has_seal: false, image_type: 'photo' }] }],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [makeAsset({ assetId: 'asset-1', contentHash: 'hash-A' })],
  });
  const asset = result.assets.find((item) => item.assetId === 'asset-1');
  assert.strictEqual(asset.status, 'skipped', `应被跳过，实际 ${asset.status}`);
  assert.strictEqual(asset.skipKind, 'not-seal-area', '内容类型跳过应标记为 not-seal-area');
  assert.ok(asset.skipReason.includes('photo'), asset.skipReason);
});

test('同 contentHash 的多个出现位置共享失败结果时一并传播 format/label', async () => {
  // 两张资产同一 contentHash 且可读，但内容批次抛错（逐张重试也抛错）。
  // 代表项失败，兄弟项应共享 error 并带上代表项的 format/label（Finding 4）。
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [new Error('整批识别失败')],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [
      makeAsset({ assetId: 'asset-1', contentHash: 'hash-A', absolutePath: 'C:/fake/seal.png' }),
      makeAsset({ assetId: 'asset-2', contentHash: 'hash-A', absolutePath: 'C:/fake/seal.png' }),
    ],
  });
  const a1 = result.assets.find((item) => item.assetId === 'asset-1');
  const a2 = result.assets.find((item) => item.assetId === 'asset-2');
  assert.strictEqual(a1.status, 'failed');
  assert.strictEqual(a2.status, 'failed');
  assert.strictEqual(a1.format || '', 'png');
  assert.strictEqual(a2.format || '', 'png', '失败结果应一并传播 format');
  assert.strictEqual(a2.label || '', a1.label || '', '失败结果应一并传播 label');
});

// 线上实例：模型（Qwen3.6 思考型）把整批回答退化成 `{}`，旧实现照单全收 → 每张图都被
// 判成「无印章」→ 报「检查成功、印章 0 张」。以下用例锁住这条路径必须走逐张重试。
test('模型返回空对象时触发逐张重试，不再静默判为无印章', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [
      {},
      { images: [{ has_seal: true, seal_text: '江西瑞智信息技术有限公司', image_type: 'document' }] },
      { images: [{ has_seal: false, image_type: 'unknown' }] },
      positionsResponse(2),
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const logs = [];
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [
      makeAsset({ assetId: 'asset-1', contentHash: 'hash-A' }),
      makeAsset({ assetId: 'asset-2', contentHash: 'hash-B' }),
    ],
  }, { onLog: (event) => logs.push(event) });

  assert.strictEqual(aiService.calls.length, 4, '应为 1 次整批（退化）+ 2 次逐张 + 1 次角色判断');
  assert.strictEqual(result.assets[0].status, 'inspected');
  assert.strictEqual(result.assets[0].hasSeal, true, '退化响应不应让这张图变成「无印章」');
  assert.ok(
    logs.some((event) => event.type === 'batch.failed' && event.code === 'VISION_RESPONSE_SHAPE_MISMATCH'),
    `应记录形状不匹配：${JSON.stringify(logs)}`,
  );
});

test('批次条目数少于图片数时同样触发逐张重试', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [
      { images: [{ has_seal: false, image_type: 'unknown' }] },
      { images: [{ has_seal: true, seal_text: '甲公司', image_type: 'document' }] },
      { images: [{ has_seal: false, image_type: 'unknown' }] },
      positionsResponse(2),
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [
      makeAsset({ assetId: 'asset-1', contentHash: 'hash-A' }),
      makeAsset({ assetId: 'asset-2', contentHash: 'hash-B' }),
    ],
  });

  assert.strictEqual(aiService.calls.length, 4, '应为 1 次整批（缺条目）+ 2 次逐张 + 1 次角色判断');
  assert.strictEqual(result.assets[0].hasSeal, true);
});

test('角色阶段条目数不匹配时该位置判为失败', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [
      { images: [{ has_seal: true, seal_text: '甲公司', image_type: 'document' }] },
      { positions: [] },
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [makeAsset({ assetId: 'asset-1', contentHash: 'hash-A' })],
  });

  const asset = result.assets.find((item) => item.assetId === 'asset-1');
  assert.strictEqual(asset.status, 'failed', `角色判断没结论时应判失败，实际 ${asset.status}`);
  assert.ok(String(asset.skipReason).includes('盖章角色判断'), asset.skipReason);
});

test('非盖章区域的图不再触发角色判断请求', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [{ images: [{ has_seal: false, image_type: 'photo' }] }],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [makeAsset({ assetId: 'asset-1', contentHash: 'hash-A' })],
  });

  assert.strictEqual(aiService.calls.length, 1, '照片已判定为非盖章区域，不该再发一次角色判断');
  assert.strictEqual(result.assets[0].status, 'skipped');
});

test('XML 残渣上下文不会进入提示词', async () => {
  const junk = '7777777" w:rsidR="00FB2113" w:rsidRDefault="00FB2113"/> 1143000 132715 <wp:effectExtent l="';
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [
      { images: [{ has_seal: false, image_type: 'unknown' }] },
      positionsResponse(1),
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  await provider.inspect({
    ...REQUEST_BASE,
    assets: [makeAsset({ assetId: 'asset-1', contentHash: 'hash-A', sectionTitle: undefined, nearbyText: junk })],
  });

  const promptText = aiService.calls[0].messages[0].content[0].text;
  assert.strictEqual(
    /w:rsid|posOffset|effectExtent|<wp:|<\/?w:/.test(promptText),
    false,
    `提示词不应含 XML 残渣：${promptText}`,
  );
});

// 源文档里真实存在的乱入文本：内容阶段识别正常，但模型会因「上下文仅显示乱码」
// 把角色判成低置信度 third-party。中文标书的上下文不含 CJK 就按没有上下文处理。
test('非中文的乱码上下文不会进入提示词', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [
      { images: [{ has_seal: false, image_type: 'unknown' }] },
      positionsResponse(1),
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  await provider.inspect({
    ...REQUEST_BASE,
    assets: [makeAsset({ assetId: 'asset-1', contentHash: 'hash-A', sectionTitle: undefined, nearbyText: 'x`' })],
  });

  const promptText = aiService.calls[0].messages[0].content[0].text;
  assert.strictEqual(promptText.includes('x`'), false, `提示词不应含乱码上下文：${promptText}`);
});
// 边缘样本二次确认：章文与主体仅差一两个字/截断/夹编号时，单次 OCR 漏字会把投标方
// 自己的章误报成近名别家（线上实例：「江西博微技术有限公司」vs「江西博微新技术有限公司」）。
// 对这类图重发单张请求做多数投票定稿章文。
test('近名转写触发二次确认，多数票定稿为完整主体名', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [
      // 首次识别：漏「科技」的近名转写
      { images: [{ has_seal: true, seal_text: '北方长风智能有限公司', image_type: 'document' }] },
      // 二次确认第 1、2 轮：读出完整主体名
      { images: [{ has_seal: true, seal_text: '北方长风智能科技有限公司', image_type: 'document' }] },
      { images: [{ has_seal: true, seal_text: '北方长风智能科技有限公司', image_type: 'document' }] },
      positionsResponse(1),
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const logs = [];
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [makeAsset({ assetId: 'asset-1', contentHash: 'hash-A' })],
  }, { onLog: (event) => logs.push(event) });

  assert.strictEqual(aiService.calls.length, 4, '应为 1 次识别 + 2 次确认 + 1 次角色判断');
  assert.strictEqual(result.assets[0].sealText, '北方长风智能科技有限公司');
  assert.ok(logs.some((event) => event.type === 'confirm.completed'), JSON.stringify(logs));
});

test('章文与主体明显不同时不触发二次确认', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [
      { images: [{ has_seal: true, seal_text: '南屿云图数据服务有限责任公司', image_type: 'document' }] },
      positionsResponse(1),
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  await provider.inspect({
    ...REQUEST_BASE,
    assets: [makeAsset({ assetId: 'asset-1', contentHash: 'hash-A' })],
  });

  assert.strictEqual(aiService.calls.length, 2, '真不一致的章不需要复核：1 次识别 + 1 次角色判断');
});

test('精确命中主体名的转写不触发二次确认', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [
      { images: [{ has_seal: true, seal_text: '北方长风智能科技有限公司', image_type: 'document' }] },
      positionsResponse(1),
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  await provider.inspect({
    ...REQUEST_BASE,
    assets: [makeAsset({ assetId: 'asset-1', contentHash: 'hash-A' })],
  });

  assert.strictEqual(aiService.calls.length, 2);
});

test('确认请求失败时保留原读，不再继续投票', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [
      { images: [{ has_seal: true, seal_text: '北方长风智能有限公司', image_type: 'document' }] },
      new Error('网络超时'),
      positionsResponse(1),
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [makeAsset({ assetId: 'asset-1', contentHash: 'hash-A' })],
  });

  assert.strictEqual(aiService.calls.length, 3, '确认失败后不再发第 2 轮');
  assert.strictEqual(result.assets[0].sealText, '北方长风智能有限公司');
});

test('三方平票时取信息最完整的转写', async () => {
  const aiService = createFakeAiServiceWithPrecheck({
    responses: [
      { images: [{ has_seal: true, seal_text: '北方长风智能有限公司', image_type: 'document' }] },
      { images: [{ has_seal: true, seal_text: '北方长风智能科技有限公司', image_type: 'document' }] },
      { images: [{ has_seal: true, seal_text: '北方长风智能科技', image_type: 'document' }] },
      positionsResponse(1),
    ],
  });
  const provider = createOnlineVlmProvider({ aiService });
  const result = await provider.inspect({
    ...REQUEST_BASE,
    assets: [makeAsset({ assetId: 'asset-1', contentHash: 'hash-A' })],
  });

  assert.strictEqual(result.assets[0].sealText, '北方长风智能科技有限公司');
});
