# P3b 正文与目录比对 实施计划

> **状态：已完成（2026-09-21）** —— 22 提交（`a7bc172` → `371d5e0`），456 → **519 测试**；
> 双评审 PASS WITH NOTES（`review-t4t7-a.md`：1×P1 + 4×P2 全部闭合；`review-t4t7-b.md`：
> 0×P0 / 0×P1，3×P2 + 2 观察全部处置）。执行期裁定与偏离登记见 spec §15 的
> 「P3b 执行期裁定回填」；逐任务证据与探针记录见 SDD ledger
> `.superpowers/sdd/2026-09-19-fastapi-check-service-p3b-content-outline-compare/progress.md`。
>
> **本计划范围外但已登记**（交 P3c/P3d 承接）：P2a↔P2b 未接线（docx 的
> `include_images=True` 仍显式拒绝）、metadata/image 两段的子统计与其 rows 落库分派。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把查重四段中的**两个真实算法段**搬进新服务：`content_extraction`（正文提取，共享上游）、`outline`（目录抽取三来源 + 跨文件分组/pairwise）、`content`（正文切句 + 招标要求五级匹配 + 跨文件重复句）。metadata 段留 stub（P3d）、image 段留 stub（P3c）。**编排、幂等、进度折算、签名作废全部不动**（P3a 已验收）。

**Architecture:** 纯算法进 `app/domain/duplicate/`（`outline.py` / `content.py` / `tender_matcher.py`，零 I/O 零框架）；段实现（编排接线）进 `app/infra/tasks/`（`duplicate_sections.py` 新模块），由 `create_app` 的 runner 构造处注入 `sections` 映射替换对应 stub 键；落库走 P3a 已验收的 `DuplicateRepository.replace_*`。

**Tech Stack:** stdlib（`re` / `hashlib` / `unicodedata`）+ 既有 `Database` / `DuplicateRepository` / `DuplicateAnalysisRunner` / `parse_document`（P2a）。**本计划不新增任何依赖。**

**Spec:** `docs/superpowers/specs/2026-09-15-fastapi-check-service-design.md` §9.2（正文/目录比对行为）、§15 P3b 行

**参考实现（只读）：** `client/electron/services/duplicateCheckService.cjs`（下称 `svc.cjs`）——本计划所有 `:行号` 都指它。

## Global Constraints

- 工作区 `.worktrees/fastapi-check-service`，分支 `fastapi-check-service`（平铺名）。
- **不得改动 `client/` 下任何文件。**
- 当前基线 **456 passed**（P3a 终态）。每任务完成后全量数字 = 456 + 累计新增，零回归；pytest 退出码被 shim 改写，只读 stdout 的 `N passed`。
- `app/domain/**` 禁止 import fastapi/starlette/sqlalchemy/httpx/pydantic/sqlite3（护栏测试强制）。
- 提交身份 `git -c user.name="InsCode" -c user.email="noreply@inscode.net"`；**不推送**；按显式路径 `git add`。
- 验收标准是**行为对齐**：阈值、文案、公式、排序键必须与参考实现**逐字**一致（下文标「逐字」处）；结构等价处（文件路径形状等）允许偏离但必须登记。
- 全量测试命令：`cd server && CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=100000 .venv/Scripts/python.exe -m pytest -q`。Bash 先 `export PATH="/c/Program Files/Git/usr/bin:$PATH"`。一次性脚本写 worktree 内跑完即删。
- **变异探针纪律**：每任务探针变异后必须 `git -C <worktree 绝对路径> diff --exit-code` 确认还原（防 cwd 漂移假 EXIT=0）；对新增/修改的每个模块级符号做 `grep -c "^def <name>"`（或 `^class`）唯一性自检（本仓两次重复定义事故的硬要求）。

## 明确不做（留给后续计划）

- **metadata 段**（`extractMetadata` 的 docx rsid / OLE / PDF 指纹）→ P3d；`metadata_extraction` 角色保持 P3a stub。
- **image 段**（`extractImageOccurrences` + 字节 hash 去重）→ P3c；`image` 角色保持 P3a stub（空产出、终态 success）。
- Excel 导出、前端页面 → P3d。
- SSE 事件流的段内细粒度事件（`GET /tasks/{id}/events` 的 domain 事件形状）不在本计划验证——TestClient 死锁前科（R22），段内进度的断言走 `GET /state` 直读段状态行。

## 已定接口（P3a 已冻结，本计划只填充不改签名）

```python
# DuplicateAnalysisRunner（app/infra/tasks/duplicate_runner.py）
#   sections: Mapping[str, SectionRunner]，五角色键：
#   content_extraction / metadata_extraction / outline / content / image
# SectionOutput(stats: dict, rows: dict[str, list], failed: bool)
# SectionRunContext(project_id, section, ctx, files, bid_files,
#                   tender_files, signature, content_files)
# DuplicateRepository.replace_content_files / replace_outline_items /
#   replace_outline_groups / replace_outline_pairwise / replace_content_duplicates
#   —— 行键 docstring 见 repositories/duplicate.py:481-670，本计划直接消费
```

**P3b 追加冻结（向后兼容的默认值扩展，不破坏 P3a 既有调用）：**

```python
@dataclass(frozen=True)
class SectionOutput:
    stats: dict[str, Any] = field(default_factory=dict)
    rows: dict[str, list] = field(default_factory=dict)
    failed: bool = False
    message: str | None = None          # ← 新增（第 4 字段）：段终态文案；
                                        #   None 时主编排用 P3a 默认文案
    extraction: dict[str, Any] | None = None   # ← 新增（第 5 字段）：段内
                                        #   extraction 子统计 {status, completed, total}

@dataclass(frozen=True)
class SectionRunContext:
    ...
    section_progress: Callable[[int, str], None] | None = None
                                        # ← 新增：段内进度回调（见裁定 R-P3b-3）
```

主编排对应改动（都在 `_commit_terminal_states` / `_run_section` 内，行为面不改）：

- 终态 message：`output.message or ("分析失败" if failed else "分析完成")`；
- 终态 stats：把 `output.extraction` 并进该段 stats 的 `extraction` 键（对齐参考 `:2485` / `:2616` 的 `extraction` 子对象）。

## 派发前已核实的前提（不要重新考古，直接引用）

