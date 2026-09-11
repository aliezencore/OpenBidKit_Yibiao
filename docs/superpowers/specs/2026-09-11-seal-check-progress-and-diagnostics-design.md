# 印章检查进度上报、格式容错与失败诊断 设计规格

- 日期：2026-09-11
- 关联规格：[`2026-09-09-bid-subject-consistency-design.md`](./2026-09-09-bid-subject-consistency-design.md)（本规格沿用其 §9.3 分批策略与 §13 视觉状态定义）
- 状态：已评审，待编制实施计划

## 1. 背景

### 1.1 现象

用户在一次真实检查中得到：

```
印章检查失败（已检查 0/7 张，印章 0 张，跳过 0 张，失败 7 张）
```

7 张图片全部失败，且界面上没有任何可定位的原因。

### 1.2 根因（已实测复现，非推测）

1. 该投标文件提取出 7 个视觉资产，但 `contentHash` 去重后只有 **3 张不同图片**：
   `3d235f…`（PNG，158x155，出现 4 次）、`4064b7…`（PNG，49x45，出现 2 次）、
   `1e643b…`（**GIF89a，187x165，出现 1 次**）。
   三者 `assetKind` 均为 `standalone`，因此被合进**同一个批次**。
2. GIF 恰好排在批次首位。`aiService.compressLocalImageToDataUrl` 使用 Electron
   `nativeImage.createFromBuffer`，**只支持 PNG/JPEG**，对 GIF 解码得到空图，抛出
   `图片读取或压缩失败（1e643b….gif）：图片格式不受支持或文件已损坏`。
3. `prepareMultimodalMessages` 是**边遍历边压缩**的，第 1 张即抛错 → 请求根本未发出。
4. `subjectVisionProvider` 的批次 `catch` 把该批次内**所有** `contentHash` 标记失败 →
   7 行资产（3 个 hash）全部 `status: 'failed'`。
5. 该异常抛在 `collectJsonResponseWithConfig`（`aiService.cjs:813`）与 `chatWithConfig`
   （`aiService.cjs:1364`），**两处都在 `writeAiLog`（`aiService.cjs:1376`）之前**，
   因此 AI 日志目录中连一条 `主体印章图片识别` 都没有产生 —— 整条链路静默。

取证位置与复现方式见附录 A。

**补充证据（决定 G1 取向的关键）**：把这张 GIF **原样**（`data:image/gif;base64,…`）发给同一个模型，
可以正常返回：

```json
{ "has_seal": true, "seal_text": "江西瑞智信息技术有限公司", "image_type": "document" }
```

即这张 GIF 上就是一枚印章，且机构名与本次正文检查已抓到的 `subject-text-1`
（「应答人：江西瑞智信息有限公司」，判为明确不一致）**同属一家**。

结论：问题只是「Electron 的 `nativeImage` 解不了 GIF」这一**解码器限制**，而不是「这张图不可用」。
因此「格式不支持就跳过」会**静默漏掉一条本应报出的印章主体不一致**。
正确取向是「让解码器限制不再决定图片的命运」——本规格最终采用**三分派**：
GIF 原字节透传、BMP/WebP 经离屏 Chromium 转码、PNG/JPEG 维持现状（见 §4.1）。

### 1.3 由根因派生出的三个问题

| # | 问题 | 影响 |
|---|---|---|
| P1 | 单张图片的格式问题会**连坐**同批次的全部图片 | 1 张坏图 = 7 张全失败 |
| P2 | 印章检查全程只报一句「正在检查 N 张…」，之后静默 | 长耗时段无进度 |
| P3 | 图片准备阶段的失败**不产生任何日志**；解析失败时真实原因被通用文案替换 | 出问题无法诊断 |

## 2. 目标与非目标

### 2.1 目标

- G1：**不再因解码器限制而丢掉可用的图片**。按格式三分派：
  GIF 走原字节透传（实测模型可识别）；BMP/WebP 走离屏转码（Chromium 可解）；
  PNG/JPEG 维持现有压缩路径。只有 Chromium **也**解不了的格式（TIFF/EMF/WMF/未识别）
  才跳过，且跳过时记录**具体格式名**。同时保证单张图片的问题不再连坐同批次其它图片。
- G2：批次失败时按图片降级重试，只有真正失败的那张记为失败。
- G3：印章检查分两阶段上报进度，每次 VLM 响应返回后推进。
- G4：图片准备失败与 VLM/LLM 失败的真实原因与错误码，落进**后台日志**。

### 2.2 非目标（明确不做）

