"""查重元数据提取的 I/O 组装（P3d T2 起，R-P3d-11）。

分层约定：本包只做**文件 → XML/字节/CFB 条目**的读取与后续 T5 的
dispatch/stat/sha256 组装；纯解析在 `app/domain/duplicate/metadata_*`。

对应参考：`extractDocxMetadata` 的 `new AdmZip(filePath)` + `readZipText`
（svc.cjs :313-316、:704-708、:327）。adm-zip `getEntry` 是**精确名匹配**、
`getEntries()` 保持 central directory 顺序——zipfile 的 `read`/`infolist` 语义
一致。缺条目 → 空串（:313-316 `entry ? … : ''`）。

CFB（T3）：`extractOleMetadata` 的 `CFB.read(buffer, {type:'buffer'})` +
`cfb.FileIndex`/`cfb.FullPaths`（svc.cjs :744-745）由 `read_cfb_entries`
用 olefile 复刻（olefile 只做 CFB 导航与流读取，属性集解析自写）。

PDF（T4）：`extractPdfMetadata` 的 `new PDFParse({data})` + `getInfo()`
（svc.cjs :1028-1056）由 pypdf 复刻成 `PdfParseResult` 形状（R-P3d-10）：
pypdf 只做文档导航/Info 字典/XMP 原始包，XMP 结构化（pdf.js
MetadataParser 的 Alt/Seq 简化版）在这里自写。
"""

from __future__ import annotations

import hashlib
import io
import math
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypedDict

import olefile
import pypdf
from olefile import STGTY_ROOT, STGTY_STORAGE, STGTY_STREAM
from pypdf.generic import NameObject

from app.domain.duplicate.metadata_base import (
    add_decoded_base64_fields,
    add_field,
    add_list_field,
    merge_metadata_fields,
    metadata_items,
)
from app.domain.duplicate.metadata_docx import extract_docx_fields
from app.domain.duplicate.metadata_ole import CfbEntry, extract_ole_fields
from app.domain.duplicate.metadata_pdf import (
    PDF_LINEARIZED_RE,
    PdfMetadata,
    PdfParseResult,
    extract_pdf_fields,
    pdf_header_version,
)
from app.domain.duplicate.metadata_signals import add_wps_signal_fields

# 参考：/^word\/.*\.xml$/i（:327，对 entryName 全串匹配）
_WORD_XML_RE = re.compile(r"word/.*\.xml", re.IGNORECASE)


def read_zip_entry_text(archive: zipfile.ZipFile, entry_name: str) -> str:
    """`readZipText`（svc.cjs :313-316）：缺条目返回空串。"""
    try:
        return archive.read(entry_name).decode("utf-8")
    except KeyError:
        return ""


def read_docx_zip_parts(path: Path) -> tuple[str, str, str, list[str]]:
    """读出 extractDocxMetadata 需要的全部 XML 文本。

    返回 `(core_xml, app_xml, custom_xml, word_xml_texts)`；word/*.xml 的
    顺序 = zip central directory 顺序（RSID root 取「首个命中」依赖此序）。
    """
    with zipfile.ZipFile(path) as archive:
        core = read_zip_entry_text(archive, "docProps/core.xml")
        app = read_zip_entry_text(archive, "docProps/app.xml")
        custom = read_zip_entry_text(archive, "docProps/custom.xml")
        word_xml_texts = [
            archive.read(info.filename).decode("utf-8")
            for info in archive.infolist()
            if _WORD_XML_RE.fullmatch(info.filename or "")
        ]
    return core, app, custom, word_xml_texts


def extract_docx_metadata_file(path: Path) -> dict[str, str]:
    """`extractDocxMetadata(filePath)` 的对应物（T5 dispatch 的 docx 分支）。"""
    core, app, custom, word_xml_texts = read_docx_zip_parts(path)
    return extract_docx_fields(core, app, custom, word_xml_texts)


# ---------------------------------------------------------------- CFB 适配（T3）