| # | 事实 | 依据 |
|---|---|---|
| P1 | 上传时已解析落盘 markdown：`data_root/projects/{pid}/documents/{document_id}.md`；`file_id` 即 `document_id`（`explicit_id=saved.document_id`）；`file_path` = uploads 副本路径 | `app/api/duplicate.py:375-390` |
| P2 | **上传解析不含图片**：`save_uploaded_document` 调 `parse_document(source_path, provider=provider)`，`include_images` 默认 **False** | `parsing/service.py:55`、`parsing/router.py:51` |
| P3 | P2a 的 markdown 表格是 **GFM 管道形式**（`\| a \| b \|` + `\| --- \|`），client 侧同款（convert.mjs:546-573）→ 参考实现的 cheerio HTML 表格路径在**两端都基本不可达** | `parsing/markdown.py:11-42` |
| P4 | `parse_document(path, provider, include_images)` 是同步函数（非 async） | `parsing/router.py:51` |
| P5 | `_persist_rows` 是 `NotImplementedError` 占位；`_output_failed` 恒读 `output.failed`；`test_failure_branch_is_unreachable_in_p3a` 钉住「P3a 失败分支不可达」 | `duplicate_runner.py:725-737` / `:687-703` |
| P6 | 编排器已把 `content_files` 依赖注入后三段（`extra_ctx={CONTENT_FILES_DEPENDENCY: content_files}`）；`content_output_of()` 是形状解读的唯一收口 | `duplicate_runner.py:124-141` / `:445-450` |
| P7 | `duplicate_check_files` 表有 `content_hash` 列（content_extraction 产物的权威写点在 `replace_content_files`）；`duplicate_check_content_files` 表列：`file_id, status, content_path, content_length, parser_label, error, updated_at` | `schema.py:96`、`repositories/duplicate.py:481-514` |
| P8 | `hashText` = `sha256(utf8).hexdigest()` 全文 | `svc.cjs:266-268` |
| P9 | `engine.cancel`：任务不在 `_running` → `False`；API：任务不存在 404，否则 `{"cancelled": bool}` | `engine.py:118-124`、`api/tasks.py:35-39` |
| P10 | `TaskEngine.submit(project_id=, domain=, kind=, payload=, runner=)`——runner 可注入（I-1 用例用慢 runner 避竞态） | `engine.py:44` |
| P11 | P3a 已登记遗留：cancel happy path、force=True HTTP 直达、`stats_json` 非 dict 加固、`clearDuplicateContentArtifacts`、真并发用例（asyncio.Event） | `progress.md` Task 5/6 各节 |
| P12 | P3a 裁定：段状态由主编排统一落库；`running` 行**不带签名**；幂等判据只认 `status=='success'` | `duplicate_runner.py:270-290` / `:428-431` |

## 核心裁定（实现前必读，冲突时以本节为准）

**R-P3b-1【content_extraction 重新解析，不复用上传 markdown】**
参考在**分析时**逐文件重新解析（`svc.cjs:2318` `parseDocumentWithConfig(app, file.file_path, config, {assetScope, preserveImages: true})`）并把产物写进**独立 content 目录**（`:2322`）。服务端对应：`content_extraction` 角色对每个文件调 `parse_document(Path(file_path), provider="local", include_images=True)`（**同步函数用 `asyncio.to_thread` 包一层**，参考是 await 的异步解析；to_thread 避免阻塞事件循环——真并发用例依赖它），产物写 `data_root/projects/{pid}/duplicate_content/{file_id}.md`。
理由：(a) 上传解析 `include_images=False` 不含图片行，P3c 的 `extractImageOccurrences` 会没有输入；(b) 与参考「分析期独立产物 + 独立目录」语义同形——上传 markdown（documents/）与查重 content（duplicate_content/）生命周期不同，后者随换文件作废。
偏离登记：目录形状 `projects/{pid}/duplicate_content/`（参考是 userData 全局 `duplicate-check-content/`）——file_id 全局唯一，行为等价，路径形状随 server 的 data_root 结构，**登记不追究**。

**R-P3b-2【HTML 表格路径不做】**
`extractContentTextBlocks` 的 cheerio HTML 表格路径（`svc.cjs:1520-1532`）不实现。依据 P3：两端 markdown 管线产的都是 GFM 表格。实现只做 `stripImagesFromMarkdown` → 直通 `extractMarkdownTextBlocks`（含 markdown 表格分支）。若未来某解析器产出 `<table>` 残留，先在 P2a 层转换，不给本模块加死代码。

**R-P3b-3【段内进度走受控回调，禁直接 ctx.progress】**
参考的段内进度（outline `:2469`、content `:2562`/`:2598`）是真实 UX。服务端若让段直接调 `ctx.progress()` 会把任务进度写成段内百分比（5→85），再被主编排的 `overall_progress` 覆盖——非单调。故新增 `SectionRunContext.section_progress(percent, message)`：由主编排的 `_run_section` 注入，内部 = `repo.save_section(status="running", progress=percent, message=message)`（**不带签名**，P3a 裁定 P12）+ 按四段整体重算 `ctx.progress(overall_progress(...), latest_analysis_message(...))`。段实现只调这个回调，不碰 `ctx` 的 progress/log。
注意 `section_progress` 为 `None` 时（P3a 既有测试的 SectionRunContext 直构）回调是 no-op——段实现调用前不必判空（主编排注入的恒非 None，但直接构造的测试上下文可能为 None → 段实现侧 `if section_progress:` 或注入恒存在的 no-op，二选一，实现者在 T5 定并钉住）。

**R-P3b-4【失败语义逐段对齐（本计划最重要的行为差异表）】**

| 段/角色 | 文件级失败 | 段级 status | 参考依据 |
|---|---|---|---|
| `content_extraction` | 逐文件 try → 该文件 `status:'error'` 行，**继续**其余文件 | 任一 error → 整体 `'error'` → `SectionOutput(failed=True)` | `:2336-2346` |
| `outline` | 逐文件 try → error 结果行，继续 | 任一 error → 段 `error`（`SectionOutput(failed=True)`），其余文件结果**保留** | `:2460-2483` |
| `content` | 文件级 catch → **只广播 message，不产 error 行** | **恒 success**（`:2629` 硬编码 `return {status:'success',...}`）→ 永不 failed | `:2587-2629` |
| tender 白名单失败 | outline/content 各广播一条 message 继续（文案逐字见 T5） | 白名单空集继续分析 | `:2425-2430` / `:2518-2523` |
| `readContentMarkdown` 找不到 success 条目 | 抛错，文案**逐字**：「正文内容尚未成功提取，无法进行目录分析」 | 进入上两行的 catch 分支 | `:2405-2410` |

`_output_failed` 的逐文件接线 = 各段 runner 在产出里设 `failed`；P3a 的 `test_failure_branch_is_unreachable_in_p3a` 在 T5 **必须更新**（它就是为此设计的提醒器），更新后的形状：用可控 runner 验证「outline 段任一文件 error → 段终态 error、其余段不受连坐」。

**R-P3b-5【UTF-16 vs 码点：登记偏离】**
JS 的 `str.length` / `slice` / `for` 循环按 UTF-16 码元；Python 按码点。影响点：`splitContentBlockSentences` 的长度计数（`:1717`）、600 字截断（`:1750`）、`splitTenderSentences` 的 6/160 长度窗。BMP 内（含全部 CJK）两者一致，仅 astral 字符（emoji 等）有差。**裁定：Python 按码点实现，登记为已知偏离**（标书正文 emoji 极罕见；改整套 UTF-16 语义代价不成比例）。`bigramSimilarity` 参考用 `Array.from`（码点迭代），Python 码点**恰好一致**——在该函数 docstring 里写明这一点。