- **不改任何前端文件**。用户已明确：前端现有的错误展示保持原样，真实原因与错误码只落后台。
- **不接入 Pi Agent 执行监视器**。理由见 §8。
- **不引入图像解码依赖**。`sharp`（25 个平台原生包 + Electron ABI 风险）、
  `jimp`（27 个依赖）都不引入 —— 离屏 Chromium 已经提供了原生解码器，成本为 0。
- **不重构 `localImageRenderService.cjs`**。新模块内联同一份离屏窗口配置并在注释里指向它，
  不去抽取共用工厂：该窗口配置是稳定的十余行，抽取的收益不抵改动在用的生产渲染模块的风险。
- 不引入数据库迁移；不新增可持久化字段。
- 不改 `visionMessage` 的写入逻辑。
- 不重构 `aiService` 的通用重试/超时/队列策略。

## 3. 影响面

| 文件 | 类型 | 说明 |
|---|---|---|
| `client/electron/utils/imageFormat.cjs` | 新增 | 魔数识别 + `resolveImagePlan` + `computeTargetSize`（纯函数） |
| `client/electron/utils/imageFormat.test.cjs` | 新增 | 纯函数单测 |
| `client/electron/services/offscreenImageTranscoder.cjs` | 新增 | 离屏窗口图片转码（懒创建 + 复用 + 空闲销毁） |
| `client/electron/utils/aiLog.cjs` | 修改 | 新增 `logAiFailure` |
| `client/electron/services/aiService.cjs` | 修改 | 格式标记、`inspectLocalImage`、补准备阶段日志、保留真实错误 |
| `client/electron/services/subjectVisionProvider.cjs` | 修改 | 预检跳过、批次降级隔离、进度回调、失败日志 |
| `client/electron/services/subjectCheckTask.cjs` | 修改 | `runSubjectSealCheck` 接收 logger、翻译进度文案、区分「全跳过」状态 |
| `client/electron/services/rejectionCheckTask.cjs` | 修改 | 把既有 `developerLogger` 传入印章检查 |
| `client/electron/services/subjectVisionProvider.test.cjs` | 修改 | 新增用例 |
| `client/electron/services/subjectCheckTask.test.cjs` | 修改 | 新增用例 |

不改动：`src/` 下全部前端文件、`checkResultExportService.cjs`、`rejectionCheckStore.cjs`、数据库 schema。

## 4. 设计

### 4.1 图片格式识别与三分派（压缩 / 透传 / 转码）

**格式全景**。`docxVisualAssetExtractor.cjs:12-23` 的 MIME 白名单就是提取器实际会产出的全部格式，
逐类实测（Electron 41.10.2 / Chromium 146）结果如下：

| 提取器产出 | `nativeImage` | Chromium（离屏） | 本规格的处理 |
|---|---|---|---|
| `png` / `jpeg` | ✅ | — | `compress`：走现有路径，行为不变 |
| `gif` | ❌ | ✅ | `passthrough`：原字节直发（实测模型可正常识别） |
| `bmp` / `webp` | ❌ | ✅ | `transcode`：离屏转 JPEG |
| `tif` / `tiff` | ❌ | ❌ | `skip`（记具体格式名） |
| `emf` / `wmf` | ❌ | ❌ | `skip`（矢量格式，记具体格式名） |
| 其它 / 未识别 | ❌ | 未验证 | `skip` |

关键事实：`nativeImage` **只认 PNG/JPEG**，GIF/BMP/WebP/TIFF/EMF/WMF 全都解不了；
而 TIFF 与 EMF/WMF 连 Chromium 也不支持（后两者是矢量），因此这三类仍需跳过——
但会给出**具体格式名**（如「图片格式不支持（TIFF）」），而不是含糊的「未知格式」。

**新增 `electron/utils/imageFormat.cjs`（纯函数，不依赖 Electron）**

```js
const FORMAT_SIGNATURES = [
  // png / jpeg / gif / webp / bmp 同前
  { format: 'tiff', label: 'TIFF', mime: 'image/tiff',
    match: (b) => b.length >= 4 && ((b[0] === 0x49 && b[1] === 0x49 && b[2] === 0x2a) || (b[0] === 0x4d && b[1] === 0x4d && b[2] === 0x00 && b[3] === 0x2a)) },
  { format: 'emf', label: 'EMF', mime: 'image/emf',
    match: (b) => b.length >= 44 && b.toString('latin1', 40, 44) === ' EMF' },
  { format: 'wmf', label: 'WMF', mime: 'image/wmf',
    match: (b) => b.length >= 4 && b[0] === 0xd7 && b[1] === 0xcd && b[2] === 0xc6 && b[3] === 0x9a },
];

const NATIVE_DECODABLE_FORMATS = new Set(['png', 'jpeg']);
const PASSTHROUGH_FORMATS = new Set(['gif']);
const TRANSCODE_FORMATS = new Set(['bmp', 'webp']);
const SKIPPED_FORMATS = new Set(['tiff', 'emf', 'wmf']);

function detectImageFormat(buffer) { /* → { format, label, mime }；未识别为 { format:'unknown', label:'未知格式', mime:'' } */ }

// 单一决策入口：把「压缩 / 透传 / 转码 / 跳过」的策略集中在一处。
// 纯函数、可单测，是 imageFormat.test.cjs 的主要被测对象。
function resolveImagePlan(buffer, { maxPassthroughBytes }) { /* → plan */ }

// 转码目标尺寸：等比缩放到最长边不超过 maxEdge；不放大。
function computeTargetSize(width, height, maxEdge) { /* → { width, height, scale } */ }
```

