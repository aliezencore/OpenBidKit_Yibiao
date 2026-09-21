# docx 图片占位接线（P2a↔P2b）实施计划

> **状态：已完成（2026-09-21）** —— 4 个 Task 全部落地，519 → **544 测试**；
> 4 条任务各有 2 条判别力探针（共 8 条）全部「探针态红 / 还原后绿」。
> 提交区间 `6b03f92`（本计划）→ Task 1/2/3 各一个 commit → 本回填。
> spec §15 的「P2a↔P2b 未接线」缺口块已标记**已闭合**；P2a 侧裁定 R2 的撤销
> 记录在 `parsing/docx.py` / `parsing/docx_html.py` 的 docstring 里（P2a 计划
> 保留原样作历史记录，指针在 spec §15）。
>
> **本次未做（属 P3c/P4，计划里已列）**：图片出现位置抽取、跨文件按 hash 分组、
> `preview_url` 的 HTTP 形态、把图片喂给视觉模型。
>
> **夹具决策的两处例外（如实记录）**：①`test_duplicate_e2e.py` 的
> `test_e2e_duplicate_sentences_and_outline_groups_reachable` **保留 txt**——它的断言
> 是**精确句数**（`totalSentenceCount == 4`），换格式会让断言与解析器细节耦合而收益为零；
> ②其余 e2e 与签名单测夹具已全部回归 docx（含图片）。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 撤销 P2a 裁定 R2 的「`include_images=True` 显式拒绝」，把 P2b 已有的图片资产能力接到 docx 解析路径上——产出**带图片占位的 markdown**并把资产落到 `<data_root>/projects/<project_id>/assets/`，使「上传 docx → 查重正文提取」在生产路径下不再必 error，并为 P3c 的图片比对备好可解析的 target。

**Architecture:** 三处最小改动 + 一个解析侧助手：
1. `docx_html.docx_to_html` 加 `include_images` 开关，图片模式下用 mammoth 的 `convert_image` 回调把 `<img src>` 写成 **`assets/<sha256>`** 占位；
2. `docx.py` 把开关透传下来（`include_images=False` 路径**逐字节不变**）；
3. `asset_store` 补一对**共轭函数**：`asset_target(data)`（生成 target）与 `resolve_asset_path(...)`（把 target 解析回磁盘文件），两者是同一契约的两端；
4. `content_extraction` 段（P3b 产物，已持有 `data_root` + `project_id`）在 `include_images=True` 且文件是 docx 时调 `save_docx_assets()` 落盘。

**Tech Stack:** 既有 `mammoth`（P2a 已有依赖）+ 既有 P2b 模块（`docx_assets` / `asset_store` / `image_codec` / `image_plan`）。**不新增任何依赖。**

**Spec:** `docs/superpowers/specs/2026-09-15-fastapi-check-service-design.md` §5.3（存放布局）、§8.3 / §8.4.1（图片管线）、§8.5（结构等价验收）、§15 的「跨阶段缺口登记 · P2a↔P2b 未接线」。

**明确不做（属 P3c / P4）**：图片出现位置抽取（`extractImageOccurrences` 的目录栈 + 前句上下文）、按 hash 跨文件分组与 `occurrence_count`/`locations_json`、`duplicateImages` 的 `preview_url`（前端可访问的 HTTP 形态）、把图片喂给视觉模型。本计划只把「**markdown 里有占位、盘上有资产、两者按哈希对应**」这件事做完。

## Global Constraints

- 工作区 `.worktrees/fastapi-check-service`，分支 `fastapi-check-service`（**平铺名，不得含 `/`**）。
- **不得改动 `client/` 下任何文件**。
- **不得破坏既有 519 个测试**；尤其 P2a 的解析全部 golden（`include_images=False` 路径）**必须逐字节不变**。
- 提交身份 `git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit`；**不推送**；按显式路径 `git add`。
- `app/domain/**` 禁止 import 框架（护栏测试强制）；Pillow 只能出现在 `app/infra/parsing/`。
- **不新增依赖。**
- 验收是**结构等价**（§8.5）：图片**占位数量与位置**一致；**不要求** target 字符串与参考的 `yibiao-asset://` 相同（§15 的 P3c 行已明示新管线改用资产相对路径）。
- 逐字文案：不得改写既有错误信息。

## 已核实的前提（实测，不需要再猜）

