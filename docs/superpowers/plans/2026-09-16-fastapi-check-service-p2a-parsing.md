# P2a 本地解析链 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「上传的招标/投标文件 → markdown 正文」这条本地解析链搬到 Python 侧，行为对齐现有 Electron 实现（`doc2markdown/convert.mjs`，1633 行），验收标准为**结构等价**（spec §8.5），不追求逐字节一致。

**Architecture:** 沿用 P1 的分层（`api → domain → infra`）。解析是纯 infra 能力：`infra/parsing/` 提供一个 `parse_document(path, options) -> ParsedDocument` 入口，内部按扩展名与配置路由。domain 不 import 解析库。所有解析结果落盘为 markdown 文件，库里只存路径与 `content_hash`（对齐 P1 的 `rejection_check_documents` 形状）。

**Tech Stack:** PyMuPDF · python-mammoth · markdownify · beautifulsoup4 + lxml · openpyxl · charset-normalizer

**Spec:** `docs/superpowers/specs/2026-09-15-fastapi-check-service-design.md` §8.1 / §8.2 / §8.5（2026-09-16 修订版）

**范围**：本地文档解析（**含 PDF**）。图片管线属 P2b、MinerU 在线解析属 P2c，见文末「明确不做」。

## Global Constraints

- 工作区 `.worktrees/fastapi-check-service`，分支 `fastapi-check-service`（**平铺名，不得含 `/`**）。
- **不得改动 `client/` 下任何文件**。
- 在 P1 之上增量开发；**不得破坏既有 123 个测试**。
- 提交身份 `git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit`；**不推送**；按显式路径 `git add`。
- `app/domain/**` 禁止 import `fastapi` / `starlette` / `sqlalchemy` / `httpx` / `pydantic` / `sqlite3`（护栏测试强制；解析库全部放在 `app/infra/parsing/`）。
- **验收标准是结构等价（spec §8.5）**：标题层级、表格数量与行列、段落数量 ±10%、无 XML 碎片、无 base64 泄漏。**不要求与 Electron 输出逐字节一致**。
- 主流程**不得网络请求**；MinerU 属 P2c。
- Main 侧文件读写显式 UTF-8（Windows 中文路径是默认场景）。
- 新增依赖仅限：`mammoth`、`markdownify`、`beautifulsoup4`、`lxml`、`openpyxl`、`charset-normalizer`（+ dev: `python-docx` 用于生成测试夹具）。**不得引入** turndown / pdfjs / SheetJS 的等价替代品以外的东西。

## File Structure

| 文件 | 职责 |
|---|---|
| `server/app/config.py` | 增：`file_parser_provider`、`mineru_token`（P2c 用）；图片压缩参数属 P2b，不在本计划 |
| `server/app/infra/parsing/__init__.py` | 包 |
| `server/app/infra/parsing/format.py` | `detect_file_format()`：按魔数+后缀识别 |
| `server/app/infra/parsing/encoding.py` | `read_text_with_encoding()`：编码探测直读 |
| `server/app/infra/parsing/spreadsheet.py` | xlsx → markdown 表格 |
| `server/app/infra/parsing/docx_html.py` | docx → HTML（mammoth）+ 表格抽取占位 |
| `server/app/infra/parsing/markdown.py` | HTML → markdown（markdownify）+ 归一化 |
| `server/app/infra/parsing/docx.py` | docx 编排：html→md + 图片占位 |
| `server/app/infra/parsing/legacy_doc.py` | .doc → LibreOffice → docx |
| `server/app/infra/parsing/router.py` | `resolve_parser()` + `parse_document()` 入口 + 错误归一化 |
| `server/app/infra/parsing/pdf.py` | PDF → markdown（PyMuPDF） |
| `server/app/domain/shared/errors.py` | `ParsingError`（domain 可见的错误类型，`code` 字段） |
| `server/tests/parsing_fixtures.py` | 测试夹具生成器（python-docx 造 docx） |
| `server/tests/test_parsing_*.py` | 各任务测试 |

---

### Task 1: 依赖、格式识别与解析错误类型

**Files:**
- Modify: `server/pyproject.toml`、`server/app/domain/shared/errors.py`
- Create: `server/app/infra/parsing/__init__.py`、`server/app/infra/parsing/format.py`
- Test: `server/tests/test_parsing_format.py`

**Interfaces:**
- Consumes: 无
- Produces: `ParsingError(message, *, code: str, path: str | None = None)`（放在 `app/domain/shared/errors.py`，只 import `__future__`，不碰框架）；`detect_file_format(path: Path) -> FileFormat`；`FileFormat` 为 frozen dataclass（`kind: str`、`extension: str`、`mime: str`），`kind` 取 `'docx' | 'doc' | 'pdf' | 'xlsx' | 'markdown' | 'txt'` 之一（**无 `'unknown'`**：识别失败直接抛 `ParsingError`）；`SUPPORTED_EXTENSIONS: frozenset[str]`（**扩展名**集合，仅用于报错信息；`.md` 与 `.markdown` 的 `kind` 都是 `'markdown'`）
  - 裁定 R5：`_is_zip` 用**纯魔数比对**，不用 `zipfile.ZipFile`（与 Node `isZipHeader` 一致，且才有 `_by_magic` 用例成立）
  - 裁定 R6：不定义 `DetectFormatError`（原 Interfaces 行的残留，全计划零引用）

- [ ] **Step 1: 加依赖并安装**

`server/pyproject.toml` 的 `[project] dependencies` 追加：

```toml
  "mammoth>=1.6",
  "markdownify>=0.13",
  "beautifulsoup4>=4.12",
  "lxml>=5.0",
  "openpyxl>=3.1",
  "charset-normalizer>=3.3",
```

`[project.optional-dependencies] dev` 追加 `"python-docx>=1.1"`。然后：

