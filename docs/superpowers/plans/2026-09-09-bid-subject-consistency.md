# 投标主体一致性检查实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有"废标项检查"中新增第四类"主体一致性检查"：自动识别投标主体候选、用户确认正式主体与允许简称后，检查整份投标文件中所有语义上代表投标方的位置（含可选在线 VLM 印章检查），输出"明确不一致 / 需人工复核"结果。

**Architecture:** 主体候选识别是新增受管任务 `bid-subject-identification`（`rejection-check` 组内互斥）；正式检查作为 `rejection-check-run` 的第四个并行分支 `subject`。DOCX 导入时新增 Open XML 媒体提取（adm-zip），媒体写盘 + SQLite 只存元数据；VLM 复用 `aiService` 的文本模型多模态能力，通过 `SubjectVisionProvider` 边界封装（两阶段：按 contentHash 缓存的内容识别 + 每出现位置一次的纯文本角色判断）。

**Tech Stack:** Electron Main（CommonJS `.cjs`）、better-sqlite3（schemaVersion 23→24）、adm-zip（已有依赖 `^0.6.0`，无需新增）、Renderer ESM TypeScript + React、`node --test` 定向测试 + `npm run smoke:electron-native` + `npm run build`。

**Spec:** `docs/superpowers/specs/2026-09-09-bid-subject-consistency-design.md`（本计划从该规格论证而来，执行者必须同时阅读规格与计划）

## Global Constraints

以下约束来自规格，**每个任务都隐含遵守**：

1. 产品代码、提示词和测试不得硬编码任何真实公司名称；测试至少使用两组互不相关的虚构主体（如 `北方长风智能科技有限公司` / `南屿云图数据服务有限责任公司`），证明不存在特定公司硬编码。
2. 首版只支持单一投标主体；联合体或多合法主体场景必须提示当前能力不适用，不能通过自动加入白名单弱化限制。
3. 名称规范化只允许：去首尾空白、合并异常空格换行、统一全半角括号、统一常见中文标点、英文大小写不敏感。禁止：去除"有限公司/有限责任公司"后缀、母子公司/总分公司等同、编辑距离判等、未经用户确认的简称加入允许列表。
4. 自动预选门槛（全部满足才预选第一候选）：第一候选置信度 ≥ `0.85`；第一、第二候选置信度差值 ≥ `0.10`；至少存在一条明确的投标身份原文。否则展示候选但不预选。**任何情况下都必须由用户确认。**
5. 明确不一致（confirmed-mismatch）门槛：完整公司名或未允许的明确简称 + 上下文明确由投标方发言/承诺/签署/担责 + 可定位原文 + 与正式主体和允许简称均不匹配 + 名称与角色置信度均 ≥ `0.85`。任何一项不满足只能进 needs-review 或不输出。
6. 防幻觉校验（每条文本结果必须全部通过，否则直接丢弃、不得降级进入人工复核）：`bidDocumentId` 属于当前输入；`originalText` 能在对应原文中定位（忽略空白差异的 indexOf）；`detectedSubject` 确实出现在原文摘录中；正式主体和允许简称没有被列为异常；相同文件、位置、主体的结果去重。
7. 固定语义代称列表：`我公司、本公司、我方、本投标人、投标人、应答人、供应商、响应人`。语义代称只帮助模型理解发言角色，不作为公司名称参与字符串比较；只有代称且没有错误具名主体时不生成异常。
8. 印章检查是可选增强：未开启多模态、视觉请求失败或图片无法识别时，正文主体检查仍可成功；此时视觉状态为 `unavailable`/`error`/`partial`，UI 不得显示"全部印章通过"。
9. VLM 复用设置页"文本模型"配置（服务商、API Key、Base URL、模型名、`multimodal_enabled`）；本功能不新增独立 VLM/OCR 配置；"生图模型"不参与；未开启多模态时不发起任何图片请求。
10. 效率控制：图片内容识别按 `contentHash` 缓存（同一内容只上传识别一次）；位置角色判断按 `contentHash + nearbyContextHash` 缓存；独立小图每批最多 4 张；整页扫描图每次只发 1 张；沿用 `aiService` 队列、取消、重试、日志和最长边 2048px 压缩。
11. SQLite 不保存图片二进制，只存相对路径和元数据；导入提交失败必须回收媒体文件；删除投标文件、清空工作区、导入重复文件时必须同步清理媒体目录，不留孤立图片。
12. 运行时 migration（`sqliteDatabase.cjs`）与根目录 `sql/workspace_schema.sql` 必须同步；schemaVersion `23` → `24`。
13. Renderer 不接收 Main 本地绝对路径；图片预览通过现有 `yibiao-asset://` 协议（新增 hostname `rejection-check-media`）。
14. Excel 导出不嵌入图片；废标检查工作簿从四张表扩展为五张（检查概览/废标项/错别字/逻辑问题/投标主体）。
15. UI 使用全局 CSS + Radix 基础组件，不用 Tailwind；用户可见文案中文；提示走 `ToastProvider`，确认/长文案用 `AppDialog`。
16. 本地客户端可信边界：不在 IPC/Store 层重复堆叠参数校验，只在用户输入层（Dialog）校验。

## 现状要点（执行者必读）

1. 没有 root `package.json`；所有客户端命令先 `cd client`。
2. `better-sqlite3` 按 Electron ABI 编译，纯 `node --test` 无法 require `sqliteDatabase.cjs`/`rejectionCheckStore.cjs`（会报 ABI 错误）。涉及 DB 的改动用 `npm run smoke:electron-native` 验证；纯逻辑模块（不 require sqliteDatabase/Store）用 `node --test`。
3. `rejection-check-run` 现有三分支在 `runRejectionCheckTask` 内用 `runOne(kind, label, runner, resultKey, inputSignature)` 包装并行执行（`client/electron/services/rejectionCheckTask.cjs:1654`）；`checkpointTask(taskPartial, workspacePartial, eventPatch?)` 原子落库（第三参是事件附加字段 `eventPatch`，**不是**持久化开关，调用时不要传布尔），`updateTask` 仅内存+推送。
4. Store 的结果持久化由 `resultFieldTypes` 映射驱动（`rejectionCheckStore.cjs:35`）：新增 `subjectCheckResult: 'subject'` 后，`updateRejectionCheckTransaction` 自动落库 `subjectCheckResult`；但 `saveFindingRows`/`loadFindingRows`/`clearFindingRows`/`saveResult`/`loadResult` 需要各自补 subject 分支。
5. `fileService.importRejectionCheckDocument()` 当前 `preserveImages: false`，返回的 `parsedDocuments` 元素**没有** `source_path` 字段（对比 `importTechnicalPlanDocument` 有），Task 6 需要补上。
6. `taskService.cancelRejectionCheckTasks(reason, taskTypes)` 已支持按类型过滤；导入/删除投标文件时当前取消列表是 `['rejection-check-run']`，需加入 `'bid-subject-identification'`。
7. `aiService.collectJsonResponse(request)` 支持 `messages[].content` 数组，`{ type: 'local_image', path }` 部件会被 `prepareMultimodalMessages` 压缩为 `image_url`（最长边 2048px JPEG）；未开启 `multimodal_enabled` 时抛错"当前文本模型未开启多模态支持，请在设置中开启后重试"。`aiService.getConfig()` 返回含 `multimodal_enabled` 的文本模型配置。
8. Renderer 读配置用 `window.yibiao.config.load()`，多模态开关在 `config.components.text_model_provider.multimodal_enabled`。
9. `yibiao-asset://` 协议在 `main.cjs:259` `registerAssetProtocol()` 的 `assetRoots` hostname 白名单映射本地目录，路径做了越界防护。
10. 测试命令在 `client/` 下：`node --test electron/services/<name>.test.cjs`。

## File Structure

**创建：**

| 文件 | 职责 |
|---|---|
| `client/electron/services/subjectNameUtils.cjs` | 纯函数：语义代称、名称规范化、允许简称匹配、主体检查输入签名、自动预选门槛 |
| `client/electron/services/docxVisualAssetExtractor.cjs` | 从 DOCX zip 提取 DrawingML/VML 图片引用与媒体文件（不依赖 DOM 解析器） |
| `client/electron/services/subjectMediaFiles.cjs` | 媒体落盘、目录回收、绝对路径解析（含越界防护） |
| `client/electron/services/subjectVisionProvider.cjs` | `SubjectVisionProvider` 在线 VLM 实现（两阶段 + 缓存 + 批量控制） |
| `client/electron/services/subjectCheckTask.cjs` | 主体候选识别任务、正文主体检查、印章检查编排 |
| `client/electron/services/*.test.cjs`（对应上述 5 个） | `node --test` 定向测试 |
| `client/src/features/rejection-check/components/SubjectConfirmDialog.tsx` | 主体确认 Dialog（候选、正式名称、简称编辑、印章开关、在线提示） |

**修改：**

| 文件 | 变更 |
|---|---|
| `client/electron/services/sqliteDatabase.cjs` | schemaVersion 24、三张新表 migration、schemaHealth 分组、`rejection_check_results.stats_json` 列 |
| `sql/workspace_schema.sql` | 同步 v24 DDL |
| `client/electron/services/rejectionCheckStore.cjs` | subject 结果/主体档案/视觉资产读写、options 扩展、导入/删除/清空联动 |
| `client/electron/services/fileService.cjs` | `importRejectionCheckDocument` 返回 `source_path` |
| `client/electron/services/rejectionCheckTask.cjs` | `runOne` 兼容 `{ findings, stats }`、新增 subject 分支 |
| `client/electron/services/taskService.cjs` | 注册 `bid-subject-identification`、确认主体、恢复逻辑、取消列表 |
| `client/electron/services/checkResultExportService.cjs` | Excel 第五张表、subject 签名筛选 |
| `client/electron/ipc/rejectionCheckIpc.cjs`、`client/electron/ipc/index.cjs` | 新通道注册/白名单 |
| `client/electron/main.cjs` | assetRoots 增加 `rejection-check-media` |
| `client/electron/preload.cjs`、`client/src/shared/types/ipc.ts` | bridge API 与类型同步 |
| `client/src/features/rejection-check/types.ts` | 主体相关类型 |
| `client/src/features/rejection-check/pages/RejectionCheckPage.tsx` | 确认流程、subject 页签、检查配置 |
| `client/src/features/rejection-check/exportState.ts` | 导出可用性扩展 |

---

### Task 1: 主体名称纯函数工具 `subjectNameUtils.cjs`

**Files:**
- Create: `client/electron/services/subjectNameUtils.cjs`
- Test: `client/electron/services/subjectNameUtils.test.cjs`

**Interfaces:**
- Consumes: 无（纯函数，不依赖任何项目模块，可用 `node --test` 直接测试）。
- Produces（后续任务依赖的精确签名）:
  - `SEMANTIC_PRONOUNS: string[]` — 固定语义代称列表
  - `SUBJECT_CHECK_PROTOCOL_VERSION = 1`
  - `normalizeSubjectName(value: string): string`
  - `matchesAllowedSubject(value: string, officialName: string, aliases: string[]): boolean`
  - `createSubjectCheckInputSignature({ bidSignature, officialName, aliases, subjectSealCheck, protocolVersion? }): string`
  - `applySubjectAutoSelectRules(candidates): number` — 返回预选下标，不预选返回 `-1`
  - `looksLikeCompanyName(value: string): boolean` — 粗略判断是否是具名机构（用于 unclear 角色过滤）

- [x] **Step 1: 写失败测试**

创建 `client/electron/services/subjectNameUtils.test.cjs`：

```js
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
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd client && node --test electron/services/subjectNameUtils.test.cjs`
Expected: FAIL，报 `Cannot find module './subjectNameUtils.cjs'`

- [x] **Step 3: 写实现**

创建 `client/electron/services/subjectNameUtils.cjs`：

```js
'use strict';

// 固定语义代称（spec §7.3）：只帮助模型理解发言角色，不作为公司名称参与字符串比较。
const SEMANTIC_PRONOUNS = ['我公司', '本公司', '我方', '本投标人', '投标人', '应答人', '供应商', '响应人'];

const SUBJECT_CHECK_PROTOCOL_VERSION = 1;

const PRONOUN_SET = new Set(SEMANTIC_PRONOUNS);

// 允许的规范化（spec §7.4）：空白折叠、全半角括号/常见中文标点统一、英文小写。
function normalizeSubjectName(value) {
  return String(value || '')
    .replace(/\s+/g, ' ')
    .replace(/（/g, '(')
    .replace(/）/g, ')')
    .replace(/【/g, '[')
    .replace(/】/g, ']')
    .replace(/，/g, ',')
    .replace(/：/g, ':')
    .trim()
    .toLowerCase();
}

function matchesAllowedSubject(value, officialName, aliases = []) {
  const normalized = normalizeSubjectName(value);
  if (!normalized) return false;
  const allowed = [officialName, ...(Array.isArray(aliases) ? aliases : [])]
    .map(normalizeSubjectName)
    .filter(Boolean);
  return allowed.includes(normalized);
}

// 主体检查输入签名（spec §11.3）：全部投标文件签名 + 正式主体 + 排序后允许简称 + 印章开关 + 协议版本。
function createSubjectCheckInputSignature({
  bidSignature,
  officialName,
  aliases,
  subjectSealCheck,
  protocolVersion = SUBJECT_CHECK_PROTOCOL_VERSION,
}) {
  const sortedAliases = (Array.isArray(aliases) ? aliases : [])
    .map((item) => String(item || '').trim())
    .filter(Boolean)
    .sort()
    .map(normalizeSubjectName)
    .join('|');
  return [
    `yibiao-subject-check-v${protocolVersion}`,
    String(bidSignature || ''),
    normalizeSubjectName(officialName),
    sortedAliases,
    subjectSealCheck ? 'seal-on' : 'seal-off',
  ].join('\n---yibiao-subject-signature---\n');
}

// 自动预选门槛（spec §7.2）：置信度≥0.85、与次名差值≥0.10、有明确投标身份原文。
function applySubjectAutoSelectRules(candidates = []) {
  const list = (Array.isArray(candidates) ? candidates : []).filter((item) => item && String(item.name || '').trim());
  if (!list.length) return -1;
  const sorted = [...list].sort((a, b) => Number(b.confidence || 0) - Number(a.confidence || 0));
  const first = sorted[0];
  const second = sorted[1];
  if (Number(first.confidence || 0) < 0.85) return -1;
  if (second && Number(first.confidence || 0) - Number(second.confidence || 0) < 0.10) return -1;
  const hasBidderEvidence = Boolean(String(first.roleEvidence || '').trim() || String(first.originalText || '').trim());
  if (!hasBidderEvidence) return -1;
  return candidates.indexOf(first);
}

// 粗略判断是否是具名机构（用于 unclear 角色下过滤纯代称）：包含常见机构后缀即视为具名。
function looksLikeCompanyName(value) {
  const text = String(value || '').trim();
  if (!text || PRONOUN_SET.has(text)) return false;
  return /公司|集团|研究院|研究所|事务所|中心|院|所|局|处|队$/.test(text);
}

module.exports = {
  SEMANTIC_PRONOUNS,
  SUBJECT_CHECK_PROTOCOL_VERSION,
  normalizeSubjectName,
  matchesAllowedSubject,
  createSubjectCheckInputSignature,
  applySubjectAutoSelectRules,
  looksLikeCompanyName,
};
```

- [x] **Step 4: 运行测试确认通过**

Run: `cd client && node --test electron/services/subjectNameUtils.test.cjs`
Expected: PASS（全部用例通过）

- [x] **Step 5: 提交**

```bash
git add client/electron/services/subjectNameUtils.cjs client/electron/services/subjectNameUtils.test.cjs
git commit -m "feat: 新增投标主体名称纯函数工具（规范化/匹配/签名/预选门槛）"
```

---

### Task 2: SQLite migration v24（三张新表 + stats_json 列）

**Files:**
- Modify: `client/electron/services/sqliteDatabase.cjs`（`schemaVersion` 第 6 行、`createRejectionCheckSchema` 之后、`schemaHealthTableGroups` 数组末尾、`schemaHealthColumnGroups` 数组末尾、`migrations` 数组末尾）
- Modify: `sql/workspace_schema.sql`（文件末尾追加 v24 段）

**Interfaces:**
- Consumes: 现有 `addColumnIfMissing(db, tableName, columnName, columnType)`（`sqliteDatabase.cjs:705`）。
- Produces（Task 3/6/15 依赖）:
  - 表 `rejection_check_subject_profile`（单行，id=1）
  - 表 `rejection_check_visual_assets`
  - 表 `rejection_check_subject_findings`（含 `asset_relative_path` 列，Renderer 预览用）
  - `rejection_check_results` 新列 `stats_json TEXT`（存主体视觉运行状态）
  - `createBidSubjectCheckSchema(db)` 导出供 smoke 使用（`module.exports` 已有 `__test__` 惯例时跟随现状）

- [x] **Step 1: 在 `createRejectionCheckSchema` 函数之后新增建表函数**

在 `client/electron/services/sqliteDatabase.cjs` 的 `createRejectionCheckSchema` 结束（约 703 行 `}` 后）插入：

```js
function createBidSubjectCheckSchema(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS rejection_check_subject_profile (
      id INTEGER PRIMARY KEY CHECK (id = 1),
      status TEXT NOT NULL DEFAULT 'idle',
      official_name TEXT NOT NULL DEFAULT '',
      aliases_json TEXT,
      candidates_json TEXT,
      bid_signature TEXT,
      confirmed_at TEXT,
      error TEXT,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS rejection_check_visual_assets (
      asset_id TEXT PRIMARY KEY,
      bid_document_id TEXT NOT NULL,
      media_name TEXT NOT NULL,
      content_hash TEXT NOT NULL,
      relative_path TEXT NOT NULL,
      mime_type TEXT NOT NULL,
      width INTEGER NOT NULL DEFAULT 0,
      height INTEGER NOT NULL DEFAULT 0,
      placement TEXT NOT NULL DEFAULT 'unknown',
      asset_kind TEXT NOT NULL DEFAULT 'unknown',
      location_hint TEXT NOT NULL DEFAULT '',
      nearby_text TEXT NOT NULL DEFAULT '',
      section_title TEXT,
      sort_order INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_rejection_check_visual_assets_document
    ON rejection_check_visual_assets(bid_document_id, sort_order);

    CREATE INDEX IF NOT EXISTS idx_rejection_check_visual_assets_hash
    ON rejection_check_visual_assets(content_hash);

    CREATE TABLE IF NOT EXISTS rejection_check_subject_findings (
      finding_id TEXT PRIMARY KEY,
      bid_document_id TEXT,
      level TEXT NOT NULL,
      source TEXT NOT NULL,
      expected_subject TEXT NOT NULL,
      detected_subject TEXT NOT NULL,
      location_kind TEXT NOT NULL,
      title TEXT NOT NULL,
      summary TEXT NOT NULL,
      location_hint TEXT NOT NULL,
      original_text TEXT NOT NULL,
      role_reason TEXT NOT NULL,
      mismatch_reason TEXT NOT NULL,
      suggestion TEXT NOT NULL,
      confidence REAL NOT NULL DEFAULT 0,
      visual_asset_id TEXT,
      asset_relative_path TEXT,
      seal_crop_relative_path TEXT,
      sort_order INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_rejection_check_subject_findings_order
    ON rejection_check_subject_findings(sort_order);

    CREATE INDEX IF NOT EXISTS idx_rejection_check_subject_findings_level
    ON rejection_check_subject_findings(level);
  `);
  addColumnIfMissing(db, 'rejection_check_results', 'stats_json', 'TEXT');
}
```

- [x] **Step 2: 注册 migration 与健康检查**

同文件四处修改：

1. `schemaVersion` 第 6 行 `const schemaVersion = 23;` → `const schemaVersion = 24;`
2. `schemaHealthTableGroups` 数组末尾（version 23 组之后）追加：

```js
  {
    version: 24,
    tables: ['rejection_check_subject_profile', 'rejection_check_visual_assets', 'rejection_check_subject_findings'],
    repair: createBidSubjectCheckSchema,
  },
```

3. `schemaHealthColumnGroups` 数组末尾追加：

```js
  {
    version: 24,
    table: 'rejection_check_results',
    columns: { stats_json: 'TEXT' },
  },
```

4. `migrations` 数组末尾（version 23 之后）追加：

```js
  {
    version: 24,
    description: '新增投标主体一致性检查表结构',
    up: createBidSubjectCheckSchema,
  },
```

- [x] **Step 3: 同步 `sql/workspace_schema.sql`**

在文件开头 `PRAGMA user_version = 23;` 改为 `PRAGMA user_version = 24;`，再在文件末尾追加（与上述 DDL 完全一致，外加注释头）：

```sql
-- v24: 投标主体一致性检查
（粘贴 Step 1 中 db.exec 内的全部 SQL，去掉 JS 反引号与缩进调整，保持语句一致）
```

注意：静态 schema 的 `PRAGMA user_version` 必须与运行时 `schemaVersion` 同为 `24`，否则新建数据库和升级数据库会出现版本漂移。

- [x] **Step 4: 语法与迁移验证**

Run: `cd client && node --check electron/services/sqliteDatabase.cjs`
Expected: 无输出（语法通过）

Run: `cd client && npm run smoke:electron-native`
Expected: 退出码 0（migrations 与 schemaHealth 通过；老库升级路径覆盖）

- [x] **Step 5: 提交**

```bash
git add client/electron/services/sqliteDatabase.cjs sql/workspace_schema.sql
git commit -m "feat: 数据库 v24 新增投标主体一致性检查表结构"
```

---

### Task 3: Store 主体档案 / 视觉资产 / 主体结果读写

**Files:**
- Modify: `client/electron/services/rejectionCheckStore.cjs`

**Interfaces:**
- Consumes: Task 1 的 `createSubjectCheckInputSignature`、`applySubjectAutoSelectRules`；Task 2 的三张表。
- Produces（后续任务依赖的精确签名，全部挂到 `createRejectionCheckStore` 返回对象上）:
  - `loadSubjectProfile(): { status, officialName, aliases, candidates, bidSignature, preselectIndex, confirmedAt, error, updatedAt }`
  - `saveSubjectProfile(profile)` — 全量 upsert（status ∈ idle|identifying|pending-confirmation|confirmed|error）
  - `resetSubjectProfile(reason?)` — 置回 idle 并清空名称/候选，`error` 记 reason
  - `confirmSubject({ officialName, aliases }): { success, message }` — 校验非空官方名后写 confirmed 档案 + `bidSignature = getCurrentBidSignature()` + `subjectCheckResult` 置 idle
  - `getCurrentBidSignature(): string` — 与 `runRejectionCheckTask` 相同算法的投标文件签名
  - `getSubjectMediaRootDir(): string` — 返回 `path.join(rejectionCheckDir, 'subject-media')`（闭包内已有 `rejectionCheckDir`；Task 11 主体分支用它定位媒体根目录）
  - `listVisualAssets(bidDocumentId?): SubjectVisualAssetRow[]`
  - `saveVisualAssets(bidDocumentId, assets)` — 整体替换该文件的资产行
  - `clearVisualAssets(bidDocumentId?)` — 清单个文件或全部
  - `deleteSubjectFinding(findingId): boolean`
  - 状态字段新增：`subjectProfile`、`subjectCheckResult`（含 `stats: { visionStatus, visionStats }`）、`subjectIdentificationTask`
  - `checkOptions` 扩展为 `{ rejectionCheck, typoCheck, logicCheck, subjectCheck, subjectSealCheck }`

- [x] **Step 1: 扩展初始状态与字段映射**

`rejectionCheckStore.cjs` 顶部修改：

```js
const initialState = {
  // ...现有字段保持不变...
  checkOptions: { rejectionCheck: true, typoCheck: true, logicCheck: true, subjectCheck: true, subjectSealCheck: true },
  // ...现有 rejectionCheckResult/typoCheckResult/logicCheckResult 保持...
  subjectCheckResult: { status: 'idle', findings: [], stats: undefined },
  subjectProfile: { status: 'idle', officialName: '', aliases: [], candidates: [] },
  subjectIdentificationTask: undefined,
};
```

`taskFieldTypes` 增加一行（`checkTask` 之后）：

```js
  subjectIdentificationTask: 'bid-subject-identification',