**R-P3b-6【`clearDuplicateContentArtifacts` 对齐】**
参考 `saveFiles` 事务外删 content 目录（`duplicateCheckStore.cjs:927` 语义）。服务端：`POST /files` 全量替换成功后（`save_files` 事务提交后）删 `data_root/projects/{pid}/duplicate_content/` 整目录（`shutil.rmtree`，复用 `_remove_tree` 的可观测清理）。这补掉 P3a 遗留登记。测试：上传新文件后断言旧 content 目录不存在（或为空）。

**R-P3b-7【数值舍入统一用 P3a 的 half-up】**
`toFixed(2)` 对应物统一用 `domain/duplicate/` 既有 half-up 约定（P3a T3 的 `rounds_half_up`）；`buildOutlineComparison` 的 score、`buildOutlineItems` 的 confidence 平均值（`:1325`）都用它。

---

## Task 1: domain/outline.py —— 目录纯函数

**Files:**
- Create: `server/app/domain/duplicate/outline.py`
- Create: `server/tests/test_domain_outline.py`

### 行为规格（全部逐字对齐，函数名 = 参考名 snake_case 化）

| 函数（:行号） | 关键行为 |
|---|---|
| `stripMarkdownForOutline` :1134 | 图片→空格；链接保文字；`<[^>]+>`→空格；`` `*_~ `` 剥除；HTML 实体 nbsp/lt/gt/amp |
| `normalizeOutlineTitle` :1146 | stripMarkdown → 去 `#{1,6}\s` 前缀 → 去 `[-*+>]\s` 前缀 → 去**编号前缀**（第X章节篇部分/1.2.3./一、/（一）/①，逐字抄 `:1150` 正则）→ 去全部空白 → 去标点集（逐字抄 `:1152`）→ lower |
| `cleanOutlineTitle` :1156 | 去 markdown/编号前缀（同上两项）→ 去**页尾数字**（`(?:\.{2,}|…{2,}|·{2,}|\s{3,})\s*\d+\s*$` 与 `\s+\d{1,4}\s*$`）→ strip |
| `splitTenderSentences` :1165 | stripMarkdown → `\|`→`\n` → 按 `[。！？!?；;\n]+` 切 → cleanOutlineTitle 洗 → `normalized` 长度窗 `[6,160]` + 去重 → `[{text, normalized}]` |
| `matchTenderSentence` :1185 | normalized<6 → None；相等 → 命中；本方≥10 且被含 → 命中；对方≥10 且含本方且长度比≥0.8 → 命中 |
| `parseOutlineMarker` :1196 | 五种编号模式（逐字抄 `:1199-1203`）；title 清洗后 normalized<2 拒；返回 `{number, title, level}` |
| `inferOutlineLevel` :1216 | `1.2.3`→点分段数；纯数字/第X章/一、→1；（一）/①→2；兜底 1 |
| `isCatalogTitleLine` :1224 | 去空白后 `^(?:#{1,6}\s*)?(目录|目次|contents)$`（ignore case） |
| `parseCatalogLine` :1228 | 去首尾 `\|`、`\|`→空格；分隔行/目录行拒；有编号走 marker；有页尾（`hasPageTrail`）才收无编号行 `{title, level:1}` |
| `extractCatalogOutline` :1239 | 找 `目录` 行起；窗口 180 行；miss≥10（有 items 后）断；confidence **0.92**；`source:'catalog'` |
| `extractHeadingOutline` :1258 | `^(#{1,6})\s+(.+)$`；catalog 标题行拒；`level=min(井号数,6)`；confidence **0.82** |
| `extractSemanticOutline` :1272 | 每行 ≤90 字、非句尾标点、非表行；编号行 confidence **0.68**、粗体行（`^\s*\*\*.+\*\*\s*$`）**0.55**、`level=marker.level or 2`；**截断 260** |
| `buildOutlineItems` :1286 | 三来源优先：`candidates.find(len≥3) or find(len) or []`；level 夹取 `[1,6]` 且 `level > stack+1 → stack+1`；去重 key `f"{level}:{normalized}"`；`path_titles` 父链；`id = O{序号:05d}`；`normalized_path = ">".join`；`from_tender`/`matched_tender_sentence` 来自 matchTenderSentence；返回 `{items, source, confidence(平均 half-up 2位)}` |
| `intersectSize` / `bigramSimilarity` :1328/:1334 | bigram=相邻字符对集合（`Array.from` 码点语义，见 R-P3b-5）；`2*shared/(left+right or 1)`；相等串短路 1 |
| `lcsSimilarity` :1350 | 经典 DP；`lcs/max(len)`；空串 0 |
| `riskFromScore` :1361 | **逐字**：≥0.75 high / ≥0.55 medium / ≥0.35 low / else none |
| `buildOutlineComparison` :1368 | 见下 |

`buildOutlineComparison` 行为分解（:1368-1451）：
1. 只吃 `status=='success'` 的文件结果；跳过 `from_tender` 项。
2. `byPath`（normalized_path）精确组 → `type='duplicate', score=1`；`byTitle`（normalized_title）组里**尚无 duplicate 组的** → `score=0.95`。组 `fileIds<2` 跳过；`id = G{序号:04d}`；回写 `item.duplicate_group_ids`；排序 `(-score, -len(file_ids))`。
3. similar：两两文件 × 未分组项 × **level 差 ≤1** × `bigramSimilarity ≥ 0.86`（**逐字阈值**）；key 去重；`score=round(score,2)`。
4. pairwise：每对文件 → `titleOverlap = titleShared/min(leftN,rightN or 1)`、`pathOverlap` 同式、`orderSimilarity = lcsSimilarity(normalized_title 列表)`；`score = round(pathOverlap*0.45 + titleOverlap*0.35 + orderSimilarity*0.2, 2)`（**逐字权重**）；`shared_count = max(titleShared, pathShared)`；`risk = riskFromScore(score)`。

### TDD 步骤

- [x] **Step 1 写失败的测试**（`test_domain_outline.py`，纯函数直调）：
  1. `test_strip_markdown_for_outline_variants`——图片/链接/HTML/强调/实体五类各一断言。
  2. `test_normalize_outline_title_strips_numbering_and_punctuation`——`1.2.3 技术要求`→`技术要求`、`第一章 总则`→`总则`、`（一）概况`→`概况`、`① 背景`→`背景`；标点/空白/大小写。
  3. `test_clean_outline_title_strips_page_trails`——`项目概述……12`→`项目概述`、`服务要求 8`→`服务要求`。
  4. `test_split_tender_sentences_length_window_and_dedupe`——6/160 窗两侧各一例 + 重复 normalized 只留一条。
  5. `test_match_tender_sentence_exact_contains_and_ratio`——exact / 双向包含（≥10 门槛）/ 0.8 长度比 / <6 拒。
  6. `test_parse_outline_marker_five_patterns_and_reject`——五种编号 + `title` normalized<2 拒。
  7. `test_infer_outline_level_rules`——`1.2.3`→3、`1`→1、`第三章`→1、`（二）`→2、`⑨`→2。
  8. `test_extract_catalog_outline_window_and_miss_limit`——目录行后 180 行窗、连续 10 个非目录行截断、confidence 0.92。
  9. `test_extract_heading_and_semantic_confidence_and_caps`——0.82 / 0.68 / 0.55 三档、semantic 260 截断。
  10. `test_build_outline_items_source_priority_and_stack_levels`——catalog≥3 优先；跳跃 level 夹到 stack+1；`O00001` 形状；from_tender 标记；confidence 平均 half-up。
  11. `test_build_outline_comparison_duplicate_groups_by_path_then_title`——同 path 组 score=1、同 title 组 0.95（且已入 path 组的项不重复入 title 组——`alreadyGrouped` 判据）、组内 file_ids/item_ids/paths 形状、排序。
  12. `test_build_outline_comparison_similar_groups_threshold_and_level_gap`——0.86 阈值两侧 + level 差 ≤1 两侧。
  13. `test_build_outline_comparison_pairwise_formula_and_risk`——构造两文件三列表，断言 score=round(0.45a+0.35b+0.2c,2)、shared_count=max、risk 四档。
  14. `test_bigram_and_lcs_similarity_values`——数值精确断言（含空串 0、相等 1）。
