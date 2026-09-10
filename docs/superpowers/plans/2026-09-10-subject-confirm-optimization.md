# 投标主体确认流程优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让投标主体可以纯手工填写并确认，把 AI 候选识别降级为弹窗内的可选按钮，并把识别扫描范围从「全部投标文件」收窄为「默认第一份、可改单份或全部」。

**Architecture:** 前端把三个入口（空状态按钮、结果区按钮、`startChecks` 守卫）改为只打开确认弹窗；弹窗新增「识别范围下拉 + AI 识别候选」区。识别范围随确认请求写入 `subjectProfile.identificationTarget`，持久化在既有 `candidates_json` 信封里。Main 侧识别任务新增可选 `payload.bidDocumentIds` 收窄扫描集合。顺带修复 Store 补丁事务丢弃 `subjectProfile` 的既有缺陷。

**Tech Stack:** Electron（Main 为 CommonJS `.cjs`）、React + TypeScript（Renderer，全局 CSS，无 Tailwind）、better-sqlite3、node:test。

**Spec:** `docs/superpowers/specs/2026-09-10-subject-confirm-optimization-design.md`

## Global Constraints

- 正文主体检查与在线印章检查的扫描范围**不变**，仍扫全部投标文件。
- `bidSignature` 仍按**全部**投标文件计算（`rejectionCheckStore.getCurrentBidSignature()` 不动）。
- `confirmSubject` 的确认契约不变：只要 `officialName` 非空即可确认，手填路径不调用任何 AI。
- Electron Renderer 不得直接访问 Node/fs/ipcRenderer，只通过 `window.yibiao`；改 preload 必须同步 `src/shared/types/ipc.ts`。
- 用户可见文案用中文；提示走 `shared/ui/ToastProvider`，不要用 `alert`。
- 改动 `.cjs` 后先 `node --check <file>`；改 Renderer 后 `cd client; npm run build`。
- `npm run build` 中既有 chunk 体积警告不算失败。
- 本仓库直接在 `main` 上按任务提交，不建 worktree；提交身份用 `-c user.name="InsCode" -c user.email="noreply@inscode.net"`，且**不推送**。
- 涉及 `better-sqlite3`（Electron ABI 原生模块）的测试**不能用普通 `node --test`**，必须用 ABI 匹配的 Electron 以 Node 模式运行：`cd client && ELECTRON_RUN_AS_NODE=1 ./node_modules/electron/dist/electron.exe --test <file>`。纯 JS 的测试仍用普通 `node --test`。

---

## File Structure

| 文件 | 职责 | 本计划中的改动 |
|---|---|---|
| `client/electron/services/subjectCheckTask.cjs` | 主体识别/正文/印章检查任务实现 | 识别任务支持 `bidDocumentIds` 收窄；提示词措辞 |
| `client/electron/services/subjectCheckTask.test.cjs` | 上述模块单测 | 新增范围收窄用例 |
| `client/electron/services/rejectionCheckStore.cjs` | 废标检查 SQLite 权威存储 | 补丁事务落库 `subjectProfile`；`identificationTarget` 读写与保留 |
| `client/electron/services/rejectionCheckStore.subject.test.cjs` | Store 主体档案单测（新建） | 新增 |
| `client/electron/ipc/rejectionCheckIpc.cjs` | 废标检查 IPC 注册 | 识别通道接收 payload |
| `client/electron/preload.cjs` | `window.yibiao` 暴露层 | 识别方法签名 |
| `client/src/shared/types/ipc.ts` | IPC 类型 | 识别/确认载荷类型 |
| `client/src/features/rejection-check/types.ts` | 功能域类型 | `SubjectIdentificationTarget`、profile 新字段 |
| `client/src/features/rejection-check/components/SubjectConfirmDialog.tsx` | 主体确认弹窗 | 识别区、候选空态、删除「重新识别」 |
| `client/src/features/rejection-check/pages/RejectionCheckPage.tsx` | 主体页签与流程编排 | 入口改为打开弹窗、范围派生与回填、确认携带范围 |
| `client/src/styles/feature-rejection-check.css` | 废标检查样式 | 识别区样式 |

---

### Task 1: 识别任务支持收窄扫描范围

**Files:**
- Modify: `client/electron/services/subjectCheckTask.cjs:36-67`（提示词）、`:547-595`（任务主体）
- Test: `client/electron/services/subjectCheckTask.test.cjs:1-27`

**Interfaces:**
- Consumes: 无（首个任务）
- Produces: `runBidSubjectIdentificationTask` 接受 `payload.bidDocumentIds?: string[]`；`buildSubjectIdentificationMessages({ bidDocuments })` 签名不变

- [ ] **Step 1: 写失败测试**

在 `client/electron/services/subjectCheckTask.test.cjs` 顶部 require 块（当前第 5–9 行）加入 `runBidSubjectIdentificationTask`：

```js
const {
  buildSubjectIdentificationMessages,
  normalizeSubjectCandidates,
  parseAliasesFromCandidates,
  runBidSubjectIdentificationTask,
} = require('./subjectCheckTask.cjs');
```

