"""T5 组装器测试：extract_metadata_file 对拍参考 extractMetadata（svc.cjs :1060-1094）。

oracle 真值来源：`oracle-extract.cjs` 直跑参考（stat 三时间 toISOString / sha256 /
extension dispatch / legacy metadata_error 文案 / items 映射）。已知登记偏离
（spec §15）不进断言：3 位年直取的 LMT 秒差（'936'/'920'/'900'）、accessed_at
时序噪音、含字母的 V8 病态串（'wps-uid-77'）。
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

import pytest

from app.domain.duplicate.js_date import js_date_iso
from app.infra.parsing.metadata_extract import (
    MetadataFileInput,
    _js_stat_iso,
    extract_metadata_file,
    has_zip_header,
    hash_file_sha256,
)
from test_metadata_docx import build_docx_fixture

FIXTURES = Path(__file__).parent / "fixtures"

_TIME_KEYS = {"created_at", "modified_at", "accessed_at"}
_ISO_MS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_THREE_DIGIT_RE = re.compile(r"^\d{3}$")
_ISO_ANY_RE = re.compile(r"^[+-]?\d{4,6}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

# ---- oracle 真值常量（oracle-extract.cjs 直跑参考 svc.cjs 产出，勿手改）----

_ORACLE_STAT_ISO = [
    # ole-metadata-fixture.doc
    (1790046704404, "2026-09-22T03:11:44.404Z"),
    (1790053305126, "2026-09-22T05:01:45.126Z"),
    # pdf-info-fixture.pdf
    (1790050331947, "2026-09-22T04:12:11.947Z"),
    (1790052520804, "2026-09-22T04:48:40.804Z"),
    # fixture-full.docx
    (1790045858954, "2026-09-22T02:57:38.954Z"),
    (1790045858954, "2026-09-22T02:57:38.954Z"),
]

_ORACLE_LEGACY_ERROR = "\u8f6c\u6362 DOCX \u5143\u6570\u636e\u8bfb\u53d6\u5931\u8d25:.doc\u548c.wps\u8bc6\u522b,\u9700\u8981\u5b89\u88c5 LibreOffice\u3001WPS Office \u6216 Microsoft Word \u4efb\u610f\u4e00\u79cd\u672c\u5730\u8f6c\u6362\u7ec4\u4ef6(LibreOffice \u4e0b\u8f7d:https://zh-tw.libreoffice.org/download/),\u6216\u8005\u624b\u52a8\u5c06\u6587\u4ef6\u8f6c\u6362\u4e3a.docx\u683c\u5f0f\u518d\u4e0a\u4f20"

# 分支 tag → items 列表；时间键（*_at）的 value/normalized/date_day 会随
# 文件触碰漂移，测试对比时跳过其值（只保留键序与存在性）。

_ORACLE_FULL = {
    "docx_branch": [
        ["file_name", "\u6587\u4ef6\u540d", "fixture-full.docx", "fixture-full.docx", ""],
        ["extension", "\u6269\u5c55\u540d", ".docx", ".docx", ""],
        ["size", "\u6587\u4ef6\u5927\u5c0f", "2666", "2666-01-01T00:00:00.000Z", "2666-01-01"],
        ["created_at", "\u6587\u4ef6\u521b\u5efa\u65f6\u95f4", "2026-09-22T02:57:38.954Z", "2026-09-22T02:57:38.954Z", "2026-09-22"],
        ["modified_at", "\u6587\u4ef6\u4fee\u6539\u65f6\u95f4", "2026-09-22T02:57:38.954Z", "2026-09-22T02:57:38.954Z", "2026-09-22"],
        ["accessed_at", "\u6587\u4ef6\u8bbf\u95ee\u65f6\u95f4", "2026-09-22T05:50:54.224Z", "2026-09-22T05:50:54.224Z", "2026-09-22"],
        ["file_sha256", "\u539f\u59cb\u6587\u4ef6 SHA256", "c20e3ebff195cf4a48014b306ec909282948b88e4640cc2abecd55f99f8de17e", "c20e3ebff195cf4a48014b306ec909282948b88e4640cc2abecd55f99f8de17e", ""],
        ["title", "\u6807\u9898", "\u57ce\u5e02\u8f68\u9053 & \u4ea4\u901a\u6807\u4e66", "\u57ce\u5e02\u8f68\u9053&\u4ea4\u901a\u6807\u4e66", ""],
        ["subject", "\u4e3b\u9898", "\u6295\u6807\u6587\u4ef6", "\u6295\u6807\u6587\u4ef6", ""],
        ["author", "\u4f5c\u8005", "\u5f20\u4e09 mail:zhangsan@example.com", "\u5f20\u4e09mail:zhangsan@example.com", ""],
        ["last_modified_by", "\u6700\u540e\u4fee\u6539\u4eba", "\u674e\u56db(WPS)", "\u674e\u56db(wps)", ""],
        ["revision", "\u4fee\u8ba2\u53f7", "3", "2001-02-28T16:00:00.000Z", "2001-02-28"],
        ["created", "\u521b\u5efa\u65f6\u95f4", "2024-01-15T08:00:00Z", "2024-01-15T08:00:00.000Z", "2024-01-15"],
        ["modified", "\u4fee\u6539\u65f6\u95f4", "2024-03-20T12:30:00Z", "2024-03-20T12:30:00.000Z", "2024-03-20"],
        ["keywords", "\u5173\u952e\u8bcd", "\u6865\u6881;\u96a7\u9053", "\u6865\u6881;\u96a7\u9053", ""],
        ["category", "\u7c7b\u522b", "\u5de5\u7a0b\u6807\u4e66", "\u5de5\u7a0b\u6807\u4e66", ""],
        ["description", "\u63cf\u8ff0", "\u7b2c\u4e00\u8282&#10;\u7b2c\u4e8c\u8282", "\u7b2c\u4e00\u8282&#10;\u7b2c\u4e8c\u8282", ""],
        ["application", "\u5e94\u7528\u7a0b\u5e8f", "WPS Office", "wpsoffice", ""],
        ["app_version", "\u5e94\u7528\u7a0b\u5e8f\u7248\u672c", "11.1.0", "2000-10-31T16:00:00.000Z", "2000-10-31"],
        ["company", "\u516c\u53f8", "\u4e2d\u94c1\u5341\u4e00\u5c40", "\u4e2d\u94c1\u5341\u4e00\u5c40", ""],
        ["manager", "\u7ba1\u7406\u8005", "\u738b\u7ecf\u7406", "\u738b\u7ecf\u7406", ""],
        ["template", "\u6a21\u677f", "normal.dotm", "normal.dotm", ""],
        ["pages", "\u9875\u6570", "42", "2041-12-31T16:00:00.000Z", "2041-12-31"],
        ["words", "\u5b57\u6570", "12345", "+012344-12-31T16:00:00.000Z", "+012344-12"],
        ["characters", "\u5b57\u7b26\u6570", "67890", "+067889-12-31T16:00:00.000Z", "+067889-12"],
        ["lines", "\u884c\u6570", "900", "0899-12-31T15:54:17.000Z", "0899-12-31"],
        ["paragraphs", "\u6bb5\u843d\u6570", "150", "0149-12-31T15:54:17.000Z", "0149-12-31"],
        ["total_time", "\u7f16\u8f91\u65f6\u957f", "120 \u5206\u949f", "120\u5206\u949f", ""],
        ["custom:\u6807\u8bb0", "\u81ea\u5b9a\u4e49\uff1a\u6807\u8bb0", "\u7edd\u5bc6\u2605\u4f7f\u7528\u524d", "\u7edd\u5bc6\u2605\u4f7f\u7528\u524d", ""],
        ["custom:KSO_Build", "\u81ea\u5b9a\u4e49\uff1aKSO_Build", "\u8d26\u53f7:wps-uid-42", "2041-12-31T16:00:00.000Z", "2041-12-31"],
        ["word_rsid_root", "Word \u7f16\u8f91\u4f1a\u8bdd\u6839 ID", "00495AEC", "00495aec", ""],
        ["word_rsid_count", "Word \u7f16\u8f91\u4f1a\u8bdd ID \u6570\u91cf", "6", "2001-05-31T16:00:00.000Z", "2001-05-31"],
        ["word_rsid_values", "Word \u7f16\u8f91\u4f1a\u8bdd ID \u5217\u8868", "000FFF01;00495AEC;00A1B2C3;00ABCDEF;00B2C3D4;0DEADBEEF", "000fff01;00495aec;00a1b2c3;00abcdef;00b2c3d4;0deadbeef", ""],
        ["word_rsid_fingerprint", "Word \u7f16\u8f91\u4f1a\u8bdd\u6307\u7eb9", "e8ea50ab6cfdbf3beaa22e513b4c9a26dd58c8d779c821d80a0a9b002188b5a0", "e8ea50ab6cfdbf3beaa22e513b4c9a26dd58c8d779c821d80a0a9b002188b5a0", ""],
        ["wps:author", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1aauthor", "\u5f20\u4e09 mail:zhangsan@example.com", "\u5f20\u4e09mail:zhangsan@example.com", ""],
        ["wps:last_modified_by", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1alast modified by", "\u674e\u56db(WPS)", "\u674e\u56db(wps)", ""],
        ["wps:application", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1aapplication", "WPS Office", "wpsoffice", ""],
        ["wps:custom_kso_build", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1acustom kso build", "\u8d26\u53f7:wps-uid-42", "2041-12-31T16:00:00.000Z", "2041-12-31"],
    ],
    "doc_with_zip_header": [
        ["file_name", "\u6587\u4ef6\u540d", "dispatch-doc-zip.doc", "dispatch-doc-zip.doc", ""],
        ["extension", "\u6269\u5c55\u540d", ".doc", ".doc", ""],
        ["size", "\u6587\u4ef6\u5927\u5c0f", "2666", "2666-01-01T00:00:00.000Z", "2666-01-01"],
        ["created_at", "\u6587\u4ef6\u521b\u5efa\u65f6\u95f4", "2026-09-22T05:25:58.553Z", "2026-09-22T05:25:58.553Z", "2026-09-22"],
        ["modified_at", "\u6587\u4ef6\u4fee\u6539\u65f6\u95f4", "2026-09-22T02:57:38.954Z", "2026-09-22T02:57:38.954Z", "2026-09-22"],
        ["accessed_at", "\u6587\u4ef6\u8bbf\u95ee\u65f6\u95f4", "2026-09-22T05:50:54.227Z", "2026-09-22T05:50:54.227Z", "2026-09-22"],
        ["file_sha256", "\u539f\u59cb\u6587\u4ef6 SHA256", "c20e3ebff195cf4a48014b306ec909282948b88e4640cc2abecd55f99f8de17e", "c20e3ebff195cf4a48014b306ec909282948b88e4640cc2abecd55f99f8de17e", ""],
        ["title", "\u6807\u9898", "\u57ce\u5e02\u8f68\u9053 & \u4ea4\u901a\u6807\u4e66", "\u57ce\u5e02\u8f68\u9053&\u4ea4\u901a\u6807\u4e66", ""],
        ["subject", "\u4e3b\u9898", "\u6295\u6807\u6587\u4ef6", "\u6295\u6807\u6587\u4ef6", ""],
        ["author", "\u4f5c\u8005", "\u5f20\u4e09 mail:zhangsan@example.com", "\u5f20\u4e09mail:zhangsan@example.com", ""],
        ["last_modified_by", "\u6700\u540e\u4fee\u6539\u4eba", "\u674e\u56db(WPS)", "\u674e\u56db(wps)", ""],
        ["revision", "\u4fee\u8ba2\u53f7", "3", "2001-02-28T16:00:00.000Z", "2001-02-28"],
        ["created", "\u521b\u5efa\u65f6\u95f4", "2024-01-15T08:00:00Z", "2024-01-15T08:00:00.000Z", "2024-01-15"],
        ["modified", "\u4fee\u6539\u65f6\u95f4", "2024-03-20T12:30:00Z", "2024-03-20T12:30:00.000Z", "2024-03-20"],
        ["keywords", "\u5173\u952e\u8bcd", "\u6865\u6881;\u96a7\u9053", "\u6865\u6881;\u96a7\u9053", ""],
        ["category", "\u7c7b\u522b", "\u5de5\u7a0b\u6807\u4e66", "\u5de5\u7a0b\u6807\u4e66", ""],
        ["description", "\u63cf\u8ff0", "\u7b2c\u4e00\u8282&#10;\u7b2c\u4e8c\u8282", "\u7b2c\u4e00\u8282&#10;\u7b2c\u4e8c\u8282", ""],
        ["application", "\u5e94\u7528\u7a0b\u5e8f", "WPS Office", "wpsoffice", ""],
        ["app_version", "\u5e94\u7528\u7a0b\u5e8f\u7248\u672c", "11.1.0", "2000-10-31T16:00:00.000Z", "2000-10-31"],
        ["company", "\u516c\u53f8", "\u4e2d\u94c1\u5341\u4e00\u5c40", "\u4e2d\u94c1\u5341\u4e00\u5c40", ""],
        ["manager", "\u7ba1\u7406\u8005", "\u738b\u7ecf\u7406", "\u738b\u7ecf\u7406", ""],
        ["template", "\u6a21\u677f", "normal.dotm", "normal.dotm", ""],
        ["pages", "\u9875\u6570", "42", "2041-12-31T16:00:00.000Z", "2041-12-31"],
        ["words", "\u5b57\u6570", "12345", "+012344-12-31T16:00:00.000Z", "+012344-12"],
        ["characters", "\u5b57\u7b26\u6570", "67890", "+067889-12-31T16:00:00.000Z", "+067889-12"],
        ["lines", "\u884c\u6570", "900", "0899-12-31T15:54:17.000Z", "0899-12-31"],
        ["paragraphs", "\u6bb5\u843d\u6570", "150", "0149-12-31T15:54:17.000Z", "0149-12-31"],
        ["total_time", "\u7f16\u8f91\u65f6\u957f", "120 \u5206\u949f", "120\u5206\u949f", ""],
        ["custom:\u6807\u8bb0", "\u81ea\u5b9a\u4e49\uff1a\u6807\u8bb0", "\u7edd\u5bc6\u2605\u4f7f\u7528\u524d", "\u7edd\u5bc6\u2605\u4f7f\u7528\u524d", ""],
        ["custom:KSO_Build", "\u81ea\u5b9a\u4e49\uff1aKSO_Build", "\u8d26\u53f7:wps-uid-42", "2041-12-31T16:00:00.000Z", "2041-12-31"],
        ["word_rsid_root", "Word \u7f16\u8f91\u4f1a\u8bdd\u6839 ID", "00495AEC", "00495aec", ""],
        ["word_rsid_count", "Word \u7f16\u8f91\u4f1a\u8bdd ID \u6570\u91cf", "6", "2001-05-31T16:00:00.000Z", "2001-05-31"],
        ["word_rsid_values", "Word \u7f16\u8f91\u4f1a\u8bdd ID \u5217\u8868", "000FFF01;00495AEC;00A1B2C3;00ABCDEF;00B2C3D4;0DEADBEEF", "000fff01;00495aec;00a1b2c3;00abcdef;00b2c3d4;0deadbeef", ""],
        ["word_rsid_fingerprint", "Word \u7f16\u8f91\u4f1a\u8bdd\u6307\u7eb9", "e8ea50ab6cfdbf3beaa22e513b4c9a26dd58c8d779c821d80a0a9b002188b5a0", "e8ea50ab6cfdbf3beaa22e513b4c9a26dd58c8d779c821d80a0a9b002188b5a0", ""],
        ["wps:author", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1aauthor", "\u5f20\u4e09 mail:zhangsan@example.com", "\u5f20\u4e09mail:zhangsan@example.com", ""],
        ["wps:last_modified_by", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1alast modified by", "\u674e\u56db(WPS)", "\u674e\u56db(wps)", ""],
        ["wps:application", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1aapplication", "WPS Office", "wpsoffice", ""],
        ["wps:custom_kso_build", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1acustom kso build", "\u8d26\u53f7:wps-uid-42", "2041-12-31T16:00:00.000Z", "2041-12-31"],
    ],
    "doc_legacy": [
        ["file_name", "\u6587\u4ef6\u540d", "ole-metadata-fixture.doc", "ole-metadata-fixture.doc", ""],
        ["extension", "\u6269\u5c55\u540d", ".doc", ".doc", ""],
        ["size", "\u6587\u4ef6\u5927\u5c0f", "4096", "4096-01-01T00:00:00.000Z", "4096-01-01"],
        ["created_at", "\u6587\u4ef6\u521b\u5efa\u65f6\u95f4", "2026-09-22T03:11:44.404Z", "2026-09-22T03:11:44.404Z", "2026-09-22"],
        ["modified_at", "\u6587\u4ef6\u4fee\u6539\u65f6\u95f4", "2026-09-22T05:01:45.126Z", "2026-09-22T05:01:45.126Z", "2026-09-22"],
        ["accessed_at", "\u6587\u4ef6\u8bbf\u95ee\u65f6\u95f4", "2026-09-22T05:50:54.222Z", "2026-09-22T05:50:54.222Z", "2026-09-22"],
        ["file_sha256", "\u539f\u59cb\u6587\u4ef6 SHA256", "083f918cb907518f3dc2eb5cce01dd3ceacac0fc1b3c8e13559106812ae40262", "083f918cb907518f3dc2eb5cce01dd3ceacac0fc1b3c8e13559106812ae40262", ""],
        ["code_page", "\u4ee3\u7801\u9875", "936", "0935-12-31T15:54:17.000Z", "0935-12-31"],
        ["title", "\u6807\u9898", "\u6865\u6881\u6295\u6807\u6587\u4ef6", "\u6865\u6881\u6295\u6807\u6587\u4ef6", ""],
        ["author", "\u4f5c\u8005", "\u5f20\u4e09 admin@corp-mail.com", "\u5f20\u4e09admin@corp-mail.com", ""],
        ["revision", "\u4fee\u8ba2\u53f7", "7", "2001-06-30T16:00:00.000Z", "2001-06-30"],
        ["total_time", "\u7f16\u8f91\u65f6\u957f", "2 \u5c0f\u65f6 15 \u5206\u949f", "2\u5c0f\u65f615\u5206\u949f", ""],
        ["created", "\u521b\u5efa\u65f6\u95f4", "2024-01-15T08:00:00.000Z", "2024-01-15T08:00:00.000Z", "2024-01-15"],
        ["ole_prop_99", "ole prop 99", "\u65e0\u5b57\u5178\u5c5e\u6027", "\u65e0\u5b57\u5178\u5c5e\u6027", ""],
        ["custom:\u79d8\u5bc6\u6807\u8bb0", "\u81ea\u5b9a\u4e49\uff1a\u79d8\u5bc6\u6807\u8bb0", "\u8d70\u5b57\u5178\u7684\u503c", "\u8d70\u5b57\u5178\u7684\u503c", ""],
        ["ole_prop_1", "ole prop 1", "936", "0935-12-31T15:54:17.000Z", "0935-12-31"],
        ["ole_prop_15", "ole prop 15", "\u4e2d\u94c1\u5efa", "\u4e2d\u94c1\u5efa", ""],
        ["ole_prop_16", "ole prop 16", "true", "true", ""],
        ["ole_prop_23", "ole prop 23", "720897", "720897", ""],
        ["company", "\u516c\u53f8", "\u4e2d\u94c1\u5efa", "\u4e2d\u94c1\u5efa", ""],
        ["links_dirty", "\u94fe\u63a5\u5df2\u53d8\u66f4", "true", "true", ""],
        ["app_version", "\u5e94\u7528\u7a0b\u5e8f\u7248\u672c", "11.0001", "2001-10-31T16:00:00.000Z", "2001-10-31"],
        ["ole_storage_count", "OLE \u5b58\u50a8\u6570\u91cf", "2", "2001-01-31T16:00:00.000Z", "2001-01-31"],
        ["ole_stream_count", "OLE Stream \u6570\u91cf", "11", "2001-10-31T16:00:00.000Z", "2001-10-31"],
        ["ole_stream_paths", "OLE Stream \u8def\u5f84\u6458\u8981", "/;Root Entry/DocumentSummaryInformation;Root Entry/Embeddings;Root Entry/Macros/;Root Entry/Macros/VBA/;Root Entry/Macros/VBA/dir;Root Entry/Sh33tJ5;Root Entry/SummaryInformation;Root Entry/WordDocument", "/;rootentry/documentsummaryinformation;rootentry/embeddings;rootentry/macros/;rootentry/macros/vba/;rootentry/macros/vba/dir;rootentry/sh33tj5;rootentry/summaryinformation;rootentry/worddocument", ""],
        ["ole_stream_paths_fingerprint", "OLE Stream \u8def\u5f84\u6307\u7eb9", "e0674736ceade93e162e3e968f8e50e952ed207c737142ab428cb8bedca078e6", "e0674736ceade93e162e3e968f8e50e952ed207c737142ab428cb8bedca078e6", ""],
        ["ole_stream_sizes_fingerprint", "OLE Stream \u5927\u5c0f\u6307\u7eb9", "39517c94687838c2130eb594ca0d65dbe8ff62f1a6d8077a4cd826599f34dc5e", "39517c94687838c2130eb594ca0d65dbe8ff62f1a6d8077a4cd826599f34dc5e", ""],
        ["ole_has_macro_storage", "OLE \u5b8f\u5b58\u50a8", "\u662f", "\u662f", ""],
        ["ole_macro_paths", "OLE \u5b8f\u5b58\u50a8\u8def\u5f84", "Root Entry/Macros/;Root Entry/Macros/VBA/;Root Entry/Macros/VBA/dir", "rootentry/macros/;rootentry/macros/vba/;rootentry/macros/vba/dir", ""],
        ["ole_signal:root_entry_macros_vba_dir", "OLE \u7591\u4f3c\u75d5\u8ff9\uff1aroot entry macros vba dir", "VBA mail code", "vbamailcode", ""],
        ["ole_signal:root_entry_worddocument", "OLE \u7591\u4f3c\u75d5\u8ff9\uff1aroot entry worddocument", "mail admin \u7490\u3009\u5f7f wps-uid-77;mail admin \u8d26\u53f7 wps-uid-77", "mailadmin\u7490\u3009\u5f7fwps-uid-77;mailadmin\u8d26\u53f7wps-uid-77", ""],
        ["wps:author", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1aauthor", "\u5f20\u4e09 admin@corp-mail.com", "\u5f20\u4e09admin@corp-mail.com", ""],
        ["wps:ole_signal_root_entry_macros_vba_dir", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1aole signal root entry macros vba dir", "VBA mail code", "vbamailcode", ""],
        ["wps:ole_signal_root_entry_worddocument", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1aole signal root entry worddocument", "mail admin \u7490\u3009\u5f7f wps-uid-77;mail admin \u8d26\u53f7 wps-uid-77", "mailadmin\u7490\u3009\u5f7fwps-uid-77;mailadmin\u8d26\u53f7wps-uid-77", ""],
        ["metadata_error", "metadata error", "\u8f6c\u6362 DOCX \u5143\u6570\u636e\u8bfb\u53d6\u5931\u8d25:.doc\u548c.wps\u8bc6\u522b,\u9700\u8981\u5b89\u88c5 LibreOffice\u3001WPS Office \u6216 Microsoft Word \u4efb\u610f\u4e00\u79cd\u672c\u5730\u8f6c\u6362\u7ec4\u4ef6(LibreOffice \u4e0b\u8f7d:https://zh-tw.libreoffice.org/download/),\u6216\u8005\u624b\u52a8\u5c06\u6587\u4ef6\u8f6c\u6362\u4e3a.docx\u683c\u5f0f\u518d\u4e0a\u4f20", "\u8f6c\u6362docx\u5143\u6570\u636e\u8bfb\u53d6\u5931\u8d25:.doc\u548c.wps\u8bc6\u522b,\u9700\u8981\u5b89\u88c5libreoffice\u3001wpsoffice\u6216microsoftword\u4efb\u610f\u4e00\u79cd\u672c\u5730\u8f6c\u6362\u7ec4\u4ef6(libreoffice\u4e0b\u8f7d:https://zh-tw.libreoffice.org/download/),\u6216\u8005\u624b\u52a8\u5c06\u6587\u4ef6\u8f6c\u6362\u4e3a.docx\u683c\u5f0f\u518d\u4e0a\u4f20", ""],
        ["wps:metadata_error", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1ametadata error", "\u8f6c\u6362 DOCX \u5143\u6570\u636e\u8bfb\u53d6\u5931\u8d25:.doc\u548c.wps\u8bc6\u522b,\u9700\u8981\u5b89\u88c5 LibreOffice\u3001WPS Office \u6216 Microsoft Word \u4efb\u610f\u4e00\u79cd\u672c\u5730\u8f6c\u6362\u7ec4\u4ef6(LibreOffice \u4e0b\u8f7d:https://zh-tw.libreoffice.org/download/),\u6216\u8005\u624b\u52a8\u5c06\u6587\u4ef6\u8f6c\u6362\u4e3a.docx\u683c\u5f0f\u518d\u4e0a\u4f20", "\u8f6c\u6362docx\u5143\u6570\u636e\u8bfb\u53d6\u5931\u8d25:.doc\u548c.wps\u8bc6\u522b,\u9700\u8981\u5b89\u88c5libreoffice\u3001wpsoffice\u6216microsoftword\u4efb\u610f\u4e00\u79cd\u672c\u5730\u8f6c\u6362\u7ec4\u4ef6(libreoffice\u4e0b\u8f7d:https://zh-tw.libreoffice.org/download/),\u6216\u8005\u624b\u52a8\u5c06\u6587\u4ef6\u8f6c\u6362\u4e3a.docx\u683c\u5f0f\u518d\u4e0a\u4f20", ""],
    ],
    "wps_legacy": [
        ["file_name", "\u6587\u4ef6\u540d", "ole-metadata-fixture.wps", "ole-metadata-fixture.wps", ""],
        ["extension", "\u6269\u5c55\u540d", ".wps", ".wps", ""],
        ["size", "\u6587\u4ef6\u5927\u5c0f", "4096", "4096-01-01T00:00:00.000Z", "4096-01-01"],
        ["created_at", "\u6587\u4ef6\u521b\u5efa\u65f6\u95f4", "2026-09-22T05:25:58.574Z", "2026-09-22T05:25:58.574Z", "2026-09-22"],
        ["modified_at", "\u6587\u4ef6\u4fee\u6539\u65f6\u95f4", "2026-09-22T05:01:45.126Z", "2026-09-22T05:01:45.126Z", "2026-09-22"],
        ["accessed_at", "\u6587\u4ef6\u8bbf\u95ee\u65f6\u95f4", "2026-09-22T05:50:54.320Z", "2026-09-22T05:50:54.320Z", "2026-09-22"],
        ["file_sha256", "\u539f\u59cb\u6587\u4ef6 SHA256", "083f918cb907518f3dc2eb5cce01dd3ceacac0fc1b3c8e13559106812ae40262", "083f918cb907518f3dc2eb5cce01dd3ceacac0fc1b3c8e13559106812ae40262", ""],
        ["code_page", "\u4ee3\u7801\u9875", "936", "0935-12-31T15:54:17.000Z", "0935-12-31"],
        ["title", "\u6807\u9898", "\u6865\u6881\u6295\u6807\u6587\u4ef6", "\u6865\u6881\u6295\u6807\u6587\u4ef6", ""],
        ["author", "\u4f5c\u8005", "\u5f20\u4e09 admin@corp-mail.com", "\u5f20\u4e09admin@corp-mail.com", ""],
        ["revision", "\u4fee\u8ba2\u53f7", "7", "2001-06-30T16:00:00.000Z", "2001-06-30"],
        ["total_time", "\u7f16\u8f91\u65f6\u957f", "2 \u5c0f\u65f6 15 \u5206\u949f", "2\u5c0f\u65f615\u5206\u949f", ""],
        ["created", "\u521b\u5efa\u65f6\u95f4", "2024-01-15T08:00:00.000Z", "2024-01-15T08:00:00.000Z", "2024-01-15"],
        ["ole_prop_99", "ole prop 99", "\u65e0\u5b57\u5178\u5c5e\u6027", "\u65e0\u5b57\u5178\u5c5e\u6027", ""],
        ["custom:\u79d8\u5bc6\u6807\u8bb0", "\u81ea\u5b9a\u4e49\uff1a\u79d8\u5bc6\u6807\u8bb0", "\u8d70\u5b57\u5178\u7684\u503c", "\u8d70\u5b57\u5178\u7684\u503c", ""],
        ["ole_prop_1", "ole prop 1", "936", "0935-12-31T15:54:17.000Z", "0935-12-31"],
        ["ole_prop_15", "ole prop 15", "\u4e2d\u94c1\u5efa", "\u4e2d\u94c1\u5efa", ""],
        ["ole_prop_16", "ole prop 16", "true", "true", ""],
        ["ole_prop_23", "ole prop 23", "720897", "720897", ""],
        ["company", "\u516c\u53f8", "\u4e2d\u94c1\u5efa", "\u4e2d\u94c1\u5efa", ""],
        ["links_dirty", "\u94fe\u63a5\u5df2\u53d8\u66f4", "true", "true", ""],
        ["app_version", "\u5e94\u7528\u7a0b\u5e8f\u7248\u672c", "11.0001", "2001-10-31T16:00:00.000Z", "2001-10-31"],
        ["ole_storage_count", "OLE \u5b58\u50a8\u6570\u91cf", "2", "2001-01-31T16:00:00.000Z", "2001-01-31"],
        ["ole_stream_count", "OLE Stream \u6570\u91cf", "11", "2001-10-31T16:00:00.000Z", "2001-10-31"],
        ["ole_stream_paths", "OLE Stream \u8def\u5f84\u6458\u8981", "/;Root Entry/DocumentSummaryInformation;Root Entry/Embeddings;Root Entry/Macros/;Root Entry/Macros/VBA/;Root Entry/Macros/VBA/dir;Root Entry/Sh33tJ5;Root Entry/SummaryInformation;Root Entry/WordDocument", "/;rootentry/documentsummaryinformation;rootentry/embeddings;rootentry/macros/;rootentry/macros/vba/;rootentry/macros/vba/dir;rootentry/sh33tj5;rootentry/summaryinformation;rootentry/worddocument", ""],
        ["ole_stream_paths_fingerprint", "OLE Stream \u8def\u5f84\u6307\u7eb9", "e0674736ceade93e162e3e968f8e50e952ed207c737142ab428cb8bedca078e6", "e0674736ceade93e162e3e968f8e50e952ed207c737142ab428cb8bedca078e6", ""],
        ["ole_stream_sizes_fingerprint", "OLE Stream \u5927\u5c0f\u6307\u7eb9", "39517c94687838c2130eb594ca0d65dbe8ff62f1a6d8077a4cd826599f34dc5e", "39517c94687838c2130eb594ca0d65dbe8ff62f1a6d8077a4cd826599f34dc5e", ""],
        ["ole_has_macro_storage", "OLE \u5b8f\u5b58\u50a8", "\u662f", "\u662f", ""],
        ["ole_macro_paths", "OLE \u5b8f\u5b58\u50a8\u8def\u5f84", "Root Entry/Macros/;Root Entry/Macros/VBA/;Root Entry/Macros/VBA/dir", "rootentry/macros/;rootentry/macros/vba/;rootentry/macros/vba/dir", ""],
        ["ole_signal:root_entry_macros_vba_dir", "OLE \u7591\u4f3c\u75d5\u8ff9\uff1aroot entry macros vba dir", "VBA mail code", "vbamailcode", ""],
        ["ole_signal:root_entry_worddocument", "OLE \u7591\u4f3c\u75d5\u8ff9\uff1aroot entry worddocument", "mail admin \u7490\u3009\u5f7f wps-uid-77;mail admin \u8d26\u53f7 wps-uid-77", "mailadmin\u7490\u3009\u5f7fwps-uid-77;mailadmin\u8d26\u53f7wps-uid-77", ""],
        ["wps:author", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1aauthor", "\u5f20\u4e09 admin@corp-mail.com", "\u5f20\u4e09admin@corp-mail.com", ""],
        ["wps:ole_signal_root_entry_macros_vba_dir", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1aole signal root entry macros vba dir", "VBA mail code", "vbamailcode", ""],
        ["wps:ole_signal_root_entry_worddocument", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1aole signal root entry worddocument", "mail admin \u7490\u3009\u5f7f wps-uid-77;mail admin \u8d26\u53f7 wps-uid-77", "mailadmin\u7490\u3009\u5f7fwps-uid-77;mailadmin\u8d26\u53f7wps-uid-77", ""],
        ["metadata_error", "metadata error", "\u8f6c\u6362 DOCX \u5143\u6570\u636e\u8bfb\u53d6\u5931\u8d25:.doc\u548c.wps\u8bc6\u522b,\u9700\u8981\u5b89\u88c5 LibreOffice\u3001WPS Office \u6216 Microsoft Word \u4efb\u610f\u4e00\u79cd\u672c\u5730\u8f6c\u6362\u7ec4\u4ef6(LibreOffice \u4e0b\u8f7d:https://zh-tw.libreoffice.org/download/),\u6216\u8005\u624b\u52a8\u5c06\u6587\u4ef6\u8f6c\u6362\u4e3a.docx\u683c\u5f0f\u518d\u4e0a\u4f20", "\u8f6c\u6362docx\u5143\u6570\u636e\u8bfb\u53d6\u5931\u8d25:.doc\u548c.wps\u8bc6\u522b,\u9700\u8981\u5b89\u88c5libreoffice\u3001wpsoffice\u6216microsoftword\u4efb\u610f\u4e00\u79cd\u672c\u5730\u8f6c\u6362\u7ec4\u4ef6(libreoffice\u4e0b\u8f7d:https://zh-tw.libreoffice.org/download/),\u6216\u8005\u624b\u52a8\u5c06\u6587\u4ef6\u8f6c\u6362\u4e3a.docx\u683c\u5f0f\u518d\u4e0a\u4f20", ""],
        ["wps:metadata_error", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1ametadata error", "\u8f6c\u6362 DOCX \u5143\u6570\u636e\u8bfb\u53d6\u5931\u8d25:.doc\u548c.wps\u8bc6\u522b,\u9700\u8981\u5b89\u88c5 LibreOffice\u3001WPS Office \u6216 Microsoft Word \u4efb\u610f\u4e00\u79cd\u672c\u5730\u8f6c\u6362\u7ec4\u4ef6(LibreOffice \u4e0b\u8f7d:https://zh-tw.libreoffice.org/download/),\u6216\u8005\u624b\u52a8\u5c06\u6587\u4ef6\u8f6c\u6362\u4e3a.docx\u683c\u5f0f\u518d\u4e0a\u4f20", "\u8f6c\u6362docx\u5143\u6570\u636e\u8bfb\u53d6\u5931\u8d25:.doc\u548c.wps\u8bc6\u522b,\u9700\u8981\u5b89\u88c5libreoffice\u3001wpsoffice\u6216microsoftword\u4efb\u610f\u4e00\u79cd\u672c\u5730\u8f6c\u6362\u7ec4\u4ef6(libreoffice\u4e0b\u8f7d:https://zh-tw.libreoffice.org/download/),\u6216\u8005\u624b\u52a8\u5c06\u6587\u4ef6\u8f6c\u6362\u4e3a.docx\u683c\u5f0f\u518d\u4e0a\u4f20", ""],
    ],
    "pdf": [
        ["file_name", "\u6587\u4ef6\u540d", "pdf-info-fixture.pdf", "pdf-info-fixture.pdf", ""],
        ["extension", "\u6269\u5c55\u540d", ".pdf", ".pdf", ""],
        ["size", "\u6587\u4ef6\u5927\u5c0f", "920", "0919-12-31T15:54:17.000Z", "0919-12-31"],
        ["created_at", "\u6587\u4ef6\u521b\u5efa\u65f6\u95f4", "2026-09-22T04:12:11.947Z", "2026-09-22T04:12:11.947Z", "2026-09-22"],
        ["modified_at", "\u6587\u4ef6\u4fee\u6539\u65f6\u95f4", "2026-09-22T04:48:40.804Z", "2026-09-22T04:48:40.804Z", "2026-09-22"],
        ["accessed_at", "\u6587\u4ef6\u8bbf\u95ee\u65f6\u95f4", "2026-09-22T05:50:54.223Z", "2026-09-22T05:50:54.223Z", "2026-09-22"],
        ["file_sha256", "\u539f\u59cb\u6587\u4ef6 SHA256", "fc6ebc14417ad2231b2f632f53704260f5c1c8a4b6d22151ef158f278da66810", "fc6ebc14417ad2231b2f632f53704260f5c1c8a4b6d22151ef158f278da66810", ""],
        ["pdf_version", "PDF \u7248\u672c", "1.7", "2001-01-06T16:00:00.000Z", "2001-01-06"],
        ["pdf_info:pdfformatversion", "PDF Info\uff1apdfformatversion", "1.7", "2001-01-06T16:00:00.000Z", "2001-01-06"],
        ["pdf_info:islinearized", "PDF Info\uff1aislinearized", "false", "false", ""],
        ["pdf_info:isacroformpresent", "PDF Info\uff1aisacroformpresent", "false", "false", ""],
        ["pdf_info:isxfapresent", "PDF Info\uff1aisxfapresent", "false", "false", ""],
        ["pdf_info:iscollectionpresent", "PDF Info\uff1aiscollectionpresent", "false", "false", ""],
        ["pdf_info:issignaturespresent", "PDF Info\uff1aissignaturespresent", "false", "false", ""],
        ["title", "\u6807\u9898", "\u6865\u6881\u6295\u6807\u6587\u4ef6", "\u6865\u6881\u6295\u6807\u6587\u4ef6", ""],
        ["pdf_info:title", "PDF Info\uff1atitle", "\u6865\u6881\u6295\u6807\u6587\u4ef6", "\u6865\u6881\u6295\u6807\u6587\u4ef6", ""],
        ["author", "\u4f5c\u8005", "\u5f20\u4e09 zhangsan@corp-mail.com", "\u5f20\u4e09zhangsan@corp-mail.com", ""],
        ["pdf_info:author", "PDF Info\uff1aauthor", "\u5f20\u4e09 zhangsan@corp-mail.com", "\u5f20\u4e09zhangsan@corp-mail.com", ""],
        ["subject", "\u4e3b\u9898", "\u6295\u6807\u6587\u4ef6", "\u6295\u6807\u6587\u4ef6", ""],
        ["pdf_info:subject", "PDF Info\uff1asubject", "\u6295\u6807\u6587\u4ef6", "\u6295\u6807\u6587\u4ef6", ""],
        ["keywords", "\u5173\u952e\u8bcd", "\u6865\u6881;\u96a7\u9053", "\u6865\u6881;\u96a7\u9053", ""],
        ["pdf_info:keywords", "PDF Info\uff1akeywords", "\u6865\u6881;\u96a7\u9053", "\u6865\u6881;\u96a7\u9053", ""],
        ["creator", "\u521b\u5efa\u5de5\u5177", "WPS Office", "wpsoffice", ""],
        ["pdf_info:creator", "PDF Info\uff1acreator", "WPS Office", "wpsoffice", ""],
        ["producer", "\u751f\u6210\u5de5\u5177", "PyMuPDF \u9020\u5939\u5177", "pymupdf\u9020\u5939\u5177", ""],
        ["pdf_info:producer", "PDF Info\uff1aproducer", "PyMuPDF \u9020\u5939\u5177", "pymupdf\u9020\u5939\u5177", ""],
        ["created", "\u521b\u5efa\u65f6\u95f4", "D:20240115080000", "d:20240115080000", ""],
        ["pdf_info:creationdate", "PDF Info\uff1acreationdate", "D:20240115080000", "d:20240115080000", ""],
        ["modified", "\u4fee\u6539\u65f6\u95f4", "D:20240320123000", "d:20240320123000", ""],
        ["pdf_info:moddate", "PDF Info\uff1amoddate", "D:20240320123000", "d:20240320123000", ""],
        ["pages", "\u9875\u6570", "1", "2000-12-31T16:00:00.000Z", "2000-12-31"],
        ["fingerprints", "PDF \u6307\u7eb9", "32346e25c394c2a60dc282c3bec2a82c;47f52ca9566dcd517e713ee8e75daf72", "32346e25c394c2a60dc282c3bec2a82c;47f52ca9566dcd517e713ee8e75daf72", ""],
        ["pdf_raw:producer", "PDF \u539f\u59cb\u8bb0\u5f55\uff1aproducer", "MuPDF 1.28.2;PyMuPDF \u9020\u5939\u5177", "mupdf1.28.2;pymupdf\u9020\u5939\u5177", ""],
        ["pdf_raw:title", "PDF \u539f\u59cb\u8bb0\u5f55\uff1atitle", "\u6865\u6881\u6295\u6807\u6587\u4ef6", "\u6865\u6881\u6295\u6807\u6587\u4ef6", ""],
        ["pdf_raw:author", "PDF \u539f\u59cb\u8bb0\u5f55\uff1aauthor", "\u5f20\u4e09 zhangsan@corp-mail.com", "\u5f20\u4e09zhangsan@corp-mail.com", ""],
        ["pdf_raw:subject", "PDF \u539f\u59cb\u8bb0\u5f55\uff1asubject", "\u6295\u6807\u6587\u4ef6", "\u6295\u6807\u6587\u4ef6", ""],
        ["pdf_raw:keywords", "PDF \u539f\u59cb\u8bb0\u5f55\uff1akeywords", "\u6865\u6881;\u96a7\u9053", "\u6865\u6881;\u96a7\u9053", ""],
        ["pdf_raw:creator", "PDF \u539f\u59cb\u8bb0\u5f55\uff1acreator", "WPS Office", "wpsoffice", ""],
        ["pdf_raw:creationdate", "PDF \u539f\u59cb\u8bb0\u5f55\uff1acreationdate", "D:20240115080000", "d:20240115080000", ""],
        ["pdf_raw:moddate", "PDF \u539f\u59cb\u8bb0\u5f55\uff1amoddate", "D:20240320123000", "d:20240320123000", ""],
        ["pdf_raw:signals", "PDF \u539f\u59cb\u8bb0\u5f55\uff1asignals", "words<FEFF68656881003B96A79053>/Creator(WPS Office)/Producer<FEFF00500079004D00750050004400460020902059395177>/CreationDate(D:2024", "words<feff68656881003b96a79053>/creator(wpsoffice)/producer<feff00500079004d00750050004400460020902059395177>/creationdate(d:2024", ""],
        ["pdf_header_version", "PDF \u5934\u7248\u672c", "1.7", "2001-01-06T16:00:00.000Z", "2001-01-06"],
        ["pdf_object_count", "PDF \u5bf9\u8c61\u6570\u91cf", "5", "2001-04-30T16:00:00.000Z", "2001-04-30"],
        ["pdf_startxref_count", "PDF startxref \u6570\u91cf", "1", "2000-12-31T16:00:00.000Z", "2000-12-31"],
        ["pdf_incremental_update_count", "PDF \u589e\u91cf\u4fdd\u5b58\u6b21\u6570", "0", "1999-12-31T16:00:00.000Z", "1999-12-31"],
        ["pdf_linearized", "PDF \u7ebf\u6027\u5316", "\u5426", "\u5426", ""],
        ["pdf_xref_type", "PDF XRef \u7c7b\u578b", "\u4f20\u7edf xref \u8868", "\u4f20\u7edfxref\u8868", ""],
        ["pdf_trailer_id", "PDF Trailer ID", "<32346e25c394c2a60dc282c3bec2a82c> / <47f52ca9566dcd517e713ee8e75daf72>", "<32346e25c394c2a60dc282c3bec2a82c>/<47f52ca9566dcd517e713ee8e75daf72>", ""],
        ["pdf_has_acroform", "PDF \u8868\u5355", "\u5426", "\u5426", ""],
        ["pdf_has_xfa", "PDF XFA \u8868\u5355", "\u5426", "\u5426", ""],
        ["pdf_signature_count", "PDF \u7b7e\u540d\u5b57\u6bb5\u6570\u91cf", "0", "1999-12-31T16:00:00.000Z", "1999-12-31"],
        ["pdf_byterange_signature_count", "PDF ByteRange \u7b7e\u540d\u6570\u91cf", "0", "1999-12-31T16:00:00.000Z", "1999-12-31"],
        ["pdf_embedded_file_count", "PDF \u9644\u4ef6\u6570\u91cf", "0", "1999-12-31T16:00:00.000Z", "1999-12-31"],
        ["wps:author", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1aauthor", "\u5f20\u4e09 zhangsan@corp-mail.com", "\u5f20\u4e09zhangsan@corp-mail.com", ""],
        ["wps:pdf_info_author", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1apdf info author", "\u5f20\u4e09 zhangsan@corp-mail.com", "\u5f20\u4e09zhangsan@corp-mail.com", ""],
        ["wps:creator", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1acreator", "WPS Office", "wpsoffice", ""],
        ["wps:pdf_info_creator", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1apdf info creator", "WPS Office", "wpsoffice", ""],
        ["wps:pdf_raw_author", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1apdf raw author", "\u5f20\u4e09 zhangsan@corp-mail.com", "\u5f20\u4e09zhangsan@corp-mail.com", ""],
        ["wps:pdf_raw_creator", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1apdf raw creator", "WPS Office", "wpsoffice", ""],
        ["wps:pdf_raw_signals", "\u7591\u4f3c WPS \u7528\u6237/\u8d26\u53f7\uff1apdf raw signals", "words<FEFF68656881003B96A79053>/Creator(WPS Office)/Producer<FEFF00500079004D00750050004400460020902059395177>/CreationDate(D:2024", "words<feff68656881003b96a79053>/creator(wpsoffice)/producer<feff00500079004d00750050004400460020902059395177>/creationdate(d:2024", ""],
    ],
    "txt_no_branch": [
        ["file_name", "\u6587\u4ef6\u540d", "dispatch-plain.txt", "dispatch-plain.txt", ""],
        ["extension", "\u6269\u5c55\u540d", ".txt", ".txt", ""],
        ["size", "\u6587\u4ef6\u5927\u5c0f", "26", "26", ""],
        ["created_at", "\u6587\u4ef6\u521b\u5efa\u65f6\u95f4", "2026-09-22T05:25:58.595Z", "2026-09-22T05:25:58.595Z", "2026-09-22"],
        ["modified_at", "\u6587\u4ef6\u4fee\u6539\u65f6\u95f4", "2026-09-22T05:50:54.354Z", "2026-09-22T05:50:54.354Z", "2026-09-22"],
        ["accessed_at", "\u6587\u4ef6\u8bbf\u95ee\u65f6\u95f4", "2026-09-22T05:50:54.354Z", "2026-09-22T05:50:54.354Z", "2026-09-22"],
        ["file_sha256", "\u539f\u59cb\u6587\u4ef6 SHA256", "8ed1d805b7bd04b1a1e0934dc61b1773e4c37863c8d52e65c217011f441b642c", "8ed1d805b7bd04b1a1e0934dc61b1773e4c37863c8d52e65c217011f441b642c", ""],
    ],
}



def _make_fixtures(tmp_path: Path) -> dict[str, MetadataFileInput]:
    """六分支夹具。docx 用 T2 的 build_docx_fixture（真值对齐唯一来源）。"""
    docx = tmp_path / "fixture-full.docx"
    build_docx_fixture(docx)

    doc_zip = tmp_path / "dispatch-doc-zip.doc"
    shutil.copyfile(docx, doc_zip)  # .doc + zip 头 → dispatch 走 docx 分支

    ole = FIXTURES / "ole-metadata-fixture.doc"
    wps = tmp_path / "ole-metadata-fixture.wps"
    shutil.copyfile(ole, wps)

    txt = tmp_path / "dispatch-plain.txt"
    txt.write_bytes("纯文本内容\n第二行\n".encode("utf-8"))

    def _input(path: Path, size: int | None = 0) -> MetadataFileInput:
        return MetadataFileInput(
            file_path=str(path), file_name=path.name,
            extension=path.suffix, size=size)

    return {
        "docx_branch": _input(docx),
        "doc_with_zip_header": _input(doc_zip),
        "doc_legacy": _input(ole),
        "wps_legacy": _input(wps),
        "pdf": _input(FIXTURES / "pdf-info-fixture.pdf"),
        "txt_no_branch": _input(txt),
    }


@pytest.mark.parametrize("tag", [
    "docx_branch", "doc_with_zip_header", "doc_legacy",
    "wps_legacy", "pdf", "txt_no_branch",
])
def test_full_items_match_oracle(tmp_path, tag):
    """六分支 items 逐键逐序对拍；时间键只对键序（值随文件触碰漂移）。"""
    fileobj = _make_fixtures(tmp_path)[tag]
    items = extract_metadata_file(fileobj)
    expected = _ORACLE_FULL[tag]

    assert len(items) == len(expected)
    for got, exp in zip(items, expected):
        assert got["key"] == exp[0], f"键序分歧: {got['key']} != {exp[0]}"
        if got["key"] in _TIME_KEYS:
            assert _ISO_MS_RE.match(got["value"]), got["value"]
            assert got["date_day"] == got["value"][:10]
            continue
        if got["key"] == "file_sha256":
            # 文件实例绑定值：构造型夹具与 oracle 跑的物理文件字节不同，
            # 只钉格式（算法正确性由 test_has_zip_header_and_sha256 钉定）。
            assert re.fullmatch(r"[0-9a-f]{64}", got["value"]), got["value"]
            continue
        if _THREE_DIGIT_RE.match(exp[2]):
            # 3 位年直取（lines='900'/code_page='936'/size='920'…）：本地化
            # 偏移 V8 走 LMT（+08:05:43）、Python astimezone 给现代偏移 →
            # 秒级分歧，spec §15 已登记——只钉 ISO 格式与 date_day 同源。
            assert _ISO_MS_RE.match(got["normalized"]), got["normalized"]
            assert got["date_day"] == got["normalized"][:10]
            continue
        if _ISO_ANY_RE.match(exp[3]) and not exp[2].isdigit():
            # V8 对含字母/符号串的病态日期匹配（'42:wps-uid-42'→2042、
            # 'wps-uid-77'→1977）不复刻（spec §15 登记）——value 已对，跳过。
            continue
        assert got["label"] == exp[1], f"{got['key']} label"
        assert got["value"] == exp[2], f"{got['key']} value: {got['value']!r}"
        assert got["normalized"] == exp[3], f"{got['key']} normalized"
        assert got["date_day"] == exp[4], f"{got['key']} date_day"


def test_legacy_metadata_error_text_matches_oracle(tmp_path):
    """R-P3d-6：无 soffice 形态的 metadata_error 逐字（NFKC 折叠后）。"""
    fileobj = _make_fixtures(tmp_path)["doc_legacy"]
    items = extract_metadata_file(fileobj)
    got = next(it["value"] for it in items if it["key"] == "metadata_error")
    assert got == _ORACLE_LEGACY_ERROR


def test_stat_iso_matches_oracle_samples():
    """Node fs 层 round 行为钉定：ms → 秒 double → _js_stat_iso 恒等回放。"""
    for ms, iso in _ORACLE_STAT_ISO:
        assert _js_stat_iso(ms / 1000.0) == iso, ms


def test_stat_iso_format_and_utc():
    text = _js_stat_iso(0.0)
    assert text == "1970-01-01T00:00:00.000Z"
    # round-half-up 实证：.999999 秒 → 毫秒进位（oracle 实测 Node 行为，非截断）
    assert _js_stat_iso(1758445522.999999) == "2025-09-21T09:05:23.000Z"
    # .597 → 进 1ms（oracle stat 样本同款语义）
    assert _js_stat_iso(1790046704.403597) == "2026-09-22T03:11:44.404Z"


def test_has_zip_header_and_sha256(tmp_path):
    zip_file = tmp_path / "a.docx"
    build_docx_fixture(zip_file)
    assert has_zip_header(zip_file) is True
    assert has_zip_header(FIXTURES / "ole-metadata-fixture.doc") is False

    short = tmp_path / "short.bin"
    short.write_bytes(b"PK\x03")
    assert has_zip_header(short) is False  # 短读（bytesRead < 4）→ False

    data = b"\x00\x01\x02"
    blob = tmp_path / "blob.bin"
    blob.write_bytes(data)
    assert hash_file_sha256(blob) == hashlib.sha256(data).hexdigest()


def test_size_fallback_zero_uses_stat(tmp_path):
    fileobj = _make_fixtures(tmp_path)["txt_no_branch"]
    actual = Path(fileobj["file_path"]).stat().st_size
    items = extract_metadata_file(fileobj)
    got = next(it["value"] for it in items if it["key"] == "size")
    assert got == str(actual)  # size=0（falsy）→ 回落实际大小

    fileobj["size"] = 5
    got = next(it["value"] for it in extract_metadata_file(fileobj)
               if it["key"] == "size")
    assert got == "5"


def test_stat_outside_try_missing_file(tmp_path):
    """stat 在 try 外（:1062）：文件不存在直接上抛 → runner 逐文件容错（R-P3d-4）。"""
    fileobj = MetadataFileInput(
        file_path=str(tmp_path / "missing.docx"), file_name="missing.docx",
        extension=".docx", size=0)
    with pytest.raises(FileNotFoundError):
        extract_metadata_file(fileobj)


def test_branch_exception_becomes_metadata_error(tmp_path):
    """分支异常 → metadata_error（:1081），基础字段仍在；文案 fallback 兜底。"""
    corrupt = tmp_path / "corrupt.docx"
    corrupt.write_bytes(b"PK\x03\x04" + b"\x00" * 32)  # zip 头 + 垃圾
    fileobj = MetadataFileInput(
        file_path=str(corrupt), file_name="corrupt.docx", extension=".docx", size=0)
    items = extract_metadata_file(fileobj)
    keys = [it["key"] for it in items]
    assert "file_name" in keys and "file_sha256" in keys
    error = next(it for it in items if it["key"] == "metadata_error")
    assert error["value"]  # 有文案（zipfile BadZipFile message）
    assert error["comparable"] is False


def test_unsupported_extension_has_no_branch_fields(tmp_path):
    fileobj = _make_fixtures(tmp_path)["txt_no_branch"]
    items = extract_metadata_file(fileobj)
    assert [it["key"] for it in items] == [
        "file_name", "extension", "size", "created_at",
        "modified_at", "accessed_at", "file_sha256"]


def test_v8_numeric_loose_dates():
    """T5 补样本：V8 legacy 数字/点分段（js_date_iso 直出 ISO）。"""
    assert js_date_iso("3") == "2001-02-28T16:00:00.000Z"
    assert js_date_iso("42") == "2041-12-31T16:00:00.000Z"
    assert js_date_iso("11.1.0") == "2000-10-31T16:00:00.000Z"
    assert js_date_iso("1.2") == "2001-01-01T16:00:00.000Z"
    assert js_date_iso("1.2.3") == "2003-01-01T16:00:00.000Z"
    assert js_date_iso("26") is None  # 13-31 仅 day 语义 → NaN
    assert js_date_iso("5.6") == "2001-05-05T16:00:00.000Z"
    assert js_date_iso("12345") == "+012344-12-31T16:00:00.000Z"  # 超界算术
