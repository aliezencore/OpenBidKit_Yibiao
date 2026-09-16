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
