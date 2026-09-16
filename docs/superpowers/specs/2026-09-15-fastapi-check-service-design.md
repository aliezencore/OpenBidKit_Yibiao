# 把标书查重与废标项检查抽为独立 FastAPI 服务 设计规格

- 日期：2026-09-15
- 关联规格：无。本次为**新建独立服务**，不改动 `client/` 现有实现。
- 工作分支：`fastapi-check-service`（worktree `.worktrees/fastapi-check-service`，从 `main` = `51900b1` 开出）
- 状态：已评审，待编制实施计划（拆分为多个计划，见 §15）

## 1. 背景与目标

现有两条检查链路（标书查重、废标项检查）实现在 Electron 客户端内：

| 归属    | Main 侧实现                                                                                                                                                                                                                                   | Renderer                       |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------ |
| 废标项检查 | `rejectionCheckTask.cjs`(1806) · `rejectionCheckStore.cjs`(1473) · `subjectCheckTask.cjs`(704) · `subjectVisionProvider.cjs`(436) · `bidAnalysisTask.cjs`(472) · `checkResultExportService.cjs`(547) · `docxVisualAssetExtractor.cjs`(260) | `RejectionCheckPage.tsx`(2806) |
| 标书查重  | `duplicateCheckService.cjs`(2870) · `duplicateCheckStore.cjs`(987)                                                                                                                                                                         | `DuplicateCheckPage.tsx`(1158) |

它们的运行离不开 Electron：`better-sqlite3` 是 Electron ABI 原生模块（普通 `node` 加载直接 `ERR_DLOPEN_FAILED`），印章图片转码靠 Electron 离屏 Chromium（`nativeImage` 只认 PNG/JPEG）。

目标：把这两条链路抽成一个**可独立部署、不依赖 Electron 的 FastAPI 服务**，自带 Web 界面，供内网少数人共用，并且**行为与现有实现保持一致**。

## 2. 范围

### 2.1 纳入

- 标书查重（`duplicate-check`）全流程。
- 废标项检查（`rejection-check`）全流程，含四个检查位：废标项、错别字、逻辑谬误、主体一致性（正文 + 印章视觉）。
- 无效与废标项解析（废标项检查的前置步骤）。
- Excel 结果导出。
- 从现有 `yibiao.sqlite` 导入历史数据的一次性脚本。

### 2.2 明确不纳入

| 项                   | 说明                                        |
| ------------------- | ----------------------------------------- |
| AI 评标               | 现有客户端中未实现（菜单项挂「开发中」提示），本次不设计。             |
| 多用户与鉴权              | 本次不做 `users` 表、登录、权限。隔离粒度止于「项目」（见 §5.1）。  |
| 现有 `client/` 的任何改动  | 本次不动 Electron 侧代码，两条链路在客户端继续可用。           |
| `.doc` 旧格式的本地正文提取   | 首版降级：仅走 MinerU 在线解析（见 §8.2）。              |
| 与 Electron 共享同一份数据库 | 新服务使用独立数据目录，不读写 `yibiao.sqlite`（迁移见 §12）。 |

## 3. 已确认决策

| #  | 决策     | 取值                                                         |
| -- | ------ | ---------------------------------------------------------- |
| D1 | 实现策略   | Python 全量重写。Prompt 资产、归一化与校验规则、相似度算法原样搬运；编排、存储、HTTP 层重新设计。 |
| D2 | 交付层次   | FastAPI 服务 + 自带 Web 界面，可脱离 Electron 独立运行。                  |
| D3 | 整体形态   | 方案 A：单体同进程（路由 + 静态前端 + 任务引擎 + SQLite 一个进程）。                |
| D4 | 部署与使用  | 内网少数人共用。                                                   |
| D5 | 持久化    | SQLite，WAL 模式。                                             |
| D6 | 印章视觉检查 | 纳入首版。Python 侧用 Pillow 重做格式转码。                              |
| D7 | 模型配置   | 服务端统一配置（共享 Base URL / Key / 模型名），不入库。                      |
| D8 | 隔离粒度   | 保留「项目」维度，不做登录。                                             |

## 4. 总体架构与模块边界

### 4.1 分层与依赖方向

```
api    →  domain  →  infra
```

`domain` 层**禁止** import FastAPI、SQLAlchemy、httpx、pydantic。领域函数只接受普通数据结构，外部依赖通过 `domain` 自己声明的 `Protocol` 注入。`infra` 实现这些 `Protocol`（依赖倒置）。

这条纪律有三个具体收益：

