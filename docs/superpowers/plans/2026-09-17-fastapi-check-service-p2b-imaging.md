# P2b 本地图片管线 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从 docx 里提取图片资产（含关系表、出现位置、段落级上下文），按现有 `resolveImagePlan` 的四分策略产出可交给视觉模型的图像，并按 `content_hash` 去重落盘。行为对齐现有 `docxVisualAssetExtractor.cjs` 与 `utils/imageFormat.cjs`。

**Architecture:** 纯本地、零网络（spec §8.3）。四个新模块各司其职：`docx_assets.py`（解压 + XML 提取）、`image_plan.py`（决策，无 I/O）、`image_codec.py`（Pillow 编解码，无决策）、`asset_store.py`（去重 + 落盘）。**决策与编解码分离**，这样四分策略可以纯函数单测、无需构造真实图片。

**Tech Stack:** stdlib `zipfile` / `hashlib` / `re` + **Pillow**（新增唯一依赖）

**Spec:** `docs/superpowers/specs/2026-09-15-fastapi-check-service-design.md` §8.3 / §8.4.1

**明确不做（属 P4 印章检查）**：把图像上传给视觉模型的批次循环、批次失败降级为逐张重试、`skipKind` 的 `not-seal-area` 分类、`stats.skippedAssets` 聚合。P2b 只把「图像准备好 + 凭什么跳过」这件事做完。

## Global Constraints

- 工作区 `.worktrees/fastapi-check-service`，分支 `fastapi-check-service`（**平铺名，不得含 `/`**）。
- **不得改动 `client/` 下任何文件**。
- 在 P2a 之上增量开发；**不得破坏既有 189 个测试**。
- 提交身份 `git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit`；**不推送**；按显式路径 `git add`。
- `app/domain/**` 禁止 import `fastapi` / `starlette` / `sqlalchemy` / `httpx` / `pydantic` / `sqlite3`（护栏测试强制）。Pillow 只能出现在 `app/infra/parsing/`。
- **新增依赖仅 `Pillow`。**
- 验收标准是**结构等价**（spec §8.5 的精神）：资产数量、出现位置、章节上下文、跳过的码要对得上；**不要求**与 Electron 侧的压缩字节逐字节一致。
- **保留四分动作与全部跳过码**（spec §8.3 第 3 条）：`compress` / `passthrough` / `transcode` / `skip`，跳过码 `UNSUPPORTED_IMAGE_FORMAT` / `PASSTHROUGH_TOO_LARGE`。理由：印章 VLM 的输入形态与「未能识别的图片」清单都依赖这些码，改动会造成 P4 行为漂移。

## File Structure

| 文件 | 职责 |
|---|---|
| `server/pyproject.toml` | 增 `Pillow` |
| `server/app/infra/parsing/docx_assets.py` | 解压 + 关系表 + 段落索引 + 出现位置 + 媒体清单 |
| `server/app/infra/parsing/image_plan.py` | `resolve_image_plan` / `compute_target_size` / 魔数识别（纯函数，无 I/O） |
| `server/app/infra/parsing/image_codec.py` | Pillow 压缩 / 透传 / 转码，产出 data URL |
| `server/app/infra/parsing/asset_store.py` | 按 `content_hash` 去重 + 落盘 assets |
| `server/tests/parsing_fixtures.py` | **增** `build_docx_with_images`（Pillow 生成合法图片） |
| `server/tests/test_parsing_docx_assets.py` / `test_image_plan.py` / `test_image_codec.py` / `test_asset_store.py` | 各任务测试 |

---

### Task 1: 依赖、媒体文件清单与图片夹具

**Files:**
- Modify: `server/pyproject.toml`、`server/tests/parsing_fixtures.py`
- Create: `server/app/infra/parsing/docx_assets.py`
- Test: `server/tests/test_parsing_docx_assets.py`

**Interfaces:**
- Consumes: `ParsingError`
- Produces: `MediaFile`（frozen dataclass：`media_name: str`（如 `"media/image1.png"`）、`data: bytes`、`content_hash: str`、`mime_type: str`、`extension: str`）；`list_media_files(docx_path: Path) -> list[MediaFile]`；夹具 `build_docx_with_images(path, *, blocks=..., images=...) -> Path`

- [ ] **Step 1: 加依赖并安装**

`server/pyproject.toml` 的 `[project] dependencies` 追加 `"Pillow>=10.0"`，然后：

```bash
cd server && .venv/Scripts/python.exe -m pip install -e ".[dev]"
```

> 已知风险：Windows 上 `pip install -e` 卸载 editable 包时可能报 `WinError 3` 并删掉 `.venv/Lib/`（P2a Task 6 实际发生过）。若发生，重建 venv 并重装，然后在报告里如实记录。

- [ ] **Step 2: 给夹具加图片支持**

在 `server/tests/parsing_fixtures.py` 追加（**保留既有 `build_docx` 签名不变**）：

```python
from PIL import Image


def make_image(path, *, size=(40, 30), color=(200, 30, 30), fmt="PNG"):
    """用 Pillow 造一张合法图片。P2b 起测试可以自己产图，不再需要手搓字节。"""
    Image.new("RGB", size, color).save(path, format=fmt)
    return path


def build_docx_with_images(path, *, blocks=None, images=None, image_paragraph=True):
    """blocks: [(kind, text)]；images: [图片路径]，每张插入一个段落。

    image_paragraph=True 时图片独占一个段落（这是真实标书里最常见的情形，
    也才能触发 extract_nearby_text 的「前后各取 2 段」分支）。
    """
    doc = Document()
    for kind, text in blocks or []:
        if kind == "h1":
            doc.add_heading(text, level=1)
        elif kind == "h2":
            doc.add_heading(text, level=2)
        else:
            doc.add_paragraph(text)
    for image_path in images or []:
        doc.add_picture(str(image_path))
        if not image_paragraph:
            doc.paragraphs[-1].add_run(" 随图文字")
    doc.save(path)
    return path
```

- [ ] **Step 3: 写失败的测试**

创建 `server/tests/test_parsing_docx_assets.py`：

```python
import hashlib

import pytest

from app.domain.shared.errors import ParsingError
from app.infra.parsing.docx_assets import list_media_files
from tests.parsing_fixtures import build_docx_with_images, make_image


def _docx_with_two_images(tmp_path):
    first = make_image(tmp_path / "a.png", size=(40, 30), color=(200, 30, 30))
    second = make_image(tmp_path / "b.jpg", size=(60, 20), color=(30, 30, 200), fmt="JPEG")
    return build_docx_with_images(
        tmp_path / "with-images.docx",
        blocks=[("p", "正文段落")],
        images=[first, second],
    )


def test_lists_every_media_entry(tmp_path):
    source = _docx_with_two_images(tmp_path)

    media = list_media_files(source)

    assert len(media) == 2
    assert sorted(item.extension for item in media) == [".jpg", ".png"]
    assert all(item.media_name.startswith("media/") for item in media)


def test_content_hash_is_sha256_of_bytes(tmp_path):
    source = _docx_with_two_images(tmp_path)

    media = list_media_files(source)

    for item in media:
        assert item.content_hash == hashlib.sha256(item.data).hexdigest()
        assert item.content_hash != ""


def test_mime_type_comes_from_extension(tmp_path):
    source = _docx_with_two_images(tmp_path)

    by_extension = {item.extension: item.mime_type for item in list_media_files(source)}

    assert by_extension[".png"] == "image/png"
    assert by_extension[".jpg"] == "image/jpeg"


def test_no_media_means_empty_list(tmp_path):
    from tests.parsing_fixtures import build_docx

    plain = build_docx(tmp_path / "plain.docx", blocks=[("p", "没有图")])

    assert list_media_files(plain) == []


def test_missing_document_xml_raises(tmp_path):
    """裁定：docx 不是有效 zip、或缺 word/document.xml 时，抛 ParsingError 而非裸异常。"""
    import zipfile

    broken = tmp_path / "broken.docx"
    with zipfile.ZipFile(broken, "w") as archive:
        archive.writestr("not-word.txt", "x")

    with pytest.raises(ParsingError) as info:
        list_media_files(broken)

    assert info.value.code == "docx-open-failed"
```

- [ ] **Step 4: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_docx_assets.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.infra.parsing.docx_assets'`

- [ ] **Step 5: 写实现**

`server/app/infra/parsing/docx_assets.py`（本任务只放媒体清单，其余在后续任务补）：