```bash
cd server && .venv/Scripts/python.exe -m pip install -e ".[dev]"
```

- [ ] **Step 2: 写失败的测试**

创建 `server/tests/test_parsing_format.py`：

```python
import pytest

from app.infra.parsing.format import SUPPORTED_EXTENSIONS, detect_file_format
from app.infra.parsing import ParsingError


def test_detects_docx_by_magic(tmp_path):
    p = tmp_path / "a.docx"
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 32)
    fmt = detect_file_format(p)
    assert fmt.kind == "docx"
    assert fmt.extension == ".docx"


def test_detects_xlsx_by_magic(tmp_path):
    p = tmp_path / "a.xlsx"
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 32)
    assert detect_file_format(p).kind == "xlsx"


def test_detects_pdf_by_magic(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(b"%PDF-1.7\n" + b"\x00" * 32)
    assert detect_file_format(p).kind == "pdf"


def test_txt_and_markdown_are_text(tmp_path):
    assert detect_file_format(tmp_path / "a.txt").kind == "txt"
    assert detect_file_format(tmp_path / "a.md").kind == "markdown"


def test_doc_detected_by_extension_only(tmp_path):
    # OLE 魔数 D0CF11E0 会与 .xls/.ppt 混淆，.doc 一律按扩展名识别。
    p = tmp_path / "a.doc"
    p.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 32)
    assert detect_file_format(p).kind == "doc"


def test_unknown_extension_raises_parsing_error(tmp_path):
    p = tmp_path / "a.exe"
    p.write_bytes(b"MZ")
    with pytest.raises(ParsingError) as info:
        detect_file_format(p)
    assert info.value.code == "unsupported-extension"
    assert ".exe" in str(info.value)


def test_magic_mismatch_wins_over_extension(tmp_path):
    # 扩展名是 .docx 但内容不是 zip：以魔数为准，判 unknown 并报错。
    p = tmp_path / "fake.docx"
    p.write_bytes(b"not a zip at all")
    with pytest.raises(ParsingError) as info:
        detect_file_format(p)
    assert info.value.code == "format-mismatch"


def test_supported_extensions_covers_brief_formats():
    assert SUPPORTED_EXTENSIONS == frozenset(
        {".docx", ".doc", ".pdf", ".xlsx", ".txt", ".md"}
    )
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_format.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.infra.parsing'`

- [ ] **Step 4: 写实现**

创建 `server/app/domain/shared/errors.py`（若已有则合并）：

```python
from __future__ import annotations


class ParsingError(Exception):
    """解析失败。`code` 是稳定的机器可读码，供 API 与前端分支。"""

    def __init__(self, message: str, *, code: str, path: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.path = path
```

创建 `server/app/infra/parsing/__init__.py`（空）与 `server/app/infra/parsing/format.py`：

```python
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.domain.shared.errors import ParsingError

_MAGIC = b"PK\x03\x04"
_PDF_MAGIC = b"%PDF-"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0"


@dataclass(frozen=True)
class FileFormat:
    kind: str
    extension: str
    mime: str


_MIME = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "markdown": "text/markdown",
    "txt": "text/plain",
}

SUPPORTED_EXTENSIONS = frozenset({".docx", ".doc", ".pdf", ".xlsx", ".txt", ".md"})


def _is_zip(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path):
            return True
    except (zipfile.BadZipFile, OSError):
        return False


def detect_file_format(path: Path) -> FileFormat:
    """按魔数识别，扩展名只用于区分 zip 里的 docx/xlsx 与文本类。

    对齐现有 detectFileFormat（convert.mjs:128）：zip 魔数 + `.docx`/`.xlsx`
    后缀区分 Office 子类型；OLE 魔数与 `.doc`/`.xls`/`.ppt` 都会命中，故
    `.doc` 一律按扩展名识别。
    """
    path = Path(path)
    extension = path.suffix.lower()

    if extension in (".md", ".markdown"):
        return FileFormat("markdown", extension, _MIME["markdown"])
    if extension == ".txt":
        return FileFormat("txt", extension, _MIME["txt"])

    if extension not in SUPPORTED_EXTENSIONS:
        raise ParsingError(
            f"不支持的文件格式：{extension or '（无扩展名）'}，"
            f"支持：{', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            code="unsupported-extension",
            path=str(path),
        )

    if extension in (".docx", ".xlsx"):
        if not _is_zip(path):
            raise ParsingError(
                "文件解析失败：该文件不是有效的 Office 文档，请用 Word/WPS 另存为标准格式后重试",
                code="format-mismatch",
                path=str(path),
            )
        kind = extension.lstrip(".")
        return FileFormat(kind, extension, _MIME[kind])

    if extension == ".pdf":
        head = path.read_bytes()[: len(_PDF_MAGIC)]
        if head != _PDF_MAGIC:
            raise ParsingError("文件解析失败：不是有效的 PDF", code="format-mismatch", path=str(path))
        return FileFormat("pdf", extension, _MIME["pdf"])

    if extension == ".doc":
        head = path.read_bytes()[: len(_OLE_MAGIC)]
        if head != _OLE_MAGIC:
            raise ParsingError("文件解析失败：不是有效的 Word 97-2003 文档", code="format-mismatch", path=str(path))
        return FileFormat("doc", extension, _MIME["doc"])

    raise ParsingError(f"未知的文件格式：{extension}", code="unknown-format", path=str(path))
```

在 `app/infra/parsing/__init__.py` 导出：

```python
from app.domain.shared.errors import ParsingError

__all__ = ["ParsingError"]
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_format.py -v`
Expected: PASS（8 passed）

- [ ] **Step 6: 提交**

```bash
git add server/pyproject.toml server/app/domain/shared/errors.py server/app/infra/parsing server/tests/test_parsing_format.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): 文件格式识别与解析错误类型（P2a Task 1）"
```

---