1. 13.8k 行逻辑能被纯单测覆盖（不起服务、不打真模型）。
2. 将来从方案 A 演进到方案 B（§6.5）只需替换 `infra/tasks/` 的实现，领域代码不动。
3. 查重与废标项检查虽同进程，但代码零耦合，将来拆服务不用重写。

现有实现已经具备这个形状——`runRejectionCheckTask({ aiService, workspaceStore, updateTask, checkpointTask, payload, previousState })` 就是依赖注入。搬运时必须保住。

### 4.2 目录结构

```
server/
  pyproject.toml
  app/
    main.py              # FastAPI 实例、路由挂载、静态前端、生命周期
    config.py            # pydantic-settings：模型配置、存储路径、并发上限
    api/
      deps.py            # 依赖注入：当前项目、仓储、任务引擎
      projects.py  documents.py  tasks.py
      rejection.py  duplicate.py  export.py  settings.py
    domain/
      shared/            # Findings 基类、输入签名、原文定位校验
      rejection/
        extraction.py    # 无效与废标项解析
        rejection.py  typo.py  logic.py  subject.py  seal.py
        prompts/         # 各检查项的 Prompt 常量
        state.py         # 滚动归并与补丁应用状态机
      duplicate/
        fingerprints/    # docx.py  ole.py  pdf.py  wps.py
        outline.py  content.py  images.py  naming.py
    infra/
      ai/                # 模型客户端、JSON 收集与修复、并发闸门、失败日志
      db/                # 引擎、schema、迁移
      repositories/      # 每个聚合一个仓储
      parsing/           # docx/pdf/xlsx → markdown；MinerU 客户端
      imaging/           # docx 图片提取、Pillow 格式转码、缩略图
      export/            # Excel 导出
      tasks/             # 任务引擎：状态机、进度、取消、checkpoint、线程池
    web/                 # 前端源码 + 构建产物
  tests/
```

## 5. 存储模型

### 5.1 隔离：引入 `projects`

现有 `rejection_check_*` 全部是单例 —— `id INTEGER PRIMARY KEY CHECK (id = 1)`；`rejection_check_tasks` 直接用 `type TEXT PRIMARY KEY`，即**每种任务全库只能存一条**。这是单工作区设计，两人共用会互相覆盖状态与结果，必须先改掉。

做法：

- 新增 `projects` 表。
- 所有业务表增加 `project_id`，把 `CHECK (id = 1)` 换成 `UNIQUE(project_id)`。需改的单例表：`rejection_check_meta`、`rejection_check_extraction`、`rejection_check_subject_profile`、`duplicate_check_meta`。
- **任务表不再保留原形态**：`rejection_check_tasks` 与 `duplicate_check_tasks` 都是 `type TEXT PRIMARY KEY`（每种任务每库一条），无法表达多项目与多次运行，统一收归 §6.1 的 `tasks` 表。
- 不做 `users` 表、不做登录。任何人可新建与切换项目，前端顶栏提供项目切换器。

### 5.2 表设计

**问句式设计保留原有的列形状，不改字段名**，以降低迁移成本与对照成本。

