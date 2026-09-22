# P3c 查重图片比对 实施计划

> **状态：已完成（2026-09-22）** —— 6 个 Task 全部落地，554 → **588 测试**；
> 8 条判别力探针全部「探针态红 / 还原后绿」（T1 前句取末句 + 弹栈、T2 跨文件过滤、
> T3 身份来源 + failed 语义、T4 totalImageCount、T5 路径泄漏）。
> 提交区间 `f981a4e`（本计划）→ `e65e25e` / `4e7f2ba` / `d9e051a` / `f8079d4` / `3b8a85f` / 本回填。
> spec §15：P3c 行 → 已完成；G4 标注已裁定；§4.4 的 image 侧承接闭合（metadata 侧待 P3d）。
> SDD ledger 与 oracle 脚本：`.superpowers/sdd/2026-09-21-p3c-image-compare/`。
>
> **执行期新增的两个实测事实**（已回写进测试断言）：
> ①**探针抓出一条无判别力的测试**——弹栈用例最初用层号单调递增的样本，弹栈分支从未
> 触发（「探针态仍绿」）；改为层级回落样本并向 oracle 补测后重写。
> ②**404 的 JSON 会转义反斜杠**——「不泄漏服务器路径」的断言必须同时查原样与转义形态。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把查重四段的第三段搬进新服务：`image` 段跑真实算法——逐 **bid** 文件的 content markdown 抽出图片出现位置（目录栈 + 前句上下文），按**图片身份**跨文件去重，产出 `image_files` / `duplicate_images` / `image_occurrences` 三处落库行 + 段的状态/进度/文案/`files`/`totalImageCount`，使 `/state.imageAnalysis` 可读。**编排、幂等、进度折算、签名作废全部不动**（P3a 已验收）。

**Architecture:** 纯算法进 `app/domain/duplicate/image.py`（零 I/O 零框架，复用 `outline`/`content` 的既有清洗与标题函数）；段接线进 `app/infra/tasks/duplicate_sections.py` 的 `build_image_runner(data_root)`，由 `create_app` 的 runner 构造处注入替换 stub；落库走 P3b 已预接线的 `_persist_rows` 三组键（评审 B 的 B-1）；新增一个只读端点供 `preview_url` 落地。

**Tech Stack:** stdlib（`re` / `hashlib`/`math`）+ 既有 `DuplicateRepository` / `DuplicateAnalysisRunner` / `asset_store.resolve_asset_path`。**不新增依赖。**

**Spec:** `docs/superpowers/specs/2026-09-15-fastapi-check-service-design.md` §9.2（图片比对）、§5.3（资产存放）、§15 的「接线遗留登记」块（G1–G4 与三条硬约束）与「跨阶段缺口登记 · P3c/P3d 段子统计无生产者」。

**明确不做（属 P3d / P4）**：元数据指纹段、Excel 导出、前端查重页；视觉模型/印章检查。也不在**本阶段**修 G2（`.doc` 图片）、G3（非 `word/media` 悬空）——见裁定 R-P3c-7。

## Global Constraints

- 工作区 `.worktrees/fastapi-check-service`，分支 `fastapi-check-service`（**平铺名，不得含 `/`**）。
- **不得改动 `client/` 下任何文件**。**不得破坏既有 554 个测试**。
- 提交身份 `git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit`；**不推送**；按显式路径 `git add`。
- `app/domain/**` 禁止 import 框架（护栏测试强制）；I/O 只能在 `app/infra/**`。
- **逐字文案**：`'正在准备图片比对'` / `'图片比对 {i}/{n}'` / `'图片比对完成'` / `'部分文件图片比对失败'` / 错误回退 `'图片比对失败'`。
- **进度公式 half-up**：`round((results.length / n) * 85)`，`n == 0` 时取 `85`；终态 `progress = 100`。JS `Math.round` = half-up（用既有的 `_half_up`）。
- 移植参考正则时，含 `\d`/`\b`/`\w` 的**一律加 `re.ASCII`**。
- **不新增依赖。**