导出：`detectImageFormat`、`resolveImagePlan`、`computeTargetSize`、
`NATIVE_DECODABLE_FORMATS`、`PASSTHROUGH_FORMATS`、`TRANSCODE_FORMATS`、`SKIPPED_FORMATS`。

`resolveImagePlan` 的完整判定（**唯一真相来源**）：

| 条件 | 返回 |
|---|---|
| `png` / `jpeg` | `{ action: 'compress' }` |
| `gif` 且 `buffer.length ≤ maxPassthroughBytes` | `{ action: 'passthrough', mime: 'image/gif' }` |
| `gif` 且超限 | `{ action: 'skip', code: 'PASSTHROUGH_TOO_LARGE', reason: 'GIF 体积超出透传上限' }` |
| `bmp` / `webp` | `{ action: 'transcode', format, label }` |
| `tiff` / `emf` / `wmf` / `unknown` | `{ action: 'skip', code: 'UNSUPPORTED_IMAGE_FORMAT', reason: '图片格式不支持（<label>）' }` |

**`aiService.cjs`**

新增常量：

```js
// 透传不做缩放，用体积上限兜底，避免超大文件撑爆请求体。
const MULTIMODAL_PASSTHROUGH_MAX_BYTES = 2 * 1024 * 1024;
```

`compressLocalImageToDataUrl`（现 `:159`）按 plan 分派，**新增 transcode 分支**：

1. 读文件 → `resolveImagePlan(buffer, { maxPassthroughBytes: MULTIMODAL_PASSTHROUGH_MAX_BYTES })`。
2. `compress` → 走现有 `nativeImage` 解码 → 缩放 → `toJPEG` 路径，**行为完全不变**；
   若仍解码失败，抛 `error.code = 'IMAGE_DECODE_FAILED'`。
3. `passthrough` → 直接返回 `` `data:${plan.mime};base64,${buffer.toString('base64')}` ``，**不解码、不缩放**。
4. `transcode` → `await getOffscreenTranscoder().convertToJpegDataUrl(filePath, { maxEdge: MULTIMODAL_IMAGE_MAX_EDGE, quality: MULTIMODAL_IMAGE_JPEG_QUALITY })`
   （见 §4.5）。**转码输出本身就是最终 JPEG data URL**，不再经过 `nativeImage`，因此缩放与压缩一次完成。
5. `skip` → 抛错并挂 `error.code = plan.code`、`error.imageFormat`、`error.filePath`，
   消息沿用既有格式：``图片读取或压缩失败（<文件名>）：<plan.reason>``。

注意：GIF 命中分支 3、BMP/WebP 命中分支 4，都**不再尝试 `nativeImage` 解码**，
因此不会再出现「格式不受支持」。`compressLocalImageToDataUrl` 对 PNG/JPEG 仍保持
「失败要响」的语义（开发者多模态测试页依赖该语义），只新增错误码标记。

新增公开方法 `inspectLocalImage(filePath)`，返回：

```js
{ readable: true }                                   // 可交给模型（压缩 / 透传 / 转码 三种都算可读）
{ readable: false, format, label, reason }           // 不可用，reason 为中文短句（不含「，已跳过」）
```

判定：

| 情况 | 结果 |
|---|---|
| 文件读不到 | `readable: false`，`图片文件不可读` |
| `plan.action` 为 `skip` | `readable: false`，用 `plan.reason` |
| `plan.action` 为 `passthrough` / `transcode` | `readable: true`（**不做预演**，失败留待隔离层处理） |
| `plan.action` 为 `compress` | 试 `nativeImage` 解码；失败则 `readable: false`、`图片无法解析` |

> 不在预检阶段试跑转码：一次转码约 3ms，但预检 + 实转会做两遍；
> 而转码失败已被 §4.2 的逐张隔离兜住，只会影响该张并留下日志。