```python
from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.domain.shared.errors import ParsingError

# 对齐现有 docxVisualAssetExtractor.cjs:13 的 MIME_BY_EXTENSION
MIME_BY_EXTENSION = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".emf": "image/emf",
    ".wmf": "image/wmf",
}


@dataclass(frozen=True)
class MediaFile:
    media_name: str      # 如 "media/image1.png"（去掉前导 word/，对齐 Node）
    data: bytes
    content_hash: str
    mime_type: str
    extension: str


def open_docx_zip(path: Path) -> zipfile.ZipFile:
    """打开 docx。失败一律归一为 ParsingError（本模块唯一的 error code 出口）。"""
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        raise ParsingError(
            "文件解析失败：不是有效的 DOCX 文档",
            code="docx-open-failed",
            path=str(path),
        ) from error


def list_media_files(docx_path: Path) -> list[MediaFile]:
    """列出 `word/media/*` 下的每个文件（对齐 extractDocxVisualAssets 的 mediaFiles）。"""
    docx_path = Path(docx_path)
    with open_docx_zip(docx_path) as archive:
        if "word/document.xml" not in archive.namelist():
            raise ParsingError(
                "DOCX 缺少 word/document.xml，无法提取图片",
                code="docx-open-failed",
                path=str(docx_path),
            )
        media: list[MediaFile] = []
        for name in archive.namelist():
            # 只收一级目录下的文件：`word/media/a/b.png` 不是标准布局
            if not name.startswith("word/media/") or "/" in name[len("word/media/"):]:
                continue
            data = archive.read(name)
            extension = Path(name).suffix.lower()
            media.append(
                MediaFile(
                    media_name=name.replace("word/", "", 1),
                    data=data,
                    content_hash=hashlib.sha256(data).hexdigest(),
                    mime_type=MIME_BY_EXTENSION.get(extension, "application/octet-stream"),
                    extension=extension,
                )
            )
    return media
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_parsing_docx_assets.py -v`
Expected: PASS（5 passed）

- [ ] **Step 7: 提交**

```bash
git add server/pyproject.toml server/app/infra/parsing/docx_assets.py server/tests/parsing_fixtures.py server/tests/test_parsing_docx_assets.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): docx 媒体清单与图片夹具（P2b Task 1）"
```

---

### Task 2: 关系表、实体反转义与可见文本提取

**Files:**
- Modify: `server/app/infra/parsing/docx_assets.py`
- Test: `server/tests/test_parsing_docx_assets.py`

**Interfaces:**
- Consumes: 无
- Produces: `parse_relationships(xml: str) -> dict[str, str]`（`Id -> Target`，Target 去掉前导 `/`）；`unescape_xml(text: str) -> str`；`extract_run_text(block: str) -> str`

**为什么 `extract_run_text` 只拼 `<w:t>`**：现有实现（`docxVisualAssetExtractor.cjs:48`）刻意不整段剥标签——`<wp:posOffset>1143000</wp:posOffset>` 这类坐标的**数字本身就是元素文本**，剥完标签会留下一串数字残渣（本任务夹具里是 `1143000132715`，无空白直接首尾相接），既污染喂给模型的上下文，又可能被章节编号规则误判成章节。这条注释必须随代码保留。

> 裁定 R6（T2 评审指出，供 T3 注意）：残渣要**真的**触发章节规则 `^\d+(\.\d+)*[\s、.]`，数字后必须跟分隔符——所以 T3 的夹具**刻意**保留带空格的形式 `"1143000 132715"`，**不要**改成无空格版，否则那条负例什么也证明不了。T3 实现侧的 `CJK_PATTERN` 兜底（含中文才认编号）同样要在，两条防线都不能少。

- [ ] **Step 1: 写失败的测试**

追加到 `server/tests/test_parsing_docx_assets.py`：

```python
from app.infra.parsing.docx_assets import extract_run_text, parse_relationships, unescape_xml


def test_parses_relationship_id_to_target():
    xml = (
        '<Relationships>'
        '<Relationship Id="rId4" Type="t" Target="media/image1.png"/>'
        '<Relationship Id="rId5" Type="t" Target="/word/media/image2.jpeg"/>'
        '</Relationships>'
    )

    rels = parse_relationships(xml)

    assert rels["rId4"] == "media/image1.png"
    assert rels["rId5"] == "word/media/image2.jpeg"  # 前导 / 被去掉


def test_ignores_relationship_without_id_or_target():
    xml = '<Relationship Type="t" Target="x"/><Relationship Id="rId1"/>'

    assert parse_relationships(xml) == {}


def test_unescapes_the_five_entities():
    assert unescape_xml("a&lt;b&gt;c&amp;d&quot;e&apos;f") == "a<b>c&d\"e'f"


def test_entity_replacement_is_single_pass():
    """裁定 R5：**替换顺序必须被钉住**。

    上一条用例在正反两种顺序下都通过（`c&amp;d` 两种顺序结果相同），所以
    「`&lt;`/`&gt;` 先于 `&amp;`」这一关键顺序此前无任何保护。顺序反了会让
    `&amp;lt;` 二次反转义成 `<`（本应是字面量 `&lt;`），是把文档里的
    「&lt;」显示成「<」这类静默损坏。这条断言就是那个钉子。
    """
    assert unescape_xml("&amp;lt;") == "&lt;"


def test_extract_run_text_joins_only_visible_runs():
    block = (
        '<w:p><w:r><w:t>第一</w:t></w:r>'
        '<w:r><w:t xml:space="preserve"> 段 </w:t></w:r>'
        '<w:r><w:t>文字</w:t></w:r></w:p>'
    )

    assert extract_run_text(block) == "第一 段 文字"


def test_extract_run_text_ignores_position_offsets():
    """坐标是元素文本，绝不能被当成可见文字——否则会污染上下文并被误判成章节编号。"""
    block = (
        '<w:p><wp:posOffset>1143000</wp:posOffset>'
        '<w:r><w:t>真正的正文</w:t></w:r>'
        '<wp:posOffset>132715</wp:posOffset></w:p>'
    )

    text = extract_run_text(block)

    assert text == "真正的正文"
    assert "1143000" not in text


def test_extract_run_text_collapses_whitespace():
    block = "<w:p><w:r><w:t>a   \n  b</w:t></w:r></w:p>"
    assert extract_run_text(block) == "a b"
```

- [ ] **Step 2: 跑测试确认失败**

Expected: FAIL，`ImportError: cannot import name 'extract_run_text'`

- [ ] **Step 3: 写实现**

追加到 `docx_assets.py`：

```python
import re

_RELATIONSHIP_RE = re.compile(r"<Relationship\b[^>]*>")
_RELATIONSHIP_ID_RE = re.compile(r'\bId="([^"]+)"')
_RELATIONSHIP_TARGET_RE = re.compile(r'\bTarget="([^"]+)"')
_RUN_TEXT_RE = re.compile(r"<w:t\b[^>]*>([\s\S]*?)</w:t>")
_WHITESPACE_RE = re.compile(r"\s+")

_ENTITIES = (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"), ("&quot;", '"'), ("&apos;", "'"))


def parse_relationships(xml: str) -> dict[str, str]:
    """`Id -> Target`，Target 去掉前导 `/`（对齐 parseRelationships）。"""
    relationships: dict[str, str] = {}
    for tag in _RELATIONSHIP_RE.findall(xml or ""):
        identifier = _RELATIONSHIP_ID_RE.search(tag)
        target = _RELATIONSHIP_TARGET_RE.search(tag)
        if identifier and target:
            relationships[identifier.group(1)] = target.group(1).lstrip("/")
    return relationships


def unescape_xml(text: str) -> str:
    result = text or ""
    for entity, character in _ENTITIES:
        result = result.replace(entity, character)
    return result


def extract_run_text(block: str) -> str:
    """只拼接 `<w:t>`（可见文本）。

    **不要**改成整段剥标签：`<wp:posOffset>1143000</wp:posOffset>` 这类坐标的数字
    本身就是元素文本，剥完标签会留下数字残渣，既污染喂给模型的上下文，
    又会被章节编号规则（如 `^\\d+(\\.\\d+)*[\\s、.]`）误判成章节标题。
    """
    runs = [unescape_xml(raw) for raw in _RUN_TEXT_RE.findall(block or "")]
    return _WHITESPACE_RE.sub(" ", "".join(runs)).strip()
```

- [ ] **Step 4: 跑测试确认通过**

Expected: PASS（T2 新增 7 个 → 累计 14 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/infra/parsing/docx_assets.py server/tests/test_parsing_docx_assets.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): 关系表解析与可见文本提取（P2b Task 2）"
```

---

### Task 3: 段落索引与章节上下文

**Files:**
- Modify: `server/app/infra/parsing/docx_assets.py`
- Test: `server/tests/test_parsing_docx_assets.py`

**Interfaces:**
- Consumes: `extract_run_text`
- Produces: `Paragraph`（dataclass：`text: str`、`style_val: str`、`bold: bool`）；`ParagraphIndex`（dataclass：`paragraphs: list[Paragraph]`、`positions: list[tuple[int, int]]`）；`build_paragraph_index(xml: str) -> ParagraphIndex`；`find_paragraph_position(index: ParagraphIndex, char_index: int) -> int`；`is_heading_paragraph(paragraph: Paragraph) -> bool`；`extract_section_title(index: ParagraphIndex, position: int) -> str | None`；`extract_nearby_text(index: ParagraphIndex, position: int) -> str`

**常量**（对齐 Node）：`MAX_SECTION_TITLE_LENGTH = 40`、`MAX_NEARBY_TEXT_LENGTH = 200`、`NEARBY_PARAGRAPH_RADIUS = 2`。

- [ ] **Step 1: 写失败的测试**

追加：

```python
from app.infra.parsing.docx_assets import (
    MAX_SECTION_TITLE_LENGTH,
    build_paragraph_index,
    extract_nearby_text,
    extract_section_title,
    find_paragraph_position,
    is_heading_paragraph,
)


def _paragraphs(*specs):
    """specs: [(text, style_val, bold)] -> 一段 docx XML。"""
    blocks = []
    for text, style, bold in specs:
        style_xml = f'<w:pStyle w:val="{style}"/>' if style else ""
        bold_xml = "<w:b/>" if bold else ""
        blocks.append(f"<w:p><w:pPr>{style_xml}</w:pPr><w:r><w:rPr>{bold_xml}</w:rPr><w:t>{text}</w:t></w:r></w:p>")
    return "<w:body>" + "".join(blocks) + "</w:body>"


def test_builds_one_entry_per_paragraph():
    index = build_paragraph_index(_paragraphs(("甲", "", False), ("乙", "", False)))
    assert [p.text for p in index.paragraphs] == ["甲", "乙"]
    assert len(index.positions) == 2


def test_heading_by_style_beats_length():
    long_heading = "很长的一级标题" * 10
    index = build_paragraph_index(_paragraphs((long_heading, "Heading1", False)))
    assert is_heading_paragraph(index.paragraphs[0]) is True


def test_chinese_numbering_counts_as_heading():
    index = build_paragraph_index(
        _paragraphs(("第一章 总体要求", "", False), ("1.2.3 建设目标", "", False),
                    ("（一）一般要求", "", False), ("一、总则", "", False))
    )
    assert all(is_heading_paragraph(p) for p in index.paragraphs)


def test_plain_sentence_is_not_a_heading():
    index = build_paragraph_index(_paragraphs(("本项目采用公开招标方式，投标人须按要求编制投标文件。", "", False)))
    assert is_heading_paragraph(index.paragraphs[0]) is False


def test_coordinates_without_cjk_are_not_headings():
    """纯数字/无中文的段落不得命中编号规则。"""
    index = build_paragraph_index(_paragraphs(("1143000 132715", "", False), ("1.2", "", False)))
    assert all(is_heading_paragraph(p) is False for p in index.paragraphs)


def test_over_long_paragraph_is_not_a_heading():
    """裁定 R8：夹具必须**同时**命中编号规则与长度上限，否则长度守卫无人钉住。

    原夹具 `"第" + "一"*45` 末尾没有「章/节/篇」，压根不匹配
    `^第[…]+[章节篇]`——于是它靠「编号不匹配」通过，删掉长度检查也照样绿。
    改写后的段落以「第一章 」开头（命中编号规则）且长度远超 40，所以
    只有长度守卫能把它挡下来。
    """
    text = "第一章 " + "很长的正文内容" * 10
    assert len(text) > MAX_SECTION_TITLE_LENGTH

    index = build_paragraph_index(_paragraphs((text, "", False)))

    assert is_heading_paragraph(index.paragraphs[0]) is False


def test_section_title_prefers_nearest_heading():
    index = build_paragraph_index(_paragraphs(("第一章 总则", "", False), ("正文", "", False)))
    assert extract_section_title(index, 1) == "第一章 总则"


def test_section_title_falls_back_to_bold_short_paragraph():
    index = build_paragraph_index(_paragraphs(("加粗小标题", "", True), ("正文", "", False)))
    assert extract_section_title(index, 1) == "加粗小标题"


def test_section_title_is_none_without_any_candidate():
    index = build_paragraph_index(_paragraphs(("普通的一段话，不算标题。", "", False)))
    assert extract_section_title(index, 1) is None


def test_nearby_text_prefers_own_paragraph():
    index = build_paragraph_index(_paragraphs(("上一段", "", False), ("自己的文字", "", False), ("下一段", "", False)))
    assert extract_nearby_text(index, 1) == "自己的文字"


def test_nearby_text_takes_two_before_and_two_after_when_own_is_empty():
    """顺序是**先近后远**（距离 1、2），不是文档顺序——对齐参考实现的循环。

    参考 `cjs:126-136` 用 `for offset = 1..2` 先取 `position - 1` 再取 `position - 2`，
    产出 `前二 更前`；照抄即得下面的期望。**不要**因为「文档顺序读起来更顺」
    就把它改成 `更前 前二`——那会偏离参考，且这条 200 字的上下文提示串
    顺序差异对下游没有实质影响，没有理由为此产生分歧。
    """
    index = build_paragraph_index(_paragraphs(
        ("更前", "", False), ("前二", "", False), ("", "", False), ("后一", "", False), ("后二", "", False),
    ))
    assert extract_nearby_text(index, 2) == "前二 更前 后一 后二"


def test_nearby_text_is_truncated():
    long_text = "字" * 500
    index = build_paragraph_index(_paragraphs(("", "", False), (long_text, "", False)))
    assert len(extract_nearby_text(index, 0)) == 200


def test_find_paragraph_position_locates_char_index():
    index = build_paragraph_index(_paragraphs(("甲", "", False), ("乙", "", False)))
    second_start = index.positions[1][0]
    assert find_paragraph_position(index, second_start) == 1
    assert find_paragraph_position(index, index.positions[0][0]) == 0
    assert find_paragraph_position(index, 10 ** 9) == -1
```

- [ ] **Step 2: 跑测试确认失败**

Expected: FAIL，`ImportError`

- [ ] **Step 3: 写实现**

追加到 `docx_assets.py`：

```python
# 中文标书大量使用纯文本编号当章节，并不使用 Word 标题样式（对齐 Node）
CHAPTER_TEXT_PATTERNS = (
    re.compile(r"^第[一二三四五六七八九十百千0-9]+[章节篇]"),
    re.compile(r"^[一二三四五六七八九十]+、"),
    re.compile(r"^（[一二三四五六七八九十]+）"),
    re.compile(r"^\d+(\.\d+)*[\s、.]"),
)
HEADING_STYLE_PATTERN = re.compile(r"^(heading\s*[1-9]|标题\s*[1-9一二三四五六七八九十])$", re.I)
# 章节候选必须含中文，否则纯数字/坐标残渣会命中上面的编号规则
CJK_PATTERN = re.compile(r"[\u4e00-\u9fa5]")

MAX_SECTION_TITLE_LENGTH = 40
MAX_NEARBY_TEXT_LENGTH = 200
NEARBY_PARAGRAPH_RADIUS = 2

_PARAGRAPH_RE = re.compile(r"<w:p\b[^>]*>[\s\S]*?</w:p>")
_PARAGRAPH_STYLE_RE = re.compile(r'<w:pStyle\b[^>]*w:val="([^"]+)"')
_BOLD_RE = re.compile(r"<w:b\b[^>]*/?>")