| # | 事实 | 证据 |
|---|---|---|
| W1 | `mammoth.images.img_element(func)` 的 `func(image)` 返回**属性 dict**（`alt` 由 mammoth 自动补，`image.alt_text` 为 None 时**不加 alt**） | `mammoth/images.py` 源码 |
| W2 | `image` 对象暴露 `alt_text` / `content_type` / `open()`；`open()` 返回 **`contextlib.closing`**，**必须 `with` 使用**（直接 `.read()` 会 `AttributeError: 'closing' object has no attribute 'read'`） | 实测（首版脚本踩过） |
| W3 | **回调里对 `image.open()` 读出的字节算 sha256，与 `docx_assets.list_media_files` 的 `content_hash` 逐字节一致** | 实测：3 次回调（img1 插两次）、2 个媒体文件，哈希一一对上 |
| W4 | 同一张图插多次 → 回调被调多次、**哈希相同** → 占位 target 天然相同（对齐「同 hash 共享一份资产」的既有约定） | 同上 |
| W5 | `tests/parsing_fixtures.py` 已有 `make_image(path, *, size, color, fmt)` 与 `build_docx_with_images(path, *, blocks, images, image_paragraph=True)` | P2b 产物 |
| W6 | `docx_html.docx_to_html(path)` 当前把**所有** `<img>` `decompose()` 掉（裁定 R14：不带 `include_images` 参数） | `parsing/docx_html.py` |
| W7 | `asset_store.save_docx_assets(*, data_root, project_id, docx_path, max_edge, max_passthrough_bytes)` 按 `content_hash` 去重、原子落盘（temp + `os.replace`）→ 重复调用幂等 | P2b Task 8 |
| W8 | 落盘扩展名取自**准备结果**（`compress`/`transcode` → `.png`/`.jpg`；`skip` → 原扩展名）→ **markdown 侧无法预知扩展名** | `asset_store._asset_extension` |
| W9 | `content_extraction` 的 runner 是 `duplicate_sections.build_content_extraction(data_root)`，**已持有 `data_root`**；它逐文件 `try/except` 兜底（R-P3b-4），落资产失败会自然降级为该文件 error 行 | P3b Task 4 |
| W10 | `parse_document(path, *, provider="local", include_images=False)` → `_parse_locally` → docx 分支 | `parsing/router.py` |

## 裁定

- **R-DW-1 — 占位 target 形态 = `assets/<sha256>`（不带扩展名）。**
  target 的职责只是**标识同一份资产**，哈希即身份。带扩展名不行：W8 说明扩展名由 `save_docx_assets` 在 compress/transcode **之后**才确定，markdown 生成侧要么重复跑一遍 Pillow（贵）要么猜（会在转码时指向不存在的文件）。绝对路径也不行：会把服务器路径写进 markdown（会随 content 文件外泄）。
- **R-DW-2 — 哈希对齐「按构造」而非映射表。** 因为 W3（回调字节 == 媒体文件字节，同一算法），markdown 的 target 与落盘文件名天然一致，**不需要任何 rId→hash 映射**。这也是本方案比「先落盘再按 occurrence 回填占位」更稳的地方：占位由 mammoth 放在**它自己认为的位置**，不存在「段落索引 ↔ markdown 行号」的对齐问题。
- **R-DW-3 — `include_images=False` 逐字节不变。** 仍走 W6 的「空 src → decompose」，P2a 的解析 golden 不受影响。这是本任务的硬红线。
- **R-DW-4 — 落盘归 `content_extraction` 段，`parse_document` 保持纯函数。** `parse_document` 只读源文件、只返回 markdown（P2a 的既有契约）；`save_docx_assets` 需要 `data_root` + `project_id`，而这两个只有段 runner 有（W9）。
- **R-DW-5 — 非 docx 格式：`include_images=True` 仍是 no-op。** P2b 的资产能力只覆盖 docx（对齐参考的 `docxVisualAssetExtractor`）；pdf/xlsx/txt/md 不受影响。
- **R-DW-6 — 页眉页脚图片不进 markdown。** `extract_docx_visual_assets` 收正文 + 页眉页脚的 occurrence，但 mammoth 只转换正文 → markdown 占位只含正文图片。与参考一致（参考的 markdown 同为 mammoth 系产出）；P2b 的 occurrence 面保持完整，供 P3c/P4 使用。
- **R-DW-7 — 落资产失败 = 该文件 error 行，不连坐。** 由 `content_extraction` 既有的逐文件 catch 兜住（W9），不为此加特判。