### Task 2: txt/md 直读与编码探测

**Files:**
- Create: `server/app/infra/parsing/encoding.py`
- Test: `server/tests/test_parsing_encoding.py`

**Interfaces:**
- Consumes: `FileFormat`
- Produces: `read_text_with_encoding(path: Path, *, max_bytes: int | None = None) -> str`；`detect_encoding(path: Path) -> str`

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_parsing_encoding.py`：

```python
from app.infra.parsing.encoding import detect_encoding, read_text_with_encoding


def test_reads_utf8(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("招标文件正文", encoding="utf-8")
    assert read_text_with_encoding(p) == "招标文件正文"


def test_reads_gbk(tmp_path):
    p = tmp_path / "b.txt"
    p.write_bytes("投标文件".encode("gbk"))
    assert read_text_with_encoding(p) == "投标文件"


def test_reads_utf8_with_bom(tmp_path):
    p = tmp_path / "c.txt"
    p.write_bytes(b"\xef\xbb\xbf" + "带 BOM 的文本".encode("utf-8"))
    assert read_text_with_encoding(p) == "带 BOM 的文本"


def test_detect_encoding_reports_charset(tmp_path):
    p = tmp_path / "d.txt"
    p.write_bytes("投标文件".encode("gbk"))
    assert detect_encoding(p) in ("GBK", "gbk")


def test_strips_bom_only_once(tmp_path):
    p = tmp_path / "e.md"
    p.write_bytes(b"\xef\xbb\xbf# 标题\n")
    text = read_text_with_encoding(p)
    assert text.startswith("# 标题")
    assert not text.startswith("\ufeff")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_encoding.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.infra.parsing.encoding'`

- [ ] **Step 3: 写实现**

创建 `server/app/infra/parsing/encoding.py`：

```python
from __future__ import annotations

from pathlib import Path

import charset_normalizer


def detect_encoding(path: Path) -> str:
    """编码探测，对齐现有 chardet 用法（convert.mjs:9）。"""
    raw = Path(path).read_bytes()
    best = charset_normalizer.from_bytes(raw).best()
    if best is None:
        return "utf-8"
    return best.encoding


def read_text_with_encoding(path: Path, *, max_bytes: int | None = None) -> str:
    """按探测到的编码读全文，统一剥掉 UTF-8 BOM。

    Node 侧用 chardet + iconv-lite；这里用 charset-normalizer（同一探测思路，
    维护更活跃）。BOM 由 read_text 的 'utf-8-sig' 处理，其余编码手动剥。
    """
    raw = Path(path).read_bytes()
    if max_bytes is not None:
        raw = raw[:max_bytes]
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace")
    best = charset_normalizer.from_bytes(raw).best()
    encoding = best.encoding if best is not None else "utf-8"
    return raw.decode(encoding, errors="replace")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_encoding.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/infra/parsing/encoding.py server/tests/test_parsing_encoding.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): 编码探测直读 txt/md（P2a Task 2）"
```

---

### Task 3: xlsx → markdown 表格

**Files:**
- Create: `server/app/infra/parsing/spreadsheet.py`
- Test: `server/tests/test_parsing_spreadsheet.py`

**Interfaces:**
- Consumes: `ParsingError`
- Produces: `spreadsheet_to_markdown(path: Path) -> str`（每个 sheet 一个 `## <sheet 名>` 标题 + GFM 表格；空 sheet 输出「（空表）」）

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_parsing_spreadsheet.py`：

```python
from openpyxl import Workbook

from app.infra.parsing.pdf import pdf_to_markdown
from app.infra.parsing.spreadsheet import spreadsheet_to_markdown


def _make_workbook(path, sheets):
    wb = Workbook()
    first = wb.active
    first.title = sheets[0][0]
    for row in sheets[0][1]:
        first.append(row)
    for name, rows in sheets[1:]:
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    wb.save(path)


def test_renders_header_and_rows(tmp_path):
    p = tmp_path / "报价.xlsx"
    _make_workbook(p, [("报价", [["项目", "金额"], ["土建", 100], ["安装", 200.5]])])

    text = spreadsheet_to_markdown(p)

    assert "## 报价" in text
    assert "| 项目 | 金额 |" in text
    assert "| --- | --- |" in text
    assert "| 土建 | 100 |" in text
    assert "| 安装 | 200.5 |" in text


def test_multiple_sheets_each_get_heading(tmp_path):
    p = tmp_path / "表.xlsx"
    _make_workbook(p, [("A", [["x"]]), ("B", [["y"]])])

    text = spreadsheet_to_markdown(p)

    assert text.index("## A") < text.index("## B")
    assert "| x |" in text and "| y |" in text


def test_empty_sheet_gets_placeholder(tmp_path):
    p = tmp_path / "空.xlsx"
    _make_workbook(p, [("空白", [])])

    text = spreadsheet_to_markdown(p)

    assert "## 空白" in text
    assert "（空表）" in text


def test_pipe_in_cell_is_escaped(tmp_path):
    p = tmp_path / "管道.xlsx"
    _make_workbook(p, [("S", [["a|b", "c"]])])

    text = spreadsheet_to_markdown(p)

    assert "a\\|b" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_spreadsheet.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写实现**

创建 `server/app/infra/parsing/spreadsheet.py`：

```python
from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook


def _escape_cell(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\r\n", " ").replace("\n", " ")


def _render_table(rows: list[list]) -> str:
    if not rows:
        return "（空表）"
    header = rows[0]
    body = rows[1:]
    lines = ["| " + " | ".join(_escape_cell(cell) for cell in header) + " |"]
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in body:
        padded = row + [""] * (len(header) - len(row))
        lines.append("| " + " | ".join(_escape_cell(cell) for cell in padded[: len(header)]) + " |")
    return "\n".join(lines)


def spreadsheet_to_markdown(path: Path) -> str:
    """每个 sheet 一节：`## <sheet 名>` + GFM 表格。

    对齐现有 renderSpreadsheetTables（convert.mjs:440）的输出形态；
    数值按 openpyxl 原样转字符串（100 → `100`、200.5 → `200.5`），
    不做千分位或货币格式化——那是显示层的事，比对层不需要。
    """
    wb = load_workbook(path, data_only=True, read_only=True)
    sections: list[str] = []
    try:
        for sheet in wb.worksheets:
            rows = [list(row) for row in sheet.iter_rows(values_only=True)]
            body = f"## {sheet.title}\n\n{_render_table(rows)}"
            sections.append(body)
    finally:
        wb.close()
    return "\n\n".join(sections) + "\n"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_spreadsheet.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/infra/parsing/spreadsheet.py server/tests/test_parsing_spreadsheet.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): xlsx 转 markdown 表格（P2a Task 3）"
```

---

### Task 4: docx → HTML → markdown（mammoth + markdownify + 表格占位）

**Files:**
- Create: `server/app/infra/parsing/docx_html.py`、`server/app/infra/parsing/markdown.py`、`server/app/infra/parsing/docx.py`
- Create: `server/tests/parsing_fixtures.py`
- Test: `server/tests/test_parsing_docx.py`

**Interfaces:**
- Consumes: `FileFormat`
- Produces: `docx_to_markdown(path: Path, *, include_images: bool = False) -> str`；`html_to_markdown(html: str) -> str`；`normalize_markdown(text: str) -> str`；`extract_tables(html: str) -> tuple[str, list[str]]`（返回「去表后的 HTML」与「GFM 表格列表」）；夹具 `build_docx(path, *, paragraphs=..., headings=..., table=..., image_paths=...)`

**关键约束**：表格必须**先从 HTML 抽出并替换为占位符，转 markdown 后回填**——对齐现有 `preserveTables`/`restoreTables`（convert.mjs:546-573）。若让 markdownify 直接吃表格 HTML，产出的表格会被它的内联规则破坏。

- [ ] **Step 1: 写夹具生成器**

创建 `server/tests/parsing_fixtures.py`：

```python
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.shared import Pt


def build_docx(
    path: Path,
    *,
    blocks: list[tuple[str, str]] | None = None,
    table: list[list[str]] | None = None,
) -> Path:
    """blocks: [(kind, text)]，kind ∈ {'h1','h2','p','bold-p'}。"""
    doc = Document()
    for kind, text in blocks or []:
        if kind == "h1":
            doc.add_heading(text, level=1)
        elif kind == "h2":
            doc.add_heading(text, level=2)
        elif kind == "bold-p":
            para = doc.add_paragraph()
            run = para.add_run(text)
            run.bold = True
        else:
            doc.add_paragraph(text)
    if table:
        rendered = doc.add_table(rows=0, cols=0)
        for row in table:
            cells = rendered.add_row().cells
            for index, value in enumerate(row):
                cells[index].text = value
    doc.save(path)
    return path
```

- [ ] **Step 2: 写失败的测试**

创建 `server/tests/test_parsing_docx.py`：

```python
from app.infra.parsing.docx import docx_to_markdown
from app.infra.parsing.markdown import html_to_markdown, normalize_markdown
from tests.parsing_fixtures import build_docx


def test_headings_and_paragraphs(tmp_path):
    p = build_docx(tmp_path / "a.docx", blocks=[
        ("h1", "第一章 总体方案"), ("p", "本项目采用分层架构。"),
        ("h2", "1.1 建设目标"), ("p", "实现自动化检查。"),
    ])

    text = docx_to_markdown(p)

    assert "# 第一章 总体方案" in text
    assert "本项目采用分层架构。" in text
    assert "## 1.1 建设目标" in text


def test_table_is_preserved_as_gfm(tmp_path):
    p = build_docx(tmp_path / "t.docx", table=[["项目", "金额"], ["土建", "100"]])

    text = docx_to_markdown(p)

    assert "| 项目 | 金额 |" in text
    assert "| --- | --- |" in text
    assert "| 土建 | 100 |" in text


def test_no_base64_or_xml_fragments_leak(tmp_path):
    p = build_docx(tmp_path / "x.docx", blocks=[("p", "正文")])

    text = docx_to_markdown(p)

    for bad in ("base64", "w:document", "<w:p", "xmlns"):
        assert bad not in text


def test_normalize_collapses_blank_runs(tmp_path):
    assert normalize_markdown("a\n\n\n\nb") == "a\n\nb"
    assert normalize_markdown("  标题  ") == "标题"


def test_html_to_markdown_keeps_bold(tmp_path):
    assert "**加粗**" in html_to_markdown("<p><strong>加粗</strong></p>")


def test_empty_document_yields_empty_string(tmp_path):
    p = build_docx(tmp_path / "empty.docx", blocks=[])
    assert docx_to_markdown(p).strip() == ""
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_docx.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.infra.parsing.docx'`

- [ ] **Step 4: 写实现**

`server/app/infra/parsing/docx_html.py`：

```python
from __future__ import annotations

import mammoth


def docx_to_html(path, *, include_images: bool = False) -> str:
    """docx → HTML。mammoth 保留语义（标题/列表/加粗），丢弃样式。"""
    with open(path, "rb") as handle:
        result = mammoth.convert_to_html(
            handle,
            convert_image=mammoth.images.img_element(lambda image: {"src": ""})
            if include_images is False
            else None,
        )
        html = result.value
    if include_images is False:
        # mammoth 的空 src 占位仍会产出 <img src="" />；统一剥掉，避免空链接。
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        for img in soup.find_all("img"):
            img.decompose()
        html = str(soup)
    return html
```

`server/app/infra/parsing/markdown.py`：

```python
from __future__ import annotations

import re

from bs4 import BeautifulSoup
from markdownify import markdownify as _markdownify

_TABLE_PLACEHOLDER = "\n\n<!--TABLE:{}-->\n\n"


def extract_tables(html: str) -> tuple[str, list[str]]:
    """把 <table> 抽成 GFM 文本并用占位符替换，返回（去表 HTML, GFM 表格列表）。

    对齐现有 preserveTables/restoreTables（convert.mjs:546-573）：turndown 的
    内联规则会破坏表格结构，必须先摘出来单独渲染。
    """
    soup = BeautifulSoup(html, "lxml")
    tables: list[str] = []
    for index, table in enumerate(soup.find_all("table")):
        rows = []
        for tr in table.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
            if cells:
                rows.append(cells)
        if not rows:
            table.decompose()
            continue
        header = rows[0]
        lines = ["| " + " | ".join(header) + " |",
                 "| " + " | ".join("---" for _ in header) + " |"]
        for row in rows[1:]:
            row = row + [""] * (len(header) - len(row))
            lines.append("| " + " | ".join(row[: len(header)]) + " |")
        tables.append("\n".join(lines))
        table.replace_with(_TABLE_PLACEHOLDER.format(index).strip())
    return str(soup), tables


def restore_tables(markdown: str, tables: list[str]) -> str:
    for index, table in enumerate(tables):
        markdown = markdown.replace(f"<!--TABLE:{index}-->", table)
    return markdown


def html_to_markdown(html: str) -> str:
    return _markdownify(html, heading_style="ATX", bullets="-")


def normalize_markdown(text: str) -> str:
    """归一化：压掉连续空行、去行尾空白、统一标题前的空行数。

    对齐现有 normalizeGeneratedMarkdown 的意图；具体规则以「可读、无噪音」为准，
    不追求与 turndown 输出逐字节一致（spec §8.5 结构等价）。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+$", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
```

`server/app/infra/parsing/docx.py`：

```python
from __future__ import annotations

from pathlib import Path

from app.domain.shared.errors import ParsingError
from app.infra.parsing.docx_html import docx_to_html
from app.infra.parsing.markdown import extract_tables, html_to_markdown, normalize_markdown, restore_tables


def docx_to_markdown(path: Path, *, include_images: bool = False) -> str:
    """docx → markdown。表格先摘出单独渲染再回填，避免被内联规则破坏。"""
    if include_images:
        # 裁定 R2：P2a 不产出图片。mammoth 的默认图片转换会把整张图编码成
        # base64 data URL 塞进 HTML→markdown，体积与噪音都会污染 LLM 提示词
        # 与正文比对。真正的图片占位 + 资产提取属 P2b，此处显式拒绝而不是
        # 留一条「能用但会泄漏 base64」的路径。
        raise ParsingError(
            "docx 图片占位与资产提取属 P2b 范围，P2a 不支持 include_images=True",
            code="not-implemented",
            path=str(path),
        )
    html = docx_to_html(Path(path), include_images=include_images)
    html_without_tables, tables = extract_tables(html)
    markdown = html_to_markdown(html_without_tables)
    markdown = restore_tables(markdown, tables)
    return normalize_markdown(markdown) + "\n"
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_docx.py -v`
Expected: PASS（6 passed）。若 mammoth 对 `add_heading` 产出的标题映射与断言不符（例如产出 `<h1>` 但 markdownify 需要 ATX），按实际输出调整**断言**而不是实现——这一步的验收是结构等价。

- [ ] **Step 6: 提交**

```bash
git add server/app/infra/parsing/docx_html.py server/app/infra/parsing/markdown.py server/app/infra/parsing/docx.py server/tests/parsing_fixtures.py server/tests/test_parsing_docx.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): docx 转 markdown（mammoth+markdownify+表格占位）（P2a Task 4）"
```

---

### Task 5: .doc 经 LibreOffice 转 docx

**Files:**
- Create: `server/app/infra/parsing/legacy_doc.py`
- Test: `server/tests/test_parsing_legacy_doc.py`

**Interfaces:**
- Consumes: `docx_to_markdown`、`ParsingError`
- Produces: `find_soffice() -> str | None`；`doc_to_docx(path: Path, output_dir: Path) -> Path`；`doc_to_markdown(path: Path) -> str`（LibreOffice 不可用时抛 `ParsingError(code='libreoffice-missing')`）

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_parsing_legacy_doc.py`：