- [x] **Step 2 跑测试确认失败**（ModuleNotFoundError）。
- [x] **Step 3 写实现**（对照上表逐字移植；每个正则加参考行号注释）。
- [x] **Step 4 跑测试确认通过** + 全量 456+新增 零回归。
- [x] **Step 5 变异探针（必做 2 条）**：
  1. `buildOutlineComparison` 的 similar 阈值 `0.86 → 0.99` → 用例 12 **必须红**；还原 `git diff --exit-code`。
  2. pairwise 权重 `0.45 → 0.55` → 用例 13 **必须红**；还原。
- [x] **Step 6 提交**：`feat(server): P3b 目录纯函数——三来源抽取/分组/pairwise/riskFromScore`

---

## Task 2: domain/content.py —— 正文文本块与切句

**Files:**
- Create: `server/app/domain/duplicate/content.py`
- Create: `server/tests/test_domain_content.py`

### 行为规格

| 函数（:行号） | 关键行为 |
|---|---|
| 模块级正则 :108-112 | `markdown_image_pattern` / `html_image_src_pattern` / `html_image_pattern` / `html_table_pattern`（**R-P3b-2：html_table 只定义不用于分流**）逐字移植（JS `gi` flag → Python `re.I | re.S` 注意 `(?<name>)` → `(?P<name>)`） |
| `codePointToString` / `hexCodePointToString` :1460/:1469 | `int(v, 10/16)` + `chr`；越界/非数 → fallback 原串 |
| `decodeBasicHtmlEntities` :1478 | nbsp/lt/gt/quot/apos/amp 顺序**逐字**（amp 在前六者**之后**，`:1485`）+ `&#x…;` 十六进制 + `&#…;` 十进制；解码失败保留原文 |
| `normalizeContentLineBreaks` :1490 | `\r\n`→`\n`、`\r`→`\n` |
| `addContentTextBlock` :1494 | decode 实体 + cleanContentSentence → 按换行拆行逐块收集 |
| `splitMarkdownTableRow` :1534 | 首尾 `\|` 剥除（尾 `\|` 须非转义）、`\\\|`→`\|`、逐字符状态机（`\\` 转义跳过） |
| `isMarkdownTableSeparator` :1560 | 多列且每格 `^:?-{2,}:?$` |
| `isMarkdownTableRow` :1565 | 分列 >1 |
| `cleanMarkdownInlineText` :1569 | 图片三连剥 → 行内代码保文字 → 链接保文字 → `<br>`→`\n` → 块级闭合标签→`\n` → 其余标签→空格 → `**`/`__`/`~~` 剥除；decode 实体 |
| `cleanMarkdownLine` :1584 | cleanMarkdownInlineText + 去 `#{1,6}\s`/`[-*+>]\s` 前缀 + 空白压缩 + trim |
| `extractMarkdownTextBlocks` :1592 | ① 全文剥 ``` 代码块 →② 逐行：**表格三元组检测**（本行是表行 && 下一行是分隔行 → 收集至非表行，分隔行跳过，**每格独立成块**）→③ `standalone`（标题/列表/编号行）/`fieldLine`（`^[^：:\s]{1,18}[：:]`）/`sentenceLine`（句尾标点）→ 立即成块；否则进段落缓冲，空行/上述条件 flush（空格 join） |
| `extractContentTextBlocks` :1644 | stripImagesFromMarkdown → **直通 extractMarkdownTextBlocks**（R-P3b-2：无 HTML 表格分流） |
| `normalizeContentSentence` :1665 | stripLeadingContentSequence → BOM 剥 → 控制字符类剥（逐字抄 `:1668` 的字符类）→ 空白压缩 |
| `stripLeadingContentSequence` :1673 | 七种编号前缀模式（逐字抄 `:1676-1682`）**循环剥**（while changed；每次命中即 restart） |
| `cleanContentSentence` :1699 | BEM/控制字符剥 + `\t `压缩 + 全角空格压缩 + trim |
| `splitContentBlockSentences` :1708 | 逐字符（**码点语义，R-P3b-5 登记**）：强边界 `[。！？!?]` 即切；弱边界 `[；;]` 且**非空白累计 ≥20** 才切；尾部余量成段 |
| `isInformativeContentSentence` :1730 | 空串/纯数字拒；`[A-Za-z0-9\u4e00-\u9fff]` 内容字符 <4 拒；≥12 收；6-11 且含 `：:` 且含 ≥2 连续字母/汉字 收；≥6 且含汉字且含单位词（逐字抄 `:1739` 正则）收 |
| `splitContentSentences` :1742 | extractContentTextBlocks × splitContentBlockSentences × clean/normalize → 非空 + informative 才收；**600 截断**：`sentence[:600] + "..."`（**码点语义偏离登记**）；返回 `[{sentence, normalized}]` |

### TDD 步骤

- [x] **Step 1 写失败的测试**：
  1. `test_decode_basic_html_entities_order_and_numeric`——`&amp;lt;` → `&lt;`（amp 在后→不二次解码）、`&#x4e2d;`→`中`、`&#20013;`→`中`、非法保留。
  2. `test_split_markdown_table_row_escapes_and_edges`——转义 `\|`、首尾管道、空单元格。
  3. `test_markdown_table_triple_becomes_per_cell_blocks`——表头+分隔+两行数据 → 每格独立块、分隔行不出现在任何块。
  4. `test_markdown_blocks_standalone_field_sentence_and_paragraph`——标题行/列表行/编号行/字段行/句尾行各自独立；连续普通行聚成一段（空格 join）；代码块剥除；图片行剥除。
  5. `test_split_content_block_sentences_strong_and_clause_boundary`——强边界即时切；`;` 在累计<20 时**不**切、≥20 切；尾段保留。
  6. `test_is_informative_content_sentence_matrix`——纯数字拒/内容字符<4 拒/≥12 收/短句带冒号收/短句带单位词收/无特征短句拒。
  7. `test_strip_leading_content_sequence_loops_all_patterns`——`1.2 （一）⑨ 内容` 连环剥净（循环 restart 语义）；`第一章 第一节` 同。
  8. `test_normalize_content_sentence_control_chars_and_spaces`——控制字符类、`\u200b`、全角空格。
  9. `test_split_content_sentences_600_truncation_and_filter`——>600 截断成 `...600字...`+`...`；非 informative 被滤。
  10. `test_strip_images_three_pattern_forms`——`![]()`、`<img src=…>`、`<img …>` 三形态。
