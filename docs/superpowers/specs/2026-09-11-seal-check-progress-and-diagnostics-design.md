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
因此「格式不支持就跳过」会**静默漏掉一条本应报出的印章主体不一致** —— 正确取向是「解码失败则透传」。

### 1.3 由根因派生出的三个问题

| # | 问题 | 影响 |
|---|---|---|
| P1 | 单张图片的格式问题会**连坐**同批次的全部图片 | 1 张坏图 = 7 张全失败 |
| P2 | 印章检查全程只报一句「正在检查 N 张…」，之后静默 | 长耗时段无进度 |
| P3 | 图片准备阶段的失败**不产生任何日志**；解析失败时真实原因被通用文案替换 | 出问题无法诊断 |

## 2. 目标与非目标

### 2.1 目标

- G1：**不再因解码器限制而丢掉可用的图片**。GIF 解码失败时按原始字节透传给模型；
  真正不可用的格式（WebP/BMP/未知）与不可读文件才跳过，且跳过时记录原因。
  同时保证单张图片的问题不再连坐同批次其它图片。
- G2：批次失败时按图片降级重试，只有真正失败的那张记为失败。
- G3：印章检查分两阶段上报进度，每次 VLM 响应返回后推进。
- G4：图片准备失败与 VLM/LLM 失败的真实原因与错误码，落进**后台日志**。

### 2.2 非目标（明确不做）

- **不改任何前端文件**。用户已明确：前端现有的错误展示保持原样，真实原因与错误码只落后台。
- **不接入 Pi Agent 执行监视器**。理由见 §8。
- **不做图片转码**。不引入图像解码库，也不走渲染层 canvas 转换（为一张小图不值得）。
  透传是「不压缩、不缩放、原字节直发」。
- 不引入数据库迁移；不新增可持久化字段。
- 不改 `visionMessage` 的写入逻辑。
- 不重构 `aiService` 的通用重试/超时/队列策略。

## 3. 影响面

| 文件 | 类型 | 说明 |
|---|---|---|
| `client/electron/utils/imageFormat.cjs` | 新增 | 魔数识别 + `resolveImagePlan` 策略（纯函数） |
| `client/electron/utils/imageFormat.test.cjs` | 新增 | 纯函数单测 |
| `client/electron/utils/aiLog.cjs` | 修改 | 新增 `logAiFailure` |
| `client/electron/services/aiService.cjs` | 修改 | 格式标记、`inspectLocalImage`、补准备阶段日志、保留真实错误 |
| `client/electron/services/subjectVisionProvider.cjs` | 修改 | 预检跳过、批次降级隔离、进度回调、失败日志 |
| `client/electron/services/subjectCheckTask.cjs` | 修改 | `runSubjectSealCheck` 接收 logger、翻译进度文案、区分「全跳过」状态 |
| `client/electron/services/rejectionCheckTask.cjs` | 修改 | 把既有 `developerLogger` 传入印章检查 |
| `client/electron/services/subjectVisionProvider.test.cjs` | 修改 | 新增用例 |
| `client/electron/services/subjectCheckTask.test.cjs` | 修改 | 新增用例 |

不改动：`src/` 下全部前端文件、`checkResultExportService.cjs`、`rejectionCheckStore.cjs`、数据库 schema。

## 4. 设计

### 4.1 图片格式识别、GIF 透传与其余格式跳过

**新增 `electron/utils/imageFormat.cjs`（纯函数，不依赖 Electron）**