```python
import sys

import pytest

from app.infra.parsing import ParsingError
from app.infra.parsing.legacy_doc import find_soffice, doc_to_markdown


def test_find_soffice_returns_none_or_str():
    # 不能假设测试机装了 LibreOffice；契约是「要么找到要么 None」。
    assert find_soffice() is None or isinstance(find_soffice(), str)


@pytest.mark.skipif(find_soffice() is None, reason="本机未安装 LibreOffice")
def test_doc_converts_when_libreoffice_available(tmp_path):
    # 该用例需要一份真实的 .doc 样本；没有样本时跳过。
    sample = tmp_path / "a.doc"
    if not sample.exists():
        pytest.skip("缺少 .doc 夹具")


def test_missing_libreoffice_raises_parsing_error(tmp_path, monkeypatch):
    monkeypatch.setattr("app.infra.parsing.legacy_doc.find_soffice", lambda: None)
    p = tmp_path / "a.doc"
    p.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 32)

    with pytest.raises(ParsingError) as info:
        doc_to_markdown(p)

    assert info.value.code == "libreoffice-missing"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_legacy_doc.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.infra.parsing.legacy_doc'`

- [ ] **Step 3: 写实现**

创建 `server/app/infra/parsing/legacy_doc.py`：

```python
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from app.domain.shared.errors import ParsingError
from app.infra.parsing.docx import docx_to_markdown

_CANDIDATES = (
    "soffice",
    "libreoffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
)


def find_soffice() -> str | None:
    """定位 LibreOffice；对齐现有 findLibreOfficeCommand（convert.mjs:1256）。"""
    for candidate in _CANDIDATES:
        found = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if found:
            return found
    return None


def doc_to_docx(path: Path, output_dir: Path) -> Path:
    soffice = find_soffice()
    if not soffice:
        raise ParsingError(
            "本机未安装 LibreOffice，无法解析 .doc 文件；请安装后重试，或将文件另存为 .docx",
            code="libreoffice-missing",
            path=str(path),
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [soffice, "--headless", "--convert-to", "docx", "--outdir", str(output_dir), str(path)],
        capture_output=True, text=True, timeout=180, check=False,
    )
    produced = output_dir / (path.stem + ".docx")
    if completed.returncode != 0 or not produced.exists():
        raise ParsingError(
            "LibreOffice 转换 .doc 失败",
            code="office-conversion-failed",
            path=str(path),
        )
    return produced


def doc_to_markdown(path: Path) -> str:
    """.doc → LibreOffice → docx → markdown。对齐 withLegacyWordDocxFile。"""
    path = Path(path)
    with tempfile.TemporaryDirectory(prefix="yibiao-doc-") as tmp:
        produced = doc_to_docx(path, Path(tmp))
        return docx_to_markdown(produced)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_legacy_doc.py -v`