> **可测性说明**：格式策略与尺寸计算全部落在纯函数 `resolveImagePlan` / `computeTargetSize`，
> 可用普通 `node --test` 覆盖。PNG/JPEG 解码依赖 Electron `nativeImage`、转码依赖离屏窗口，
> 二者不新增单测，由 §7 手动验收覆盖。

### 4.2 预检跳过与批次降级

**`subjectVisionProvider.cjs`**

`inspect(request, hooks = {})` 增加第二个可选参数。

**(a) 预检（解决 P1 的「连坐」）**

在生成 `contentBatches` 之前，对每个去重后的 entry 调用
`aiService.inspectLocalImage?.(entry.asset.absolutePath)`：

- 方法不存在（测试桩、或未来别处注入）→ **跳过预检，行为退回现状**（向后兼容）。
- 返回 `readable: false` → 记 `entry.skipReason = \`${reason}，已跳过\``，
  **不进入任何批次**，也不写入 `contentCache`。
- 返回 `readable: true` 的图片（PNG/JPEG/GIF/BMP/WebP）照常进入批次，
  下游由 `compressLocalImageToDataUrl` 按格式走压缩 / 透传 / 转码分支。
- 组装结果时 `entry.skipReason` 存在 → `{ assetId, status: 'skipped', skipReason, hasSeal: false }`。

**(b) 批次降级（解决 P1 的「一颗老鼠屎」）**

把现有的批次循环改造成「先整批、失败则逐张」的两级结构：

```js
async function runContentBatch(entries) {
  try {
    return await runJsonForEntries(entries);          // 现有整批逻辑
  } catch (error) {
    if (entries.length === 1) throw error;            // 单张失败就是它自己的问题
    return await runContentBatchIndividually(entries, error);  // 逐张重试，各自成败
  }
}
```

- 逐张重试时，**只有仍然抛错的那张**写入 `entry.error`，其余正常写 `contentCache`。
- 角色判断阶段（`ROLE_BATCH_SIZE = 8`）采用同样的两级结构。
- 逐张重试会额外消耗调用次数，但仅发生在失败路径；最坏情况为批大小（4 或 8）次额外调用。
  这是为「不再连坐」付出的代价，已确认接受。

### 4.3 进度上报

**(a) Provider 侧事件契约**

`hooks` 有两个可选回调：

```js
hooks = {
  onProgress(event),  // 进度事件，见下
  onLog(event),       // 结构化诊断事件，供 developerLog 落盘（§4.4d）
}
```

`onProgress(event)` 只有两种形状：

```js
{ phase: 'content', processed, total, sealCount }  // total = 全部图片张数（含重复）
{ phase: 'role',    processed, total }             // total = 参与角色判断的图片张数
```

- `total`（content）＝ `assets.length`（本次为 7，不是去重后的 3）。
- `processed` 按**出现次数**累加：某去重图片完成时，按其 `contentHash` 的出现次数推进。
- 被跳过的图片、失败的图片**都计入** `processed`，保证进度单调走到 `total`。
- `sealCount` 同类按出现次数累加，只在 content 阶段携带。
- 触发时机：**每次 VLM 响应返回后**（成功或失败均触发），以及每个跳过判定完成时。
- 分批导致进度**按批跳跃**（小图最多 4 张一批、整页扫描 1 张一批），这是批量的必然结果。

**(b) 文案（`runSubjectSealCheck` 负责翻译）**

| 事件 | 文案 |
|---|---|
| content | `正在识别图片内容 ${processed}/${total} 张（已发现印章 ${sealCount} 张）…` |
| role | `正在判断盖章身份 ${processed}/${total} 张…` |

现有的首句 `正在检查 ${n} 张投标文件图片的印章主体。` 由 content 阶段的首个事件取代。

**(c) 状态判定补充**

当「已检查 0 张、跳过 > 0 张」时（例如全部图片格式都不支持），`visionStatus` 不应报
`success`。`runSubjectSealCheck` 的判定改为：

```js
const allFailed = stats.failedAssets > 0 && stats.inspectedAssets + stats.skippedAssets === 0;
const noneInspected = stats.inspectedAssets === 0 && stats.skippedAssets > 0;
const visionStatus = response.providerStatus === 'error' || allFailed
  ? 'error'
  : (stats.failedAssets > 0 || noneInspected) ? 'partial'
  : 'success';
```

复用既有 `partial` 状态（前端文案 `印章检查部分完成` 已存在），**不需要动前端**。

### 4.4 后台日志（真实原因与错误码）

**(a) 补上缺失的一层：图片准备失败也写 AI 日志**