## File Structure

| 文件 | 改动 |
|---|---|
| `server/app/infra/parsing/asset_store.py` | **增** `asset_target(data) -> str` 与 `resolve_asset_path(*, data_root, project_id, target) -> Path \| None` |
| `server/app/infra/parsing/docx_html.py` | **改** `docx_to_html(path, *, include_images=False)`；图片模式保留 `<img>` 并写占位 src |
| `server/app/infra/parsing/docx.py` | **改** `docx_to_markdown(path, *, include_images=False)` 透传开关；撤销 R2 的显式拒绝 |
| `server/app/infra/tasks/duplicate_sections.py` | **改** `content_extraction`：docx + `include_images` 时落资产 |
| `server/tests/test_parsing_docx_images.py` | **建** 解析侧测试（占位形态/位置/去重/回归） |
| `server/tests/test_asset_store.py` | **改** 补 target/解析 的共轭测试 |
| `server/tests/test_content_extraction_section.py` | **改** 图片用例改真 docx（撤掉桩）、补「markdown 的 target 都能解析」钉子 |
| `server/tests/test_duplicate_e2e.py`（或 `test_api_duplicate_files.py`） | **改** 夹具回归 docx（含图片） |
| `docs/superpowers/specs/2026-09-15-fastapi-check-service-design.md` | **改** §15 缺口块标记已闭合 + 裁定 R2 更新 |

---

### Task 1: 占位 target 的共轭函数（纯函数，先立契约）

**Files:**
- Modify: `server/app/infra/parsing/asset_store.py`
- Test: `server/tests/test_asset_store.py`

**Interfaces:**
- Produces：`ASSET_TARGET_PREFIX = "assets/"`；`asset_target(data: bytes) -> str`（→ `assets/<sha256hex>`）；`resolve_asset_path(*, data_root: Path, project_id: str, target: str) -> Path | None`
- Consumes：`validate_project_id`（同一道净化守卫）、`path_safety` 的目录包含判定（若已存在可复用的助手则复用，否则与 `service.py` 同款实现）

**语义（逐条钉住）**：
- `asset_target(b"")` 也要返回合法串（空文件不是合法媒体，但**不能抛**——沿 P2b 的 F1 教训：伪资产应被**上游**的 `list_media_files` 过滤，这里不额外加特判，避免两处规则漂移）。
- `resolve_asset_path` 只接受**以 `assets/` 开头、去掉前缀后是 64 位十六进制**的 target；其余（`yibiao-asset://…`、`http(s)://…`、`assets/../../etc/passwd`、`assets/abc`）一律返回 `None`——**不抛**。
- 解析是在 assets 目录里找 `<hash>.*`（扩展名由落盘时决定，见 W8）：**唯一命中**才返回，0 个或多个（理论上不会）返回 `None`。
- `project_id` 必须过净化守卫（失败抛 `ParsingError`，与 `save_docx_assets` 同款）。

- [x] **Step 1: 写失败的测试**（`server/tests/test_asset_store.py` 追加）
  - `test_asset_target_is_content_hash_under_assets_prefix`：`asset_target(b"x") == f"assets/{hashlib.sha256(b'x').hexdigest()}"`。
  - `test_asset_target_matches_stored_filename`：造 1 张图 → `save_docx_assets`；断言 `asset_target(media_data)` 的哈希段 == 落盘文件的 stem（**这是全任务最关键的一条**：证明「生成侧」与「落盘侧」按构造对齐，W3）。
  - `test_resolve_asset_path_finds_file_regardless_of_extension`：手工在 `assets/` 放 `abc….png` 与 `abc….jpg` 两种，各用自己的哈希 target 解析成功。
  - `test_resolve_asset_path_rejects_foreign_and_escaping_targets`：参数化 `["yibiao-asset://x/y.png", "http://a/b.png", "assets/../secret", "assets/not-a-hash", "", "assets/A"*1]` → 全部 `None`。
  - `test_resolve_asset_path_returns_none_when_missing`：合法靶但盘上无文件 → `None`。
  - `test_resolve_asset_path_rejects_bad_project_id`：`project_id=".."` → `ParsingError`。