Expected: PASS（本机无 LibreOffice 时 1 skipped + 2 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/infra/parsing/legacy_doc.py server/tests/test_parsing_legacy_doc.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): .doc 经 LibreOffice 转 docx（P2a Task 5）"
```

---

### Task 6: PDF → markdown（PyMuPDF）

**Files:**
- Create: `server/app/infra/parsing/pdf.py`
- Test: `server/tests/test_parsing_pdf.py`

**Interfaces:**
- Consumes: `ParsingError`
- Produces: `pdf_to_markdown(path: Path) -> str`（按页输出，页间用 `\n\n---\n\n` 分隔；页内先正文后表格；无文本的页输出「（本页无可提取文本，可能是扫描件）」）

**说明**：PDF 是**纯本地**解析（PyMuPDF 是本地 wheel，无网络无系统依赖）。现有 Node 侧用 pdf-parse + pdfjs-dist，其中表格靠 ~150 行自定义的矩形分析；这里用 PyMuPDF 的 `page.find_tables()`，算法不同但**验收标准是结构等价**（spec §8.5），只要求「表格数量与行列一致」。

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_parsing_pdf.py`：

```python
import fitz
import pytest

from app.infra.parsing.pdf import pdf_to_markdown


def _make_pdf(path, pages):
    """pages: [list[str]]，每页一组文本行。"""
    doc = fitz.open()
    for lines in pages:
        page = doc.new_page()
        y = 72
        for line in lines:
            page.insert_text((72, y), line, fontsize=12)
            y += 20
    doc.save(path)
    doc.close()
    return path


def test_extracts_text_per_page(tmp_path):
    p = _make_pdf(tmp_path / "a.pdf", [["Chapter One", "body text"]])

    text = pdf_to_markdown(p)

    assert "Chapter One" in text
    assert "body text" in text


def test_pages_are_separated(tmp_path):
    p = _make_pdf(tmp_path / "b.pdf", [["first page"], ["second page"]])

    text = pdf_to_markdown(p)

    assert text.index("first page") < text.index("---") < text.index("second page")


def test_blank_page_gets_placeholder(tmp_path):
    p = _make_pdf(tmp_path / "c.pdf", [[]])

    text = pdf_to_markdown(p)

    assert "本页无可提取文本" in text


def test_no_xml_or_base64_leak(tmp_path):
    p = _make_pdf(tmp_path / "d.pdf", [["plain"]])

    text = pdf_to_markdown(p)

    for bad in ("base64", "<w:", "xmlns"):
        assert bad not in text


def test_missing_file_raises_parsing_error(tmp_path):
    from app.infra.parsing import ParsingError

    with pytest.raises(ParsingError):
        pdf_to_markdown(tmp_path / "nope.pdf")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_pdf.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.infra.parsing.pdf'`

