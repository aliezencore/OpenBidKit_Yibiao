# P3d-1 计划：查重元数据指纹段（metadata_extraction 角色）

日期：2026-09-22。上游：P3c 已完成（`8849071`，588 passed）。
本计划只覆盖 **P3d 的 metadata 段**；Excel 导出（P3d-2）与前端查重页（P3d-3）另行立计划。
spec 依据：§6 P3d 行（`extractMetadata`：docx rsid、OLE/CFB 属性集、PDF Info+XMP+结构、WPS 信号）；
**spec §15 §4.4 登记的必办项**（metadata 段 `files` / `metadataExtraction` 子统计）在本计划兑现。

oracle = 参考实现 `client/electron/services/duplicateCheckService.cjs`（下称 svc.cjs）。
所有断言值一律 Node 直跑参考取真值（`.superpowers/sdd/2026-09-22-p3d-metadata/` 下的 oracle 脚本），
不许手工推演。依赖（adm-zip / cfb / iconv-lite / pdf-parse@2.4.5 CJS）均可从
`client/node_modules` require。

## 已核实前提（W = verified fact）

- W1 读取侧契约（repositories/duplicate.py `_load_metadata_analysis` :831-884，P3a 已评审）：
  - `stats.files` → 逐 bid 文件的 `{file_id, status, error}` 摘要；**缺键 → 全部 bid 文件误报
    `pending`**（:850 `summary.get("status") or "pending"`）——§4.4 必办项的根据。
  - `stats.metadataExtraction` → 子统计（缺键 → `_empty_progress("pending", len(files))`）。
  - `stats.contentExtraction` / `stats.logs` → 现有主编排/读取侧已接，runner 不重复写。
  - `files[].metadata` 从 `duplicate_check_metadata_items` 表重建；`rows` 由读取侧
    `_build_rows` 从 items 现算（对齐参考 loadMetadataAnalysis :633-682）——**runner 不落 rows 表**。
- W2 编排契约（duplicate_runner.py）：`metadata_extraction` 角色被 `await`（:425-427），
  不写状态行；产出经 `_commit_terminal_states` → `_merge_metadata_stats`（stats 作基底）→
  metadata 段终态；`_persist_rows` 已预接 `metadata_items` 分支（:834-836）；
  `failed=True` → metadata 段 error + 文案「部分文件提取失败」。
- W3 参考行为（svc.cjs `runMetadataExtraction` :2365-2403）：
  - 只跑 **bidFiles**（:2734 调用点）；逐文件 try/catch → error 行
    `{file_id, file_name, status:'error', error, metadata: []}`；
  - 子统计 `metadataExtraction = {status, completed, total}`（终态 status=error 若任一文件 error）；
  - 每文件广播 message「元数据提取 {n}/{total}」（:2389），无进度百分比；
  - rows = `buildRows(results)`（:1096-1132）——服务端由读取侧重建（W1），runner 不产。
- W4 `extractMetadata`（svc.cjs :1060-1094）的产物形状：逐字段
  `{key, label, value, normalized, date_day, comparable, date_comparable}`；基础字段
  file_name/extension/size/created_at/modified_at/accessed_at/file_sha256 来自 stat + sha256；
  dispatch：`.docx` 或「.doc/.wps 且 zip 头 PK\x03\x04」→ docx 提取；.doc/.wps → legacy
  word（OLE + 转换 docx）；.pdf → PDF；其他 → 仅基础字段；内部异常 → `metadata_error` 字段。
- W5 服务器文件行已带 `extension`（小写、带点；api/duplicate.py :433/:459），
  `extractMetadata` 的 `String(file.extension||'').toLowerCase()` 可直接消费。
- W6 依赖现状：server 已有 pymupdf/Pillow/lxml/openpyxl；**无 olefile / pypdf**（需新增）。
  参考的 pdf-parse@2.4.5 提供 CJS 入口（可 require）；cfb 可写 CFB 容器（T3 夹具生成用）。
- W7 本机无 soffice（G2 既定事实）：参考 `extractConvertedDocxMetadata` 在本机的真值 =
  抛错 → `extractLegacyWordMetadata` 捕获 → `metadata_error` 列表字段
  「转换 DOCX 元数据读取失败：{error.message}」；error.message 由 convert.mjs 的
  soffice 探测失败文案决定（oracle 实测取逐字真值）。
- W8 store.cjs 的持久化裁剪（state.py `build_section_stats_patch` 已逐字对齐 :179-222）：
  metadata 段 stats 持久化形状 = `{contentExtraction?, metadataExtraction?, logs?, files?(4字段
  file_id/file_name/status/error)}`。服务器无广播层、持久化即唯一 wire → runner 产出的
  stats 直接按此形状裁剪（R-P3d-9）。