```

`resultFieldTypes` 增加一行：

```js
  subjectCheckResult: 'subject',
```

`normalizeCheckResultTab`（第 100 行）改为：

```js
function normalizeCheckResultTab(value) {
  return ['rejection', 'typo', 'logic', 'subject'].includes(value) ? value : 'rejection';
}
```

`normalizeCheckOptions`（第 104 行）改为（subjectSealCheck 跟随 subjectCheck）：

```js
function normalizeCheckOptions(options) {
  const subjectCheck = options?.subjectCheck !== false;
  return {
    rejectionCheck: true,
    typoCheck: options?.typoCheck !== false,
    logicCheck: options?.logicCheck !== false,
    subjectCheck,
    subjectSealCheck: subjectCheck && options?.subjectSealCheck !== false,
  };
}
```

- [x] **Step 2: 主体结果 stats 持久化**

`saveResult`（第 580 行）在 INSERT 列清单中加入 `stats_json`（`@stats_json`），值来源：

```js
      stats_json: hasOwn(result, 'stats')
        ? jsonOrNull(result.stats)
        : existing?.stats_json || null,
```

（`ON CONFLICT ... DO UPDATE SET` 同步加 `stats_json = excluded.stats_json`。）

`loadResult`（第 699 行）返回对象增加一行（无行时 base 也补 `stats: undefined`）：

```js
      stats: safeJsonParse(row.stats_json, undefined),
```

- [x] **Step 3: 主体发现项行读写**

`clearFindingRows` 增加：

```js
    if (resultType === 'subject') db.prepare('DELETE FROM rejection_check_subject_findings').run();
```

`saveFindingRows` 增加 subject 分支：

```js
    if (resultType === 'subject') {
      const insert = db.prepare(`
        INSERT INTO rejection_check_subject_findings (
          finding_id, bid_document_id, level, source, expected_subject, detected_subject, location_kind, title, summary, location_hint, original_text, role_reason, mismatch_reason, suggestion, confidence, visual_asset_id, asset_relative_path, seal_crop_relative_path, sort_order, created_at, updated_at
        ) VALUES (
          @finding_id, @bid_document_id, @level, @source, @expected_subject, @detected_subject, @location_kind, @title, @summary, @location_hint, @original_text, @role_reason, @mismatch_reason, @suggestion, @confidence, @visual_asset_id, @asset_relative_path, @seal_crop_relative_path, @sort_order, @created_at, @updated_at
        )
      `);
      findings.forEach((item, index) => insert.run({
        finding_id: String(item.id || `subject-finding-${index + 1}`),
        bid_document_id: item.bidDocumentId ? String(item.bidDocumentId) : null,
        level: item.level === 'confirmed-mismatch' ? 'confirmed-mismatch' : 'needs-review',
        source: item.source === 'seal' ? 'seal' : 'text',
        expected_subject: String(item.expectedSubject || ''),
        detected_subject: String(item.detectedSubject || ''),
        location_kind: ['body', 'table', 'signature', 'standalone-image', 'scanned-page'].includes(item.locationKind) ? item.locationKind : 'body',
        title: String(item.title || ''),
        summary: String(item.summary || ''),
        location_hint: String(item.locationHint || ''),
        original_text: String(item.originalText || ''),
        role_reason: String(item.roleReason || ''),
        mismatch_reason: String(item.mismatchReason || ''),
        suggestion: String(item.suggestion || ''),
        confidence: Math.max(0, Math.min(1, Number(item.confidence || 0))),
        visual_asset_id: item.visualAssetId ? String(item.visualAssetId) : null,
        asset_relative_path: item.assetRelativePath ? String(item.assetRelativePath) : null,
        seal_crop_relative_path: item.sealCropRelativePath ? String(item.sealCropRelativePath) : null,
        sort_order: index,
        created_at: timestamp,
        updated_at: timestamp,
      }));
    }
```

`loadFindingRows` 增加 subject 分支（在 logic 分支 return 之前）：

```js
    if (resultType === 'subject') {
      return db.prepare('SELECT * FROM rejection_check_subject_findings ORDER BY sort_order ASC').all().map((item) => ({
        id: item.finding_id,
        bidDocumentId: item.bid_document_id || fallbackBidDocumentId,
        level: item.level,
        source: item.source,
        expectedSubject: item.expected_subject,
        detectedSubject: item.detected_subject,
        locationKind: item.location_kind,
        title: item.title,
        summary: item.summary,
        locationHint: item.location_hint,
        originalText: item.original_text,
        roleReason: item.role_reason,
        mismatchReason: item.mismatch_reason,
        suggestion: item.suggestion,
        confidence: Number(item.confidence || 0),
        visualAssetId: item.visual_asset_id || undefined,
        assetRelativePath: item.asset_relative_path || undefined,
        sealCropRelativePath: item.seal_crop_relative_path || undefined,
      }));
    }
```

- [x] **Step 4: 主体档案 / 视觉资产 / 单条删除函数**

在 `saveUiState` 之前插入（`now()`、`safeJsonParse`、`jsonOrNull`、`createDocumentSignature` 均为文件内已有工具）：

```js
  function loadSubjectProfile() {
    const row = db.prepare('SELECT * FROM rejection_check_subject_profile WHERE id = 1').get();
    if (!row) return { ...initialState.subjectProfile };
    return {
      status: normalizeStatus(row.status, ['idle', 'identifying', 'pending-confirmation', 'confirmed', 'error'], 'idle'),
      officialName: row.official_name || '',
      aliases: safeJsonParse(row.aliases_json, []) || [],
      candidates: safeJsonParse(row.candidates_json, []) || [],
      bidSignature: row.bid_signature || undefined,
      preselectIndex: Number.isInteger(safeJsonParse(row.candidates_json, {})?.preselectIndex)
        ? safeJsonParse(row.candidates_json, {}).preselectIndex
        : undefined,
      confirmedAt: row.confirmed_at || undefined,
      error: row.error || undefined,
      updatedAt: row.updated_at || undefined,
    };
  }

  function saveSubjectProfile(profile) {
    const normalized = {
      status: normalizeStatus(profile?.status, ['idle', 'identifying', 'pending-confirmation', 'confirmed', 'error'], 'idle'),
      official_name: String(profile?.officialName || ''),
      aliases_json: jsonOrNull(Array.isArray(profile?.aliases) ? profile.aliases : []),
      candidates_json: jsonOrNull({
        items: Array.isArray(profile?.candidates) ? profile.candidates : [],
        preselectIndex: Number.isInteger(profile?.preselectIndex) ? profile.preselectIndex : -1,
      }),
      bid_signature: profile?.bidSignature ? String(profile.bidSignature) : null,
      confirmed_at: profile?.confirmedAt ? String(profile.confirmedAt) : null,
      error: profile?.error ? String(profile.error) : null,
      updated_at: now(),
    };
    db.prepare(`
      INSERT INTO rejection_check_subject_profile (id, status, official_name, aliases_json, candidates_json, bid_signature, confirmed_at, error, updated_at)
      VALUES (1, @status, @official_name, @aliases_json, @candidates_json, @bid_signature, @confirmed_at, @error, @updated_at)
      ON CONFLICT(id) DO UPDATE SET
        status = excluded.status,
        official_name = excluded.official_name,
        aliases_json = excluded.aliases_json,
        candidates_json = excluded.candidates_json,
        bid_signature = excluded.bid_signature,
        confirmed_at = excluded.confirmed_at,
        error = excluded.error,
        updated_at = excluded.updated_at
    `).run(normalized);
    return loadSubjectProfile();
  }

  function resetSubjectProfile(reason) {
    return saveSubjectProfile({ status: 'idle', officialName: '', aliases: [], candidates: [], error: reason || undefined });
  }

  function getCurrentBidSignature() {
    const rows = db.prepare("SELECT * FROM rejection_check_documents WHERE role = 'bid' ORDER BY sort_order ASC, imported_at ASC").all();
    return rows.map(documentFromRow).filter(Boolean)
      .map((document) => createDocumentSignature(document))
      .filter(Boolean)
      .join('\n---yibiao-rejection-bid-signature---\n');
  }

  function getSubjectMediaRootDir() {
    return path.join(rejectionCheckDir, 'subject-media');
  }
  // 注：若 rejectionCheckStore.cjs 顶部尚未 require('node:path')，需补上（文件已 require fs 与 getRejectionCheckDir）。

  function confirmSubject({ officialName, aliases } = {}) {
    const name = String(officialName || '').trim();
    if (!name) return { success: false, message: '请填写正式投标主体名称' };
    const normalizedAliases = (Array.isArray(aliases) ? aliases : [])
      .map((item) => String(item || '').trim())
      .filter(Boolean);
    const previous = loadSubjectProfile();
    const profile = saveSubjectProfile({
      status: 'confirmed',
      officialName: name,
      aliases: normalizedAliases,
      candidates: previous.candidates,
      preselectIndex: previous.preselectIndex,
      bidSignature: getCurrentBidSignature(),
      confirmedAt: now(),
    });
    // 修改正式主体或简称都会让旧主体结果失效（spec §11.4）。
    saveResult('subject', { status: 'idle', findings: [], inputSignature: null, activeFindingId: null, progressMessage: null, error: null, stats: null, updatedAt: now() });
    clearFindingRows('subject');
    return { success: true, message: '投标主体已确认', profile };
  }

  function listVisualAssets(bidDocumentId) {
    const rows = bidDocumentId
      ? db.prepare('SELECT * FROM rejection_check_visual_assets WHERE bid_document_id = ? ORDER BY sort_order ASC').all(String(bidDocumentId))
      : db.prepare('SELECT * FROM rejection_check_visual_assets ORDER BY bid_document_id ASC, sort_order ASC').all();
    return rows.map((row) => ({
      id: row.asset_id,
      bidDocumentId: row.bid_document_id,
      mediaName: row.media_name,
      contentHash: row.content_hash,
      relativePath: row.relative_path,
      mimeType: row.mime_type,
      width: Number(row.width || 0),
      height: Number(row.height || 0),
      placement: row.placement,
      assetKind: row.asset_kind,
      locationHint: row.location_hint,
      nearbyText: row.nearby_text,
      sectionTitle: row.section_title || undefined,
    }));
  }

  function saveVisualAssets(bidDocumentId, assets = []) {
    const documentId = String(bidDocumentId || '');
    db.prepare('DELETE FROM rejection_check_visual_assets WHERE bid_document_id = ?').run(documentId);
    const timestamp = now();
    const insert = db.prepare(`
      INSERT INTO rejection_check_visual_assets (
        asset_id, bid_document_id, media_name, content_hash, relative_path, mime_type, width, height, placement, asset_kind, location_hint, nearby_text, section_title, sort_order, created_at
      ) VALUES (
        @asset_id, @bid_document_id, @media_name, @content_hash, @relative_path, @mime_type, @width, @height, @placement, @asset_kind, @location_hint, @nearby_text, @section_title, @sort_order, @created_at
      )
    `);
    (Array.isArray(assets) ? assets : []).forEach((asset, index) => insert.run({
      asset_id: String(asset.id || `${documentId}-asset-${index + 1}`),
      bid_document_id: documentId,
      media_name: String(asset.mediaName || ''),
      content_hash: String(asset.contentHash || ''),
      relative_path: String(asset.relativePath || ''),
      mime_type: String(asset.mimeType || 'application/octet-stream'),
      width: Math.max(0, Math.round(Number(asset.width || 0))),
      height: Math.max(0, Math.round(Number(asset.height || 0))),
      placement: asset.placement === 'anchor' ? 'anchor' : asset.placement === 'inline' ? 'inline' : 'unknown',
      asset_kind: ['standalone', 'page-scan', 'unknown'].includes(asset.assetKind) ? asset.assetKind : 'unknown',
      location_hint: String(asset.locationHint || ''),
      nearby_text: String(asset.nearbyText || ''),
      section_title: asset.sectionTitle ? String(asset.sectionTitle) : null,
      sort_order: index,
      created_at: timestamp,
    }));
  }

  function clearVisualAssets(bidDocumentId) {
    if (bidDocumentId) {
      db.prepare('DELETE FROM rejection_check_visual_assets WHERE bid_document_id = ?').run(String(bidDocumentId));
      return;
    }
    db.prepare('DELETE FROM rejection_check_visual_assets').run();
  }

  function deleteSubjectFinding(findingId) {
    const result = db.prepare('DELETE FROM rejection_check_subject_findings WHERE finding_id = ?').run(String(findingId || ''));
    return result.changes > 0;
  }
```

注意 `loadSubjectProfile` 里 `preselectIndex` 从 `candidates_json` 读取两次不优雅，可直接：

```js
  function loadSubjectProfile() {
    const row = db.prepare('SELECT * FROM rejection_check_subject_profile WHERE id = 1').get();
    if (!row) return { ...initialState.subjectProfile };
    const candidatesPayload = safeJsonParse(row.candidates_json, {}) || {};
    const items = Array.isArray(candidatesPayload.items) ? candidatesPayload.items : (Array.isArray(candidatesPayload) ? candidatesPayload : []);
    return {
      status: normalizeStatus(row.status, ['idle', 'identifying', 'pending-confirmation', 'confirmed', 'error'], 'idle'),
      officialName: row.official_name || '',
      aliases: safeJsonParse(row.aliases_json, []) || [],
      candidates: items,
      preselectIndex: Number.isInteger(candidatesPayload.preselectIndex) ? candidatesPayload.preselectIndex : undefined,
      bidSignature: row.bid_signature || undefined,
      confirmedAt: row.confirmed_at || undefined,
      error: row.error || undefined,
      updatedAt: row.updated_at || undefined,
    };
  }
```

（用这个版本替换上面的首个 `loadSubjectProfile` 实现。）

- [x] **Step 5: 装配 load/清理/导出**

1. `loadRejectionCheck`（第 790 行）返回对象增加：

```js
      subjectCheckResult: loadResult('subject'),
      subjectProfile: loadSubjectProfile(),
```

2. `clearCheckResults`（第 755 行）增加一行：

```js
    db.prepare('DELETE FROM rejection_check_subject_findings').run();
```

3. `clearRejectionCheck`（第 1112 行）事务内增加：

```js
      db.prepare('DELETE FROM rejection_check_subject_profile').run();
      db.prepare('DELETE FROM rejection_check_visual_assets').run();
      db.prepare('DELETE FROM rejection_check_subject_findings').run();
```

（目录清理已覆盖 `subject-media`，因为整个 `rejectionCheckDir` 被递归删除。）

4. `createRejectionCheckStore` 返回对象增加：

```js
    loadSubjectProfile,
    saveSubjectProfile,
    resetSubjectProfile,
    confirmSubject,
    getCurrentBidSignature,
    getSubjectMediaRootDir,
    listVisualAssets,
    saveVisualAssets,
    clearVisualAssets,
    deleteSubjectFinding,
```

- [x] **Step 6: 验证**

Run: `cd client && node --check electron/services/rejectionCheckStore.cjs`
Expected: 无输出

Run: `cd client && npm run smoke:electron-native`
Expected: 退出码 0

- [x] **Step 7: 提交**

```bash
git add client/electron/services/rejectionCheckStore.cjs
git commit -m "feat: 废标检查 Store 支持主体档案、视觉资产与主体结果读写"
```

---

### Task 4: DOCX 视觉材料提取器 `docxVisualAssetExtractor.cjs`

**Files:**
- Create: `client/electron/services/docxVisualAssetExtractor.cjs`
- Test: `client/electron/services/docxVisualAssetExtractor.test.cjs`

**Interfaces:**
- Consumes: `adm-zip`（`client/package.json` 已有 `^0.6.0`）、`node:crypto`。不依赖 sqliteDatabase，可 `node --test`。
- Produces:
  - `extractDocxVisualAssets(docxFilePath): Promise<{ occurrences: DocxVisualOccurrence[], mediaFiles: DocxMediaFile[] }>`
  - `DocxVisualOccurrence = { mediaName, relationshipId?, placement: 'inline'|'anchor', width, height, assetKind: 'standalone'|'page-scan'|'unknown', locationHint, nearbyText, sectionTitle? }`
  - `DocxMediaFile = { mediaName, buffer: Buffer, contentHash, mimeType }`
  - `isDocxFilePath(filePath): boolean`（`.docx` 大小写不敏感判断）
  - 抛错：文件不存在/非 zip/关键 part 缺失（调用方按 spec §13"DOCX 局部媒体损坏：记录资产错误并继续处理其他资产和正文"处理）

- [x] **Step 1: 写失败测试（内联构造最小 DOCX 夹具）**

创建 `client/electron/services/docxVisualAssetExtractor.test.cjs`：

```js
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const AdmZip = require('adm-zip');
const { extractDocxVisualAssets, isDocxFilePath } = require('./docxVisualAssetExtractor.cjs');

// 1x1 PNG（透明像素），用于充当媒体文件。
const PNG_BUFFER = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
);

function buildMinimalDocx() {
  const zip = new AdmZip();
  zip.addFile('word/document.xml', Buffer.from(`<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
  xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
  xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
  xmlns:v="urn:schemas-microsoft-com:vml">
  <w:body>
    <w:p><w:r><w:t>第一章 投标函</w:t></w:r></w:p>
    <w:p><w:r><w:t>致：某某招标代理公司。我公司郑重承诺：</w:t></w:r></w:p>
    <w:p><w:r>
      <w:drawing><wp:inline distT="0" distB="0"><wp:extent cx="1905000" cy="635000"/>
        <a:graphic><a:graphicData><pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
          <pic:blipFill><a:blip r:embed="rId11"/></pic:blipFill>
        </pic:pic></a:graphicData></a:graphic>
      </wp:inline></w:drawing>
    </w:r></w:p>
    <w:p><w:r><w:t>授权委托书：本授权书自签订之日起生效。</w:t></w:r></w:p>
    <w:tbl><w:tr><w:tc><w:p><w:r>
      <w:drawing><wp:anchor behindDoc="1"><wp:extent cx="5715000" cy="7620000"/>
        <a:graphic><a:graphicData><pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
          <pic:blipFill><a:blip r:embed="rId11"/></pic:blipFill>
        </pic:pic></a:graphicData></a:graphic>
      </wp:anchor></w:drawing>
      <v:shape style="width:60pt;height:60pt"><v:imagedata r:id="rId12"/></v:shape>
      </w:r></w:p></w:tc></w:tr></w:tbl>
    <w:p><w:r><w:t>报价表：含税总价 1234567.89 元。</w:t></w:r></w:p>
  </w:body>
</w:document>`, 'utf8'));
  zip.addFile('word/_rels/document.xml.rels', Buffer.from(`<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId11" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/seal-a.png"/>
  <Relationship Id="rId12" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/logo-b.png"/>
</Relationships>`, 'utf8'));
  zip.addFile('word/media/seal-a.png', PNG_BUFFER);
  zip.addFile('word/media/logo-b.png', PNG_BUFFER);
  return zip;
}

function writeFixture(zip, fileName) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-extract-test-'));
  const filePath = path.join(dir, fileName);
  zip.writeZip(filePath);
  return filePath;
}

test('isDocxFilePath 识别 .docx（大小写不敏感）', () => {
  assert.strictEqual(isDocxFilePath('C:/a/b/投标文件.DOCX'), true);
  assert.strictEqual(isDocxFilePath('C:/a/b/投标文件.docx'), true);
  assert.strictEqual(isDocxFilePath('C:/a/b/投标文件.pdf'), false);
});

test('提取 inline/anchor/VML 引用并保留重复图片的每个出现位置', async () => {
  const filePath = writeFixture(buildMinimalDocx(), 'bid.docx');
  const { occurrences, mediaFiles } = await extractDocxVisualAssets(filePath);

  // rId11 出现两次（inline + anchor），rId12 出现一次（VML）。
  assert.strictEqual(occurrences.filter((item) => item.mediaName === 'media/seal-a.png').length, 2);
  assert.strictEqual(occurrences.filter((item) => item.mediaName === 'media/logo-b.png').length, 1);

  const inline = occurrences.find((item) => item.placement === 'inline');
  const anchor = occurrences.find((item) => item.placement === 'anchor');
  assert.ok(inline, '缺少 inline 引用');
  assert.ok(anchor, '缺少 anchor 引用');

  // EMU→px：1905000/9525=200，635000/9525≈66。
  assert.strictEqual(inline.width, 200);
  // VML pt→px：60pt×12700 EMU/9525=80px。
  const vml = occurrences.find((item) => item.mediaName === 'media/logo-b.png');
  assert.strictEqual(vml.width, 80);

  // 大尺寸 anchor 判定 page-scan；小图 standalone。
  assert.strictEqual(anchor.assetKind, 'page-scan');
  assert.strictEqual(inline.assetKind, 'standalone');

  // 附近文字映射包含上下文线索。
  assert.ok(inline.nearbyText.includes('承诺') || inline.nearbyText.includes('投标函'), 'inline 附近文字应包含上下文');
  assert.ok(vml.nearbyText.length >= 0);

  // 媒体文件读取 + 内容哈希：同一 buffer 只按文件名区分，hash 相同。
  assert.strictEqual(mediaFiles.length, 2);
  const seal = mediaFiles.find((item) => item.mediaName === 'media/seal-a.png');
  assert.strictEqual(seal.mimeType, 'image/png');
  assert.strictEqual(seal.contentHash, mediaFiles.find((item) => item.mediaName === 'media/logo-b.png').contentHash);
});

