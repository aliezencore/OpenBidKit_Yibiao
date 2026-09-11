'use strict';

const crypto = require('node:crypto');
const { logAiFailure } = require('../utils/aiLog.cjs');

const STANDALONE_BATCH_SIZE = 4;
const ROLE_BATCH_SIZE = 8;

function chunkItems(items, batchSize) {
  const result = [];
  for (let index = 0; index < items.length; index += batchSize) result.push(items.slice(index, index + batchSize));
  return result;
}

function hashText(content) {
  return crypto.createHash('sha256').update(String(content || ''), 'utf8').digest('hex').slice(0, 16);
}

function buildContentPrompt(batch) {
  const descriptions = batch.map((entry, index) => ({
    image_index: index + 1,
    context: entry.asset.sectionTitle || entry.asset.nearbyText || '',
  }));
  return `【印章与内容识别】
请逐张识别图片：是否包含印章/公章、印章上的机构名称文字（尽量完整转写）、图片内容类型（document=公文/函件/证书、product-screenshot=产品截图、diagram=拓扑/图表、photo=照片、unknown）。
只有印章足够清晰、文字可辨认时才填写 seal_text；没有印章时 has_seal 为 false。
第 ${batch.map((entry, index) => index + 1).join('、')} 张图片对应的上下文：${JSON.stringify(descriptions)}

JSON 格式：{"images":[{"has_seal":true,"seal_text":"印章上的机构名称","image_type":"document"}]}`;
}

function buildRolePrompt({ officialName, aliases, positions }) {
  const positionText = positions.map((entry, index) => ({
    position: index + 1,
    seal_text: entry.content.sealText || '',
    image_type: entry.content.imageType || 'unknown',
    context: entry.asset.nearbyText || '',
    section: entry.asset.sectionTitle || '',
  }));
  return `【盖章方角色判断】
正式投标主体：${officialName}
允许简称：${aliases.join('、') || '（无）'}

以下是若干盖章/图片位置。请判断每个位置的页面角色 page_role 与盖章方角色 seal_role：
- bidder：该位置语义上应由投标方盖章/署名（如投标函、承诺函、报价表、授权委托书的投标方落款）；
- third-party：该位置合法属于第三方（如制造商授权函的制造商章、认证证书的认证机构章、客户证明的客户章）；
- unclear：无法确定。

同时给出 detected_subject（印章识别出的机构名称）、name_confidence 与 role_confidence（0~1）。

待判断位置：
${JSON.stringify(positionText, null, 2)}

JSON 格式：{"positions":[{"page_role":"bidder","seal_role":"bidder","detected_subject":"","name_confidence":0.9,"role_confidence":0.9,"reason":"判断原因"}]}`;
}

// 把模型返回的单条角色判断归一化（原实现内联在循环里，抽出来便于逐张重试复用）。
function normalizeRoleItem(item) {
  const source = item || {};
  const pageRole = source.page_role !== undefined ? source.page_role : source.pageRole;
  const sealRole = source.seal_role !== undefined ? source.seal_role : source.sealRole;
  const detectedSubject = source.detected_subject !== undefined ? source.detected_subject : source.detectedSubject;
  const nameConfidence = source.name_confidence !== undefined ? source.name_confidence : source.nameConfidence;
  const roleConfidence = source.role_confidence !== undefined ? source.role_confidence : source.roleConfidence;
  return {
    pageRole: ['bidder', 'third-party', 'unclear'].includes(pageRole) ? pageRole : 'unclear',
    sealRole: ['bidder', 'third-party', 'unclear'].includes(sealRole) ? sealRole : 'unclear',
    detectedSubject: String(detectedSubject || ''),
    nameConfidence: Math.max(0, Math.min(1, Number(nameConfidence || 0))),
    roleConfidence: Math.max(0, Math.min(1, Number(roleConfidence || 0))),
    reason: String(source.reason || ''),
  };
}

