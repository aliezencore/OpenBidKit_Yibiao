# 投标主体一致性检查设计

## 1. 背景

易标投标工具箱现有“废标项检查”会导入一份或多份招标文件、投标文件，并并行执行废标项、错别字和逻辑谬误检查。本设计在同一功能中增加第四类“主体一致性检查”：系统从当前投标文件自动识别投标主体候选，用户确认正式主体和允许简称后，检查整份投标文件中所有语义上代表投标方发言、承诺、声明、签署、落款或盖章的位置，列出由其他主体错误替代投标方的内容。

主体不是固定公司。每次检查都以当前投标包自动识别并经用户确认的主体为准，产品代码、提示词和测试不得硬编码任何真实公司名称。

## 2. 目标

1. 从当前投标文件自动提取投标主体候选及证据，并要求用户确认或修改。
2. 自动生成允许简称，让用户确认、增加或删除。
3. 检查全部正文、表格、函件、声明、承诺、签署和落款区域，而不是只匹配预定义的“投标人”字段。
4. 区分投标方身份与招标人、代理机构、客户、制造商、认证机构、案例单位等合法第三方角色。
5. 对 DOCX 中独立嵌入图片和整页扫描图片进行可选的在线 VLM 印章检查。
6. 将问题分为“明确不一致”和“需人工复核”，并保留可定位、可解释的证据。
7. 复用现有后台任务、SQLite 持久化、结果页签、筛选、删除和 Excel 导出体系。
8. 为未来替换视觉实现保留稳定的 `SubjectVisionProvider` 边界，但本版本不实现本地或服务端 OCR/YOLO。

## 3. 非目标

本版本不实现：

- YOLO 印章检测模型；
- PaddleOCR 或其他本地 OCR 运行时；
- 自建视觉服务器、服务端接口或部署脚本；
- 精确 Word 页码；
- 页面坐标的交互式框选编辑；
- 联合体投标或多个合法投标主体；
- 母子公司、总分公司自动等同；
- 自动修改 DOCX、替换公司名称或替换印章；
- 对所有第三方公司名称和第三方印章报警；
- 将用户提供的真实投标文件作为仓库测试夹具。

首版只支持单一投标主体。遇到联合体或需要多个合法主体共同作为投标方的项目时，页面必须提示当前能力不适用，不能通过自动加入白名单弱化这一限制。

## 4. 已确认的产品决策

- 主体来源：系统自动识别候选，用户确认或修改后才能检查。
- 主体数量：只支持单一投标主体。
- 检查范围：覆盖所有语义上代表投标方的区域，不限于固定字段。
- 简称策略：自动生成允许简称并交给用户确认。
- 印章策略：只检查语义上应由投标方盖章的位置；合法第三方印章不报警。
- 印章可用性：作为可选增强，失败不阻塞正文主体检查。
- 结果分级：明确不一致、需人工复核。
- 功能入口：作为现有废标项检查的第四个结果页签。
- 定位方式：章节、表格、附近文字、原文和图片预览；不要求精确页码。
- 当前视觉实现：复用现有文本模型的在线多模态能力；YOLO 和 PaddleOCR 不在本版本范围。
- VLM 配置来源：复用设置页“文本模型”中的当前服务商、API Key、Base URL、模型名称和“支持多模态”开关；本功能不新增独立 VLM 配置。
- “生图模型”配置不参与主体印章检查。

## 5. 总体架构

```text
投标 DOCX 导入
├─ 现有 Markdown 文本解析
└─ DOCX Visual Asset Extractor
   ├─ word/media 图片
   ├─ DrawingML/VML 引用
   ├─ inline/anchor 放置方式
   └─ 段落、表格、章节和附近文字

主体候选识别
└─ 文本模型提取候选、简称和证据

用户确认
├─ 正式主体名称
├─ 允许简称
└─ 是否启用在线印章检查

rejection-check-run
├─ 废标项检查
├─ 错别字检查
├─ 逻辑谬误检查
└─ 主体一致性检查
   ├─ 正文主体检查
   └─ OnlineVlmProvider 印章检查（可选）

SQLite Store
├─ 主体确认状态
├─ 视觉材料清单
├─ 主体发现项
└─ 正文/视觉运行状态与覆盖统计
```