`collectJsonResponseWithConfig`（`aiService.cjs:813`）与 `chatWithConfig`（`:1364`）中的
`prepareMultimodalMessages` 调用包进 try/catch，失败时先写一条 AI 日志再抛出：

```js
writeAiLog(app, config, {
  request_id: createRequestId(),
  log_title: resolveAiLogTitle(request, progressLabel),
  type: 'request-prepare-error',
  url: `${trimBaseUrl(config.base_url)}/chat/completions`,
  status: 'error',
  error: error.message,
  error_code: error.code || '',
  error_detail: { file_path: error.filePath, image_format: error.imageFormat },
  created_at: new Date().toISOString(),
});
```

这是本次最关键的一条：若不补，图片准备阶段的问题永远不产生日志。
（`:813` 抛错后不会再到 `:1364`，因此不会重复记录。）

**(b) 不再用通用文案替换真实原因**

`collectJsonResponseWithConfig` 中两处 `throw new Error(failureMessage)`（`:807`、`:859`）
保持对用户的 `message` 不变，但把底层原因挂到 `error.detail` 与 `error.cause`：

```js
const wrapped = new Error(failureMessage);
wrapped.detail = { cause: lastError?.message, issues: formatJsonIssues(lastError) };
wrapped.cause = lastError;
throw wrapped;
```

**(c) 始终打印：`logAiFailure`**

在 `electron/utils/aiLog.cjs` 新增：

```js
function logAiFailure(scope, payload = {}) { /* 单行 key=value，截断，永不抛 */ }
```

输出格式（stdout，`logger.logError`，**不受 `developer_mode` 控制**）：

```
[yibiao-client] ai-failure scope=subject.seal.content phase=content batch=1/2 index=1 \
  code=UNSUPPORTED_IMAGE_FORMAT image_format=tiff file=scan.tif message=图片格式不支持（TIFF）
```
```
[yibiao-client] ai-failure scope=subject.seal.content phase=content batch=1/1 assets=3 \
  status=400 provider_message="…" body={"error":{…}} durationMs=1284 error_name=Error
```

字段取法：
- HTTP 状态：`error.status` / `error.statusCode`；
- 服务商错误码与消息：`getAiHttpError(error)` → `{ status, statusText, body }`，body 截断至 500 字符；
- 本地错误码：`error.code`（如 `UNSUPPORTED_IMAGE_FORMAT`）；
- JSON 解析问题：`formatJsonIssues(error)`。

调用点：`aiService` 的准备失败、`subjectVisionProvider` 的批次失败与逐张失败。

**(d) 持久化：开发者日志事件**

`rejectionCheckTask.cjs` 已为检查编排创建 `developerLogger`（`:1632`），把该实例作为可选参数
传入印章检查。provider 通过 `hooks.onLog(event)` 上报结构化事件，`runSubjectSealCheck` 负责
转写为下表事件并落盘到 `logs/rejection-check/*.jsonl`：

| 事件 | 关键字段 |
|---|---|
| `rejection.subject.seal.content.started` | `total_assets`、`unique_assets`、`skipped_unsupported[]` |
| `rejection.subject.seal.asset.skipped` | `asset_id`、`format`、`reason` |
| `rejection.subject.seal.batch.failed` | `phase`、`asset_ids[]`、`status`、`error_code`、`error_message`、`provider_body` |
| `rejection.subject.seal.batch.isolated` | `phase`、`batch_size`、`failed_asset_ids[]`、`recovered_count` |
| `rejection.subject.seal.completed` | `vision_status`、`vision_stats`（全量） |

`runSubjectSealCheck` 的 `logger` 参数为**可选**，缺失时内部降级为 no-op（保证既有调用与测试不受影响）。

### 4.5 离屏转码服务（新增 `offscreenImageTranscoder.cjs`）

用于把 Chromium 能解、`nativeImage` 不能解的格式（BMP/WebP）转成 JPEG data URL。

**接口**

```js
function createOffscreenImageTranscoder({ idleDisposeMs = 30000 } = {}) {
  return {
    // 读文件 → 离屏解码 → 按 maxEdge 等比缩放（不放大）→ JPEG 编码 → 返回 data URL
    async convertToJpegDataUrl(filePath, { maxEdge, quality }) { /* → 'data:image/jpeg;base64,…' */ },
    async dispose() {},
  };
}
```

**实现要点**

- **窗口复用**：离屏窗口创建约 110~340ms，而转码本身只要 ~2.9ms/张（实测）——
  每次新建会慢 **37.7 倍**，因此必须懒创建并复用同一个窗口。
- **惰性 + 空闲释放**：首次调用时才创建窗口；`idleDisposeMs`（默认 30s）内无新请求则销毁，
  避免长期占用一个渲染进程（约 40~70MB RSS）。
