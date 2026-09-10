# 投标主体确认流程优化设计（手工优先 + 识别范围收窄）

- 状态：待用户复核
- 关联规格：`docs/superpowers/specs/2026-09-09-bid-subject-consistency-design.md`（下称“原规格”）
- 前序实施：原规格对应的实施计划已完成 Task 1–16

## 1. 背景

原规格 §7.1 规定“文本模型扫描当前全部投标文件”产出候选主体，§12.2 规定“没有有效主体：启动候选识别，完成后打开确认 Dialog”。落地后暴露两个耗时点：

1. **AI 识别是强制前置。** 用户只是要填一个自己早就知道的主体名称，也必须先等一次全量 AI 识别。页面三个入口（空状态按钮、结果区按钮、`startChecks` 守卫）全部直接触发识别。
2. **识别范围过宽。** 识别一次性扫描全部投标文件。投标文件越多、越长，首字延迟越高；而主体名称通常只在投标函或封面一处出现。

## 2. 目标

1. 手工填写成为默认路径：打开确认弹窗即可直接填名称与简称并确认，全程不调用 AI。
2. AI 候选识别降级为弹窗内的可选按钮，扫描范围默认收窄到第一份投标文件，可改为任意单份或回退全部。
3. 缩短“确认主体”的端到端耗时，同时保留候选证据展示、自动预选规则与“任何情况必须人工确认”这些既有约束。

## 3. 非目标

- 不改正文主体检查与在线印章检查的扫描范围，二者仍扫全部投标文件。
- 不改主体确认的持久化契约（`confirmSubject` 仍只要求名称非空）。
- 不改 `bidSignature` 计算方式（仍按全部投标文件）。
- 不新增独立的 VLM/OCR 配置入口。
- 不引入“AI 自动判断是否需要主体检查”。

## 4. 方案选择

考虑过三个方案：

- **方案 A（采纳）弹窗内一体化。** `确认投标主体` 直接打开现有确认弹窗，进来即可手填；弹窗内新增“识别范围 + AI 识别候选”。改动集中在弹窗组件与识别任务输入参数，保留现有证据展示与确认约束。
- **方案 B 页面内嵌表单。** 把名称/简称输入直接放到页面上，仅 AI 出候选时才弹窗。手填路径更短，但要改写空状态与结果页签结构，且与既有确认交互模型冲突。
- **方案 C 手填为主 + AI 只做校验。** AI 不再产出候选列表，只校验手填名称。最轻，但丢失候选证据，诊断价值下降。

采纳方案 A。

## 5. 交互流程与状态机

### 5.1 入口点

| 入口 | 现状 | 改为 |
|---|---|---|
| 主体页签空状态按钮 | `识别并确认投标主体`，点击即跑 AI | `确认投标主体`，点击只打开弹窗 |
| 结果摘要区按钮 | `重新识别并确认主体`，点击即跑 AI | `修改投标主体`，打开弹窗并回填已确认名称 / 简称 / 上次识别范围 |
| `startChecks` 守卫 | 未确认时自动启动识别并 return | 未确认时打开弹窗 + 提示“请先确认投标主体”，不启动识别 |

### 5.2 状态机

沿用原规格 §7.3 的 5 态 `idle | identifying | pending-confirmation | confirmed | error`，仅调整触发者：

- `idle`：未确认；弹窗可直接打开。
- `identifying`：**只由弹窗内“AI 识别候选”按钮触发**，页面不再自动进入。
- `pending-confirmation`：候选到达后展示列表；未识别时为空列表 + 手填提示。
- `confirmed`：用户确认（手填或选中候选）。确认后关闭弹窗并启动总检查（沿用原规格 §12.2）。
- `error`：仅 AI 识别失败时进入；弹窗内提示并可重试，手填路径不受影响。

### 5.3 关键约定

- 手工路径不调用 AI。
- AI 结果不覆盖用户已手填的名称：若用户已填名称，候选只作参考、不预选覆盖；名称仍为空时才套用原规格 §7.2 的自动预选规则。
- `identifying` 期间禁用确认与取消，避免识别结果晚到覆盖刚确认的值。
- 修改识别范围只影响下一次识别，不改已确认主体；只有确认才落库。

## 6. 数据模型

`SubjectProfileState`（Renderer）与 `subjectProfile`（Main）新增：

```ts
identificationTarget?: {
  mode: 'single' | 'all';
  bidDocumentId?: string;  // mode === 'single' 时有效
};
```

存用户的选择而非解析后的 id 列表：重开弹窗时回填下拉；文档被删时选择自动失效并回退默认，不会残留脏 id。

**存储位置**：扩展现有 `candidates_json` 信封，由 `{ items, preselectIndex }` 扩为 `{ items, preselectIndex, identificationTarget }`。理由：与候选同生共死、无需数据库迁移、不触碰 `sql/workspace_schema.sql`。未采用“新增 `identification_scope_json` 列”方案。

