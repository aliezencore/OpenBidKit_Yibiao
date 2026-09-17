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
Expected: PASS（7 passed）

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


# 裁定 R8：统计式编码探测需要足够样本。8 字节的 "投标文件" 连 Node 侧的
# chardet 都会判错（实测判成 windows-1251），因此样本必须是真实量级的中文段落。
GBK_SAMPLE = "投标文件正文：本项目采用公开招标方式，投标人须按招标文件要求编制投标文件并按时递交。"


def test_reads_gbk(tmp_path):
    p = tmp_path / "b.txt"
    p.write_bytes(GBK_SAMPLE.encode("gbk"))
    assert read_text_with_encoding(p) == GBK_SAMPLE


def test_reads_utf8_with_bom(tmp_path):
    p = tmp_path / "c.txt"
    p.write_bytes(b"\xef\xbb\xbf" + "带 BOM 的文本".encode("utf-8"))
    assert read_text_with_encoding(p) == "带 BOM 的文本"


def test_detect_encoding_reports_charset(tmp_path):
    p = tmp_path / "d.txt"
    p.write_bytes(GBK_SAMPLE.encode("gbk"))
    # 裁定 R8：gbk 是 gb18030 的子集，charset-normalizer 与实际 Node chardet 都可能
    # 报 gb18030 / gb2312；三者解码同一份 GBK 文本的结果一致，故都接受。
    assert detect_encoding(p).lower() in {"gb18030", "gbk", "gb2312"}


def test_strips_bom_only_once(tmp_path):
    p = tmp_path / "e.md"
    # 裁定 R7：bytes 字面量只能含 ASCII，含中文必须用 .encode()。
    p.write_bytes(b"\xef\xbb\xbf" + "# 标题\n".encode("utf-8"))
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
Expected: PASS（5 passed）。若 GBK 用例仍不稳，**继续加长 `GBK_SAMPLE`**，不要改实现去猜编码——统计探测的可靠性与样本量成正比，这与 Node 侧行为一致（裁定 R8）。

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
- Consumes: `openpyxl`（**不**抛 `ParsingError`——归一化在 Task 7 的路由层统一做，见裁定 R12）
- Produces: `spreadsheet_to_markdown(path: Path) -> str`（每个 sheet 一个 `## <sheet 名>` 标题 + GFM 表格；空 sheet 输出「（空表）」）

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_parsing_spreadsheet.py`：

```python
from openpyxl import Workbook

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
        # 裁定 R17：add_table(rows=0, cols=0) 会让 python-docx 抛 IndexError
        # （它按行列数建网格）。按实际形状建表再填单元格。
        rendered = doc.add_table(rows=len(table), cols=len(table[0]))
        for row_index, row in enumerate(table):
            for col_index, value in enumerate(row):
                rendered.cell(row_index, col_index).text = value
    doc.save(path)
    return path
```

- [ ] **Step 2: 写失败的测试**

创建 `server/tests/test_parsing_docx.py`：

```python
import pytest

from app.infra.parsing.docx import docx_to_markdown
from app.infra.parsing.markdown import (
    extract_tables,
    html_to_markdown,
    normalize_markdown,
    restore_tables,
)
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


def test_extract_tables_returns_placeholder_and_gfm():
    """裁定 R15：markdownify 自己也能产出 GFM 表格，所以端到端的表格用例
    区分不出「是否走了占位符往返」。这两个 helper 的契约必须由它们自己的
    单测钉住，否则整条占位符机制等于没有测试。"""
    html = "<p>前</p><table><tr><th>项目</th><th>金额</th></tr><tr><td>土建</td><td>100</td></tr></table><p>后</p>"

    html_without_tables, tables = extract_tables(html)

    assert "<table>" not in html_without_tables
    assert "<!--TABLE:0-->" in html_without_tables
    assert tables == ["| 项目 | 金额 |\n| --- | --- |\n| 土建 | 100 |"]
    assert "前" in html_without_tables and "后" in html_without_tables


def test_restore_tables_puts_the_table_back():
    html_without_tables, tables = extract_tables(
        "<table><tr><th>A</th></tr><tr><td>1</td></tr></table>"
    )

    restored = restore_tables(html_without_tables, tables)

    assert "<!--TABLE:0-->" not in restored
    assert "| A |" in restored and "| --- |" in restored and "| 1 |" in restored


def test_extract_tables_ignores_table_without_rows():
    html_without_tables, tables = extract_tables("<table></table><p>x</p>")
    assert tables == []
    assert "x" in html_without_tables