## 已核实的前提（实测/读码得出，不需再猜）

| # | 事实 | 来源 |
|---|---|---|
| C1 | 参考 `extractImageOccurrences(markdown)`：逐行维护标题栈（`parseImageContextHeading` + `updateImageContextHeadings`），行内每个图片 target 产出一条 `{target, index(1-based), directory(headings 以 ' > ' join), previous_sentence}`；`previousText` 累计 `cleanMarkdownLine(line)` 的**最近 4000 字符**；`previous_sentence` = `getPreviousImageSentence(previousText + 行内图片之前的文本)`，取最后一个句子并 `.slice(0, 500)` | `svc.cjs:2048-2072`、`:2039-2046`、`:2019-2037` |
| C2 | `getPreviousImageSentence` 的清洗链：`cleanMarkdownInlineText` → 把 `\|` 换成空格 → 压 `[\t ]+` → `trim` → 按 `[。！？!?；;\n]+` 切 → `cleanContentSentence` 过滤空 → 取**最后一段** `.slice(0, 500)` | `svc.cjs:2039-2046` |
| C3 | 参考的逐文件循环：`readContentMarkdown` 读 markdown → `extractImageOccurrences` → `totalImageCount += len` → 本地 map（键 = **图片字节的 sha256**，值 `{count, preview_url: occurrence.target, locations:[{image_index, directory, previous_sentence}]}`）→ **单张读不出来就 `continue`（不算错误）** | `svc.cjs:2643-2662` |
| C4 | 成功行 = `{file_id, file_name, status:'success', image_count, unique_image_count: local.size}`；失败行 = `{…, status:'error', image_count:0, unique_image_count:0, error: message \|\| '图片比对失败'}` | `svc.cjs:2671-2674`、`:2676-2678` |
| C5 | 全局 map：`{hash, preview_url, file_ids[], occurrences{fileId:count}, locations{fileId:[…]}}`；`file_ids` 去重保序 | `svc.cjs:2664-2669` |
| C6 | `buildDuplicateImages`：过滤 `file_ids.length > 1` → 按 `file_ids.length` **降序**、再按出现总次数降序 → `id = 'I' + 6 位补零` | `svc.cjs:2115-2121` |
| C7 | 段终态：`status = 有 error 行 ? 'error' : 'success'`（**图片段的文件级 catch 产 error 行**，与 content 段恒 `failed=False` 不同）；`extraction = {status, completed: len(results), total: n}`；带 `files`、`totalImageCount`、`duplicateImages` | `svc.cjs:2700-2714` |
| C8 | 读 markdown 用的 `readContentMarkdown` 失败文案是 **`'正文内容尚未成功提取，无法进行目录分析'`**（该函数被目录段与图片段共用，文案里出现「目录」是参考原样） | `svc.cjs:2405-2410` |
| C9 | 参考只遍历 **bidFiles**（tender 的图不参与比对） | `svc.cjs:2641` |
| C10 | `_persist_rows` **已预接线** `image_files` 与 `duplicate_images`+`image_occurrences`（P3b 评审 B 的 B-1 修复 `371d5e0`），仓储方法 `replace_image_files(project_id, rows)` / `replace_duplicate_images(project_id, images, occurrences)` 已存在 | `duplicate_runner.py` 分派表；`repositories/duplicate.py:687/707` |
| C11 | 仓储行键：`image_files` → `file_id/status/image_count/unique_image_count/error/updated_at`；`duplicate_images` → `image_id/hash/preview_url/file_ids/sort_order`；`image_occurrences` → `image_id/file_id/occurrence_count/locations` | `repositories/duplicate.py` 两方法的 docstring |
| C12 | 读取侧 `_load_image_analysis` 期望 `files[{file_id,file_name,status,image_count,unique_image_count,error}]`、`extraction`、`totalImageCount`、`duplicateImages[{id,hash,preview_url,file_ids,occurrences{},locations{}}]` | `repositories/duplicate.py:1031-1083` |
| C13 | **我们产出的 markdown 里图片 target 是 `assets/<sha256(原始媒体字节)>`**，与 `save_docx_assets` 落盘的文件名**同哈希**（docx 接线的 R-DW-2，已实测） | 计划 `2026-09-21-...-docx-image-wiring.md`；`asset_store.asset_target` |
| C14 | 可复用的域函数：`outline.clean_outline_title` / `outline.is_catalog_title_line` / `outline.parse_outline_marker`；`content.clean_markdown_inline_text` / `clean_markdown_line` / `clean_content_sentence` / `split_markdown_table_row` | `server/app/domain/duplicate/*.py` |
| C15 | 参考的 `parseImageContextHeading`：`#{1,6}` 标题 → `cleanOutlineTitle`；否则 `cleanOutlineTitle(line)` 后要求「非空、≤90 字、不以 `。！？；` 结尾、不以 `\|` 开头、非目录标题」且**有编号标记或整行加粗**，level 取 `marker.level` 否则 2 | `svc.cjs:2019-2037` |