在该 require 块之后、第一个 `test(...)` 之前，加入夹具：

```js
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
```

在该文件末尾追加四个用例：

```js
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd client && node --test electron/services/subjectCheckTask.test.cjs`
Expected: FAIL —— 两个范围用例报 `不应包含范围外的文档` / `请扫描以下投标文件` 断言失败；`识别范围外的候选会被丢弃` 报未 reject。

- [ ] **Step 3: 改提示词措辞**

`client/electron/services/subjectCheckTask.cjs` 第 54 行，把：

```
请扫描全部投标文件，重点但不仅限于以下区域：
```

改为：

```
请扫描以下投标文件，重点但不仅限于以下区域：
```

- [ ] **Step 4: 识别任务按范围过滤**

`client/electron/services/subjectCheckTask.cjs`，把第 549–552 行：

```js
  const bidDocuments = (Array.isArray(state.bidDocuments) ? state.bidDocuments : [])
    .map((document) => ({ ...document, content: String(document.content || '') }))
    .filter((document) => document.id && document.content.trim());
  if (!bidDocuments.length) throw new Error('缺少投标文件内容，无法识别投标主体');
```

改为：

```js
  const bidDocuments = (Array.isArray(state.bidDocuments) ? state.bidDocuments : [])
    .map((document) => ({ ...document, content: String(document.content || '') }))
    .filter((document) => document.id && document.content.trim());
  if (!bidDocuments.length) throw new Error('缺少投标文件内容，无法识别投标主体');

  // 识别范围可收窄到指定文档；未指定时保持扫描全部（向后兼容）。
  const requestedDocumentIds = Array.isArray(payload?.bidDocumentIds)
    ? payload.bidDocumentIds.map((id) => String(id))
    : [];
  const scopedBidDocuments = requestedDocumentIds.length
    ? bidDocuments.filter((document) => requestedDocumentIds.includes(document.id))
    : bidDocuments;
  if (!scopedBidDocuments.length) throw new Error('所选投标文件没有可识别的内容，请重新选择识别范围');
```

同文件第 568 行，把：

```js
    messages: buildSubjectIdentificationMessages({ bidDocuments }),
```

改为：

```js
    messages: buildSubjectIdentificationMessages({ bidDocuments: scopedBidDocuments }),
```

同文件第 576 行，把：

```js
  const candidates = mergeCandidatesByName(normalizeSubjectCandidates(response, bidDocuments));
```

改为：

```js
  const candidates = mergeCandidatesByName(normalizeSubjectCandidates(response, scopedBidDocuments));
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd client && node --test electron/services/subjectCheckTask.test.cjs`
Expected: PASS（含原有 15 个用例与新增 4 个）

- [ ] **Step 6: 提交**

```bash
cd C:/workspace/OpenBidKit_Yibiao
git add client/electron/services/subjectCheckTask.cjs client/electron/services/subjectCheckTask.test.cjs
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat: 主体识别支持收窄扫描范围"
```

---

### Task 2: Store 落库 subjectProfile 并持久化识别范围

修复既有缺陷：`updateRejectionCheckTransaction` 未处理 `partial.subjectProfile`，导致识别候选刷新后丢失。

**Files:**
- Modify: `client/electron/services/rejectionCheckStore.cjs:841-860`（事务）、`:1233-1317`（档案读写与确认）
- Test: `client/electron/services/rejectionCheckStore.subject.test.cjs`（新建）

**Interfaces:**
- Consumes: 无
- Produces: `subjectProfile.identificationTarget` 持久化；`updateRejectionCheckWithoutReload({ subjectProfile })` 生效

- [ ] **Step 1: 写失败测试**

新建 `client/electron/services/rejectionCheckStore.subject.test.cjs`：

