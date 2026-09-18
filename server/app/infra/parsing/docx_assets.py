from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.domain.shared.errors import ParsingError
from app.infra.parsing.image_plan import half_up

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


def _read_zip_entry(archive: zipfile.ZipFile, name: str, docx_path: Path) -> bytes:
    """读 zip 条目；读失败（坏 CRC、截断条目等）归一为 ParsingError。

    否则裸的 `BadZipFile`/`OSError` 会漏出——而本模块其它失败路径都走 `ParsingError`，
    P3/P4 错误处理抓不到（F2）。与 `open_docx_zip` 用同一个 code：二者对用户而言都是
    「这不是一个能用的 DOCX」。
    """
    try:
        return archive.read(name)
    except (zipfile.BadZipFile, OSError) as error:
        raise ParsingError(
            "文件解析失败：DOCX 条目读取出错",
            code="docx-open-failed",
            path=str(docx_path),
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
            # 只收一级目录下的文件：`word/media/a/b.png` 不是标准布局。
            # 目录条目 `word/media/`（remainder 为空、无 `/`）也会被 `name.endswith("/")`
            # 拦下——否则它会变成 data=b""、content_hash=sha256(b"") 的**固定常量**伪资产，
            # 既命不中参考的 `/^word\/media\/[^/]+$/` 校验，又会在去重键上与任何空文件相撞（F1）。
            if (
                not name.startswith("word/media/")
                or "/" in name[len("word/media/"):]
                or name.endswith("/")
            ):
                continue
            data = _read_zip_entry(archive, name, docx_path)
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

    当 `position < 0`（drawing 落在任何 `<w:p>` 之外，或 XML 残缺）时直接返回
    `None`，与 `extract_nearby_text` 的形状一致（F7）。注：Python 里
    `range(position-1, -1, -1)` 在 position==-1 时本就是空区间，旧实现同样返回 None；
    此处显式守卫只为形状一致与防御未来调用方传入其它负值。
    """
    if position < 0:
        return None
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
            # half-up（对齐参考 Math.round），不是内置 round() 的 half-even（F5）
            width = half_up(int(extent.group(1)) / EMU_PER_PX)
            height = half_up(int(extent.group(2)) / EMU_PER_PX)
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
            # half-up（对齐参考 Math.round），不是内置 round() 的 half-even（F5）
            half_up(width_pt * PT_TO_EMU / EMU_PER_PX),
            half_up(height_pt * PT_TO_EMU / EMU_PER_PX),
            part_label, index, match.start(),
        ))
    return occurrences


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
            return _read_zip_entry(archive, name, docx_path).decode("utf-8") if name in names else ""

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