```sql
projects(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL)

rejection_check_documents(
  document_id TEXT, project_id TEXT, role TEXT, source TEXT, file_name TEXT,
  markdown_path TEXT, content_hash TEXT, content_chars INTEGER DEFAULT 0,
  parser_label TEXT, sort_order INTEGER DEFAULT 0, imported_at TEXT, updated_at TEXT,
  PRIMARY KEY (project_id, document_id))

rejection_check_extraction(
  project_id TEXT PK, status TEXT DEFAULT 'idle', content TEXT DEFAULT '',
  source TEXT, tender_signature TEXT, error TEXT, updated_at TEXT)

rejection_check_results(
  project_id TEXT, result_type TEXT, status TEXT DEFAULT 'idle',
  input_signature TEXT, active_finding_id TEXT, progress_message TEXT,
  error TEXT, stats_json TEXT, updated_at TEXT,
  PRIMARY KEY (project_id, result_type))

-- 四张 findings 表列形状互不相同，保持四张表，不合并成万能表
rejection_check_risk_findings(
  project_id TEXT, finding_id TEXT, bid_document_id TEXT, type TEXT, severity TEXT,
  title TEXT, summary TEXT, requirement TEXT, bid_evidence TEXT, risk_reason TEXT,
  suggestion TEXT, sort_order INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT,
  PRIMARY KEY (project_id, finding_id))

rejection_check_typo_findings(
  project_id TEXT, finding_id TEXT, bid_document_id TEXT, wrong_text TEXT,
  correct_text TEXT, original_excerpt TEXT, reason TEXT, location_hint TEXT,
  sort_order INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT,
  PRIMARY KEY (project_id, finding_id))

rejection_check_logic_findings(
  project_id TEXT, finding_id TEXT, bid_document_id TEXT, title TEXT,
  original_text TEXT, location_hint TEXT, fallacy_reason TEXT, suggestion TEXT,
  sort_order INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT,
  PRIMARY KEY (project_id, finding_id))

rejection_check_subject_profile(
  project_id TEXT PK, status TEXT DEFAULT 'idle', official_name TEXT DEFAULT '',
  aliases_json TEXT, candidates_json TEXT, bid_signature TEXT,
  confirmed_at TEXT, error TEXT, updated_at TEXT)

rejection_check_visual_assets(
  project_id TEXT, asset_id TEXT, bid_document_id TEXT, media_name TEXT,
  content_hash TEXT, relative_path TEXT, mime_type TEXT, width INTEGER, height INTEGER,
  placement TEXT DEFAULT 'unknown', asset_kind TEXT DEFAULT 'unknown',
  location_hint TEXT DEFAULT '', nearby_text TEXT DEFAULT '', section_title TEXT,
  sort_order INTEGER DEFAULT 0, created_at TEXT,
  PRIMARY KEY (project_id, asset_id))

rejection_check_subject_findings(
  project_id TEXT, finding_id TEXT, bid_document_id TEXT, level TEXT, source TEXT,
  expected_subject TEXT, detected_subject TEXT, location_kind TEXT, title TEXT,
  summary TEXT, location_hint TEXT, original_text TEXT, role_reason TEXT,
  mismatch_reason TEXT, suggestion TEXT, confidence REAL DEFAULT 0,
  visual_asset_id TEXT, asset_relative_path TEXT, seal_crop_relative_path TEXT,
  sort_order INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT,
  PRIMARY KEY (project_id, finding_id))

-- 查重侧沿用既有 duplicate_check_* 表，同样加 project_id
duplicate_check_meta / _files / _tasks / _analysis_sections / _content_files
  / _metadata_items / _outline_items / _outline_groups / _outline_pairwise
  / _content_duplicates / _content_occurrences / _image_files / _image_occurrences
  / _duplicate_images
```

**保留「结构化列 + JSON 扩展」的混合形态**（`stats_json`、`aliases_json`、`candidates_json`、`check_options_json`），不强行全字段化。

### 5.3 内容与资产的存放

沿用现有做法：正文 markdown 与图片二进制落磁盘，库里只存路径与 `content_hash`。多项目下路径必须带 `project_id` 前缀：

```
<data_root>/projects/<project_id>/documents/<document_id>.md
<data_root>/projects/<project_id>/assets/<content_hash>.<ext>
```

## 6. 任务引擎

### 6.1 `tasks` 表（方案 A→B 的唯一接缝）

```sql
tasks(
  task_id TEXT PK,
  project_id TEXT NOT NULL REFERENCES projects(id),
  domain TEXT NOT NULL,        -- 'rejection' | 'duplicate'
  kind TEXT NOT NULL,          -- 'rejection-items-extraction' | 'bid-subject-identification'
                               -- | 'rejection-check-run' | 'duplicate-analysis'
  status TEXT NOT NULL,        -- queued | running | success | error | cancelled | interrupted
  progress INTEGER NOT NULL DEFAULT 0,
  stage_label TEXT,
  payload_json TEXT,           -- 入参快照，形状即现有 payload
  checkpoint_json TEXT,        -- 滚动状态，断点续跑用
  stats_json TEXT,
  error TEXT,
  claimed_by TEXT,             -- 方案 A 恒为 'inline'；方案 B 为 worker 标识
  heartbeat_at TEXT,           -- 方案 A 不写；方案 B 用于僵尸回收
  created_at TEXT, started_at TEXT, finished_at TEXT)
```

`claimed_by` 与 `heartbeat_at` **现在就留字段但方案 A 不写值**。将来切方案 B 只需新增 worker 入口，表结构不动。

**任务粒度**：一次「开始检查」= **一条** `rejection-check-run` 任务，四个检查位是该任务内部的并行子步骤（见 §9.1），**不是四条任务**。单点重试（现有界面的 `retrySingleCheck`）在服务端表达为「新建一条 `rejection-check-run`，`payload` 只启用目标检查位」——与现有实现传入单个 `checkOptions` 布尔位的做法等价。

沿用现有 `task_logs(task_domain, task_type, task_id, message, created_at)` 形状，增加 `project_id`。

### 6.2 领域层看到的注入面

