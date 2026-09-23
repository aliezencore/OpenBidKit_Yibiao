# P3d-3 计划：前端查重页（spec §10，Vite + React + TypeScript）

日期：2026-09-22 ｜ spec：`docs/superpowers/specs/2026-09-15-fastapi-check-service-design.md` §10 / §15 P3d 行
参考实现：`client/src/features/duplicate-check/pages/DuplicateCheckPage.tsx`（1158 行，两步流程 + 四页签）+ `exportState.ts`（exportable 判定）
服务端契约：P3a–P3d-2 已就绪的 7 个 duplicate 端点 + projects/tasks API（见下「契约面清单」）

## 范围裁定

- **只做查重页 + 最小应用壳**（spec §15 P3d 行产出即「Vite+React 查重页」）：
  顶栏项目切换器（新建/切换，localStorage 持久化）+ 查重页两步流程 +
  四段结果区 + Excel 导出。**废标项检查页与文档页属 P4**（「复用 API 与前端骨架」），
  本计划只把壳留出扩展位（导航条占位），不实现废标页。
- **不复用现有组件、不动 `client/`**（spec §10 硬约束）：`UploadBoard`/
  `FloatingToolbar`/`ProgressBar`/`useToast` 等全部以**轻量自写原语**重写
  （`src/ui.tsx`），交互语义对齐参考页面（R7 缓解路径），不追求像素级样式一致。
- **不引路由库、不引状态管理库**（spec §10）：页面内步进用组件 state；
  数据获取 REST + SSE。
- **前端落位 `server/web/`**（spec §4.2 目录结构）；构建产物 `dist/` 由根
  `.gitignore` 的全局 `dist/` 覆盖，`node_modules/` 同理（:46），无需新增忽略。
- **main.py 静态挂载**（方案 A「路由 + 静态前端」）：`StaticFiles(html=True)`
  挂 `server/web/dist`，**dist 存在才挂**；API 路由先注册、静态挂载在后，
  路由优先级不被遮蔽。

## 契约面清单（勘察结论，前端按此消费）

| 端点 | 响应要点 |
| --- | --- |
| `GET/POST /projects`、`GET /projects/{id}` | 项目列表/新建（201）；404 → 前端回落首项 |
| `POST /projects/{pid}/duplicate/files` | multipart，`tender`/`bid` 两组**全量替换**；两组字段都缺席=400；单组缺席=清空该组；「该组为空」= `filename=""` 空 part（N-4，浏览器空 input 原生形状）；逐文件容错 200 |
| `GET /projects/{pid}/duplicate/files` | `{tender:[…], bid:[…]}`，行键 `file_id/file_name/file_path/extension/size/modified_at/role/error` |
| `DELETE /projects/{pid}/duplicate/files` | 清空文件与全部结果 |
| `GET /projects/{pid}/duplicate/state` | `load_analysis` 全键（四段 + rows/duplicateGroups/pairwiseSimilarities/duplicateSentences/duplicateImages，camelCase）+ `sections`（缺段也有 pending 形状）+ `overallProgress` + `message` |
| `POST /projects/{pid}/duplicate/analysis` | body `{force?:bool}`；`{taskId}`；全失败行 400 |
| `GET /tasks/{task_id}/events` | SSE，事件 `data: {"type":"status","taskId","status":"queued/running/success/failed/cancelled",…}`，心跳 `: ping` |
| `POST /tasks/{task_id}/cancel` | `{cancelled:bool}`（本期不做取消按钮——参考页亦无） |
| `GET /projects/{pid}/duplicate/assets/{digest}` | 图片字节；**`duplicateImages[].preview_url` 已是相对端点**（`/projects/{pid}/duplicate/assets/{digest}`），`<img>` 直用 |
| `GET /projects/{pid}/duplicate/export` | xlsx 字节 + `Content-Disposition`（`filename="duplicate-check-export.xlsx"; filename*=UTF-8''…`）；不可导出 → 400 detail「没有可导出的当前标书查重结果」 |

## 裁定条款

- **R-P3d3-1（multipart 全量替换 + 会话文件集模型）**：`POST /files` 是两组
  全量替换语义（无 append），浏览器只在**本次会话选中时**才持有文件字节。
  规则：① 页面把「本会话选中的 `File` 对象集合」作为唯一可提交面；
  ② 每次提交**两组字段恒出现**——组内每个文件一个真实 part，组为空则补
  `new File([],"")` 占位 part（N-4 形状），杜绝「单组缺席=清空该组」的
  误清空；③ 页面加载时若服务端已有文件（hydrated 行），这些行**只读展示**
  （无字节不可重传），编辑集合须先「重置」（`DELETE /files`）——此时
  上传按钮禁用并提示。参考页的 `persistSelectedFiles(nextTender, nextBid)`
  语义由「重发全部会话文件」等价承载。
- **R-P3d3-2（自动启动分析）**：对齐参考 `:787-796`——进入分析步且
  `bidFiles` 非空、四段非全 `success`、本轮尚未尝试过 → `POST /analysis`
  （无 force）。守卫 ref 在「上传成功 / 重置 / 切项目 / 点重新查重」时复位，
  失败后不自动重试（防循环），用户可手点「重新查重」（force=true）。