主体候选识别新增受管任务 `bid-subject-identification`，加入现有 `rejection-check` 组内互斥。正式主体检查不新增独立受管任务，而是作为现有 `rejection-check-run` 的第四个并行分支；单独重试时通过只启用 `subjectCheck` 的 `runOptions` 执行。

## 6. DOCX 视觉材料提取

### 6.1 必要性

当前 `fileService.importRejectionCheckDocument()` 使用 `preserveImages: false`，导入结果只有 Markdown。主体印章检查不能依赖这份纯文本结果，必须在导入 DOCX 时增加独立的 Open XML 媒体提取。

### 6.2 提取范围

提取器读取：

- `word/document.xml`；
- `word/_rels/document.xml.rels`；
- `word/header*.xml` 及对应 relationships；
- `word/footer*.xml` 及对应 relationships；
- `word/media/*`。

至少支持：

- DrawingML `a:blip`；
- `wp:inline` 行内图片；
- `wp:anchor` 浮动图片；
- VML `v:imagedata`；
- 普通段落、表格、页眉和页脚中的图片。

### 6.3 持久化

媒体文件保存到工作区废标检查目录的投标文件专属子目录，SQLite 只保存相对路径和元数据，不保存二进制。导入服务可以使用临时导入标识提取媒体；Store 生成最终 `bidDocumentId` 后，将媒体移动到对应目录并在同一业务提交中写入清单。提交失败必须回收临时文件。

删除投标文件、清空废标检查工作区或导入重复文件时，必须同步处理媒体目录，不能留下孤立图片。

### 6.4 视觉材料模型

```ts
interface SubjectVisualAsset {
  id: string;
  bidDocumentId: string;
  mediaName: string;
  contentHash: string;
  relativePath: string;
  mimeType: string;
  width: number;
  height: number;
  placement: 'inline' | 'anchor';
  assetKind: 'standalone' | 'page-scan' | 'unknown';
  locationHint: string;
  nearbyText: string;
  sectionTitle?: string;
  relationshipId?: string;
}
```

`assetKind` 只用于决定视觉请求策略，不能直接作为主体或印章结论。分类可使用图片尺寸、显示范围、长宽比、是否独占段落、放置方式和附近章节等结构特征。

## 7. 主体候选识别与确认

### 7.1 候选来源

文本模型扫描当前全部投标文件，重点使用但不限于：

- 封面和文档标题；
- 投标函、响应函；
- 投标人、应答人、供应商字段；
- 报价表；
- 授权委托书；
- 法定代表人声明；
- 投标方承诺函；
- 签字盖章附近文字；
- 高频“我公司”“本公司”对应的具名主体。

输出必须包含 `bidDocumentId`、候选名称、置信度、位置、原文和角色依据。

### 7.2 自动选择

只有同时满足以下条件时才预选第一候选：

1. 第一候选置信度不低于 `0.85`；
2. 第一、第二候选置信度差值不低于 `0.10`；
3. 至少存在一条明确的投标身份原文。

否则 Dialog 展示候选但不预选。任何情况下都必须由用户确认。

### 7.3 主体模型

```ts
interface BidSubjectProfile {
  status: 'idle' | 'identifying' | 'pending-confirmation' | 'confirmed' | 'error';
  officialName: string;
  aliases: string[];
  semanticPronouns: string[];
  candidates: BidSubjectCandidate[];
  bidSignature: string;
  confirmedAt?: string;
  error?: string;
}
```

固定语义代称至少包括：我公司、本公司、我方、本投标人、投标人、应答人、供应商、响应人。语义代称只帮助模型理解发言角色，不作为公司名称参与字符串比较。

### 7.4 名称标准化

允许执行：

- 去除首尾空白；
- 合并异常空格和换行；
- 统一全角、半角括号；
- 统一常见中文标点；
- 英文字母大小写不敏感。

禁止自动执行：

- 去除“有限公司”或“有限责任公司”；
- 将母公司、子公司、总公司、分公司视为同一主体；
- 使用编辑距离将相似公司名直接判为一致；
- 将未经用户确认的简称加入允许列表。

相似但未确认的名称不能自动通过，至少进入“需人工复核”。

## 8. 正文主体检查

### 8.1 检查范围

检查整份投标文件中所有可能代表投标方身份的语义区域，包括正文、表格、函件、声明、承诺、报价、资格陈述、授权材料、签署和落款。功能不能退化为仅搜索固定字段。

### 8.2 角色优先

模型先提取主体出现项并判断角色：

