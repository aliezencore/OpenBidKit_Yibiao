'use strict';

/**
 * 印章 VLM 探针：用当前客户端的真实配置（base_url / api_key / model_name / request_mode）
 * 把指定的图片按「应用真实使用的提示词」发给模型，打印每次的原始返回与解析结果。
 *
 * 用途：区分「调用链路有问题」与「模型本身识别不出来」。
 * 它只读 user_config.json 与图片文件，不写任何东西，也绝不打印 API Key。
 *
 * 用法（在 client/ 下）：
 *   node scripts/seal-vlm-probe.cjs --single --repeat 3
 *   node scripts/seal-vlm-probe.cjs --batch  --repeat 3
 *   node scripts/seal-vlm-probe.cjs --single --images "C:/a.gif" "C:/b.png"
 *
 *   --single   每张图单独发一次请求（定位「哪张图识别不出来」）
 *   --batch    全部图片按应用的分批规则（独立小图每批最多 4 张）一次性发出
 *   --repeat N 每个用例重复 N 次（默认 1，用于观察模型稳定性）
 *   --images   显式指定图片路径；缺省时自动取最近一次检查的 subject-media 去重图片
 *   --contexts 传入一个 JSON 文件（内容为字符串数组），按顺序作为每张图的「上下文」，
 *              用来复现应用实际发送的上下文（例如污染前后的差异）
 */

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const USER_DATA_DIR = path.join(os.homedir(), 'AppData', 'Roaming', 'yibiao-client');
const CONFIG_PATH = path.join(USER_DATA_DIR, 'user_config.json');
const SUBJECT_MEDIA_DIR = path.join(USER_DATA_DIR, 'workspace', 'rejection-check', 'subject-media');
const STANDALONE_BATCH_SIZE = 4;

const MIME_BY_EXTENSION = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.bmp': 'image/bmp',
  '.webp': 'image/webp',
  '.tif': 'image/tiff',
  '.tiff': 'image/tiff',
  '.emf': 'image/emf',
  '.wmf': 'image/wmf',
};

function parseArgs(argv) {
  const options = { mode: 'single', repeat: 1, images: [], contextsFile: '' };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--single') options.mode = 'single';
    else if (arg === '--batch') options.mode = 'batch';
    else if (arg === '--repeat') { options.repeat = Math.max(1, Number(argv[index + 1]) || 1); index += 1; }
    else if (arg === '--contexts') { options.contextsFile = String(argv[index + 1] || ''); index += 1; }
    else if (arg === '--images') {
      index += 1;
      while (index < argv.length && !argv[index].startsWith('--')) { options.images.push(argv[index]); index += 1; }
      index -= 1;
    }
  }
  return options;
}

function maskKey(key) {
  const value = String(key || '');
  return value.length <= 8 ? '***' : `${value.slice(0, 3)}***${value.slice(-4)}`;
}

// 复刻应用在 subjectVisionProvider.buildContentPrompt 里的提示词。
function buildContentPrompt(contexts) {
  const descriptions = contexts.map((context, index) => ({ image_index: index + 1, context: context || '' }));
  return `【印章与内容识别】
请逐张识别图片：是否包含印章/公章、印章上的机构名称文字（尽量完整转写）、图片内容类型（document=公文/函件/证书、product-screenshot=产品截图、diagram=拓扑/图表、photo=照片、unknown）。
只有印章足够清晰、文字可辨认时才填写 seal_text；没有印章时 has_seal 为 false。
第 ${contexts.map((_context, index) => index + 1).join('、')} 张图片对应的上下文：${JSON.stringify(descriptions)}

JSON 格式：{"images":[{"has_seal":true,"seal_text":"印章上的机构名称","image_type":"document"}]}`;
}

function discoverImages() {
  if (!fs.existsSync(SUBJECT_MEDIA_DIR)) return [];
  const documents = fs.readdirSync(SUBJECT_MEDIA_DIR, { withFileTypes: true }).filter((entry) => entry.isDirectory());
  if (!documents.length) return [];
  // 取「最近修改」的那份投标文件目录
  const latest = documents
    .map((entry) => ({ name: entry.name, at: fs.statSync(path.join(SUBJECT_MEDIA_DIR, entry.name)).mtimeMs }))
    .sort((a, b) => b.at - a.at)[0];
  const documentDir = path.join(SUBJECT_MEDIA_DIR, latest.name);
  const files = [];
  for (const assetDir of fs.readdirSync(documentDir, { withFileTypes: true }).filter((entry) => entry.isDirectory())) {
    const inside = fs.readdirSync(path.join(documentDir, assetDir.name)).filter((name) => MIME_BY_EXTENSION[path.extname(name).toLowerCase()]);
    for (const name of inside) files.push(path.join(documentDir, assetDir.name, name));
  }
  return files;
}

function toDataUrl(filePath) {
  const mime = MIME_BY_EXTENSION[path.extname(filePath).toLowerCase()] || 'application/octet-stream';
  return `data:${mime};base64,${fs.readFileSync(filePath).toString('base64')}`;
}