@dataclass(frozen=True)
class Paragraph:
    text: str
    style_val: str
    bold: bool


@dataclass(frozen=True)
class ParagraphIndex:
    paragraphs: list[Paragraph]
    positions: list[tuple[int, int]]


def build_paragraph_index(xml: str) -> ParagraphIndex:
    """一次性把 XML 切成段落并预计算特征，避免每张图片都重扫全文。"""
    source = xml or ""
    paragraphs: list[Paragraph] = []
    positions: list[tuple[int, int]] = []
    for match in _PARAGRAPH_RE.finditer(source):
        block = match.group(0)
        style = _PARAGRAPH_STYLE_RE.search(block)
        paragraphs.append(
            Paragraph(
                text=extract_run_text(block),
                style_val=style.group(1) if style else "",
                bold=bool(_BOLD_RE.search(block)),
            )
        )
        positions.append((match.start(), match.end()))
    return ParagraphIndex(paragraphs=paragraphs, positions=positions)


def find_paragraph_position(index: ParagraphIndex, char_index: int) -> int:
    """自后向前找包含该偏移的段落；找不到返回 -1（对齐 findParagraphPosition）。"""
    for position in range(len(index.positions) - 1, -1, -1):
        start, end = index.positions[position]
        if start <= char_index < end:
            return position
    return -1


def is_heading_paragraph(paragraph: Paragraph) -> bool:
    if not paragraph or not paragraph.text:
        return False
    if paragraph.style_val and HEADING_STYLE_PATTERN.match(paragraph.style_val.strip()):
        return True
    if len(paragraph.text) > MAX_SECTION_TITLE_LENGTH:
        return False
    if not CJK_PATTERN.search(paragraph.text):
        return False
    return any(pattern.match(paragraph.text) for pattern in CHAPTER_TEXT_PATTERNS)


def extract_section_title(index: ParagraphIndex, position: int) -> str | None:
    """向前全篇回溯：先找标题样式/编号段落，再退一步找加粗短段落，都没有返回 None。

    **不加**「上一个非空段落」兜底——那会把正文首句当成章节名。
    """
    for cursor in range(position - 1, -1, -1):
        candidate = index.paragraphs[cursor]
        if candidate.text and is_heading_paragraph(candidate):
            return candidate.text
    for cursor in range(position - 1, -1, -1):
        candidate = index.paragraphs[cursor]
        if not candidate.text or len(candidate.text) > MAX_SECTION_TITLE_LENGTH:
            continue
        if candidate.bold and CJK_PATTERN.search(candidate.text):
            return candidate.text
    return None


def extract_nearby_text(index: ParagraphIndex, position: int) -> str:
    """图片所在段落的文本；该段为空时（图片独占段落）取前后各最多 2 个有文本的段落。"""
    if position < 0:
        return ""
    own = index.paragraphs[position].text if position < len(index.paragraphs) else ""
    if own:
        return own[:MAX_NEARBY_TEXT_LENGTH]

    parts: list[str] = []
    # 顺序刻意是「先近后远」——与参考实现（cjs:126-136）的 offset 递增循环一致。
    # 不是文档顺序；不要为了「读起来更顺」而反转，那会偏离参考。
    for offset in range(1, NEARBY_PARAGRAPH_RADIUS + 1):
        before = position - offset
        if before >= 0 and index.paragraphs[before].text:
            parts.append(index.paragraphs[before].text)
    for offset in range(1, NEARBY_PARAGRAPH_RADIUS + 1):
        after = position + offset
        if after < len(index.paragraphs) and index.paragraphs[after].text:
            parts.append(index.paragraphs[after].text)
    return " ".join(parts)[:MAX_NEARBY_TEXT_LENGTH]
```

- [ ] **Step 4: 跑测试确认通过**

Expected: PASS（T3 新增 13 个 → 累计 26 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/infra/parsing/docx_assets.py server/tests/test_parsing_docx_assets.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): 段落索引与章节上下文提取（P2b Task 3）"
```

---

### Task 4: Drawing / VML 出现位置与资产分类

**Files:**
- Modify: `server/app/infra/parsing/docx_assets.py`
- Test: `server/tests/test_parsing_docx_assets.py`

**Interfaces:**
- Consumes: `parse_relationships`、`ParagraphIndex`、`extract_nearby_text`、`extract_section_title`、`find_paragraph_position`
- Produces: `AssetOccurrence`（frozen dataclass：`media_name: str`、`relationship_id: str`、`placement: str`（`"inline"` / `"anchor"`）、`width: int`、`height: int`、`asset_kind: str`、`location_hint: str`、`nearby_text: str`、`section_title: str | None`）；`classify_asset_kind(width: int, height: int, placement: str) -> str`；`collect_drawing_occurrences(xml, relationships, part_label, index) -> list[AssetOccurrence]`；`collect_vml_occurrences(xml, relationships, part_label, index) -> list[AssetOccurrence]`

**常量**（对齐 Node）：`EMU_PER_PX = 9525`、`PT_TO_EMU = 12700`、`PAGE_SCAN_MIN_WIDTH_PX = 500`、`PAGE_SCAN_MIN_HEIGHT_PX = 650`。

- [ ] **Step 1: 写失败的测试**

追加：