- W9 测试/环境约束沿用 MEMORY：pytest 以 stdout「N passed」为准；探针铁律（唯一上下文
  锚点 / 结构保持单行变异 / ast.parse + grep 查残留）；提交身份 InsCode、不推送。

## 裁定（R-P3d-x）

- **R-P3d-1（库选型）**：CFB 容器导航用 **olefile**（新增；只取条目树/流字节/大小，
  **不用**它的 metadata 解析——键映射与格式化必须走移植的 parsePropertySetStream）；
  PDF Info 原键 dict 与 XMP 原始包用 **pypdf**（新增），pymupdf 的 metadata 键名/形状
  与 pdf.js 不同，不用于 Info dict。纯 Python、无 ABI 包，符合「不污染环境」。
- **R-P3d-2（§4.4 必办项，阻塞性）**：runner 的 stats 必须写
  `metadataExtraction`（camelCase 子统计 `{status, completed, total}`）与
  `files`（逐 bid 文件 `{file_id, file_name, status, error?}`）。判定以 W1 的读取侧
  消费为准；e2e 用例显式断言 `/state.metadataAnalysis.files[].status` 非 pending、
  `metadataExtraction.status` 非 pending。
- **R-P3d-3（rows 形状）**：runner rows 只产 `metadata_items`（跨文件扁平；行键 =
  `replace_metadata_items` 的键：`file_id`(必)/`key`(必)/`label`/`value`/`normalized`/
  `date_day`/`comparable`/`date_comparable`/`sort_order`（文件内序号））。
  指纹对比表 rows 不落库（W1：读取侧现算，与 outline 的 `items` 同一 wire-invisible 裁法）。
- **R-P3d-4（处理对象）**：只跑 `section_ctx.bid_files`（W3）；失败上传行
  （`file_path = upload-failed:*`）stat 抛 FileNotFoundError → error 行，与
  content_extraction 的逐文件容错同构（参考 :2381-2382）。
- **R-P3d-5（进度与文案）**：提取角色无 `section_progress`（编排器不注入，:877-883）；
  中段文案走 `_broadcast`：「正在提取投标文件元数据」（:2371 逐字）/
  「元数据提取 {n}/{total}」（:2389 逐字）。终态文案由主编排写（P3a 契约不变）。
- **R-P3d-6（.doc/.wps 的 converted_docx 部分）**：本阶段**不实现 soffice 转换**
  （与 P2a 的 .doc 本地解析边界一致；G2 已登记）。服务端恒走「转换失败」分支，
  `metadata_error` 的文案与参考在无 soffice 机器上的真值逐字对齐（W7，oracle 取）。
  部署机有 soffice 时的接线另行登记，不在本计划。
- **R-P3d-7（JS Date 语义）**：`normalizeComparable`/`normalizeDateDay` 的 `new Date(text)`
  用自写 `js_date_parse`（ISO 系 + `D:` 剥离 + `±HH'MM'` 时区修复 + 常见宽松格式，
  本地时区语义与 JS 一致）；**不引入 dateutil**。样本真值从 oracle 取；oracle 暴露出
  未覆盖格式 → 扩 parser，不许改参考。
- **R-P3d-8（指纹哈希与排序）**：`hashText` = sha256(utf8).hexdigest()；
  `uniqueSortedValues` 的 `localeCompare` 排序先按 Python `sorted` 实现，
  oracle 对拍若出现分歧（U+0005 前缀流名等）→ 按 oracle 真值修排序键，不许手写特例。
- **R-P3d-9（stats 持久化形状的落点）**：runner 产出 stats 后**就地**用
  `build_section_stats_patch("metadata", …)` 裁剪（该 domain 函数由此获得首个真实调用点，
  恰好对齐 store.cjs「广播全量、持久化裁剪」的净效果——服务器无广播层）。
  `contentExtraction` 仍由主编排 `_merge_metadata_stats` 叠加（不动）。
- **R-P3d-10（PDF 字段的 oracle 策略）**：pdf.js 派生形状（info 键集 / XMP entries /
  fingerprints / permission / version / pages）以 oracle 实测为准，用 pypdf + 原始字节
  解析复刻；复刻不了且影响输出的键 → 显式登记偏离（spec §15），不许静默丢。
- **R-P3d-11（代码分层）**：纯函数全部进 `app/domain/duplicate/`（零 I/O；
  `metadata_base.py` 基座 + `js_date.py` + `metadata_docx.py` + `metadata_ole.py` +
  `metadata_pdf.py`）；I/O 组装进 `app/infra/parsing/metadata_extract.py`（stat/sha256/
  zipfile/olefile/pypdf + dispatch）；runner 进 `app/infra/tasks/duplicate_sections.py`
  （`build_metadata_runner`），接线在 api/duplicate.py 的 sections 注入处。