```js
const FORMAT_SIGNATURES = [
  { format: 'png',  label: 'PNG',  mime: 'image/png',  match: (b) => b.length >= 8 && b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47 },
  { format: 'jpeg', label: 'JPEG', mime: 'image/jpeg', match: (b) => b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff },
  { format: 'gif',  label: 'GIF',  mime: 'image/gif',  match: (b) => b.length >= 6 && b.toString('latin1', 0, 4) === 'GIF8' },
  { format: 'webp', label: 'WebP', mime: 'image/webp', match: (b) => b.length >= 12 && b.toString('latin1', 0, 4) === 'RIFF' && b.toString('latin1', 8, 12) === 'WEBP' },
  { format: 'bmp',  label: 'BMP',  mime: 'image/bmp',  match: (b) => b.length >= 2 && b[0] === 0x42 && b[1] === 0x4d },
];

// Electron nativeImage.createFromBuffer 只稳定支持 PNG/JPEG。
const NATIVE_DECODABLE_FORMATS = new Set(['png', 'jpeg']);
// 解码器不支持的格式中，可原字节透传给模型的（实测 GIF 可被模型正常识别）。
const PASSTHROUGH_FORMATS = new Set(['gif']);

function detectImageFormat(buffer) { /* → { format, label, mime }，未识别为 { format:'unknown', label:'未知格式', mime:'' } */ }

// 单一决策入口：把「该压缩 / 该透传 / 必须跳过」的策略集中在一处。
// 纯函数、可单测，是 imageFormat.test.cjs 的主要被测对象。
function resolveImagePlan(buffer, { maxPassthroughBytes }) {
  // → { action: 'compress' }                                  // PNG / JPEG
  // → { action: 'passthrough', mime: 'image/gif' }             // GIF 且体积未超限
  // → { action: 'skip', format, label, code, reason }          // 其余一律跳过
}
```

导出：`detectImageFormat`、`resolveImagePlan`、`NATIVE_DECODABLE_FORMATS`、`PASSTHROUGH_FORMATS`。

`resolveImagePlan` 的完整判定（**唯一真相来源**）：

| 条件 | 返回 | `reason`（不含「，已跳过」后缀） |
|---|---|---|
| `format` 为 `png` / `jpeg` | `{ action: 'compress' }` | — |
| `format` 为 `gif` 且 `buffer.length ≤ maxPassthroughBytes` | `{ action: 'passthrough', mime: 'image/gif' }` | — |
| `format` 为 `gif` 且超限 | `{ action: 'skip', code: 'PASSTHROUGH_TOO_LARGE' }` | `图片体积超出透传上限` |
| `format` 为 `webp` / `bmp` / `unknown` | `{ action: 'skip', code: 'UNSUPPORTED_IMAGE_FORMAT' }` | `图片格式不支持（WebP）` |

**`aiService.cjs`**

新增常量：

```js
// 透传不做缩放，用体积上限兜底，避免超大文件撑爆请求体。
const MULTIMODAL_PASSTHROUGH_MAX_BYTES = 2 * 1024 * 1024;
```

`compressLocalImageToDataUrl`（现 `:159`）按 `resolveImagePlan` 分派：

1. 读文件 → `resolveImagePlan(buffer, { maxPassthroughBytes: MULTIMODAL_PASSTHROUGH_MAX_BYTES })`。
2. `action === 'passthrough'` → 直接返回 `` `data:${plan.mime};base64,${buffer.toString('base64')}` ``，
   **不解码、不缩放**。
3. `action === 'compress'` → 走现有 `nativeImage` 解码 → 必要时缩放 → `toJPEG` 路径，**行为完全不变**；
   若仍解码失败，抛 `error.code = 'IMAGE_DECODE_FAILED'`。
4. `action === 'skip'` → 抛错并挂 `error.code = plan.code`、`error.imageFormat`、`error.filePath`，
   消息沿用既有格式：``图片读取或压缩失败（<文件名>）：<plan.reason>``。

注意：GIF 命中分支 2，**不再尝试解码**，因此不会再走到「格式不受支持」。
`compressLocalImageToDataUrl` 对 PNG/JPEG 仍保持「失败要响」的语义（开发者多模态测试页依赖该语义），
只新增错误码标记。

新增公开方法 `inspectLocalImage(filePath)`，返回：

```js
{ readable: true }                                   // 可交给模型（含 GIF 透传）
{ readable: false, format, label, reason }           // 不可用，reason 为中文短句（不含「，已跳过」）
```

判定：文件读不到 → `图片文件不可读`；否则按 `resolveImagePlan`：`skip` → 用其 `reason`；
`passthrough` → `readable: true`；`compress` → 再试 `nativeImage` 解码，
失败则 `readable: false`、`图片无法解析`。