test('页眉页脚中的图片也会被提取', async () => {
  const zip = buildMinimalDocx();
  zip.addFile('word/header1.xml', Buffer.from(`<?xml version="1.0" encoding="UTF-8"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
  xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
  xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
  <w:p><w:r><w:drawing><wp:inline><wp:extent cx="952500" cy="952500"/>
    <a:graphic><a:graphicData><pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
      <pic:blipFill><a:blip r:embed="rId21"/></pic:blipFill></pic:pic></a:graphicData></a:graphic>
  </wp:inline></w:drawing></w:r></w:p>
</w:hdr>`, 'utf8'));
  zip.addFile('word/_rels/header1.xml.rels', Buffer.from(`<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId21" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/header-logo.png"/>
</Relationships>`, 'utf8'));
  zip.addFile('word/media/header-logo.png', PNG_BUFFER);
  const filePath = writeFixture(zip, 'bid-with-header.docx');
  const { occurrences, mediaFiles } = await extractDocxVisualAssets(filePath);
  assert.ok(occurrences.some((item) => item.mediaName === 'media/header-logo.png'));
  assert.ok(mediaFiles.some((item) => item.mediaName === 'media/header-logo.png'));
});

test('非 zip 或缺失关键 part 时抛错', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-extract-test-'));
  const notZip = path.join(dir, 'bad.docx');
  fs.writeFileSync(notZip, 'not a zip', 'utf8');
  await assert.rejects(() => extractDocxVisualAssets(notZip));
  const emptyZip = path.join(dir, 'empty.docx');
  new AdmZip().writeZip(emptyZip);
  await assert.rejects(() => extractDocxVisualAssets(emptyZip));
});
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd client && node --test electron/services/docxVisualAssetExtractor.test.cjs`
Expected: FAIL，`Cannot find module './docxVisualAssetExtractor.cjs'`

- [x] **Step 3: 写实现**

创建 `client/electron/services/docxVisualAssetExtractor.cjs`：

```js
'use strict';

const crypto = require('node:crypto');
const AdmZip = require('adm-zip');

const EMU_PER_PX = 9525;
const PT_TO_EMU = 12700;
const PAGE_SCAN_MIN_WIDTH_PX = 500;
const PAGE_SCAN_MIN_HEIGHT_PX = 650;
const NEARBY_TEXT_RADIUS = 160;

const MIME_BY_EXTENSION = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.bmp': 'image/bmp',
  '.tif': 'image/tiff',
  '.tiff': 'image/tiff',
  '.webp': 'image/webp',
  '.emf': 'image/emf',
  '.wmf': 'image/wmf',
};

function isDocxFilePath(filePath) {
  return /\.docx$/i.test(String(filePath || ''));
}

function parseRelationships(xml) {
  const map = new Map();
  const re = /<Relationship\b[^>]*>/g;
  for (const tag of String(xml || '').matchAll(re)) {
    const id = /\bId="([^"]+)"/.exec(tag[0])?.[1];
    const target = /\bTarget="([^"]+)"/.exec(tag[0])?.[1];
    if (id && target) map.set(id, target.replace(/^\/+/, ''));
  }
  return map;
}

function stripXmlToText(xml) {
  return String(xml || '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&apos;/g, "'")
    .replace(/\s+/g, ' ')
    .trim();
}

function extractNearbyText(xml, index) {
  const start = Math.max(0, index - NEARBY_TEXT_RADIUS * 3);
  const end = Math.min(xml.length, index + NEARBY_TEXT_RADIUS * 3);
  const text = stripXmlToText(xml.slice(start, end));
  const half = Math.floor(text.length / 2);
  return text.slice(Math.max(0, half - NEARBY_TEXT_RADIUS), half + NEARBY_TEXT_RADIUS);
}

function extractSectionTitle(xml, index) {
  // 向前找最近一个标题样式段落或加粗短行的纯文本。
  const before = xml.slice(Math.max(0, index - 4000), index);
  const headingMatch = /<w:p\b[^>]*>(?:(?!<\/w:p>)[\s\S])*?<w:t[^>]*>([^<]{2,60})<\/w:t>(?:(?!<\/w:p>)[\s\S])*?<\/w:p>\s*$/.exec(before);
  return headingMatch ? stripXmlToText(headingMatch[1]) : undefined;
}

function classifyAssetKind(widthPx, heightPx, placement) {
  if (widthPx >= PAGE_SCAN_MIN_WIDTH_PX && heightPx >= PAGE_SCAN_MIN_HEIGHT_PX) return 'page-scan';
  return placement === 'inline' || placement === 'anchor' ? 'standalone' : 'unknown';
}

function collectDrawingOccurrences(xml, relationships, partLabel) {
  const occurrences = [];
  const drawingRe = /<w:drawing>[\s\S]*?<\/w:drawing>/g;
  for (const drawing of String(xml || '').matchAll(drawingRe)) {
    const block = drawing[0];
    const embed = /\ba:blip\b[^>]*\br:embed="([^"]+)"/.exec(block)?.[1]
      ?? /\br:embed="([^"]+)"/.exec(block)?.[1];
    if (!embed) continue;
    const target = relationships.get(embed);
    if (!target || !/^word\/media\//.test(target) && !/^media\//.test(target)) continue;
    const placement = /<wp:anchor\b/.test(block) ? 'anchor' : 'inline';
    const extent = /<wp:extent\b[^>]*\bcx="(\d+)"[^>]*\bcy="(\d+)"/.exec(block)
      ?? /<wp:extent\b[^>]*\bcy="(\d+)"[^>]*\bcx="(\d+)"/.exec(block);
    let widthPx = 0;
    let heightPx = 0;
    if (extent) {
      widthPx = Math.round(Number(extent[1]) / EMU_PER_PX);
      heightPx = Math.round(Number(extent[2]) / EMU_PER_PX);
    }
    occurrences.push({
      mediaName: target.replace(/^word\//, ''),
      relationshipId: embed,
      placement,
      width: widthPx,
      height: heightPx,
      assetKind: classifyAssetKind(widthPx, heightPx, placement),
      locationHint: partLabel,
      nearbyText: extractNearbyText(xml, drawing.index),
      sectionTitle: extractSectionTitle(xml, drawing.index),
    });
  }
  return occurrences;
}

function collectVmlOccurrences(xml, relationships, partLabel) {
  const occurrences = [];
  const vmlRe = /<v:imagedata\b[^>]*\br:id="([^"]+)"[^>]*\/?>/g;
  for (const match of String(xml || '').matchAll(vmlRe)) {
    const target = relationships.get(match[1]);
    if (!target || !/^word\/media\//.test(target) && !/^media\//.test(target)) continue;
    // VML 尺寸在外层 v:shape 的 style 中，单位 pt。
    const shapeStart = xml.lastIndexOf('<v:shape', match.index);
    const styleBlock = shapeStart >= 0 ? xml.slice(shapeStart, match.index) : '';
    const widthPt = Number(/width:([\d.]+)pt/i.exec(styleBlock)?.[1] || 0);
    const heightPt = Number(/height:([\d.]+)pt/i.exec(styleBlock)?.[1] || 0);
    const widthPx = Math.round((widthPt * PT_TO_EMU) / EMU_PER_PX);
    const heightPx = Math.round((heightPt * PT_TO_EMU) / EMU_PER_PX);
    occurrences.push({
      mediaName: target.replace(/^word\//, ''),
      relationshipId: match[1],
      placement: 'inline',
      width: widthPx,
      height: heightPx,
      assetKind: classifyAssetKind(widthPx, heightPx, 'inline'),
      locationHint: partLabel,
      nearbyText: extractNearbyText(xml, match.index),
      sectionTitle: extractSectionTitle(xml, match.index),
    });
  }
  return occurrences;
}

async function extractDocxVisualAssets(docxFilePath) {
  const zip = new AdmZip(docxFilePath);
  const readPart = (name) => {
    const entry = zip.getEntry(name);
    return entry ? entry.getData().toString('utf8') : null;
  };

  const documentXml = readPart('word/document.xml');
  if (!documentXml) throw new Error('DOCX 缺少 word/document.xml，无法提取图片');

  const parts = [{ xml: documentXml, rels: parseRelationships(readPart('word/_rels/document.xml.rels') || ''), label: '正文' }];
  for (const entry of zip.getEntries()) {
    const name = entry.entryName;
    const partMatch = /^word\/(header\d*|footer\d*)\.xml$/.exec(name);
    if (!partMatch) continue;
    const relsName = `word/_rels/${partMatch[1]}.xml.rels`;
    parts.push({
      xml: entry.getData().toString('utf8'),
      rels: parseRelationships(readPart(relsName) || ''),
      label: partMatch[1].startsWith('header') ? '页眉' : '页脚',
    });
  }

  const occurrences = [];
  for (const part of parts) {
    occurrences.push(...collectDrawingOccurrences(part.xml, part.rels, part.label));
    occurrences.push(...collectVmlOccurrences(part.xml, part.rels, part.label));
  }

  const mediaFiles = [];
  for (const entry of zip.getEntries()) {
    if (!/^word\/media\/[^/]+$/.test(entry.entryName)) continue;
    const buffer = entry.getData();
    const extension = /\.[^.]+$/.exec(entry.entryName)?.[0]?.toLowerCase() || '';
    mediaFiles.push({
      mediaName: entry.entryName.replace(/^word\//, ''),
      buffer,
      contentHash: crypto.createHash('sha256').update(buffer).digest('hex'),
      mimeType: MIME_BY_EXTENSION[extension] || 'application/octet-stream',
    });
  }

  return { occurrences, mediaFiles };
}

module.exports = {
  extractDocxVisualAssets,
  isDocxFilePath,
};
```

- [x] **Step 4: 运行测试确认通过**

Run: `cd client && node --test electron/services/docxVisualAssetExtractor.test.cjs`
Expected: PASS（若 `extractSectionTitle` 的正则在夹具上取不到标题，允许 `sectionTitle` 为 undefined，断言不强制）

- [x] **Step 5: 提交**

```bash
git add client/electron/services/docxVisualAssetExtractor.cjs client/electron/services/docxVisualAssetExtractor.test.cjs
git commit -m "feat: 新增 DOCX 视觉材料提取器（DrawingML/VML/页眉页脚/媒体文件）"
```

---

### Task 5: 媒体落盘工具 `subjectMediaFiles.cjs`

**Files:**
- Create: `client/electron/services/subjectMediaFiles.cjs`
- Test: `client/electron/services/subjectMediaFiles.test.cjs`

**Interfaces:**
- Consumes: `node:fs`、`node:path`。不依赖 sqliteDatabase，可 `node --test`。
- Produces:
  - `getSubjectMediaDir(rejectionCheckDir, bidDocumentId): string` — `<rejection-check>/subject-media/<safeDocId>`
  - `writeVisualMediaFiles({ mediaDir, mediaFiles }): Map<mediaName, relativePath>` — 按 `contentHash.ext` 去重写盘，返回 mediaName→相对 mediaDir 的路径
  - `removeVisualMediaDir(mediaDir): Promise<void>` — 递归删除（force，幂等）
  - `resolveVisualAssetAbsolutePath({ mediaRootDir, bidDocumentId, relativePath }): string` — 拼接并做越界防护（resolve 后必须仍在 mediaRootDir 内，否则抛错）
  - `ASSET_PROTOCOL_HOSTNAME = 'rejection-check-media'`
  - `buildAssetPreviewUrl({ bidDocumentId, relativePath }): string` — `yibiao-asset://rejection-check-media/<docId>/<relativePath>`

- [x] **Step 1: 写失败测试**

创建 `client/electron/services/subjectMediaFiles.test.cjs`：

```js
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {
  getSubjectMediaDir,
  writeVisualMediaFiles,
  removeVisualMediaDir,
  resolveVisualAssetAbsolutePath,
  buildAssetPreviewUrl,
  ASSET_PROTOCOL_HOSTNAME,
} = require('./subjectMediaFiles.cjs');

function makeTempRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'subject-media-test-'));
}

test('writeVisualMediaFiles 相同内容哈希只写一份，mediaName 映射各自路径', () => {
  const root = makeTempRoot();
  const mediaDir = getSubjectMediaDir(root, 'bid-abc');
  const buffer = Buffer.from('fake-image-bytes');
  const paths = writeVisualMediaFiles({
    mediaDir,
    mediaFiles: [
      { mediaName: 'media/a.png', buffer, contentHash: 'hash-1', mimeType: 'image/png' },
      { mediaName: 'media/b.png', buffer, contentHash: 'hash-1', mimeType: 'image/png' },
      { mediaName: 'media/c.jpg', buffer: Buffer.from('other'), contentHash: 'hash-2', mimeType: 'image/jpeg' },
    ],
  });
  assert.strictEqual(paths.get('media/a.png'), path.posix.join('hash-1', 'hash-1.png'));
  assert.strictEqual(paths.get('media/b.png'), path.posix.join('hash-1', 'hash-1.png'));
  assert.strictEqual(paths.get('media/c.jpg'), path.posix.join('hash-2', 'hash-2.jpg'));
  assert.strictEqual(fs.readdirSync(path.join(mediaDir, 'hash-1')).length, 1);
  assert.ok(fs.existsSync(path.join(mediaDir, 'hash-2', 'hash-2.jpg')));
});

test('resolveVisualAssetAbsolutePath 阻止越界路径', () => {
  const root = makeTempRoot();
  const resolved = resolveVisualAssetAbsolutePath({
    mediaRootDir: path.join(root, 'subject-media'),
    bidDocumentId: 'bid-abc',
    relativePath: 'hash-1/hash-1.png',
  });
  assert.strictEqual(resolved, path.join(root, 'subject-media', 'bid-abc', 'hash-1', 'hash-1.png'));
  assert.throws(() => resolveVisualAssetAbsolutePath({
    mediaRootDir: path.join(root, 'subject-media'),
    bidDocumentId: 'bid-abc',
    relativePath: '../../user_config.json',
  }));
});

test('removeVisualMediaDir 幂等删除', async () => {
  const root = makeTempRoot();
  const mediaDir = getSubjectMediaDir(root, 'bid-abc');
  writeVisualMediaFiles({ mediaDir, mediaFiles: [{ mediaName: 'm.png', buffer: Buffer.from('x'), contentHash: 'h', mimeType: 'image/png' }] });
  await removeVisualMediaDir(mediaDir);
  assert.strictEqual(fs.existsSync(mediaDir), false);
  await removeVisualMediaDir(mediaDir);
});

test('buildAssetPreviewUrl 使用 rejection-check-media 主机名', () => {
  assert.strictEqual(ASSET_PROTOCOL_HOSTNAME, 'rejection-check-media');
  const url = buildAssetPreviewUrl({ bidDocumentId: 'bid-abc', relativePath: 'hash-1/hash-1.png' });
  assert.strictEqual(url, 'yibiao-asset://rejection-check-media/bid-abc/hash-1/hash-1.png');
  const encoded = buildAssetPreviewUrl({ bidDocumentId: 'bid 中文', relativePath: 'h/a.png' });
  assert.ok(encoded.startsWith(`yibiao-asset://${ASSET_PROTOCOL_HOSTNAME}/bid%20`));
});
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd client && node --test electron/services/subjectMediaFiles.test.cjs`
Expected: FAIL，`Cannot find module './subjectMediaFiles.cjs'`

- [x] **Step 3: 写实现**

创建 `client/electron/services/subjectMediaFiles.cjs`：

```js
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const ASSET_PROTOCOL_HOSTNAME = 'rejection-check-media';

function sanitizeDocumentId(documentId) {
  return String(documentId || '').replace(/[^a-zA-Z0-9_-]+/g, '_') || 'unknown';
}

function extensionForMimeType(mimeType) {
  const map = {
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/gif': 'gif',
    'image/bmp': 'bmp',
    'image/tiff': 'tif',
    'image/webp': 'webp',
    'image/emf': 'emf',
    'image/wmf': 'wmf',
  };
  return map[mimeType] || 'bin';
}

function getSubjectMediaDir(rejectionCheckDir, bidDocumentId) {
  return path.join(String(rejectionCheckDir || ''), 'subject-media', sanitizeDocumentId(bidDocumentId));
}

function writeVisualMediaFiles({ mediaDir, mediaFiles = [] }) {
  const written = new Map();
  for (const media of mediaFiles) {
    if (!media?.mediaName || !Buffer.isBuffer(media.buffer)) continue;
    const hash = String(media.contentHash || '').replace(/[^a-fA-F0-9]/g, '') || 'unknown';
    const extension = extensionForMimeType(media.mimeType);
    const relativePath = path.posix.join(hash, `${hash}.${extension}`);
    const targetPath = path.join(mediaDir, relativePath);
    if (!written.has(media.mediaName)) {
      fs.mkdirSync(path.dirname(targetPath), { recursive: true });
      if (!fs.existsSync(targetPath)) fs.writeFileSync(targetPath, media.buffer);
    }
    written.set(media.mediaName, relativePath);
  }
  return written;
}

async function removeVisualMediaDir(mediaDir) {
  await fs.promises.rm(String(mediaDir || ''), { recursive: true, force: true });
}

function resolveVisualAssetAbsolutePath({ mediaRootDir, bidDocumentId, relativePath }) {
  const baseDir = path.resolve(String(mediaRootDir || ''), sanitizeDocumentId(bidDocumentId));
  const filePath = path.resolve(baseDir, String(relativePath || ''));
  if (filePath !== baseDir && !filePath.startsWith(`${baseDir}${path.sep}`)) {
    throw new Error('图片路径超出工作区范围');
  }
  return filePath;
}

function buildAssetPreviewUrl({ bidDocumentId, relativePath }) {
  const documentPart = encodeURIComponent(sanitizeDocumentId(bidDocumentId));
  const pathPart = String(relativePath || '').split('/').map(encodeURIComponent).join('/');
  return `yibiao-asset://${ASSET_PROTOCOL_HOSTNAME}/${documentPart}/${pathPart}`;
}

module.exports = {
  ASSET_PROTOCOL_HOSTNAME,
  getSubjectMediaDir,
  writeVisualMediaFiles,
  removeVisualMediaDir,
  resolveVisualAssetAbsolutePath,
  buildAssetPreviewUrl,
};
```

- [x] **Step 4: 运行测试确认通过**

Run: `cd client && node --test electron/services/subjectMediaFiles.test.cjs`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add client/electron/services/subjectMediaFiles.cjs client/electron/services/subjectMediaFiles.test.cjs
git commit -m "feat: 新增主体检查媒体落盘与回收工具"
```

---

### Task 6: 导入链路集成（fileService + Store bid 分支 + 清理联动）

**Files:**
- Modify: `client/electron/services/fileService.cjs`（`importRejectionCheckDocument` 内 `parsedDocuments.push` 处，约 727 行）
- Modify: `client/electron/services/rejectionCheckStore.cjs`（`importDocument` bid 分支约 926-976 行、`removeDocument` bid 分支约 1090-1099 行）
- Modify: `client/electron/services/taskService.cjs`（`importRejectionCheckDocument`/`removeRejectionCheckDocument` 的取消列表，约 1617-1639 行）

**Interfaces:**
- Consumes: Task 4 `extractDocxVisualAssets`/`isDocxFilePath`、Task 5 `getSubjectMediaDir`/`writeVisualMediaFiles`/`removeVisualMediaDir`、Task 3 `saveVisualAssets`/`clearVisualAssets`。
- Produces:
  - `fileService.importRejectionCheckDocument` 返回的 `documents[]` 每项增加 `source_path: string`
  - 导入投标 DOCX 后 `rejection_check_visual_assets` 有对应行、`subject-media/<docId>/` 有图片文件；提交失败时媒体目录被回收
  - 删除投标文件时该文档的资产行与媒体目录同步清理

- [x] **Step 1: fileService 返回 source_path**

`importRejectionCheckDocument` 中 `parsedDocuments.push({...})`（约 727 行）增加一个字段：

```js
        parsedDocuments.push({
          file_content: fileContent,
          file_name: path.basename(filePath),
          source_path: filePath,
          parser_provider: parser.provider,
          parser_label: parserLabels[parser.provider] || '本地解析',
          fallback_to_local: Boolean(parser.fallbackToLocal),
        });
```

- [x] **Step 2: Store bid 分支提取媒体并入库**

`rejectionCheckStore.cjs` 顶部 require 增加：

```js
const { extractDocxVisualAssets, isDocxFilePath } = require('./docxVisualAssetExtractor.cjs');
const { getSubjectMediaDir, writeVisualMediaFiles, removeVisualMediaDir } = require('./subjectMediaFiles.cjs');
```

`importDocument` 的 bid 分支（`} else {` 之后，从 `const existingRows = ...` 到 `addedCount = preparedDocuments.length;`）替换为：