```text
bidder       当前上下文代表投标方
third-party  招标人、代理机构、客户、制造商、认证机构等第三方
unclear      无法确定
```

Main 再执行名称比较：

- `third-party`：不生成结果；
- `bidder` 且命中正式名称或允许简称：通过；
- `bidder` 且明确为其他主体：按证据门槛生成明确不一致或需人工复核；
- `unclear` 且出现其他公司名称：生成需人工复核；
- 只有“我公司”等代称且没有错误具名主体：不生成异常。

### 8.3 长文处理

主体正文检查复用上下文切分能力。每段携带当前文件、章节、上级章节、表格/正文类型及前后必要上下文。程序维护已发现主体和角色摘要，最后对候选项分批定稿并全局去重。

不得因为当前片段没有解释，就把第三方主体直接定为投标主体错误。

### 8.4 防幻觉校验

每条文本结果必须通过：

1. `bidDocumentId` 属于当前输入；
2. `originalText` 能在对应原文中定位；
3. `detectedSubject` 确实出现在原文摘录中；
4. 正式主体和允许简称没有被列为异常；
5. 相同文件、位置和主体的结果完成去重。

找不到原文证据的模型结果直接丢弃，不能降级后进入人工复核。

## 9. 在线 VLM 印章检查

### 9.1 检查原则

只检查语义上应由投标方盖章的位置。制造商授权函、第三方售后承诺、认证证书、客户证明等材料中的合法第三方印章不报警。

视觉检查是可选增强。未开启多模态、视觉请求失败或图片无法识别时，正文主体检查仍可成功。

### 9.2 当前流程

当前项目已经具备文本模型多模态能力，但没有独立的 VLM 配置页或 OCR 配置页。设置页的“文本模型”配置包含 `multimodal_enabled`；用户可以手动打开“支持多模态”，也可以通过“自动填充高级参数”根据模型信息中的 `imageInputStatus` 自动设置。底层 `aiService` 会在未开启该开关时拦截图片请求。

主体检查复用当前已配置的文本模型：

```text
读取当前文本模型配置
  ↓
确认 API Key、Base URL、模型名称和 multimodal_enabled
  ↓
DOCX 结构上下文筛选
  ↓
独立图片或整页扫描图
  ↓
OnlineVlmProvider（封装现有 aiService）
  ↓
印章发现、文字识别、页面角色和盖章方角色判断
  ↓
Main 名称匹配与结果分级
```

如果当前文本模型没有开启多模态，主体正文检查仍可运行，但印章检查显示“当前文本模型未开启多模态支持”，不将生图模型当作替代品。

当前版本不调用 YOLO、PaddleOCR 或其他专用 OCR。VLM 可返回印章的可选归一化框选坐标，但 UI 不提供人工编辑坐标能力。

### 9.3 效率控制

- 图片内容识别按 `contentHash` 缓存；
- 位置角色判断按 `contentHash + nearbyContextHash` 缓存；
- 同一图片内容只上传识别一次，不同出现位置仍分别判断角色；
- 独立小图每批最多 4 张；
- 整页扫描图每次只发送 1 张；
- 沿用 `aiService` 的队列、取消、重试、日志、统计和最长边 `2048px` 压缩；
- 明确为普通产品截图、拓扑图或照片的图片可以跳过；
- 明确的第三方认证材料可以跳过，但必须记录跳过原因；
- 高相关及角色未知的候选不能仅因数量较多而静默丢弃。

### 9.4 Provider 边界

```ts
interface SubjectVisionProvider {
  getCapabilities(): {
    sealDetection: boolean;
    sealOcr: boolean;
    roleClassification: boolean;
    boundingBoxes: boolean;
  };
  inspect(request: SubjectVisionRequest): Promise<SubjectVisionResponse>;
}
```

请求包含协议版本、正式主体、允许简称以及资产 ID、文件 ID、图片类型、Main 侧本地路径、章节和附近文字。响应按资产返回是否有印章、识别主体、印章文字、页面角色、印章角色、可选 `0～1` 坐标、名称置信度、角色置信度和原因。

Renderer 不接收 Main 本地绝对路径；图片预览通过现有 `yibiao-asset://` 协议暴露。

## 10. 结果模型与分级