function createOnlineVlmProvider({ aiService }) {
  async function runJson(request, label, failureMessage) {
    if (aiService.collectJsonResponse) {
      return aiService.collectJsonResponse({
        ...request,
        progressLabel: label,
        failureMessage,
      });
    }
    return aiService.requestJson(request);
  }

  async function inspect(request, hooks = {}) {
    const emitProgress = typeof hooks.onProgress === 'function' ? hooks.onProgress : () => {};
    const emitLog = typeof hooks.onLog === 'function' ? hooks.onLog : () => {};

    const config = typeof aiService.getConfig === 'function' ? aiService.getConfig() : {};
    if (!config?.multimodal_enabled) {
      return {
        providerStatus: 'unavailable',
        message: '当前文本模型未开启多模态支持，请到设置页的文本模型中开启',
        assets: [],
      };
    }

    const assets = Array.isArray(request?.assets) ? request.assets : [];
    const byAssetId = new Map(assets.map((asset) => [asset.assetId, { asset, content: null, error: undefined, skipReason: undefined }]));

    // 同一内容在文档里的出现次数：进度按去重张数，印章数按出现次数累计。
    const occurrenceCount = new Map();
    for (const entry of byAssetId.values()) {
      const hash = entry.asset.contentHash;
      occurrenceCount.set(hash, (occurrenceCount.get(hash) || 0) + 1);
    }

    // 预检：两种解码器都解不了的格式直接跳过，且不连坐同批其它图片。
    const precheck = typeof aiService.inspectLocalImage === 'function'
      ? aiService.inspectLocalImage.bind(aiService)
      : null;
    const pendingEntries = [];
    const seenHashes = new Set();
    let skippedCount = 0;
    for (const entry of byAssetId.values()) {
      const hash = entry.asset.contentHash;
      if (seenHashes.has(hash)) continue;
      seenHashes.add(hash);

      if (precheck) {
        let verdict = null;
        try {
          verdict = await precheck(entry.asset.absolutePath);
        } catch (error) {
          verdict = { readable: false, format: 'unknown', label: '未知格式', reason: '图片不可读' };
          logAiFailure('subject.seal.precheck', { asset: entry.asset.assetId, message: error?.message });
        }
        if (verdict && verdict.readable === false) {
          entry.skipReason = `${verdict.reason || '图片不可读'}，已跳过`;
          entry.format = verdict.format || 'unknown';
          entry.label = verdict.label || '未知格式';
          skippedCount += 1;
          emitLog({ type: 'asset.skipped', assetId: entry.asset.assetId, format: verdict.format, reason: entry.skipReason });
          continue;
        }
        entry.format = verdict?.format || 'unknown';
        entry.label = verdict?.label || '未知格式';
      }
      pendingEntries.push(entry);
    }

    const contentTotal = pendingEntries.length;
    let contentProcessed = 0;
    let sealCount = 0;
    const emitContentProgress = () => emitProgress({
      phase: 'content', processed: contentProcessed, total: contentTotal, sealCount, skipped: skippedCount,
    });
    // 先发一次，令「全部被跳过」时上层也能看见 total === 0。
    emitContentProgress();

    // 分批策略（spec §9.3）：整页扫描图每批 1 张；独立小图每批最多 4 张。两类不可混批。
    const contentBatches = [
      ...pendingEntries.filter((entry) => entry.asset.assetKind === 'page-scan').map((entry) => [entry]),
      ...chunkItems(pendingEntries.filter((entry) => entry.asset.assetKind !== 'page-scan'), STANDALONE_BATCH_SIZE),
    ];

    const contentCache = new Map();

    async function submitContentBatch(entries) {
      const payload = await runJson({
        messages: [{
          role: 'user',
          content: [
            { type: 'text', text: buildContentPrompt(entries) },
            ...entries.map((entry) => ({ type: 'local_image', path: entry.asset.absolutePath })),
          ],
        }],
      }, '主体印章图片识别', '印章图片识别结果格式无效');

      const images = Array.isArray(payload?.images) ? payload.images : [];
      entries.forEach((entry, index) => {
        const image = images[index] || {};
        const hasSeal = image.has_seal !== undefined ? image.has_seal : image.hasSeal;
        const sealText = image.seal_text !== undefined ? image.seal_text : image.sealText;
        const imageType = image.image_type !== undefined ? image.image_type : image.imageType;
        entry.content = {
          hasSeal: Boolean(hasSeal),
          sealText: String(sealText || ''),
          imageType: String(imageType || 'unknown'),
        };
        contentCache.set(entry.asset.contentHash, entry.content);
      });
    }

    function describeFailure(error) {
      return {
        status: error?.status || error?.statusCode || '',
        code: error?.code || '',
        providerMessage: error?.message || String(error || ''),
        errorName: error?.name || 'Error',
      };
    }

    for (let batchIndex = 0; batchIndex < contentBatches.length; batchIndex += 1) {
      const entries = contentBatches[batchIndex];
      const batchLabel = `${batchIndex + 1}/${contentBatches.length}`;
      const startedAt = Date.now();
      try {
        await submitContentBatch(entries);
      } catch (error) {
        const failure = describeFailure(error);
        logAiFailure('subject.seal.content', { phase: 'content', batch: batchLabel, assets: entries.length, ...failure, durationMs: Date.now() - startedAt });
        emitLog({
          type: 'batch.failed', phase: 'content', batch: batchLabel,
          assetIds: entries.map((entry) => entry.asset.assetId), ...failure,
        });

        if (entries.length > 1) {
          // 降级：逐张重试，只有仍然失败的那张记为失败。
          const recovered = [];
          const failedAssetIds = [];
          for (const entry of entries) {
            try {
              await submitContentBatch([entry]);
              recovered.push(entry.asset.assetId);
            } catch (singleError) {
              entry.error = singleError?.message || '图片识别失败';
              failedAssetIds.push(entry.asset.assetId);
              logAiFailure('subject.seal.content.isolated', { asset: entry.asset.assetId, ...describeFailure(singleError) });
            }
          }
          emitLog({ type: 'batch.isolated', phase: 'content', batch: batchLabel, batchSize: entries.length, recovered, failedAssetIds });
        } else {
          entries[0].error = error?.message || '图片识别失败';
        }
      }

      for (const entry of entries) {
        contentProcessed += 1;
        if (entry.content?.hasSeal) sealCount += occurrenceCount.get(entry.asset.contentHash) || 1;
      }
      emitContentProgress();
    }

    // 把缓存中的内容识别结果回填到被去重跳过的同 hash 出现位置，保证每个出现位置都参与角色判断。
    for (const entry of byAssetId.values()) {
      if (!entry.content && !entry.error && !entry.skipReason && contentCache.has(entry.asset.contentHash)) {
        entry.content = contentCache.get(entry.asset.contentHash);
      }
    }

    // 阶段二：角色判断（纯文本请求，按 contentHash + nearbyContextHash 缓存）。
    const roleCache = new Map();
    const pendingRoles = [...byAssetId.values()].filter((entry) => entry.content && !entry.error);
    const roleTotal = pendingRoles.length;
    let roleProcessed = 0;
    const emitRoleProgress = () => emitProgress({ phase: 'role', processed: roleProcessed, total: roleTotal });
    const roleBatches = chunkItems(pendingRoles, ROLE_BATCH_SIZE);

    async function submitRoleBatch(entries) {
      const positions = entries.map((entry) => ({ asset: entry.asset, content: entry.content }));
      const cacheKey = positions.map((entry) => `${entry.asset.contentHash}:${hashText(entry.asset.nearbyText)}`).join('|');
      if (roleCache.has(cacheKey)) {
        const cached = roleCache.get(cacheKey);
        entries.forEach((entry, index) => { entry.role = cached[index]; });
        return;
      }
      const payload = await runJson({
        messages: [{ role: 'user', content: buildRolePrompt({ officialName: request.officialName, aliases: request.aliases || [], positions }) }],
      }, '主体盖章角色判断', '盖章角色判断结果格式无效');
      const results = Array.isArray(payload?.positions) ? payload.positions : [];
      const normalized = entries.map((_entry, index) => normalizeRoleItem(results[index]));
      roleCache.set(cacheKey, normalized);
      entries.forEach((entry, index) => { entry.role = normalized[index]; });
    }

    for (let batchIndex = 0; batchIndex < roleBatches.length; batchIndex += 1) {
      const entries = roleBatches[batchIndex];
      const batchLabel = `${batchIndex + 1}/${roleBatches.length}`;
      const startedAt = Date.now();
      try {
        await submitRoleBatch(entries);
      } catch (error) {
        const failure = describeFailure(error);
        logAiFailure('subject.seal.role', { phase: 'role', batch: batchLabel, assets: entries.length, ...failure, durationMs: Date.now() - startedAt });
        emitLog({
          type: 'batch.failed', phase: 'role', batch: batchLabel,
          assetIds: entries.map((entry) => entry.asset.assetId), ...failure,
        });
        if (entries.length > 1) {
          for (const entry of entries) {
            try {
              await submitRoleBatch([entry]);
            } catch (singleError) {
              entry.error = singleError?.message || '角色判断失败';
            }
          }
        } else {
          entries[0].error = error?.message || '角色判断失败';
        }
      }
      roleProcessed += entries.length;
      emitRoleProgress();
    }

    // 组装结果：明确普通产品截图/拓扑图/照片跳过并记录原因（spec §9.3）。
    const resultAssets = [...byAssetId.values()].map((entry) => {
      if (entry.skipReason) {
        return {
          assetId: entry.asset.assetId,
          status: 'skipped',
          skipReason: entry.skipReason,
          format: entry.format || '',
          label: entry.label || '',
          hasSeal: false,
        };
      }
      if (entry.error) {
        return {
          assetId: entry.asset.assetId,
          status: 'failed',
          skipReason: entry.error,
          format: entry.format || '',
          label: entry.label || '',
          hasSeal: false,
        };
      }
      const content = contentCache.get(entry.asset.contentHash) || { hasSeal: false, sealText: '', imageType: 'unknown' };
      if (['product-screenshot', 'diagram', 'photo'].includes(content.imageType)) {
        return { assetId: entry.asset.assetId, status: 'skipped', skipReason: `识别为${content.imageType}，非盖章区域`, hasSeal: false };
      }
      if (!content.hasSeal) {
        return { assetId: entry.asset.assetId, status: 'inspected', hasSeal: false, imageType: content.imageType };
      }
      return {
        assetId: entry.asset.assetId,
        status: 'inspected',
        hasSeal: true,
        sealText: content.sealText || undefined,
        detectedSubject: entry.role?.detectedSubject || content.sealText || '',
        imageType: content.imageType,
        pageRole: entry.role?.pageRole || 'unclear',
        sealRole: entry.role?.sealRole || 'unclear',
        nameConfidence: entry.role?.nameConfidence ?? 0,
        roleConfidence: entry.role?.roleConfidence ?? 0,
        reason: entry.role?.reason || '',
      };
    });

    const failedCount = resultAssets.filter((item) => item.status === 'failed').length;
    return {
      providerStatus: failedCount === resultAssets.length && resultAssets.length > 0 ? 'error' : 'success',
      message: failedCount ? `${failedCount} 张图片识别失败` : undefined,
      assets: resultAssets,
    };
  }

  return {
    getCapabilities() {
      return { sealDetection: true, sealOcr: true, roleClassification: true, boundingBoxes: false };
    },
    inspect,
  };
}

module.exports = {
  createOnlineVlmProvider,
};