```js
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createSqliteDatabase } = require('./sqliteDatabase.cjs');
const { createRejectionCheckStore } = require('./rejectionCheckStore.cjs');

function withStore(run) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'yibiao-subject-store-'));
  // createSqliteDatabase 会调用 app.once('will-quit', ...)，并返回 { db, close, ... } 包装对象；
  // store 需要的是包装对象里的原生 better-sqlite3 实例。
  const app = { getPath: () => dir, once: () => {} };
  const db = createSqliteDatabase(app).db;
  try {
    const store = createRejectionCheckStore({
      app,
      db,
      fileService: {},
      technicalPlanStore: {},
      taskLogStore: { list: () => [], sync: () => {} },
    });
    // 事务首次写入会补建 meta 行，先跑一次空补丁，保证后续 loadRejectionCheck 可用。
    store.updateRejectionCheckWithoutReload({});
    run(store);
  } finally {
    db.close();
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

test('补丁里的 subjectProfile 会真正落库', () => {
  withStore((store) => {
    store.updateRejectionCheckWithoutReload({
      subjectProfile: {
        status: 'pending-confirmation',
        officialName: '',
        aliases: [],
        candidates: [{
          bidDocumentId: 'bid-1',
          name: '北方长风智能科技有限公司',
          confidence: 0.9,
          locationHint: '投标函落款',
          originalText: '投标人：北方长风智能科技有限公司（盖章）',
          roleEvidence: '落款标注投标人',
          aliases: [],
        }],
        preselectIndex: 0,
      },
    });
    const profile = store.loadRejectionCheck().subjectProfile;
    assert.equal(profile.status, 'pending-confirmation');
    assert.equal(profile.candidates.length, 1);
    assert.equal(profile.candidates[0].name, '北方长风智能科技有限公司');
  });
});

test('identificationTarget 能存取往返', () => {
  withStore((store) => {
    store.updateRejectionCheckWithoutReload({
      subjectProfile: {
        status: 'confirmed',
        officialName: '北方长风智能科技有限公司',
        aliases: [],
        candidates: [],
        identificationTarget: { mode: 'single', bidDocumentId: 'bid-2' },
      },
    });
    assert.deepEqual(
      store.loadRejectionCheck().subjectProfile.identificationTarget,
      { mode: 'single', bidDocumentId: 'bid-2' },
    );
  });
});

test('后续补丁未携带 identificationTarget 时保留旧值', () => {
  withStore((store) => {
    store.updateRejectionCheckWithoutReload({
      subjectProfile: { status: 'confirmed', officialName: '甲公司', aliases: [], candidates: [], identificationTarget: { mode: 'all' } },
    });
    store.updateRejectionCheckWithoutReload({
      subjectProfile: { status: 'identifying', officialName: '', aliases: [], candidates: [] },
    });
    assert.deepEqual(
      store.loadRejectionCheck().subjectProfile.identificationTarget,
      { mode: 'all' },
    );
  });
});

test('resetSubjectProfile 清空 identificationTarget', () => {
  withStore((store) => {
    store.updateRejectionCheckWithoutReload({
      subjectProfile: { status: 'confirmed', officialName: '甲公司', aliases: [], candidates: [], identificationTarget: { mode: 'all' } },
    });
    store.resetSubjectProfile('投标文件已更新，请重新确认投标主体');
    const profile = store.loadRejectionCheck().subjectProfile;
    assert.equal(profile.status, 'idle');
    assert.equal(profile.identificationTarget, undefined);
  });
});

test('confirmSubject 未显式传值时保留既有 identificationTarget', () => {
  withStore((store) => {
    store.updateRejectionCheckWithoutReload({
      subjectProfile: {
        status: 'pending-confirmation',
        officialName: '',
        aliases: [],
        candidates: [],
        identificationTarget: { mode: 'single', bidDocumentId: 'bid-1' },
      },
    });
    store.confirmSubject({ officialName: '北方长风智能科技有限公司', aliases: [] });
    const profile = store.loadRejectionCheck().subjectProfile;
    assert.equal(profile.status, 'confirmed');
    assert.deepEqual(profile.identificationTarget, { mode: 'single', bidDocumentId: 'bid-1' });
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd client && ELECTRON_RUN_AS_NODE=1 ./node_modules/electron/dist/electron.exe --test electron/services/rejectionCheckStore.subject.test.cjs`
Expected: FAIL —— 第 1 个用例 `status` 为 `idle`（补丁被丢弃）；`identificationTarget` 相关用例得到 `undefined`。

**注意**：这个测试涉及 Electron ABI 的原生模块 `better-sqlite3`，**不能用普通 `node --test` 运行**（会报 `ERR_DLOPEN_FAILED`），必须走上面的 Electron-as-Node 命令。

- [ ] **Step 3: 事务落库 subjectProfile**

`client/electron/services/rejectionCheckStore.cjs`，在第 857–859 行的任务字段循环之后、事务闭合之前插入：

```js
    if (hasOwn(partial, 'subjectProfile')) saveSubjectProfile(partial.subjectProfile);
```

即把第 857–860 行改为：

```js
    for (const [field, type] of Object.entries(taskFieldTypes)) {
      if (hasOwn(partial, field)) saveTask(type, partial[field]);
    }
    if (hasOwn(partial, 'subjectProfile')) saveSubjectProfile(partial.subjectProfile);
  });
```

- [ ] **Step 4: 读取时解析 identificationTarget**

`client/electron/services/rejectionCheckStore.cjs` 第 1233–1249 行的 `loadSubjectProfile`，把返回对象里的 `preselectIndex` 一行之后补上 `identificationTarget`。改后该返回块为：

```js
    return {
      status: normalizeStatus(row.status, ['idle', 'identifying', 'pending-confirmation', 'confirmed', 'error'], 'idle'),
      officialName: row.official_name || '',
      aliases: safeJsonParse(row.aliases_json, []) || [],
      candidates: items,
      preselectIndex: Number.isInteger(candidatesPayload.preselectIndex) ? candidatesPayload.preselectIndex : undefined,
      identificationTarget: normalizeIdentificationTarget(candidatesPayload.identificationTarget),
      bidSignature: row.bid_signature || undefined,
      confirmedAt: row.confirmed_at || undefined,
      error: row.error || undefined,
      updatedAt: row.updated_at || undefined,
    };
```