```js
    } else {
      const existingRows = db.prepare("SELECT document_id, file_name, content_hash FROM rejection_check_documents WHERE role = 'bid'").all();
      const existingKeys = new Set(existingRows.map((row) => `${row.file_name}\u0000${row.content_hash}`));
      const preparedDocuments = [];
      let sortOrder = existingRows.length;
      for (const item of importedDocuments) {
        const markdown = String(item.file_content || '').trim();
        if (!markdown) continue;
        const fileName = item.file_name || '投标文件';
        const contentHash = stableHash(markdown);
        const key = `${fileName}\u0000${contentHash}`;
        if (existingKeys.has(key)) {
          skippedCount += 1;
          continue;
        }
        const prepared = createPreparedDocument({
          id: createBidDocumentId(fileName, markdown),
          role: 'bid',
          fileName,
          content: markdown,
          source: 'upload',
          parserLabel: item.parser_label || undefined,
          importedAt: now(),
        }, sortOrder);
        preparedDocuments.push(prepared);
        existingKeys.add(key);
        if (!firstAddedBidDocumentId) firstAddedBidDocumentId = prepared.values.document_id;
        sortOrder += 1;
      }
      if (preparedDocuments.length) {
        await runBeforeCommit(options.beforeCommit);
      }
      await writePreparedDocuments(preparedDocuments);

      // 主体检查视觉材料：DOCX 在 docId 确定后直接写最终目录，事务失败回收（与 Markdown 先写后回收同一模式）。
      const importedMediaDirs = [];
      const assetRowsByDocument = new Map();
      const assetErrors = [];
      for (const prepared of preparedDocuments) {
        const documentId = prepared.values.document_id;
        const sourcePath = preparedDocuments.length
          ? String(importedDocuments.find((item) => item.file_name === prepared.values.file_name)?.source_path || '')
          : '';
        if (!isDocxFilePath(sourcePath)) continue;
        try {
          const { occurrences, mediaFiles } = await extractDocxVisualAssets(sourcePath);
          const mediaDir = getSubjectMediaDir(rejectionCheckDir, documentId);
          const relativePathByName = writeVisualMediaFiles({ mediaDir, mediaFiles });
          importedMediaDirs.push(mediaDir);
          const rows = occurrences.map((occurrence, index) => ({
            id: `${documentId}-asset-${index + 1}`,
            mediaName: occurrence.mediaName,
            contentHash: mediaFiles.find((media) => media.mediaName === occurrence.mediaName)?.contentHash || '',
            relativePath: relativePathByName.get(occurrence.mediaName) || '',
            mimeType: mediaFiles.find((media) => media.mediaName === occurrence.mediaName)?.mimeType || 'application/octet-stream',
            width: occurrence.width,
            height: occurrence.height,
            placement: occurrence.placement,
            assetKind: occurrence.assetKind,
            locationHint: occurrence.locationHint,
            nearbyText: occurrence.nearbyText,
            sectionTitle: occurrence.sectionTitle,
          })).filter((row) => row.relativePath && row.contentHash);
          assetRowsByDocument.set(documentId, rows);
        } catch (error) {
          // 单份 DOCX 媒体损坏不阻塞导入（spec §13），记录后继续其他资产和正文。
          assetErrors.push(`${prepared.values.file_name}：图片提取失败（${error.message}）`);
        }
      }

      const transaction = db.transaction(() => {
        preparedDocuments.forEach(savePreparedDocument);
        for (const [documentId, rows] of assetRowsByDocument.entries()) {
          saveVisualAssets(documentId, rows);
        }
        if (preparedDocuments.length) {
          clearCheckResults();
          resetSubjectProfile();
          updateMeta({ active_document_tab: firstAddedBidDocumentId || 'tender' });
        }
      });
      try {
        transaction();
      } catch (error) {
        await Promise.all([
          ...preparedDocuments.map((prepared) => runCleanup(
            `回收未提交 Markdown 失败：${prepared.absolutePath}`,
            () => fs.promises.rm(prepared.absolutePath, { force: true }),
          )),
          ...importedMediaDirs.map((mediaDir) => runCleanup(
            `回收未提交图片失败：${mediaDir}`,
            () => removeVisualMediaDir(mediaDir),
          )),
        ]);
        throw error;
      }
      addedCount = preparedDocuments.length;
      if (assetErrors.length) {
        importWarnings.push(...assetErrors);
      }
    }
```

同时在 `importDocument` 开头（`let firstAddedBidDocumentId = '';` 之后）增加 `const importWarnings = [];`，并在最终返回消息组装处（第 986-989 行 `messageParts` 段）加入：

```js
    if (importWarnings.length) messageParts.push(importWarnings.join('；'));
```

（`clearCheckResults()` 已含 subject 结果清理；`resetSubjectProfile()` 使主体确认随投标文件变化失效，见 spec §11.4 第一行。）

- [x] **Step 3: removeDocument / clearRejectionCheck 清理媒体**

`removeDocument` 的 bid 清理段（约 1092-1096 行 `await runCleanup('清理投标文件图片失败', ...)` 附近）增加：

```js
    } else if (documentId) {
      await runCleanup('清理投标文件图片失败', () => deleteImportedImageBatchesAsync(app, `rejection-check-bid-${documentId}`));
      // 主体检查视觉材料随文档删除（spec §6.3）。
      clearVisualAssets(documentId);
      await runCleanup('清理投标文件视觉材料失败', () => removeVisualMediaDir(getSubjectMediaDir(rejectionCheckDir, documentId)));
      resetSubjectProfile('投标文件已更新，请重新确认投标主体');
      if (documentId === 'bid-1') {
        await runCleanup('清理旧投标文件图片失败', () => deleteImportedImageBatchesForExactScopeAsync(app, 'rejection-check-bid'));
      }
    } else {
      await runCleanup('清理投标文件图片失败', () => deleteImportedImageBatchesAsync(app, 'rejection-check-bid'));
      clearVisualAssets();
      await runCleanup('清理投标文件视觉材料失败', () => removeVisualMediaDir(path.join(rejectionCheckDir, 'subject-media')));
      resetSubjectProfile('投标文件已更新，请重新确认投标主体');
    }
```

（整段替换现有 bid 分支的三个 runCleanup 段；`clearRejectionCheck` 在 Task 3 已覆盖——它递归删除整个 `rejectionCheckDir` 并清空三张新表。）

- [x] **Step 4: taskService 取消列表扩展**

`taskService.cjs` 的 `importRejectionCheckDocument`（约 1617 行）和 `removeRejectionCheckDocument`（约 1631 行）中，bid 角色的取消列表由 `['rejection-check-run']` 改为：

```js
          ['rejection-check-run', 'bid-subject-identification'],
```

（两处；`bid-subject-identification` 的 taskDefinition 在 Task 11 注册，本任务只改列表字符串，此时即使类型尚未注册 `cancelRejectionCheckTasks` 也只是匹配不到、无副作用。）

- [x] **Step 5: 验证**

Run: `cd client && node --check electron/services/fileService.cjs && node --check electron/services/rejectionCheckStore.cjs && node --check electron/services/taskService.cjs`
Expected: 无输出

Run: `cd client && node --test electron/services/docxVisualAssetExtractor.test.cjs electron/services/subjectMediaFiles.test.cjs`
Expected: PASS（回归）

Run: `cd client && npm run smoke:electron-native`
Expected: 退出码 0

- [x] **Step 6: 提交**

```bash
git add client/electron/services/fileService.cjs client/electron/services/rejectionCheckStore.cjs client/electron/services/taskService.cjs
git commit -m "feat: 投标 DOCX 导入时提取视觉材料并同步主体失效"
```

---

### Task 7: 在线 VLM Provider `subjectVisionProvider.cjs`

**Files:**
- Create: `client/electron/services/subjectVisionProvider.cjs`
- Test: `client/electron/services/subjectVisionProvider.test.cjs`

**Interfaces:**
- Consumes: `aiService.collectJsonResponse(request)`（支持 `messages[].content` 数组与 `local_image` 部件）、`aiService.getConfig()`。测试用 fake aiService。
- Produces（Task 10 依赖）:
  - `createOnlineVlmProvider({ aiService })` 返回 `{ getCapabilities(), inspect(request) }`
  - `SubjectVisionRequest = { protocolVersion: 1, officialName: string, aliases: string[], assets: Array<{ assetId, bidDocumentId, absolutePath, contentHash, assetKind, mimeType, sectionTitle?, nearbyText }> }`
  - `inspect(request)` → `Promise<{ providerStatus: 'success'|'unavailable'|'error', message?, assets: Array<{ assetId, status: 'inspected'|'skipped'|'failed', skipReason?, hasSeal, sealText?, detectedSubject?, imageType?, pageRole?, sealRole?, nameConfidence?, roleConfidence?, reason? }> }>`
  - `providerStatus === 'unavailable'` 表示未开启多模态（不发起任何图片请求）；`'error'` 表示全部资产失败。部分失败时 `providerStatus` 仍为 `'success'`，但资产级 `status: 'failed'` 会保留——视觉整体状态（partial）由 Task 10 编排层根据 `stats.failedAssets` 计算。

- [x] **Step 1: 写失败测试**

创建 `client/electron/services/subjectVisionProvider.test.cjs`：

```js
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
      // 第一阶段：内容识别（含图片请求，同一 contentHash 只上传一次）。
      { images: [{ hasSeal: true, sealText: '南屿云图数据服务有限责任公司', imageType: 'document' }] },
      // 第二阶段：角色判断（纯文本请求，两个位置在一次请求中逐位置返回角色）。
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
  // 1 次图片请求 + 1 次角色判断（两个位置一次请求逐位置判定）= 2 次调用。
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
      { images: [{ hasSeal: false, imageType: 'document' }] }, // page-scan 单独一批
      { images: [{ hasSeal: false, imageType: 'document' }, { hasSeal: false, imageType: 'document' }] }, // 两张小图一批
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
      // a1 为整页扫描图（单独成批，第一批失败）；a2 为独立小图（第二批成功）。
      makeAsset({ assetId: 'a1', contentHash: 'hash-1', assetKind: 'page-scan' }),
      makeAsset({ assetId: 'a2', contentHash: 'hash-2' }),
    ],
  });
  assert.strictEqual(result.assets.find((item) => item.assetId === 'a1').status, 'failed');
  assert.strictEqual(result.assets.find((item) => item.assetId === 'a2').status, 'inspected');
  // 部分失败时 providerStatus 仍为 success（资产级状态保留 failed）；Task 10 编排层据此计算 partial。
  assert.strictEqual(result.providerStatus, 'success');
});
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd client && node --test electron/services/subjectVisionProvider.test.cjs`
Expected: FAIL，`Cannot find module './subjectVisionProvider.cjs'`

- [x] **Step 3: 写实现**

创建 `client/electron/services/subjectVisionProvider.cjs`：

```js
'use strict';

const crypto = require('node:crypto');

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

  async function inspect(request) {
    const config = typeof aiService.getConfig === 'function' ? aiService.getConfig() : {};
    if (!config?.multimodal_enabled) {
      return {
        providerStatus: 'unavailable',
        message: '当前文本模型未开启多模态支持，请到设置页的文本模型中开启',
        assets: [],
      };
    }

    const assets = Array.isArray(request?.assets) ? request.assets : [];
    const byAssetId = new Map(assets.map((asset) => [asset.assetId, { asset, content: null, error: undefined }]));

    // 阶段一：内容识别，按 contentHash 缓存（spec §9.3：同一图片内容只上传识别一次）。
    const contentCache = new Map();
    // 同一 contentHash 只保留一个代表条目进入上传（spec §9.3：同一内容只上传识别一次）。
    const uncached = [];
    const seenHashes = new Set();
    for (const entry of byAssetId.values()) {
      if (entry.error || contentCache.has(entry.asset.contentHash) || seenHashes.has(entry.asset.contentHash)) continue;
      seenHashes.add(entry.asset.contentHash);
      uncached.push(entry);
    }
    // 分批策略（spec §9.3）：整页扫描图每批 1 张；独立小图每批最多 4 张。两类不可混批。
    const contentBatches = [
      ...uncached.filter((entry) => entry.asset.assetKind === 'page-scan').map((entry) => [entry]),
      ...chunkItems(uncached.filter((entry) => entry.asset.assetKind !== 'page-scan'), STANDALONE_BATCH_SIZE),
    ];
    for (const batch of contentBatches) {
      try {
        const payload = await runJson({
          messages: [{
            role: 'user',
            content: [
              { type: 'text', text: buildContentPrompt(batch) },
              ...batch.map((entry) => ({ type: 'local_image', path: entry.asset.absolutePath })),
            ],
          }],
        }, '主体印章图片识别', '印章图片识别结果格式无效');
        const images = Array.isArray(payload?.images) ? payload.images : [];
        batch.forEach((entry, index) => {
          const image = images[index] || {};
          entry.content = {
            hasSeal: Boolean(image.has_seal),
            sealText: String(image.seal_text || ''),
            imageType: String(image.image_type || 'unknown'),
          };
          contentCache.set(entry.asset.contentHash, entry.content);
        });
      } catch (error) {
        // 批次失败时，同 contentHash 的所有出现位置一并标记失败（它们不会再有内容识别结果）。
        const failedHashes = new Set(batch.map((entry) => entry.asset.contentHash));
        for (const entry of byAssetId.values()) {
          if (failedHashes.has(entry.asset.contentHash)) entry.error = error.message || '图片识别失败';
        }
      }
    }

    // 把缓存中的内容识别结果回填到被去重跳过的同 hash 出现位置，保证每个出现位置都参与角色判断。
    for (const entry of byAssetId.values()) {
      if (!entry.content && !entry.error && contentCache.has(entry.asset.contentHash)) {
        entry.content = contentCache.get(entry.asset.contentHash);
      }
    }

    // 阶段二：角色判断（纯文本请求，按 contentHash + nearbyContextHash 缓存）。
    const roleCache = new Map();
    const pendingRoles = [...byAssetId.values()].filter((entry) => entry.content && !entry.error);
    for (const batch of chunkItems(pendingRoles, ROLE_BATCH_SIZE)) {
      const positions = batch.map((entry) => ({ asset: entry.asset, content: entry.content }));
      const cacheKey = positions.map((entry) => `${entry.asset.contentHash}:${hashText(entry.asset.nearbyText)}`).join('|');
      if (roleCache.has(cacheKey)) {
        const cached = roleCache.get(cacheKey);
        batch.forEach((entry, index) => {
          entry.role = cached[index];
        });
        continue;
      }
      try {
        const payload = await runJson({
          messages: [{ role: 'user', content: buildRolePrompt({ officialName: request.officialName, aliases: request.aliases || [], positions }) }],
        }, '主体盖章角色判断', '盖章角色判断结果格式无效');
        const results = Array.isArray(payload?.positions) ? payload.positions : [];
        const normalized = batch.map((_entry, index) => {
          const item = results[index] || {};
          return {
            pageRole: ['bidder', 'third-party', 'unclear'].includes(item.page_role) ? item.page_role : 'unclear',
            sealRole: ['bidder', 'third-party', 'unclear'].includes(item.seal_role) ? item.seal_role : 'unclear',
            detectedSubject: String(item.detected_subject || ''),
            nameConfidence: Math.max(0, Math.min(1, Number(item.name_confidence || 0))),
            roleConfidence: Math.max(0, Math.min(1, Number(item.role_confidence || 0))),
            reason: String(item.reason || ''),
          };
        });
        roleCache.set(cacheKey, normalized);
        batch.forEach((entry, index) => {
          entry.role = normalized[index];
        });
      } catch (error) {
        batch.forEach((entry) => {
          entry.error = error.message || '角色判断失败';
        });
      }
    }

    // 组装结果：明确普通产品截图/拓扑图/照片跳过并记录原因（spec §9.3）。
    const resultAssets = [...byAssetId.values()].map((entry) => {
      if (entry.error) {
        return { assetId: entry.asset.assetId, status: 'failed', skipReason: entry.error, hasSeal: false };
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
```

- [x] **Step 4: 运行测试确认通过**

Run: `cd client && node --test electron/services/subjectVisionProvider.test.cjs`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add client/electron/services/subjectVisionProvider.cjs client/electron/services/subjectVisionProvider.test.cjs
git commit -m "feat: 新增主体印章在线 VLM Provider（两阶段+缓存+批量控制）"
```

---

### Task 8: 主体候选识别任务 `subjectCheckTask.cjs`（识别部分）

**Files:**
- Create: `client/electron/services/subjectCheckTask.cjs`
- Test: `client/electron/services/subjectCheckTask.test.cjs`

**Interfaces:**
- Consumes: `subjectNameUtils.cjs`（`applySubjectAutoSelectRules`、`SEMANTIC_PRONOUNS`）、`aiService.collectJsonResponse`。
- Produces:
  - `runBidSubjectIdentificationTask({ aiService, workspaceStore, checkpointTask, payload, previousState })`
  - `buildSubjectIdentificationMessages({ bidDocuments, semanticPronouns })` — 供测试断言提示词内容
  - `normalizeSubjectCandidates(payload, bidDocuments)` — 防幻觉：丢弃 `bidDocumentId` 不属于输入的候选
  - `parseAliasesFromCandidates(candidates)` — 汇总去重允许简称
  - 检查点写入：`subjectProfile`（status/candidates/preselectIndex）、`subjectIdentificationTask`、`checkTask` 不涉及

- [x] **Step 1: 写失败测试**

创建 `client/electron/services/subjectCheckTask.test.cjs`：

```js
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
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd client && node --test electron/services/subjectCheckTask.test.cjs`
Expected: FAIL，`Cannot find module './subjectCheckTask.cjs'`

- [x] **Step 3: 写实现（识别部分）**

创建 `client/electron/services/subjectCheckTask.cjs`：

```js
'use strict';

const {
  SEMANTIC_PRONOUNS,
  normalizeSubjectName,
  applySubjectAutoSelectRules,
} = require('./subjectNameUtils.cjs');

const IDENTIFICATION_SEGMENT_CHARS = 12000;
const BID_DOCUMENT_MARKER = '\n---yibiao-subject-bid-document---\n';

function now() {
  return new Date().toISOString();
}

function formatBidDocumentIdList(bidDocuments) {
  return (Array.isArray(bidDocuments) ? bidDocuments : [])
    .map((document) => `- ${document.id}（${document.fileName}）`)
    .join('\n');
}

function chunkContent(content, limit) {
  const text = String(content || '');
  const segments = [];
  for (let index = 0; index < text.length; index += limit) {
    segments.push({ start: index, text: text.slice(index, index + limit) });
  }
  return segments.length ? segments : [{ start: 0, text: '' }];
}

function buildSubjectIdentificationMessages({ bidDocuments }) {
  const documents = Array.isArray(bidDocuments) ? bidDocuments : [];
  const idList = formatBidDocumentIdList(documents);
  const documentBlocks = documents.map((document) => {
    const segments = chunkContent(document.content, IDENTIFICATION_SEGMENT_CHARS);
    const segmentText = segments.map((segment, index) => `【${document.fileName} 第 ${index + 1}/${segments.length} 段】\n${segment.text}`).join('\n');
    return `【投标文件】bidDocumentId：${document.id}\n文件名：${document.fileName}\n${segmentText}`;
  }).join(BID_DOCUMENT_MARKER);

  return [
    {
      role: 'user',
      content: `【投标主体候选识别任务】
本投标包有效 bidDocumentId：
${idList}

语义代称说明：文档中出现的“${SEMANTIC_PRONOUNS.join('、')}”等代称，语义上代表投标方发言、承诺、声明、签署或盖章。请据此定位真正的投标主体，不要把这些代称本身当作公司名称。

请扫描全部投标文件，重点但不仅限于以下区域：封面与文档标题、投标函/响应函、投标人/应答人/供应商字段、报价表、授权委托书、法定代表人声明、投标方承诺函、签字盖章附近文字，以及高频“我公司/本公司”对应的具名主体。

输出要求：
1. 每个候选必须给出 bidDocumentId（必须来自上方有效列表）、候选公司全称、confidence（0~1）、location_hint（位置线索）、original_text（可定位的原文摘录，必须逐字来自文件）、role_evidence（为什么判断它是投标方）。
2. aliases 只填写文中确实出现、可安全视为该主体简称的短名称，不要臆造。
3. 不得把招标人、代理机构、客户、制造商、认证机构、案例单位列为候选。

JSON 格式：{"candidates":[{"bid_document_id":"有效 bidDocumentId","name":"候选公司全称","confidence":0.93,"location_hint":"投标函落款","original_text":"原文摘录","role_evidence":"判断依据","aliases":["简称"]}]}

待分析投标文件：
${documentBlocks}`,
    },
  ];
}

function locateOriginalText(content, excerpt) {
  const source = String(content || '');
  const target = String(excerpt || '');
  if (!source || !target) return false;
  if (source.includes(target)) return true;
  // 忽略空白差异的定位（spec §8.4）。
  const compactSource = source.replace(/\s+/g, '');
  const compactTarget = target.replace(/\s+/g, '');
  return Boolean(compactTarget) && compactSource.includes(compactTarget);
}

function normalizeSubjectCandidates(payload, bidDocuments) {
  const documents = Array.isArray(bidDocuments) ? bidDocuments : [];
  const byId = new Map(documents.map((document) => [document.id, document]));
  const rawCandidates = Array.isArray(payload?.candidates) ? payload.candidates : [];
  const merged = new Map();
  for (const item of rawCandidates) {
    const bidDocumentId = String(item?.bid_document_id || '');
    const document = byId.get(bidDocumentId);
    if (!document) continue;
    const name = String(item?.name || '').trim();
    if (!name) continue;
    const originalText = String(item?.original_text || '').trim();
    if (!originalText || !locateOriginalText(document.content, originalText)) continue;
    const key = `${bidDocumentId}\u0000${normalizeSubjectName(name)}`;
    const candidate = {
      bidDocumentId,
      name,
      confidence: Math.max(0, Math.min(1, Number(item?.confidence || 0))),
      locationHint: String(item?.location_hint || ''),
      originalText,
      roleEvidence: String(item?.role_evidence || ''),
      aliases: (Array.isArray(item?.aliases) ? item.aliases : [])
        .map((alias) => String(alias || '').trim())
        .filter((alias) => alias && normalizeSubjectName(alias) !== normalizeSubjectName(name)),
    };
    const existing = merged.get(key);
    if (!existing || candidate.confidence > existing.confidence) merged.set(key, candidate);
  }
  return [...merged.values()].sort((a, b) => b.confidence - a.confidence);
}

function parseAliasesFromCandidates(candidates = []) {
  const seen = new Set();
  const result = [];
  for (const candidate of Array.isArray(candidates) ? candidates : []) {
    const nameKey = normalizeSubjectName(candidate?.name);
    for (const alias of Array.isArray(candidate?.aliases) ? candidate.aliases : []) {
      const trimmed = String(alias || '').trim();
      const key = normalizeSubjectName(trimmed);
      if (!key || key === nameKey || seen.has(key)) continue;
      seen.add(key);
      result.push(trimmed);
    }
  }
  return result;
}

// 合并同名候选的简称（同一主体可能在多份文件里各给了一次简称）。
function mergeCandidatesByName(candidates = []) {
  const byName = new Map();
  for (const candidate of candidates) {
    const key = normalizeSubjectName(candidate.name);
    const existing = byName.get(key);
    if (!existing) {
      byName.set(key, { ...candidate, aliases: [...(candidate.aliases || [])] });
      continue;
    }
    existing.aliases = [...new Set([...existing.aliases, ...(candidate.aliases || [])])];
    if (candidate.confidence > existing.confidence) {
      Object.assign(existing, { ...candidate, aliases: existing.aliases });
    }
  }
  return [...byName.values()].sort((a, b) => b.confidence - a.confidence);
}

