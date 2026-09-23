# P4 计划：废标项检查领域 + 检查闭环（spec §9.1 / §15 P4 行）

日期：2026-09-23 ｜ spec：`docs/superpowers/specs/2026-09-15-fastapi-check-service-design.md` §9.1 / §5.2 / §11 / §12
参考实现（规模）：
`rejectionCheckStore.cjs` 1473 行 · `rejectionCheckTask.cjs` 1806 行 · `bidAnalysisTask.cjs` 472 行 ·
`subjectCheckTask.cjs` 714 行 · `RejectionCheckPage.tsx` 2806 行 · 另 `checkResultExportService.cjs`（5 工作表）

## 范围裁定

- 产出 = spec §15 P4 行：「跑四项检查 → 结果页签 → 导 Excel；含印章视觉检查与
  `yibiao.sqlite` 迁移导入」。
- **P4 必须再拆**（与 P3 同理，参考实现 ~7.3k 行、11 张表、四个 AI 检查 + 一个视觉检查 +
  2806 行前端）：先按「P3a 式底座优先」钉死编排与状态机（零算法可端到端验收），再把
  算法/前端/迁移各自单独交付。
- 前端复用 P3d-3 的 `server/web` 骨架（顶栏已有「废标项检查」导航占位）与 `src/shared/`
  纯函数层；**不复用** client/ 的组件（spec §10 不变）。

## 分期（每期单独可验收）

| 期 | 产出 | 依赖 |
| --- | --- | --- |
| **P4a 底座** | 11 张 `rejection_check_*` 表落 schema（project_id 作用域）；仓储（documents / extraction / subject_profile / 四类 findings / visual_assets / meta / results）；文档导入 API（markdown 落盘 + `content_hash` + 解析器标签）；三步状态与步进 API；**四项检查以零算法 stub 提交任务**，跑通「导入 → 触发 → 四结果齐 → 状态/进度正确 → 幂等」闭环 | P1、P2a |
| **P4b 提取与检查项** | `runInvalidBidAndRejectionItemsExtraction`（含 `splitUserTextByContextLimit` 分段与 `mergeSegmentedAiResults` 合并，逐字搬）；四项检查真实 prompt + JSON 解析 + findings 归一化 + 原文定位校验；四条签名口径（`rejectionInputSignature` / `bidSignature` / `subjectInputSignature`）与前置约束（步骤 2 完成 / 步骤 4 confirmed） | P4a |
| **P4c 印章视觉检查** | `runSubjectSealCheck`（复用 P2b 图片管线 + VLM），主体检查内部串行、印章失败不连坐（`visionStatus='error'`、正文 findings 保留）、`visionStats` 口径 | P4a、P2b |
| **P4d 前端三步页 + 导出** | 对齐 `RejectionCheckPage.tsx` 三步（documents → items → results）+ 四页签 + 按投标文件筛选 + 主体确认弹窗 + 导出 Excel（5 工作表）+ 重试单个检查 | P4b、P4c、P3d-3 |
| **P4e 迁移导入** | 一次性脚本：`yibiao.sqlite` 的 `rejection_check_*` 读出来落成默认 project，复制 markdown 与图片资产（spec §12） | P4a |

## 裁定条款

- **R-P4-1（底座优先）**：P4a 的四项检查是**零算法 stub**（返回空 findings、status success），
  目的是先钉死编排：并行四检查、进度 `min(95, 5 + completed/enabledTasks × 90)`、
  前置约束报错文案、幂等与签名作废。算法在 P4b/P4c 逐项替换，编排不动——**照搬 P3a 的成功路径**。
- **R-P4-2（四检查并行 + 主体内部串行）**：`废标项/错别字/逻辑谬误/主体一致性` 四者并行
  （参考 `Promise.all`）；但**主体一致性内部**先正文、再印章，`findings = [...bodyFindings, ...sealFindings]`。
- **R-P4-3（三条硬前置，不是可选）**：① 至少启用一种检查；② 废标项检查需步骤 2 已完成；
  ③ 主体检查需步骤 4 已确认（未 confirmed 时**生成不出** `subjectInputSignature`）。
  报错文案逐字对齐参考。
- **R-P4-4（印章失败不连坐）**：`runSubjectSealCheck` 抛错只置 `visionStatus='error'`，
  正文 findings 必须保留（不得整段失败）。
- **R-P4-5（签名口径三枚）**：`rejectionInputSignature`（含检查项内容）、`bidSignature`、
  `subjectInputSignature`（依赖 profile confirmed）——三者决定幂等与作废边界，逐字对齐。
- **R-P4-6（Prompt 与分段合并逐字搬）**：Prompt 常量整体搬运（spec §14：一字不改）；
  `splitUserTextByContextLimit` / `mergeSegmentedAiResults` 原样搬，否则超长招标文件的
  解析结果与现状不一致（spec §9.1 明写）。
- **R-P4-7（`saveSubjectProfile` 的键语义）**：未携带 `identificationTarget` 键 = 保留旧值，
  显式 `null` 才清空；`normalizeSubjectProfileState` 可能产出 `undefined` 值键，直接落库会误清
  （client 侧既有坑，见项目记忆）。
- **R-P4-8（导出 5 工作表）**：`检查概览`/`废标项`/`错别字`/`逻辑问题`/`投标主体`；
  「投标主体」表末尾追加「未能识别的图片」区块（无项不追加）+ autofilter 收缩到表头+findings
  （spec §9.3，参考 `checkResultExportService.cjs:322-330` 修过的 bug）。
- **R-P4-9（测试形态）**：AI 层用假 provider（spec §11.3）；金标准对照覆盖 Prompt 构造、
  findings 归一化、签名口径、`overallProgress` 聚合；P4a 的 e2e 用真实回环服务器（SSE 用例）。
- **R-P4-10（迁移导入只读旧库）**：脚本只读 `yibiao.sqlite`、只写新库与数据目录；不修改旧库
  （它是用户唯一的历史数据源）。

## P4a 任务拆分（TDD，本次执行）

- **T1 schema**：11 张表 + 幂等迁移；每表 project_id 作用域；`rejection_check_meta` 单例改
  `UNIQUE(project_id)`（spec §5.1）；任务表不再自持（统一走 `tasks`）。
- **T2 仓储**：documents / extraction / subject_profile / findings 四类 / visual_assets / meta /
  results 的读写；`save_check_result` 的替换语义与状态补丁。
- **T3 文档导入 API**：`POST /projects/{pid}/rejection/documents`（role= tender/bid、多文件、
  解析+落盘+content_hash+parser_label，复用 P2a `save_uploaded_document`）；
  `GET /documents`、`DELETE /documents`。
- **T4 状态与步进 API**：`GET /rejection/state`（三步 + 四检查结果 + overallProgress）；
  步骤 2/3/4 的触发端点（提取、主体识别、主体确认）。
- **T5 检查编排骨架**：四检查 stub + 前置约束 + 进度口径 + 幂等/作废；`POST /rejection/run`。
- **T6 e2e + 收尾**：导入 → 触发 → 四结果齐 → 幂等 → 换文件作废；spec §15 回填 + ledger + 提交 + 双评审。

## 已知空白（登记）

- 前端三步页（P4d）；印章视觉的真实 VLM 判定（P4c）；迁移导入（P4e）。
- `rejection_check_tasks` 表在参考里存在，但按 spec §5.1「任务表统一收归 `tasks`」**不再建**——
  迁移导入时该表只读不写，登记为有意偏离。