在 `loadSubjectProfile` 函数定义之前（第 1233 行之上）新增规范化辅助函数：

```js
  function normalizeIdentificationTarget(target) {
    if (!target || typeof target !== 'object') return undefined;
    if (target.mode === 'all') return { mode: 'all' };
    if (target.mode !== 'single') return undefined;
    const bidDocumentId = typeof target.bidDocumentId === 'string' && target.bidDocumentId
      ? target.bidDocumentId
      : undefined;
    return bidDocumentId ? { mode: 'single', bidDocumentId } : undefined;
  }
```

- [ ] **Step 5: 写入时保留 identificationTarget**

`client/electron/services/rejectionCheckStore.cjs` 的 `saveSubjectProfile`（第 1251 行起），把函数开头与 `candidates_json` 一行改为：

```js
  function saveSubjectProfile(profile) {
    // 未显式携带 identificationTarget 时保留旧值，避免任务 checkpoint 把它冲掉。
    const identificationTarget = hasOwn(profile || {}, 'identificationTarget')
      ? profile.identificationTarget
      : loadSubjectProfile().identificationTarget;
    const normalized = {
      status: normalizeStatus(profile?.status, ['idle', 'identifying', 'pending-confirmation', 'confirmed', 'error'], 'idle'),
      official_name: String(profile?.officialName || ''),
      aliases_json: jsonOrNull(Array.isArray(profile?.aliases) ? profile.aliases : []),
      candidates_json: jsonOrNull({
        items: Array.isArray(profile?.candidates) ? profile.candidates : [],
        preselectIndex: Number.isInteger(profile?.preselectIndex) ? profile.preselectIndex : -1,
        identificationTarget: identificationTarget || null,
      }),
      bid_signature: profile?.bidSignature ? String(profile.bidSignature) : null,
      confirmed_at: profile?.confirmedAt ? String(profile.confirmedAt) : null,
      error: profile?.error ? String(profile.error) : null,
      updated_at: now(),
    };
```

- [ ] **Step 6: 重置时显式清空**

`client/electron/services/rejectionCheckStore.cjs` 第 1281–1283 行的 `resetSubjectProfile`，改为：

```js
  function resetSubjectProfile(reason) {
    return saveSubjectProfile({
      status: 'idle',
      officialName: '',
      aliases: [],
      candidates: [],
      identificationTarget: null,
      error: reason || undefined,
    });
  }
```

- [ ] **Step 7: 确认时按需携带 identificationTarget**

`client/electron/services/rejectionCheckStore.cjs` 第 1297–1317 行的 `confirmSubject`，把签名与档案写入改为：

```js
  function confirmSubject({ officialName, aliases, identificationTarget } = {}) {
    const name = String(officialName || '').trim();
    if (!name) return { success: false, message: '请填写正式投标主体名称' };
    const normalizedAliases = (Array.isArray(aliases) ? aliases : [])
      .map((item) => String(item || '').trim())
      .filter(Boolean);
    const previous = loadSubjectProfile();
    const profilePatch = {
      status: 'confirmed',
      officialName: name,
      aliases: normalizedAliases,
      candidates: previous.candidates,
      preselectIndex: previous.preselectIndex,
      bidSignature: getCurrentBidSignature(),
      confirmedAt: now(),
    };
    if (identificationTarget !== undefined) {
      profilePatch.identificationTarget = identificationTarget;
    }
    const profile = saveSubjectProfile(profilePatch);
```

该函数其余部分（`saveResult('subject', ...)`、`clearFindingRows('subject')`、`return`）保持不变。

- [ ] **Step 8: 运行测试确认通过**

Run: `cd client && node --check electron/services/rejectionCheckStore.cjs && ELECTRON_RUN_AS_NODE=1 ./node_modules/electron/dist/electron.exe --test electron/services/rejectionCheckStore.subject.test.cjs`
Expected: PASS（5 个用例全绿）

- [ ] **Step 9: 提交**

```bash
cd C:/workspace/OpenBidKit_Yibiao
git add client/electron/services/rejectionCheckStore.cjs client/electron/services/rejectionCheckStore.subject.test.cjs
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "fix: 主体档案补丁落库并持久化识别范围"
```

---

### Task 3: IPC / preload / 类型传递识别范围

**Files:**
- Modify: `client/electron/ipc/rejectionCheckIpc.cjs:11`
- Modify: `client/electron/preload.cjs:227`
- Modify: `client/src/shared/types/ipc.ts`（`rejectionCheck` 段与 `tasks` 段）

**Interfaces:**
- Consumes: Task 1 的 `payload.bidDocumentIds`；Task 2 的 `confirmSubject({ identificationTarget })`
- Produces: `window.yibiao.rejectionCheck.startSubjectIdentification({ bidDocumentIds })`、`window.yibiao.rejectionCheck.confirmSubject({ officialName, aliases, identificationTarget })`