- [x] **Step 2 确认失败**。**Step 3 实现**（正则逐字移植，`(?<`→`(?P<`，每条带参考行号注释）。**Step 4 全量零回归**。
- [x] **Step 5 变异探针（必做 2 条）**：
  1. `isInformativeContentSentence` 的内容字符门槛 `4 → 40` → 用例 6 **必须红**；还原。
  2. 弱边界累计阈值 `20 → 200` → 用例 5 **必须红**；还原。
- [x] **Step 6 提交**：`feat(server): P3b 正文纯函数——文本块提取(GFM表格)/切句/信息量判据`

---

## Task 3: domain/tender_matcher.py —— 招标五级匹配器与重复句汇总

**Files:**
- Create: `server/app/domain/duplicate/tender_matcher.py`
- Create: `server/tests/test_domain_tender_matcher.py`
- Import: `app.domain.duplicate.content`（`normalizeContentSentence` / `normalize_content_sentence`）

### 行为规格

| 函数（:行号） | 关键行为 |
|---|---|
| `normalizeTenderComparableText` :1756 | normalizeContentSentence → **NFKC** → 转义符剥 → 连字符类归一（`‐‑‒–—―`→`-`）→ 弯引号直引号 → 全角标点归半角（`（），。；：` 等，逐字抄 `:1763-1768`）→ `≥|大于等于|不低于|不少于`→`>=`、`≤|小于等于|不高于|不超过`→`<=`（逐字）→ `N×N` 归一 → 两种日期形态归一成 `YYYY年M月D日`（`:1772-1773`）→ 空白压缩 → **stripTenderTablePrefix** → **stripTenderDirectoryPageTail** |
| `stripTenderTablePrefix` :1782 | 六前缀（技术要求/招标要求/评分标准/评标标准/投标应答/偏离说明）+ 可选序号，**循环剥** |
| `stripTenderDirectoryPageTail` :1801 | 含目录类词才动；剥 `第N页`/`PN[-~至PN]`/省略号页尾/裸数字页尾 |
| `buildTenderStrictKey` / `buildTenderLooseText` :1812/:1819 | normalize → 去全部空白 → 去标点集（逐字抄）→ lower（两函数**当前实现相同**，保留两个名字——参考就是同款两份，别合并成别名） |
| `buildTenderSkeletonKey` :1826 | 日期/编号/万元/元/百分比/分/页码/裸数字 → `{date}/{code}/{money}/{percent}/{score}/{page}/{num}` 占位（**替换顺序逐字**，`{money}` 两种先万元后元）→ 去空白/标点 → lower |
| `isTenderSkeletonAllowed` :1844 | skeletonKey 非空且 ≥8 且含 `{}`；compact 长度 ≥18 收；否则须含评分类词（逐字抄 `:1849`） |
| `normalizeTenderFieldName` :1855 | 去空白/尾部冒号/括号类 |
| `parseTenderFormatField` :1863 | `评分因素及评标标准页码检索` 特例（含可选页码尾）→ `{field, tail}`；`:` 在 1-24 位 → `field:tail`；否则 allow 白名单整行匹配 |
| `isTenderFieldAllowed` :1881 | deny 黑名单（`:1852` 逐字）先拒 → allow 白名单（`:1853` 逐字） |
| `isSafeTenderFieldTail` :1886 | 空尾真；日期/页码/P码/大写编码串真；≤36 且含特定项目名（`:1893` 逐字——**这是参考实现写死的项目专名，逐字移植**） |
| `charBigramsFromLooseText` :1896 | 长度 1 → `{自身}`；否则相邻对集合（码点） |
| `diceSimilarityFromShared` :1905 | `2*shared/max(left+right,1)` |
| `shouldApplyNearTenderMatch` :1909 | loose ≥12 或含评分类词 |
| `buildTenderSourceMatcher` :1914 | 建四集合（exact/strict/skeleton/field）+ entries + **gram 倒排索引**（bigram → entry 序号列表）+ `candidateCounts` 计数数组。`match(sentence)` 五级**短路顺序逐字**：exact → strict（≥3）→ field（allow + 命中 fieldSet + isSafeTenderFieldTail）→ skeleton（allowed + 命中 skeletonSet）→ near。`matchNear`（:1946-1978）：`shouldApplyNearTenderMatch` 门；grams≥4；候选 = gram 倒排并集（candidateCounts 复位语义逐字）；**三阈值两档**（逐字抄 `:1971-1973`）：compact≥30 → `containment≥0.9 && dice≥0.82 && lengthRatio≥0.5`；否则 `≥0.95 && ≥0.88 && ≥0.55`；取 dice 最大者 `{reason:'near', dice, containment, tender}`。返回对象含 `tenderSentenceCount = len(exactSet)` |
| `buildDuplicateSentences` :1999 | `file_ids 长度 > 1` 过滤；排序 `(-len(file_ids), -len(sentence), first_order)`；`id = S{序号:06d}` |

### TDD 步骤

- [x] **Step 1 写失败的测试**：
  1. `test_normalize_tender_comparable_text_pipeline`——全角→半角、`不低于`→`>=`、日期两形态归一、表前缀循环剥、目录页尾剥。
  2. `test_strict_and_loose_keys_normalize_punctuation_case`——同文异形（标点/空白/大小写）→ 同 key。
  3. `test_skeleton_key_placeholder_order_and_allowed_gate`——`2026年1月2日前支付100万元`→`{date}前支付{money}`（万元先于元）；allowed 门（<8、无 `{}`、短文本无评分词各拒一例）。
  4. `test_field_level_whitelist_denylist_and_safe_tail`——`投标日期:2026年1月2日` 命中 field；`联系电话:xxx` 被 deny 拒；allow 白名单外拒；tail 安全各形态。
  5. `test_match_short_circuit_order_exact_strict_field_skeleton_near`——一条句子同时满足多级时只报最先级（构造 exact 与 strict 同判真的句子 → reason='exact'）。
  6. `test_near_match_two_threshold_bands`——compact≥30 宽松档（0.9/0.82/0.5）与 <30 严格档（0.95/0.88/0.55）各一收一拒；grams<4 拒；`shouldApplyNearTenderMatch` 拒。
  7. `test_gram_index_candidate_reset_semantics`——同一候选在多 gram 命中时 shared 计数正确（构造共享多 bigram 的句子对）。
  8. `test_build_duplicate_sentences_filter_sort_and_ids`——单文件句滤掉；排序三级逐级断言；`S000001` 形状。
  9. `test_tender_sentence_count_is_exact_set_size`——重复 normalized 只计一次。