def test_empty_document_yields_empty_string(tmp_path):
    p = build_docx(tmp_path / "empty.docx", blocks=[])
    assert docx_to_markdown(p).strip() == ""


def test_include_images_true_is_rejected(tmp_path):
    """裁定 R2：P2a 不产出图片。mammoth 默认会把整图编成 base64 data URL
    塞进 markdown，污染 LLM 提示词与正文比对；真正实现属 P2b。
    本用例把「拒绝」这一行为钉住，避免它被无声移除。"""
    from app.domain.shared.errors import ParsingError

    p = build_docx(tmp_path / "img.docx", blocks=[("p", "正文")])

    with pytest.raises(ParsingError) as info:
        docx_to_markdown(p, include_images=True)

    assert info.value.code == "not-implemented"
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_docx.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.infra.parsing.docx'`

- [ ] **Step 4: 写实现**

`server/app/infra/parsing/docx_html.py`：

```python
from __future__ import annotations

import mammoth


def docx_to_html(path) -> str:
    """docx → HTML。mammoth 保留语义（标题/列表/加粗），丢弃样式与图片。

    裁定 R14：不带 `include_images` 参数。P2a 里 `docx_to_markdown` 对
    `include_images=True` 已显式拒绝（R2），带上该参数会让 True 分支成为
    不可达死代码。P2b 做真实图片占位时再按需要加参数。
    """
    with open(path, "rb") as handle:
        result = mammoth.convert_to_html(
            handle,
            convert_image=mammoth.images.img_element(lambda image: {"src": ""}),
        )
        html = result.value
    # mammoth 的图片转换器只产出空 src 占位，仍会留下 <img>；统一剥掉，避免空链接。
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for img in soup.find_all("img"):
        img.decompose()
    return str(soup)
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
    html = docx_to_html(Path(path))
    html_without_tables, tables = extract_tables(html)
    markdown = html_to_markdown(html_without_tables)
    markdown = restore_tables(markdown, tables)
    return normalize_markdown(markdown) + "\n"
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_docx.py -v`
Expected: PASS（10 passed）。若 mammoth 对 `add_heading` 产出的标题映射与断言不符（例如产出 `<h1>` 但 markdownify 需要 ATX），按实际输出调整**断言**而不是实现——这一步的验收是结构等价。

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
import pytest

from app.infra.parsing import ParsingError
from app.infra.parsing.legacy_doc import find_soffice, doc_to_markdown


def test_find_soffice_returns_none_or_str():
    # 不能假设测试机装了 LibreOffice；契约是「要么找到要么 None」。
    assert find_soffice() is None or isinstance(find_soffice(), str)


# 裁定 R20：**不要**写「造一个不存在的 .doc 路径再 skip」的用例——它自己构造路径、
# 自己判定不存在、再跳过自己，在任何机器上都不会执行到断言，属零信息量的死用例。
# .doc → docx 的真实转换需要一份真实的 .doc 样本，而本机（以及多数 CI）没有
# LibreOffice，产不出样本，因此**该路径无法在自动化里验证**，如实记为已知缺口
# （见本计划「完成标准」下方），由装了 LibreOffice 的机器手工验证。


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
Expected: PASS（2 passed——本机无 LibreOffice，`find_soffice()` 返回 None，两个用例都不依赖它）

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
    from app.domain.shared.errors import ParsingError

    p = tmp_path / "a.exe"
    p.write_bytes(b"MZ")
    # 裁定 R22：用 pytest.raises(Exception) 不能钉住「必须是 ParsingError」——
    # 任何异常（连 NameError）都能让它变绿。断言到具体类型与码。
    with pytest.raises(ParsingError) as info:
        resolve_parser(p, provider="local")
    assert info.value.code == "unsupported-extension"


def test_parse_document_routes_docx(tmp_path):
    p = build_docx(tmp_path / "a.docx", blocks=[("h1", "标题"), ("p", "正文")])

    result = parse_document(p, provider="local")

    assert isinstance(result, ParsedDocument)
    assert result.format_kind == "docx"
    assert result.parser_label == "local"
    assert "标题" in result.markdown


# 注（裁定 R22）：`supported=False` 这条分支**当前不可达**——`detect_file_format`
# 只返回 docx/doc/txt/markdown/pdf/xlsx 六种 kind，而它们恰好构成
# `_LOCAL_SUPPORTED_KINDS` 的全部；于是 provider == "local" 时 supported 必为真，
# provider != "local" 时必走「回退本地」，结果为真。故 `parse_document` 里
# `if not decision.supported: raise ParsingError(code="parser-unsupported")`
# 是**防御性死分支**，保留作为 `ParserDecision` 不变式的守卫，但**不写用例**
# （写了必然失败）。若 P2c 引入一个「本地不支持、它才支持」的格式，该分支自然可达。