def _walk_cfb_paths(entry: Any, paths: dict[int, str]) -> None:
    """从 root DFS 构建每个 sid 的 cfb FullPaths 形状（见 read_cfb_entries）。"""
    for kid in entry.kids:
        if kid.entry_type == STGTY_STORAGE:
            paths[kid.sid] = paths[entry.sid] + kid.name + "/"
        else:
            paths[kid.sid] = paths[entry.sid] + kid.name
        _walk_cfb_paths(kid, paths)


def read_cfb_entries(path: Path) -> list[CfbEntry]:
    """olefile → `CfbEntry` 列表（`cfb.FileIndex` + `cfb.FullPaths` 等价形状）。

    **形状契约（oracle-ole.out.json 的 cfb_shape 钉定）**：

    - 顺序 = 目录项 sid 序（cfb 的 FileIndex 即 sid 序；FullPaths[i] 与
      FileIndex[i] 一一对应）；
    - FullPaths 无前导 `/`：root = ``Root Entry/``、storage 尾带 `/`、
      流不带；**未挂到树上的空槽**（olefile 里保持 None 的目录项）= `/`；
    - content：**仅 root 为 None**；流按声明大小读出（空流 → ``b''``）；
      storage/空槽为 ``b''``（oracle：`contentIsUndefined` 只对 root 成立）。

    流读取用 `ole._open(isectStart, size)`（sid 精确、自动路由 mini 流），
    不走 `openstream`——后者按名字大小写不敏感查找，有同名异 case 碰撞风险。
    """
    ole = olefile.OleFileIO(str(path))
    try:
        paths: dict[int, str] = {0: "Root Entry/"}
        _walk_cfb_paths(ole.root, paths)

        entries: list[CfbEntry] = []
        for sid, dirent in enumerate(ole.direntries):
            if dirent is None or dirent.entry_type == 0:  # 空槽
                entries.append(CfbEntry("/", 0, 0, b""))
                continue
            entry_type = int(dirent.entry_type)
            if entry_type == STGTY_ROOT:
                content: bytes | None = None
            elif entry_type == STGTY_STREAM:
                if dirent.size:
                    content = ole._open(dirent.isectStart, dirent.size).read()
                else:
                    content = b""
            else:  # storage（及病态的 lockbytes/property）——无 content
                content = b""
            entries.append(CfbEntry(
                path=paths.get(sid, "/"),
                entry_type=entry_type,
                size=int(dirent.size),
                content=content,
            ))
        return entries
    finally:
        ole.close()


def extract_ole_metadata_file(path: Path) -> dict[str, str]:
    """`extractOleMetadata(filePath)` 的对应物（T5 dispatch 的 .doc/.wps 分支）。

    参考里 `CFB.read` 的解析失败会抛异常、由 legacy 包装层转
    「OLE 元数据读取失败：…」——这里让 olefile 的异常原样传播。
    """
    return extract_ole_fields(read_cfb_entries(path))


# ---------------------------------------------------------------- PDF 适配（T4）

_RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_RDF_CONTAINERS = frozenset({"Alt", "Seq", "Bag"})


