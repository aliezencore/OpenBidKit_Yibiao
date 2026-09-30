# 易标投标工具箱

AI 智能标书工具集：桌面客户端（AI 标书写作）+ 服务端（标书查重与废标项检查）。

| 组成部分 | 目录 | 说明 |
| --- | --- | --- |
| 桌面客户端 | [`client/`](./client) | Electron + React，AI 标书写作、技术方案、主体识别等（原开源项目主体） |
| 服务端 | [`server/`](./server) | FastAPI，把客户端的「标书查重」「废标项检查」两大能力服务端化，Web 页面操作 |

> 桌面客户端的介绍与使用说明见 [`使用说明/`](./使用说明) 与 [`client/`](./client)；
> 旧版 README 已从仓库移除，可在 git 历史中查看。

---

## 服务端（server/）

服务端把客户端里最重的两条检查链路搬到后端长任务执行，浏览器直接操作：

- **标书查重**：元数据指纹比对、目录结构重复、正文重复句子、重复图片四段分析 + Excel 导出；
- **废标项检查**：招标文件解析（无效投标/废标项口径提取）→ 投标主体识别与确认 → 四项检查
  （废标项三轮分析、错别字、逻辑谬误、主体一致性含印章视觉）→ 五工作表 Excel 导出；
  超长文档自动分段/滚动审阅，支持 legacy `yibiao.sqlite` 一键迁移。

技术栈：FastAPI + SQLite（单文件库）+ openpyxl + React/Vite 前端；AI 调用走
OpenAI 兼容 `/chat/completions`（文本模型 + 视觉模型）。

### 快速开始

```bash
# 1. 安装（Python 3.13+）
cd server
python -m venv .venv
.venv/Scripts/pip install -e .          # Windows；macOS/Linux 用 .venv/bin/pip

# 2. 配置模型（环境变量，见下表）
set YIBIAO_TEXT_MODEL__BASE_URL=https://api.example.com/v1
set YIBIAO_TEXT_MODEL__API_KEY=sk-xxx
set YIBIAO_TEXT_MODEL__MODEL_NAME=你的模型名

# 3a. 单体模式（前端已构建进 web/dist 时，一个端口全搞定）
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 打开 http://127.0.0.1:8000/

# 3b. 前后端分离开发
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
cd web && npm install && npm run dev    # http://localhost:5173，API 代理到 8000
```

### 配置（环境变量，前缀 `YIBIAO_`）

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `YIBIAO_DATA_ROOT` | 数据根目录（数据库/文档/图片/导出） | `./data` |
| `YIBIAO_TEXT_MODEL__BASE_URL` | 文本模型 API 地址（OpenAI 兼容） | 空 |
| `YIBIAO_TEXT_MODEL__API_KEY` | 文本模型 Key | 空 |
| `YIBIAO_TEXT_MODEL__MODEL_NAME` | 文本模型名 | 空 |
| `YIBIAO_VISION_MODEL__*` | 视觉模型三项（印章检查用；不配则印章检查跳过） | 空 |
| `YIBIAO_MINERU_TOKEN` | MinerU 在线解析 token（可选） | 空 |
| `YIBIAO_AI_CONTEXT_LENGTH_LIMIT` | 长文档分段上限（字符） | `400000` |

完整项见 [`server/app/config.py`](./server/app/config.py)。后端监听地址/端口由
uvicorn 启动参数（`--host` / `--port`）决定，不在配置文件里。

### 运行测试

```bash
cd server
.venv/Scripts/python.exe -m pytest -q        # 当前 923 passed
cd web && npm run test                       # 前端单测 22 passed
```

### legacy 数据迁移（可选）

把桌面客户端既有工作区里的废标项检查数据（文档、解析结果、四项检查结果、
主体档案、印章图片）迁入服务端：

```bash
cd server
.venv/Scripts/python.exe scripts/migrate_rejection_legacy.py \
  --legacy-db <workspace>/yibiao.sqlite \
  --legacy-workspace <workspace> \
  --data-root ./data
```

旧库只读不改（URI `mode=ro`）；缺表的旧版本库会跳过对应数据继续迁移。

---

## 仓库结构

```
client/            桌面客户端（Electron 主进程 + React 渲染层）
server/            服务端（FastAPI + SQLite + React 前端）
  app/api/         REST 端点（项目/查重/废标项/任务/健康检查）
  app/domain/      领域层：查重、废标项四项检查算法、Prompt、Excel 导出 plan
  app/infra/       AI 客户端、DB、仓储、长任务引擎、xlsx 组装、文档解析
  web/             前端（React + Vite；查重页 + 废标项检查页）
  scripts/         legacy 迁移脚本
docs/superpowers/  设计与实施文档（spec/plans，含双评审记录）
analytics/         Cloudflare Worker 统计服务（客户端埋点）
sql/               客户端 SQLite schema 参考
```

## 设计文档

服务端的逐字移植依据、裁定条款与评审记录都在
[`docs/superpowers/`](./docs/superpowers)（spec = 设计，plans = 分阶段实施计划），
从 P1（查重基础）到 P4（废标项检查）共 40+ 提交、923 后端测试与 22 前端测试。
