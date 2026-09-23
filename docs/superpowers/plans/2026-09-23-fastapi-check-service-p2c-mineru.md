# P2c 计划：MinerU 在线解析（spec §8.1 末段 / §15 P2c 行）

日期：2026-09-23 ｜ spec：`docs/superpowers/specs/2026-09-15-fastapi-check-service-design.md` §8.1 / §8.2 / §15
参考实现：`client/electron/services/fileService.cjs`（802 行，MinerU 两条 API 在 :140-318）

## 范围裁定

- **只做服务端**：MinerU `agent` / `accurate` 两个客户端 + 解析路由接入 + 配置开关。
  **前端 provider 选择器属 P4**（spec §15 P4 行「复用 API 与前端骨架」）；本阶段 provider
  只从 `Settings.file_parser_provider` 读，默认 `local`（既有行为不变，纯 opt-in）。
- **不做远端图片导入**：参考在 `preserveImages=true` 时把 MinerU 结果的图片下载/重写到资产库
  （`rewriteMarkdownImages` + zip entries）；新管线的图片来自 docx 资产提取（P2b），
  MinerU 的远端图片在 P2c **一律按 `stripMarkdownImages` 语义剥除**，并登记为缺口。
- **失败不回退**：参考只在「请求的 provider 不支持该扩展名」时回退本地；运行期失败
  （HTTP 非 2xx、`code != 0`、`state=failed`、轮询超时）**直接抛错**，文案逐字保留。

## 裁定条款

- **R-P2c-1（客户端分层）**：`app/infra/parsing/mineru_common.py` 放共用件
  （`upload_file` / `download_text` / `download_bytes` / `poll_until_done` /
  `make_data_id` / `strip_markdown_images`），两条 API 各自一个模块
  （`mineru_agent.py` / `mineru_accurate.py`）。同步 `httpx.Client`（`parse_document`
  是同步签名），`transport` 与 `sleep` 可注入——测试用 `httpx.MockTransport` + 快进 sleeper，
  不联网、不睡眠。
- **R-P2c-2（路由恢复参考语义，推翻 P2a 收窄）**：P2a 让 MinerU 请求一律回退本地
  （`router.py:46` 注释自陈是阶段行为）。P2c 恢复 `resolveFileParser`（:70-83）语义：
  请求 provider 的**扩展名支持表**命中 → `provider=requested, fallback_to_local=False`；
  未命中但本地支持该格式 → `provider='local', fallback_to_local=True`；
  两者都不支持 → `supported=False`（抛 `parser-unsupported`）。
  支持表（`fileService.cjs:17-22`）：
  - agent：`.pdf .doc .docx .ppt .pptx .png .jpg .jpeg .jp2 .webp .gif .bmp`
  - accurate：同上 + `.html`
  本地支持表沿用 P2a 的 `_LOCAL_SUPPORTED_KINDS`（按 `fmt.kind` 判定，勿按扩展名）。
- **R-P2c-3（两条 API 逐字对齐，`fileService.cjs:140-318`）**：
  - agent：`POST https://mineru.net/api/v1/agent/parse/file`，body
    `{file_name, language:'ch', enable_table:true, is_ocr:true, enable_formula:true}`，
    **无 Authorization**；响应 `code===0` 且 `data.{task_id,file_url}` 齐备；
    `PUT file_url` 上传原始字节；轮询 `GET /api/v1/agent/parse/{task_id}`
    （**3s 间隔 / 300s 超时**）→ `data.state`：`done` → `data.markdown_url`；
    `failed` → `MinerU-Agent 解析失败：{err_msg}{ (err_code)}`；其余继续等。
  - accurate：需 `Bearer {token}`；`POST https://mineru.net/api/v4/file-urls/batch`，body
    `{files:[{name, data_id: make_data_id(name), is_ocr:true}], model_version:'vlm',
    language:'ch', enable_table:true, enable_formula:true}` → `data.{batch_id,file_urls[0]}`；
    轮询 `GET /api/v4/extract-results/batch/{batch_id}`（**5s / 600s**），
    从 `data.extract_result` 里**按 file_name 匹配、缺则取首个** → `state`：`done` →
    `item.full_zip_url`；`failed` → `MinerU 精准解析失败：{err_msg}`。
  - 错误文案一律「动作短语：HTTP {status}，{响应 JSON}」形态（`code != 0` 与 `!ok` 同判）。
  - `make_data_id`：`re.sub(r'[^A-Za-z0-9_.-]+', '_', name)[:96] or 'document'`。
- **R-P2c-4（token 与 provider 走配置）**：`Settings.mineru_token`（默认空）、
  `Settings.file_parser_provider`（默认 `local`）。accurate 缺 token 时**先**抛
  逐字「请先在设置中填写 MinerU Token」（参考 :204-206，在发请求之前）。
- **R-P2c-5（zip 抽取）**：`full.md`（`(^|[/\\])full\.md$`，大小写不敏感）优先，
  否则任一 `.md`；都无 → 「MinerU 精准解析结果 zip 中未找到 Markdown 文件」。
- **R-P2c-6（图片剥除）**：`strip_markdown_images` = 删 `![alt](target "title")`
  （target 允许 `<...>` 包裹）→ 删 `<img ...>` 标签 → `\n{3,}` 折叠为 `\n\n`。
  正则含 `\b`/`\w` 类一律 `re.ASCII`（P3b 裁定③同纪律）。
- **R-P2c-7（不阻塞事件循环）**：`_ingest_upload` 里的解析调用改走
  `asyncio.to_thread`——本地解析顺带受益，远端轮询（最长 10 分钟）不再冻结 API。
- **R-P2c-8（测试形态）**：客户端单测用 `httpx.MockTransport` + 注入 sleeper
  （断言请求体/头/URL/轮询次数与顺序、失败与超时分支、zip 内存构造）；
  路由测试注入 `RemoteParser` 假件；不联网、不睡眠、无真实 token。
  探针：每条客户端 ≥1 判别力探针（如把轮询终止条件 `state=='done'` 变异掉）。

## 任务拆分（TDD）

- **T1 MinerU-Agent 客户端**：`mineru_common.py` + `mineru_agent.py` + 单测（成功、
  创建失败、上传失败、state=failed、轮询超时、缺字段）+ 探针。
- **T2 MinerU 精准解析客户端**：`mineru_accurate.py` + 单测（缺 token、成功走 zip、
  extract_result 匹配回退、state=failed、超时）+ 探针。
- **T3 路由 + 配置 + 接线**：`resolve_parser` 恢复参考语义、`parse_document` 接真客户端
  （可注入假件）、`Settings` 两个新字段、duplicate 上传传 provider/token +
  `asyncio.to_thread`；改写 P2a 的两条「一律回退」测试为参考语义 + 新增回退/不支持用例。
- **T4 收尾**：全量回归 + spec §15 P2c 行回填 + 执行期裁定 + ledger + memory + 提交 +
  双评审（A 保真 / B 契约），评审修复。

## 已知空白（登记）

- 远端图片导入（MinerU 结果里的图片下载入资产库）——需要时单开。
- provider 的**前端**选择器与设置页（P4）。
- `.wps` / `.xls` 在本地支持表里的映射沿用 P2a 现状（`detect_file_format` 决定），
  本阶段不动。