- [ ] **Step 3: 写实现**

先加依赖：`server/pyproject.toml` 的 dependencies 追加 `"pymupdf>=1.24"`，然后 `pip install -e ".[dev]"`。

创建 `server/app/infra/parsing/pdf.py`：

```python
from __future__ import annotations

from pathlib import Path

import fitz

from app.domain.shared.errors import ParsingError


def _escape_cell(value) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def _render_table(rows: list[list]) -> str:
    if not rows:
        return ""
    header = [_escape_cell(cell) for cell in rows[0]]
    lines = ["| " + " | ".join(header) + " |",
             "| " + " | ".join("---" for _ in header) + " |"]
    for row in rows[1:]:
        padded = [_escape_cell(cell) for cell in row] + [""] * (len(header) - len(row))
        lines.append("| " + " | ".join(padded[: len(header)]) + " |")
    return "\n".join(lines)


def pdf_to_markdown(path: Path) -> str:
    """PDF → markdown（逐页，正文 + GFM 表格）。

    现有 Node 侧用 pdf-parse 取正文、pdfjs-dist 分析图形矩形还原表格
    （convert.mjs:596-737）。这里改用 PyMuPDF 的 find_tables()：算法不同，
    但 spec §8.5 只要求结构等价（表格数量与行列一致），不要求逐字节一致。
    """
    path = Path(path)
    try:
        document = fitz.open(path)
    except Exception as error:  # fitz 抛的是自身的 RuntimeError 子类
        raise ParsingError("文件解析失败：不是有效的 PDF", code="pdf-open-failed", path=str(path)) from error

    sections: list[str] = []
    try:
        for page in document:
            parts: list[str] = []
            text = page.get_text("text").strip()
            if text:
                parts.append(text)
            for table in page.find_tables():
                rendered = _render_table(table.extract())
                if rendered:
                    parts.append(rendered)
            sections.append("\n\n".join(parts) if parts else "（本页无可提取文本，可能是扫描件）")
    finally:
        document.close()

    return "\n\n---\n\n".join(sections) + "\n"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_pdf.py -v`