- [x] **Step 2: 跑测试确认失败**（`ImportError`/`AttributeError`）。
- [x] **Step 3: 写实现**（含 docstring 行号/来源注释；`resolve_asset_path` 的 glob 用 `assets_dir.glob(f"{hash}.*")` 并 `sorted()` 取唯一）。
- [x] **Step 4: 跑测试确认通过** + 全量 519+新增 零回归。
- [x] **Step 5: 变异探针（必做 2 条）**
  - 探针 A：把 `asset_target` 的哈希算法改成 `sha1` → `test_asset_target_matches_stored_filename` **必须红**。
  - 探针 B：把 `resolve_asset_path` 的前缀校验去掉（直接当成文件名拼）→ `test_resolve_asset_path_rejects_foreign_and_escaping_targets` **必须红**。
  - 还原后 **grep 验证**。
- [x] **Step 6: 提交**：`feat(server): 资产 target 共轭函数——asset_target/resolve_asset_path`

---

### Task 2: docx 解析产出占位（撤销 R2 的拒绝）

**Files:**
- Modify: `server/app/infra/parsing/docx_html.py`、`server/app/infra/parsing/docx.py`
- Test: `server/tests/test_parsing_docx_images.py`（新建）

**Interfaces:**
- `docx_to_html(path, *, include_images: bool = False) -> str`
- `docx_to_markdown(path, *, include_images: bool = False) -> str`
- Consumes：`asset_store.asset_target`

**行为**：
- `include_images=False`：**与现状逐字节相同**（R-DW-3）。
- `include_images=True`：`convert_image` 读字节 → `{"src": asset_target(data)}`；**不再 decompose**；`ParsingError`（`docx-conversion-failed`）拦截逻辑保持不变。
- `docx_to_markdown` 的 include_images=True 分支把占位交给既有的「摘表格 → turndown → 回填 → normalize」流水线，**不新增后处理**（占位是标准 markdown 图片语法，turndown 会原样保留）。

- [x] **Step 1: 写失败的测试**（新建 `test_parsing_docx_images.py`）
  - `test_markdown_without_images_is_unchanged`：同一 docx，`include_images=False` 的 markdown **不含** `<img`、不含 `assets/`（回归钉子）。
  - `test_markdown_with_images_has_one_placeholder_per_occurrence`：2 张不同图 + 其中 1 张插两次 → 占位 **3 个**、`![](assets/<hash>)` 形态、**位置**在图片所在段落（顺序断言）。
  - `test_same_image_twice_shares_one_target`：3 个占位里只有 **2 个不同** target（W4）。
  - `test_placeholder_hash_equals_media_content_hash`：占位里的哈希 ∈ `{m.content_hash for m in list_media_files(docx)}`（**跨模块一致性**，W3）。
  - `test_non_docx_include_images_is_noop`：`.txt` + `include_images=True` → 无 `assets/`、无异常（R-DW-5）。
  - `test_tables_and_headings_still_intact_with_images`：带图 + 表格 + 标题的 docx → 表格行列数与标题层级仍正确（**图片不该破坏既有解析**）。
- [x] **Step 2: 确认失败**。**Step 3: 实现**。**Step 4: 全量零回归**（**P2a 的 golden 必须全绿**——这是本任务的红线）。
- [x] **Step 5: 变异探针（必做 2 条）**
  - 探针 A：`include_images=True` 时仍 `decompose()` 掉图片 → `test_markdown_with_images_has_one_placeholder_per_occurrence` **必须红**。
  - 探针 B：把回调的哈希换成图片尺寸（如 `f"{width}x{height}"`）→ `test_placeholder_hash_equals_media_content_hash` **必须红**。
- [x] **Step 6: 提交**：`feat(server): docx 带图解析——mammoth 回调产出 assets/<hash> 占位（撤销 R2 拒绝）`

---

### Task 3: content_extraction 落资产

**Files:**
- Modify: `server/app/infra/tasks/duplicate_sections.py`
- Test: `server/tests/test_content_extraction_section.py`

**行为**：`content_extraction` 跑某文件时，若 `include_images` 为真且扩展名是 `docx` → `save_docx_assets(data_root=..., project_id=..., docx_path=Path(file_path))`。非 docx 跳过（R-DW-5）。失败由既有逐文件 catch 兜成 error 行（R-DW-7）。