def test_parse_document_routes_txt(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("纯文本内容", encoding="utf-8")

    result = parse_document(p, provider="local")

    assert result.markdown.strip() == "纯文本内容"
    assert result.format_kind == "txt"


def test_parse_document_detects_format(tmp_path):
    p = build_docx(tmp_path / "a.docx", blocks=[("p", "x")])
    assert parse_document(p, provider="local").format_kind == detect_file_format(p).kind


def test_parser_exception_is_normalised_to_parsing_error(tmp_path):
    """裁定 R12：合法 zip 但不是有效 xlsx（如 docx 改名成 .xlsx）时，
    解析器内部的异常必须被归一为 ParsingError，而不是漏给调用方。"""
    import zipfile

    from app.domain.shared.errors import ParsingError

    p = tmp_path / "fake.xlsx"
    with zipfile.ZipFile(p, "w") as archive:
        archive.writestr("not-a-sheet.txt", "x")

    with pytest.raises(ParsingError) as info:
        parse_document(p, provider="local")

    assert info.value.code == "parse-failed"
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
    try:
        if decision.provider == "local":
            markdown = _parse_locally(path, fmt)
        else:
            # MinerU 客户端属 P2c；P2a 阶段请求它会被路由回退到本地，故此处不可达。
            raise ParsingError("MinerU 解析属 P2c 范围", code="not-implemented", path=str(path))
    except ParsingError:
        raise
    except Exception as error:
        # 裁定 R12：各格式解析器只负责解析，不各自包 ParsingError；归一化在这里
        # 一处收口。否则「合法 zip 但不是有效 xlsx」（如 docx 改名成 .xlsx）会漏出
        # 裸 zipfile.BadZipFile / openpyxl 异常，绕过 API 的错误信封。
        raise ParsingError(f"文件解析失败：{error}", code="parse-failed", path=str(path)) from error
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
Expected: PASS（7 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/infra/parsing/router.py server/tests/test_parsing_router.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): 解析路由与统一入口 parse_document（P2a Task 7）"
```

---

### Task 8: 解析落盘服务（写入文件 + 返回值对象）

**Files:**
- Create: `server/app/infra/parsing/service.py`
- Test: `server/tests/test_parsing_service.py`

**Interfaces:**
- Consumes: `parse_document`、`ParsedDocument`、`ParsingError`
- Produces:
  - `SavedDocument`（frozen dataclass）：`document_id: str`、`project_id: str`、`role: str`、`source: str`、`file_name: str`、`source_path: str`、`markdown_path: str`、`content_hash: str`、`content_chars: int`、`parser_label: str`、`format_kind: str`、`extension: str`
  - `save_uploaded_document(*, data_root: Path, project_id: str, source_path: Path, role: str, source: str = "upload", provider: str = "local") -> SavedDocument`

**落盘布局**（spec §5.3）：
```
<data_root>/projects/<project_id>/documents/<document_id>.md   ← 正文
<data_root>/projects/<project_id>/uploads/<document_id><ext>   ← 原件副本
```

**裁定 R24（本任务的关键决定，务必遵守）**：本任务**不写任何数据库表**。
理由：① P2a 的 schema 只有 `projects` / `tasks` / `task_logs` 三张表，`rejection_check_documents` 属 **P4**，此刻不存在；② 让**共享的解析层**去写某个检查域的表是分层错误，查重（P3）同样需要文档簿记，而 spec 目前**没有**给它定义表；③ 因此把「落盘 + 算哈希」与「记进哪个域的表」分开——**前者属 P2a，后者属 P3/P4**。
`SavedDocument` 的字段就是域表要写的列，调用方直接落库即可（`rejection_check_documents` 的列形状见 spec §5.2，与本 dataclass 一一对应）。

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_parsing_service.py`：