## 裁定

- **R-P3c-1 — 图片身份 = target 里的哈希，不读字节再哈希。** 参考必须读字节算 sha256，因为它的 target 是 `yibiao-asset://` URL（不含哈希）。我们的 target **本身就是内容哈希**（C13），所以身份直接取 `assets/<hash>` 的哈希段：**零 I/O、确定性、且语义就是「同一份原始媒体」**（落盘文件可能是转码后的字节，若对盘上字节再哈希会得到另一个值——那是容器哈希，不是图片身份）。登记为偏离（等价但更省）。
- **R-P3c-2 — 缺资产/不可解析 → 跳过该图，不算错误**（对齐 C3 的 `catch { continue }`）：用 `asset_store.resolve_asset_path` 判存在，`None` 即跳过。
- **R-P3c-3 — `preview_url` = `/projects/{project_id}/duplicate/assets/{hash}`**（相对端点）。**不得**透出服务器绝对路径（spec §15 硬约束①）。本阶段**新增该 GET 端点**，否则 `preview_url` 对前端无意义。
- **R-P3c-4 — `occurrence_count` = 该图在**该文件内**的出现次数**（参考 `current.count += 1`）；跨文件关系由 `file_ids` 承担，两者**不混**（spec §15 登记要求写死）。
- **R-P3c-5 — `failed` 语义与 outline 段同类**：任一文件 error → 段 `failed=True`（**与 content 段恒 false 不同**，见 C7）。
- **R-P3c-6 — 只遍历 bid 文件**（C9）。tender 的图片不参与比对、不进 `totalImageCount`。
- **R-P3c-7 — 已知缺口不在本阶段修**：G2（`.doc` 图片）不动（本机无 soffice，无法实跑验证）；G3（非 `word/media` 悬空）由 R-P3c-2 自然降级；**G4 要在这里处置**：不照搬参考的 `!\[(?<alt>[^\]]*)\]\(…` 正则（alt 含 `]` 会命中 0），改为**只锚定 target 形态**——匹配 `\((assets/[0-9a-f]{64})\)`（并兼容 `<>` 包裹与 `<img src="assets/…">` 的 HTML 形态）。理由：**产出形态是我们自己定义的**（docx 接线产出 markdown 图片 + 表内图片也是 markdown 形态），消费侧应与产出一致而不必迁就参考的表内 HTML 形态。
- **R-P3c-8 — 文案逐字复刻 C8**：读 markdown 失败的 per-file error 文案就是「正文内容尚未成功提取，无法进行目录分析」（参考共用同一函数）。**不**为了「说得更好」改文案——改了就与参考的 `error` 字段不一致。
- **R-P3c-9 — 段内进度走 `section_progress`（`(percent, message)`，R-P3b-3 冻结）**：`running` 期的 `extraction`/`files`/`totalImageCount` 与 P3b 一样**不写**（中间态偏离已在 spec §15 登记，终态正确）。

## File Structure