async function runBidSubjectIdentificationTask({ aiService, checkpointTask, payload, previousState }) {
  const state = { ...(previousState || {}), ...(payload?.workspaceState || {}) };
  const bidDocuments = (Array.isArray(state.bidDocuments) ? state.bidDocuments : [])
    .map((document) => ({ ...document, content: String(document.content || '') }))
    .filter((document) => document.id && document.content.trim());
  if (!bidDocuments.length) throw new Error('缺少投标文件内容，无法识别投标主体');

  const bidSignature = String(payload?.workspaceState?.bidSignature || '');

  checkpointTask({ status: 'running', progress: 10, logs: ['正在识别投标主体候选。'] }, {
    subjectProfile: {
      status: 'identifying',
      officialName: '',
      aliases: [],
      candidates: [],
      bidSignature,
      updatedAt: now(),
    },
  });

  const response = await aiService.collectJsonResponse({
    messages: buildSubjectIdentificationMessages({ bidDocuments }),
    progressLabel: '投标主体候选识别',
    failureMessage: '投标主体候选识别结果格式无效，请重新识别',
    progressCallback: (message) => {
      checkpointTask({ status: 'running', progress: 45, logs: [`投标主体候选识别：${message}`] }, {});
    },
  });

  const candidates = mergeCandidatesByName(normalizeSubjectCandidates(response, bidDocuments));
  if (!candidates.length) throw new Error('未能从投标文件中识别出投标主体，请手工填写');

  const preselectIndex = applySubjectAutoSelectRules(candidates);
  const aliases = parseAliasesFromCandidates(candidates);

  checkpointTask({ status: 'success', progress: 100, logs: [`投标主体候选识别完成，共 ${candidates.length} 个候选。`] }, {
    subjectProfile: {
      status: 'pending-confirmation',
      officialName: preselectIndex >= 0 ? candidates[preselectIndex].name : '',
      aliases: preselectIndex >= 0 ? aliases : [],
      candidates,
      preselectIndex,
      bidSignature,
      updatedAt: now(),
    },
  });

  return { candidates, preselectIndex, aliases };
}

module.exports = {
  runBidSubjectIdentificationTask,
  buildSubjectIdentificationMessages,
  normalizeSubjectCandidates,
  parseAliasesFromCandidates,
  mergeCandidatesByName,
};
```

- [x] **Step 4: 运行测试确认通过**

Run: `cd client && node --test electron/services/subjectCheckTask.test.cjs`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add client/electron/services/subjectCheckTask.cjs client/electron/services/subjectCheckTask.test.cjs
git commit -m "feat: 新增投标主体候选识别任务与防幻觉候选校验"
```

---

### Task 9: 正文主体检查（滚动 + 定稿 + 防幻觉）

**Files:**
- Modify: `client/electron/services/subjectCheckTask.cjs`（追加函数与导出）
- Modify: `client/electron/services/subjectCheckTask.test.cjs`（追加用例）

**Interfaces:**
- Consumes: Task 1 `matchesAllowedSubject`、`looksLikeCompanyName`、`SEMANTIC_PRONOUNS`；Task 3 无直接依赖。
- Produces:
  - `runSubjectBodyCheck(aiService, { bidDocuments, officialName, aliases, semanticPronouns }, onProgress): Promise<SubjectCheckFinding[]>`
  - `normalizeSubjectBodyFindings(payload, { bidDocuments, officialName, aliases }): SubjectCheckFinding[]`
  - `createEmptySubjectBodyState()` / `applySubjectBodyPatch(state, patch)` / `createSubjectBodyFinalCandidates(state)` — 滚动状态机
  - `SubjectCheckFinding` 字段与 spec §10 一致（`level`、`source`、`expectedSubject`、`detectedSubject`、`locationKind`、`title`、`summary`、`locationHint`、`originalText`、`roleReason`、`mismatchReason`、`suggestion`、`confidence`）

- [x] **Step 1: 追加失败测试**

在 `client/electron/services/subjectCheckTask.test.cjs` 末尾追加：

```js
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
  const payload = {
    findings: [
      {
        bid_document_id: 'bid-1',
        role: 'bidder',
        detected_subject: SUBJECT_B,
        location_kind: 'signature',
        title: '落款主体错误',
        summary: '投标函落款为其他公司',
        location_hint: '投标函落款',
        original_text: `投标人：${SUBJECT_B}（盖章）`,
        role_reason: '投标函落款处应由投标方签署',
        mismatch_reason: '落款主体与正式投标主体不一致',
        suggestion: '改为投标方名称并加盖投标方公章',
        name_confidence: 0.93,
        role_confidence: 0.92,
      },
      {
        bid_document_id: 'bid-1',
        role: 'third-party',
        detected_subject: SUBJECT_B,
        location_kind: 'body',
        title: '第三方出现',
        summary: '第三方',
        location_hint: '正文',
        original_text: '致：某某招标代理公司',
        role_reason: '招标人',
        mismatch_reason: '不适用',
        suggestion: '无需修改',
        name_confidence: 0.95,
        role_confidence: 0.95,
      },
    ],
  };
  const findings = normalizeSubjectBodyFindings(payload, BODY_CONTEXT);
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
        title: 'z', summary: 'z', location_hint: 'z', original_text: '我公司郑重承诺遵守招标文件全部条款。',
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
      title: 't', summary: 's', location_hint: 'l', original_text: `投标人名称：${SUBJECT_A}`.replace(SUBJECT_A, SUBJECT_B),
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
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd client && node --test electron/services/subjectCheckTask.test.cjs`
Expected: FAIL，`normalizeSubjectBodyFindings is not a function`

- [x] **Step 3: 写实现（正文检查部分）**

在 `subjectCheckTask.cjs` 中，`runBidSubjectIdentificationTask` 之前插入以下代码，并更新 `module.exports`：

```js
const ROLLING_SEGMENT_CHARS = 12000;
const FINAL_CANDIDATE_BATCH_SIZE = 12;

function createEmptySubjectBodyState() {
  return {
    subjectSummary: [],
    roleSummary: [],
    candidates: [],
    candidateKeys: new Set(),
  };
}

function createSubjectCandidateKey(candidate) {
  return [
    String(candidate.bidDocumentId || ''),
    String(candidate.locationKind || ''),
    normalizeSubjectName(candidate.detectedSubject),
    String(candidate.originalText || '').replace(/\s+/g, ''),
  ].join('\u0000');
}

function applySubjectBodyPatch(state, patch) {
  const next = {
    subjectSummary: [...state.subjectSummary],
    roleSummary: [...state.roleSummary],
    candidates: [...state.candidates],
    candidateKeys: new Set(state.candidateKeys),
  };
  for (const item of Array.isArray(patch?.subject_summary) ? patch.subject_summary : []) {
    const text = String(item || '').trim();
    if (text && !next.subjectSummary.includes(text)) next.subjectSummary.push(text);
  }
  for (const item of Array.isArray(patch?.role_summary) ? patch.role_summary : []) {
    const text = String(item || '').trim();
    if (text && !next.roleSummary.includes(text)) next.roleSummary.push(text);
  }
  for (const raw of Array.isArray(patch?.candidates) ? patch.candidates : []) {
    const candidate = {
      bidDocumentId: String(raw?.bid_document_id || ''),
      role: ['bidder', 'third-party', 'unclear'].includes(raw?.role) ? raw.role : 'unclear',
      detectedSubject: String(raw?.detected_subject || ''),
      locationKind: ['body', 'table', 'signature'].includes(raw?.location_kind) ? raw.location_kind : 'body',
      title: String(raw?.title || ''),
      summary: String(raw?.summary || ''),
      locationHint: String(raw?.location_hint || ''),
      originalText: String(raw?.original_text || ''),
      roleReason: String(raw?.role_reason || ''),
      mismatchReason: String(raw?.mismatch_reason || ''),
      suggestion: String(raw?.suggestion || ''),
      nameConfidence: Math.max(0, Math.min(1, Number(raw?.name_confidence || 0))),
      roleConfidence: Math.max(0, Math.min(1, Number(raw?.role_confidence || 0))),
    };
    const key = createSubjectCandidateKey(candidate);
    if (next.candidateKeys.has(key)) continue;
    next.candidateKeys.add(key);
    next.candidates.push(candidate);
  }
  return next;
}

function createSubjectBodyFinalCandidates(state) {
  const seen = new Set();
  const result = [];
  for (const candidate of state.candidates) {
    const key = createSubjectCandidateKey(candidate);
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(candidate);
  }
  return result;
}

function createSubjectBodyStateSummary(state) {
  return {
    subject_summary: state.subjectSummary.slice(-40),
    role_summary: state.roleSummary.slice(-40),
    candidate_count: state.candidates.length,
  };
}

function buildSubjectBodySegmentMessages({ bidDocuments, segment, stateSummary, officialName, aliases }) {
  const document = bidDocuments.find((item) => item.id === segment.documentId);
  return [
    {
      role: 'user',
      content: `【投标主体一致性滚动审阅｜已发现状态】
正式投标主体：${officialName}
允许简称：${aliases.join('、') || '（无）'}
语义代称：${SEMANTIC_PRONOUNS.join('、')}

以下是 Main 侧维护的已发现主体与角色摘要，用于跨片段去重和避免早期信息丢失：
${JSON.stringify(stateSummary, null, 2)}`,
    },
    {
      role: 'user',
      content: `【投标主体一致性滚动审阅任务】
当前文件：${document?.fileName || segment.documentId}（bidDocumentId：${segment.documentId}）
当前片段：第 ${segment.segmentIndex}/${segment.totalSegments} 段

请通读本片段，找出所有可能出现的主体名称，并判断其在当前上下文中的角色：
- bidder：当前上下文代表投标方发言、承诺、声明、签署、落款或承担责任；
- third-party：招标人、代理机构、客户、制造商、认证机构、案例单位等第三方；
- unclear：无法确定。

规则：
1. 只有“我公司/本公司”等代称且没有出现其他具名公司时，不要生成候选。
2. 第三方角色不要生成候选，只在 role_summary 里记录，避免后续重复分析。
3. 不得因为片段里没有解释，就把第三方主体直接定为投标主体错误。
4. original_text 必须是本片段中逐字出现的原文摘录。

JSON 格式：{"subject_summary":["已确认的主体与证据"],"role_summary":["角色结论"],"candidates":[{"bid_document_id":"${segment.documentId}","role":"bidder","detected_subject":"检测到的公司名称","location_kind":"body","title":"不超过 24 字的标题","summary":"简要说明","location_hint":"位置线索","original_text":"原文摘录","role_reason":"角色判断依据","mismatch_reason":"不一致原因","suggestion":"修改建议","name_confidence":0.9,"role_confidence":0.9}]}

本片段正文：
${segment.text}`,
    },
  ];
}

function buildSubjectBodyFinalMessages({ officialName, aliases, candidates, stateSummary }) {
  return [
    {
      role: 'user',
      content: `【投标主体一致性定稿｜事实索引】
正式投标主体：${officialName}
允许简称：${aliases.join('、') || '（无）'}

Main 侧全量状态索引：
${JSON.stringify(stateSummary, null, 2)}`,
    },
    {
      role: 'user',
      content: `【投标主体一致性定稿任务】
以下 candidates 来自滚动审阅的初步结果。请只保留确实代表投标方身份、且与正式主体和允许简称均不匹配的问题，输出最终 JSON。

定稿规则：
1. 正式投标主体名称和允许简称出现在原文中属于正常，必须删除。
2. 第三方主体（招标人、代理机构、客户、制造商、认证机构、案例单位）不得输出。
3. 只有代称而没有错误具名主体的位置不得输出。
4. 相似但未经用户确认的公司名至少保留为需人工复核，不得直接丢弃。
5. bidDocumentId 必须来自上方有效列表，original_text 必须逐字来自对应原文。

待定稿 candidates：
${JSON.stringify(candidates, null, 2)}

JSON 格式：{"findings":[{"bid_document_id":"有效 bidDocumentId","role":"bidder","detected_subject":"检测到的公司名称","location_kind":"body","title":"不超过 24 字的标题","summary":"简要说明","location_hint":"位置线索","original_text":"原文摘录","role_reason":"角色判断依据","mismatch_reason":"不一致原因","suggestion":"修改建议","name_confidence":0.9,"role_confidence":0.9}]}`,
    },
  ];
}

function normalizeSubjectBodyFindings(payload, { bidDocuments, officialName, aliases }) {
  const documents = Array.isArray(bidDocuments) ? bidDocuments : [];
  const byId = new Map(documents.map((document) => [document.id, document]));
  const rawFindings = Array.isArray(payload?.findings) ? payload.findings : [];
  const seen = new Set();
  const findings = [];
  for (const raw of rawFindings) {
    const bidDocumentId = String(raw?.bid_document_id || '');
    const document = byId.get(bidDocumentId);
    if (!document) continue; // spec §8.4-1
    const originalText = String(raw?.original_text || '').trim();
    if (!originalText || !locateOriginalText(document.content, originalText)) continue; // spec §8.4-2
    const detectedSubject = String(raw?.detected_subject || '').trim();
    if (!detectedSubject || !originalText.includes(detectedSubject)) continue; // spec §8.4-3
    if (matchesAllowedSubject(detectedSubject, officialName, aliases)) continue; // spec §8.4-4
    const role = ['bidder', 'third-party', 'unclear'].includes(raw?.role) ? raw.role : 'unclear';
    if (role === 'third-party') continue; // spec §8.2
    if (role === 'unclear' && !looksLikeCompanyName(detectedSubject)) continue; // spec §8.2
    const nameConfidence = Math.max(0, Math.min(1, Number(raw?.name_confidence || 0)));
    const roleConfidence = Math.max(0, Math.min(1, Number(raw?.role_confidence || 0)));
    const locationKind = ['body', 'table', 'signature'].includes(raw?.location_kind) ? raw.location_kind : 'body';
    const key = [bidDocumentId, locationKind, normalizeSubjectName(detectedSubject), originalText.replace(/\s+/g, '')].join('\u0000');
    if (seen.has(key)) continue; // spec §8.4-5
    seen.add(key);
    // 明确不一致门槛（spec §10.1）：bidder + 双置信度 ≥0.85；否则降级为需人工复核。
    const level = role === 'bidder' && nameConfidence >= 0.85 && roleConfidence >= 0.85
      ? 'confirmed-mismatch'
      : 'needs-review';
    findings.push({
      id: `subject-text-${findings.length + 1}`,
      bidDocumentId,
      level,
      source: 'text',
      expectedSubject: officialName,
      detectedSubject,
      locationKind,
      title: String(raw?.title || '投标主体不一致'),
      summary: String(raw?.summary || ''),
      locationHint: String(raw?.location_hint || ''),
      originalText,
      roleReason: String(raw?.role_reason || ''),
      mismatchReason: String(raw?.mismatch_reason || ''),
      suggestion: String(raw?.suggestion || ''),
      confidence: Math.min(nameConfidence, roleConfidence),
    });
  }
  return findings;
}

async function runSubjectBodyCheck(aiService, { bidDocuments, officialName, aliases = [], semanticPronouns = SEMANTIC_PRONOUNS }, onProgress) {
  const documents = Array.isArray(bidDocuments) ? bidDocuments : [];
  const segments = [];
  for (const document of documents) {
    const text = String(document.content || '');
    const total = Math.max(1, Math.ceil(text.length / ROLLING_SEGMENT_CHARS));
    for (let index = 0; index < total; index += 1) {
      segments.push({
        documentId: document.id,
        documentLabel: document.fileName || document.id,
        segmentIndex: index + 1,
        totalSegments: total,
        text: text.slice(index * ROLLING_SEGMENT_CHARS, (index + 1) * ROLLING_SEGMENT_CHARS),
      });
    }
  }

  let state = createEmptySubjectBodyState();
  for (const segment of segments) {
    onProgress(`${segment.documentLabel}：正在审阅第 ${segment.segmentIndex}/${segment.totalSegments} 段。`);
    const payload = await aiService.collectJsonResponse({
      messages: buildSubjectBodySegmentMessages({
        bidDocuments: documents,
        segment,
        stateSummary: createSubjectBodyStateSummary(state),
        officialName,
        aliases,
      }),
      progressLabel: '投标主体一致性滚动审阅',
      failureMessage: '投标主体一致性审阅结果格式无效，请重新检查',
    });
    state = applySubjectBodyPatch(state, payload);
  }

  const candidates = createSubjectBodyFinalCandidates(state);
  if (!candidates.length) return [];

  onProgress('正在定稿投标主体一致性问题。');
  const findings = [];
  const batches = chunkItems(candidates, FINAL_CANDIDATE_BATCH_SIZE);
  for (const [batchIndex, batch] of batches.entries()) {
    onProgress(`正在定稿第 ${batchIndex + 1}/${batches.length} 批。`);
    const payload = await aiService.collectJsonResponse({
      messages: buildSubjectBodyFinalMessages({
        officialName,
        aliases,
        candidates: batch,
        stateSummary: createSubjectBodyStateSummary(state),
      }),
      progressLabel: '投标主体一致性定稿',
      failureMessage: '投标主体一致性定稿结果格式无效，请重新检查',
    });
    findings.push(...normalizeSubjectBodyFindings(payload, { bidDocuments: documents, officialName, aliases }));
  }

  const seen = new Set();
  return findings.filter((finding) => {
    const key = [finding.bidDocumentId, finding.locationKind, normalizeSubjectName(finding.detectedSubject), finding.originalText.replace(/\s+/g, '')].join('\u0000');
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
```

同时把 `chunkItems` 补进本文件（与 Task 7 同实现，避免跨模块依赖）：

```js
function chunkItems(items, batchSize) {
  const result = [];
  const source = Array.isArray(items) ? items : [];
  for (let index = 0; index < source.length; index += batchSize) result.push(source.slice(index, index + batchSize));
  return result;
}
```

`module.exports` 更新为：

```js
module.exports = {
  runBidSubjectIdentificationTask,
  buildSubjectIdentificationMessages,
  normalizeSubjectCandidates,
  parseAliasesFromCandidates,
  mergeCandidatesByName,
  runSubjectBodyCheck,
  normalizeSubjectBodyFindings,
  createEmptySubjectBodyState,
  applySubjectBodyPatch,
  createSubjectBodyFinalCandidates,
};
```

- [x] **Step 4: 运行测试确认通过**

Run: `cd client && node --test electron/services/subjectCheckTask.test.cjs`
Expected: PASS（含 Task 8 用例回归）

- [x] **Step 5: 提交**

```bash
git add client/electron/services/subjectCheckTask.cjs client/electron/services/subjectCheckTask.test.cjs
git commit -m "feat: 新增投标主体正文滚动检查与防幻觉定稿"
```

---

### Task 10: 印章检查编排 `runSubjectSealCheck`

**Files:**
- Modify: `client/electron/services/subjectCheckTask.cjs`（追加函数与导出）
- Modify: `client/electron/services/subjectCheckTask.test.cjs`（追加用例）

**Interfaces:**
- Consumes: Task 5 `resolveVisualAssetAbsolutePath`、`getSubjectMediaDir`；Task 7 `createOnlineVlmProvider`；Task 1 `matchesAllowedSubject`、`normalizeSubjectName`。
- Produces:
  - `runSubjectSealCheck(aiService, { bidDocuments, visualAssets, officialName, aliases, subjectSealCheck, mediaRootDir }, onProgress): Promise<{ findings, visionStatus, visionStats }>`
  - `classifySealFinding(asset, providerAsset, { officialName, aliases })` — 单资产分级（供测试直接断言）
  - `createEmptyVisionStats()` — 返回 spec §11.2 的 `SubjectVisionStats` 初值
  - `visionStatus` ∈ `disabled | success | partial | unavailable | error`（`pending`/`running` 由调用方在任务开始时写入）

- [x] **Step 1: 追加失败测试**

在 `client/electron/services/subjectCheckTask.test.cjs` 末尾追加：

```js
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
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd client && node --test electron/services/subjectCheckTask.test.cjs`
Expected: FAIL，`classifySealFinding is not a function`

- [x] **Step 3: 写实现（印章编排部分）**

在 `subjectCheckTask.cjs` 顶部 require 增加：

```js
const path = require('node:path');
const { resolveVisualAssetAbsolutePath } = require('./subjectMediaFiles.cjs');
const { createOnlineVlmProvider } = require('./subjectVisionProvider.cjs');
```

在 `runSubjectBodyCheck` 之后插入：

```js
function createEmptyVisionStats() {
  return {
    totalAssets: 0,
    candidateAssets: 0,
    inspectedAssets: 0,
    sealAssets: 0,
    skippedAssets: 0,
    reviewAssets: 0,
    failedAssets: 0,
  };
}

// 单资产分级（spec §10.2）：名称与角色置信度均 ≥0.85 才算明确印章不一致。
function classifySealFinding(asset, providerAsset, { officialName, aliases }) {
  if (!providerAsset || providerAsset.status !== 'inspected' || !providerAsset.hasSeal) return null;
  const detected = String(providerAsset.detectedSubject || providerAsset.sealText || '').trim();
  if (!detected) return null;
  if (matchesAllowedSubject(detected, officialName, aliases)) return null; // 投标方自身印章
  if (providerAsset.sealRole === 'third-party' || providerAsset.pageRole === 'third-party') return null; // 合法第三方印章
  if (providerAsset.pageRole === 'unclear' && providerAsset.sealRole === 'unclear') return null; // 角色不清且无投标方要求证据
  const nameConfidence = Number(providerAsset.nameConfidence || 0);
  const roleConfidence = Number(providerAsset.roleConfidence || 0);
  const level = nameConfidence >= 0.85 && roleConfidence >= 0.85 ? 'confirmed-mismatch' : 'needs-review';
  const isScan = asset.assetKind === 'page-scan';
  return {
    id: `subject-seal-${asset.id}`,
    bidDocumentId: asset.bidDocumentId,
    level,
    source: 'seal',
    expectedSubject: officialName,
    detectedSubject: detected,
    locationKind: isScan ? 'scanned-page' : 'standalone-image',
    title: isScan ? '整页扫描件印章主体异常' : '图片印章主体异常',
    summary: `${asset.sectionTitle || asset.locationHint || '图片位置'}处的印章识别为“${detected}”`,
    locationHint: [asset.sectionTitle, asset.locationHint].filter(Boolean).join(' / '),
    originalText: String(providerAsset.sealText || detected),
    roleReason: String(providerAsset.reason || ''),
    mismatchReason: `印章主体与正式投标主体“${officialName}”不一致`,
    suggestion: '请核对该位置是否应为投标方盖章，必要时替换为投标方公章',
    confidence: Math.min(nameConfidence, roleConfidence),
    visualAssetId: asset.id,
    assetRelativePath: asset.relativePath,
  };
}

async function runSubjectSealCheck(aiService, {
  bidDocuments = [],
  visualAssets = [],
  officialName,
  aliases = [],
  subjectSealCheck = true,
  mediaRootDir,
}, onProgress) {
  const stats = createEmptyVisionStats();
  stats.totalAssets = visualAssets.length;

  if (!subjectSealCheck) {
    return { findings: [], visionStatus: 'disabled', visionStats: stats };
  }

  const provider = createOnlineVlmProvider({ aiService });
  const config = typeof aiService.getConfig === 'function' ? aiService.getConfig() : {};
  if (!config?.multimodal_enabled) {
    onProgress('当前文本模型未开启多模态支持，已跳过印章检查。');
    return { findings: [], visionStatus: 'unavailable', visionStats: stats };
  }
  if (!visualAssets.length) {
    return { findings: [], visionStatus: 'success', visionStats: stats };
  }

  onProgress(`正在检查 ${visualAssets.length} 张投标文件图片的印章主体。`);
  const providerAssets = visualAssets.map((asset) => ({
    assetId: asset.id,
    bidDocumentId: asset.bidDocumentId,
    absolutePath: resolveVisualAssetAbsolutePath({
      mediaRootDir,
      bidDocumentId: asset.bidDocumentId,
      relativePath: asset.relativePath,
    }),
    contentHash: asset.contentHash,
    assetKind: asset.assetKind,
    mimeType: asset.mimeType,
    sectionTitle: asset.sectionTitle,
    nearbyText: asset.nearbyText,
  }));
  stats.candidateAssets = providerAssets.length;

  let response;
  try {
    response = await provider.inspect({
      protocolVersion: 1,
      officialName,
      aliases,
      assets: providerAssets,
    });
  } catch (error) {
    return { findings: [], visionStatus: 'error', visionStats: stats, error: error.message };
  }

  if (response.providerStatus === 'unavailable') {
    return { findings: [], visionStatus: 'unavailable', visionStats: stats, error: response.message };
  }

  const findings = [];
  for (const asset of visualAssets) {
    const providerAsset = response.assets.find((item) => item.assetId === asset.id);
    if (!providerAsset) {
      stats.failedAssets += 1;
      continue;
    }
    if (providerAsset.status === 'skipped') {
      stats.skippedAssets += 1;
      continue;
    }
    if (providerAsset.status === 'failed') {
      stats.failedAssets += 1;
      continue;
    }
    stats.inspectedAssets += 1;
    if (providerAsset.hasSeal) stats.sealAssets += 1;
    const finding = classifySealFinding(asset, providerAsset, { officialName, aliases });
    if (finding) {
      if (finding.level === 'needs-review') stats.reviewAssets += 1;
      findings.push(finding);
    }
  }

  // 全部资产失败（含 Provider 整体 error）时为 error，部分失败为 partial（spec §13）。
  const allFailed = stats.failedAssets > 0 && stats.inspectedAssets + stats.skippedAssets === 0;
  const visionStatus = response.providerStatus === 'error' || allFailed ? 'error' : stats.failedAssets > 0 ? 'partial' : 'success';
  return { findings, visionStatus, visionStats: stats };
}
```