- **串行化**：内部用一条 promise 链保证同一时刻只有一个转码在跑（离屏窗口是单线程的）。
- **失败与销毁防护**：每次使用前检查 `win.isDestroyed()`；`webContents` 不可用时重建窗口；
  转码异常包装成带 `error.code = 'IMAGE_TRANSCODE_FAILED'` 的错误抛出。
- **窗口配置**：与 `localImageRenderService.cjs:95-108` 保持一致
  （`show:false`、`offscreen:true`、`sandbox:true`、`contextIsolation:true`、`nodeIntegration:false`、
  `webSecurity:false`、`backgroundThrottling:false`），并在注释中指向该文件说明来源。
- **渲染层脚本**：`new Image()` → `img.decode()` → `drawImage` 到
  `computeTargetSize(naturalWidth, naturalHeight, maxEdge)` 决定尺寸的 canvas →
  `canvas.toDataURL('image/jpeg', quality)`。**缩放与编码一次完成**，不产生中间 PNG。
- **`aiService` 侧接入**：模块级懒单例 `getOffscreenTranscoder()`；应用退出时由该模块自身
  在 `app` 的 `will-quit` 上兜底销毁（若拿不到 `app` 则依赖空闲释放）。

**为什么不用 `localImageRenderService.cjs`**：它是为「HTML 页面 → 截图拼接」设计的
（CDP `Page.captureScreenshot` + 纵向拼接），与本需求（单图 → 缩放 → 编码）路径不同；
直接复用会把无关逻辑拖进来。故只复用其窗口配置这一事实，不共用代码（见 §2.2）。

## 5. 兼容性、边界与风险

| 项 | 处理 |
|---|---|
| `inspectLocalImage` 不存在 | 跳过预检，行为退回现状；既有测试桩无需改动即可通过 |
| GIF（含静态与动图） | 一律透传原字节；模型侧按静态帧识别。体积 > 2MB 才跳过 |
| GIF 透传导致请求体变大 | 仅 GIF 走此路径；本例 5KB。超 2MB 的 GIF 跳过并记原因 |
| 服务商不接受 GIF | 仅该张失败（逐张隔离已覆盖），服务商错误码进后台日志 |
| BMP / WebP | 离屏转码为 JPEG（顺带完成缩放），正常参与识别 |
| TIFF / EMF / WMF / 未识别 | 跳过并记「图片格式不支持（TIFF），已跳过」等**具体格式名** |
| 转码失败（窗口不可用、文件损坏） | 视为该张**失败**（不是跳过），进入逐张隔离，错误码进后台日志 |
| 转码窗口的生命周期 | 懒创建 + 复用 + 30s 空闲销毁；启动 `will-quit` 兜底销毁 |
| 同一时刻多个检查并发转码 | 转码器内部串行化（promise 链），不并发使用同一窗口 |
| 全部图片均被跳过 | `inspected = 0 && skipped > 0` → `visionStatus = 'partial'`（见 §4.3c） |
| 全部图片读不到 | 维持既有 `providerStatus = 'error'` 逻辑不变 |
| 整页扫描图（`page-scan`，批大小 1） | 降级逻辑天然退化为单张，无额外调用 |
| 逐张重试的成本 | 仅失败路径触发，最坏为批大小次额外调用；已确认接受 |
| `compressLocalImageToDataUrl` 对 PNG/JPEG 仍抛错 | 开发者多模态测试页语义不变（该页显式选图，失败应当响亮） |
| `logAiFailure` 自身异常 | 内部 try/catch 吞掉，绝不影响主流程 |
| 前端 | **零改动**；汇总行数字变化是行为修复的自然结果 |

## 6. 测试计划

全部为纯 JavaScript 测试，**不涉及 `better-sqlite3`**，可用普通 `node --test` 运行。

### 6.1 新增 `electron/utils/imageFormat.test.cjs`

- 魔数识别：PNG / JPEG / GIF89a / WebP / BMP / TIFF（II 与 MM 两种字节序）/ EMF / WMF
  均正确（含 `label` 与 `mime`）。
- 未知格式与截断缓冲区 → `{ format: 'unknown' }`，不抛错。
- `resolveImagePlan` 的五条分支：
  - PNG/JPEG → `{ action: 'compress' }`；
  - GIF 且体积 ≤ 上限 → `{ action: 'passthrough', mime: 'image/gif' }`；
  - GIF 且体积 > 上限 → `skip`，`code === 'PASSTHROUGH_TOO_LARGE'`；
  - BMP / WebP → `{ action: 'transcode', format }`；
  - TIFF / EMF / WMF / unknown → `skip`，`code === 'UNSUPPORTED_IMAGE_FORMAT'`，
    且 `reason` 含具体格式中文名（如 `图片格式不支持（TIFF）`）。