| 文件 | 职责 |
|---|---|
| `server/app/domain/duplicate/image.py` | **新建**：`line_image_targets` / `parse_image_context_heading` / `previous_image_sentence` / `extract_image_occurrences` / `build_duplicate_images`（纯函数，零 I/O） |
| `server/app/infra/tasks/duplicate_sections.py` | **改**：新增 `build_image_runner(data_root)` |
| `server/app/api/duplicate.py` | **改**：新增 `GET /projects/{pid}/duplicate/assets/{hash}`（只读取图，供 `preview_url`） |
| `server/app/main.py`（或 `create_app` 的 runner 构造处） | **改**：注入 `image` 段的真实 runner |
| `server/tests/test_domain_image.py` | **建**：纯函数测试（Node oracle 取真值） |
| `server/tests/test_image_section.py` | **建**：段接线测试（落库行/统计/文案/失败语义） |
| `server/tests/test_api_duplicate_assets.py` | **建**：取图端点测试（命中/未命中/遍历/非法 id） |
| `server/tests/test_duplicate_e2e.py` | **改**：e2e 加「图片重复可读」闭环断言 |

---

### Task 1: `domain/duplicate/image.py` —— 图片出现位置纯函数

**Files:** Create `server/app/domain/duplicate/image.py`；Test `server/tests/test_domain_image.py`

**Interfaces:**
- `line_image_targets(line: str) -> list[dict]`（`{target, index}`，按出现位置升序；**只认 `assets/<64hex>` 形态**，见 R-P3c-7）
- `parse_image_context_heading(line: str) -> dict | None`（C15）
- `previous_image_sentence(value: str) -> str`（C2，含 `.slice(0, 500)`）
- `extract_image_occurrences(markdown: str) -> list[dict]`（C1：`{target, index, directory, previous_sentence}`）

- [x] **Step 1: 写失败的测试**（**预期值一律 Node 直跑参考取真值**：截取 `extractImageOccurrences`/`extractLineImageTargets`/`parseImageContextHeading`/`getPreviousImageSentence` 及其依赖 `cleanMarkdownInlineText`/`cleanMarkdownLine`/`cleanContentSentence`/`cleanOutlineTitle`/`isCatalogTitleLine`/`parseOutlineMarker`/`normalizeContentLineBreaks` 一并 eval）
  - `test_line_targets_finds_assets_targets_in_order`
  - `test_line_targets_covers_markdown_and_html_and_table_cell_forms`（`![](assets/<h>)` / `<img src="assets/<h>">` / GFM 单元格里的 `![](assets/<h>)`）
  - `test_heading_stack_builds_directory`（`#`/`##` 与加粗短行 → `A > B`）
  - `test_previous_sentence_takes_the_last_sentence_and_truncates_to_500`
  - `test_occurrences_index_is_1_based_and_counts_across_headings`
  - `test_fenced_code_blocks_are_stripped_before_scanning`
- [x] **Step 2: 确认失败**。**Step 3: 实现**（每条正则带参考行号注释）。**Step 4: 全量零回归**（554+新增）。
- [x] **Step 5: 变异探针（必做 2 条）**：①`previous_sentence` 取第一句而非最后一句 → 相应用例红；②`directory` 不 pop 同级/更深层级（只 append）→ 目录栈用例红。
- [x] **Step 6: 提交**：`feat(server): P3c 图片出现位置纯函数——目录栈/前句上下文/target 抽取`

---

### Task 2: `build_duplicate_images` —— 跨文件去重汇总

**Files:** Modify `server/app/domain/duplicate/image.py`；Test `server/tests/test_domain_image.py`

**Interfaces:**
- `build_duplicate_images(rows: Sequence[Mapping]) -> list[dict]`：过滤 `len(file_ids) > 1` → 按 `(len(file_ids), 出现总次数)` 双键降序 → `id = f"I{index:06d}"`（C6）；输出行键对齐 C11（`image_id`/`hash`/`preview_url`/`file_ids`/`sort_order`）+ `occurrences`/`locations` 由调用方展开。