`module.exports` 追加：

```js
  runSubjectSealCheck,
  classifySealFinding,
  createEmptyVisionStats,
```

- [x] **Step 4: 运行测试确认通过**

Run: `cd client && node --test electron/services/subjectCheckTask.test.cjs`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add client/electron/services/subjectCheckTask.cjs client/electron/services/subjectCheckTask.test.cjs
git commit -m "feat: 新增主体印章检查编排与结果分级"
```

---

### Task 11: 第四分支集成 + taskService 注册

**Files:**
- Modify: `client/electron/services/rejectionCheckTask.cjs`（`runOne`、`runRejectionCheckTask`）
- Modify: `client/electron/services/taskService.cjs`（`taskDefinitions`、`startBidSubjectIdentification`、`confirmBidSubject`、`recoverInterruptedRejectionCheckTasks`、`startRejectionCheck` 注入 bidSignature）

**Interfaces:**
- Consumes: Task 8 `runBidSubjectIdentificationTask`、Task 9 `runSubjectBodyCheck`、Task 10 `runSubjectSealCheck`、Task 1 `createSubjectCheckInputSignature`、Task 3 Store 新接口。
- Produces:
  - `runRejectionCheckTask` 的 `runOptions.subjectCheck` / `runOptions.subjectSealCheck` 生效；结果键 `subjectCheckResult`
  - `taskService.startBidSubjectIdentification(payload)`、`taskService.confirmBidSubject(payload)`
  - `bid-subject-identification` taskDefinition（group `rejection-check`、field `subjectIdentificationTask`）
  - 启动主体检查前 `payload.workspaceState.bidSignature` 已注入（Store 权威计算）

- [x] **Step 1: `runOne` 兼容 `{ findings, stats }` 返回值**

`rejectionCheckTask.cjs` 的 `runOne`（第 1654 行）中，`const findings = await runner(...)` 之后改为：

```js
      const runnerResult = await runner((message) => {
        updateOverall(`${label}：${message}`, { [resultKey]: createRunningResult(inputSignature, message) });
      });
      const findings = Array.isArray(runnerResult) ? runnerResult : (runnerResult?.findings || []);
      const stats = Array.isArray(runnerResult) ? undefined : runnerResult?.stats;
```

对应的成功分支 `updateOverall` 的 partial 改为：

```js
      updateOverall(`${label}完成。`, {
        [resultKey]: {
          status: 'success',
          findings,
          inputSignature,
          activeFindingId: findings[0]?.id,
          progressMessage: findings.length ? `${label}发现 ${findings.length} 项` : `${label}未发现问题`,
          ...(stats ? { stats } : {}),
          updatedAt: now(),
        },
      }, true);
```

- [x] **Step 2: 在 `runRejectionCheckTask` 中增加第四分支**

顶部 require 增加：

```js
const {
  runSubjectBodyCheck,
  runSubjectSealCheck,
} = require('./subjectCheckTask.cjs');
const { createSubjectCheckInputSignature } = require('./subjectNameUtils.cjs');
```

`enabledTasks` 计算（第 1616 行）增加 subject：

```js
  const enabledTasks = [
    runOptions.rejectionCheck ? 'rejection' : '',
    runOptions.typoCheck ? 'typo' : '',
    runOptions.logicCheck ? 'logic' : '',
    runOptions.subjectCheck ? 'subject' : '',
  ].filter(Boolean);
```

`initialPartial`（第 1643 行）之后增加：

```js
  const subjectProfile = typeof workspaceStore.loadSubjectProfile === 'function' ? workspaceStore.loadSubjectProfile() : undefined;
  const subjectVisualAssets = typeof workspaceStore.listVisualAssets === 'function' ? workspaceStore.listVisualAssets() : [];
  const subjectInputSignature = subjectProfile?.status === 'confirmed'
    ? createSubjectCheckInputSignature({
      bidSignature,
      officialName: subjectProfile.officialName,
      aliases: subjectProfile.aliases,
      subjectSealCheck: Boolean(runOptions.subjectSealCheck),
    })
    : '';
  if (runOptions.subjectCheck && (!subjectProfile?.officialName || !subjectInputSignature)) {
    throw new Error('请先确认投标主体后再开始主体一致性检查');
  }
  if (runOptions.subjectCheck) {
    initialPartial.subjectCheckResult = {
      ...createRunningResult(subjectInputSignature, '正在检查投标主体一致性。'),
      findings: [],
      activeFindingId: undefined,
      stats: { visionStatus: runOptions.subjectSealCheck ? 'pending' : 'disabled', visionStats: undefined },
    };
  }
```

（注意：`initialPartial` 在 `updateCheckWorkspace(...initialPartial, true)` 之前构建，上述代码插入位置应在该调用之前。）

任务注册（第 1698 行 `const tasks = [];` 之后追加）：

```js
  if (runOptions.subjectCheck) {
    tasks.push(runOne('subject', '主体一致性检查', async (onProgress) => {
      const bodyFindings = await runSubjectBodyCheck(aiService, {
        bidDocuments: currentBidDocuments,
        officialName: subjectProfile.officialName,
        aliases: subjectProfile.aliases,
      }, onProgress);

      // 视觉检查是可选增强：失败不影响正文结果（spec §9.1）。
      let sealResult = { findings: [], visionStatus: runOptions.subjectSealCheck ? 'error' : 'disabled', visionStats: undefined };
      if (runOptions.subjectSealCheck) {
        onProgress('正在检查投标文件图片印章。');
        try {
          sealResult = await runSubjectSealCheck(aiService, {
            bidDocuments: currentBidDocuments,
            visualAssets: subjectVisualAssets,
            officialName: subjectProfile.officialName,
            aliases: subjectProfile.aliases,
            subjectSealCheck: true,
            mediaRootDir: workspaceStore.getSubjectMediaRootDir(),
          }, onProgress);
        } catch (error) {
          sealResult = { findings: [], visionStatus: 'error', visionStats: undefined, error: error.message };
        }
      }

      const findings = [...bodyFindings, ...sealResult.findings];
      // 排序规则（spec §12.3）：明确不一致优先，再按投标文件和原文位置。
      findings.sort((a, b) => {
        if (a.level !== b.level) return a.level === 'confirmed-mismatch' ? -1 : 1;
        if (a.bidDocumentId !== b.bidDocumentId) return String(a.bidDocumentId).localeCompare(String(b.bidDocumentId));
        return String(a.locationHint || '').localeCompare(String(b.locationHint || ''));
      });
      return {
        findings,
        stats: {
          visionStatus: sealResult.visionStatus,
          visionStats: sealResult.visionStats,
          visionMessage: sealResult.error || undefined,
          confirmedMismatchCount: findings.filter((item) => item.level === 'confirmed-mismatch').length,
          needsReviewCount: findings.filter((item) => item.level === 'needs-review').length,
        },
      };
    }, 'subjectCheckResult', subjectInputSignature));
  }
```

（上面的 `findings.sort(...)` 已并入主体分支的返回逻辑，无需在 `const findings` 声明之后再单独插入一段排序代码。）

**mediaRootDir 来源（重要）**：`runRejectionCheckTask` 作用域内已有 `workspaceStore`（第 1613 行即用它调用 `createDocumentSignature`），主体分支直接调用 Task 3 新增的 `workspaceStore.getSubjectMediaRootDir()`（返回 `path.join(rejectionCheckDir, 'subject-media')`，Store 闭包内已有 `rejectionCheckDir`）。**不要**在 `rejectionCheckTask.cjs` require `getRejectionCheckDir`（它需要 `app` 参数，本文件拿不到），也**不要**改 `taskService.startRejectionCheck` 注入——`taskService.cjs` 当前没有任何 `app`/`path`/`getRejectionCheckDir` 引用，原注入方案不可执行。`runRejectionCheckTask` 内的 `bidSignature` 也是本地计算（第 1613 行），无需注入。

- [x] **Step 3: taskService 注册识别任务与确认接口**

`taskDefinitions`（第 92 行 `rejection-items-extraction` 之前）插入：

```js
  'bid-subject-identification': {
    label: '投标主体识别',
    group: 'rejection-check',
    groupLabel: '废标项检查',
    step: 2,
    lockPolicy: 'group-exclusive',
    stateKey: 'rejectionCheck',
    field: 'subjectIdentificationTask',
  },
```

（`step` 现有取值为 extraction=1、check-run=2。识别任务与检查任务 `group-exclusive` 互斥、不会同时运行，`step: 2` 不影响正确性；若执行时发现 `step` 在任务列表 UI 中有排序展示用途，再按实际语义调整。）

顶部 require 增加：

```js
const { runBidSubjectIdentificationTask } = require('./subjectCheckTask.cjs');
```

`startRejectionItemsExtraction` 附近（第 1508 行后）插入：

```js
    startBidSubjectIdentification(payload) {
      // runner 读取 payload.workspaceState.bidSignature，因此注入必须写进 payload 本身，
      // 同时作为 startManagedTask 第 4 参（initialPartial）保持与现有调用形态一致。
      const workspaceState = {
        ...(payload?.workspaceState || {}),
        bidSignature: rejectionCheckStore.getCurrentBidSignature(),
      };
      return startManagedTask('bid-subject-identification', { ...payload, workspaceState }, runBidSubjectIdentificationTask, workspaceState);
    },
    confirmBidSubject(payload) {
      return rejectionCheckStore.confirmSubject({
        officialName: payload?.officialName,
        aliases: payload?.aliases,
      });
    },
    deleteSubjectFinding(findingId) {
      return rejectionCheckStore.deleteSubjectFinding(findingId);
    },
```

`recoverInterruptedRejectionCheckTasks`（第 1237 行）在 `rejection-check-run` 恢复段之后追加：

```js
    const staleSubjectMessage = '上次投标主体识别未完成，请重新识别';
    if (!activeTasks.has('bid-subject-identification') && state.subjectIdentificationTask?.status === 'running') {
      partial.subjectIdentificationTask = {
        ...state.subjectIdentificationTask,
        status: 'error',
        progress: 100,
        error: staleSubjectMessage,
        logs: [staleSubjectMessage],
        updated_at: now(),
      };
      partial.subjectProfile = {
        ...(state.subjectProfile || {}),
        status: 'error',
        error: staleSubjectMessage,
        updatedAt: now(),
      };
    }
```

`rejection-check-run` 的恢复段（第 1256 行）内 `markResult` 之后追加一行：

```js
      partial.subjectCheckResult = markResult(state.subjectCheckResult);
```

- [x] **Step 4: 验证**

Run: `cd client && node --check electron/services/rejectionCheckTask.cjs && node --check electron/services/taskService.cjs && node --check electron/services/subjectCheckTask.cjs`
Expected: 无输出

Run: `cd client && node --test electron/services/subjectCheckTask.test.cjs electron/services/subjectNameUtils.test.cjs`
Expected: PASS

Run: `cd client && npm run smoke:electron-native`
Expected: 退出码 0

- [x] **Step 5: 提交**

```bash
git add client/electron/services/rejectionCheckTask.cjs client/electron/services/taskService.cjs
git commit -m "feat: 废标检查接入主体一致性第四分支与主体识别受管任务"
```

---

### Task 12: IPC / preload / 资产协议

**Files:**
- Modify: `client/electron/ipc/rejectionCheckIpc.cjs`
- Modify: `client/electron/ipc/index.cjs`（`workspaceDatabaseChannels` 白名单）
- Modify: `client/electron/preload.cjs`
- Modify: `client/electron/main.cjs`（`registerAssetProtocol` 的 `assetRoots`）
- Modify: `client/src/shared/types/ipc.ts`

**Interfaces:**
- Consumes: Task 11 的 `taskService.startBidSubjectIdentification` / `confirmBidSubject` / `deleteSubjectFinding`。
- Produces（Renderer 调用面）:
  - `rejection-check:start-subject-identification`（无参）
  - `rejection-check:confirm-subject`（payload `{ officialName, aliases }`）
  - `rejection-check:delete-subject-finding`（findingId）
  - `tasks:start-bid-subject-identification`
  - `yibiao-asset://rejection-check-media/<docId>/<hash>/<file>`

- [x] **Step 1: 注册 IPC**

`rejectionCheckIpc.cjs` 增加三行（`clear` 之前）：

```js
  ipcMain.handle('rejection-check:start-subject-identification', () => taskService.startBidSubjectIdentification({}));
  ipcMain.handle('rejection-check:confirm-subject', (_event, payload) => taskService.confirmBidSubject(payload));
  ipcMain.handle('rejection-check:delete-subject-finding', (_event, findingId) => taskService.deleteSubjectFinding(findingId));
```

- [x] **Step 2: 白名单**

`client/electron/ipc/index.cjs` 的 `workspaceDatabaseChannels` 数组中，`'rejection-check:clear',` 之后插入：

```js
  'rejection-check:start-subject-identification',
  'rejection-check:confirm-subject',
  'rejection-check:delete-subject-finding',
```

并在 `'tasks:start-rejection-check',` 之后插入：

```js
  'tasks:start-bid-subject-identification',
```

- [x] **Step 3: preload**

`preload.cjs` 的 `rejectionCheck` 对象（第 219-227 行）增加：

```js
    startSubjectIdentification: () => ipcRenderer.invoke('rejection-check:start-subject-identification'),
    confirmSubject: (payload) => ipcRenderer.invoke('rejection-check:confirm-subject', payload),
    deleteSubjectFinding: (findingId) => ipcRenderer.invoke('rejection-check:delete-subject-finding', findingId),
```

`tasks` 对象（`startRejectionCheck` 附近）增加：

```js
    startBidSubjectIdentification: (payload) => ipcRenderer.invoke('tasks:start-bid-subject-identification', payload),
```

- [x] **Step 4: 资产协议**

`main.cjs` 的 `registerAssetProtocol` 中 `assetRoots` 增加一项：

```js
      const assetRoots = {
        'generated-images': getGeneratedImagesDir(app),
        'imported-images': getImportedImagesDir(app),
        'rejection-check-media': path.join(getRejectionCheckDir(app), 'subject-media'),
      };
```

若 `main.cjs` 未引入 `getRejectionCheckDir`，在 `require('./utils/paths.cjs')` 的解构中加入（保持现有 require 结构）。

- [x] **Step 5: ipc.ts 类型同步**

`client/src/shared/types/ipc.ts` 中 `rejectionCheck` 与 `tasks` 的类型声明增加对应方法（与 preload 同名同签名）：

```ts
    startSubjectIdentification: () => Promise<unknown>; // startManagedTask 的启动确认；候选结果经 subjectProfile 工作区补丁推送，不在返回值里
    confirmSubject: (payload: { officialName: string; aliases: string[] }) => Promise<{ success: boolean; message: string }>;
    deleteSubjectFinding: (findingId: string) => Promise<boolean>;
```

```ts
    startBidSubjectIdentification: (payload: { workspaceState?: unknown }) => Promise<unknown>;
```

（具体插入位置跟随 `ipc.ts` 中现有 `rejectionCheck: {` 与 `tasks: {` 对象字面量的字段顺序，保持缩进一致。）

- [x] **Step 6: 验证**

Run: `cd client && node --check electron/preload.cjs && node --check electron/ipc/rejectionCheckIpc.cjs && node --check electron/ipc/index.cjs && node --check electron/main.cjs`
Expected: 无输出

Run: `cd client && npm run build`
Expected: 退出码 0（可能只有既有 chunk 体积警告）

- [x] **Step 7: 提交**

```bash
git add client/electron/ipc/rejectionCheckIpc.cjs client/electron/ipc/index.cjs client/electron/preload.cjs client/electron/main.cjs client/src/shared/types/ipc.ts
git commit -m "feat: 主体一致性检查 IPC、preload 与图片预览协议接入"
```

---

### Task 13: Renderer 类型 + 主体确认 Dialog + 页面确认流程

**Files:**
- Modify: `client/src/features/rejection-check/types.ts`
- Create: `client/src/features/rejection-check/components/SubjectConfirmDialog.tsx`
- Modify: `client/src/features/rejection-check/pages/RejectionCheckPage.tsx`
- Modify: `client/src/styles/feature-rejection-check.css`（该 feature 的实际样式文件，经 `client/src/styles.css:17` 全局引入；页面组件本身不 import CSS）

**Interfaces:**
- Consumes: Task 12 的 bridge 方法；Task 3 的 `subjectProfile` / `subjectCheckResult` 状态结构。
- Produces:
  - 类型：`SubjectFindingLevel`、`SubjectFindingSource`、`SubjectLocationKind`、`SubjectVisionStatus`、`SubjectVisionStats`、`SubjectCheckFinding`、`SubjectCandidate`、`SubjectProfileState`、`SubjectCheckResultState`
  - `RejectionCheckOptions` 增加 `subjectCheck`、`subjectSealCheck`
  - `RejectionCheckResultTab` 增加 `'subject'`
  - `SubjectConfirmDialog` props：`{ open, candidates, preselectIndex, defaultOfficialName, defaultAliases, sealEnabled, sealAvailable, onSealEnabledChange, onConfirm, onCancel, onReidentify, identifying }`

- [x] **Step 1: 类型扩展**

`client/src/features/rejection-check/types.ts` 修改：

```ts
export type RejectionCheckResultTab = 'rejection' | 'typo' | 'logic' | 'subject';

export interface RejectionCheckOptions {
  rejectionCheck: boolean;
  typoCheck: boolean;
  logicCheck: boolean;
  subjectCheck: boolean;
  subjectSealCheck: boolean;
}

export type SubjectProfileStatus = 'idle' | 'identifying' | 'pending-confirmation' | 'confirmed' | 'error';

export type SubjectFindingLevel = 'confirmed-mismatch' | 'needs-review';

export type SubjectFindingSource = 'text' | 'seal';

export type SubjectLocationKind = 'body' | 'table' | 'signature' | 'standalone-image' | 'scanned-page';

export type SubjectVisionStatus = 'disabled' | 'pending' | 'running' | 'success' | 'partial' | 'unavailable' | 'error';

export interface SubjectVisionStats {
  totalAssets: number;
  candidateAssets: number;
  inspectedAssets: number;
  sealAssets: number;
  skippedAssets: number;
  reviewAssets: number;
  failedAssets: number;
}

export interface SubjectCandidate {
  bidDocumentId: string;
  name: string;
  confidence: number;
  locationHint: string;
  originalText: string;
  roleEvidence: string;
  aliases: string[];
}

export interface SubjectProfileState {
  status: SubjectProfileStatus;
  officialName: string;
  aliases: string[];
  candidates: SubjectCandidate[];
  preselectIndex?: number;
  bidSignature?: string;
  confirmedAt?: string;
  error?: string;
  updatedAt?: string;
}

export interface SubjectCheckFinding {
  id: string;
  bidDocumentId: string;
  level: SubjectFindingLevel;
  source: SubjectFindingSource;
  expectedSubject: string;
  detectedSubject: string;
  locationKind: SubjectLocationKind;
  title: string;
  summary: string;
  locationHint: string;
  originalText: string;
  roleReason: string;
  mismatchReason: string;
  suggestion: string;
  confidence: number;
  visualAssetId?: string;
  assetRelativePath?: string;
  sealCropRelativePath?: string;
}

export interface SubjectCheckResultState {
  status: RejectionCheckRunStatus;
  findings: SubjectCheckFinding[];
  inputSignature?: string;
  activeFindingId?: string;
  progressMessage?: string;
  updatedAt?: string;
  error?: string;
  stats?: {
    visionStatus: SubjectVisionStatus;
    visionStats?: SubjectVisionStats;
    visionMessage?: string;
    confirmedMismatchCount?: number;
    needsReviewCount?: number;
  };
}
```

`RejectionBackgroundTaskType` 增加 `'bid-subject-identification'`；`RejectionCheckWorkspaceState` 增加：

```ts
  subjectCheckResult?: SubjectCheckResultState;
  subjectProfile?: SubjectProfileState;
  subjectIdentificationTask?: RejectionBackgroundTaskState;
```

`RejectionCheckWorkspacePatch` 的 `Omit` 列表与交叉类型增加 `subjectCheckResult`：