```python
import hashlib
from pathlib import Path

import pytest

from app.domain.shared.errors import ParsingError
from app.infra.parsing.service import SavedDocument, save_uploaded_document
from tests.parsing_fixtures import build_docx

ROLE_TENDER = "tender"
ROLE_BID = "bid"


def _upload(tmp_path, *, name="招标文件.docx", blocks=(("h1", "第一章"), ("p", "正文"))):
    source_dir = tmp_path / "inbox"
    source_dir.mkdir(exist_ok=True)
    return build_docx(source_dir / name, blocks=list(blocks))


def test_returns_saved_document_value_object(tmp_path):
    data_root = tmp_path / "data"
    source = _upload(tmp_path)

    saved = save_uploaded_document(
        data_root=data_root, project_id="p1", source_path=source, role=ROLE_TENDER
    )

    assert isinstance(saved, SavedDocument)
    assert saved.project_id == "p1"
    assert saved.role == ROLE_TENDER
    assert saved.source == "upload"
    assert saved.file_name == "招标文件.docx"
    assert saved.parser_label == "local"
    assert saved.format_kind == "docx"
    assert saved.extension == ".docx"
    assert saved.document_id, "document_id 不能为空"


def test_writes_markdown_under_project_scoped_path(tmp_path):
    data_root = tmp_path / "data"
    source = _upload(tmp_path)

    saved = save_uploaded_document(
        data_root=data_root, project_id="p1", source_path=source, role=ROLE_TENDER
    )

    expected = data_root / "projects" / "p1" / "documents" / f"{saved.document_id}.md"
    assert Path(saved.markdown_path) == expected
    assert expected.exists()
    assert "第一章" in expected.read_text(encoding="utf-8")


def test_content_hash_and_chars_describe_the_markdown(tmp_path):
    data_root = tmp_path / "data"
    source = _upload(tmp_path)

    saved = save_uploaded_document(
        data_root=data_root, project_id="p1", source_path=source, role=ROLE_TENDER
    )

    markdown = Path(saved.markdown_path).read_text(encoding="utf-8")
    assert saved.content_hash == hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    assert saved.content_chars == len(markdown)


def test_copies_the_original_upload(tmp_path):
    data_root = tmp_path / "data"
    source = _upload(tmp_path)

    saved = save_uploaded_document(
        data_root=data_root, project_id="p1", source_path=source, role=ROLE_TENDER
    )

    copied = Path(saved.source_path)
    assert copied.exists()
    assert copied.parent == data_root / "projects" / "p1" / "uploads"
    assert copied.name == f"{saved.document_id}.docx"
    assert copied.read_bytes() == source.read_bytes()


def test_two_uploads_of_the_same_file_get_distinct_ids(tmp_path):
    data_root = tmp_path / "data"
    source = _upload(tmp_path)

    first = save_uploaded_document(
        data_root=data_root, project_id="p1", source_path=source, role=ROLE_TENDER
    )
    second = save_uploaded_document(
        data_root=data_root, project_id="p1", source_path=source, role=ROLE_BID
    )

    assert first.document_id != second.document_id
    assert Path(first.markdown_path).exists()
    assert Path(second.markdown_path).exists()
    # 内容相同 → 哈希相同：哈希是本体的指纹，不是槽位编号
    assert first.content_hash == second.content_hash
    assert second.role == ROLE_BID


def test_projects_are_isolated(tmp_path):
    data_root = tmp_path / "data"
    source = _upload(tmp_path)

    a = save_uploaded_document(
        data_root=data_root, project_id="p1", source_path=source, role=ROLE_TENDER
    )
    b = save_uploaded_document(
        data_root=data_root, project_id="p2", source_path=source, role=ROLE_TENDER
    )

    assert Path(a.markdown_path).parent == data_root / "projects" / "p1" / "documents"
    assert Path(b.markdown_path).parent == data_root / "projects" / "p2" / "documents"


def test_parse_failure_leaves_no_artifacts(tmp_path):
    """裁定 R24：落盘前必须先把解析做完。解析失败时不得留下半成品目录或文件。"""
    data_root = tmp_path / "data"
    bad = tmp_path / "broken.xlsx"
    bad.write_bytes(b"PK\x03\x04" + b"\x00" * 32)  # zip 魔数正确、内容不是 xlsx

    with pytest.raises(ParsingError) as info:
        save_uploaded_document(
            data_root=data_root, project_id="p1", source_path=bad, role=ROLE_TENDER
        )

    assert info.value.code == "parse-failed"
    documents_dir = data_root / "projects" / "p1" / "documents"
    assert not documents_dir.exists() or list(documents_dir.iterdir()) == []
    uploads_dir = data_root / "projects" / "p1" / "uploads"
    assert not uploads_dir.exists() or list(uploads_dir.iterdir()) == []


def test_missing_source_raises_and_leaves_no_artifacts(tmp_path):
    data_root = tmp_path / "data"

    with pytest.raises(Exception):
        save_uploaded_document(
            data_root=data_root,
            project_id="p1",
            source_path=tmp_path / "nope.docx",
            role=ROLE_TENDER,
        )

    documents_dir = data_root / "projects" / "p1" / "documents"
    assert not documents_dir.exists() or list(documents_dir.iterdir()) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_service.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.infra.parsing.service'`