```python
from app.infra.parsing.docx_assets import (
    EMU_PER_PX,
    PT_TO_EMU,
    classify_asset_kind,
    collect_drawing_occurrences,
    collect_vml_occurrences,
)

# 注（裁定 R10）：`EMU_PER_PX` / `PT_TO_EMU` 只在本文件里用于**换算推导的注释**，
# 断言一律用字面量。若最终这两个名字确实没被引用，从 import 里去掉即可——
# **不要**为了让它们「有用」而把断言改回自指的表达式。


def _drawing(embed, *, cx=381000, cy=285750, anchor=False):
    """cx/cy 用**字面量** EMU 值（裁定 R10）。

    写成 `EMU_PER_PX * 40` 会让「40px」这类期望变成自指的：换算常量错掉也照样绿。
    381000 EMU ÷ 9525 = 40px；285750 ÷ 9525 = 30px——同样由字面量钉住。
    """
    tag = "wp:anchor" if anchor else "wp:inline"
    return (
        f"<w:p><w:r><w:drawing><{tag}>"
        f'<a:blip r:embed="{embed}"/>'
        f'<wp:extent cx="{cx}" cy="{cy}"/>'
        f"</{tag}></w:drawing></w:r></w:p>"
    )


RELS = {"rId4": "media/image1.png", "rId5": "embeddings/oleObject1.bin"}


def test_drawing_occurrence_carries_geometry_and_placement():
    xml = _drawing("rId4")
    index = build_paragraph_index(xml)

    occurrences = collect_drawing_occurrences(xml, RELS, "正文", index)

    assert len(occurrences) == 1
    assert occurrences[0].media_name == "media/image1.png"
    assert occurrences[0].relationship_id == "rId4"
    assert occurrences[0].placement == "inline"
    assert (occurrences[0].width, occurrences[0].height) == (40, 30)
    assert occurrences[0].location_hint == "正文"
    # 裁定 R11：`asset_kind` 此前在所有 occurrence 用例里都没被断言，
    # 于是「按 width/height 分类」这行接线（含传参顺序）毫无钉子。
    assert occurrences[0].asset_kind == "standalone"


def test_anchor_placement_is_detected():
    xml = _drawing("rId4", anchor=True)
    index = build_paragraph_index(xml)
    assert collect_drawing_occurrences(xml, RELS, "正文", index)[0].placement == "anchor"


def test_drawing_ignores_targets_outside_the_media_directory():
    """裁定 R9：夹具必须是**真的在 media/ 之外**的目标。

    原夹具用 `media/not-image.txt`——那仍在 `media/` 目录内，`^(word/)?media/` 会放行，
    参考实现的 `/^media\\//` 同样放行，所以那条用例期望 `[]` 却会拿到 1 条，必失败；
    而且它表达的也不是它声称的语义。改用 `embeddings/oleObject1.bin`——
    既不在 `media/`，也不在 `word/media/`，才是真正的负例。
    """
    xml = _drawing("rId5")
    index = build_paragraph_index(xml)
    assert collect_drawing_occurrences(xml, RELS, "正文", index) == []


def test_drawing_ignores_unknown_relationship():
    xml = _drawing("rId99")
    index = build_paragraph_index(xml)
    assert collect_drawing_occurrences(xml, RELS, "正文", index) == []


def test_vml_occurrence_reads_size_from_shape_style():
    xml = (
        '<w:p><w:r><w:pict><v:shape style="width:80pt;height:20pt;">'
        '<v:imagedata r:id="rId4"/></v:shape></w:pict></w:r></w:p>'
    )
    index = build_paragraph_index(xml)

    occurrences = collect_vml_occurrences(xml, RELS, "页眉", index)

    assert len(occurrences) == 1
    assert occurrences[0].media_name == "media/image1.png"
    assert occurrences[0].placement == "inline"          # VML 一律 inline
    # 裁定 R10：用**字面量**期望，不要把期望写成与实现同式的表达式——
    # 写成 `round(80 * PT_TO_EMU / EMU_PER_PX)` 是自指的：把 EMU_PER_PX 改成
    # 1000 之类的错值，该断言仍会通过，两个换算常量实际无人钉住。
    # 推导：80pt × 12700(EMU/pt) ÷ 9525(EMU/px) = 106.67 → 107；20pt 同法 = 26.67 → 27。
    assert occurrences[0].width == 107
    assert occurrences[0].height == 27


def test_classify_asset_kind():
    assert classify_asset_kind(600, 700, "inline") == "page-scan"
    assert classify_asset_kind(600, 700, "anchor") == "page-scan"
    assert classify_asset_kind(100, 100, "inline") == "standalone"
    assert classify_asset_kind(100, 100, "anchor") == "standalone"
    assert classify_asset_kind(100, 100, "") == "unknown"


def test_classify_asset_kind_requires_both_thresholds():
    """裁定 R11：上面的用例钉不住「双阈值是与、且用 >=」。

    原用例的四个尺寸里两个都达标、两个都不达标——`and` 换成 `or` 结果完全一样，
    所以「必须两者都达标」这层语义此前无人钉住。这里用**单边达标**的尺寸把它钉死。
    """
    assert classify_asset_kind(600, 100, "inline") == "standalone"   # 只有宽达标
    assert classify_asset_kind(100, 700, "inline") == "standalone"   # 只有高达标


def test_classify_asset_kind_boundaries_are_inclusive():
    """裁定 R11：阈值是 `>=`，不是 `>`——恰好等于阈值必须算 page-scan。"""
    assert classify_asset_kind(500, 650, "inline") == "page-scan"


def test_drawing_asset_kind_depends_on_argument_order():
    """裁定 R15：钉住传给 `classify_asset_kind` 的 **width/height 顺序**。

    普通夹具（如 40×30）两个维度都低于阈值，交换 width/height 完全不改变分类结果，
    所以「分类发生了」被钉住了、「顺序对不对」没有。要让两种顺序产生**不同**结果，
    必须选一对**跨阈值**的尺寸：600×660 时 `(600>=500 且 660>=650)` 为真 → `page-scan`，
    而交换后 `(660>=500 且 600>=650)` 为假 → `standalone`。这才叫钉住顺序。
    """
    xml = (
        "<w:p><w:r><w:drawing><wp:inline>"
        '<a:blip r:embed="rId4"/>'
        '<wp:extent cx="5715000" cy="6286500"/>'   # 600px × 660px（宽略小于高）
        "</wp:inline></w:drawing></w:r></w:p>"
    )
    index = build_paragraph_index(xml)

    occurrence = collect_drawing_occurrences(xml, RELS, "正文", index)[0]

    assert (occurrence.width, occurrence.height) == (600, 660)
    assert occurrence.asset_kind == "page-scan"


def test_drawing_with_reversed_extent_attributes_swaps_width_and_height():
    """裁定 R12：**钉住参考实现自带的缺陷，不要「顺手修」**。

    `_EXTENT_REVERSED_RE` 在 `cx`/`cy` 出现顺序颠倒时，group(1) 其实是 `cy`、
    group(2) 是 `cx`，而取值处固定把 group(1) 当 width——于是宽高被**转置**。
    参考实现（`cjs:161,165-166`）有**完全相同**的行为，按「与参考行为对齐」的
    验收标准本任务**不改**。这条用例的作用就是把现状钉住：谁若将来修正它，
    会看到这条失败，从而必须显式决定「偏离参考」而不是静默改掉。
    """
    xml = (
        "<w:p><w:r><w:drawing><wp:inline>"
        '<a:blip r:embed="rId4"/>'
        '<wp:extent cy="285750" cx="381000"/>'   # 刻意逆序
        "</wp:inline></w:drawing></w:r></w:p>"
    )
    index = build_paragraph_index(xml)

    occurrence = collect_drawing_occurrences(xml, RELS, "正文", index)[0]

    # 逆序分支下宽高被转置：width 取到的是 cy(30px)，height 取到的是 cx(40px)
    assert (occurrence.width, occurrence.height) == (30, 40)


def test_occurrence_uses_surrounding_paragraphs_for_context():
    """图片独占段落时，上下文来自前后段落，而不是坐标残渣。"""
    blocks = (
        "<w:p><w:r><w:t>第一章 总体方案</w:t></w:r></w:p>"
        + _drawing("rId4")
        + "<w:p><w:r><w:t>这是图后的说明文字。</w:t></w:r></w:p>"
    )
    index = build_paragraph_index(blocks)

    occurrence = collect_drawing_occurrences(blocks, RELS, "正文", index)[0]

    assert occurrence.section_title == "第一章 总体方案"
    assert "这是图后的说明文字。" in occurrence.nearby_text
```

- [ ] **Step 2: 跑测试确认失败**

Expected: FAIL，`ImportError`

- [ ] **Step 3: 写实现**

追加到 `docx_assets.py`：

```python
EMU_PER_PX = 9525
PT_TO_EMU = 12700
PAGE_SCAN_MIN_WIDTH_PX = 500
PAGE_SCAN_MIN_HEIGHT_PX = 650

_DRAWING_RE = re.compile(r"<w:drawing>[\s\S]*?</w:drawing>")
_BLIP_EMBED_RE = re.compile(r'\ba:blip\b[^>]*\br:embed="([^"]+)"|r:embed="([^"]+)"')
_ANCHOR_RE = re.compile(r"<wp:anchor\b")
_EXTENT_RE = re.compile(r'<wp:extent\b[^>]*\bcx="(\d+)"[^>]*\bcy="(\d+)"')
_EXTENT_REVERSED_RE = re.compile(r'<wp:extent\b[^>]*\bcy="(\d+)"[^>]*\bcx="(\d+)"')
_VML_RE = re.compile(r'<v:imagedata\b[^>]*\br:id="([^"]+)"[^>]*/?>')
_VML_STYLE_WIDTH_RE = re.compile(r"width:([\d.]+)pt", re.I)
_VML_STYLE_HEIGHT_RE = re.compile(r"height:([\d.]+)pt", re.I)
_MEDIA_TARGET_RE = re.compile(r"^(word/)?media/")


@dataclass(frozen=True)
class AssetOccurrence:
    media_name: str
    relationship_id: str
    placement: str
    width: int
    height: int
    asset_kind: str
    location_hint: str
    nearby_text: str
    section_title: str | None


def classify_asset_kind(width: int, height: int, placement: str) -> str:
    if width >= PAGE_SCAN_MIN_WIDTH_PX and height >= PAGE_SCAN_MIN_HEIGHT_PX:
        return "page-scan"
    if placement in ("inline", "anchor"):
        return "standalone"
    return "unknown"


def _is_media_target(target: str | None) -> bool:
    return bool(target) and bool(_MEDIA_TARGET_RE.match(target))


def _occurrence(media_name: str, relationship_id: str, placement: str,
                width: int, height: int, part_label: str,
                index: ParagraphIndex, char_index: int) -> AssetOccurrence:
    position = find_paragraph_position(index, char_index)
    return AssetOccurrence(
        media_name=media_name,
        relationship_id=relationship_id,
        placement=placement,
        width=width,
        height=height,
        asset_kind=classify_asset_kind(width, height, placement),
        location_hint=part_label,
        nearby_text=extract_nearby_text(index, position),
        section_title=extract_section_title(index, position),
    )


def collect_drawing_occurrences(xml: str, relationships: dict[str, str],
                                part_label: str, index: ParagraphIndex) -> list[AssetOccurrence]:
    source = xml or ""
    occurrences: list[AssetOccurrence] = []
    for match in _DRAWING_RE.finditer(source):
        block = match.group(0)
        embed = _BLIP_EMBED_RE.search(block)
        relationship_id = embed.group(1) or embed.group(2) if embed else None
        if not relationship_id:
            continue
        target = relationships.get(relationship_id)
        if not _is_media_target(target):
            continue
        placement = "anchor" if _ANCHOR_RE.search(block) else "inline"
        extent = _EXTENT_RE.search(block) or _EXTENT_REVERSED_RE.search(block)
        width = height = 0
        if extent:
            width = round(int(extent.group(1)) / EMU_PER_PX)
            height = round(int(extent.group(2)) / EMU_PER_PX)
        occurrences.append(_occurrence(
            target.replace("word/", "", 1), relationship_id, placement,
            width, height, part_label, index, match.start(),
        ))
    return occurrences


def collect_vml_occurrences(xml: str, relationships: dict[str, str],
                            part_label: str, index: ParagraphIndex) -> list[AssetOccurrence]:
    source = xml or ""
    occurrences: list[AssetOccurrence] = []
    for match in _VML_RE.finditer(source):
        target = relationships.get(match.group(1))
        if not _is_media_target(target):
            continue
        # VML 尺寸在外层 v:shape 的 style 里，单位 pt
        shape_start = source.rfind("<v:shape", 0, match.start())
        style_block = source[shape_start:match.start()] if shape_start >= 0 else ""
        width_pt = float(_VML_STYLE_WIDTH_RE.search(style_block).group(1)) if _VML_STYLE_WIDTH_RE.search(style_block) else 0.0
        height_pt = float(_VML_STYLE_HEIGHT_RE.search(style_block).group(1)) if _VML_STYLE_HEIGHT_RE.search(style_block) else 0.0
        occurrences.append(_occurrence(
            target.replace("word/", "", 1), match.group(1), "inline",
            round(width_pt * PT_TO_EMU / EMU_PER_PX),
            round(height_pt * PT_TO_EMU / EMU_PER_PX),
            part_label, index, match.start(),
        ))
    return occurrences
```

- [ ] **Step 4: 跑测试确认通过**

Expected: PASS（T4 新增 7 个 → 累计 33 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/infra/parsing/docx_assets.py server/tests/test_parsing_docx_assets.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): Drawing/VML 出现位置与资产分类（P2b Task 4）"
```

---

### Task 5: 提取入口（正文 + 页眉页脚）

**Files:**
- Modify: `server/app/infra/parsing/docx_assets.py`
- Test: `server/tests/test_parsing_docx_assets.py`

**Interfaces:**
- Consumes: 前述全部
- Produces: `DocxVisualAssets`（frozen dataclass：`occurrences: list[AssetOccurrence]`、`media_files: list[MediaFile]`）；`extract_docx_visual_assets(docx_path: Path) -> DocxVisualAssets`

**行为**（对齐 `extractDocxVisualAssets`）：正文走 `word/document.xml` + 其 `.rels`，label 为 `正文`；再对每个 `word/headerN.xml` / `word/footerN.xml` 用各自的 `.rels`，label 为 `页眉` / `页脚`。每个部分各建一次段落索引（各自坐标系）。

- [ ] **Step 1: 写失败的测试**

追加：

```python
from app.infra.parsing.docx_assets import extract_docx_visual_assets


def test_extracts_occurrences_and_media_together(tmp_path):
    first = make_image(tmp_path / "a.png", size=(40, 30))
    second = make_image(tmp_path / "b.png", size=(50, 25))
    source = build_docx_with_images(
        tmp_path / "two.docx", blocks=[("p", "正文")], images=[first, second]
    )

    assets = extract_docx_visual_assets(source)

    assert len(assets.media_files) == 2
    assert len(assets.occurrences) == 2
    media_names = {item.media_name for item in assets.occurrences}
    assert media_names == {item.media_name for item in assets.media_files}


def test_occurrence_label_defaults_to_body(tmp_path):
    image = make_image(tmp_path / "a.png")
    source = build_docx_with_images(tmp_path / "one.docx", images=[image])

    assets = extract_docx_visual_assets(source)

    assert {item.location_hint for item in assets.occurrences} == {"正文"}


def test_media_count_equals_distinct_media_files(tmp_path):
    """同一张图插入两次时：媒体文件只有一个，但出现位置有两个。"""
    image = make_image(tmp_path / "same.png")
    source = build_docx_with_images(tmp_path / "dup.docx", images=[image, image])

    assets = extract_docx_visual_assets(source)

    assert len(assets.media_files) == 1
    assert len(assets.occurrences) == 2
    assert assets.occurrences[0].media_name == assets.occurrences[1].media_name
```

- [ ] **Step 2: 跑测试确认失败**

Expected: FAIL，`ImportError`

- [ ] **Step 3: 写实现**

追加到 `docx_assets.py`：

```python
_HEADER_FOOTER_RE = re.compile(r"^word/(header\d*|footer\d*)\.xml$")


@dataclass(frozen=True)
class DocxVisualAssets:
    occurrences: list[AssetOccurrence]
    media_files: list[MediaFile]


def extract_docx_visual_assets(docx_path: Path) -> DocxVisualAssets:
    """正文 + 页眉页脚的出现位置，加上 word/media 下的全部媒体文件。

    每个部分用**自己的**关系表与段落索引：页眉的 `rId4` 与正文的 `rId4`
    是两套不同的映射，共用会把图片位置指错。
    """
    docx_path = Path(docx_path)
    with open_docx_zip(docx_path) as archive:
        names = archive.namelist()
        if "word/document.xml" not in names:
            raise ParsingError(
                "DOCX 缺少 word/document.xml，无法提取图片",
                code="docx-open-failed",
                path=str(docx_path),
            )

        def read_text(name: str) -> str:
            return archive.read(name).decode("utf-8") if name in names else ""

        parts: list[tuple[str, str, dict[str, str]]] = [
            ("正文", "word/document.xml", parse_relationships(read_text("word/_rels/document.xml.rels"))),
        ]
        for name in names:
            match = _HEADER_FOOTER_RE.match(name)
            if not match:
                continue
            stem = match.group(1)
            label = "页眉" if stem.startswith("header") else "页脚"
            parts.append((label, name, parse_relationships(read_text(f"word/_rels/{stem}.xml.rels"))))

        occurrences: list[AssetOccurrence] = []
        for label, part_name, relationships in parts:
            xml = read_text(part_name)
            if not xml:
                continue
            index = build_paragraph_index(xml)
            occurrences.extend(collect_drawing_occurrences(xml, relationships, label, index))
            occurrences.extend(collect_vml_occurrences(xml, relationships, label, index))

    return DocxVisualAssets(occurrences=occurrences, media_files=list_media_files(docx_path))
```

- [ ] **Step 4: 跑测试确认通过**

Expected: PASS（本文件原 38 个 + 本任务新增）

> **附录（裁定 R16，实现后补记）**：实现者交付了 **5** 个用例而不是上面列的 3 个，并给出了经变异验证的理由，**裁定采纳**：
>
> 1. `test_header_part_uses_its_own_relationships_and_label` —— 本任务的行为规格有一半是**页眉页脚**，而上面的 3 个用例对那部分**零覆盖**。该用例实测发现 python-docx 产出的两份关系表里存在**同名不同义的 rId**（正文 `rId1 → ../customXml/item1.xml`、页眉 `rId1 → media/image2.png`），若实现把正文的关系表套给页眉，页眉那条出现位置会被**静默丢弃**。它的期望值**独立从 zip 的页眉关系表读取**（不复用被测模块），因此不循环。代码见 `tests/test_parsing_docx_assets.py`。
> 2. `test_part_without_rels_degrades_to_empty_instead_of_raising` —— 覆盖「有图片引用、却没有关系表」的退化路径（python-docx 对纯文字页眉正是这样产出）。
>
> **同时它证明了上面 `test_occurrence_label_defaults_to_body` 无法判别**：把 `location_hint` 硬编码成 `"正文"`，该用例**照样绿**（它只断言集合 `{"正文"}`，而夹具里只有正文一张图）。真正钉住 label 派生的是附录第 1 条。原始用例按 R16 保留（无害），但它**不再是 label 行为的依据**。
>
> 另记：`_rewrite_zip_entry` 刻意**写到新路径**而非用 `os.replace` 原地替换——后者在 Windows 上会间歇性抛 `PermissionError: [WinError 5]`（杀软/索引占用），会让用例随机红。

- [ ] **Step 5: 全量套件确认无回归**

Run: `cd server && .venv/Scripts/python.exe -m pytest -q`
Expected: 全绿（P2a 的 189 + P2b 新增）

- [ ] **Step 6: 提交**

```bash
git add server/app/infra/parsing/docx_assets.py server/tests/test_parsing_docx_assets.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): docx 视觉资产提取入口（P2b Task 5）"
```

---

### Task 6: 图像格式决策（纯函数）

**Files:**
- Create: `server/app/infra/parsing/image_plan.py`
- Test: `server/tests/test_image_plan.py`

**Interfaces:**
- Consumes: 无（**纯函数、不读文件、不 import Pillow**）
- Produces: `detect_image_format(data: bytes) -> ImageFormat`（dataclass：`format: str`、`label: str`、`mime: str`）；`resolve_image_plan(data: bytes, *, max_passthrough_bytes: int | None = None) -> ImagePlan`（dataclass：`action: str`、`format: str`、`label: str`、`mime: str`、`code: str | None`、`reason: str | None`）；`compute_target_size(width, height, max_edge) -> tuple[int, int]`

**四分动作与跳过码必须逐字对齐** `utils/imageFormat.cjs`：
- `png` / `jpeg` → `compress`
- `gif` → `passthrough`；超过 `max_passthrough_bytes` → `skip` + `PASSTHROUGH_TOO_LARGE`
- `bmp` / `webp` → `transcode`
- `tiff` / `emf` / `wmf` / 未识别 → `skip` + `UNSUPPORTED_IMAGE_FORMAT`

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_image_plan.py`：

```python
import pytest

from app.infra.parsing.image_plan import compute_target_size, detect_image_format, resolve_image_plan

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 32
BMP = b"BM" + b"\x00" * 32
TIFF_LE = b"II\x2a\x00" + b"\x00" * 32
TIFF_BE = b"MM\x00\x2a" + b"\x00" * 32
EMF = b"\x00" * 40 + b" EMF" + b"\x00" * 8
WMF = b"\xd7\xcd\xc6\x9a" + b"\x00" * 32


@pytest.mark.parametrize(
    ("data", "expected"),
    [(PNG, "png"), (JPEG, "jpeg"), (GIF, "gif"), (WEBP, "webp"),
     (BMP, "bmp"), (TIFF_LE, "tiff"), (TIFF_BE, "tiff"), (EMF, "emf"), (WMF, "wmf"),
     (b"\x00\x01\x02\x03", "unknown")],
)
def test_detect_image_format(data, expected):
    assert detect_image_format(data).format == expected


def test_detect_reports_label_and_mime():
    detected = detect_image_format(PNG)
    assert detected.label == "PNG"
    assert detected.mime == "image/png"
    assert detect_image_format(b"\x00").mime == ""


@pytest.mark.parametrize(
    ("data", "action", "code"),
    [
        (PNG, "compress", None),
        (JPEG, "compress", None),
        (GIF, "passthrough", None),
        (BMP, "transcode", None),
        (WEBP, "transcode", None),
        (TIFF_LE, "skip", "UNSUPPORTED_IMAGE_FORMAT"),
        (EMF, "skip", "UNSUPPORTED_IMAGE_FORMAT"),
        (WMF, "skip", "UNSUPPORTED_IMAGE_FORMAT"),
        (b"\x00\x01\x02\x03", "skip", "UNSUPPORTED_IMAGE_FORMAT"),
    ],
)
def test_resolve_image_plan_actions(data, action, code):
    plan = resolve_image_plan(data)
    assert plan.action == action
    assert plan.code == code


def test_gif_over_the_limit_is_skipped():
    big = GIF + b"\x00" * 100
    plan = resolve_image_plan(big, max_passthrough_bytes=50)
    assert plan.action == "skip"
    assert plan.code == "PASSTHROUGH_TOO_LARGE"


def test_gif_without_a_limit_is_passed_through():
    assert resolve_image_plan(GIF + b"\x00" * 100).action == "passthrough"


def test_unsupported_skip_carries_a_reason_with_the_label():
    plan = resolve_image_plan(TIFF_LE)
    assert "TIFF" in (plan.reason or "")


def test_compute_target_size_shrinks_to_the_longest_edge():
    assert compute_target_size(1000, 500, 400) == (400, 200)
    assert compute_target_size(500, 1000, 400) == (200, 400)


def test_compute_target_size_never_upscales():
    assert compute_target_size(100, 80, 400) == (100, 80)


def test_compute_target_size_handles_bad_input():
    assert compute_target_size(0, 0, 400) == (1, 1)
    assert compute_target_size(100, 100, 0) == (100, 100)


def test_compute_target_size_rounds_half_up_like_the_reference():
    """裁定 R17：`.5` 临界必须 half-up——这是对齐参考 `Math.round` 的钉子。"""
    # 8000 → 4000 的缩放系数恰为 0.5：6001×0.5 = 3000.5 → 3001（half-up）
    assert compute_target_size(6001, 8000, 4000) == (3001, 4000)


def test_compute_target_size_scales_both_axes():
    """裁定 R18：两条轴**同时**缩小此前无用例（原用例只缩一边）。"""
    assert compute_target_size(1000, 800, 400) == (400, 320)


def test_compute_target_size_at_the_limit_does_not_shrink():
    """裁定 R18：最长边恰好等于上限时不缩（`longest <= edge` 的含等号语义）。"""
    assert compute_target_size(400, 200, 400) == (400, 200)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && .venv/Scripts/python.exe -m pytest tests/test_image_plan.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.infra.parsing.image_plan'`

- [ ] **Step 3: 写实现**

创建 `server/app/infra/parsing/image_plan.py`：

```python
"""图像格式决策。**纯函数：不读文件、不 import Pillow、无副作用。**