- [x] **Step 2 确认失败**。**Step 3 实现**。**Step 4 全量零回归**。
- [x] **Step 5 变异探针（必做 2 条）**：
  1. near 严格档 dice `0.88 → 0.99` → 用例 6 **必须红**；还原。
  2. `buildDuplicateSentences` 的 `> 1` → `> 2` → 用例 8 **必须红**；还原。
- [x] **Step 6 提交**：`feat(server): P3b 招标匹配器——exact/strict/field/skeleton/near 五级与重复句汇总`

---

## Task 4: content_extraction 角色接线 —— 重解析、content 目录、content_files 落库

**Files:**
- Create: `server/app/infra/tasks/duplicate_sections.py`（段实现模块；本任务先放 `content_extraction` runner）
- Modify: `server/app/infra/tasks/duplicate_runner.py`（`SectionOutput` 加 `message`/`extraction` 字段；`content_output_of` 的形状解读按新产物调整——见下）
- Modify: `server/app/api/duplicate.py`（`POST /files` 成功路径追加 content 目录清理，R-P3b-6）
- Create: `server/tests/test_content_extraction_section.py`

### 行为规格（对齐 `runContentExtraction` svc.cjs:2302-2354）

1. 输入 = `SectionRunContext.files`（tender+bid 全量，参考 `:2315` 是 `allFiles`）。逐文件：
   - `parse_document(Path(file.file_path), provider="local", include_images=True)`（**经 `asyncio.to_thread`**，R-P3b-1）；`markdown = parsed.markdown.strip()`（参考 `:2321` `.trim()`）。
   - 写 `data_root/projects/{pid}/duplicate_content/{file_id}.md`（utf-8；目录不存在则建）。
   - success 行：`{"file_id", "file_name", "status": "success", "content_path", "content_length": len(markdown), "content_hash": sha256(utf8(markdown)).hexdigest()}`（P8）。
   - 解析/写盘异常 → error 行 `{"file_id", "file_name", "status": "error", "error": str(error)}`，**继续**下一文件（参考 `:2336-2342`）。
2. 任一 error → `SectionOutput(failed=True)`（R-P3b-4）；stats 携带 `{"content_extraction": {"status": "error"|"success", "completed": len(results), "total": len(files)}}`（对齐参考 `:2313`/`:2347` 的 `contentExtraction` 子对象，**键名逐字 `content_extraction` 落在我们自己的 stats 命名下**——主 stats 键用 snake_case `content_extraction`，与 `_merge_metadata_stats` 的既有键一致）。
3. 返回 `SectionOutput(rows={"content_files": results}, ...)`。**`content_files` 依赖的形状自此定义**：`content_extraction` 的产出=「results 列表 + content 目录路径」。`content_output_of()` 改为接受本产出（T4 内调整该函数并保持 P3a 测试兼容——P3a 的 stub 透传 `SectionOutput`，新实现仍是 `SectionOutput`，形状兼容，预期只需补 docstring 与可选的 helper）。
4. **上传清目录（R-P3b-6）**：`POST /files` 在 `save_files` 成功后 `shutil.rmtree(duplicate_content 目录)`（复用 `_remove_tree`）。`DELETE /files` 同样清（对齐参考 `deleteFiles` → `clearAnalysisState` 连带语义；实现时核实参考 `duplicateCheckStore.cjs` 的 deleteFiles 是否也删——若参考不删则只做 POST 路径，**以参考为准并登记**）。

### TDD 步骤

- [x] **Step 1 写失败的测试**（构造 `DuplicateAnalysisRunner` + 注入本 runner；docx 夹具用 `tests.parsing_fixtures.build_docx`）：
  1. `test_content_extraction_writes_per_file_content_and_rows`——tender+bid 各一 → content 目录两份 `{file_id}.md`、rows 两行 success、`content_length`/`content_hash` 正确（sha256 手算对照）。
  2. `test_content_extraction_markdown_includes_images`——带图片的 docx → 产出的 content markdown **含图片语法**（与上传解析 documents/ 的差异，R-P3b-1 的判别证据）。
  3. `test_content_extraction_failed_file_does_not_poison_batch`——一个坏文件（如伪造 `.pdf` 走 fail 路径或 monkeypatch `parse_document` 抛）→ 其余文件仍 success、`failed=True`。
  4. `test_upload_clears_duplicate_content_directory`（R-P3b-6）——先跑一次分析产 content 目录 → 重新上传文件 → 目录被清空/不存在。
  5. `test_content_extraction_progress_reports_per_file`——`section_progress` 回调被逐文件调用（回调用测试桩收集调用参数；completed/total 递增）。
- [x] **Step 2 确认失败**。**Step 3 实现**（`duplicate_sections.py` 提供 `build_content_extraction(data_root)` 返回 runner 闭包；`create_app` 的 runner 构造处注入）。**Step 4 全量零回归**（P3a 的 `test_failure_branch_is_unreachable_in_p3a` 若因此变红——**不会**，本任务只动 content_extraction，四段终态仍未接逐文件失败——若红，停下来查清再动）。
- [x] **Step 5 变异探针（必做 1 条）**：`content_extraction` 的 error 行改成「抛出中断整批」→ 用例 3 **必须红**（单文件失败连坐 = 参考 `:2336` 的 catch 被删）；还原。
- [x] **Step 6 提交**：`feat(server): P3b content_extraction 角色——重解析/content 目录/落库/上传清理`

---

## Task 5: outline 与 content 段接线 —— 段 runner、_persist_rows、段内进度

**Files:**
- Modify: `server/app/infra/tasks/duplicate_sections.py`（+`build_outline_runner` / `build_content_runner`）
- Modify: `server/app/infra/tasks/duplicate_runner.py`（`_persist_rows` 实现；`_run_section` 注入 `section_progress`；`_commit_terminal_states` 用 `output.message`/`output.extraction`）
- Create: `server/tests/test_outline_content_sections.py`

### 行为规格

**outline 段（对齐 `runOutlineAnalysis` svc.cjs:2412-2502）**：
1. tender 白名单：`readCombinedTenderMarkdown` = 拼 tender 各文件的 content markdown（从 `content_files` 上游找 success 条目读 `content_path`；`\n\n` join，`:2356-2363`）→ `split_tender_sentences`；读不到/失败 → 广播 message「招标文件句子白名单生成失败，继续对比投标文件目录：{error}」（**逐字** `:2426`）继续（空白名单）。
2. 逐 bid 文件：`read_content_markdown`（找不到 success 条目 → 抛「正文内容尚未成功提取，无法进行目录分析」**逐字** `:2408`）→ `build_outline_items(markdown, tender_sentences)` → 结果行 `{file_id, file_name, status:'success', source, confidence, item_count, tender_matched_count, items}`；异常 → error 行（`items: []`），继续。
3. 逐文件后 `section_progress(round((completed/total)*80), f"目录提取 {completed}/{total}")`（参考 `:2469`/`:2474`；total=0 → 80，逐字 `:2469` 的三元式）。
4. `build_outline_comparison(successful)` → groups + pairwise；产出：
   - `rows = {"outline_items": [逐项展开 items（每项带 file_id）], "outline_groups": [...], "outline_pairwise": [...]}`
   - `failed = any(文件 error)`；`message = "部分文件目录分析失败" if failed else "目录分析完成"`（**逐字** `:2483`）
   - `extraction = {"status": ..., "completed": len(results), "total": len(bid_files)}`（对齐 `:2485`）
   - stats = `{"tender_sentence_count", "tender_matched_item_count", ...}`（对齐 `:2487-2488`）