```python
class TaskContext(Protocol):
    def progress(self, percent: int, label: str) -> None: ...
    def log(self, message: str) -> None: ...
    def checkpoint(self, state: dict) -> None: ...
    def raise_if_cancelled(self) -> None: ...

# 领域函数签名
# run(ctx, ai, store, payload, previous_state)
# 对应现有 runRejectionCheckTask({ aiService, workspaceStore, updateTask,
#                                 checkpointTask, payload, previousState })
```

这个一一对应关系是保真度的关键，搬运时逐个函数对齐。

### 6.3 执行模型

- `asyncio` 编排（AI 请求是 IO 密集）。
- CPU 密集段（查重指纹提取、相似度计算、docx 解析）走 `ThreadPoolExecutor`。
- 并发闸门：**每项目同时最多 1 个写任务**，全局最多 4 个。目的是避免同一项目两次检查互相踩状态（现有实现靠用户操作顺序与签名校验间接规避，服务端必须显式加锁）。

### 6.4 进度、取消、断点续跑

- 进度写库做节流：整数百分比变化，或距上次写入 ≥200ms，才落盘。
- 实时推送走 `GET /tasks/{id}/events`（SSE）。SSE 走进程内事件总线，不查库。
- 取消为协作式：每个 AI 调用前检查 `ctx.raise_if_cancelled()`，抛 `asyncio.CancelledError`。
- 断点续跑：`checkpoint_json` 保存滚动状态；进程重启时把 `running` 的任务标记为 `interrupted`，界面提供「继续」。

### 6.5 演进到方案 B 的路径（本次不实现）

方案 B 为「API 与 Worker 双进程」：API 只做 HTTP / 上传 / 鉴权，Worker 从 `tasks` 表按 `status='queued'` 领取任务，写 `claimed_by` 与 `heartbeat_at`；Worker 崩溃后由超时心跳触发僵尸回收重新入队。

因为 `tasks` 表字段已按 B 预留，切换时**只需新增 worker 入口并把 TaskRunner 换成队列实现**，`domain`、`infra/ai`、`infra/db`、`infra/parsing`、`infra/imaging`、`infra/export` 与全部 HTTP 接口均不变。

触发升级的信号：有人跑任务时其他人明显被卡住；单次检查平均耗时升到十几分钟以上；需要跨机器分摊。


## 7. AI 层

### 7.1 客户端

出口是标准 OpenAI 兼容 `POST {base_url}/chat/completions` + `Authorization: Bearer {api_key}`。用 `httpx.AsyncClient`（连接池复用）。**不引 `openai` SDK** —— 现有实现是裸 `fetch`，引 SDK 会引入行为差异。

必须逐条保留的现有行为：

| 行为         | 原实现                                                | 要求                                    |
| ---------- | -------------------------------------------------- | ------------------------------------- |
| JSON 收集与修复 | `aiService.collectJsonResponse` / `requestJson`    | 发请求 → 抽 JSON → 失败修复重试，重试策略照抄          |
| 多模态        | 图片以 data URL 塞进 message content                    | 同                                     |
| 配置分离       | 文本模型与视觉模型**各自**有 base_url / api_key / model_name   | 同                                     |
| 上下文长度      | `getConfig()` 提供上限，`userTextSplitter` 依赖它切分        | 同                                     |
| 失败日志       | `utils/aiLog.cjs` 的 `logAiFailure(scope, payload)` | **始终打印**的单行 `key=value`，不受 debug 开关控制 |

**结构化输出继续靠 Prompt 约束 + JSON 抽取修复，不换 function calling / `response_format`。** 换机制意味着全部 Prompt 需要重调，风险不可接受。

### 7.2 配置与并发

`config.py` 用 `pydantic-settings` 从环境变量读取（文本模型与视觉模型各一组），启动时加载，**不入库**（D7）。

全局 `asyncio.Semaphore` 卡模型并发，上限可配，默认 8，用于保护上游限流。

## 8. 文档解析与图片管线

### 8.1 解析选型（2026-09-16 修订，用户已确认「Python 原生等价物」）

**现状事实**（2026-09-16 实读 `doc2markdown/convert.mjs`，1633 行）：现有本地解析是一条**库链**，不是手写解析器——
docx → **mammoth**（HTML）→ 表格用 **cheerio** 抽出、GFM 渲染、占位符回填 → **turndown + GFM 插件**（HTML→markdown）→ normalize；
PDF 走 **pdf-parse + pdfjs-dist**（含约 150 行自定义的矩形表格抽取）；
xlsx 走 **SheetJS**；编码探测 chardet + iconv-lite；旧 `.doc` 由 **LibreOffice 转 docx** 后走 docx 路径。