- [ ] **Step 1: 识别通道接收 payload**

`client/electron/ipc/rejectionCheckIpc.cjs` 第 11 行，把：

```js
  ipcMain.handle('rejection-check:start-subject-identification', () => taskService.startBidSubjectIdentification({}));
```

改为：

```js
  ipcMain.handle('rejection-check:start-subject-identification', (_event, payload) => taskService.startBidSubjectIdentification(payload || {}));
```

- [ ] **Step 2: preload 透传 payload**

`client/electron/preload.cjs` 第 227 行，把：

```js
    startSubjectIdentification: () => ipcRenderer.invoke('rejection-check:start-subject-identification'),
```

改为：

```js
    startSubjectIdentification: (payload) => ipcRenderer.invoke('rejection-check:start-subject-identification', payload),
```

- [ ] **Step 3: 同步 IPC 类型**

`client/src/shared/types/ipc.ts`，把 `startSubjectIdentification` 声明改为：

```ts
    startSubjectIdentification: (payload?: { bidDocumentIds?: string[] }) => Promise<unknown>;
```

同文件 `confirmSubject` 声明改为：

```ts
    confirmSubject: (payload: {
      officialName: string;
      aliases: string[];
      identificationTarget?: { mode: 'single' | 'all'; bidDocumentId?: string } | null;
    }) => Promise<{ success: boolean; message?: string }>;
```

- [ ] **Step 4: 语法检查与构建**

Run: `cd client && node --check electron/ipc/rejectionCheckIpc.cjs && node --check electron/preload.cjs && npm run build`
Expected: `SYNTAX` 无错误；build 通过（允许既有 chunk 体积警告）

- [ ] **Step 5: 提交**

```bash
cd C:/workspace/OpenBidKit_Yibiao
git add client/electron/ipc/rejectionCheckIpc.cjs client/electron/preload.cjs client/src/shared/types/ipc.ts
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat: 主体识别 IPC 与 preload 支持识别范围"
```

---

### Task 4: 前端类型与弹窗识别区

**Files:**
- Modify: `client/src/features/rejection-check/types.ts:170-210`
- Modify: `client/src/features/rejection-check/components/SubjectConfirmDialog.tsx`（整体）
- Modify: `client/src/features/rejection-check/pages/RejectionCheckPage.tsx:2715-2728`（最小接线，保证编译）
- Modify: `client/src/styles/feature-rejection-check.css`（追加识别区样式）

**Interfaces:**
- Consumes: Task 3 的 `startSubjectIdentification(payload)` / `confirmSubject(payload)` 类型
- Produces: `SubjectIdentificationTarget`；`SubjectConfirmDialog` 新 props：`identificationTarget`、`bidDocuments`、`onIdentificationTargetChange`、`onIdentify`；移除 `onReidentify`

- [ ] **Step 1: 新增类型**

`client/src/features/rejection-check/types.ts`，在 `SubjectProfileStatus` 定义之后新增：

```ts
export interface SubjectIdentificationTarget {
  mode: 'single' | 'all';
  bidDocumentId?: string;
}
```

同文件 `SubjectProfileState` 中，在 `preselectIndex?: number;` 之后新增一行：

```ts
  identificationTarget?: SubjectIdentificationTarget;
```

- [ ] **Step 2: 弹窗新增识别区与空态文案**

重写 `client/src/features/rejection-check/components/SubjectConfirmDialog.tsx`：