- [x] **Step 1: 写失败的测试**
  - 把现有的桩用例（`fake_parse` 返回 `![图1](asset://img1.png)`）**换成真 docx**：`build_docx_with_images` 造文件 → 真跑 `content_extraction` → 断言 ① markdown 里有占位；② **每个 target 都能被 `resolve_asset_path` 解析到存在的文件**（这是「接线真的接上了」的判别点）；③ `assets/` 目录里的文件数 == 不同哈希数。
  - 新增 `test_content_extraction_skips_asset_storage_for_non_docx`：`.txt` + include_images=True → 不创建 assets 目录、不抛。
  - 新增 `test_content_extraction_asset_failure_is_per_file`：`monkeypatch` `save_docx_assets` 抛异常 → 该文件 error 行、**其余文件仍 success**、段 `failed=True`（不连坐，R-DW-7）。
- [x] **Step 2: 确认失败**。**Step 3: 实现**。**Step 4: 全量零回归**。
- [x] **Step 5: 变异探针（必做 2 条）**
  - 探针 A：删掉落资产那一步 → 「每个 target 都能解析」的用例 **必须红**。
  - 探针 B：把落资产的 `try` 拿掉、让异常冒泡到整批 → `test_content_extraction_asset_failure_is_per_file` **必须红**。
- [x] **Step 6: 提交**：`feat(server): content_extraction 落图片资产——docx+include_images 时按 hash 落盘`

---

### Task 4: 夹具回归 docx + 收尾回填

**Files:**
- Modify: `server/tests/test_duplicate_e2e.py`（或 `test_api_duplicate_files.py`）、`docs/superpowers/specs/2026-09-15-fastapi-check-service-design.md`、本计划

**内容**：
1. **夹具改回 docx**：P3b Task 4 曾因「docx 必 error」把 e2e 夹具临时 txt 化——现在**撤销该规避**，至少留一条用例走 docx + 图片，并断言 `/state` 的 `contentFiles[].status == "success"`。`test_api_duplicate_files.py` 的签名单测同理（若改动它，注意签名只依赖 `file_path|size|modified_at`，与格式无关）。
2. **spec §15 回填**：把「跨阶段缺口登记 · P2a↔P2b 未接线」块标记为**已闭合**（附 commit 与验收证据）；更新 `parsing/docx.py` 相关的裁定 R2 描述（R2 的拒绝前提已消失，改为「已接线，见本计划」）。
3. **本计划勾选**。
- [x] **Step 1-2**: 改夹具 → 确认仍绿（若红，先查清是不是真实的接线缺陷）。**Step 3**: 回填 spec 与计划。**Step 4**: 全量。**Step 5**: 提交 `docs(server): docx 图片接线完成回填（§15 缺口闭合）+ e2e 夹具回归 docx`。

---

## Self-Review

**Spec 覆盖检查：**

| Spec 条目 | 落在哪个任务 |
|---|---|
| §5.3 资产存放布局 | Task 1（解析）+ Task 3（落盘） |
| §8.5「图片占位数量与位置一致」 | Task 2 的 3 条断言（数量/位置/同图共享） |
| §15 缺口块「接入 P2b 资产提取并产出图片占位 markdown」 | Task 2 + Task 3 |
| §15 缺口块「e2e 夹具改回 docx」 | Task 4 |
| §15 P3c 行「资产相对路径，替代 `yibiao-asset://`」 | R-DW-1 |
| P2b 的四分决策 / 去重 / 原子落盘 | 复用，不改（Task 3 只调用） |

**已知空白（实现者必须在报告如实说明）：**

1. **页眉页脚图片不进 markdown**（R-DW-6）——与参考一致，但意味着「markdown 占位数」可能小于 P2b `occurrences` 数。Task 2 的断言只按**正文**数写，不按 `occurrences` 数写。
2. **`preview_url` 未定义**：`duplicateImages[].preview_url` 需要前端可访问的 HTTP 形态，属 P3c；本计划只保证 target **服务端可解析**。若 P3c 选择「HTTP 端点按 hash 取图」，`resolve_asset_path` 就是它的解析核心。
3. **`alt` 文本**：mammoth 只在 docx 带 `desc` 时补 alt（W1），通常为空 → 占位是 `![](assets/<hash>)`。参考侧同为 mammoth 系，形态一致；若发现参考带 alt，需另立裁定。
4. **P3b 的 `content_extraction` 已验收**（P3b 已回填完成）——Task 3 修改的是它的**行为**（新增落盘副作用），P3b 的双评审结论不覆盖这一点，故 Task 3 必须走独立评审。