**决定：采用 Python 原生等价物，验收标准为「结构等价」而非逐字节一致。** 理由：mammoth / turndown / pdfjs 的表格算法均无精确 Python 对应，逐字节对齐不可达；而服务的目标是检查可用，不是与 Electron 逐字节一致。

| 扩展名            | Python 方案                                                              |
| -------------- | ---------------------------------------------------------------------- |
| `.docx`        | python-mammoth（docx→HTML）+ 表格抽取（lxml/bs4）+ markdownify（HTML→markdown）+ 归一化 |
| `.pdf`         | PyMuPDF（文本 + `find_tables`）                                            |
| `.xlsx`        | openpyxl                                                               |
| `.txt` / `.md` | 直读 + charset-normalizer 编码探测                                           |
| `.doc`         | 见 §8.2                                                                 |
| 其他             | 报错并列出支持的扩展名                                            |

**代价（必须显式承担）**：markdown 输出与 Electron 版**必然不同**——查重的正文切句、目录抽取结果会与旧客户端漂移。因此：

- P3 的查重阈值与切句口径**必须用新管线的输出重新校准**，不得直接沿用旧客户端的调参结果；
- §11.2 的金标准对照仅适用于其明确列出的四类（Prompt 构造、归一化/去重、指纹口径、聚合口径），**不包含解析器的 markdown 输出**。

MinerU 在线解析沿用现有两条 API（agent / accurate），用 `httpx` 重做，token 走配置。解析路由按扩展名 + 配置里的 provider 选择器决定，与现在 `resolveFileParser` 同构，**含「请求的 provider 不支持该扩展名 → 回退本地」语义**。

### 8.2 `.doc`（2026-09-16 修订，原「降级」决定作废）

原 §8.2 说 `.doc` 首版降级、正文走 MinerU——**该决定基于不完整的调研，作废**。现有客户端对 `.doc` 的本地支持就是 LibreOffice 转 docx（`withLegacyWordDocxFile` + `runLibreOfficeDocxConversion`），Python 侧同样调用 `soffice --convert-to docx` 后走 docx 路径。LibreOffice 不可用时给出明确错误，对齐现有 `LIBREOFFICE_REQUIRED_MESSAGE` 的语义。

### 8.3 图片管线（印章检查用）

1. **提取**：`zipfile` 读 `word/media/*`；解析 `document.xml` 的 `r:embed` / `r:link` 关系拿放置位置。
2. **上下文**：**按段落索引**计算 `section_title` / `nearby_text`，对齐现有 `docxVisualAssetExtractor`。章节优先级：标题样式 / 中文编号模式 → 加粗短段 → `None`，**不加**「上一个非空段落」兜底。
   绝不退回按字符偏移切 XML —— 旧实现那版产出的「附近文本」是 XML 碎片，还被当作位置上下文喂给模型。
3. **格式决策与转码**：沿用现有 `resolveImagePlan` 的四分动作（`compress` / `passthrough` / `transcode` / `skip`）与全部跳过码（`UNSUPPORTED_IMAGE_FORMAT`、`PASSTHROUGH_TOO_LARGE`），以及 `computeTargetSize` 的等比缩放语义。**理由**：印章 VLM 的输入形态要对齐——GIF 原字节透传、BMP/WebP 转码是现有实现刻意的取舍，不是 Electron 的缺陷；Python 侧虽然 Pillow 也能解 GIF/BMP/WebP，仍保留同一动作分类，只把「transcode」的实现从离屏 Chromium 换成 Pillow，「compress」的实现换成 Pillow 压缩。`skipped` 的码与「未能识别的图片」清单直接依赖这些码，改动会造成 P4 行为漂移。

### 8.4 必须保留的三条既有约定

1. **按 `content_hash` 去重**，同一 hash 的所有出现位置**共享**跳过 / 失败结果（`applyContentOutcomeToSiblings` 语义）。
2. **`skipped` 两分**（`skipKind`）：`unsupported-format`（格式解不了，**要进**「未能识别的图片」清单）vs `not-seal-area`（已识别但判定为产品截图 / 拓扑图 / 照片，**不进**清单）。`stats.skippedAssets` 两种都计。
3. **单张失败不连坐**：上传前预检（不可读 → `skipped` 且不进批次）；批次失败**降级为逐张重试**。

### 8.5 解析一致性验收（2026-09-16 新增）

解析输出的验收是**结构等价**，用真实 docx/pdf/xlsx 样本对照：

- 标题层级序列一致（层级深度可不同，相对结构须一致）；
- 表格数量与行列数一致；
- 段落数量在同一量级（±10%），无 XML 碎片、无 base64 泄漏进正文；
- 图片占位数量与位置一致；
- 编码探测对 GBK/UTF-8/BOM 样本均正确。