```tsx
import { useEffect, useRef, useState } from 'react';
import { AppDialog } from '../../../shared/ui';
import type { SubjectCandidate, SubjectIdentificationTarget } from '../types';

interface SubjectConfirmDialogProps {
  open: boolean;
  candidates: SubjectCandidate[];
  preselectIndex?: number;
  defaultOfficialName: string;
  defaultAliases: string[];
  identificationTarget: SubjectIdentificationTarget;
  bidDocuments: { id: string; fileName: string }[];
  sealEnabled: boolean;
  sealAvailable: boolean;
  onSealEnabledChange: (enabled: boolean) => void;
  identifying?: boolean;
  onIdentificationTargetChange: (target: SubjectIdentificationTarget) => void;
  onIdentify: () => void;
  onConfirm: (payload: { officialName: string; aliases: string[] }) => void;
  onCancel: () => void;
}

/**
 * 投标主体确认弹窗（spec §12.2）：手工填写为主，AI 候选识别为弹窗内可选操作。
 * 任何情况下都必须由用户确认，系统只做自动预选提示。
 */
export function SubjectConfirmDialog({
  open,
  candidates,
  preselectIndex,
  defaultOfficialName,
  defaultAliases,
  identificationTarget,
  bidDocuments,
  sealEnabled,
  sealAvailable,
  identifying = false,
  onIdentificationTargetChange,
  onIdentify,
  onConfirm,
  onCancel,
}: SubjectConfirmDialogProps) {
  const [officialName, setOfficialName] = useState(defaultOfficialName);
  const [aliases, setAliases] = useState<string[]>(defaultAliases);
  const [aliasDraft, setAliasDraft] = useState('');
  const wasOpenRef = useRef(false);

  useEffect(() => {
    // 仅在弹窗由关闭转为打开时灌入初值；识别结果后到时不得覆盖用户已填内容。
    if (open && !wasOpenRef.current) {
      setOfficialName(defaultOfficialName);
      setAliases(defaultAliases);
      setAliasDraft('');
    }
    wasOpenRef.current = open;
  }, [open, defaultOfficialName, defaultAliases]);

  useEffect(() => {
    // 候选后到时：仅在用户尚未填写名称的情况下套用系统预选。
    if (!open || identifying || officialName.trim()) return;
    if (preselectIndex === undefined || preselectIndex < 0) return;
    const candidate = candidates[preselectIndex];
    if (candidate) setOfficialName(candidate.name);
  }, [open, identifying, officialName, preselectIndex, candidates]);

  function addAlias() {
    const value = aliasDraft.trim();
    if (!value || aliases.includes(value)) {
      setAliasDraft('');
      return;
    }
    setAliases([...aliases, value]);
    setAliasDraft('');
  }

  const selectValue = identificationTarget.mode === 'all'
    ? 'all'
    : identificationTarget.bidDocumentId || bidDocuments[0]?.id || '';

  return (
    <AppDialog
      open={open}
      onOpenChange={(next) => { if (!next) onCancel(); }}
      kicker="主体确认"
      title="确认投标主体"
      description="可直接手工填写正式主体和允许简称；也可以让 AI 从指定投标文件中提取候选。任何情况下都需要你确认。"
      actions={(
        <>
          <button type="button" className="secondary-action" onClick={onCancel} disabled={identifying}>
            取消
          </button>
          <button
            type="button"
            className="primary-action"
            disabled={identifying || !officialName.trim()}
            onClick={() => onConfirm({ officialName: officialName.trim(), aliases })}
          >
            {identifying ? '识别中…' : '确认并开始检查'}
          </button>
        </>
      )}
    >
      <div className="subject-confirm-dialog">
        <section className="subject-confirm-dialog__field">
          <h4>AI 识别候选（可选）</h4>
          <p className="muted">
            不想等待时可直接手工填写下方名称。也可以先指定识别范围，让 AI 只扫描需要的投标文件。
          </p>
          <div className="subject-confirm-dialog__identify">
            {bidDocuments.length > 1 && (
              <select
                value={selectValue}
                aria-label="识别范围"
                onChange={(event) => {
                  const value = event.target.value;
                  onIdentificationTargetChange(value === 'all'
                    ? { mode: 'all' }
                    : { mode: 'single', bidDocumentId: value });
                }}
              >
                {bidDocuments.map((document) => (
                  <option key={document.id} value={document.id}>{document.fileName}</option>
                ))}
                <option value="all">全部投标文件</option>
              </select>
            )}
            <button
              type="button"
              className="secondary-action"
              onClick={onIdentify}
              disabled={identifying || !bidDocuments.length}
            >
              {identifying ? '识别中…' : candidates.length ? '重新识别' : 'AI 识别候选'}
            </button>
          </div>
          {bidDocuments.length === 0 && (
            <p className="muted">请先导入至少一份投标文件。</p>
          )}
        </section>

        <section className="subject-confirm-dialog__candidates">
          <h4>识别到的候选主体</h4>
          {candidates.length === 0 && (
            <p className="muted">可直接手工填写下方名称，或点上方「AI 识别候选」从文档中提取。</p>
          )}
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
            <button type="button" className="secondary-action" onClick={addAlias}>添加</button>
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

export default SubjectConfirmDialog;
```

- [ ] **Step 3: 页面最小接线（保证编译）**

`client/src/features/rejection-check/pages/RejectionCheckPage.tsx` 第 2715–2728 行，把 `<SubjectConfirmDialog ... />` 替换为：

```tsx
      <SubjectConfirmDialog
        open={subjectDialogOpen}
        candidates={subjectProfile.candidates || []}
        preselectIndex={subjectProfile.preselectIndex}
        defaultOfficialName={subjectProfile.officialName || ''}
        defaultAliases={subjectProfile.aliases || []}
        identificationTarget={resolvedIdentificationTarget}
        bidDocuments={subjectBidDocuments}
        sealEnabled={checkOptions.subjectSealCheck}
        sealAvailable={subjectSealAvailable}
        identifying={subjectProfile.status === 'identifying'}
        onSealEnabledChange={(enabled) => setCheckOptions((prev) => ({ ...prev, subjectSealCheck: enabled }))}
        onIdentificationTargetChange={setPendingIdentificationTarget}
        onIdentify={() => { void startSubjectIdentification(); }}
        onConfirm={handleSubjectConfirm}
        onCancel={() => setSubjectDialogOpen(false)}
      />
```

同文件在 `useState` 区块（第 742 行 `const [subjectDialogOpen, setSubjectDialogOpen] = useState(false);` 之后）新增待确认范围状态：