## 7. 识别任务输入契约

`runBidSubjectIdentificationTask` 新增可选输入：

- `payload.bidDocumentIds?: string[]`。
- 传了且非空 → 只扫这些文档；未传或空数组 → 扫描全部（向后兼容，`tasks:start-bid-subject-identification` 通道行为不变）。
- 过滤在任务内基于启动时的 `state.bidDocuments` 快照完成，中途删文档不影响本次结果。

`buildSubjectIdentificationMessages` 仅调整措辞：把“请扫描全部投标文件”改为“请扫描以下投标文件”，避免模型寻找不在输入中的内容。其余提示词、输出 JSON 格式、候选规范化逻辑不变。

## 8. IPC / preload / 类型

- `electron/ipc/rejectionCheckIpc.cjs`：`rejection-check:start-subject-identification` 由无参改为接收 `payload`。
- `electron/preload.cjs`：`startSubjectIdentification(payload)`。
- `src/shared/types/ipc.ts`：`startSubjectIdentification: (payload?: { bidDocumentIds?: string[] }) => Promise<unknown>`。

IPC 白名单通道名不变。

## 9. UI 细节

弹窗自上而下：

1. 标题说明：*“可直接手工填写，或让 AI 从指定投标文件中提取候选。”*
2. 识别区（新增）：`识别范围` 下拉 + `AI 识别候选` 按钮。
   - 下拉项：每份投标文件（显示文件名）+ `全部投标文件`；默认第一份。
   - 下拉只列出**有正文内容**的投标文件；可扫文档为 0 时按钮禁用并提示先导入。
   - 仅 1 份可扫文档时不显示下拉，直接扫该份。
   - 识别完成后按钮文案变 `重新识别`。
3. 候选列表：有候选才展示；未识别且无候选时提示*“可直接手工填写，或点上方『AI 识别候选』”*。
4. 正式名称、允许简称、语义代称说明、在线印章检查：不变。
5. 底部按钮：`确认并开始检查`、`取消`。删除原「重新识别」按钮，避免与识别区入口语义重复。

## 10. 错误处理

- AI 识别失败 → 弹窗内识别区展示错误并可重试；手填路径可用。
- 名称为空 → 禁用确认（现有行为）。
- `identifying` 中禁用确认与取消（见 §5.3）。

## 11. 与原规格的差异（修订点）

- 原 §7.1“扫描当前全部投标文件” → 改为“默认扫描第一份投标文件，可指定单份或全部”。
- 原 §12.2“没有有效主体：启动候选识别，完成后打开确认 Dialog” → 改为“没有有效主体：打开确认弹窗，用户可手工填写或点『AI 识别候选』”。
- 原 §12.2“多个接近候选时不预选”保持不变。

## 12. 测试与验收

### 12.1 自动测试

- `electron/services/subjectCheckTask.test.cjs`：
  - 传 `bidDocumentIds` 时，识别 messages 只包含选定文件名；
  - 未传时仍包含全部文件；
  - 提示词措辞断言。
- `electron/services/rejectionCheckStore` 相关：`identificationTarget` 存取往返。

### 12.2 手动验收

1. 不点 AI 直接手填名称 + 简称 → 确认后能正常启动检查（零 AI 调用）。
2. 默认范围 = 第一份；切到其他单份 / 全部后识别范围随之变化。
3. 重开弹窗时识别范围回填。
4. 仅 1 份投标文件时无下拉、直接扫该份。
5. AI 识别失败时弹窗可重试且手填可用。

### 12.3 验证命令

```
cd client
node --check <改动的 .cjs>
node --test electron/services/subjectCheckTask.test.cjs
npm run smoke:electron-native
npm run build
```

## 13. 涉及文件

| 文件 | 改动 |
|---|---|
| `client/src/features/rejection-check/components/SubjectConfirmDialog.tsx` | 识别区、候选空态、删除「重新识别」按钮 |
| `client/src/features/rejection-check/pages/RejectionCheckPage.tsx` | 入口改打开弹窗；`startSubjectIdentification` 接收范围；范围派生与回填 |
| `client/src/features/rejection-check/types.ts` | `identificationTarget` |
| `client/electron/services/subjectCheckTask.cjs` | `bidDocumentIds` 过滤 + 提示词措辞 |
| `client/electron/services/rejectionCheckStore.cjs` | `candidates_json` 信封扩展 |
| `client/electron/services/taskService.cjs` | 无需改动（`startBidSubjectIdentification` 已整体透传 `payload`） |
| `client/electron/ipc/rejectionCheckIpc.cjs` | 通道接收 payload |
| `client/electron/preload.cjs` | 方法签名 |
| `client/src/shared/types/ipc.ts` | 类型 |
| `client/electron/services/subjectCheckTask.test.cjs` | 新增用例 |
