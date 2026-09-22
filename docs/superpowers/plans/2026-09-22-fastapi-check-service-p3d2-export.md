# P3d-2 计划：查重 Excel 导出（spec §9.3 查重六工作表）

日期：2026-09-22 ｜ spec：`docs/superpowers/specs/2026-09-15-fastapi-check-service-design.md` §9.3
参考实现：`client/electron/services/checkResultExportService.cjs`（547 行，`buildDuplicateWorkbook` :341-469）
oracle：参考实现本身（抠 `buildDuplicateWorkbook` + 传递依赖 eval，stub XLSX 记录 AOA 真值）

## 范围裁定

- **只做查重六工作表**（spec §15 P3d 行的产出描述即「6 工作表 Excel 导出」）。
  废标检查工作簿（5 表）属 P4（rejectionCheckStore 未迁移），本计划不做。
- 参考的 `dialog.showSaveDialog` 保存语义属 Electron；服务端改为 **HTTP 下载**
  （`GET /projects/{pid}/duplicate/export` 返回 xlsx 字节 + `Content-Disposition`
  文件名），保存路径交给前端（P3d-3）。`sanitizeFileName`/`ensureXlsxPath` 的
  文件名形态照搬（用于 Content-Disposition）。
- `request.signature`：参考由前端从 state 传入；服务端**自持**——直接读
  `duplicate_check_meta.current_signature` 作为判据输入（R-P3d2-1 有意偏离：
  server 无「前端 state」概念，自持等价且更强）。

## 裁定条款

- **R-P3d2-2（纯函数/组装器分层）**：六表 AOA rows/widths/filterHeaderRow/百分比
  格式坐标的构造 = **纯函数**（`app/domain/duplicate/export_workbook.py`，输入 =
  `load_analysis` 产物 + signature + exportedAt，零 I/O 零 openpyxl）；
  xlsx 序列化 = 组装器（`app/infra/exporting/duplicate_xlsx.py`，openpyxl）。
  对齐 R-P3d-11 分层惯例。
- **R-P3d2-3（形状逐字）**：表名序 `['查重概览','元数据','目录重复','文件相似度',
  '重复句子','重复图片']`；概览表结构（导出时间/空行/表头/四维度行/空行/统计块）、
  五数据表表头与列宽数组、`joinFileNames` 的 `；` join、risk 中文映射
  （high→高/medium→中/low→低/none→无，缺省 无）、目录重复表第 4 列与
  文件相似度表 c2-c5 的 `0.00%` 格式、autofilter（概览无、其余五表
  filterHeaderRow=0）——全部照抄参考。
- **R-P3d2-4（文本净化）**：`normalizeCellText` 照搬（剥 `\u0000-\u0008\u000B\u000C
  \u000E-\u001F\uFFFE\uFFFF`；>32767 截断至 32750 前缀 + 「……（内容过长，已截断）」）；
  数字值不过净化（`normalizeRows` 的 typeof number 分支）。
- **R-P3d2-5（exportable 判定）**：四段任一 `valid`（`source.signature == signature`
  且 source 存在）且（`status=='success'` 或 有重复行）才可导出；否则 HTTP 400
  detail=「没有可导出的当前标书查重结果」（参考 :520 逐字）。
- **R-P3d2-6（概览统计口径）**：`countDuplicateRows` 四字段口径照搬
  （metadata→rows 长度、outline→duplicateGroups、content→duplicateSentences、
  image→duplicateImages）；统计块四行的键与 `Number(x||0)` 缺省照搬。
- **R-P3d2-7（openpyxl 形态映射）**：SheetJS `!cols` wch → openpyxl
  `column_dimensions[letter].width`；`!autofilter.ref` → `ws.auto_filter.ref`；
  `cell.z='0.00%'` → `cell.number_format='0.00%'`；`XLSX.write(compression:true)`
  → openpyxl 默认 zip 压缩。值语义：number 直写、其余经 normalizeCellText。
- **R-P3d2-8（时间格式）**：`formatDisplayTime` → `YYYY-MM-DD HH:MM:SS`（本地时区，
  padStart 2 位）；非法时间串原样过 normalizeCellText（Number.isNaN 分支）；
  文件名时间戳 `YYYYMMDD_HHMMSS`。

## 任务拆分（TDD）

- **T1 纯函数层**：`export_workbook.py` 的 `build_duplicate_workbook_plan(state,
  signature, exported_at)` → 结构化产物（六表 name/rows/widths/filter_header_row/
  percent_cells + 文件名两枚）+ `normalize_cell_text` 等辅助。单测 = oracle 对拍
  （oracle-duplicate.cjs stub XLSX 记录 appendWorksheet 参数与 z 坐标；覆盖：
  全维度有数据 state、签名不一致 state、空 state、长文本截断、risk 全枚举、
  非法时间串）；探针：变异 joinFileNames 分隔符 → 概览/数据表多行红。
- **T2 组装器**：`duplicate_xlsx.py` 的 `write_duplicate_xlsx(plan) -> bytes`。
  单测 = openpyxl 读回验证（表名序/autofilter ref/number_format/列宽/截断文本）；
  探针：变异 number_format 坐标偏移 → 读回断言红。
- **T3 API 接线**：替换 501 → 真实现（400 语义 + xlsx 响应）；e2e：上传+分析 →
  导出 → openpyxl 读回六表断言；空项目/无签名 → 400。
- **T4 收尾**：全量回归 + spec §15 回填（P3d 行状态更新）+ ledger + memory +
  提交（InsCode，不推送）+ 双评审。

## 已知空白（登记）

- 废标检查工作簿（P4 承接）。
- 前端保存对话框与下载触发（P3d-3 承接）。
- openpyxl 与 SheetJS 的 xlsx 字节级差异（字体/默认样式）不在保真范围——
  保真锚点是**工作表内容结构**（rows/列宽/autofilter/数字格式），不是文件字节。