```ts
  const [pendingIdentificationTarget, setPendingIdentificationTarget] = useState<SubjectIdentificationTarget | null>(null);
```

并在第 7 行的 import 中把 `SubjectIdentificationTarget` 加入类型导入列表。

`resolvedIdentificationTarget` 与 `subjectBidDocuments` 在 Task 5 定义；为让本任务先编译通过，此处先在第 760 行 `subjectProfileRef` 定义之后临时加入：

```ts
  const subjectBidDocuments: { id: string; fileName: string }[] = [];
  const resolvedIdentificationTarget: SubjectIdentificationTarget = pendingIdentificationTarget || { mode: 'single' };
```

（Task 5 会把这两行替换为真实派生逻辑。）

- [ ] **Step 4: 追加识别区样式**

在 `client/src/styles/feature-rejection-check.css` 中 `.subject-confirm-dialog__quote` 规则之后追加：

```css
.subject-confirm-dialog__identify {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.subject-confirm-dialog__identify select {
  flex: 1 1 auto;
  min-width: 0;
  height: 32px;
  padding: 0 8px;
  border: 1px solid var(--yb-border-soft);
  border-radius: 6px;
  background: var(--yb-surface);
  color: inherit;
}

.subject-confirm-dialog__identify button {
  flex: 0 0 auto;
}
```

- [ ] **Step 5: 构建验证**

Run: `cd client && npm run build`
Expected: 通过（允许既有 chunk 体积警告）

- [ ] **Step 6: 提交**

```bash
cd C:/workspace/OpenBidKit_Yibiao
git add client/src/features/rejection-check/types.ts client/src/features/rejection-check/components/SubjectConfirmDialog.tsx client/src/features/rejection-check/pages/RejectionCheckPage.tsx client/src/styles/feature-rejection-check.css
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat: 主体确认弹窗新增可选识别区"
```

---

### Task 5: 页面流程改为手工优先

**Files:**
- Modify: `client/src/features/rejection-check/pages/RejectionCheckPage.tsx:123-125`、`:388-404`、`:737-760`、`:1373-1400`、`:1427-1431`、`:2098-2112`、`:2175-2185`

**Interfaces:**
- Consumes: Task 4 的 `SubjectIdentificationTarget`、弹窗新 props
- Produces: `startSubjectIdentification(target?)` 新签名；`openSubjectDialog()`

- [ ] **Step 1: 规范化函数支持 identificationTarget**

在 `normalizeSubjectProfileState`（第 388 行）的返回对象里，把 `preselectIndex` 一行之后补：

```ts
    identificationTarget: state.identificationTarget
      && (state.identificationTarget.mode === 'single' || state.identificationTarget.mode === 'all')
      ? {
          mode: state.identificationTarget.mode,
          bidDocumentId: typeof state.identificationTarget.bidDocumentId === 'string'
            ? state.identificationTarget.bidDocumentId
            : undefined,
        }
      : undefined,
```

- [ ] **Step 2: 真实派生可识别文档与最终范围**

把 Task 4 Step 3 临时加入的两行（`subjectBidDocuments` / `resolvedIdentificationTarget`）替换为：

```ts
  const subjectBidDocuments = useMemo(
    () => bidDocuments
      .filter((document) => String(document.content || '').trim())
      .map((document) => ({ id: document.id, fileName: document.fileName })),
    [bidDocuments],
  );
  const resolvedIdentificationTarget: SubjectIdentificationTarget = useMemo(() => {
    const stored = pendingIdentificationTarget || subjectProfile.identificationTarget;
    if (stored?.mode === 'all') return { mode: 'all' };
    const selected = stored?.mode === 'single' && stored.bidDocumentId
      && subjectBidDocuments.some((document) => document.id === stored.bidDocumentId)
      ? stored.bidDocumentId
      : subjectBidDocuments[0]?.id;
    return selected ? { mode: 'single', bidDocumentId: selected } : { mode: 'single' };
  }, [pendingIdentificationTarget, subjectProfile.identificationTarget, subjectBidDocuments]);
```

确认文件顶部第 2 行已导入 `useMemo`（`import { useEffect, useMemo, useRef, useState } from 'react';`），无需额外改动。

- [ ] **Step 3: 改造识别入口**

把第 1373–1385 行的 `startSubjectIdentification` 整体替换为：

```ts
  function openSubjectDialog() {
    setPendingIdentificationTarget(null);
    setSubjectDialogOpen(true);
  }

  async function startSubjectIdentification(target: SubjectIdentificationTarget = resolvedIdentificationTarget) {
    const bidDocumentIds = target.mode === 'all'
      ? subjectBidDocuments.map((document) => document.id)
      : target.bidDocumentId
        ? [target.bidDocumentId]
        : [];
    if (!bidDocumentIds.length) {
      showToast('请先导入至少一份投标文件', 'info');
      return;
    }
    try {
      setPendingIdentificationTarget(target);
      setSubjectProfile((prev) => ({ ...prev, status: 'identifying' }));
      // 返回值是 startManagedTask 的启动确认；候选经工作区补丁异步推送。
      await window.yibiao.rejectionCheck.startSubjectIdentification({ bidDocumentIds });
      setSubjectDialogOpen(true);
    } catch (error) {
      const message = error instanceof Error ? error.message : '投标主体识别失败';
      setSubjectProfile((prev) => ({ ...prev, status: 'error', error: message }));
      showToast(message, 'error');
    }
  }
```