```ts
export type RejectionCheckWorkspacePatch = Omit<Partial<RejectionCheckWorkspaceState>,
  'rejectionCheckResult' | 'typoCheckResult' | 'logicCheckResult' | 'subjectCheckResult'> & {
  rejectionCheckResult?: Partial<RejectionCheckResultState>;
  typoCheckResult?: Partial<TypoCheckResultState>;
  logicCheckResult?: Partial<LogicCheckResultState>;
  subjectCheckResult?: Partial<SubjectCheckResultState>;
};
```

- [x] **Step 2: 主体确认 Dialog**

创建 `client/src/features/rejection-check/components/SubjectConfirmDialog.tsx`：

```tsx
import { useEffect, useMemo, useState } from 'react';
import { AppDialog } from '../../../shared/ui/AppDialog';
import type { SubjectCandidate } from '../types';

interface SubjectConfirmDialogProps {
  open: boolean;
  candidates: SubjectCandidate[];
  preselectIndex?: number;
  defaultOfficialName: string;
  defaultAliases: string[];
  sealEnabled: boolean;
  sealAvailable: boolean;
  identifying?: boolean;
  onSealEnabledChange: (enabled: boolean) => void;
  onConfirm: (payload: { officialName: string; aliases: string[] }) => void;
  onReidentify: () => void;
  onCancel: () => void;
}

export function SubjectConfirmDialog({
  open,
  candidates,
  preselectIndex,
  defaultOfficialName,
  defaultAliases,
  sealEnabled,
  sealAvailable,
  identifying = false,
  onSealEnabledChange,
  onConfirm,
  onReidentify,
  onCancel,
}: SubjectConfirmDialogProps) {
  const [officialName, setOfficialName] = useState(defaultOfficialName);
  const [aliases, setAliases] = useState<string[]>(defaultAliases);
  const [aliasDraft, setAliasDraft] = useState('');

  useEffect(() => {
    if (!open) return;
    setOfficialName(defaultOfficialName);
    setAliases(defaultAliases);
    setAliasDraft('');
  }, [open, defaultOfficialName, defaultAliases]);

  const selectedCandidate = useMemo(
    () => candidates.find((candidate) => candidate.name === officialName),
    [candidates, officialName],
  );

  function addAlias() {
    const value = aliasDraft.trim();
    if (!value || aliases.includes(value)) {
      setAliasDraft('');
      return;
    }
    setAliases([...aliases, value]);
    setAliasDraft('');
  }

  return (
    <AppDialog
      open={open}
      onOpenChange={(next) => { if (!next) onCancel(); }}
      title="确认投标主体"
      description="系统已从投标文件中识别出以下候选主体。请确认正式投标主体和允许简称后再开始检查。"
      actions={(
        <>
          <button type="button" className="ghost-button" onClick={onReidentify} disabled={identifying}>
            重新识别
          </button>
          <button type="button" className="ghost-button" onClick={onCancel} disabled={identifying}>
            取消
          </button>
          <button
            type="button"
            className="primary-button"
            disabled={identifying || !officialName.trim()}
            onClick={() => onConfirm({ officialName: officialName.trim(), aliases })}
          >
            {identifying ? '识别中…' : '确认并开始检查'}
          </button>
        </>
      )}
    >
      <div className="subject-confirm-dialog">
        <section className="subject-confirm-dialog__candidates">
          <h4>识别到的候选主体</h4>
          {candidates.length === 0 && <p className="muted">未识别到候选，请手工填写正式主体名称。</p>}
          <ul>
            {candidates.map((candidate, index) => (
              <li key={`${candidate.bidDocumentId}-${candidate.name}-${index}`}>
                <label>
                  <input
                    type="radio"
                    name="subject-candidate"
                    checked={officialName === candidate.name}
                    onChange={() => setOfficialName(candidate.name)}
                  />
                  <span className="subject-confirm-dialog__name">{candidate.name}</span>
                  <span className="subject-confirm-dialog__confidence">
                    置信度 {(candidate.confidence * 100).toFixed(0)}%
                    {preselectIndex === index ? '（系统预选）' : ''}
                  </span>
                </label>
                <p className="subject-confirm-dialog__evidence">
                  {candidate.locationHint ? `位置：${candidate.locationHint}　` : ''}
                  {candidate.roleEvidence || ''}
                </p>
                {candidate.originalText && (
                  <blockquote className="subject-confirm-dialog__quote">{candidate.originalText}</blockquote>
                )}
              </li>
            ))}
          </ul>
        </section>

        <section className="subject-confirm-dialog__field">
          <h4>正式投标主体名称</h4>
          <input
            type="text"
            value={officialName}
            placeholder="请输入完整的投标主体名称"
            onChange={(event) => setOfficialName(event.target.value)}
          />
        </section>

        <section className="subject-confirm-dialog__field">
          <h4>允许简称</h4>
          <p className="muted">
            只有这里确认的简称才会被视为投标方本身。系统不会自动把“有限公司”后缀、母子公司或相似名称视为同一主体。
          </p>
          <div className="subject-confirm-dialog__aliases">
            {aliases.map((alias) => (
              <span key={alias} className="subject-confirm-dialog__alias">
                {alias}
                <button type="button" onClick={() => setAliases(aliases.filter((item) => item !== alias))} aria-label={`删除简称 ${alias}`}>
                  ×
                </button>
              </span>
            ))}
          </div>
          <div className="subject-confirm-dialog__alias-input">
            <input
              type="text"
              value={aliasDraft}
              placeholder="输入简称后回车添加"
              onChange={(event) => setAliasDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  addAlias();
                }
              }}
            />
            <button type="button" onClick={addAlias}>添加</button>
          </div>
        </section>

        <section className="subject-confirm-dialog__field">
          <h4>语义代称说明</h4>
          <p className="muted">
            “我公司、本公司、我方、本投标人、投标人、应答人、供应商、响应人”等代称在语义上代表投标方，
            只会帮助模型理解发言角色，不作为公司名称参与字符串比较。
          </p>
        </section>

        <section className="subject-confirm-dialog__field">
          <h4>在线印章检查</h4>
          <label className="subject-confirm-dialog__switch">
            <input
              type="checkbox"
              checked={sealEnabled && sealAvailable}
              disabled={!sealAvailable}
              onChange={(event) => onSealEnabledChange(event.target.checked)}
            />
            <span>启用在线印章检查（可选）</span>
          </label>
          {!sealAvailable && (
            <p className="muted">当前文本模型未开启多模态支持，请到设置页的文本模型中开启“支持多模态”。</p>
          )}
          <p className="muted">
            启用后，筛选出的投标文件图片和必要的附近文字会发送到当前配置的多模态模型服务；客户端不会上传整份 DOCX。
            关闭此项后，正文主体检查仍可正常执行。
          </p>
        </section>
      </div>
    </AppDialog>
  );
}
```

（**已核实** `AppDialog` 实际 props 只有 `open/onOpenChange/kicker/title/description/children/actions/cardClassName/preventClose`（`client/src/shared/ui/AppDialog.tsx:4-19`），没有 `confirmText/confirmDisabled/onConfirm/onCancel/extraActions` 这类约定式按钮 props——确认/取消/重新识别按钮必须通过 `actions` 插槽自备，取消经 `onOpenChange(false)` 触发。按钮 className 跟随项目现有按钮样式类，执行时以该文件中其他 Dialog 的按钮类名为准。）

- [x] **Step 3: 页面确认流程接入**

`RejectionCheckPage.tsx` 修改：

1. 导入：

```tsx
import { SubjectConfirmDialog } from '../components/SubjectConfirmDialog';
import type { SubjectCheckFinding, SubjectCheckResultState, SubjectProfileState } from '../types';
```

2. `checkResultTabs`（第 42 行）追加：

```tsx
  { id: 'subject', label: '主体一致性检查', description: '检查投标方身份、承诺与盖章位置是否被其他主体替代' },
```

同时 tab 状态三元链（`RejectionCheckPage.tsx:2102-2111`，`tab.id === 'rejection' ? visibleRejectionCheckStatus : tab.id === 'typo' ? ... : visibleLogicCheckStatus`）需补第四臂 `tab.id === 'subject' ? visibleSubjectCheckStatus : ...`，并仿照 `visibleLogicCheckStatus` 定义 `visibleSubjectCheckStatus`（由 `checkOptions.subjectCheck` 与 `subjectCheckResult.status` 推导，含 inputSignature 过期判断，跟随现有三个 status 的推导写法）。

3. 新增状态（跟随现有 `useState` 区域）：

```tsx
  const [subjectProfile, setSubjectProfile] = useState<SubjectProfileState>({ status: 'idle', officialName: '', aliases: [], candidates: [] });
  const [subjectCheckResult, setSubjectCheckResult] = useState<SubjectCheckResultState>({ status: 'idle', findings: [] });
  const [subjectIdentificationTask, setSubjectIdentificationTask] = useState<RejectionBackgroundTaskState | undefined>();
  const [subjectDialogOpen, setSubjectDialogOpen] = useState(false);
  const [subjectSealAvailable, setSubjectSealAvailable] = useState(false);
```

4. 读取多模态开关（跟随现有配置读取 effect；若页面已有 `window.yibiao.config.load()` 调用则合并）：

```tsx
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const config = await window.yibiao.config.load();
        if (cancelled) return;
        setSubjectSealAvailable(Boolean(config?.components?.text_model_provider?.multimodal_enabled));
      } catch {
        if (!cancelled) setSubjectSealAvailable(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);
```

5. `applyWorkspaceState`（第 717 行）内增加：

```tsx
    setSubjectProfile(state.subjectProfile || { status: 'idle', officialName: '', aliases: [], candidates: [] });
    setSubjectCheckResult(state.subjectCheckResult || { status: 'idle', findings: [] });
    setSubjectIdentificationTask(normalizeBackgroundTaskState(state.subjectIdentificationTask));
```

6. `applyWorkspacePatch`（第 750 行）内增加：

```tsx
    if (has('subjectProfile')) setSubjectProfile(patch.subjectProfile || { status: 'idle', officialName: '', aliases: [], candidates: [] });
    if (has('subjectCheckResult')) setSubjectCheckResult((prev) => ({ ...prev, ...(patch.subjectCheckResult || {}) }));
    if (has('subjectIdentificationTask')) setSubjectIdentificationTask(normalizeBackgroundTaskState(patch.subjectIdentificationTask));
```

7. 启动识别与确认（放在 `startChecks` 之前）：

```tsx
  async function startSubjectIdentification() {
    try {
      setSubjectProfile((prev) => ({ ...prev, status: 'identifying' }));
      // IPC 返回的是 startManagedTask 的启动确认，不是识别结果；
      // 候选经 subjectProfile 工作区补丁异步推送，Dialog 先打开并显示"识别中"，候选到达后由 patch 更新。
      await window.yibiao.rejectionCheck.startSubjectIdentification();
      setSubjectDialogOpen(true);
    } catch (error) {
      const message = error instanceof Error ? error.message : '投标主体识别失败';
      setSubjectProfile((prev) => ({ ...prev, status: 'error', error: message }));
      showToast(message, 'error');
    }
  }

  async function handleSubjectConfirm({ officialName, aliases }: { officialName: string; aliases: string[] }) {
    const result = await window.yibiao.rejectionCheck.confirmSubject({ officialName, aliases });
    if (!result?.success) {
      showToast(result?.message || '投标主体确认失败', 'error');
      return;
    }
    setSubjectProfile((prev) => ({ ...prev, status: 'confirmed', officialName, aliases }));
    setSubjectDialogOpen(false);
    showToast('投标主体已确认', 'success');
    // spec §12.2：确认后启动总检查（用户最初勾选的全部检查项；startChecks() 默认用当前 checkOptions）。
    await startChecks();
  }
```

8. `startChecks` 内，在现有三种检查的 `if` 之后追加主体分支的本地乐观状态：

```tsx
    if (runOptions.subjectCheck) {
      setSubjectCheckResult({
        status: 'running',
        findings: [],
        progressMessage: '正在检查投标主体一致性。',
        stats: { visionStatus: runOptions.subjectSealCheck && subjectSealAvailable ? 'pending' : 'disabled' },
        updatedAt: startedAt,
      });
    }
```

并把第 1168 行的守卫改为：

```tsx
    if (!runOptions.rejectionCheck && !runOptions.typoCheck && !runOptions.logicCheck && !runOptions.subjectCheck) {
      showToast('请至少启用一种检查', 'info');
      return;
    }
```

以及第 1173 行的默认页签选择改为：

```tsx
    const nextActiveCheckResultTab: RejectionCheckResultTab = runOptions.rejectionCheck
      ? 'rejection'
      : runOptions.typoCheck
        ? 'typo'
        : runOptions.logicCheck
          ? 'logic'
          : 'subject';
```

9. 启动前的主体前置校验（插在第 1166 行 `currentRejectionCheckInputSignature` 校验之后）：

```tsx
    if (runOptions.subjectCheck && subjectProfile.status !== 'confirmed') {
      showToast('请先确认投标主体', 'info');
      await startSubjectIdentification();
      return;
    }
```

10. Dialog 挂载（放在页面返回 JSX 的末尾、根容器内）：

```tsx
      <SubjectConfirmDialog
        open={subjectDialogOpen}
        candidates={subjectProfile.candidates || []}
        preselectIndex={subjectProfile.preselectIndex}
        defaultOfficialName={subjectProfile.officialName || ''}
        defaultAliases={subjectProfile.aliases || []}
        sealEnabled={checkOptions.subjectSealCheck}
        sealAvailable={subjectSealAvailable}
        identifying={subjectProfile.status === 'identifying'}
        onSealEnabledChange={(enabled) => setCheckOptions((prev) => ({ ...prev, subjectSealCheck: enabled }))}
        onConfirm={handleSubjectConfirm}
        onReidentify={startSubjectIdentification}
        onCancel={() => setSubjectDialogOpen(false)}
      />
```

11. 导出调用（第 1459 行）request 增加：

```tsx
      const result = await window.yibiao.rejectionCheck.exportExcel({
        rejectionInputSignature: currentRejectionCheckInputSignature,
        bidSignature,
        subjectInputSignature: subjectCheckResult.inputSignature || '',
        subjectOfficialName: subjectProfile.officialName || '',
        subjectAliases: subjectProfile.aliases || [],
      });
```

- [x] **Step 4: 验证**

Run: `cd client && npm run build`
Expected: 退出码 0

Run: `cd client && npm run dev`
Expected: 打开废标项检查页，勾选“主体一致性检查”后点击开始检查，弹出主体确认 Dialog；未开启多模态时印章开关禁用并显示设置页提示文案。

- [x] **Step 5: 提交**

```bash
git add client/src/features/rejection-check/types.ts client/src/features/rejection-check/components/SubjectConfirmDialog.tsx client/src/features/rejection-check/pages/RejectionCheckPage.tsx
git commit -m "feat: 主体一致性检查前端类型、确认弹窗与启动流程"
```

---

### Task 14: 结果页签、筛选、删除与样式

**Files:**
- Modify: `client/src/features/rejection-check/pages/RejectionCheckPage.tsx`
- Modify: `client/src/styles/feature-rejection-check.css`（该 feature 的实际样式文件，经 `client/src/styles.css:17` 全局引入）
- Modify: `client/src/features/rejection-check/exportState.ts`

**Interfaces:**
- Consumes: Task 13 的类型与状态；Task 12 的 `deleteSubjectFinding`。
- Produces:
  - `SubjectFindingItem` 组件（本文件内）
  - `hasExportableRejectionResults` 支持 `subjectCheckResult`
  - 结果页签空/运行/部分完成/失败/过期状态展示

- [x] **Step 1: 导出可用性扩展**

`client/src/features/rejection-check/exportState.ts`：

```ts
import type { LogicCheckResultState, RejectionCheckResultState, SubjectCheckResultState, TypoCheckResultState } from './types';

type ExportableResultState = RejectionCheckResultState | TypoCheckResultState | LogicCheckResultState | SubjectCheckResultState;

function isCurrentExportableResult(result: ExportableResultState, expectedSignature: string) {
  return Boolean(
    expectedSignature
    && result.inputSignature === expectedSignature
    && (result.status === 'success' || result.findings.length > 0),
  );
}

export function hasExportableRejectionResults({
  rejectionCheckResult,
  typoCheckResult,
  logicCheckResult,
  subjectCheckResult,
  rejectionInputSignature,
  bidSignature,
  subjectInputSignature,
}: {
  rejectionCheckResult: RejectionCheckResultState;
  typoCheckResult: TypoCheckResultState;
  logicCheckResult: LogicCheckResultState;
  subjectCheckResult?: SubjectCheckResultState;
  rejectionInputSignature: string;
  bidSignature: string;
  subjectInputSignature?: string;
}) {
  return isCurrentExportableResult(rejectionCheckResult, rejectionInputSignature)
    || isCurrentExportableResult(typoCheckResult, bidSignature)
    || isCurrentExportableResult(logicCheckResult, bidSignature)
    || Boolean(subjectCheckResult && isCurrentExportableResult(subjectCheckResult, subjectInputSignature || ''));
}
```

`RejectionCheckPage.tsx` 中 `hasExportableCurrentResult` 的调用（第 661 行）同步补 `subjectCheckResult` 与 `subjectInputSignature: subjectCheckResult.inputSignature || ''`。

- [x] **Step 2: 视觉状态文案与结果项组件**

在 `RejectionCheckPage.tsx` 内、组件外新增（跟随该文件既有辅助函数位置）：

```tsx
const subjectVisionStatusText: Record<string, string> = {
  disabled: '未启用印章检查',
  pending: '等待印章检查',
  running: '正在检查印章',
  success: '印章检查已完成',
  partial: '印章检查部分完成',
  unavailable: '当前文本模型未开启多模态支持，未执行印章检查',
  error: '印章检查失败',
};

function SubjectFindingItem({
  finding,
  documentName,
  onDelete,
}: {
  finding: SubjectCheckFinding;
  documentName: string;
  onDelete: (findingId: string) => void;
}) {
  const isConfirmed = finding.level === 'confirmed-mismatch';
  return (
    <article className={`subject-finding subject-finding--${isConfirmed ? 'confirmed' : 'review'}`}>
      <header className="subject-finding__header">
        <span className={`subject-finding__level subject-finding__level--${isConfirmed ? 'confirmed' : 'review'}`}>
          {isConfirmed ? '明确不一致' : '需人工复核'}
        </span>
        <span className="subject-finding__source">{finding.source === 'seal' ? '印章' : '正文'}</span>
        <span className="subject-finding__file">{documentName}</span>
        <span className="subject-finding__confidence">置信度 {(finding.confidence * 100).toFixed(0)}%</span>
        <button type="button" className="ghost-button" onClick={() => onDelete(finding.id)}>删除</button>
      </header>
      <h4 className="subject-finding__title">{finding.title}</h4>
      <dl className="subject-finding__meta">
        <div><dt>正确主体</dt><dd>{finding.expectedSubject}</dd></div>
        <div><dt>检测主体</dt><dd>{finding.detectedSubject}</dd></div>
        <div><dt>位置</dt><dd>{finding.locationHint || '未识别位置'}</dd></div>
        <div><dt>位置类型</dt><dd>{({ body: '正文', table: '表格', signature: '签署/落款', 'standalone-image': '独立图片', 'scanned-page': '整页扫描件' } as Record<string, string>)[finding.locationKind] || finding.locationKind}</dd></div>
      </dl>
      {finding.originalText && <blockquote className="subject-finding__quote">{finding.originalText}</blockquote>}
      {finding.assetRelativePath && (
        <img
          className="subject-finding__preview"
          src={`yibiao-asset://rejection-check-media/${encodeURIComponent(finding.bidDocumentId)}/${finding.assetRelativePath.split('/').map(encodeURIComponent).join('/')}`}
          alt="印章位置预览"
        />
      )}
      <p className="subject-finding__reason"><strong>角色判断：</strong>{finding.roleReason || '—'}</p>
      <p className="subject-finding__reason"><strong>不一致原因：</strong>{finding.mismatchReason || '—'}</p>
      <p className="subject-finding__suggestion"><strong>建议：</strong>{finding.suggestion || '—'}</p>
    </article>
  );
}
```

- [x] **Step 3: 结果页签渲染**

在结果页签内容区（`RejectionCheckPage.tsx:2140` 附近）追加 subject 分支。**已核实**现有渲染模式是 `activeCheckResult.id === 'rejection' ? (...) : activeCheckResult.id === 'typo' ? renderTypoCheckContent() : renderLogicCheckContent()`，其中 typo/logic 走 `renderXxxCheckContent()` 辅助函数——subject 分支应照此模式包成 `renderSubjectCheckContent()` 并接入三元链末尾，而不是新增独立的 `activeCheckResultTab === 'subject'` 判断：

```tsx
              {activeCheckResultTab === 'subject' && (
                <div className="check-results__subject">
                  <div className="subject-summary">
                    <div className="subject-summary__item">
                      <span className="subject-summary__label">当前确认主体</span>
                      <span className="subject-summary__value">{subjectProfile.officialName || '未确认'}</span>
                    </div>
                    <div className="subject-summary__item">
                      <span className="subject-summary__label">允许简称</span>
                      <span className="subject-summary__value">{subjectProfile.aliases.length ? subjectProfile.aliases.join('、') : '无'}</span>
                    </div>
                    <div className="subject-summary__item">
                      <span className="subject-summary__label">明确不一致</span>
                      <span className="subject-summary__value">{subjectCheckResult.findings.filter((item) => item.level === 'confirmed-mismatch').length}</span>
                    </div>
                    <div className="subject-summary__item">
                      <span className="subject-summary__label">需人工复核</span>
                      <span className="subject-summary__value">{subjectCheckResult.findings.filter((item) => item.level === 'needs-review').length}</span>
                    </div>
                    <div className="subject-summary__item">
                      <span className="subject-summary__label">印章检查</span>
                      <span className="subject-summary__value">
                        {subjectVisionStatusText[subjectCheckResult.stats?.visionStatus || 'disabled'] || '未执行'}
                        {subjectCheckResult.stats?.visionStats
                          ? `（已检查 ${subjectCheckResult.stats.visionStats.inspectedAssets}/${subjectCheckResult.stats.visionStats.totalAssets} 张，印章 ${subjectCheckResult.stats.visionStats.sealAssets} 张，跳过 ${subjectCheckResult.stats.visionStats.skippedAssets} 张，失败 ${subjectCheckResult.stats.visionStats.failedAssets} 张）`
                          : ''}
                      </span>
                    </div>
                  </div>

                  {subjectCheckResult.status === 'idle' && (
                    <p className="check-results__empty">尚未执行主体一致性检查。</p>
                  )}
                  {subjectCheckResult.status === 'running' && (
                    <p className="check-results__empty">{subjectCheckResult.progressMessage || '正在检查投标主体一致性。'}</p>
                  )}
                  {subjectCheckResult.status === 'error' && (
                    <p className="check-results__empty">{subjectCheckResult.error || '主体一致性检查失败'}</p>
                  )}
                  {subjectCheckResult.status === 'success' && subjectCheckResult.findings.length === 0 && (
                    <p className="check-results__empty">检查已完成，未发现主体不一致问题。</p>
                  )}
                  {subjectCheckResult.findings.map((finding) => (
                    <SubjectFindingItem
                      key={finding.id}
                      finding={finding}
                      documentName={bidDocuments.find((document) => document.id === finding.bidDocumentId)?.fileName || finding.bidDocumentId}
                      onDelete={handleDeleteSubjectFinding}
                    />
                  ))}
                </div>
              )}