## 任务（TDD：先 oracle 取真值 → 实现 → 探针 ≥1/任务）

- **T1 基座纯函数**（metadata_base + js_date）：normalizeValue/normalizeComparable/
  normalizeDateDay/addField 三兄弟/safeMetadataKey/formatMetadataKey/getMetadataLabel/
  isComparableKey/isDateComparableKey/yesNo/countMatches/uniqueSortedValues/summarizeValues/
  xmlText/decodeXml/formatDocxTotalTime/hashText/tryDecodeBase64Text/
  shouldSkipBase64Decode/addDecodedBase64Fields/extractMetadata 尾部的 items 映射。
  oracle：oracle-base.cjs（抠函数 + 三张键表 const，喂样本出 JSON 真值）。
  探针：`safeMetadataKey` 的 80 字符截断（或 normalizeComparable 空值清单）。
- **T2 docx 提取**（metadata_docx + infra zip 读取）：core/app/custom 逐字段
  （:710-736 的键序即 sort_order 序）、custom 属性正则、addDocxRsidFields（rsid 正则 +
  rsidroot + fingerprint）、addWpsSignalFields、mergeMetadataFields。
  夹具：python-docx 生成 + zip 注入 custom.xml/rsid 属性（测试内现做）。
  oracle：同一 docx 文件喂 Node（AdmZip）与 Python，逐字段对拍。
  探针：rsidroot 分支（多 word/*.xml、首处 rsidRoot）。
- **T3 OLE/CFB + legacy word**（metadata_ole + infra olefile）：parsePropertySetStream
  全家（typed value/vector/dictionary/filetime/duration/version/code page）、
  addOleStructureFields/addOleSignalFields/addWpsSignalFields、findCfbEntry 语义
  （olefile 对应物）、extractLegacyWordMetadata 的无 soffice 分支（R-P3d-6）。
  夹具：Node `cfb` 生成 .doc 容器（含 \x05SummaryInformation /
  \x05DocumentSummaryInformation / 自定义 dictionary / 宏存储流）。
  oracle：同一文件两边对拍（含 metadata_error 逐字文案）。
  探针：0xfffe magic / setCount 循环边界。
- **T4 PDF 提取**（metadata_pdf + infra pypdf）：addPdfInfoFields（PDF_INFO_KEY_MAP +
  pdf_info:*）、XMP（canonicalPdfXmpKey + pdf_xmp:* + raw_signals）、canonical 回填
  （addFieldIfAbsent :1038-1045）、pages/pdf_version/fingerprints/pdf_permissions、
  addPdfRawFields/addPdfStructureFields/addPdfFormSignatureAttachmentFields。
  夹具：pymupdf 生成（带 Info / 带 XMP / 增量保存）。
  oracle：pdf-parse@2 实测 result 形状 → R-P3d-10。
  探针：结构计数（object_count/startxref_count）。
- **T5 extractMetadata 组装器**（infra/parsing/metadata_extract.py）：stat 三时间
  （toISOString 口径，注意 JS 本地时区→UTC）/sha256/extension dispatch（zip 头探测）/
  base64 解码收尾/items 映射；`metadata_error` 只在对应分支异常时出现。
  探针：dispatch 分支（.doc 假 zip 头）。
- **T6 runner + 接线 + e2e**：`build_metadata_runner`（R-P3d-2/3/4/5/9）；
  api/duplicate.py 注入 `sections[METADATA_EXTRACTION]`；e2e（docx+pdf+txt 混合）：
  跑分析 → `/state.metadataAnalysis` 全形状（files 逐文件 status 非 pending + metadata
  键序 + metadataExtraction 子统计 + rows 键序）+ metadata 段终态。
  探针（钉 §4.4 必办项的判别力）：变异 runner 去掉 stats.files → e2e 必红。
- **T7 收尾**：全量回归（默认临时目录）+ spec §15 回填（§4.4 metadata 侧闭合）+
  ledger + MEMORY/日志 + 提交 + 双评审（不并行共享 worktree）。

## 已知空白（不阻塞，登记）

- soffice 转换的 .doc 元数据（R-P3d-6；与 G2 同源）。
- JS Date 的极端宽松格式（如 "Jan 5, 2024" 英文月名）——oracle 样本覆盖不到的
  分支按「登记 + 后续补样本」处理，不凭记忆实现。
- localeCompare 在非 ASCII 流名上的排序分歧（R-P3d-8 的对拍兜底）。