不要求逐字节一致；LLM 提示词对 markdown 风格不敏感，但**不得**出现解析伪影。

## 9. 流程对照（搬运基准）

本节是 §4-§8 之外最关键的约束：**流程骨架必须逐步对齐**，不是只搬函数。

### 9.1 废标项检查

**页面三步**：`documents` → `items` → `results`（类型 `RejectionCheckStep`）。

| # | 步骤       | 原实现入口                                                                                                            | 输入                                        | 产出                                                       | 前置约束                                          |
| - | -------- | ---------------------------------------------------------------------------------------------------------------- | ----------------------------------------- | -------------------------------------------------------- | --------------------------------------------- |
| 1 | 导入文档     | IPC `rejection-check:import-document`、`rejection-check:import-tender-from-technical-plan`                        | `role`（tender/bid）+ 文件路径                  | markdown 落盘、`rejection_check_documents` 行、`content_hash` | —                                             |
| 2 | 无效与废标项解析 | 任务 `rejection-items-extraction` → `runRejectionItemsExtractionTask` → `runInvalidBidAndRejectionItemsExtraction` | **招标文件正文**                                | `rejection_check_extraction.content`、`tender_signature`  | 招标文件非空，否则抛「缺少招标文件内容，无法解析无效与废标项」               |
| 3 | 投标主体识别   | 任务 `bid-subject-identification` → `runBidSubjectIdentificationTask`                                              | 投标文件（默认全部；可按 `payload.bidDocumentIds` 收窄） | `rejection_check_subject_profile` 候选                     | —                                             |
| 4 | 主体确认     | IPC `rejection-check:confirm-subject`                                                                            | `officialName`、`aliases`                  | `status='confirmed'`、`bid_signature`                     | —                                             |
| 5 | 执行检查     | 任务 `rejection-check-run` → `runRejectionCheckTask`                                                               | 见下表                                       | 四个结果                                                     | 至少启用一种检查；废标项检查需**步骤 2 已完成**；主体检查需**步骤 4 已确认** |

步骤 2 的细节：`bidAnalysisTask` 对超长招标文件先 `splitUserTextByContextLimit` 分段，再 `mergeSegmentedAiResults` 合并。**分段与合并逻辑必须原样搬**，否则超长招标文件的解析结果会与现状不一致。

步骤 5 内部：**四个检查并行**（`Promise.all`），进度 `min(95, 5 + completed/enabledTasks × 90)`。

| 检查    | 原实现入口                                                   | 输入                                                              | 输出键                    | 签名口径                                            |
| ----- | ------------------------------------------------------- | --------------------------------------------------------------- | ---------------------- | ----------------------------------------------- |
| 废标项   | `runRejectionItemCheck`                                 | `invalidBidAndRejectionItems` + `customCheckItems` + **全部投标文件** | `rejectionCheckResult` | `rejectionInputSignature`（含检查项内容）               |
| 错别字   | `runTypoCheck`                                          | **全部投标文件**                                                      | `typoCheckResult`      | `bidSignature`                                  |
| 逻辑谬误  | `runLogicCheck`                                         | **全部投标文件**                                                      | `logicCheckResult`     | `bidSignature`                                  |
| 主体一致性 | `runSubjectBodyCheck` → `runSubjectSealCheck`（**内部串行**） | **全部投标文件** + `subjectProfile` + `visualAssets`                  | `subjectCheckResult`   | `subjectInputSignature`（依赖 profile 已 confirmed） |

三条易漏约束：

1. **主体检查内部是串行的**：先正文、再印章，最后 `findings = [...bodyFindings, ...sealResult.findings]`。
2. **印章失败不连坐**：`runSubjectSealCheck` 抛错时只置 `visionStatus='error'`，**正文 findings 保留**。
3. `subjectProfile` 未 confirmed 时**生成不出** `subjectInputSignature`，主体检查不能启动 —— 这是硬前置，不是可选。

落盘：进度走轻量 `updateTask`；结果变化与终态经 `workspaceStore.saveCheckResult`。

### 9.2 标书查重

**页面两步**：`upload` → `analysis`。

| # | 步骤 | 原实现入口                                                             | 说明                                           |
| - | -- | ----------------------------------------------------------------- | -------------------------------------------- |
| 1 | 上传 | IPC `duplicate-check:save-files`                                  | 招标文件与投标文件按 `role` 存入 `duplicate_check_files` |
| 2 | 分析 | 任务 `duplicate-analysis` → `duplicateCheckService.runAnalysisTask` | 见下                                           |