- **R-P3d3-3（进度双通道）**：SSE（`EventSource`）做**终态信号**——收到
  `status ∈ {success, failed, cancelled}` 即关流并立即刷 `GET /state`；
  运行期间 1s 轮询 `GET /state` 做进度刷新兜底（runner 不向 bus 发进度事件，
  段级进度只落库，轮询是唯一进度源；SSE 消除轮询的终态延迟）。
- **R-P3d3-4（导出为 fetch+blob 下载）**：参考的 `dialog.showSaveDialog`+
  `openFile` 属 Electron；浏览器改为 `fetch(GET /export)` → 400 时把 detail
  逐字 toast（「没有可导出的当前标书查重结果」），200 时 blob +
  `a[download]`，文件名优先取 `Content-Disposition` 的 `filename*`
  （`标书查重结果_YYYYMMDD_HHMMSS.xlsx`）。按钮禁用条件用「任一段
  `status=='success'`」近似（前端无法自算签名，R-P3d2-1 signature 自持；
  误判由服务端 400 兜底）。
- **R-P3d3-5（纯函数逐字移植）**：`formatFileSize` / `formatDate` /
  `statusLabel` / `progressText` / `fileIndexLabel`（bijective base-26，A..Z,
  AA..）/ `buildFileLabelMap` / 重复句子 600 字截断 / 定位线索 72 字截断，
  从参考页 `:38-96` **逐字移植**进 `src/lib/format.ts`，Vitest 单测 +
  判别力探针。页面级签名 SHA1（参考 `:98-167`）**不移植**——signature
  已服务端自持（R-P3d2-1）。
- **R-P3d3-6（结果区行为对齐）**：四页签 id/序/标签（metadata/outline/
  content/image ↔ 元数据/目录/正文/图片）；元数据页签头部双进度块
  （contentExtraction/metadataExtraction）+ 表格 `is-duplicate`/`is-same-day`
  高亮 + files 缺席回落 bidFiles 占位（对齐参考 `:198`）；目录/正文分页
  50/页、图片 24/页，翻页 reset（`useEffect setPage(1)`）；复制按钮走
  `navigator.clipboard` + toast。`exportState.ts` 的 exportable 判定不移植
  （signature 维度服务端自持，见 R-P3d3-4）。
- **R-P3d3-7（Vitest 轻测试）**：纯函数单测 + `react-dom/server`
  `renderToString` 冒烟（不引 jsdom/RTL，node 环境即可）；构建冒烟 =
  `npm run build` 产物存在。交互流（上传/自动启动/SSE）由 T7 的真实服务
  冒烟与双评审 B 承担。
- **R-P3d3-8（dev 代理）**：`vite.config.ts` dev server 代理 `/` →
  `http://127.0.0.1:8000`（保留 `/@vite`、`/@fs`、`src` 等给 Vite 本体），
  生产形态即方案 A 单进程。

## 任务拆分（TDD）

- **T1 骨架与构建链**：`server/web/{package.json,tsconfig.json,vite.config.ts,
  index.html,src/main.tsx,src/App.tsx(占位),src/styles.css}`；managed node 的
  npm install；`npm run build` 通过。验收：dist 产出 index.html + assets。
- **T2 类型层 + API 客户端 + 纯函数库**：`src/api/types.ts`（契约面清单的
  TS 形状）、`src/api/client.ts`（fetch 封装 + multipart 组装 + SSE 订阅 +
  export 下载）、`src/lib/format.ts` + `format.test.ts`；探针：变异
  `fileIndexLabel` 的 base-26 偏移 → 红 → 还原 → 绿。
- **T3 App 壳 + 项目切换器**：`src/ui.tsx`（Toast/ProgressBar/Toolbar 原语）、
  `src/App.tsx`（顶栏 + 项目下拉 + 新建 + localStorage + 导航占位）；
  `renderToString` 冒烟测试。
- **T4 上传步**：`src/duplicate/UploadStep.tsx`（双行上传板 + 拖拽 +
  文件 pill + 会话模型 + 引导面板）；主容器 `DuplicatePage.tsx` 编排
  两步 + 工具条。
- **T5 分析步**：`src/duplicate/AnalysisStep.tsx`（四页签 + 四 Pane + 分页 +
  复制 + 进度）+ 自动启动 + SSE/轮询 + 重新查重。
- **T6 导出 + 静态挂载**：导出按钮接线（fetch→blob→download）；`main.py`
  `web_dist` 挂载 + `tests/test_static_frontend.py`（缺省不炸/存在可服务/
  API 优先）。
- **T7 收尾**：全量 pytest + vitest + build 冒烟；spec §15 P3d 行回填
  P3d-3 完成与执行期裁定；ledger + memory + 提交（仓库 git 身份，不推送）+
  双评审（A=参考页面行为对齐 R7；B=契约消费：multipart 形状/键形/SSE/下载）。

## 已知空白（登记）

- 废标项检查页、文档页、项目内导航完整性（P4 承接）。
- 任务取消按钮（参考页亦无，端点已在，P4 一起补）。
- 移动端/窄屏适配不做（本地工具，桌面宽度为准）。
- e2e 浏览器自动化冒烟（agent-browser）视 T7 时间机动，非门槛。