把「压缩 / 透传 / 转码 / 跳过」的策略集中在一处（对齐 utils/imageFormat.cjs:52 的
resolveImagePlan），这样策略可以脱离真实图片单测，也不会与编解码实现纠缠。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

# 按魔数识别。EMF/WMF 是矢量格式，Pillow 与浏览器都无法解码，但仍要识别出来，
# 好让跳过原因里带上具体格式名而不是含糊的「未知格式」。
_FORMAT_SIGNATURES: tuple[tuple[str, str, str, Callable[[bytes], bool]], ...] = (
    ("png", "PNG", "image/png", lambda b: len(b) >= 8 and b[:4] == b"\x89PNG"),
    ("jpeg", "JPEG", "image/jpeg", lambda b: len(b) >= 3 and b[0] == 0xFF and b[1] == 0xD8 and b[2] == 0xFF),
    ("gif", "GIF", "image/gif", lambda b: len(b) >= 6 and b[:4] == b"GIF8"),
    ("webp", "WebP", "image/webp",
     lambda b: len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WEBP"),
    ("bmp", "BMP", "image/bmp", lambda b: len(b) >= 2 and b[:2] == b"BM"),
    ("tiff", "TIFF", "image/tiff",
     lambda b: len(b) >= 4 and ((b[:3] == b"II\x2a") or (b[:4] == b"MM\x00\x2a"))),
    ("emf", "EMF", "image/emf", lambda b: len(b) >= 44 and b[40:44] == b" EMF"),
    ("wmf", "WMF", "image/wmf", lambda b: len(b) >= 4 and b[:4] == b"\xd7\xcd\xc6\x9a"),
)

# Electron 的 nativeImage 只稳定支持 PNG/JPEG；Python 侧用 Pillow，能力更强，
# 但仍保留同一套动作分类（spec §8.3）——印章 VLM 的输入形态与「未能识别的图片」
# 清单都依赖这些动作与跳过码。
NATIVE_DECODABLE_FORMATS = frozenset({"png", "jpeg"})
PASSTHROUGH_FORMATS = frozenset({"gif"})
TRANSCODE_FORMATS = frozenset({"bmp", "webp"})


@dataclass(frozen=True)
class ImageFormat:
    format: str
    label: str
    mime: str


@dataclass(frozen=True)
class ImagePlan:
    action: str                     # compress | passthrough | transcode | skip
    format: str
    label: str
    mime: str
    code: str | None = None         # 仅 skip 时有值
    reason: str | None = None


UNKNOWN_FORMAT = ImageFormat(format="unknown", label="未知格式", mime="")


def detect_image_format(data: bytes) -> ImageFormat:
    payload = data or b""
    for name, label, mime, matches in _FORMAT_SIGNATURES:
        if matches(payload):
            return ImageFormat(format=name, label=label, mime=mime)
    return UNKNOWN_FORMAT


def resolve_image_plan(data: bytes, *, max_passthrough_bytes: int | None = None) -> ImagePlan:
    """单一决策入口（对齐 resolveImagePlan）。"""
    detected = detect_image_format(data)
    limit = max_passthrough_bytes if isinstance(max_passthrough_bytes, int) and max_passthrough_bytes > 0 else None

    if detected.format in NATIVE_DECODABLE_FORMATS:
        return ImagePlan("compress", detected.format, detected.label, detected.mime)
    if detected.format in PASSTHROUGH_FORMATS:
        if limit is not None and len(data or b"") > limit:
            return ImagePlan("skip", detected.format, detected.label, detected.mime,
                             code="PASSTHROUGH_TOO_LARGE",
                             reason=f"{detected.label} 体积超出透传上限")
        return ImagePlan("passthrough", detected.format, detected.label, detected.mime)
    if detected.format in TRANSCODE_FORMATS:
        return ImagePlan("transcode", detected.format, detected.label, detected.mime)
    return ImagePlan("skip", detected.format, detected.label, detected.mime,
                     code="UNSUPPORTED_IMAGE_FORMAT",
                     reason=f"图片格式不支持（{detected.label}）")


def compute_target_size(width: int, height: int, max_edge: int) -> tuple[int, int]:
    """等比缩放到最长边不超过 max_edge；**不放大**（对齐 computeTargetSize）。

    裁定 R17：用 `math.floor(x + 0.5)` 而不是内置 `round()`。
    Python 的 `round()` 是 **half-even**（银行家舍入：`round(3000.5) == 3000`），
    而参考实现的 `Math.round` 是 **half-up**（`Math.round(3000.5) === 3001`）。
    两者只在缩放值**恰好落 .5** 时分道扬镳（如 `6001×8000, edge=4000` →
    py 3000 / js 3001）。按「与参考行为对齐」的验收标准，这里对齐参考。
    **不要**改回 `round()`——那会重新引入 1px 的平台性偏离。
    """
    safe_width = max(1, int(width or 0))
    safe_height = max(1, int(height or 0))
    edge = int(max_edge or 0)
    longest = max(safe_width, safe_height)
    if edge <= 0 or longest <= edge:
        return safe_width, safe_height
    scale = edge / longest
    return max(1, math.floor(safe_width * scale + 0.5)), max(1, math.floor(safe_height * scale + 0.5))
```

- [ ] **Step 4: 跑测试确认通过**

Expected: PASS（本文件全部用例；参数化展开后 **34** 项。原稿写的「21 passed」是笔误）

> **实现后补记（裁定 R19/R20）**：
>
> - **R19**：`test_compute_target_size_scales_both_axes` 在实现轮里**已经加过**（brief 原稿漏了、实现者补的），因此**不要重复声明同名函数**（会 shadow／F811，且收集数不变）。全量实测 **266**，不是 267。
> - **R20**：`test_compute_target_size_at_the_limit_does_not_shrink` 钉的是「恰在上限时既不缩也不改形状」这一**契约**，但**钉不住 `longest <= edge` 与 `longest < edge` 的区别**——恰在上限时 `scale = 1.0`，两条分支产出相同，该区分**不可观测**（等效变异体）。**不要为它设计变异探针**，白费一轮。

- [ ] **Step 5: 提交**

```bash
git add server/app/infra/parsing/image_plan.py server/tests/test_image_plan.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): 图像格式决策的四分策略与等比缩放（P2b Task 6）"
```

---

### Task 7: Pillow 压缩 / 透传 / 转码

**Files:**
- Create: `server/app/infra/parsing/image_codec.py`
- Test: `server/tests/test_image_codec.py`

**Interfaces:**
- Consumes: `resolve_image_plan`、`compute_target_size`、`ImagePlan`、`MediaFile`
- Produces: `PreparedImage`（frozen dataclass：`data: bytes`、`mime: str`、`data_url: str`、`action: str`、`width: int`、`height: int`、`skip_code: str | None`、`skip_reason: str | None`、`detail: str | None`）；`prepare_image(media: MediaFile, *, max_edge: int = 1600, max_passthrough_bytes: int | None = None) -> PreparedImage`

**行为**：
- `compress` / `transcode`：Pillow 打开（`Image.open`，`GIF` 走首帧）→ 若带 alpha 存 PNG，否则存 JPEG（quality 对齐现有压缩策略）→ 先按 `max_edge` 缩放
- `passthrough`：原字节直接编码为 data URL（**不解码**，保留 GIF 原样）
- `skip`：不解码、不产出 data URL，带上 `code` 与 `reason`
- **不可解码**（Pillow 抛异常）→ 归为 `skip` + `IMAGE_DECODE_FAILED`，**不抛异常**：单张失败不连坐（spec §8.4.3）

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_image_codec.py`：

