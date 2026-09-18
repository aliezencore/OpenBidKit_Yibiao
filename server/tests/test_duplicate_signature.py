from __future__ import annotations

import hashlib

from app.domain.duplicate.signature import compute_analysis_signature, stable_file_id


class _File:
    """用最小替身而非 Mock，避免测试绑定到 Mock 内部。"""

    def __init__(self, path, size, modified, fid=None, name=""):
        self.file_path = path
        self.size = size
        self.modified_at = modified
        self.id = fid
        self.file_name = name


def test_signature_matches_reference_format():
    """逐字对齐 :126-131：'path|size|modified' 按输入顺序、\\n 连接、sha1。"""
    files = [_File("/a.docx", 10, "t1"), _File("/b.docx", 20, "t2")]
    expected = hashlib.sha1("/a.docx|10|t1\n/b.docx|20|t2".encode()).hexdigest()
    assert compute_analysis_signature(files) == expected


def test_signature_is_order_sensitive():
    a = _File("/a.docx", 10, "t1")
    b = _File("/b.docx", 20, "t2")
    assert compute_analysis_signature([a, b]) != compute_analysis_signature([b, a])


def test_signature_changes_when_size_changes():
    a = _File("/a.docx", 10, "t1")
    b = _File("/a.docx", 11, "t1")
    assert compute_analysis_signature([a]) != compute_analysis_signature([b])


def test_signature_changes_when_modified_changes():
    assert (
        compute_analysis_signature([_File("/a.docx", 10, "t1")])
        != compute_analysis_signature([_File("/a.docx", 10, "t2")])
    )


def test_signature_of_empty_is_sha1_of_empty_string():
    assert compute_analysis_signature([]) == hashlib.sha1(b"").hexdigest()


def test_signature_skips_falsy_entries():
    """.filter(Boolean)：None 与空值被跳过。"""
    files = [None, _File("/a.docx", 10, "t1")]
    expected = hashlib.sha1("/a.docx|10|t1".encode()).hexdigest()
    assert compute_analysis_signature(files) == expected


def test_signature_renders_none_modified_as_js_null():
    """JS 模板字符串把 null 渲染成字面 "null"；Python f-string 会渲染成 "None"。
    逐字对齐 :129 —— 必须是 "null"。"""
    expected = hashlib.sha1("/a.docx|10|null".encode()).hexdigest()
    assert compute_analysis_signature([_File("/a.docx", 10, None)]) == expected


def test_signature_none_modified_is_not_python_none_literal():
    """反向钉子：防实现者把 None 渲染成 Python 的 "None"。"""
    wrong = hashlib.sha1("/a.docx|10|None".encode()).hexdigest()
    assert compute_analysis_signature([_File("/a.docx", 10, None)]) != wrong


def test_signature_renders_none_size_as_js_null():
    """`${f.size}` 在 JS 里对 null/undefined 都出 "null"，与 modified_at 同一渲染规则。

    防御性钉子：上传路径上 size 通常总有值，此处钉的是渲染规则的一致性，
    不是数据可能性。
    """
    expected = hashlib.sha1("/a.docx|null|t1".encode()).hexdigest()
    assert compute_analysis_signature([_File("/a.docx", None, "t1")]) == expected


def test_stable_file_id_prefers_explicit_id():
    assert stable_file_id("/a.docx", "a.docx", explicit_id="fixed") == "fixed"


def test_stable_file_id_falls_back_to_path_hash():
    expected = hashlib.sha1("/a.docx".encode()).hexdigest()
    assert stable_file_id("/a.docx", "a.docx") == expected


def test_stable_file_id_uses_name_when_path_empty():
    expected = hashlib.sha1("a.docx".encode()).hexdigest()
    assert stable_file_id("", "a.docx") == expected