- [x] **Step 1: 写失败的测试**（Node oracle 对拍 `buildDuplicateImages` 的**排序稳定性**与 **id 编号从 1 开始**；含「同分时按总次数」与「都不重复 → 空数组」两个边界）
- [x] **Step 2-3: 确认失败 → 实现**。**Step 4: 全量零回归**。
- [x] **Step 5: 变异探针（必做 1 条）**：去掉 `len(file_ids) > 1` 过滤 → 只用一份文件的图也进结果 → 相应用例红。
- [x] **Step 6: 提交**：`feat(server): P3c 跨文件图片去重汇总——双键排序与 I%06d 编号`

---

### Task 3: `build_image_runner(data_root)` —— 段 runner

**Files:** Modify `server/app/infra/tasks/duplicate_sections.py`；Test `server/tests/test_image_section.py`

**Interfaces:**
- `build_image_runner(data_root) -> SectionRunner`
- Consumes：`SectionRunContext.files/content_files`、`_read_content_markdown`（P3b 既有）、`extract_image_occurrences`、`build_duplicate_images`、`resolve_asset_path`、`USER_ASSET_PREVIEW_PATH`（见 Task 5）

**行为（逐条对齐参考）**：逐 **bid** 文件（R-P3c-6）→ 读 markdown（失败 → error 行，文案逐字 C8/R-P3c-8）→ 抽出现位置 → 逐出现位置取 `resolve_asset_path`（`None` → 跳过，R-P3c-2）→ 本地按哈希聚合 `{count, preview_url, locations}` → 全局聚合（C5）→ 段末 `build_duplicate_images` + 终态（C7）→ `SectionOutput(rows={"image_files": …, "duplicate_images": …, "image_occurrences": …}, stats={...}, extraction=…, failed=…)`。

- [x] **Step 1: 写失败的测试**：真 docx + 图片（跨两个文件内嵌**同一张图**）→ 断言 ①三组 rows 的形状与计数；②`duplicate_images` 只含跨文件重复项且 `file_ids` 有序；③`image_occurrences` 的 `occurrence_count` = **文件内**次数（R-P3c-4）；④`locations` 含 `image_index`/`directory`/`previous_sentence`；⑤`totalImageCount` 与 `files[].image_count` 一致；⑥一个文件读不到 markdown → 该行 error、段 `failed=True`、其余文件仍 success（R-P3c-5）。
- [x] **Step 2-3: 确认失败 → 实现**。**Step 4: 全量零回归**。
- [x] **Step 5: 变异探针（必做 2 条）**：①把「哈希取 target 哈希段」改成「对盘上字节哈希」→ 跨文件同图不再合并 → 相应用例红；②把 `failed` 改成恒 `False` → 失败语义用例红。
- [x] **Step 6: 提交**：`feat(server): P3c image 段 runner——出现位置/跨文件去重/三组落库行`

---

### Task 4: 段接线 + `image` 段子统计（spec §4.4 的必办项）

**Files:** Modify `create_app` 的 runner 注入处、`duplicate_runner.py`（若需）；Test `server/tests/test_image_section.py`

**行为**：把 stub 的 `image` 角色换成真实 runner；段 stats 写 `files`（逐文件摘要）与 `totalImageCount`（spec §15 的「P3c/P3d 段子统计无生产者」登记要求）；`/state.imageAnalysis` 因此不再出现「段 success 而子统计 pending」的自相矛盾。

- [x] **Step 1: 写失败的测试**：`/state` 的 `imageAnalysis.files` 非空且与 `duplicateImages` 一致；`totalImageCount` == 各文件 `image_count` 之和；`extraction.status` 与段 status 同源。
- [x] **Step 2-4: 确认失败 → 实现 → 全量零回归**。
- [x] **Step 5: 变异探针（必做 1 条）**：删掉 stats 里的 `totalImageCount` → 相应用例红。
- [x] **Step 6: 提交**：`feat(server): P3c image 段接线——runner 注入 + files/totalImageCount 子统计`

---