- 边界：体积恰好等于上限时判为「透传」（用 `≤` 比较）。
- `computeTargetSize`：
  - 长边小于 `maxEdge` → 原尺寸（**不放大**）；
  - 长边大于 `maxEdge` → 等比缩到 `maxEdge`，宽高比误差在 1px 内；
  - 横图与竖图各一组。

### 6.2 `electron/services/subjectVisionProvider.test.cjs` 增补

1. **被跳过的格式不进批次**：桩 `inspectLocalImage` 对 `hash-A` 返回 `readable:false`、
   `reason:'图片格式不支持（TIFF）'`；断言该资产 `status === 'skipped'` 且 `skipReason` 含该格式名、
   断言图片请求的 `local_image` 数量不含它。
2. **可转码格式正常入批**：桩 `inspectLocalImage` 对 BMP/WebP 资产返回 `readable:true`；
   断言该资产被计入批次（不出现 `skipped`），走既有识别路径（转码在 `aiService` 内部发生，
   provider 不感知）。
3. **批次失败降级为逐张**：让首个整批调用抛错，之后逐张成功；断言健康图 `inspected`、
   坏图 `failed`，且总调用次数 = 1（整批）+ n（逐张）。
4. **进度回调计数**：断言 `onProgress` 收到的 content 事件 `processed` 单调不减、终值等于
   `assets.length`，且 `sealCount` 与预期一致；role 事件同构。
5. **跳过资产计入进度**：`processed` 终值仍等于 `assets.length`。
6. **无 `inspectLocalImage` 时向后兼容**：行为与改动前一致（现有用例即回归基线）。

### 6.3 `electron/services/subjectCheckTask.test.cjs` 增补

7. **进度文案翻译**：以桩 `aiService`（提供 `getConfig`、`collectJsonResponse`、`inspectLocalImage`）
   驱动 `runSubjectSealCheck`，断言 `onProgress` 依次收到含
   `正在识别图片内容`、`已发现印章`、`正在判断盖章身份` 的文案。
8. **全跳过时状态为 `partial`**：全部资产 `readable:false` → 断言
   `visionStatus === 'partial'` 且 `stats.skippedAssets === assets.length`、`failedAssets === 0`。
9. **`logger` 缺省不影响结果**：不传 `logger` 时调用成功。

### 6.4 回归

- `node --test electron/services/subjectVisionProvider.test.cjs electron/services/subjectCheckTask.test.cjs electron/services/subjectNameUtils.test.cjs electron/utils/imageFormat.test.cjs`
- `node --check` 全部改动的 `.cjs`
- `cd client && npm run build`（前端未改动，此项为保险）
- **不加单测的部分**：PNG/JPEG 解码（`nativeImage`）与 BMP/WebP 转码（离屏窗口）都依赖 Electron
  运行时，无法用普通 `node --test` 覆盖；由 §7 手动验收第 1、6、7 条兜住。

## 7. 手动验收清单

1. 用**同一份含 GIF 的投标文件**重跑主体一致性检查 → 期望：
   - 汇总行为 `已检查 7/7 张，跳过 0 张，失败 0 张`（统计口径是**资产行数**，去重只影响上传次数，
     不影响这里的计数；`印章 N 张` 取决于识别结果）；
   - 该 GIF 参与印章识别；由于其印章文字为「江西瑞智信息技术有限公司」、与已确认主体
     「江西博微新技术有限公司」不符，**期望产出额外一条印章来源的 finding**
     （级别取决于名称与角色置信度是否均 ≥ 0.85）。
2. 检查过程中观察进度：应依次出现「正在识别图片内容 x/7 张（已发现印章 y 张）…」与
   「正在判断盖章身份 x/x 张…」，且数字单调递增。
3. 故意把模型 `base_url` 改错后重跑 → 期望终端出现 `[yibiao-client] ai-failure scope=subject.seal.content …`
   单行日志，且带 HTTP 状态。
4. 打开 `%APPDATA%/yibiao-client/logs/rejection-check/*.jsonl` → 期望看到
   `rejection.subject.seal.content.started`、`asset.skipped`、`completed` 事件。
5. 打开 `%APPDATA%/yibiao-client/logs/ai/` → 期望出现 `request-prepare-error` 类型的日志（若发生准备失败）。
6. **BMP / WebP 转码**：用一份含 WebP 或 BMP 图片的投标文件（或把某张 PNG 的字节换成 WebP 内容、
   扩展名同步改掉）重跑 → 期望该张**不计入跳过**，正常参与识别；`logs/rejection-check` 中无对应 skip 事件。