- [ ] **Step 4: 确认时携带识别范围**

把 `handleSubjectConfirm`（第 1387–1400 行）的确认调用改为：

```ts
    const result = await window.yibiao.rejectionCheck.confirmSubject({
      officialName,
      aliases,
      identificationTarget: resolvedIdentificationTarget,
    });
```

并在确认成功后关闭弹窗的行之后补一行，清空待确认范围（保留其余逻辑不变）：

```ts
    setPendingIdentificationTarget(null);
```

- [ ] **Step 5: 守卫改为打开弹窗**

把第 1427–1431 行：

```ts
    if (runOptions.subjectCheck && subjectProfileRef.current.status !== 'confirmed') {
      showToast('请先确认投标主体', 'info');
      await startSubjectIdentification();
      return;
    }
```

改为：

```ts
    if (runOptions.subjectCheck && subjectProfileRef.current.status !== 'confirmed') {
      showToast('请先确认投标主体', 'info');
      openSubjectDialog();
      return;
    }
```

- [ ] **Step 6: 空状态入口改为打开弹窗**

把第 2103–2110 行的按钮改为：

```tsx
          <button
            type="button"
            className="secondary-action"
            onClick={openSubjectDialog}
            disabled={checkRunning || extractionRunning || !bidDocuments.length}
          >
            {subjectProfile.status === 'identifying' ? '识别中...' : '确认投标主体'}
          </button>
```

- [ ] **Step 7: 结果区入口改为打开弹窗**

把第 2177–2184 行的按钮改为：

```tsx
            <button
              type="button"
              className="text-button"
              onClick={openSubjectDialog}
              disabled={checkRunning || extractionRunning || !bidDocuments.length}
            >
              修改投标主体
            </button>
```

- [ ] **Step 8: 构建验证**

Run: `cd client && npm run build`
Expected: 通过

- [ ] **Step 9: 全量校验残留引用**

Run: `cd client && grep -n "onReidentify\|识别并确认投标主体\|重新识别并确认主体" src/features/rejection-check/pages/RejectionCheckPage.tsx src/features/rejection-check/components/SubjectConfirmDialog.tsx`
Expected: 无输出（旧文案与旧 prop 已全部清除）

- [ ] **Step 10: 提交**

```bash
cd C:/workspace/OpenBidKit_Yibiao
git add client/src/features/rejection-check/pages/RejectionCheckPage.tsx
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat: 主体确认改为手工优先并支持指定识别范围"
```

---

### Task 6: 全量验证

**Files:**
- 无源码改动（仅验证与记录）

**Interfaces:**
- Consumes: Task 1–5 的全部改动
- Produces: 验收结论

- [ ] **Step 1: 语法检查全部改动的 .cjs**

Run:

```bash
cd client
for f in electron/services/subjectCheckTask.cjs electron/services/rejectionCheckStore.cjs electron/ipc/rejectionCheckIpc.cjs electron/preload.cjs; do node --check "$f" || echo "FAIL: $f"; done
echo SYNTAX_OK
```

Expected: 打印 `SYNTAX_OK`，无 `FAIL`

- [ ] **Step 2: 定向单测**

Run:

```bash
cd client
node --test electron/services/subjectCheckTask.test.cjs electron/services/checkResultExportService.subject.test.cjs src/features/rejection-check/exportState.test.cjs
ELECTRON_RUN_AS_NODE=1 ./node_modules/electron/dist/electron.exe --test electron/services/rejectionCheckStore.subject.test.cjs
```

Expected: 全部 PASS（Store 主体测试必须走第二条 Electron-as-Node 命令，普通 node 会 `ERR_DLOPEN_FAILED`）

- [ ] **Step 3: native 冒烟与构建**

Run:

```bash
cd client
npm run smoke:electron-native
npm run build
```

Expected: 冒烟成功；build 通过

- [ ] **Step 4: 记录手动验收清单（交给用户）**

在计划文件末尾追加 `## 手动验收清单`，列出 spec §12.2 的 5 条：

1. 不点 AI 直接手填名称 + 简称 → 确认后能正常启动检查（零 AI 调用）。
2. 默认范围 = 第一份；切到其他单份 / 全部后识别范围随之变化。
3. 确认后重开弹窗，识别范围已回填。
4. 仅 1 份投标文件时无下拉、直接扫该份。
5. AI 识别失败时弹窗可重试且手填可用。

- [ ] **Step 5: 提交**

```bash
cd C:/workspace/OpenBidKit_Yibiao
git add docs/superpowers/plans/2026-09-10-subject-confirm-optimization.md
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "docs: 补充主体确认优化手动验收清单"
```