阶段表 —— **四处文件参与范围的非对称是搬运最大风险点**：

| 阶段                                 | 参与文件                       | 产出表                                                                        | 依赖           |
| ---------------------------------- | -------------------------- | -------------------------------------------------------------------------- | ------------ |
| 元数据提取 `runMetadataExtraction`      | **仅 bidFiles**             | `duplicate_check_metadata_items`                                           | —            |
| 正文提取 `runContentExtraction`        | **allFiles（含招标）**          | `duplicate_check_content_files`                                            | —            |
| 目录分析 `runOutlineAnalysis`          | **tenderFiles + bidFiles** | `duplicate_check_outline_items` / `_groups` / `_outline_pairwise`          | contentFiles |
| 正文比对 `runContentDuplicateAnalysis` | **tenderFiles + bidFiles** | `duplicate_check_content_duplicates` / `_content_occurrences`              | contentFiles |
| 图片比对 `runImageDuplicateAnalysis`   | **仅 bidFiles**             | `duplicate_check_image_files` / `_image_occurrences` / `_duplicate_images` | contentFiles |

编排（`run()`，逐行对照）：

1. **幂等**：四段 `signature` 全部 `success` 且非 `force` → 直接 `status='success'`、`progress=100`，日志「标书查重分析已完成，无需重复分析。」并返回。
2. `contentPromise = runContentExtraction(allFiles)` **先启动不等待**；`metadataFiles = await runMetadataExtraction(bidFiles)` —— 两者**并行**。
3. 元数据提取完成后，把目录 / 正文 / 图片三段都置 `running`，进度 1%，消息「元数据提取完成，等待正文内容用于…」。
4. `contentFiles = await contentPromise`；`metadataFailed = contentFiles 有 error || metadataFiles 有 error`。
5. `Promise.all([runOutlineAnalysis, runContentDuplicateAnalysis, runImageDuplicateAnalysis])` —— **三段并行**。
6. `failed = metadataFailed || outlineFiles 有 error || contentResult.status==='error' || imageResult.status==='error'`；**部分失败即整体 `error`，但已产出的结果仍保留**。
7. 签名校验：完成后若 `isCurrentDuplicateCheckSignature(signature)` 为假（用户已改动文件），丢弃结果不落库。

其它必须保留的细节：

- **总进度 = 四段进度的等权平均**（`overallProgress`，不是加权）。
- 落盘策略：`checkpointTask` 只在「结果字段变化或终态」时使用，其余走轻量 `updateTask`。
- 消息优先级：`imageAnalysis → contentAnalysis → outlineAnalysis → metadataAnalysis → 默认「标书查重分析运行中。」`。

### 9.3 Excel 导出的保真要求

`checkResultExportService` 的产物结构必须对齐：

- **废标项检查**导 5 个工作表：`检查概览`、`废标项`、`错别字`、`逻辑问题`、`投标主体`。
- **查重**导 6 个工作表：`查重概览`、`元数据`、`目录重复`、`文件相似度`、`重复句子`、`重复图片`。
- 「投标主体」表末尾追加「**未能识别的图片**」明细区块，用于解释「跳过 N 张 / 失败 N 张」到底卡在哪几张图上；**无条目时不追加该区块**。
- 追加区块后必须把该表的 autofilter **收缩到「表头 + findings 行」**。否则筛选会把说明性区块当数据行一起筛掉（这是原实现修过的 bug，见 `checkResultExportService.cjs:322-330`）。
- 各表表头文案与列宽照抄现有实现（列宽数组已在原实现中固定，逐表不同）。

## 10. 前端

- 技术栈：Vite + React + TypeScript（与现有 Renderer 同栈，沿用全局 CSS + Radix 的写法），**不复用现有组件**（本次不动 `client/`）。
- 页面：
  - 项目列表 / 新建 / 切换（顶栏切换器）。
  - 项目内「文档」页：导入招标文件与投标文件，展示解析状态与解析器标签。
  - 「废标项检查」页：对齐现有三步（文档 → 检查项 → 结果），结果区四个页签（废标项 / 错别字 / 逻辑谬误 / 主体一致性），支持按投标文件筛选、主体确认弹窗、Excel 导出、重试单个检查。
  - 「标书查重」页：对齐现有两步（上传 → 分析），展示四段进度、目录相似度、正文重复、图片重复与元数据指纹。
- 数据获取：REST + SSE（任务进度与日志）。不引状态管理库。

## 11. 测试与保真策略

「证明没漂移」是本次最大风险，策略分三层：