```ts
type SubjectFindingLevel = 'confirmed-mismatch' | 'needs-review';
type SubjectFindingSource = 'text' | 'seal';

interface SubjectCheckFinding {
  id: string;
  bidDocumentId: string;
  level: SubjectFindingLevel;
  source: SubjectFindingSource;
  expectedSubject: string;
  detectedSubject: string;
  locationKind: 'body' | 'table' | 'signature' | 'standalone-image' | 'scanned-page';
  title: string;
  summary: string;
  locationHint: string;
  originalText: string;
  roleReason: string;
  mismatchReason: string;
  suggestion: string;
  confidence: number;
  visualAssetId?: string;
  sealCropRelativePath?: string;
}
```

### 10.1 明确正文不一致

必须同时满足：

1. 出现完整公司名称或用户未允许的明确简称；
2. 上下文明确由投标方发言、承诺、签署或承担责任；
3. 有可定位原文；
4. 与正式主体和允许简称均不匹配；
5. 角色置信度和名称置信度均不低于 `0.85`。

### 10.2 明确印章不一致

必须同时满足：

1. 印章公司名称清晰；
2. 当前区域明确要求投标方盖章；
3. 不是制造商、认证机构等合法第三方印章；
4. 与正式主体和允许简称均不匹配；
5. 名称、角色置信度均不低于 `0.85`；
6. 保留原图或印章裁剪证据。

任何一项不满足，只能进入“需人工复核”或不输出。

## 11. 持久化与状态失效

### 11.1 数据库

新增：

- `rejection_check_subject_profile`：主体候选、确认名称、简称、签名和状态；
- `rejection_check_visual_assets`：媒体清单和结构上下文；
- `rejection_check_subject_findings`：主体发现项。

现有 `rejection_check_results.result_type` 增加 `subject`。运行时 migration 与根目录 `sql/workspace_schema.sql` 必须同步。

### 11.2 结果状态

主体结果额外记录：

```ts
type SubjectVisionStatus =
  | 'disabled'
  | 'pending'
  | 'running'
  | 'success'
  | 'partial'
  | 'unavailable'
  | 'error';

interface SubjectVisionStats {
  totalAssets: number;
  candidateAssets: number;
  inspectedAssets: number;
  sealAssets: number;
  skippedAssets: number;
  reviewAssets: number;
  failedAssets: number;
}
```

正文检查成功、视觉检查不可用时，主体检查整体仍为成功，但必须显示视觉未执行或部分完成，不能显示“全部印章通过”。

### 11.3 输入签名

主体检查签名由以下内容生成：

```text
全部投标文件签名
+ 正式主体名称
+ 排序后的允许简称
+ subjectSealCheck 开关状态
+ 主体检查协议版本
```

### 11.4 失效规则

| 变化 | 主体确认 | 主体结果 | 视觉资产 |
|---|---|---|---|
| 导入或删除投标文件 | 失效 | 失效 | 对应重建或清理 |
| 修改正式主体 | 更新确认值 | 失效 | 保留 |
| 修改允许简称 | 保留确认状态 | 失效 | 保留 |
| 修改废标自定义项 | 不受影响 | 不受影响 | 保留 |
| 修改其他检查开关 | 不受影响 | 不受影响 | 保留 |
| 切换视觉模型 | 不自动删除 | 提示可重跑 | 保留 |
| 关闭印章检查 | 保留 | 失效并按新签名重跑 | 保留 |

导入或删除投标文件时必须先取消主体识别及现有检查任务并等待 runner settled。

## 12. 页面交互

### 12.1 检查配置

现有检查配置增加 `subjectCheck` 和 `subjectSealCheck`，均默认开启。关闭主体检查时印章开关禁用；读取当前文本模型配置的 `multimodal_enabled`，未开启多模态时印章开关禁用并显示“请到设置页的文本模型中开启支持多模态”。本功能不新增独立 VLM/OCR 配置入口。

### 12.2 确认流程

- 未启用主体检查：直接运行其他检查；
- 已有与当前文件签名匹配的已确认主体：直接运行；
- 没有有效主体：启动候选识别，完成后打开确认 Dialog；
- 用户确认名称、简称和印章开关后，再启动总检查；
- 多个接近候选时不预选，要求用户主动选择或输入。

Dialog 展示候选证据、正式名称输入、简称标签编辑、语义代称说明、印章开关和在线图片传输提示。

### 12.3 结果页签

新增“主体一致性检查”页签，支持：未启用、待确认、检查中、已完成、部分完成、失败和结果过期状态。