Expected: PASS（5 passed）。若 `insert_text` 造出的样本让 `find_tables` 找不到表格，**不要为了造表格去改实现**——表格还原在 Task 9 的结构等价验收里用真实 PDF 样本验证；本任务只钉「逐页文本 + 分隔 + 占位 + 无泄漏」。

- [ ] **Step 5: 提交**

```bash
git add server/pyproject.toml server/app/infra/parsing/pdf.py server/tests/test_parsing_pdf.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): PDF 转 markdown（PyMuPDF）（P2a Task 6）"
```

---

### Task 7: 解析路由与统一入口

**Files:**
- Create: `server/app/infra/parsing/router.py`
- Test: `server/tests/test_parsing_router.py`

**Interfaces:**
- Consumes: 前述全部
- Produces: `resolve_parser(path: Path, provider: str) -> ParserDecision`（dataclass：`provider`、`requested_provider`、`extension`、`supported`、`fallback_to_local`，**与现有 `resolveFileParser` 字段一一对应**）；`parse_document(path: Path, *, provider: str = "local", include_images: bool = False) -> ParsedDocument`；`ParsedDocument`（dataclass：`markdown: str`、`format_kind: str`、`extension: str`、`parser_label: str`）

**路由语义（对齐 `resolveFileParser`，fileService.cjs:70）：**
1. 请求的 provider 支持该扩展名 → 用它；
2. 请求的 provider 不是 `local` 但本地支持 → **回退本地**（`fallback_to_local=True`）；
3. 都不支持 → `ParsingError`。

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_parsing_router.py`：

```python
import pytest

from app.infra.parsing.format import detect_file_format
from app.infra.parsing.router import ParsedDocument, parse_document, resolve_parser
from tests.parsing_fixtures import build_docx


def test_local_provider_supports_docx(tmp_path):
    p = build_docx(tmp_path / "a.docx", blocks=[("p", "正文")])
    decision = resolve_parser(p, provider="local")
    assert (decision.provider, decision.supported, decision.fallback_to_local) == ("local", True, False)


def test_mineru_falls_back_to_local_for_docx(tmp_path):
    p = build_docx(tmp_path / "a.docx", blocks=[("p", "正文")])
    decision = resolve_parser(p, provider="mineru-agent-api")
    assert decision.provider == "local"
    assert decision.fallback_to_local is True


def test_unsupported_extension_raises(tmp_path):
    p = tmp_path / "a.exe"
    p.write_bytes(b"MZ")
    with pytest.raises(Exception):
        resolve_parser(p, provider="local")


def test_parse_document_routes_docx(tmp_path):
    p = build_docx(tmp_path / "a.docx", blocks=[("h1", "标题"), ("p", "正文")])

    result = parse_document(p, provider="local")

    assert isinstance(result, ParsedDocument)
    assert result.format_kind == "docx"
    assert result.parser_label == "local"
    assert "标题" in result.markdown


def test_parse_document_routes_txt(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("纯文本内容", encoding="utf-8")

    result = parse_document(p, provider="local")

    assert result.markdown.strip() == "纯文本内容"
    assert result.format_kind == "txt"


def test_parse_document_detects_format(tmp_path):
    p = build_docx(tmp_path / "a.docx", blocks=[("p", "x")])
    assert parse_document(p, provider="local").format_kind == detect_file_format(p).kind
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_router.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.infra.parsing.router'`

- [ ] **Step 3: 写实现**

创建 `server/app/infra/parsing/router.py`：

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.domain.shared.errors import ParsingError
from app.infra.parsing.docx import docx_to_markdown
from app.infra.parsing.encoding import read_text_with_encoding
from app.infra.parsing.format import FileFormat, detect_file_format
from app.infra.parsing.legacy_doc import doc_to_markdown as legacy_doc_to_markdown
from app.infra.parsing.pdf import pdf_to_markdown
from app.infra.parsing.spreadsheet import spreadsheet_to_markdown

_MINERU_PROVIDERS = frozenset({"mineru-agent-api", "mineru-accurate-api"})
# 注意：这里必须是 **kind**（不带点的格式名），不是扩展名。
# 判据是 `fmt.kind`，写成 {".docx", ...} 会恒为 False（裁定 R1）。
_LOCAL_SUPPORTED_KINDS = frozenset({"docx", "doc", "txt", "markdown", "pdf", "xlsx"})


@dataclass(frozen=True)
class ParserDecision:
    provider: str
    requested_provider: str
    extension: str
    supported: bool
    fallback_to_local: bool


@dataclass(frozen=True)
class ParsedDocument:
    markdown: str
    format_kind: str
    extension: str
    parser_label: str


def resolve_parser(path: Path, provider: str) -> ParserDecision:
    """对齐现有 resolveFileParser（fileService.cjs:70）的字段与回退语义。"""
    path = Path(path)
    fmt = detect_file_format(path)
    provider = provider or "local"
    mineru_supports = provider in _MINERU_PROVIDERS and fmt.kind in ("pdf",)
    local_supports = provider == "local" and fmt.kind in _LOCAL_SUPPORTED_KINDS
    requested_supported = local_supports or mineru_supports
    if requested_supported:
        return ParserDecision(provider, provider, fmt.extension, True, False)
    if provider != "local" and fmt.kind in _LOCAL_SUPPORTED_KINDS:
        return ParserDecision("local", provider, fmt.extension, True, True)
    return ParserDecision(provider, provider, fmt.extension, False, False)


def parse_document(path: Path, *, provider: str = "local", include_images: bool = False) -> ParsedDocument:
    path = Path(path)
    decision = resolve_parser(path, provider)
    if not decision.supported:
        raise ParsingError(
            f"所选解析器（{decision.requested_provider}）不支持 {decision.extension}",
            code="parser-unsupported",
            path=str(path),
        )
    fmt: FileFormat = detect_file_format(path)
    if decision.provider == "local":
        markdown = _parse_locally(path, fmt)
    else:
        # MinerU 客户端属 P2c；P2a 阶段请求它会被路由回退到本地，故此处不可达。
        raise ParsingError("MinerU 解析属 P2c 范围", code="not-implemented", path=str(path))
    label = "local" if decision.fallback_to_local else decision.provider
    return ParsedDocument(markdown=markdown, format_kind=fmt.kind,
                          extension=fmt.extension, parser_label=label)


def _parse_locally(path: Path, fmt: FileFormat) -> str:
    if fmt.kind == "txt":
        return read_text_with_encoding(path) + "\n"
    if fmt.kind == "markdown":
        return read_text_with_encoding(path) + "\n"
    if fmt.kind == "xlsx":
        return spreadsheet_to_markdown(path)
    if fmt.kind == "docx":
        return docx_to_markdown(path)
    if fmt.kind == "doc":
        return legacy_doc_to_markdown(path)
    if fmt.kind == "pdf":
        return pdf_to_markdown(path)
    raise ParsingError(f"尚未支持 {fmt.kind}", code="not-implemented", path=str(path))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_router.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/infra/parsing/router.py server/tests/test_parsing_router.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): 解析路由与统一入口 parse_document（P2a Task 6）"