- [ ] **Step 3: 写实现**

创建 `server/app/infra/parsing/service.py`：

```python
from __future__ import annotations

import hashlib
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.infra.parsing.router import parse_document


@dataclass(frozen=True)
class SavedDocument:
    """落盘结果。字段与域表要写的列一一对应（spec §5.2），由调用方落库。"""

    document_id: str
    project_id: str
    role: str
    source: str
    file_name: str
    source_path: str
    markdown_path: str
    content_hash: str
    content_chars: int
    parser_label: str
    format_kind: str
    extension: str


def save_uploaded_document(
    *,
    data_root: Path,
    project_id: str,
    source_path: Path,
    role: str,
    source: str = "upload",
    provider: str = "local",
) -> SavedDocument:
    """解析 → 落盘 markdown → 复制原件 → 返回值对象。**不写数据库**（裁定 R24）。

    顺序很关键（裁定 R24）：**先解析、后落盘**。解析失败时抛 `ParsingError`，
    此时一个字节都没写过，因此不会留下半成品目录——`test_parse_failure_leaves_no_artifacts`
    钉住这一点。若反过来先建目录再解析，失败就会留下空目录与已复制的原件。
    """
    data_root = Path(data_root)
    source_path = Path(source_path)

    # 1) 解析（失败即抛，此时尚未写任何文件）
    parsed = parse_document(source_path, provider=provider)

    # 2) 生成 id 与目标路径
    document_id = uuid.uuid4().hex[:12]
    project_dir = data_root / "projects" / project_id
    documents_dir = project_dir / "documents"
    uploads_dir = project_dir / "uploads"
    documents_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    markdown_path = documents_dir / f"{document_id}.md"
    copied_path = uploads_dir / f"{document_id}{parsed.extension}"

    # 3) 落盘：正文与原件副本
    markdown_path.write_text(parsed.markdown, encoding="utf-8")
    shutil.copyfile(source_path, copied_path)

    return SavedDocument(
        document_id=document_id,
        project_id=project_id,
        role=role,
        source=source,
        file_name=source_path.name,
        source_path=str(copied_path),
        markdown_path=str(markdown_path),
        content_hash=hashlib.sha256(parsed.markdown.encode("utf-8")).hexdigest(),
        content_chars=len(parsed.markdown),
        parser_label=parsed.parser_label,
        format_kind=parsed.format_kind,
        extension=parsed.extension,
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_service.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/infra/parsing/service.py server/tests/test_parsing_service.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): 解析落盘服务，写入正文与原件并返回值对象（P2a Task 8）"
```

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
- `parse_document()` 对 docx / doc / pdf / xlsx / txt / md 六类输入都能产出 markdown；`save_uploaded_document()` 把正文与原件副本落到 `<data_root>/projects/<project_id>/` 下并返回含 `content_hash` 的 `SavedDocument`。**数据库行由 P3/P4 在各自的域表里写**（裁定 R24）。
- `resolve_parser` 的三段语义（支持 / 回退本地 / 不支持）与现有 `resolveFileParser` 一致；`local` 支持集为 docx/doc/pdf/xlsx/txt/md。
- 全程无网络请求（MinerU 属 P2c）。
- **已知验证缺口（裁定 R20）**：`.doc → docx` 的**真实转换**未被自动化覆盖——需要一份真实 `.doc` 样本，而样本要靠 LibreOffice 产出、本机没有。自动化只覆盖 `find_soffice()` 的契约与「LibreOffice 缺失时报 `libreoffice-missing`」。装了 LibreOffice 的机器上应手工验证一次真实 `.doc`。
- 护栏测试仍全绿：解析库全部位于 `app/infra/parsing/`，`app/domain/**` 无新增依赖。

## 明确不做（属 P2b / P2c）

- **P2b（本地图片管线，纯本地）**：docx 图片解压提取（`word/media/*` + 关系表 + `a:blip`/VML 出现位置）、段落级 `section_title`/`nearby_text`、`resolveImagePlan` 四分决策、Pillow 压缩 / 透传 / 转码、`content_hash` 图片去重与兄弟共享。
- **P2c（在线解析，需网络）**：MinerU `agent` / `accurate` 两个客户端与路由接入。

**注**：PDF 解析**在本计划内**（Task 6）——它是纯本地的。原先把 PDF 归入后续计划是把「本地的文本解析」与「需网络的 MinerU」混为一谈，已被 spec §15 的修正纠正。