```

配套删除处理（放在 `exportCheckResultsExcel` 附近）：

```tsx
  async function handleDeleteSubjectFinding(findingId: string) {
    const removed = await window.yibiao.rejectionCheck.deleteSubjectFinding(findingId);
    if (!removed) {
      showToast('删除失败，该结果可能已被清理', 'error');
      return;
    }
    setSubjectCheckResult((prev) => ({ ...prev, findings: prev.findings.filter((item) => item.id !== findingId) }));
    showToast('已删除该条主体检查结果', 'success');
  }
```

- [x] **Step 4: 样式**

在 `client/src/styles/feature-rejection-check.css` 末尾追加。**已核实**项目 CSS 变量为 `--yb-*` 前缀（定义于 `client/src/styles/tokens.css`），不存在 `--border-color/--text-secondary/--surface-subtle/--color-danger/--color-warning`，下述样式一律使用实际存在的变量：

```css
.subject-confirm-dialog__candidates ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
.subject-confirm-dialog__candidates li { border: 1px solid var(--yb-border-soft); border-radius: 8px; padding: 10px 12px; }
.subject-confirm-dialog__name { font-weight: 600; margin-left: 8px; }
.subject-confirm-dialog__confidence { margin-left: 12px; color: var(--yb-text-soft); font-size: 12px; }
.subject-confirm-dialog__evidence { margin: 6px 0 0; color: var(--yb-text-soft); font-size: 13px; }
.subject-confirm-dialog__quote { margin: 6px 0 0; padding: 6px 10px; border-left: 3px solid var(--yb-border-soft); color: var(--yb-text-soft); font-size: 13px; }
.subject-confirm-dialog__aliases { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0; }
.subject-confirm-dialog__alias { display: inline-flex; align-items: center; gap: 6px; padding: 3px 8px; border-radius: 999px; background: var(--yb-surface); font-size: 13px; }
.subject-confirm-dialog__alias-input { display: flex; gap: 8px; }
.subject-confirm-dialog__switch { display: flex; align-items: center; gap: 8px; }

.subject-summary { display: flex; flex-wrap: wrap; gap: 16px; padding: 12px 0 16px; border-bottom: 1px solid var(--yb-border-soft); margin-bottom: 16px; }
.subject-summary__item { display: flex; flex-direction: column; gap: 4px; min-width: 120px; }
.subject-summary__label { color: var(--yb-text-soft); font-size: 12px; }
.subject-summary__value { font-weight: 600; font-size: 14px; }

.subject-finding { border: 1px solid var(--yb-border-soft); border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.subject-finding--confirmed { border-left: 4px solid var(--yb-danger); }
.subject-finding--review { border-left: 4px solid var(--yb-warning); }
.subject-finding__header { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 12px; color: var(--yb-text-soft); }
.subject-finding__level--confirmed { color: var(--yb-danger); font-weight: 600; }
.subject-finding__level--review { color: var(--yb-warning); font-weight: 600; }
.subject-finding__title { margin: 8px 0; font-size: 15px; }
.subject-finding__meta { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px; margin: 0 0 10px; }
.subject-finding__meta dt { color: var(--yb-text-soft); font-size: 12px; }
.subject-finding__meta dd { margin: 2px 0 0; font-size: 13px; }
.subject-finding__quote { margin: 0 0 10px; padding: 8px 12px; border-left: 3px solid var(--yb-border-soft); background: var(--yb-surface); font-size: 13px; }
.subject-finding__preview { max-width: 320px; max-height: 220px; border: 1px solid var(--yb-border-soft); border-radius: 6px; margin-bottom: 10px; display: block; }
.subject-finding__reason, .subject-finding__suggestion { margin: 4px 0; font-size: 13px; }
```

（以上变量均已核实存在于 `client/src/styles/tokens.css`：`--yb-border-soft`、`--yb-text-soft`、`--yb-surface`、`--yb-danger`、`--yb-warning`。不要新增全局变量。）

- [x] **Step 5: 验证**

Run: `cd client && npm run build`
Expected: 退出码 0

Run: `cd client && npm run dev`
Expected: 主体页签展示摘要头、级别标签、原文、图片预览与删除按钮；多模态关闭时显示"当前文本模型未开启多模态支持，未执行印章检查"。

- [x] **Step 6: 提交**

```bash
git add client/src/features/rejection-check/pages/RejectionCheckPage.tsx client/src/features/rejection-check/exportState.ts client/src/styles/feature-rejection-check.css
git commit -m "feat: 主体一致性检查结果页签、图片预览与样式"
```

---

### Task 15: Excel 导出第五张表

**Files:**
- Modify: `client/electron/services/checkResultExportService.cjs`
- Test: `client/electron/services/checkResultExportService.subject.test.cjs`

**Interfaces:**
- Consumes: Task 3 的 `subjectCheckResult`（含 `inputSignature`、`stats`）；Task 12 传入的 `request.subjectInputSignature`。
- Produces:
  - `rejectionSheetNames` 扩展为五张（新增 `'投标主体'`）
  - `selectCurrentRejectionResults` 返回 `subjectCheckResult` 描述符并纳入 `exportable`
  - 概览新增四行统计：当前确认主体、允许简称、主体结果数量、印章状态与视觉覆盖
  - `buildRejectionWorkbook` 追加第五张表

- [x] **Step 1: 写失败测试**

创建 `client/electron/services/checkResultExportService.subject.test.cjs`：

```js
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
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd client && node --test electron/services/checkResultExportService.subject.test.cjs`
Expected: FAIL（`SheetNames` 只有四张 / `selectCurrentRejectionResults` 无 `subjectCheckResult`）

- [x] **Step 3: 写实现**

`checkResultExportService.cjs` 四处修改：

1. 第 9 行 sheet 名扩展：

```js
const rejectionSheetNames = ['检查概览', '废标项', '错别字', '逻辑问题', '投标主体'];
```

2. `selectCurrentRejectionResults`（第 75 行）增加 subject 描述符：

```js
    subjectCheckResult: getRejectionResultDescriptor(
      workspace.subjectCheckResult,
      request?.subjectInputSignature,
    ),
```

并把 `const results = [...]` 与 `selected.exportable` 的 `some` 判定加入 `selected.subjectCheckResult`。

3. `buildRejectionWorkbook` 概览段（第 178-195 行）扩展：

```js
  const overviewDefinitions = [
    ['废标项检查', selected.rejectionCheckResult],
    ['错别字检查', selected.typoCheckResult],
    ['逻辑问题检查', selected.logicCheckResult],
    ['主体一致性检查', selected.subjectCheckResult],
  ];
  const subjectStats = selected.subjectCheckResult.stats || {};
  const visionStats = subjectStats.visionStats;
  const overviewRows = [
    ['导出时间', formatDisplayTime(exportedAt)],
    [],
    ['检查类型', '状态', '问题数量', '更新时间', '检查对象', '说明'],
    ...overviewDefinitions.map(([label, result]) => [
      label,
      getResultStatus(result),
      result.valid ? result.findings.length : 0,
      result.updatedAt || '',
      bidNames,
      getResultMessage(result, result.findings.length, '检查已完成，未发现问题'),
    ]),
    [],
    ['主体检查统计项', '数值'],
    ['当前确认主体', request?.subjectOfficialName || ''],
    ['允许简称', Array.isArray(request?.subjectAliases) ? request.subjectAliases.join('、') : ''],
    ['主体结果数量', selected.subjectCheckResult.valid ? selected.subjectCheckResult.findings.length : 0],
    ['印章检查状态', subjectVisionStatusLabels[subjectStats.visionStatus] || '未执行'],
    ['视觉覆盖', visionStats
      ? `已检查 ${visionStats.inspectedAssets}/${visionStats.totalAssets}，印章 ${visionStats.sealAssets}，跳过 ${visionStats.skippedAssets}，失败 ${visionStats.failedAssets}`
      : ''],
  ];
```

并在文件顶部（`rejectionSheetNames` 之后）加常量：

```js
const subjectVisionStatusLabels = {
  disabled: '未启用印章检查',
  pending: '等待印章检查',
  running: '正在检查印章',
  success: '印章检查已完成',
  partial: '印章检查部分完成',
  unavailable: '当前文本模型未开启多模态支持，未执行印章检查',
  error: '印章检查失败',
};
```

4. `buildRejectionWorkbook` 末尾（`return workbook;` 之前）追加第五张表：

```js
  const subjectRows = [
    ['序号', '投标文件序号', '投标文件名', '级别', '来源', '正确主体', '检测主体', '位置类型', '位置', '原文或印章文字', '角色依据', '不一致原因', '置信度', '建议'],
    ...selected.subjectCheckResult.findings.map((finding, index) => [
      index + 1,
      ...getDocumentColumns(documentLookup, finding.bidDocumentId),
      finding.level === 'confirmed-mismatch' ? '明确不一致' : '需人工复核',
      finding.source === 'seal' ? '印章' : '正文',
      finding.expectedSubject,
      finding.detectedSubject,
      ({ body: '正文', table: '表格', signature: '签署/落款', 'standalone-image': '独立图片', 'scanned-page': '整页扫描件' })[finding.locationKind] || finding.locationKind,
      finding.locationHint,
      finding.originalText,
      finding.roleReason,
      finding.mismatchReason,
      Number(finding.confidence || 0),
      finding.suggestion,
    ]),
  ];
  appendWorksheet(workbook, rejectionSheetNames[4], subjectRows, [8, 14, 28, 14, 10, 32, 32, 14, 28, 48, 42, 42, 12, 42], 0);
```

`module.exports` 的 `__test__` 增加 `selectCurrentRejectionResults`、`buildRejectionWorkbook`（若尚未导出）。

- [x] **Step 4: 运行测试确认通过**

Run: `cd client && node --test electron/services/checkResultExportService.subject.test.cjs`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add client/electron/services/checkResultExportService.cjs client/electron/services/checkResultExportService.subject.test.cjs
git commit -m "feat: 废标检查 Excel 导出新增投标主体表与视觉覆盖统计"
```

---

### Task 16: 全量验证与手动验收

**Files:**
- 无代码改动（本任务是验证关卡；发现问题回到对应任务修复）

**Interfaces:**
- Consumes: Task 1-15 的全部产出。
- Produces: 可交付的验证结论；若发现缺陷，回到对应任务修复后重跑本任务。

- [x] **Step 1: 全部改动 .cjs 语法检查**

Run:

```bash
cd client && for f in electron/services/subjectNameUtils.cjs electron/services/docxVisualAssetExtractor.cjs electron/services/subjectMediaFiles.cjs electron/services/subjectVisionProvider.cjs electron/services/subjectCheckTask.cjs electron/services/rejectionCheckTask.cjs electron/services/rejectionCheckStore.cjs electron/services/fileService.cjs electron/services/taskService.cjs electron/services/sqliteDatabase.cjs electron/services/checkResultExportService.cjs electron/ipc/rejectionCheckIpc.cjs electron/ipc/index.cjs electron/preload.cjs electron/main.cjs; do node --check "$f" || echo "FAIL: $f"; done
```

Expected: 无 `FAIL` 行

- [x] **Step 2: 定向单元测试**

Run:

```bash
cd client && node --test electron/services/subjectNameUtils.test.cjs electron/services/docxVisualAssetExtractor.test.cjs electron/services/subjectMediaFiles.test.cjs electron/services/subjectVisionProvider.test.cjs electron/services/subjectCheckTask.test.cjs electron/services/checkResultExportService.subject.test.cjs
```

Expected: 全部 PASS（`# fail 0`）

- [x] **Step 3: native smoke**

Run: `cd client && npm run smoke:electron-native`
Expected: 退出码 0

- [x] **Step 4: 构建**

Run: `cd client && npm run build`
Expected: 退出码 0（允许既有 chunk 体积警告）

- [ ] **Step 5: 手动验收（`npm run dev`）**

对照规格 §15.2 核心行为场景逐项确认：

1. 正式名称、允许简称及"我公司"等代称不报警。
2. 投标方承诺、投标函、报价表或落款中出现其他公司名称时报警。
3. 客户案例、招标人、制造商、认证机构等第三方名称不报警。
4. 投标方盖章位置出现其他公司清晰印章时报警。
5. 制造商授权函、第三方承诺函和认证证书中的合法第三方印章不报警。
6. 模糊印章、相似名称或角色不清进入"需人工复核"。
7. 多模态关闭时正文完成，印章状态明确显示"当前文本模型未开启多模态支持，未执行印章检查"。
8. 导入或删除投标文件后主体确认与旧结果失效（页面回到待确认状态）。
9. 同一图片内容只调用一次 VLM，但保留并判断每个出现位置。
10. Excel 导出包含五张表，投标主体表内容与页面一致，概览显示印章状态与视觉覆盖。

- [x] **Step 6: 记录验收结论并提交**

```bash
git status
git log --oneline -16
```

若手动验收发现缺陷：回到对应任务修复、重跑该任务的测试与本任务 Step 1-4，再重新验收。

---

## 执行完成状态

本计划 16 个任务全部实现完成。Task 1-10 由前期会话完成，本次执行 Task 11-16 并复核 Task 1-10 产出。

| Task | 内容 | 提交 |
|---|---|---|
| 1 | 主体名称纯函数工具 `subjectNameUtils.cjs` | `7b0c5df` |
| 2 | SQLite migration v24（三张新表 + `stats_json`） | `7b0c5df` |
| 3 | Store 主体档案 / 视觉资产 / 主体结果读写 | `3e243c5` |
| 4 | DOCX 视觉材料提取器 | `49b0265` |
| 5 | 媒体落盘与回收工具 | `45e1827` |
| 6 | 导入链路集成（fileService + bid 分支 + 清理联动） | `f37569c` |
| 7 | 在线 VLM Provider | `ccb27cf` |
| 8 | 主体候选识别任务 | `9c7a31b` |
| 9 | 正文主体检查（滚动 + 定稿 + 防幻觉） | `f0931fd` |
| 10 | 印章检查编排 `runSubjectSealCheck` | `a7c8290` |
| 11 | 第四分支集成 + taskService 注册 | `0ffe05a` |
| 12 | IPC / preload / 资产协议 | `17a4837` |
| 13 | Renderer 类型 + 主体确认 Dialog + 页面确认流程 | `ab7676d` |
| 14 | 结果页签、筛选、删除与样式 | `81dc410` |
| 15 | Excel 导出第五张表 | `73c9bfe` |
| 16 | 全量验证与手动验收 | 无代码改动 |

## 验收记录（Task 16）

- **Step 1 语法检查**：16 个 `.cjs` 全部 `node --check` 通过，无 `FAIL` 行。
- **Step 2 定向单测**：45/45 通过（`subjectNameUtils` / `docxVisualAssetExtractor` / `subjectMediaFiles` / `subjectVisionProvider` / `subjectCheckTask` / `checkResultExportService.subject` / `exportState`）。
- **Step 3 native smoke**：`npm run smoke:electron-native` 退出码 0（electron 41.10.2 / modules 145）。
- **Step 4 构建**：`npm run build` 退出码 0，仅有既有 chunk 体积警告。
- **既有无关失败**：`electron/services/knowledgeBaseStore.search.test.cjs` 有 4 个用例失败；`git diff f0931fd..HEAD` 证明该测试与其源文件本次零改动，属既有问题，与本功能无关。
- **Step 5 手动验收：未执行**。需要人工在 GUI 中逐项确认 spec §15.2 的 10 个场景；且执行本任务时本机 5173 端口已有 dev 会话在运行，其 Electron 主进程启动早于 Task 11/12 的 Main 侧改动，必须先重启 dev 才能验收。
- **Step 6 记录**：`git status` 干净（仅 `docs/`、`.workbuddy/`、`.claude/` 等未跟踪目录）；提交链见上表。

## 执行偏差与计划修正

实现过程中发现计划以下位置与实际代码不符，已按实际代码修正执行：

1. **Task 12** 还需修改 `electron/ipc/taskIpc.cjs` 注册 `tasks:start-bid-subject-identification` 的真实 handler；仅加白名单与 preload 不会生效。
2. **Task 13** 还需在检查配置弹窗中补「主体一致性检查」「在线印章检查」两个开关；`subjectCheck` / `subjectSealCheck` 均默认开启（spec §12.1），不补 UI 该功能无法开关。
3. **Task 13** 中 Renderer 可以复算主体输入签名：按 Main 侧 `subjectNameUtils.cjs` 复刻 `normalizeSubjectName` / `createSubjectCheckInputSignature`（与既有 `createRejectionCheckInputSignature` 在 Renderer 重复实现的做法一致），用于结果过期判断与本地乐观状态；Main 仍是权威计算方。原「Renderer 无法复算」的结论不成立。
4. **Task 13** 原写的 `config.components.text_model_provider.multimodal_enabled` 路径不存在：`ComponentsConfig` 无该字段，多模态开关位于 `ClientConfig` 顶层（`AiConfig extends TextModelConfig`），与 `aiService.getConfig()` 的读取位置一致，应读 `config.multimodal_enabled`。
5. **Task 15** 的测试与实现代码对「投标主体」表列顺序相互矛盾（测试要求第 4 列为来源、第 5 列正确主体、第 6 列检测主体，实现代码把「级别」放在该位置）。按 TDD 以测试为准，最终表头为：`序号 / 投标文件序号 / 投标文件名 / 来源 / 正确主体 / 检测主体 / 级别 / 位置类型 / 位置 / 原文或印章文字 / 角色依据 / 不一致原因 / 置信度 / 建议`。
6. **Task 15** 还需同步修改既有 `checkResultExportService.test.cjs` 中「固定生成四张工作表」的断言（改为五张，并补主体表表头与概览主体行断言），否则该既有测试会失败。
7. **Task 11** 的 Files 中写了「`taskService.startRejectionCheck` 注入 `bidSignature`」，但按该任务正文的 note，`runRejectionCheckTask` 内部已本地计算 `bidSignature`，无需注入，故未改动 `startRejectionCheck`。

## 待人工验收清单（spec §15.2）

1. 正式名称、允许简称及「我公司」等代称不报警。
2. 投标方承诺、投标函、报价表或落款中出现其他公司名称时报警。
3. 客户案例、招标人、制造商、认证机构等第三方名称不报警。
4. 投标方盖章位置出现其他公司清晰印章时报警。
5. 制造商授权函、第三方承诺函和认证证书中的合法第三方印章不报警。
6. 模糊印章、相似名称或角色不清进入「需人工复核」。
7. 多模态关闭时正文完成，印章状态明确显示「当前文本模型未开启多模态支持，未执行印章检查」。
8. 导入或删除投标文件后主体确认与旧结果失效（页面回到待确认状态）。
9. 同一图片内容只调用一次 VLM，但保留并判断每个出现位置。
10. Excel 导出包含五张表，投标主体表内容与页面一致，概览显示印章状态与视觉覆盖。

## 自查结论

**1. 规格覆盖检查**

| 规格章节 | 覆盖任务 |
|---|---|
| §6 DOCX 视觉材料提取 | Task 4（提取器）、Task 5（落盘）、Task 6（导入集成） |
| §7 主体候选识别与确认 | Task 1（门槛/规范化）、Task 8（识别）、Task 13（确认 Dialog） |
| §8 正文主体检查 | Task 9（滚动+定稿+防幻觉） |
| §9 在线 VLM 印章检查 | Task 7（Provider）、Task 10（编排与分级） |
| §10 结果模型与分级 | Task 9、Task 10、Task 3（持久化） |
| §11 持久化与状态失效 | Task 2（表）、Task 3（读写）、Task 6（失效联动）、Task 11（恢复） |
| §12 页面交互 | Task 13（配置与确认流程）、Task 14（结果页签） |
| §13 错误处理 | Task 4（媒体损坏继续）、Task 7（分批失败）、Task 10（视觉不可用/失败）、Task 11（重启恢复） |
| §14 导出 | Task 15 |
| §15 测试与验收 | 各任务 TDD 步骤 + Task 16 |

**2. 占位符扫描**：无 TBD/TODO；所有代码步骤均给出可执行代码；所有验证步骤给出具体命令与期望结果。

**3. 类型一致性核对**（跨任务接口）

- `applySubjectAutoSelectRules(candidates) → number`：Task 1 定义，Task 8 使用（`preselectIndex`）✔
- `matchesAllowedSubject(value, officialName, aliases)`：Task 1 定义，Task 9、Task 10 使用 ✔
- `createSubjectCheckInputSignature({ bidSignature, officialName, aliases, subjectSealCheck, protocolVersion? })`：Task 1 定义，Task 11 使用 ✔
- `normalizeSubjectCandidates(payload, bidDocuments)` / `parseAliasesFromCandidates(candidates)`：Task 8 定义与使用 ✔
- `runSubjectBodyCheck(aiService, input, onProgress) → SubjectCheckFinding[]`：Task 9 定义，Task 11 使用 ✔
- `runSubjectSealCheck(aiService, input, onProgress) → { findings, visionStatus, visionStats }`：Task 10 定义，Task 11 使用 ✔
- `resolveVisualAssetAbsolutePath({ mediaRootDir, bidDocumentId, relativePath })`：Task 5 定义，Task 10 使用 ✔
- `saveVisualAssets(bidDocumentId, rows)` / `listVisualAssets(bidDocumentId?)`：Task 3 定义，Task 6、Task 11 使用 ✔
- Store 结果键统一为 `subjectCheckResult`，DB `result_type` 统一为 `'subject'` ✔
- 任务类型字符串 `'bid-subject-identification'` 在 Task 3（字段映射）、Task 6（取消列表）、Task 11（注册）、Task 12（IPC）一致 ✔
- 资产协议 hostname `'rejection-check-media'` 在 Task 5、Task 12、Task 14 一致 ✔

**已知风险与处置**：`AppDialog` 若不支持 `extraActions`，按 Task 13 Step 2 的说明改为 children 内按钮；`getRejectionCheckDir` 在 Main 侧调用需要 `app`，Task 11 Step 2 已改为由 `taskService` 注入 `mediaRootDir`，执行时若发现 Store 已有等价目录接口则优先复用。