1. **翻译现有单测**，逐个改为 pytest：  
   `subjectCheckTask.test.cjs`、`subjectMediaFiles.test.cjs`、`docxVisualAssetExtractor.test.cjs`、`checkResultExportService.test.cjs`、`rejectionCheckStore.subject.test.cjs`、`taskService.confirmBidSubject.test.cjs`、`localImageRenderService.test.cjs`（仅取其中的图片格式决策部分）。
2. **金标准对照（主力手段）**：从现有实现导出固定「输入 → 输出」样本对，新实现必须逐字符对上。重点覆盖：
   - **Prompt 构造结果**（拼接顺序、换行、分隔符，逐字节对齐）；
   - 归一化与去重结果（findings 归一化、滚动状态补丁应用、错别字原文定位校验）；
   - 查重指纹输出字段名与口径；
   - `overallProgress` 等聚合口径。
3. **AI 层用假 provider**：domain 测试注入 fake `ai` 与 fake `TaskContext`，不打真模型、不联网。

**不做的**：不复刻 Electron `Store` 的行为测试（那些测的是实现细节，新实现不再有同样的 Store）。

## 12. 迁移导入脚本

一次性脚本，把现有 `yibiao.sqlite` 的 `rejection_check_*` 与 `duplicate_check_*` 读出来，落成一个默认 `project`，并复制对应的 markdown 与图片资产到新数据目录。

做这个的理由很实际：**没有它就无法用现有项目数据做 §11.2 的金标准对照**。

## 13. 风险与缓解

| #  | 风险                                                                                                                           | 缓解                                                |
| -- | ---------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| R1 | `ole.py` 的手写 CFB / OLE 属性集字节级解析（`parsePropertySet`、`parseTypedPropertyValue`、`parseLpstr`、`codePageToEncoding` 等）最易出**静默偏差** | **最先搬运并配足单测**；`olefile` 只用于「取流」，语义解析照搬；用现有文件做输出对照 |
| R2 | Prompt 在 Python 中的拼接顺序与换行必须**逐字节**对齐，否则模型输出漂移                                                                                | 把 Prompt 作为常量整体搬运；用金标准对照逐字符校验                     |
| R3 | Pillow 的压缩参数与 Electron 侧不完全等价，印章 VLM 判定可能有细微差异                                                                               | 用真实图片（含 GIF / BMP / WebP）回归，对照 `visionStats`      |
| R4 | 查重的相似度阈值与目录归一化规则是**调参调出来的**                                                                                                  | 原样搬，不「顺手优化」                                       |
| R5 | `.doc` 正文首版降级                                                                                                                | 在界面明确提示不支持，不静默失败                                  |
| R6 | 检查项**内部**算法（滚动归并状态机、补丁应用）细节尚未逐行核对                                                                                            | 不靠读代码保证，靠 §11.2 金标准对照兜底                           |
| R7 | 前端两个页面（2806 / 1158 行）的完整交互流尚未逐行核对                                                                                            | 按 §10 的页面清单对齐，实现期以现有页面行为为准逐步核对                    |

## 14. 交付边界声明

- 本规格**只覆盖流程骨架与架构约束**，不包含检查项内部逐行算法说明；后者的保真由 §11.2 的金标准对照承担。
- 检查项的 Prompt 文本不在本规格内复制，实现时从现有 `.cjs` 原样搬运，一字不改。
- 本规格落地为实施计划后，才进入编码；实施计划由 `writing-plans` 产出。

## 15. 实施计划拆分

本规格覆盖多个可独立交付的子系统。按「每个计划产出可运行、可测试的软件」拆成 4 个计划，按序实施：

| # | 计划 | 产出（可独立验证） | 依赖 |
|---|---|---|---|
| P1 | 平台底座 | `uvicorn` 起得来；`/health` 可用；schema 可幂等迁移；任务引擎能提交 / 取消 / 推进度 / SSE；AI 客户端能跑通 `collect_json_response` 的成功与修复路径；「domain 层不 import 框架」的护栏测试生效 | — |
| P2 | 文档解析与图片管线 | 给定 docx / pdf / xlsx 能产出 markdown 与视觉资产（含章节上下文、去重、格式转码） | P1 |
| P3 | 标书查重领域 + 查重闭环 | 上传招标 / 投标文件 → 跑查重 → 看结果 → 导 Excel（**零模型依赖**，可端到端验证） | P1、P2 |
| P4 | 废标项检查领域 + 检查闭环 | 跑四项检查 → 结果页签 → 导 Excel；含印章视觉检查与 `yibiao.sqlite` 迁移导入 | P1、P2、P3（复用 API 与前端骨架） |

§11.2 的金标准对照工具在 P2 起随各计划就地搭建，不单独成计划。