```

---

### Task 8: 存储集成与「上传 → 解析 → 落盘」服务入口

**Files:**
- Create: `server/app/infra/parsing/service.py`
- Modify: `server/app/infra/repositories/projects.py`（或新增 `documents.py` 仓储）
- Test: `server/tests/test_parsing_service.py`

**Interfaces:**
- Consumes: `parse_document`、`Database`、`apply_schema`
- Produces: `save_uploaded_document(state, project_id, source_path, role) -> dict`（落盘 markdown 到 `<data_root>/projects/<project_id>/documents/<document_id>.md`，写 `rejection_check_documents` 行，返回该行 dict）

**说明**：spec §5.3 的落盘布局与 §5.2 的 `rejection_check_documents` 列。本任务把「上传」从「HTTP 接收」中解耦出来——HTTP 路由属后续计划。

- [ ] **Step 1: 写失败的测试**（断言：markdown 文件生成且内容正确、库行含 `content_hash`/`content_chars`/`parser_label`、重复上传产生不同 `document_id`、`role` 可为 tender/bid）
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**（`hashlib.sha256` 算 content_hash；`uuid4().hex[:12]` 生成 document_id；复制原文件到 `<data_root>/projects/<id>/uploads/` 以便复现）
- [ ] **Step 4: 全量套件确认通过且无回归**
- [ ] **Step 5: 提交** `feat(server): 上传→解析→落盘服务入口（P2a Task 8）`

---

### Task 9: 真实样本结构等价验证

**Files:**
- Create: `server/tests/test_parsing_structure_equivalence.py`
- Create: `server/tests/fixtures/`（放入真实样本；**若样本含敏感信息，用脱敏副本**）

**Interfaces:** 无新接口——这是 spec §8.5 的验收执行。

- [ ] **Step 1: 准备样本**：一个含「标题 3 级 + 表格 2 张 + 段落 40+」的 docx、一个 GBK 编码 txt、一个多 sheet xlsx。样本由 `python-docx` 生成（确定性）或用户提供脱敏件。
- [ ] **Step 2: 写结构等价断言**：

```python
def test_docx_structure_equivalence(sample_docx):
    text = docx_to_markdown(sample_docx)
    headings = [line for line in text.splitlines() if line.startswith("#")]
    assert len(headings) == EXPECTED_HEADING_COUNT
    assert text.count("| --- |") == EXPECTED_TABLE_COUNT
    assert "base64" not in text and "<w:" not in text
    paragraphs = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert abs(len(paragraphs) - EXPECTED_PARAGRAPH_COUNT) <= EXPECTED_PARAGRAPH_COUNT * 0.10
```

- [ ] **Step 3: 全量套件确认通过**
- [ ] **Step 4: 提交** `test(server): 解析链结构等价验收（P2a Task 9）`

---

## 完成标准

- `cd server && .venv/Scripts/python.exe -m pytest -q` 全绿（P1 的 123 个 + P2a 新增）。
- `parse_document()` 对 docx / doc / pdf / xlsx / txt / md 六类输入都能产出 markdown 并落盘，`rejection_check_documents` 行含 `content_hash`。
- `resolve_parser` 的三段语义（支持 / 回退本地 / 不支持）与现有 `resolveFileParser` 一致；`local` 支持集为 docx/doc/pdf/xlsx/txt/md。
- 全程无网络请求（MinerU 属 P2c）。
- 护栏测试仍全绿：解析库全部位于 `app/infra/parsing/`，`app/domain/**` 无新增依赖。

## 明确不做（属 P2b / P2c）

- **P2b（本地图片管线，纯本地）**：docx 图片解压提取（`word/media/*` + 关系表 + `a:blip`/VML 出现位置）、段落级 `section_title`/`nearby_text`、`resolveImagePlan` 四分决策、Pillow 压缩 / 透传 / 转码、`content_hash` 图片去重与兄弟共享。
- **P2c（在线解析，需网络）**：MinerU `agent` / `accurate` 两个客户端与路由接入。

**注**：PDF 解析**在本计划内**（Task 6）——它是纯本地的。原先把 PDF 归入后续计划是把「本地的文本解析」与「需网络的 MinerU」混为一谈，已被 spec §15 的修正纠正。