5. **items 展开为落库行**：`item_id` 用 scoped 形式 `{file_id}::{item.id}`（P3a `replace_outline_items` docstring 的 scoped 约定）；`parent_item_id` = `{file_id}::{item.parent_id}`；`path_titles` → `path_titles` 键（仓储转 json）；`order` → `sort_order`；`from_tender` 布尔。

**content 段（对齐 `runContentDuplicateAnalysis` svc.cjs:2504-2630）**：
1. tender matcher：`readCombinedTenderMarkdown` → `split_content_sentences` → `build_tender_source_matcher`；失败 → 广播「招标文件句子白名单生成失败，继续比对投标正文：{error}」（**逐字** `:2519`）。
2. 逐 bid 文件（按序 `first_order` 全局递增）：`split_content_sentences(markdown)` → 逐句 `matcher.match(sentence)`：命中 → `tender_matched_count` + reason 计数；未命中 → local map `{normalized: {sentence, count, order}}`。
   - **每 500 句** `section_progress(min(89, round(5 + ((file_index + processed/len(sentences))/bid_count)*80)), f"正文比对 {file_index+1}/{bid_count}（{processed}/{len(sentences)} 句）")`（**逐字公式** `:2562`/`:2567`）+ `await asyncio.sleep(0)` 让出（对应参考 `:2570` setImmediate）。
   - 文件级 **catch → 只广播 message**（`section_progress` 传当前进度 + 「{file_name} 正文比对失败：{error}」，参考 `:2588`），**不产 error 行、不置 failed**（R-P3b-4）。
   - 文件尾：local → global（`file_ids` 追加去重、`occurrences[file_id]=count`、`first_order` 取首个 order）；`section_progress(round((10 if global.size else 5) + (file_index+1)/bid_count*80), f"正文比对 {file_index+1}/{bid_count}")`（**逐字** `:2598`）。
3. `build_duplicate_sentences(global)` → 产出：
   - `rows = {"content_duplicates": [{"duplicate_id": s.id, "sentence", "normalized", "file_ids", "first_order"}], "content_occurrences": [{"duplicate_id", "file_id", "occurrence_count"} for 每个 global 项×每个 file_id]}`
   - `message = "正文比对完成"`（`:2611`）；`failed = False`（恒）；`extraction = {"status": "success", "completed": bid_count, "total": bid_count}`；stats = `{"tender_sentence_count", "tender_matched_sentence_count", "total_sentence_count"}`（`:2613-2615`）+ `tender_match_reason_counts`。

**`_persist_rows`（编排层，本任务实现）**：

| rows 键 | 落库调用 |
|---|---|
| `content_files` | `replace_content_files(project_id, rows)` |
| `outline_items` / `outline_groups` / `outline_pairwise` | `replace_outline_items` / `replace_outline_groups` / `replace_outline_pairwise` |
| `content_duplicates` + `content_occurrences` | `replace_content_duplicates(project_id, rows, occurrences)` |

`_commit_terminal_states` 的调用顺序：签名校验（既有）→ 逐段 `_save_section`（用 `output.message`/`extraction`）→ `_persist_rows`。**落库失败的表现**：任一 replace 抛 → 上抛 → `__call__` 顶层兜底把未终态段收 error（P3a F1 语义）——不需要新兜底。

**`section_progress` 注入**：`_run_section` 构造 `SectionRunContext` 时补 `section_progress=lambda percent, message: self._save_section(ctx, project_id, section对应的状态段名, status="running", progress=percent, message=message)`。**键映射**：`content_extraction`/`metadata_extraction` → 不注入（None；它们没有状态行）→ `outline`/`content`/`image` → 同名段。注意 content_extraction 的进度报告（T4 用例 5）需要改道：content_extraction 角色的进度走 `ctx.log`+`ctx.progress`？——**不**。参考里 contentExtraction 的进度写在 `metadataAnalysis.stats`（经 `_merge_metadata_stats`），**不产生进度行**。T4 用例 5 的 `section_progress` 桩改为「runner 产出后断言 stats.content_extraction.completed == total」——**修正 T4 的用例 5 为 stats 断言**（实现 T4 时按此执行，Test 5 名改为 `test_content_extraction_stats_reports_completed`）。

### TDD 步骤

- [x] **Step 1 写失败的测试**：
  1. `test_outline_section_extracts_groups_and_persists`——两份投标 docx 含相同目录标题 → 段 success；`outline_items`（scoped id、父子、from_tender）、`outline_groups`（score=1 的 path 组）、`outline_pairwise`（score/risk）全部落库可读（经 `repo` 直查）。
  2. `test_outline_section_tender_whitelist_failure_continues`——tender 的 content 条目缺失 → 广播文案逐字断言（经 `client.app.state.yibiao.logs` 或段 stats/message 桩）、分析继续。
  3. `test_outline_section_file_error_marks_failed_and_keeps_rest`——一个 bid 的 content 条目缺失（抛「正文内容尚未成功提取，无法进行目录分析」）→ 该文件 error 行、`failed=True`、其余文件 items 保留。
  4. `test_content_section_duplicate_sentences_persisted`——两份投标 docx 含同一句 → `content_duplicates` 一行、`file_ids` 两元素、`content_occurrences` 两行（count≥1）；`failed=False`。
  5. `test_content_section_tender_sentences_are_not_duplicates`——招标含句 S、两投标都含 S → matcher 命中 → S 不出现在 duplicates。
  6. `test_content_section_file_error_keeps_status_success`——一个 bid 的 content 条目缺失 → 该文件被跳过（message 广播）、`failed=False`、其余文件的重复句照常产出。
  7. `test_section_progress_running_rows_without_signature`——大文件（构造长正文触发 500 句粒度或直接小 batch 断言文件尾回调）→ 段状态行出现 `running` 行且 `signature` 为 NULL（P3a 裁定 P12）；终态后为 success + 签名。
  8. `test_persist_rows_is_implemented_now`——`DuplicateAnalysisRunner._persist_rows` 对带 rows 的产出不再抛 `NotImplementedError`（P3a 占位正式退役）。
  9. `test_terminal_message_from_output`——outline 全成 → 终态 message「目录分析完成」（**不是** P3a 的统一文案「分析完成」）；部分失败 →「部分文件目录分析失败」。
- [x] **Step 2 确认失败**。**Step 3 实现**。**Step 4 全量零回归**：
  - **`test_failure_branch_is_unreachable_in_p3a` 必须更新**（R-P3b-4：它就是提醒器）——新形状：可控 runner 令 outline 段产 `failed=True` → 编排器写 outline=error、**content/image 段不受连坐**（各自 success）；改名 `test_section_failure_marks_only_own_section`。
  - `_merge_metadata_stats` 按 `extraction` 字段把 `content_extraction` 子统计填实（P3a docstring 预留点，`duplicate_runner.py:493-512`）——补断言进用例 1。
