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


def test_signature_skips_falsy_non_none_scalars():
    """判别性用例：`.filter(Boolean)` 判定的是 **JS 真值**，不是 `!== null`。

    只传 `None` 的用例无法区分 `if file` 与 `if file is not None`（两者对 `None`
    都跳过）。这里放进裸标量 `0` 与 `""` —— JS 的 `Boolean(0)` / `Boolean("")`
    均为 `false`，会被 `filter(Boolean)` 丢弃：

    ```js
    [0, {...}, ""].filter(Boolean)  // => 只剩中间那个对象
    ```

    注意**对象永远是真值**：即使 `_File("", 0, None)` 字段全为假，JS 也会保留它
    （`Boolean({})` 为 `true`，Python 同样把任意非 `None` 对象当真）。
    所以判别点必须是**裸的假标量**，不能是「字段为假的 `_File`」。

    可用的 JS 参考值（Node 实测）：

    - 期望（两个标量都丢弃）：`"/a.docx|10|t1"` →
      `d031cbb73756be21fd93e0eac3bc524e28c0d8c7`
    - 仅丢 `0`、留下 `""`：`395e3182aa2be0eaafd233e5f097f467abae2cfd`
    - 仅丢 `""`、留下 `0`：`c2fe8c213a363b8e66f5c2f0d42322633752207e`
    - 两个都留下：`420487b61c57a24399f1c5102fa57b0cd5f1edcd`

    四个哈希互不相同，故任何「改用 `is not None`」或「只处理其中一种」的弱化
    都会在此变红。
    """
    files = [0, _File("/a.docx", 10, "t1"), ""]
    expected = "d031cbb73756be21fd93e0eac3bc524e28c0d8c7"  # Node 实测
    assert compute_analysis_signature(files) == expected


def test_signature_keeps_object_with_falsy_fields():
    """反向钉子：字段为假的**对象**是真值，`filter(Boolean)` 必须保留它。

    防止把过滤条件误写成「字段全空就跳过」。JS 侧 Node 实测：
    `[{"file_path":"","size":0,"modified_at":null}].filter(Boolean).length === 1`，
    渲染为 `"|0|null"` → `7fb05d0927612a69dad9047c6fb358f4e44fe647`。
    空串 / `0` / `null` 的渲染值两端一致，故直接用该字面量。
    """
    files = [_File("", 0, None)]
    assert compute_analysis_signature(files) == "7fb05d0927612a69dad9047c6fb358f4e44fe647"


def test_signature_renders_bool_as_js_lowercase():
    """`` `${true}` `` 在 JS 里是 `"true"`，Python `str(True)` 是 `"True"` —— 哈希不同。

    Node 实测：`/a.docx|true|t1` → `8c52a3d0…`；`/a.docx|True|t1` → `7a1cca30…`。
    `isinstance(True, int)` 为真，故实现里 `bool` 必须**先于**数值分支判定。
    """
    expected = "8c52a3d0afecef0345426c51aa84f80679859bcc"  # Node 实测
    assert compute_analysis_signature([_File("/a.docx", True, "t1")]) == expected
    assert expected != hashlib.sha1("/a.docx|True|t1".encode()).hexdigest()


def test_signature_renders_false_as_js_lowercase():
    """同上，`False` 必须是 `"false"`（防只处理 `True` 的半吊子实现）。"""
    expected = "349515b0e448cdd1d4494ef1778aecbdeed51a31"  # Node 实测
    assert compute_analysis_signature([_File("/a.docx", False, "t1")]) == expected


def test_signature_renders_integral_float_without_trailing_zero():
    """`` `${10.0}` `` 在 JS 里是 `"10"`（Number 无 `10.0` 表示），Python 是 `"10.0"`。

    Node 实测：`/a.docx|10|t1` → `d031cbb7…`（与整数 `10` 同哈希，符合预期）；
    `/a.docx|10.0|t1` → `9fdd0d7f…`。钉住不许出现 `.0`。
    """
    expected = "d031cbb73756be21fd93e0eac3bc524e28c0d8c7"  # Node 实测
    assert compute_analysis_signature([_File("/a.docx", 10.0, "t1")]) == expected
    assert compute_analysis_signature([_File("/a.docx", 10.0, "t1")]) == \
        compute_analysis_signature([_File("/a.docx", 10, "t1")])


def test_signature_renders_fractional_float_same_as_python():
    """非整值浮点：JS 与 Python 本就一致（`1.5` → `"1.5"`），此用例把它钉住。

    Node 实测：`/a.docx|1.5|t1` → `d8df02f5d12115e25c2dc294d54087e9d7e5838b`，
    与 Python `str(1.5)` 拼出的串同哈希 —— 即此处**无需**特殊处理，
    但仍加钉子防止有人把「整值走 int」的分支误写成「所有 float 都走 int」。
    """
    expected = "d8df02f5d12115e25c2dc294d54087e9d7e5838b"  # Node 实测
    assert compute_analysis_signature([_File("/a.docx", 1.5, "t1")]) == expected
    # 反向：若被误渲染成 "1"（截断）或 "15" 之类都会不同
    assert hashlib.sha1("/a.docx|1.5|t1".encode()).hexdigest() == expected


def test_signature_renders_large_integral_float_without_trailing_zero():
    """`100.0` 这类更大的整值浮点同样不能带 `.0`（防只特判个位数）。"""
    expected = "63accaefe26ffca4e807ed786ab3d528e0ab57de"  # Node 实测 `/a.docx|100|t1`
    assert compute_analysis_signature([_File("/a.docx", 100.0, "t1")]) == expected


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