def _xml_local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_uri(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _iter_xmp_namespaces(raw_text: str) -> dict[str, str]:
    """iterparse 抓 start-ns：uri → 首个声明的前缀（pdf.js 保留声明前缀，
    键 = `prefix:local` 小写，oracle 的 `pdfw:customprop` 即此形态）。"""
    prefixes: dict[str, str] = {}
    try:
        for _event, ns in ET.iterparse(io.BytesIO(raw_text.encode("utf-8")),
                                       events=("start-ns",)):
            prefix, uri = ns
            prefixes.setdefault(uri, prefix)
    except ET.ParseError:
        pass
    return prefixes


def _xmp_qname(tag: str, prefixes: dict[str, str]) -> str:
    uri = _xml_uri(tag)
    local = _xml_local(tag)
    prefix = prefixes.get(uri, uri)
    return f"{prefix}:{local}".lower() if prefix else local.lower()


def _xmp_property_value(element: ET.Element) -> Any:
    """属性元素取值：rdf:Alt → 首个 li 文本（字符串）；rdf:Seq/Bag → li
    文本数组；其余 → 元素文本（`&#10;` 实体由 ET 解码，保留原文不折叠）。"""
    for child in element:
        if _xml_uri(child.tag) == _RDF_NS and _xml_local(child.tag) in _RDF_CONTAINERS:
            items = [(li.text or "") for li in child
                     if _xml_uri(li.tag) == _RDF_NS and _xml_local(li.tag) == "li"]
            if _xml_local(child.tag) == "Alt":
                return items[0] if items else ""
            return items
    return element.text or ""


def parse_xmp_metadata(raw_text: str) -> dict[str, Any]:
    """XMP 包串 → pdf.js MetadataParser 的简化形状（键全小写、插入序）。

    覆盖：rdf:Description 的**属性式**与**子元素式**属性、Alt/Seq/Bag
    容器、简单文本值；更深嵌套/parseType=Resource 不在 fixtures 与中文
    标书语料形态内（偏离登记 spec §15）。解析失败 → 空 entries（raw 仍
    随行，raw_signals 不受影响）。
    """
    prefixes = _iter_xmp_namespaces(raw_text)
    try:
        root = ET.fromstring(raw_text)
    except ET.ParseError:
        try:
            root = ET.fromstring(raw_text.replace("\x00", ""))
        except ET.ParseError:
            return {}
    entries: dict[str, Any] = {}
    for node in root.iter():
        if _xml_uri(node.tag) != _RDF_NS or _xml_local(node.tag) != "Description":
            continue
        for attr_tag, attr_value in node.attrib.items():
            if _xml_uri(attr_tag) == _RDF_NS:  # rdf:about 等结构属性
                continue
            entries[_xmp_qname(attr_tag, prefixes)] = attr_value
        for child in node:
            if _xml_uri(child.tag) == _RDF_NS:  # rdf 结构元素不是属性
                continue
            entries[_xmp_qname(child.tag, prefixes)] = _xmp_property_value(child)
    return entries


def _pdf_root(reader: pypdf.PdfReader) -> Any:
    try:
        root = reader.trailer["/Root"]
    except (KeyError, ValueError):
        return None
    return root.get_object() if root is not None else None


def _pdf_acroform(root: Any) -> Any:
    if root is None:
        return None
    raw = root.get("/AcroForm")
    if raw is None:
        return None
    try:
        return raw.get_object()
    except Exception:
        return None


def _pdf_signatures_present(acroform: Any) -> bool:
    """pdf.js 的 IsSignaturesPresent 来自**解析后**的签名字段（非 raw 正则，
    手拼在尾部的 /FT /Sig 不算）；这里走 AcroForm/Fields 的 /FT。"""
    if acroform is None:
        return False
    try:
        fields = acroform.get("/Fields")
        if fields is None:
            return False
        fields = fields.get_object() if hasattr(fields, "get_object") else fields
        for field in fields:
            field_obj = field.get_object() if hasattr(field, "get_object") else field
            if field_obj is not None and str(field_obj.get("/FT", "")) == "/Sig":
                return True
    except Exception:
        pass
    return False


def _pdf_info_dict(reader: pypdf.PdfReader, buffer: bytes, root: Any,
                   acroform: Any) -> dict[str, Any]:
    """pdf.js `getMetadata().info` 的派生键（前 8 个）+ Info 字典字符串值。

    派生键的口径：IsLinearized 用与参考 `addPdfStructureFields` 同源的
    `/Linearized\\s+\\d` 头部启发式（pdf.js 是线性化字典解析，非线性化
    文件上两口径等值）；IsSignaturesPresent 走 AcroForm/Fields；加密文档
    的 EncryptFilterName 取 /Encrypt 的 /Filter（剥斜杠，无加密 fixture
    对拍，best-effort）。
    """
    head_text = buffer[:4096].decode("latin-1", errors="replace")
    encrypt_name = None
    if reader.is_encrypted:
        try:
            encrypt = reader.trailer["/Encrypt"].get_object()
            filt = encrypt.get("/Filter")
            if filt is not None:
                encrypt_name = str(filt).lstrip("/")
        except Exception:
            encrypt_name = None
    lang = root.get("/Lang") if root is not None else None

    info: dict[str, Any] = {
        "PDFFormatVersion": pdf_header_version(head_text) or "1.7",
        "Language": lang if isinstance(lang, str) else None,
        "EncryptFilterName": encrypt_name,
        "IsLinearized": PDF_LINEARIZED_RE.search(head_text) is not None,
        "IsAcroFormPresent": acroform is not None,
        "IsXFAPresent": bool(acroform is not None and acroform.get("/XFA") is not None),
        "IsCollectionPresent": bool(root is not None and root.get("/Collection") is not None),
        "IsSignaturesPresent": _pdf_signatures_present(acroform),
    }
    doc_info = reader.metadata
    if doc_info is not None:
        for key, value in doc_info.items():  # pypdf 保持文件序
            name = str(key)
            if name.startswith("/"):
                name = name[1:]
            # pdf.js 只收字符串值；NameObject 是 str 子类但 pdf.js 里是 Name → 排除
            if isinstance(value, str) and not isinstance(value, NameObject):
                info[name] = value
    return info


def _pdf_fingerprints(reader: pypdf.PdfReader, buffer: bytes) -> list[str]:
    """pdf.js fingerprints：trailer /ID 两项字节的小写十六进制（oracle 实测
    = pdf_trailer_id 的同一对值）；缺项/缺失 → 整文件 MD5 兜底（best-effort）。"""
    md5_hex = hashlib.md5(buffer).hexdigest()

    def entry_hex(index: int, id_array: Any) -> str:
        if id_array is not None and index < len(id_array) and id_array[index] is not None:
            value = id_array[index]
            if isinstance(value, str) and not isinstance(value, bytes):
                # TextStringObject：pypdf 已按 UTF-16 解码，get_original_bytes 还原原始字节
                original = getattr(value, "get_original_bytes", None)
                raw = original() if callable(original) else value.encode("latin-1", "replace")
            else:
                raw = bytes(value)
            if raw:
                return raw.hex()
        return md5_hex

    try:
        id_array = reader.trailer.get("/ID")
    except Exception:
        id_array = None
    if isinstance(id_array, (list, tuple)) and len(id_array):
        return [entry_hex(0, id_array), entry_hex(1, id_array)]
    return [md5_hex, md5_hex]


def _pdf_permissions(reader: pypdf.PdfReader) -> list[int] | None:
    """未加密 → None（oracle：result.permission 为 null）；加密 → pdf.js
    PermissionFlag 位值列表（PRINT=4 … PRINT_TO_REPRESENTATION=2048，
    无加密 fixture 对拍，best-effort）。"""
    if not reader.is_encrypted:
        return None
    flags = getattr(reader, "user_access_permissions", None)
    if not isinstance(flags, int):
        return None
    return [bit for bit in (4, 8, 16, 32, 256, 512, 1024, 2048) if flags & bit]


def read_pdf_parse_result(path: Path) -> PdfParseResult:
    """pypdf → `PdfParseResult`（pdf-parse getInfo 的形状契约）。

    依赖 pypdf 的异常原样传播（解析失败走 T5 的 metadata_error 文案路径，
    与参考 `extractPdfMetadata` 抛错等价）。
    """
    buffer = path.read_bytes()
    reader = pypdf.PdfReader(io.BytesIO(buffer), strict=False)
    root = _pdf_root(reader)
    acroform = _pdf_acroform(root)

    metadata: PdfMetadata | None = None
    raw_xmp_obj = root.get("/Metadata") if root is not None else None
    if raw_xmp_obj is not None:
        try:
            raw_xmp = raw_xmp_obj.get_object().get_data()
        except Exception:
            raw_xmp = None
        if raw_xmp:
            raw_text = raw_xmp.decode("utf-8", errors="replace")
            metadata = PdfMetadata(parse_xmp_metadata(raw_text), raw_text)

    return PdfParseResult(
        info=_pdf_info_dict(reader, buffer, root, acroform),
        metadata=metadata,
        buffer=buffer,
        total=len(reader.pages),
        fingerprints=_pdf_fingerprints(reader, buffer),
        permission=_pdf_permissions(reader),
        version=None,  # pdf-parse@2 的 result.version 不存在（R-P3d-10 实测）
    )


def extract_pdf_metadata_file(path: Path) -> dict[str, str]:
    """`extractPdfMetadata(filePath)` 的对应物（T5 dispatch 的 .pdf 分支）。"""
    result = read_pdf_parse_result(path)
    return extract_pdf_fields(
        result.info, result.metadata, result.buffer,
        total=result.total, version=result.version,
        fingerprints=result.fingerprints, permission=result.permission)


# ---------------------------------------------------------------- T5 组装器
# 参考 extractMetadata（svc.cjs :1060-1094）：stat 三时间 / sha256 / extension
# dispatch / metadata_error / addDecodedBase64Fields / items 映射。


class MetadataFileInput(TypedDict):
    """`extractMetadata(file)` 的入参形状（svc.cjs :1063-1065 消费的四个键）。"""

    file_path: str
    file_name: str
    extension: str
    size: int | None


def has_zip_header(path: Path) -> bool:
    """`hasZipHeader`（svc.cjs :780-789）：前 4 字节 = `PK\\x03\\x04`。

    JS `bytesRead >= 4` 短读（文件不足 4 字节）→ False。
    """
    with open(path, "rb") as handle:
        head = handle.read(4)
    return len(head) >= 4 and head == b"PK\x03\x04"


def hash_file_sha256(path: Path) -> str:
    """`hashFileSha256`（svc.cjs :261-264）：全文件 sha256 hex（小写）。"""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


_EPOCH_DATETIME = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _js_stat_iso(seconds: float) -> str:
    """`stats.<time>.toISOString()` 口径：UTC、`T` 分隔、毫秒恒 3 位、`Z` 后缀。

    Node fs.Stats 的 Date 毫秒 = **round**(原始 double 毫秒)（round 发生在 Node fs
    层而非 Date 构造——JS Date 构造是朝零截断（实测 `new Date(1.9).getTime()`
    = 1），而 fs 的 `.597/.812/.715/.892→+1`、`.164/.267→+0` 四组实测全符
    round-half-up）。Python 侧 `st_*` 秒与 Node `xxxMs` 同为 FILETIME 双精度换算，
    `floor(x*1000+0.5)` 与 JS Math.round（正数域）一致。Windows 的 st_ctime =
    creation time（= Node birthtime）；Linux 上 Node birthtime 同样回落 st_ctime，
    两平台语义一致。1ulp 边界差异登记 spec §15。
    """
    ms = math.floor(seconds * 1000 + 0.5)
    dt = _EPOCH_DATETIME + timedelta(milliseconds=ms)  # int ms → int µs，无 double 往返
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{ms % 1000:03d}Z"


def _js_error_text(error: BaseException) -> str:
    """`error.message || error` 的对应物（:797/:806 的 push 文案 fallback）。

    message 空时 JS 走 `String(error)`——对 Error 子类即 `'Error'` 等 name，
    这里用异常类型名近似。
    """
    return str(error) or type(error).__name__


# R-P3d-6：本阶段不接 soffice 转换。参考在无任何转换后端的机器上
# `withLegacyWordDocxFile` 抛 `ConversionError('office_backend_missing',
# LIBREOFFICE_REQUIRED_MESSAGE)`（convert.mjs :1064-1071），经
# `normalizeDocumentParseError` 重建后 message 不变。文案逐字转录
# documentParseErrors.cjs :4（全角标点由 add_list_field 的 NFKC 折叠，
# oracle-extract.out.json 实证折后形态）。
_LIBREOFFICE_REQUIRED_MESSAGE = (
    ".doc和.wps识别，需要安装 LibreOffice、WPS Office 或 Microsoft Word 任意一种"
    "本地转换组件（LibreOffice 下载：https://zh-tw.libreoffice.org/download/），"
    "或者手动将文件转换为.docx格式再上传"
)


class MetadataConvertUnavailableError(Exception):
    """converted_docx 转换不可用（R-P3d-6 的 office_backend_missing 对应物）。"""


def extract_legacy_word_metadata_file(path: Path) -> dict[str, str]:
    """`extractLegacyWordMetadata`（svc.cjs :791-812）。

    - OLE 提取失败**不中断**：错误收集进 `metadata_error`（add_list_field 的
      `；` 连接，经 NFKC 折叠后分隔符为半角 `;`）；
    - converted_docx 部分恒走「转换失败」分支（R-P3d-6）；
    - 收尾 `addWpsSignalFields`（:810）。
    """
    fields: dict[str, str] = {}
    errors: list[str] = []
    try:
        merge_metadata_fields(fields, extract_ole_metadata_file(path))
    except Exception as error:  # noqa: BLE001 —— 参考 catch (error)
        errors.append(f"OLE 元数据读取失败：{_js_error_text(error)}")

    try:
        # converted_docx 分支：无 soffice 形态恒失败（R-P3d-6）。
        raise MetadataConvertUnavailableError(_LIBREOFFICE_REQUIRED_MESSAGE)
    except Exception as error:  # noqa: BLE001
        errors.append(f"转换 DOCX 元数据读取失败：{_js_error_text(error)}")

    if errors:
        add_list_field(fields, "metadata_error", "；".join(errors))
    add_wps_signal_fields(fields)
    return fields


def extract_metadata_file(file: MetadataFileInput) -> list[dict[str, Any]]:
    """`extractMetadata`（svc.cjs :1060-1094）全流程。

    - stat 在 try **外**：文件不存在直接上抛（R-P3d-4，runner 逐文件容错）；
    - `file.size || stats.size`：0/None 回落实际大小（:1065）；
    - dispatch（:1072-1079）：`.docx` 不查 zip 头；`.doc`/`.wps` 查 zip 头
      决定 docx/legacy 分支；`.pdf` 直走；其他扩展名无分支；
    - `metadata_error` 只在分支异常时出现（:1081，文案 fallback '元数据读取失败'）。
    """
    path = Path(file["file_path"])
    stats = os.stat(path)

    fields: dict[str, str] = {}
    add_field(fields, "file_name", file["file_name"])
    add_field(fields, "extension", file["extension"])
    add_field(fields, "size", file["size"] or stats.st_size)
    add_field(fields, "created_at", _js_stat_iso(stats.st_ctime))
    add_field(fields, "modified_at", _js_stat_iso(stats.st_mtime))
    add_field(fields, "accessed_at", _js_stat_iso(stats.st_atime))
    add_field(fields, "file_sha256", hash_file_sha256(path))

    try:
        extension = str(file["extension"] or "").lower()
        if extension == ".docx" or (
            (extension == ".doc" or extension == ".wps") and has_zip_header(path)
        ):
            merge_metadata_fields(fields, extract_docx_metadata_file(path))
        elif extension == ".doc" or extension == ".wps":
            merge_metadata_fields(fields, extract_legacy_word_metadata_file(path))
        elif extension == ".pdf":
            merge_metadata_fields(fields, extract_pdf_metadata_file(path))
    except Exception as error:  # noqa: BLE001 —— 参考 catch (error)
        add_field(fields, "metadata_error", str(error) or "元数据读取失败")

    add_decoded_base64_fields(fields)
    return metadata_items(fields)