```python
import base64
import io

import pytest
from PIL import Image

from app.infra.parsing.docx_assets import MediaFile
from app.infra.parsing.image_codec import prepare_image
from tests.parsing_fixtures import make_image


def _media(path, *, media_name="media/a.png"):
    data = path.read_bytes()
    import hashlib

    return MediaFile(
        media_name=media_name, data=data,
        content_hash=hashlib.sha256(data).hexdigest(),
        mime_type="application/octet-stream", extension=path.suffix.lower(),
    )


def test_png_is_recompressed_to_a_data_url(tmp_path):
    media = _media(make_image(tmp_path / "a.png", size=(100, 80), fmt="PNG"))

    prepared = prepare_image(media)

    assert prepared.action == "compress"
    assert prepared.data_url.startswith("data:")
    assert ";base64," in prepared.data_url
    assert prepared.width == 100 and prepared.height == 80
    # 裁定 R21：上面三条只证明「解码过」（宽高来自 Pillow），**证不了重编码**——
    # 「只解码取尺寸、却把原字节 base64 回去」的变异体也能全绿，而本用例的名字
    # 恰恰叫 recompressed。下面两条才钉住重编码：无 alpha 的 PNG 按约定必须
    # 重编码成 JPEG，且产物字节与输入不同。
    assert prepared.mime == "image/jpeg"
    assert prepared.data != media.data


def test_transcode_turns_bmp_into_a_model_readable_format(tmp_path):
    media = _media(make_image(tmp_path / "a.bmp", size=(60, 40), fmt="BMP"), media_name="media/a.bmp")

    prepared = prepare_image(media)

    assert prepared.action == "transcode"
    assert prepared.mime in ("image/png", "image/jpeg")
    # 解码回来看得出来，说明确实是可读位图而不是原字节透传
    decoded = Image.open(io.BytesIO(base64.b64decode(prepared.data_url.split(",", 1)[1])))
    assert decoded.size == (60, 40)


def test_gif_is_passed_through_byte_for_byte(tmp_path):
    media = _media(make_image(tmp_path / "a.gif", size=(20, 20), fmt="GIF"), media_name="media/a.gif")

    prepared = prepare_image(media)

    assert prepared.action == "passthrough"
    assert base64.b64decode(prepared.data_url.split(",", 1)[1]) == media.data
    assert prepared.mime == "image/gif"


def test_tiff_is_skipped_with_the_unsupported_code(tmp_path):
    media = _media(make_image(tmp_path / "a.tiff", size=(20, 20), fmt="TIFF"), media_name="media/a.tiff")

    prepared = prepare_image(media)

    assert prepared.action == "skip"
    assert prepared.skip_code == "UNSUPPORTED_IMAGE_FORMAT"
    assert prepared.data_url == ""


def test_oversized_gif_is_skipped_by_the_limit(tmp_path):
    media = _media(make_image(tmp_path / "a.gif", size=(20, 20), fmt="GIF"), media_name="media/a.gif")

    prepared = prepare_image(media, max_passthrough_bytes=8)

    assert prepared.action == "skip"
    assert prepared.skip_code == "PASSTHROUGH_TOO_LARGE"


def test_large_image_is_scaled_down_to_max_edge(tmp_path):
    media = _media(make_image(tmp_path / "big.png", size=(800, 400), fmt="PNG"))

    prepared = prepare_image(media, max_edge=200)

    assert (prepared.width, prepared.height) == (200, 100)
    # 裁定 R22：上面那条**证不了真的缩过**——width/height 来自 compute_target_size
    # 并被原样上报，与 `_encode` 是否执行 resize 无关。把 `resized = image`（永不缩放）
    # 变异后上面那条仍绿，而用例名里的「缩了」恰恰没被钉住。解回产物看真实尺寸：
    decoded = Image.open(io.BytesIO(base64.b64decode(prepared.data_url.split(",", 1)[1])))
    assert decoded.size == (200, 100)


def test_undecodable_png_is_skipped_not_raised(tmp_path):
    """魔数像 PNG 但内容坏掉：必须降级为 skip，不能让单张失败拖垮整批（spec §8.4.3）。"""
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    media = _media(broken)

    prepared = prepare_image(media)

    assert prepared.action == "skip"
    assert prepared.skip_code == "IMAGE_DECODE_FAILED"
    assert prepared.skip_reason
    assert prepared.data_url == ""


def test_alpha_png_stays_png(tmp_path):
    """带透明通道的图存 JPEG 会丢掉 alpha，必须存 PNG。"""
    path = tmp_path / "alpha.png"
    Image.new("RGBA", (30, 30), (10, 20, 30, 128)).save(path, format="PNG")

    prepared = prepare_image(_media(path))

    assert prepared.mime == "image/png"
```