// 流式请求；同时统计 content 与 reasoning_content，便于看清 token 花在哪。
async function requestStream(config, contexts, imagePaths) {
  const body = {
    model: config.model_name,
    stream: true,
    response_format: { type: 'json_object' },
    messages: [{
      role: 'user',
      content: [
        { type: 'text', text: buildContentPrompt(contexts) },
        ...imagePaths.map((filePath) => ({ type: 'image_url', image_url: { url: toDataUrl(filePath) } })),
      ],
    }],
  };

  const response = await fetch(`${String(config.base_url).replace(/\/+$/, '')}/chat/completions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${config.api_key}` },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    return { httpError: `${response.status} ${response.statusText} ${String(await response.text()).slice(0, 400)}` };
  }

  const state = { content: '', reasoning: '', usage: null, deltaKeys: new Set(), chunks: 0 };
  let buffer = '';
  const decoder = new TextDecoder();

  for await (const chunk of response.body) {
    buffer += decoder.decode(chunk, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith('data:')) continue;
      const payloadText = trimmed.slice(5).trim();
      if (!payloadText || payloadText === '[DONE]') continue;
      let payload = null;
      try { payload = JSON.parse(payloadText); } catch { continue; }
      state.chunks += 1;
      if (payload.usage) state.usage = payload.usage;
      const delta = payload.choices?.[0]?.delta || {};
      Object.keys(delta).forEach((key) => state.deltaKeys.add(key));
      if (typeof delta.content === 'string') state.content += delta.content;
      if (typeof delta.reasoning_content === 'string') state.reasoning += delta.reasoning_content;
    }
  }

  return state;
}

function summarize(state, requestedCount) {
  if (state.httpError) return { ok: false, note: `HTTP 失败：${state.httpError}` };
  let parsed = null;
  try { parsed = JSON.parse(state.content); } catch { parsed = null; }
  const images = Array.isArray(parsed?.images) ? parsed.images : null;
  const seals = images ? images.filter((item) => item?.has_seal) : [];
  return {
    ok: Boolean(images),
    content: state.content,
    imageCount: images ? images.length : 0,
    requestedCount,
    countMatches: images ? images.length === requestedCount : false,
    seals: seals.map((item) => item.seal_text || '(空)'),
    types: images ? images.map((item) => item.image_type || '?') : [],
    reasoningChars: state.reasoning.length,
    completionTokens: state.usage?.completion_tokens ?? '-',
    chunks: state.chunks,
    deltaKeys: [...state.deltaKeys].join('/'),
  };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
  console.log(`配置：base_url=${config.base_url}  model=${config.model_name}  request_mode=${config.request_mode}  key=${maskKey(config.api_key)}`);

  const images = (options.images.length ? options.images : discoverImages()).filter((filePath) => fs.existsSync(filePath));
  if (!images.length) { console.log('未找到可用图片'); process.exit(1); }
  images.forEach((filePath, index) => {
    const stat = fs.statSync(filePath);
    console.log(`  图${index + 1}: ${path.extname(filePath).toLowerCase()} ${(stat.size / 1024).toFixed(1)}KB  ${path.basename(filePath)}`);
  });

  const cases = options.mode === 'batch'
    ? [{ label: `批量 ${images.length} 张`, paths: images.slice(0, STANDALONE_BATCH_SIZE) }]
    : images.map((filePath, index) => ({ label: `单张 图${index + 1} (${path.extname(filePath)})`, paths: [filePath] }));

  const injectedContexts = options.contextsFile
    ? JSON.parse(fs.readFileSync(options.contextsFile, 'utf8'))
    : null;
  if (injectedContexts) console.log(`上下文：来自 ${options.contextsFile}（${injectedContexts.length} 条）`);

  console.log('');
  let stats = [];
  for (const testCase of cases) {
    for (let round = 1; round <= options.repeat; round += 1) {
      const contexts = testCase.paths.map((_filePath, index) => (
        injectedContexts ? String(injectedContexts[index] || '') : ''
      ));
      const state = await requestStream(config, contexts, testCase.paths);
      const result = summarize(state, testCase.paths.length);
      stats.push({ label: testCase.label, ...result });
      const head = `[${testCase.label} #${round}] `;
      if (result.ok) {
        console.log(`${head}✓ 解析成功 图片条目=${result.imageCount}/${result.requestedCount}${result.countMatches ? '' : ' ⚠️数量不匹配'} 印章=${result.seals.length} ${result.seals.join(' | ')} 类型=${result.types.join(',')}`);
        console.log(`      tokens=${result.completionTokens} 推理字符=${result.reasoningChars} delta键=${result.deltaKeys} 原始=${JSON.stringify(result.content).slice(0, 200)}`);
      } else {
        console.log(`${head}✗ ${result.note || '解析失败'}`);
        if (!result.note) console.log(`      tokens=${result.completionTokens} 推理字符=${result.reasoningChars} delta键=${result.deltaKeys} 原始=${JSON.stringify(result.content)}`);
      }
    }
  }

  console.log('\n=== 汇总 ===');
  for (const item of stats) {
    if (item.ok) console.log(`${item.label}: 条目=${item.imageCount}/${item.requestedCount} 印章=${item.seals.length} tokens=${item.completionTokens} 推理字符=${item.reasoningChars}`);
    else console.log(`${item.label}: 解析失败`);
  }
}

main().catch((error) => { console.log('ERR ' + error.message); process.exit(1); });