- [x] **Step 5 变异探针（必做 2 条）**：
  1. content 段的文件级 catch 改成「置 failed=True」→ 用例 6 **必须红**（R-P3b-4 的判别核心：content 恒 success）；还原。
  2. `_persist_rows` 的 `content_occurrences` 分支注释掉 → 用例 4 **必须红**（occurrences 表空）；还原。
- [x] **Step 6 提交**：`feat(server): P3b outline/content 段接线——段runner/_persist_rows/段内进度/终态文案`

---

## Task 6: 编排杂项与遗留清单（P3a 登记的承接）

**Files:**
- Modify: `server/app/infra/tasks/duplicate_runner.py`（如需微调）
- Modify: `server/app/infra/repositories/duplicate.py`（`_load_json` 非 dict 加固）
- Modify: `server/tests/test_api_tasks.py`（cancel happy path）
- Modify: `server/tests/test_duplicate_runner.py`（真并发用例）
- Modify: `server/tests/test_api_duplicate_files.py`（force=True、旧形状兼容）
- Create: 无

### 清单（每项独立提交或合并提交均可，但**逐项可判别**）

1. **真并发用例（P3a F4 登记）**：注入可控 runner——content_extraction  runner 卡在 `asyncio.Event` → 断言 outline 段的 running 行已出现（或 content_extraction 未完成时后三段已在等）→ 释放 → 全部 success。**判别**：串行化变异（把 `ensure_future` 改裸 await）必须让该用例红。
2. **cancel HTTP happy path（I-1）**：`test_api_tasks.py` 补「注入慢 runner（`engine.submit(..., runner=slow)` 或等价缝）→ `POST /tasks/{id}/cancel` → 200 `{"cancelled": true}` → 轮询终态 `cancelled`」。慢 runner 的注入缝实现者先核实 `create_app` 的 runner 构造（`AppState`/`engine` 装配处），若无缝则给 `create_app` 加可选参数（默认不变）。附带断言：不存在的任务 404、已完成的任务 `{"cancelled": false}`。
3. **force=True HTTP 直达（M-3）**：`test_api_duplicate_files.py` 补「分析一次 → `POST /analysis {"force": true}` → 新任务日志**不含**「无需重复分析」且重跑到 success」。
4. **`stats_json` 非 dict 加固（Task 5 评审 B 登记）**：`repositories/duplicate.py` 的 `_load_json`（或 stats 读取点 `:826`/`:1062`）对非 dict JSON 回落 `{}`；补一条直写标量 `stats_json` → `GET /state` 不 500 的用例。
5. **旧形状行兼容钉子（Task 5 复审登记）**：补「`file_path` 为无 `\x1f` 的旧形状 → `_error_of` 返回 None（或既有解码行为）」的直调用例，把兼容性钉成显式契约。

### TDD 步骤（每项：先红后绿，探针按判别点自选，还原判据同全局约束）

- [x] Step 1-6 同前格式；提交：`chore(server): P3b 编排收口——真并发用例/cancel HTTP/force 直达/stats_json 加固/旧形状钉子`

---

## Task 7: e2e 升级与收尾回填

**Files:**
- Modify: `server/tests/test_duplicate_e2e.py`（升级断言）
- Modify: `docs/superpowers/specs/2026-09-15-fastapi-check-service-design.md`（§15 P3b 行）
- Modify: 本计划（勾选）

### 内容

1. **e2e 升级**：现有 4 条用例的闭环升级为真实算法——用例 1 补断言「`GET /state` 的 outline 段 stats 含 `tender_sentence_count`、content 段 stats 含 `total_sentence_count`」；新增 `test_e2e_duplicate_sentences_and_outline_groups_reachable`：两份含相同句子与相同目录标题的投标文件 + 一份招标 → 分析后 `GET /state`：outline 组/pairwise 可经 `load_analysis` 读出、content 重复句 ≥1。**若 TestClient 下真实解析+比对太慢（>10s），允许在 e2e 中用更小的 docx 或标注 slow**——如实登记实际耗时。
2. **spec §15 回填**：P3b 行 → `**已完成**（N 提交 / M 测试）` + 执行期裁定回填（R-P3b-1/2/5 的偏离登记）。
3. **全量留底 + 提交**：`docs(server): P3b 完成回填 spec §15 与本计划勾选`

---

## Self-Review

**Spec 覆盖检查：**

| Spec §9.2 / §15 条目 | 落在哪个任务 |
|---|---|
| 目录抽取三来源 + 分组 + riskFromScore | Task 1 |
| 切句口径（splitContentBlockSentences / isInformativeContentSentence / 600 截断） | Task 2 |
| 五级匹配（exact/strict/field/skeleton/near）+ containment/dice/lengthRatio | Task 3 |
| content_extraction 接入（runContentExtraction 对应物） | Task 4 |
| 目录/正文段落库（outline_items/groups/pairwise、content_duplicates/occurrences） | Task 5 |
| 段内进度、终态文案、失败语义 | Task 5（R-P3b-3/4） |
| P3a 遗留承接（并发/cancel/force/stats_json/旧形状/content 目录清理） | Task 4（R-P3b-6）+ Task 6 |
| e2e 真实算法闭环 | Task 7 |

**已知空白（实现者必须在报告如实说明，不得掩盖）：**

1. **UTF-16 vs 码点**（R-P3b-5）：600 截断、长度窗、切句长度计数对 astral 字符（emoji）与参考有差。登记偏离，不做 UTF-16 仿真。
2. **HTML 表格路径不实现**（R-P3b-2）：两端 markdown 管线均为 GFM 表格；`svc.cjs:1520-1532` 的 cheerio 路径在两端同为事实不可达。若评审实测出反例（构造含 `<table>` 的 markdown 输入），该路径按参考补齐并更新本裁定。
3. **`isSafeTenderFieldTail` 写死的项目专名**（`svc.cjs:1893`「天津港保税区消防救援支队…」）：这是参考实现**当前部署**的硬编码。逐字移植（行为对齐优先），并在 docstring 登记其在通用场景下的局限——**不改**。
4. **段状态文案差异**：段内 running message 逐字对齐；**终态** message 用段的产出文案（T5），与参考的段自写文案一致，但**写入时机**由主编排统一（P3a 架构），SSE 事件序列因此与参考的逐次 update 不同——spec §10 的事件面以 P1/P3a 已验收形状为准，不在本计划改。
5. **`parse_document` 同步阻塞**：经 `to_thread` 化；「真并发」的判别用例（Task 6.1）依赖此。若实测 to_thread 的线程池调度使并发用例不稳定，允许退回「段级并发由编排器的 gather 保证」并如实登记。
6. **content 段 `tender_match_reason_counts`** 的 stats 键形状（参考是 JS 对象）→ Python dict，键为 reason 字符串，逐字对齐（exact/strict/field/skeleton/near/unknown）。

**已核实、不需再猜的接口事实：**见「派发前已核实的前提」表 P1-P12。