- [ ] **Step 2: 跑测试确认失败**

Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写实现**

创建 `server/app/infra/parsing/image_codec.py`：

```python
"""Pillow 编解码。**不做策略决策**——动作由 image_plan 决定，这里只执行。

现有 Electron 侧用 nativeImage 压缩、离屏 Chromium 转码；Python 侧一律用 Pillow，
但**动作分类与跳过码保持一致**（spec §8.3），否则印章 VLM 的输入形态会漂移。
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

from PIL import Image

from app.infra.parsing.docx_assets import MediaFile
from app.infra.parsing.image_plan import compute_target_size, resolve_image_plan

JPEG_QUALITY = 85
DEFAULT_MAX_EDGE = 1600


@dataclass(frozen=True)
class PreparedImage:
    data: bytes
    mime: str
    data_url: str
    action: str
    width: int
    height: int
    skip_code: str | None = None
    skip_reason: str | None = None
    detail: str | None = None


def _to_data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _encode(image: Image.Image, target_size: tuple[int, int]) -> tuple[bytes, str, str]:
    """按是否带 alpha 选容器：带 alpha 存 PNG，否则存 JPEG。

    存 JPEG 时丢掉 alpha 会得到黑底，所以必须先判断 `has_alpha` 或调色板透明。
    """
    resized = image if image.size == target_size else image.resize(target_size, Image.LANCZOS)
    has_alpha = resized.mode in ("RGBA", "LA") or (
        resized.mode == "P" and "transparency" in resized.info
    )
    buffer = io.BytesIO()
    if has_alpha:
        resized.convert("RGBA").save(buffer, format="PNG")
        return buffer.getvalue(), "image/png", "PNG"
    resized.convert("RGB").save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue(), "image/jpeg", "JPEG"


def prepare_image(
    media: MediaFile,
    *,
    max_edge: int = DEFAULT_MAX_EDGE,
    max_passthrough_bytes: int | None = None,
) -> PreparedImage:
    plan = resolve_image_plan(media.data, max_passthrough_bytes=max_passthrough_bytes)

    if plan.action == "skip":
        return PreparedImage(
            data=b"", mime=plan.mime, data_url="", action="skip",
            width=0, height=0, skip_code=plan.code, skip_reason=plan.reason,
        )

    if plan.action == "passthrough":
        # 原字节直接交给模型（现有实现发现模型能识别静态 GIF），不解码
        return PreparedImage(
            data=media.data, mime=plan.mime,
            data_url=_to_data_url(media.data, plan.mime),
            action="passthrough", width=0, height=0,
        )

    try:
        with Image.open(io.BytesIO(media.data)) as image:
            source_size = image.size
            target = compute_target_size(source_size[0], source_size[1], max_edge)
            data, mime, detail = _encode(image, target)
    except Exception as error:
        # spec §8.4.3：单张失败不连坐。降级为 skip，不抛异常。
        return PreparedImage(
            data=b"", mime=plan.mime, data_url="", action="skip",
            width=0, height=0, skip_code="IMAGE_DECODE_FAILED",
            skip_reason=f"{plan.label} 无法解码",
            detail=f"{type(error).__name__}: {error}",
        )

    return PreparedImage(
        data=data, mime=mime, data_url=_to_data_url(data, mime),
        action=plan.action, width=target[0], height=target[1], detail=detail,
    )
```

- [ ] **Step 4: 跑测试确认通过**

Expected: PASS（8 passed）

- [ ] **Step 5: 提交**

```bash
git add server/app/infra/parsing/image_codec.py server/tests/test_image_codec.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): Pillow 压缩/透传/转码，单张失败降级不连坐（P2b Task 7）"
```

---

### Task 8: 按 content_hash 去重与资产落盘

**Files:**
- Create: `server/app/infra/parsing/asset_store.py`
- Test: `server/tests/test_asset_store.py`

**Interfaces:**
- Consumes: `DocxVisualAssets`、`MediaFile`、`prepare_image`、`PreparedImage`、`AssetOccurrence`、`ParsingError`
- Produces: `StoredAsset`（frozen dataclass：`content_hash: str`、`path: str`、`mime: str`、`extension: str`、`size_bytes: int`、`occurrences: list[AssetOccurrence]`、`prepared: PreparedImage`）；`save_docx_assets(*, data_root: Path, project_id: str, docx_path: Path, max_edge: int = 1600, max_passthrough_bytes: int | None = None) -> list[StoredAsset]`