7. **转码窗口生命周期**：任务完成后等待超过 30s，用任务管理器确认多出的渲染进程已释放；
   再次触发检查时能正常重建窗口并成功转码。
8. **不支持的格式仍跳过**：含 TIFF / EMF / WMF 图片时，期望跳过且 skipReason 里带具体格式名。

## 8. 为什么不用 Pi Agent 执行监视器

已核实调用链（见附录 B），**废标项检查与主体一致性检查（含印章 VLM）都不经过 Pi**：

- 监视器数据源：`PiAgentMonitorWindow` → `developerAgentMonitor` IPC（`electron/ipc/developerIpc.cjs:52-66`）
  → `agentService.getMonitorSnapshot()/onMonitorEvent()` → `pi/piRuntimeService.cjs`，而后者**只被 `agentService` 使用**。
- 废标/主体检查走 `aiService.collectJsonResponse` / `aiService.chat` → `fetch(baseUrl + '/chat/completions')`。
- 监视器的事件模型是 Pi 会话形状（`task_start`、timeline、tool call、input/output、`workspaceDir`），
  与一次性 JSON 抽取调用不兼容。
- 决定性证据：Pi 会话注册的模型声明 `input: ['text']`（`pi/piSessionFactory.cjs:62`），
  **结构上不支持图片输入**，印章 VLM 不可能走 Pi。

因此诊断能力必须落在 `aiService` 与视觉 provider 层。

## 附录 A：根因取证与复现

**取证位置**（`%APPDATA%/yibiao-client/`）：

- `workspace/yibiao.sqlite`
  - `rejection_check_results.stats_json`（subject 行）＝
    `{"visionStatus":"error","visionStats":{"totalAssets":7,"candidateAssets":7,"inspectedAssets":0,"sealAssets":0,"skippedAssets":0,"reviewAssets":0,"failedAssets":7}, ...}`
  - `rejection_check_meta.check_options_json` ＝ `{"rejectionCheck":true,"typoCheck":false,"logicCheck":false,"subjectCheck":true,"subjectSealCheck":true}`
  - `rejection_check_visual_assets` ＝ 7 行，`asset_kind` 全为 `standalone`
- `logs/ai/` ＝ 仅 5 条正文检查日志，**无** `主体印章图片识别`
- `logs/rejection-check/*.jsonl` ＝ `rejection.check.stage.completed`（subject）耗时约 67 秒

**复现要点**：必须用**真实 Electron 主进程**执行，不能用 `ELECTRON_RUN_AS_NODE`
（该模式下 `require('electron').nativeImage` 为 `undefined`）。本机 shell 中该环境变量常驻，
需显式清除：

```bash
cd client
env -u ELECTRON_RUN_AS_NODE ./node_modules/electron/dist/electron.exe <script.cjs>
```

以真实 `aiService.collectJsonResponse` 传入 3 张图片（含该 GIF），稳定得到：

```
message=图片读取或压缩失败（1e643b….gif）：图片格式不受支持或文件已损坏
  at compressLocalImageToDataUrl (aiService.cjs:186)
  at prepareMultimodalMessages (aiService.cjs:220)
  at collectJsonResponseWithConfig (aiService.cjs:813)
```

## 附录 B：AI 调用路径分区（Pi 与直连）

| 场景 | 路径 |
|---|---|
| 技术方案正文生成、原方案覆盖修复、一致性审计修复 | `agentService.runTask` → Pi（`contentGenerationTask.cjs:3344/5025/5537`） |
| 可行性报告大纲、模板提取、大纲 V2、全局事实 V2 | `agentService.runTask`（`feasibilityOutlineTask.cjs:284`、`templateExtractionTask.cjs:62` 等） |
| 废标项检查、错别字、逻辑谬误 | `aiService.chat` / `collectJsonResponse`（`rejectionCheckTask.cjs:1303/1320` 的共享 `runText`/`runJson`） |
| 主体一致性正文与识别、**印章 VLM** | `aiService.collectJsonResponse`（`subjectCheckTask.cjs:370/392/576`、`subjectVisionProvider.cjs:59`） |
| 废标项提取 | `aiService.chat`（`bidAnalysisTask.cjs:222`） |
| 共同出口 | `aiService.cjs:934` → `fetch(baseUrl + '/chat/completions')` |

另记：`config.agent_mode_scenarios`（唯一键 `existing_plan_expansion_original_outline_extraction`）
全仓仅有默认值、归一化与设置页读写，**无任何消费点**，当前为空转开关。此为独立缺陷，不在本规格范围内。