顶部展示当前确认主体、允许简称、明确不一致数量、人工复核数量及视觉覆盖统计。结果排序为明确不一致优先、人工复核其次，再按投标文件和原文位置排序。

卡片展示级别、来源、文件、正确主体、检测主体、位置、原文、角色判断、不一致原因、置信度、建议以及可用的图片预览。用户可以删除单条结果；删除不修改原始 DOCX。

### 12.4 在线数据提示

首次启用在线印章检查时说明：筛选出的投标文件图片和必要附近文字会发送到当前配置的多模态模型服务；关闭印章检查后正文主体检查仍可执行。客户端不上传整份 DOCX 给视觉模型。

## 13. 错误处理

- 主体候选识别失败：不启动主体检查，提供重新识别和手工输入；用户可选择继续其他检查。
- 正文主体检查失败：主体结果为错误；视觉结果不能将整体提升为成功。
- 当前文本模型未开启多模态：不发起图片请求，保留正文结果，视觉状态为 `unavailable`，并引导用户到设置页开启“支持多模态”。
- 在线 VLM 请求失败：保留正文结果，视觉状态为 `error`。
- 单张或单批图片失败：继续其他图片，保留成功结果，最终视觉状态为 `partial`。
- VLM JSON 无效：使用 `aiService.collectJsonResponse()` 重试；仍失败则标记该批次失败，不接受自由文本。
- DOCX 局部媒体损坏：记录资产错误并继续处理其他资产和正文。
- 应用重启后的残留主体识别或检查运行状态：转为可理解的错误状态并允许重试。

## 14. 导出

废标检查 Excel 从四张表扩展为：

1. 检查概览；
2. 废标项；
3. 错别字；
4. 逻辑问题；
5. 投标主体。

概览增加当前确认主体、允许简称、主体结果数量、印章状态和视觉覆盖。投标主体表包含文件、级别、来源、正确主体、检测主体、位置类型、位置、原文或印章文字、角色依据、不一致原因、置信度和建议。

Excel 不嵌入图片，避免工作簿体积失控。

## 15. 测试与验收

### 15.1 自动测试

使用人工构造的最小 DOCX/ZIP 夹具覆盖：

- inline、anchor、VML、表格、页眉和页脚图片提取；
- 媒体 relationship 与附近文字映射；
- 重复图片内容哈希和出现位置保留；
- 媒体目录提交失败回收；
- 名称标准化但不错误合并相似法人主体；
- 主体候选自动选择门槛；
- 原文证据校验和幻觉结果丢弃；
- 明确不一致与人工复核降级；
- 第三方正文和第三方印章排除；
- 视觉部分失败仍保留正文结果；
- 主体签名失效；
- Store 读写、删除和 migration；
- IPC 参数转发；
- Excel 第五张表和当前签名筛选。

### 15.2 核心行为场景

- 当前确认主体的正式名称、允许简称及“我公司”等正常代称不报警；
- 投标方承诺、投标函、报价表或落款中出现其他公司名称时报警；
- 客户案例、招标人、制造商、认证机构等第三方名称不报警；
- 投标方盖章位置出现其他公司清晰印章时报警；
- 制造商授权函、第三方承诺函和认证证书中的合法第三方印章不报警；
- 模糊印章、相似名称或角色不清进入人工复核；
- 多模态关闭时正文完成、印章明确显示未执行；
- 投标文件变化后主体确认和旧结果失效；
- 同一图片内容只调用一次 VLM，但保留并判断每个出现位置。

示例公司名称只用于测试数据，不代表默认主体。测试至少使用两组互不相关的虚构主体，证明实现不存在特定公司硬编码。

### 15.3 验证命令

最低验证包括：

- 对新增或修改的 `.cjs` 执行 `node --check`；
- 定向执行主体提取、任务、Store、IPC 和导出 `node --test`；
- 在 `client/` 执行 `npm run smoke:electron-native`；
- 在 `client/` 执行 `npm run build`；
- 手动使用 DOCX 验证主体确认、正文检查、在线 VLM、第三方印章排除、部分失败、重启恢复和 Excel 导出。

## 16. 未来演进

未来可以在不改变页面和主体结果协议的前提下新增服务端 `SubjectVisionProvider`，在服务端组合印章定位、OCR 和 VLM。该方向不是本版本承诺，本次 plan 不创建 YOLO、PaddleOCR 或服务器实现任务。