**去重语义（spec §8.4.1）**：按 `media_files` 的 `content_hash` 去重——**同一张图插多次只落盘一份、只准备一次**，但它的**所有出现位置**都挂在这一份上（`occurrences` 是列表）。这是现有 `applyContentOutcomeToSiblings` 的同一约定：同 hash 的所有位置共享结果。

**落盘**：`<data_root>/projects/<project_id>/assets/<content_hash>.<ext>`。`project_id` 必须过 P2a 的同一道净化守卫（`^[A-Za-z0-9._-]+$` 且非 `.`/`..`）——**直接复用 `service.py` 里的校验**，不要复制一份。

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_asset_store.py`：

```python
import hashlib

import pytest

from app.domain.shared.errors import ParsingError
from app.infra.parsing.asset_store import save_docx_assets
from tests.parsing_fixtures import build_docx_with_images, make_image


def _docx(tmp_path, images, name="a.docx"):
    return build_docx_with_images(tmp_path / name, blocks=[("p", "正文")], images=images)


def test_stores_one_file_per_distinct_image(tmp_path):
    a = make_image(tmp_path / "a.png", size=(40, 30), color=(255, 0, 0))
    b = make_image(tmp_path / "b.png", size=(40, 30), color=(0, 255, 0))
    source = _docx(tmp_path, [a, b])

    assets = save_docx_assets(data_root=tmp_path / "data", project_id="p1", docx_path=source)

    assert len(assets) == 2
    for asset in assets:
        assert (tmp_path / "data" / "projects" / "p1" / "assets" / f"{asset.content_hash}.{asset.extension}").exists()


def test_same_image_twice_is_stored_once_with_all_occurrences(tmp_path):
    image = make_image(tmp_path / "same.png")
    source = _docx(tmp_path, [image, image])

    assets = save_docx_assets(data_root=tmp_path / "data", project_id="p1", docx_path=source)

    assert len(assets) == 1
    assert len(assets[0].occurrences) == 2
    assert assets[0].occurrences[0].media_name == assets[0].occurrences[1].media_name


def test_content_hash_matches_the_stored_file(tmp_path):
    image = make_image(tmp_path / "a.png")
    source = _docx(tmp_path, [image])

    asset = save_docx_assets(data_root=tmp_path / "data", project_id="p1", docx_path=source)[0]

    stored = (tmp_path / "data" / "projects" / "p1" / "assets" / f"{asset.content_hash}.{asset.extension}").read_bytes()
    assert asset.size_bytes == len(stored)


def test_prepared_image_is_carried_on_the_asset(tmp_path):
    image = make_image(tmp_path / "a.png", size=(50, 40))
    source = _docx(tmp_path, [image])

    asset = save_docx_assets(data_root=tmp_path / "data", project_id="p1", docx_path=source)[0]

    assert asset.prepared.action == "compress"
    assert asset.prepared.data_url.startswith("data:")


def test_skipped_image_is_kept_as_an_asset_with_its_code(tmp_path):
    tiff = make_image(tmp_path / "a.tiff", size=(20, 20), fmt="TIFF")
    source = _docx(tmp_path, [tiff])

    assets = save_docx_assets(data_root=tmp_path / "data", project_id="p1", docx_path=source)

    assert len(assets) == 1
    assert assets[0].prepared.action == "skip"
    assert assets[0].prepared.skip_code == "UNSUPPORTED_IMAGE_FORMAT"


def test_docx_without_images_yields_no_assets(tmp_path):
    from tests.parsing_fixtures import build_docx

    source = build_docx(tmp_path / "plain.docx", blocks=[("p", "没有图")])

    assert save_docx_assets(data_root=tmp_path / "data", project_id="p1", docx_path=source) == []


def test_unsafe_project_id_is_rejected(tmp_path):
    """复用 P2a 的同一道守卫，不再复制一份。"""
    image = make_image(tmp_path / "a.png")
    source = _docx(tmp_path, [image])

    with pytest.raises(ParsingError) as info:
        save_docx_assets(data_root=tmp_path / "data", project_id="../evil", docx_path=source)

    assert info.value.code == "invalid-project-id"


def test_stored_identity_tracks_the_media_bytes(tmp_path):
    """落盘实体是**媒体文件本身**：`content_hash` 必须等于原图的 sha256。

    裁定：原先这条写成 `all(isinstance(asset.occurrences, list))` 是平凡真的
    （任何列表都成立），什么也没钉住。改为钉真正的契约——哈希取自媒体字节。
    """
    image = make_image(tmp_path / "a.png")
    source = _docx(tmp_path, [image])

    assets = save_docx_assets(data_root=tmp_path / "data", project_id="p1", docx_path=source)

    assert [asset.content_hash for asset in assets] == [hashlib.sha256(image.read_bytes()).hexdigest()]
```

- [ ] **Step 2: 跑测试确认失败**

Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写实现**

创建 `server/app/infra/parsing/asset_store.py`：

```python
"""资产落盘与按 content_hash 去重（spec §8.3、§8.4.1）。

去重语义：同一 hash 的媒体**只落盘一份、只准备一次**，所有出现位置挂在同一份上
（现有 applyContentOutcomeToSiblings 的同一约定）——P4 的印章检查据此让同一张图
的所有位置共享跳过/失败结果，避免同一张图被反复上传。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.infra.parsing.docx_assets import AssetOccurrence, MediaFile, extract_docx_visual_assets
from app.infra.parsing.image_codec import DEFAULT_MAX_EDGE, PreparedImage, prepare_image
from app.infra.parsing.service import validate_project_id


@dataclass(frozen=True)
class StoredAsset:
    content_hash: str
    path: str
    mime: str
    extension: str
    size_bytes: int
    occurrences: list[AssetOccurrence]
    prepared: PreparedImage


def _asset_extension(media: MediaFile, prepared: PreparedImage) -> str:
    """落盘扩展名取自**准备结果**：转码后的容器才是文件的真实类型。"""
    if prepared.action in ("compress", "transcode") and prepared.mime:
        return ".png" if prepared.mime == "image/png" else ".jpg"
    return media.extension or ".bin"


def save_docx_assets(
    *,
    data_root: Path,
    project_id: str,
    docx_path: Path,
    max_edge: int = DEFAULT_MAX_EDGE,
    max_passthrough_bytes: int | None = None,
) -> list[StoredAsset]:
    validate_project_id(project_id)
    data_root = Path(data_root)
    docx_path = Path(docx_path)

    extracted = extract_docx_visual_assets(docx_path)

    # 去重：第一遍按 hash 归组，保留媒体本体与全部出现位置
    grouped: dict[str, tuple[MediaFile, list[AssetOccurrence]]] = {}
    for media in extracted.media_files:
        grouped.setdefault(media.content_hash, (media, []))
    for occurrence in extracted.occurrences:
        for content_hash, (media, positions) in grouped.items():
            if media.media_name == occurrence.media_name:
                positions.append(occurrence)
                break

    assets_dir = data_root / "projects" / project_id / "assets"
    saved: list[StoredAsset] = []
    for content_hash, (media, occurrences) in grouped.items():
        prepared = prepare_image(
            media, max_edge=max_edge, max_passthrough_bytes=max_passthrough_bytes
        )
        payload = prepared.data if prepared.action != "skip" else media.data
        extension = _asset_extension(media, prepared)
        assets_dir.mkdir(parents=True, exist_ok=True)
        target = assets_dir / f"{content_hash}{extension}"
        target.write_bytes(payload)
        saved.append(StoredAsset(
            content_hash=content_hash,
            path=str(target),
            mime=prepared.mime or media.mime_type,
            extension=extension,
            size_bytes=len(payload),
            occurrences=occurrences,
            prepared=prepared,
        ))
    return saved
```

**同时**：`service.py` 里的 `project_id` 校验当前是私有函数 `_validate_project_id`——把它改名为公开的 `validate_project_id` 并保持行为不变（P2a 终审 F1 要求的就是一道共用守卫）。测试若直接引用了私有名，一并更新。

- [ ] **Step 4: 跑测试确认通过**

Expected: PASS（8 passed）。若 `test_media_without_an_occurrence_is_still_stored` 的断言与“出现位置必然齐全”的现实不符，**如实报告**并按 triage 规则判断是我写错还是实现错。

- [ ] **Step 5: 全量套件确认无回归**

Run: `cd server && .venv/Scripts/python.exe -m pytest -q`
Expected: 全绿（以实际收集数为准）

- [ ] **Step 6: 提交**

```bash
git add server/app/infra/parsing/asset_store.py server/app/infra/parsing/service.py server/tests/test_asset_store.py
git -c user.name="InsCode" -c user.email="noreply@inscode.net" commit -m "feat(server): 资产按 content_hash 去重落盘，复用同一道 project_id 守卫（P2b Task 8）"
```

---

## 完成标准

- `cd server && .venv/Scripts/python.exe -m pytest -q` 全绿（P2a 的 189 + P2b 新增）。
- `extract_docx_visual_assets()` 对含图 docx 产出出现位置（带 `placement`/宽高/`asset_kind`/`section_title`/`nearby_text`）与全量 `media_files`；页眉页脚各自用**自己的**关系表。
- `resolve_image_plan()` 的四分动作与全部跳过码与 `utils/imageFormat.cjs` 逐字一致，且是**纯函数**（不 import Pillow）。
- `prepare_image()` 对 GIF 原字节透传、对 BMP/WebP 用 Pillow 转码、对 TIFF/EMF/WMF 跳过、对**不可解码**降级为 `IMAGE_DECODE_FAILED` 而不抛异常。
- 资产按 `content_hash` 去重：同图多次出现只落盘一份，`occurrences` 保留全部位置。
- 全程无网络请求；`app/domain/**` 无新增依赖（Pillow 只在 `app/infra/parsing/`）。

## 明确不做（属 P4）

把图像上传给视觉模型的批次循环、批次失败降级为逐张重试、`skipKind` 的 `not-seal-area` 分类、`stats.skippedAssets` 聚合、把资产行写入数据库（同 P2a 的 R24：P2b 不写库，行由 P4 在域表里写）。