### Task 5: 取图端点（`preview_url` 的落地面）

**Files:** Modify `server/app/api/duplicate.py`；Test `server/tests/test_api_duplicate_assets.py`

**行为**：`GET /projects/{project_id}/duplicate/assets/{hash}` → `validate_project_id` → `resolve_asset_path` → 命中则按扩展名/嗅探定 `Content-Type` 返回文件内容；未命中 404。**绝不**在响应里暴露服务器路径。

- [x] **Step 1: 写失败的测试**：命中 200 + `content-type` 正确（png/jpg 各一）；未命中 404；非法 `hash`（非 64hex）404；非法 `project_id`（`..`）**不返回 200 且不触达读盘**（复用 B1 的哨兵手法）；响应体字节与盘上一致。
- [x] **Step 2-4: 确认失败 → 实现 → 全量零回归**。
- [x] **Step 5: 变异探针（必做 1 条）**：把 `validate_project_id` 去掉 → 遍历用例红。
- [x] **Step 6: 提交**：`feat(server): P3c 取图端点——preview_url 落地（相对路径，不泄漏部署路径）`

---

### Task 6: e2e 闭环 + 收尾回填

**Files:** Modify `server/tests/test_duplicate_e2e.py`、spec §15、本计划

1. **e2e 加闭环断言**：两份投标各内嵌**同一张图** → `/state.imageAnalysis` 的 `duplicateImages` ≥1、`file_ids` 覆盖两个文件、`totalImageCount` 正确、`preview_url` 可 200 取到。
2. **spec §15 回填**：P3c 行 → 已完成；「接线遗留登记」块里 G4 标注已裁定（R-P3c-7）；§4.4 的 P3c 承接项标记闭合。
3. 本计划勾选。
- [x] **Step 1-2**: 加断言 → 跑绿（若红先查清）。**Step 3**: 回填。**Step 4**: 全量。**Step 5**: 提交。

---

## Self-Review

| Spec 条目 | 落在哪个任务 |
|---|---|
| §9.2 `extractImageOccurrences`（目录栈 + 前句上下文） | Task 1 |
| §9.2 跨文件按 hash 去重与 `occurrence_count`/`locations_json` | Task 2 + Task 3 |
| §5.3 资产存放（`assets/<hash>.<ext>`）与 target 解析 | Task 3（R-P3c-1/2）+ Task 5 |
| §15 硬约束①（`preview_url` 不得透出绝对路径） | Task 5 |
| §15 硬约束②（markdown 占位面 < occurrence 面 → 页眉页脚图不参与） | R-P3c-6 + Task 1（只扫 markdown） |
| §15 硬约束③（mime 未落库 → 反推/嗅探） | Task 5 |
| §15 §4.4（image 段的 `files` / `totalImageCount` / `extraction`） | Task 4 |
| G4（alt 含 `]`） | R-P3c-7（正则只锚定 target 形态） |
| 段内进度/文案/终态（R-P3b-3/4 的既有裁定） | Task 3 |

**已知空白（实现者必须在报告如实说明）：**

1. **页眉页脚图片不参与**（R-P3c-6 + 只扫 markdown）：与参考一致，但与 P2b `extract_docx_visual_assets` 的 occurrence 面**不等**。P3c 不引入那条面（参考也不用它）。
2. **`image_count` 与落库 `unique_image_count` 的口径**：前者 = markdown 里的**出现次数**，后者 = **该文件内不同图片数**（本地 map 大小）。若某图的资产缺失，前者含它、后者不含——与参考的 `count` 口径一致（参考在缺字节时 `continue`，`image_count` 仍是 `imageOccurrences.length`）。
3. **`.doc` 图片全链路缺失**（G2）不在本阶段；`.doc` 文件的 `image_count` 恒 0、段仍 success。**不要**为此加特判。
4. **`preview_url` 的 `hash` 是原始媒体哈希**，而盘上文件可能是转码后的字节（扩展名不同）——端点按 `resolve_asset_path` 的 glob 解析，mime 由实际文件决定。
