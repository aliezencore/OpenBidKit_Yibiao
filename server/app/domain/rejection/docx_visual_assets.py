"""DOCX 视觉资产提取（P4c-3）——印章检查的图片来源。

逐字移植 `client/electron/services/docxVisualAssetExtractor.cjs`（:25-260）：
关系解析、段落索引（样式/加粗/CJK 特征）、章节标题回溯、邻近文本、
drawing/VML 两类图片收集、按 contentHash 落盘（`<hash>/<hash>.<ext>`）。

**平台适配**：AdmZip → 标准库 `zipfile`；字节哈希用 hashlib（与参考同为 sha256 hex）。
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path
from typing import Any

EMU_PER_PX = 9525          # 参考 :6
PT_TO_EMU = 12700          # 参考 :7
PAGE_SCAN_MIN_WIDTH_PX = 500    # 参考 :8
PAGE_SCAN_MIN_HEIGHT_PX = 650   # 参考 :9
MAX_SECTION_TITLE_LENGTH = 40
MAX_NEARBY_TEXT_LENGTH = 200
NEARBY_PARAGRAPH_RADIUS = 2

MIME_BY_EXTENSION = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".bmp": "image/bmp", ".tif": "image/tiff",
    ".tiff": "image/tiff", ".webp": "image/webp", ".emf": "image/emf",
    ".wmf": "image/wmf",
}

# 中文标书大量使用纯文本编号当章节，并不使用 Word 标题样式（参考 :33-37）
_CHAPTER_TEXT_PATTERNS = [
    re.compile(r"^第[一二三四五六七八九十百千0-9]+[章节篇]"),
    re.compile(r"^[一二三四五六七八九十]+、"),
    re.compile(r"^（[一二三四五六七八九十]+）"),
    re.compile(r"^\d+(\.\d+)*[\s、.]"),
]
_HEADING_STYLE_RE = re.compile(r"^(heading\s*[1-9]|标题\s*[1-9一二三四五六七八九十])$", re.I)
_CJK_RE = re.compile(r"[\u4e00-\u9fa5]")


def is_docx_file_path(file_path: Any) -> bool:
    return bool(re.search(r"\.docx$", str(file_path or ""), re.I))


def unescape_xml_entities(text: Any) -> str:
    return (str(text or "")
            .replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
            .replace("&quot;", '"').replace("&apos;", "'"))


def extract_run_text(block: Any) -> str:
    """参考 :48-59：只拼 `<w:t>` 可见文本——`<wp:posOffset>` 的坐标数字本身就是元素
    文本，整段剥标签会留下残渣并污染上下文。"""
    source = str(block or "")
    runs = [unescape_xml_entities(match)
            for match in re.findall(r"<w:t\b[^>]*>([\s\S]*?)</w:t>", source)]
    return re.sub(r"\s+", " ", "".join(runs)).strip()


def parse_relationships(xml: Any) -> dict[str, str]:
    """参考 :29-38。"""
    mapping: dict[str, str] = {}
    for tag in re.findall(r"<Relationship\b[^>]*>", str(xml or "")):
        id_match = re.search(r"\bId=\"([^\"]+)\"", tag)
        target_match = re.search(r"\bTarget=\"([^\"]+)\"", tag)
        if id_match and target_match:
            mapping[id_match.group(1)] = re.sub(r"^/+", "", target_match.group(1))
    return mapping


def build_paragraph_index(xml: Any) -> dict[str, list[Any]]:
    """参考 :76-95：一次性切段落并预计算特征。"""
    source = str(xml or "")
    paragraphs: list[dict[str, Any]] = []
    positions: list[dict[str, int]] = []
    for match in re.finditer(r"<w:p\b[^>]*>[\s\S]*?</w:p>", source):
        block = match.group(0)
        style_match = re.search(r"<w:pStyle\b[^>]*w:val=\"([^\"]+)\"", block)
        paragraphs.append({
            "text": extract_run_text(block),
            "styleVal": style_match.group(1) if style_match else "",
            "bold": bool(re.search(r"<w:b\b[^>]*/?>", block)),
        })
        positions.append({"start": match.start(), "end": match.end()})
    return {"paragraphs": paragraphs, "positions": positions}


def find_paragraph_position(positions: Sequence[Mapping[str, int]],
                            char_index: int) -> int:
    """参考 :96-102：倒序找包含该字符位置的段落。"""
    for index in range(len(positions) - 1, -1, -1):
        if positions[index]["start"] <= char_index < positions[index]["end"]:
            return index
    return -1


def is_heading_paragraph(paragraph: Mapping[str, Any]) -> bool:
    """参考 :103-111：标题样式或（长度 ≤40 且含 CJK 的）编号段落。"""
    if not paragraph or not paragraph.get("text"):
        return False
    if paragraph.get("styleVal") and _HEADING_STYLE_RE.match(str(paragraph["styleVal"]).strip()):
        return True
    if len(paragraph["text"]) > MAX_SECTION_TITLE_LENGTH:
        return False
    if not _CJK_RE.search(paragraph["text"]):
        return False
    return any(pattern.match(paragraph["text"]) for pattern in _CHAPTER_TEXT_PATTERNS)


def extract_section_title(paragraphs: Sequence[Mapping[str, Any]],
                          paragraph_position: int) -> str | None:
    """参考 :112-125：先找标题样式/编号段落，再退一步找加粗短段落，都没有→None。"""
    for index in range(paragraph_position - 1, -1, -1):
        paragraph = paragraphs[index] if 0 <= index < len(paragraphs) else None
        if not paragraph or not paragraph.get("text"):
            continue
        if is_heading_paragraph(paragraph):
            return paragraph["text"]
    for index in range(paragraph_position - 1, -1, -1):
        paragraph = paragraphs[index] if 0 <= index < len(paragraphs) else None
        if not paragraph or not paragraph.get("text") or \
                len(paragraph["text"]) > MAX_SECTION_TITLE_LENGTH:
            continue
        if paragraph.get("bold") and _CJK_RE.search(paragraph["text"]):
            return paragraph["text"]
    return None


def extract_nearby_text(paragraphs: Sequence[Mapping[str, Any]],
                        paragraph_position: int) -> str:
    """参考 :126-142：取所在段落文本；图片独占段落时取前后各最多 2 个有文本的段落。"""
    if paragraph_position < 0:
        return ""
    own = paragraphs[paragraph_position].get("text") if 0 <= paragraph_position < len(paragraphs) else ""
    if own:
        return own[:MAX_NEARBY_TEXT_LENGTH]
    parts: list[str] = []
    for offset in range(1, NEARBY_PARAGRAPH_RADIUS + 1):
        index = paragraph_position - offset
        if 0 <= index < len(paragraphs) and paragraphs[index].get("text"):
            parts.append(paragraphs[index]["text"])
    for offset in range(1, NEARBY_PARAGRAPH_RADIUS + 1):
        index = paragraph_position + offset
        if 0 <= index < len(paragraphs) and paragraphs[index].get("text"):
            parts.append(paragraphs[index]["text"])
    return " ".join(parts)[:MAX_NEARBY_TEXT_LENGTH]


def classify_asset_kind(width_px: int, height_px: int, placement: Any) -> str:
    """参考 :143-147：达到页扫尺寸 → page-scan；inline/anchor → standalone。"""
    if width_px >= PAGE_SCAN_MIN_WIDTH_PX and height_px >= PAGE_SCAN_MIN_HEIGHT_PX:
        return "page-scan"
    return "standalone" if placement in ("inline", "anchor") else "unknown"


def collect_drawing_occurrences(xml: Any, relationships: Mapping[str, str],
                                part_label: str, paragraph_index: Mapping[str, Any]
                                ) -> list[dict[str, Any]]:
    """参考 :148-181：`<w:drawing>`（含 a:blip 引用与 wp:extent 尺寸，EMU→px）。"""
    paragraphs = paragraph_index["paragraphs"]
    positions = paragraph_index["positions"]
    occurrences: list[dict[str, Any]] = []
    for drawing in re.finditer(r"<w:drawing>[\s\S]*?</w:drawing>", str(xml or "")):
        block = drawing.group(0)
        embed = (re.search(r"\ba:blip\b[^>]*\br:embed=\"([^\"]+)\"", block)
                 or re.search(r"\br:embed=\"([^\"]+)\"", block))
        if not embed:
            continue
        target = relationships.get(embed.group(1))
        if not target or (not re.match(r"^word/media/", target)
                          and not re.match(r"^media/", target)):
            continue
        placement = "anchor" if re.search(r"<wp:anchor\b", block) else "inline"
        extent = (re.search(r"<wp:extent\b[^>]*\bcx=\"(\d+)\"[^>]*\bcy=\"(\d+)\"", block)
                  or re.search(r"<wp:extent\b[^>]*\bcy=\"(\d+)\"[^>]*\bcx=\"(\d+)\"", block))
        width_px = round(int(extent.group(1)) / EMU_PER_PX) if extent else 0
        height_px = round(int(extent.group(2)) / EMU_PER_PX) if extent else 0
        paragraph_position = find_paragraph_position(positions, drawing.start())
        occurrences.append({
            "mediaName": re.sub(r"^word/", "", target),
            "relationshipId": embed.group(1),
            "placement": placement,
            "width": width_px,
            "height": height_px,
            "assetKind": classify_asset_kind(width_px, height_px, placement),
            "locationHint": part_label,
            "nearbyText": extract_nearby_text(paragraphs, paragraph_position),
            "sectionTitle": extract_section_title(paragraphs, paragraph_position),
        })
    return occurrences


def collect_vml_occurrences(xml: Any, relationships: Mapping[str, str],
                            part_label: str, paragraph_index: Mapping[str, Any]
                            ) -> list[dict[str, Any]]:
    """参考 :183-210：VML `<v:imagedata>`（尺寸在外层 v:shape 的 style，单位 pt）。"""
    paragraphs = paragraph_index["paragraphs"]
    positions = paragraph_index["positions"]
    occurrences: list[dict[str, Any]] = []
    for match in re.finditer(r"<v:imagedata\b[^>]*\br:id=\"([^\"]+)\"[^>]*/?>", str(xml or "")):
        target = relationships.get(match.group(1))
        if not target or (not re.match(r"^word/media/", target)
                          and not re.match(r"^media/", target)):
            continue
        shape_start = str(xml or "").rfind("<v:shape", 0, match.start())
        style_block = str(xml or "")[shape_start:match.start()] if shape_start >= 0 else ""
        width_pt = float((re.search(r"width:([\d.]+)pt", style_block, re.I) or [None, 0])[1] or 0)
        height_pt = float((re.search(r"height:([\d.]+)pt", style_block, re.I) or [None, 0])[1] or 0)
        width_px = round(width_pt * PT_TO_EMU / EMU_PER_PX)
        height_px = round(height_pt * PT_TO_EMU / EMU_PER_PX)
        paragraph_position = find_paragraph_position(positions, match.start())
        occurrences.append({
            "mediaName": re.sub(r"^word/", "", target),
            "relationshipId": match.group(1),
            "placement": "inline",
            "width": width_px,
            "height": height_px,
            "assetKind": classify_asset_kind(width_px, height_px, "inline"),
            "locationHint": part_label,
            "nearbyText": extract_nearby_text(paragraphs, paragraph_position),
            "sectionTitle": extract_section_title(paragraphs, paragraph_position),
        })
    return occurrences


def extract_docx_visual_assets(docx_file_path: Any) -> dict[str, list[Any]]:
    """参考 :212-256：解压 DOCX → 正文/页眉/页脚三段收集 → 媒体文件清单（sha256）。"""
    source_path = Path(docx_file_path)
    with zipfile.ZipFile(source_path) as archive:
        def read_part(name: str) -> str | None:
            try:
                return archive.read(name).decode("utf-8")
            except KeyError:
                return None

        document_xml = read_part("word/document.xml")
        if not document_xml:
            raise ValueError("DOCX 缺少 word/document.xml，无法提取图片")

        parts = [{"xml": document_xml,
                  "rels": parse_relationships(read_part("word/_rels/document.xml.rels") or ""),
                  "label": "正文"}]
        for name in archive.namelist():
            part_match = re.match(r"^word/(header\d*|footer\d*)\.xml$", name)
            if not part_match:
                continue
            parts.append({
                "xml": archive.read(name).decode("utf-8"),
                "rels": parse_relationships(
                    read_part(f"word/_rels/{part_match.group(1)}.xml.rels") or ""),
                "label": "页眉" if part_match.group(1).startswith("header") else "页脚",
            })

        occurrences: list[dict[str, Any]] = []
        for part in parts:
            paragraph_index = build_paragraph_index(part["xml"])
            occurrences.extend(collect_drawing_occurrences(
                part["xml"], part["rels"], part["label"], paragraph_index))
            occurrences.extend(collect_vml_occurrences(
                part["xml"], part["rels"], part["label"], paragraph_index))
        media_files: list[dict[str, Any]] = []
        for name in archive.namelist():
            if not re.match(r"^word/media/[^/]+$", name):
                continue
            buffer = archive.read(name)
            extension_match = re.search(r"\.[^.]+$", name)
            extension = (extension_match.group(0).lower() if extension_match else "")
            media_files.append({
                "mediaName": re.sub(r"^word/", "", name),
                "buffer": buffer,
                "contentHash": hashlib.sha256(buffer).hexdigest(),
                "mimeType": MIME_BY_EXTENSION.get(extension, "application/octet-stream"),
            })
    return {"occurrences": occurrences, "mediaFiles": media_files}


def extension_for_mime_type(mime_type: Any) -> str:
    """参考 subjectMediaFiles.cjs 的扩展名映射（写盘用）。"""
    for extension, mime in MIME_BY_EXTENSION.items():
        if mime == mime_type:
            return extension
    return ".bin"


def write_visual_media_files(media_dir: Path,
                             media_files: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """参考 subjectMediaFiles.cjs:30-45：按 contentHash 落盘 `<hash>/<hash>.<ext>`，
    同 hash 只写一次；返回 mediaName → relativePath。"""
    written: dict[str, str] = {}
    for media in media_files:
        media_name = str(media.get("mediaName") or "")
        buffer = media.get("buffer")
        if not media_name or not isinstance(buffer, (bytes, bytearray)):
            continue
        content_hash = str(media.get("contentHash") or "").strip() or "unknown"
        extension = extension_for_mime_type(media.get("mimeType"))
        relative_path = f"{content_hash}/{content_hash}{extension}"
        target = media_dir / content_hash / f"{content_hash}{extension}"
        if media_name not in written:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(bytes(buffer))
        written[media_name] = relative_path
    return written