> **可测性说明**：格式策略（含 GIF 透传与体积上限）全部落在纯函数 `resolveImagePlan`，
> 可用普通 `node --test` 覆盖。PNG/JPEG 的实际解码依赖 Electron `nativeImage`，
> 不新增单测，由 §7 手动验收覆盖。

### 4.2 预检跳过与批次降级

**`subjectVisionProvider.cjs`**

`inspect(request, hooks = {})` 增加第二个可选参数。

**(a) 预检（解决 P1 的「连坐」）**

在生成 `contentBatches` 之前，对每个去重后的 entry 调用
`aiService.inspectLocalImage?.(entry.asset.absolutePath)`：

- 方法不存在（测试桩、或未来别处注入）→ **跳过预检，行为退回现状**（向后兼容）。
- 返回 `readable: false` → 记 `entry.skipReason = \`${reason}，已跳过\``，
  **不进入任何批次**，也不写入 `contentCache`。
- 返回 `readable: true` 的 GIF 照常进入批次（下游由 `compressLocalImageToDataUrl` 走透传分支）。
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
  code=UNSUPPORTED_IMAGE_FORMAT image_format=webp file=logo.webp message=图片格式不受支持
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

## 5. 兼容性、边界与风险

| 项 | 处理 |
|---|---|
| `inspectLocalImage` 不存在 | 跳过预检，行为退回现状；既有测试桩无需改动即可通过 |
| GIF（含静态与动图） | 一律透传原字节；模型侧按静态帧识别。体积 > 2MB 才跳过 |
| GIF 透传导致请求体变大 | 仅 GIF 走此路径；本例 5KB。超 2MB 的 GIF 跳过并记原因 |
| 服务商不支持 GIF | 仅该张失败（逐张隔离已覆盖），服务商错误码进后台日志 |
| WebP / BMP / unknown | 跳过并记「图片格式不支持（X），已跳过」 |
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

- PNG / JPEG / GIF89a / WebP / BMP 魔数识别正确（含 `label` 与 `mime`）。
- 未知格式与截断缓冲区 → `{ format: 'unknown' }`，不抛错。
- `resolveImagePlan` 的四条分支：
  - PNG/JPEG → `{ action: 'compress' }`；
  - GIF 且体积 ≤ 上限 → `{ action: 'passthrough', mime: 'image/gif' }`；
  - GIF 且体积 > 上限 → `skip` 且 `code === 'PASSTHROUGH_TOO_LARGE'`；
  - WebP / BMP / unknown → `skip` 且 `code === 'UNSUPPORTED_IMAGE_FORMAT'`，`reason` 含格式中文名。
- 边界：体积恰好等于上限时判定为「透传」（用 `≤` 比较）。

### 6.2 `electron/services/subjectVisionProvider.test.cjs` 增补

1. **被跳过的格式不进批次**：桩 `inspectLocalImage` 对 `hash-A` 返回 `readable:false`；
   断言该资产 `status === 'skipped'` 且 `skipReason` 含格式、断言图片请求的 `local_image` 数量不含它。
2. **GIF 可读时正常入批**：桩 `inspectLocalImage` 对 GIF 资产返回 `readable:true`；
   断言该资产被计入批次（不出现 `skipped`），走既有识别路径。
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

- `node --test electron/services/subjectVisionProvider.test.cjs electron/services/subjectCheckTask.test.cjs electron/services/subjectNameUtils.test.cjs`
- `node --check` 全部改动的 `.cjs`
- `cd client && npm run build`（前端未改动，此项为保险）

## 7. 手动验收清单

1. 用**同一份含 GIF 的投标文件**重跑主体一致性检查 → 期望：
   - 汇总行为 `已检查 3/7 张，跳过 0 张，失败 0 张`（GIF 走透传，不再失败也不再跳过）；
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
6. 另找一份含 **WebP** 图片的投标文件（或临时把某张 PNG 的扩展名与内容改成 WebP）→
   期望该张计入「跳过」、其余正常检查，不再连坐。

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
